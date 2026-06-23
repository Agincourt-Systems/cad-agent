"""ADR 0018: laser/sheet manufacturability (DFM) checks.

The ``manufacturability`` requirement type is documented but unimplemented;
``evaluate.py`` raises ``ValueError`` on it today. These tests exercise the new
check purely over ``spatial.json`` built from synthetic dict publications, so
they run without a CAD kernel and are deterministic. Each test asserts the
rule math (offending feature ids, pass/fail status, warn vs fail severity),
not the presence of keys.

The design files publish a plate object as a plain dict (its bbox is the only
geometry the rules need) plus explicit features carrying ``source_object`` so
the evaluator can resolve each feature's owning bbox for edge distance and
thickness. Because dict objects produce no STEP export, automatic detection is
inert and the published features pass straight into ``spatial.json``.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke cadx through its public CLI, as agents do."""

    repo_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}
    return subprocess.run(
        [sys.executable, "-m", "cadx.cli", *args],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def parse_stdout_json(result: subprocess.CompletedProcess[str]) -> dict:
    """Parse successful cadx JSON output."""

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _write_design(tmp_path: Path, feature_lines: str) -> Path:
    """Write a design publishing a 80x20x4 plate plus the given features.

    ``feature_lines`` is python publishing one or more explicit features. The
    plate bbox is centered on the origin so edge distances are easy to reason
    about: half-width 40 mm in x, half-height 10 mm in y, thickness 4 mm in z.
    """

    design = tmp_path / "design.py"
    design.write_text(
        textwrap.dedent(
            """
            from cadx import publish, publish_feature


            def build(params):
                publish(
                    "plate",
                    {
                        "bbox": {"min": [-40, -10, 0], "max": [40, 10, 4]},
                        "mass_properties": {"volume": 6400},
                        "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
                    },
                    role="final",
                )
            __FEATURES__
            """
        ).replace("__FEATURES__", textwrap.indent(textwrap.dedent(feature_lines), "    ")),
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    return design


def _run_model(tmp_path: Path, feature_lines: str) -> Path:
    """Run a synthetic plate design and return its run directory."""

    design = _write_design(tmp_path, feature_lines)
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


def _evaluate(tmp_path: Path, run_dir: Path, requirements_yaml: str) -> dict:
    """Write requirements, evaluate, and return the parsed checks.json check."""

    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(textwrap.dedent(requirements_yaml), encoding="utf-8")
    parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))
    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))
    return checks


def _only_check(checks: dict) -> dict:
    assert len(checks["checks"]) == 1, checks
    return checks["checks"][0]


def _violation_features(check: dict, rule: str) -> set[str]:
    """Collect all feature ids cited by fail-severity violations of a rule."""

    ids: set[str] = set()
    for violation in check.get("violations", []):
        if violation["rule"] == rule:
            ids.update(violation["features"])
    return ids


def test_min_hole_diameter(tmp_path):
    """A hole smaller than thickness fails naming it; a large enough one passes."""

    # Hole diameter 2 mm < thickness 4 mm -> violation.
    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "tiny_hole",
            kind="cylindrical_hole",
            diameter=2,
            center=[0, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_hole_diameter
            """,
        )
    )
    assert check["type"] == "manufacturability"
    assert check["status"] == "fail"
    assert "feat.tiny_hole" in _violation_features(check, "min_hole_diameter")
    # Limit defaults to the resolved thickness (4 mm from the bbox min size).
    assert float(check["thickness"]) == 4.0

    # Hole diameter 5 mm >= thickness 4 mm -> passes.
    run_dir2 = _run_model(
        tmp_path,
        """
        publish_feature(
            "ok_hole",
            kind="cylindrical_hole",
            diameter=5,
            center=[0, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check2 = _only_check(
        _evaluate(
            tmp_path,
            run_dir2,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_hole_diameter
            """,
        )
    )
    assert check2["status"] == "pass"
    assert _violation_features(check2, "min_hole_diameter") == set()


def test_hole_to_edge(tmp_path):
    """A hole within 1x thickness of an edge fails; moved inward it passes."""

    # Hole radius 2 mm at x=37 -> edge gap = (40-37) - 2 = 1 mm < thickness 4.
    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "edge_hole",
            kind="cylindrical_hole",
            diameter=4,
            center=[37, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: hole_to_edge
                    factor: 1.0
            """,
        )
    )
    assert check["status"] == "fail"
    assert "feat.edge_hole" in _violation_features(check, "hole_to_edge")

    # Move the hole to x=0: edge gap = 40 - 2 = 38 mm, comfortably clears 4 mm.
    run_dir2 = _run_model(
        tmp_path,
        """
        publish_feature(
            "center_hole",
            kind="cylindrical_hole",
            diameter=4,
            center=[0, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check2 = _only_check(
        _evaluate(
            tmp_path,
            run_dir2,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: hole_to_edge
                    factor: 1.0
            """,
        )
    )
    assert check2["status"] == "pass"


def test_min_web_names_both_features(tmp_path):
    """Two holes whose edges are too close violate min_web naming both."""

    # Two 4 mm holes (r=2) centered 5 mm apart -> edge gap = 5 - 2 - 2 = 1 mm.
    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "hole_a",
            kind="cylindrical_hole",
            diameter=4,
            center=[-2.5, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        publish_feature(
            "hole_b",
            kind="cylindrical_hole",
            diameter=4,
            center=[2.5, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_web
            """,
        )
    )
    assert check["status"] == "fail"
    assert _violation_features(check, "min_web") == {"feat.hole_a", "feat.hole_b"}

    # Spread to 20 mm apart -> edge gap = 20 - 4 = 16 mm, clears thickness 4.
    run_dir2 = _run_model(
        tmp_path,
        """
        publish_feature(
            "hole_a",
            kind="cylindrical_hole",
            diameter=4,
            center=[-10, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        publish_feature(
            "hole_b",
            kind="cylindrical_hole",
            diameter=4,
            center=[10, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check2 = _only_check(
        _evaluate(
            tmp_path,
            run_dir2,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_web
            """,
        )
    )
    assert check2["status"] == "pass"


def test_severity_warn_does_not_fail(tmp_path):
    """A violated rule marked warn surfaces in warnings without failing."""

    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "tiny_hole",
            kind="cylindrical_hole",
            diameter=2,
            center=[0, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_hole_diameter
                    severity: warn
            """,
        )
    )
    assert check["status"] == "pass"
    # No fail-severity violations for the rule...
    assert _violation_features(check, "min_hole_diameter") == set()
    # ...but it must be surfaced as a warning naming the feature.
    warned = {feature for w in check.get("warnings", []) if w["rule"] == "min_hole_diameter" for feature in w["features"]}
    assert "feat.tiny_hole" in warned


def test_explicit_thickness_and_factor(tmp_path):
    """Explicit thickness and a per-rule factor drive the resolved limit."""

    # Hole diameter 5 mm. With explicit thickness 4 and factor 1.5 the limit is
    # 6 mm, so the hole fails despite being larger than the geometric thickness.
    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "hole",
            kind="cylindrical_hole",
            diameter=5,
            center=[0, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                thickness: 4
                rules:
                  - rule: min_hole_diameter
                    factor: 1.5
            """,
        )
    )
    assert check["status"] == "fail"
    fail = next(v for v in check["violations"] if v["rule"] == "min_hole_diameter")
    assert float(fail["limit"]) == 6.0
    assert float(fail["observed"]) == 5.0
    assert "feat.hole" in fail["features"]


def test_min_slot_width(tmp_path):
    """A slot narrower than thickness violates min_slot_width."""

    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "narrow_slot",
            kind="slot",
            width=2,
            length=10,
            center=[0, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_slot_width
            """,
        )
    )
    assert check["status"] == "fail"
    assert "feat.narrow_slot" in _violation_features(check, "min_slot_width")


def test_min_bend_radius(tmp_path):
    """An explicit bend feature with an inside radius below thickness fails."""

    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "sharp_bend",
            kind="bend",
            inside_radius=1,
            center=[0, 0, 2],
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_bend_radius
            """,
        )
    )
    assert check["status"] == "fail"
    assert "feat.sharp_bend" in _violation_features(check, "min_bend_radius")


def test_hole_to_bend(tmp_path):
    """A hole too close to a bend line violates hole_to_bend naming both."""

    # Hole edge is 5 - 2 = 3 mm from the bend line at x=0; limit = 2 * 4 = 8 mm.
    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "near_hole",
            kind="cylindrical_hole",
            diameter=4,
            center=[5, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        publish_feature(
            "the_bend",
            kind="bend",
            line=[[0, -10], [0, 10]],
            center=[0, 0, 2],
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: hole_to_bend
            """,
        )
    )
    assert check["status"] == "fail"
    assert "feat.near_hole" in _violation_features(check, "hole_to_bend")


def test_hole_to_edge_slot_uses_thickness_axis(tmp_path):
    """A slot elongated in-plane (the inspector's axis convention) is measured
    against the real edges, not the thickness face: centered passes, overhang fails."""

    # axis=[1,0,0] is the slot ELONGATION direction, as inspector._slot_features
    # records it. A 20 mm-long slot centered in an 80x20 plate is well clear.
    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "centered_slot",
            kind="slot",
            width=4,
            length=20,
            center=[0, 0, 2],
            axis=[1, 0, 0],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    rules = """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: hole_to_edge
                    factor: 1.0
            """
    assert _only_check(_evaluate(tmp_path, run_dir, rules))["status"] == "pass"

    # Same slot pushed to x=35 extends to x=50, past the +x edge at 40.
    run_dir2 = _run_model(
        tmp_path,
        """
        publish_feature(
            "overhang_slot",
            kind="slot",
            width=4,
            length=30,
            center=[35, 0, 2],
            axis=[1, 0, 0],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check2 = _only_check(_evaluate(tmp_path, run_dir2, rules))
    assert check2["status"] == "fail"
    assert "feat.overhang_slot" in _violation_features(check2, "hole_to_edge")


def test_min_web_ignores_unsourced_features(tmp_path):
    """Two features that name no owning object are not paired by min_web."""

    run_dir = _run_model(
        tmp_path,
        """
        publish_feature("orphan_a", kind="cylindrical_hole", diameter=4, center=[-2.5, 0, 2], axis=[0, 0, 1], through=True)
        publish_feature("orphan_b", kind="cylindrical_hole", diameter=4, center=[2.5, 0, 2], axis=[0, 0, 1], through=True)
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                thickness: 4
                rules:
                  - rule: min_web
            """,
        )
    )
    # Without a shared source_object the two holes must not be treated as a web.
    assert check["status"] == "pass"
    assert _violation_features(check, "min_web") == set()


def test_unknown_rule_and_passing_slot(tmp_path):
    """An unrecognized rule name is skipped; a wide-enough slot passes."""

    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "wide_slot",
            kind="slot",
            width=6,
            length=20,
            center=[0, 0, 2],
            axis=[1, 0, 0],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_slot_width
                  - rule: not_a_real_rule
            """,
        )
    )
    # Slot width 6 >= thickness 4 passes, and the unknown rule contributes nothing.
    assert check["status"] == "pass"
    assert check["violations"] == []
