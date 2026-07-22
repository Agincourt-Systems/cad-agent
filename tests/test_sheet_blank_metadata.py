"""ADR 0050: self-describing sheet blank — blank extents + thickness in spatial.json.

ADRs 0043/0044 left three explicit DFM parameters (`blank_length`,
`blank_width`, `thickness`) that exist only because `spatial.json` did not
record the flat-blank facts `bend_chain` already knows. These tests pin the
knit: `publish_sheet_metal` serializes a `metadata.sheet` block, and the DFM
rules fall back to it when the check/rule omits the explicit parameter.
Explicit parameters must still win, and non-sheet parts must be untouched.

The DFM tests run the real pipeline (a build123d `bend_chain` part through the
CLI) so the metadata path is exercised end to end, not hand-built.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("build123d")


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
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# One bracket serves every test: 40/60 mm flanges, 30 mm width, t = 2.29 mm,
# R = 2.29 mm, K = 0.44 — the downstream probe's reference part. Developed
# length = 40 + BA + 60 with BA = (pi/2)(2.29 + 0.44*2.29) ≈ 5.18 mm, so the
# blank is ≈ 105.18 x 30 mm. The FOLDED bbox is ≈ 44.9 x 30 x 64.9 — its
# smallest dimension is the 30 mm WIDTH, which is the thickness trap ADR 0033
# documented and this ADR removes. A 4 mm hole sits 3 mm from the blank's
# leading edge (u = 3 on flange 0): clearance 1 mm, far below 1.0*t = 2.29.
_DESIGN = """
from cadx import publish_sheet_metal
from cadx.sheetmetal import bend


def build(params):
    part = bend(
        40,
        60,
        angle_deg=90,
        inside_radius=2.29,
        k_factor=0.44,
        thickness=2.29,
        width=30,
        holes=[{"flange": 0, "u": 3, "v": 0, "diameter": 4}],
    )
    publish_sheet_metal("bracket", part, material="5052-H32")
"""


def _run_bracket(tmp_path: Path) -> Path:
    design = tmp_path / "design.py"
    design.write_text(textwrap.dedent(_DESIGN), encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


def _evaluate(tmp_path: Path, run_dir: Path, requirements_yaml: str) -> dict:
    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(textwrap.dedent(requirements_yaml), encoding="utf-8")
    parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))
    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))
    assert len(checks["checks"]) == 1, checks
    return checks["checks"][0]


def _rule_violations(check: dict, rule: str) -> list[dict]:
    return [violation for violation in check.get("violations", []) if violation["rule"] == rule]


def test_publish_sheet_metal_serializes_blank_metadata(tmp_path):
    """The published object's spatial record carries the sheet block verbatim."""

    run_dir = _run_bracket(tmp_path)
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    bracket = next(obj for obj in spatial["objects"] if obj["label"] == "bracket")
    sheet = bracket["metadata"]["sheet"]
    assert sheet["blank_width"] == pytest.approx(30.0)
    assert sheet["thickness"] == pytest.approx(2.29)
    # Developed length = 40 + BA + 60 with BA = (pi/2)(R + K t).
    import math

    ba = (math.pi / 2.0) * (2.29 + 0.44 * 2.29)
    assert sheet["blank_length"] == pytest.approx(40 + ba + 60)


def test_min_flange_checks_outer_flanges_without_blank_length(tmp_path):
    """`min_flange` with no `blank_length` still measures the outer flanges.

    Limit 45 mm: the 40 + BA/2 ≈ 42.6 mm leading segment must be flagged. With
    only the pre-ADR-0050 interior-web subset this single-bend part has no
    bend-to-bend segment at all, so the rule found nothing to check and passed.
    """

    run_dir = _run_bracket(tmp_path)
    check = _evaluate(
        tmp_path,
        run_dir,
        """
        checks:
          - id: dfm
            type: manufacturability
            rules:
              - rule: min_flange
                min: 45
        """,
    )
    violations = _rule_violations(check, "min_flange")
    assert violations, check
    observed = sorted(violation["observed"] for violation in violations)
    # The leading outer flange (~42.6 mm) is below 45; the trailing (~62.6) is not.
    assert len(observed) == 1
    assert observed[0] == pytest.approx(40 + 5.18 / 2.0, abs=0.1)


def test_hole_to_edge_flat_frame_without_blank_dims(tmp_path):
    """`frame: flat` with no blank dims reads them from the part's sheet block.

    The 4 mm hole at u = 3 has 1 mm clearance to the blank's leading edge —
    below the 1.0*t = 2.29 mm default. Pre ADR 0050 the flat frame silently
    deactivated without explicit `blank_length`/`blank_width` and the check
    fell back to the folded bbox.
    """

    run_dir = _run_bracket(tmp_path)
    check = _evaluate(
        tmp_path,
        run_dir,
        """
        checks:
          - id: dfm
            type: manufacturability
            frame: flat
            rules:
              - rule: hole_to_edge
        """,
    )
    violations = _rule_violations(check, "hole_to_edge")
    assert violations, check
    assert violations[0]["observed"] == pytest.approx(1.0, abs=1e-6)


def test_thickness_resolves_from_sheet_metadata_not_folded_bbox(tmp_path):
    """Thickness-relative limits use the true sheet thickness on a folded part.

    `min_bend_radius` with no explicit `thickness`: the bend radius is exactly
    1.0*t (2.29 mm), so with the true thickness the rule PASSES. The folded
    bbox's smallest dimension is the 30 mm width — that wrong fallback would
    demand a 30 mm radius and fail. A pass therefore proves the metadata path.
    """

    run_dir = _run_bracket(tmp_path)
    check = _evaluate(
        tmp_path,
        run_dir,
        """
        checks:
          - id: dfm
            type: manufacturability
            rules:
              - rule: min_bend_radius
        """,
    )
    assert _rule_violations(check, "min_bend_radius") == [], check


def test_explicit_parameters_still_win(tmp_path):
    """An explicit `thickness` overrides the sheet block (10 mm demands R >= 10)."""

    run_dir = _run_bracket(tmp_path)
    check = _evaluate(
        tmp_path,
        run_dir,
        """
        checks:
          - id: dfm
            type: manufacturability
            thickness: 10
            rules:
              - rule: min_bend_radius
        """,
    )
    violations = _rule_violations(check, "min_bend_radius")
    assert violations, check
    assert violations[0]["limit"] == pytest.approx(10.0)
