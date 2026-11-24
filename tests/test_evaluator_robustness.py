"""Evaluator robustness: a bad check fails alone and never passes vacuously.

ADR 0021/0022 promise that a malformed requirement fails its own check with a
descriptive ``error`` instead of aborting ``cadx evaluate``. Those ADRs fixed
the instances found at the time; a 2026-11-24 review reproduced seven more
(see LOG.md). These tests pin the boundary rule rather than each instance:

* any exception raised while evaluating one check becomes a failed record for
  that check, and the other checks still evaluate;
* a check that states no expectation, or names a rule the evaluator does not
  know, fails instead of passing with nothing compared.

All tests are kernel-free and in-process: the evaluator reads only
``spatial.json``/``diagnostics.json``, so synthetic run directories suffice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cadx.evaluate import evaluate_run, sweep_run


def _box(label: str, low: list[float], high: list[float]) -> dict:
    """A synthetic spatial object with a bbox and a centre of mass."""

    return {
        "id": f"obj.{label}",
        "label": label,
        "role": "part",
        "bbox": {"min": low, "max": high, "size": [h - l for l, h in zip(low, high)]},
        "mass_properties": {
            "volume": 1000.0,
            "center_of_mass": [(l + h) / 2 for l, h in zip(low, high)],
        },
    }


def _run_dir(tmp_path: Path, exports: list[dict] | None = None) -> Path:
    """Write a synthetic run directory with two boxes and two hole features.

    ``feat.no_axis`` deliberately carries no ``axis``: that is the shape of the
    ``cadx init`` starter design's own ``publish_feature`` calls.
    """

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    spatial = {
        "schema_version": "1.0",
        "units": "mm",
        "objects": [_box("left", [0, 0, 0], [10, 10, 10]), _box("right", [15, 0, 0], [25, 10, 10])],
        "features": [
            {"id": "feat.no_axis", "kind": "cylindrical_hole", "diameter": 6, "center": [5, 5, 0]},
            {
                "id": "feat.with_axis",
                "kind": "cylindrical_hole",
                "diameter": 6,
                "center": [5, 5, 5],
                "axis": [0, 0, 1],
                "source_object": "obj.left",
            },
        ],
        "assembly": {"center_of_mass": [12.5, 5, 5], "mass": 2000.0},
    }
    (run_dir / "spatial.json").write_text(json.dumps(spatial), encoding="utf-8")
    (run_dir / "diagnostics.json").write_text(
        json.dumps({"status": "ok", "exports": exports or [], "warnings": []}), encoding="utf-8"
    )
    return run_dir


def _evaluate(tmp_path: Path, checks: list, exports: list[dict] | None = None) -> dict:
    """Evaluate ``checks`` against the synthetic run; return ``checks.json`` by id."""

    run_dir = _run_dir(tmp_path, exports)
    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(json.dumps({"checks": checks}), encoding="utf-8")  # JSON is YAML
    payload = evaluate_run(run_dir, requirements)
    records = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))["checks"]
    return {"payload": payload, "records": records, "by_id": {r["id"]: r for r in records}, "run_dir": run_dir}


# A valid check evaluated beside each malformed one: it must still pass, which
# proves the malformed check did not abort the evaluation.
_GOOD = {"id": "good", "type": "dimension", "target": "obj.left.bbox.size.x", "equals": 10}

_MALFORMED = [
    pytest.param({"id": "bad", "type": "feature_count", "kind": "cylindrical_hole", "min": 1}, id="feature_count-no-equals"),
    pytest.param({"id": "bad", "type": "feature_dimension", "selector": {"kind": "cylindrical_hole"}}, id="feature_dimension-no-property"),
    pytest.param({"id": "bad", "type": "center_of_mass", "target": "assembly"}, id="center_of_mass-no-expected"),
    pytest.param({"id": "bad", "type": "stability"}, id="stability-no-support"),
    pytest.param({"id": "bad", "type": "clearance", "between": ["obj.left"], "min": 1}, id="clearance-one-ref"),
    pytest.param({"id": "bad", "type": "clearance", "min": 1}, id="clearance-no-between"),
    pytest.param({"id": "bad", "type": "interference", "between": ["left", "obj.right"]}, id="interference-ref-without-obj"),
    pytest.param({"id": "bad", "type": "feature_alignment", "features": [{"id": "feat.no_axis"}]}, id="alignment-one-selector"),
    pytest.param({"id": "bad", "type": "manufacturability", "rules": [{"min": 3}]}, id="dfm-rule-without-name"),
    pytest.param({"id": "bad", "type": "parametric", "checks": []}, id="parametric-no-params"),
]


@pytest.mark.parametrize("bad", _MALFORMED)
def test_malformed_check_fails_alone(tmp_path, bad):
    """One malformed check fails with an error; its neighbour still evaluates."""

    result = _evaluate(tmp_path, [bad, _GOOD])

    assert result["by_id"]["bad"]["status"] == "fail"
    assert result["by_id"]["bad"]["error"]
    assert result["by_id"]["good"]["status"] == "pass"
    assert result["payload"]["failed"] == ["bad"]


def test_check_without_id_or_type_fails_with_positional_id(tmp_path):
    """A check missing ``id`` or ``type`` is reported by its position in the file."""

    result = _evaluate(
        tmp_path,
        [
            {"type": "dimension", "target": "obj.left.bbox.size.x", "equals": 10},
            {"id": "typeless", "target": "obj.left.bbox.size.x", "equals": 10},
            "not a mapping",
            _GOOD,
        ],
    )

    statuses = [(record["id"], record["status"]) for record in result["records"]]
    assert statuses == [("check[0]", "fail"), ("typeless", "fail"), ("check[2]", "fail"), ("good", "pass")]
    assert "id" in result["by_id"]["check[0]"]["error"]
    assert "type" in result["by_id"]["typeless"]["error"]


def test_unknown_check_type_still_raises(tmp_path):
    """The documented contract for an unknown ``type`` is a ValueError naming it."""

    run_dir = _run_dir(tmp_path)
    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(json.dumps({"checks": [{"id": "x", "type": "bogus"}]}), encoding="utf-8")

    with pytest.raises(ValueError, match="bogus"):
        evaluate_run(run_dir, requirements)


def test_alignment_names_the_feature_that_has_no_axis(tmp_path):
    """A published hole without ``axis`` fails alignment with an actionable error."""

    result = _evaluate(
        tmp_path,
        [
            {
                "id": "align",
                "type": "feature_alignment",
                "features": [{"id": "feat.no_axis"}, {"id": "feat.with_axis"}],
                "tolerance": 0.1,
            }
        ],
    )

    record = result["by_id"]["align"]
    assert record["status"] == "fail"
    assert "feat.no_axis" in record["error"] and "axis" in record["error"]


@pytest.mark.parametrize(
    "check",
    [
        # ``equal`` is a typo for ``equals``: without the guard nothing is compared.
        pytest.param({"id": "v", "type": "dimension", "target": "obj.left.bbox.size.x", "equal": 999}, id="dimension"),
        pytest.param({"id": "v", "type": "topology", "target": "obj.left.bbox.size.x"}, id="topology"),
        pytest.param({"id": "v", "type": "clearance", "between": ["obj.left", "obj.right"], "minimum": 99}, id="clearance"),
    ],
)
def test_numeric_check_without_expectation_fails(tmp_path, check):
    """A numeric check with none of equals/min/max must not pass vacuously."""

    record = _evaluate(tmp_path, [check])["by_id"]["v"]

    assert record["status"] == "fail"
    assert "equals" in record["error"] and "min" in record["error"] and "max" in record["error"]


def test_unknown_manufacturability_rule_fails(tmp_path):
    """A mistyped rule name fails the check and is named; known rules still run."""

    record = _evaluate(
        tmp_path,
        [
            {
                "id": "dfm",
                "type": "manufacturability",
                "thickness": 2,
                "rules": [{"rule": "min_hole_diamater", "min": 100}, {"rule": "min_hole_diameter", "min": 100}],
            }
        ],
    )["by_id"]["dfm"]

    assert record["status"] == "fail"
    assert "min_hole_diamater" in record["error"]
    # The correctly spelled rule still reports its own violations.
    assert {violation["rule"] for violation in record["violations"]} == {"min_hole_diameter"}


def test_tip_angle_gate_without_com_height_fails(tmp_path):
    """``min_tip_angle_deg`` cannot be judged without ``com_height``; do not skip it."""

    check = {
        "id": "tip",
        "type": "stability",
        "target": "obj.left.mass_properties.center_of_mass",
        "support": [[0, 0], [10, 0], [10, 10], [0, 10]],
        "min_tip_angle_deg": 89,
    }

    record = _evaluate(tmp_path, [check])["by_id"]["tip"]

    assert record["status"] == "fail"
    assert "com_height" in record["error"]


def test_interference_fails_when_a_step_export_cannot_be_measured(tmp_path):
    """An unreadable STEP must fail the check, never count as 'no overlap'."""

    pytest.importorskip("build123d")
    run_dir_exports = []
    for label in ("left", "right"):
        path = tmp_path / f"{label}.step"
        path.write_text("this is not a STEP file", encoding="utf-8")
        run_dir_exports.append({"label": label, "format": "step", "path": str(path)})

    result = _evaluate(tmp_path, [{"id": "clash", "type": "interference"}, _GOOD], run_dir_exports)

    assert result["by_id"]["clash"]["status"] == "fail"
    assert result["by_id"]["clash"]["error"]
    assert result["by_id"]["good"]["status"] == "pass"


def test_interference_fails_when_an_intersection_raises(tmp_path, monkeypatch):
    """A kernel failure on one pair is an error for that pair, not a clean pass.

    build123d 0.11 changed ``intersect`` in a way the old blanket ``except``
    turned into "volume 0" — every overlap passed (ADR 0030). The pair that
    cannot be measured must be reported.
    """

    build123d = pytest.importorskip("build123d")

    class _Unmeasurable:
        def faces(self):
            return [object()]  # non-empty, so the shape counts as loaded

        def intersect(self, other):
            raise RuntimeError("kernel refused")

    monkeypatch.setattr(build123d, "import_step", lambda path: _Unmeasurable())
    exports = [{"label": label, "format": "step", "path": f"{label}.step"} for label in ("left", "right")]

    record = _evaluate(tmp_path, [{"id": "clash", "type": "interference"}], exports)["by_id"]["clash"]

    assert record["status"] == "fail"
    assert "left" in record["error"] and "right" in record["error"] and "kernel refused" in record["error"]


@pytest.mark.parametrize("check_id", ["../../escape", "..", "/abs/elsewhere", "a/b"])
def test_sweep_directory_stays_inside_the_run(tmp_path, check_id):
    """A check id is data, not a path: the sweep dir must stay under ``sweeps/``.

    The sweep directory is deleted and recreated on every evaluation, so an id
    such as ``..`` would otherwise delete the run directory itself.
    """

    run_dir = _run_dir(tmp_path)
    (run_dir / "source_snapshot.py").write_text(
        "from cadx import publish\n\n\ndef build(params):\n"
        "    publish('left', {'bbox': {'min': [0, 0, 0], 'max': [10, 10, 10]}})\n",
        encoding="utf-8",
    )
    sentinel = tmp_path / "escape"
    sentinel.mkdir()
    (sentinel / "keep.txt").write_text("must survive", encoding="utf-8")
    requirements = tmp_path / "requirements.yaml"
    check = {
        "id": check_id,
        "type": "parametric",
        "params": [{}],
        "checks": [{"id": "w", "type": "dimension", "target": "obj.left.bbox.size.x", "equals": 10}],
    }
    requirements.write_text(json.dumps({"checks": [check]}), encoding="utf-8")
    before = {path for path in tmp_path.rglob("*") if "sweeps" not in path.parts}

    payload = sweep_run(run_dir, requirements)

    assert payload["status"] == "pass", payload
    assert payload["sweeps"][0]["id"] == check_id  # the record keeps the authored id
    sweeps = run_dir / "sweeps"
    children = list(sweeps.iterdir())
    assert len(children) == 1 and children[0].parent == sweeps
    after = {path for path in tmp_path.rglob("*") if "sweeps" not in path.parts}
    assert after == before  # nothing created or removed outside sweeps/
    assert (sentinel / "keep.txt").read_text(encoding="utf-8") == "must survive"


def test_report_shows_the_error_of_a_failed_check(tmp_path):
    """``report.md`` is the agent's convergence context; it must carry the error."""

    result = _evaluate(tmp_path, [{"id": "typo", "type": "dimension", "target": "obj.nope.bbox.size.x", "equals": 1}])

    report = (result["run_dir"] / "report.md").read_text(encoding="utf-8")
    assert "error:" in report and "obj.nope.bbox.size.x" in report
