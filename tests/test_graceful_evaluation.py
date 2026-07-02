"""ADR 0022: graceful evaluation, loop timeout passthrough, robust ingestion.

The evaluator promises (ADR 0021) that a malformed check fails alone with a
descriptive error instead of aborting the whole evaluation. These tests pin the
value-handling paths that promise did not yet cover — non-scalar targets,
missing feature properties, exact clearance without STEP exports, and bad
center-of-mass paths — plus the ``cadx loop`` timeout passthrough and the
inspector/renderer behavior when a recorded STEP export is unreadable.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke cadx through its public CLI."""

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


def run_synthetic_model(tmp_path: Path) -> Path:
    """Run a kernel-free synthetic design and return its run directory.

    ``left`` deliberately publishes ``faces: None`` (an object whose topology
    could not be counted) and the hole feature deliberately omits ``depth`` so
    the malformed-check tests below have realistic deficient data to hit.
    """

    design = tmp_path / "design.py"
    design.write_text(
        """
from cadx import publish, publish_feature


def build(params):
    publish(
        "left",
        {
            "bbox": {"min": [0, 0, 0], "max": [10, 10, 10]},
            "mass_properties": {"volume": 1000},
            "topology": {"solids": 1, "faces": None, "edges": 12, "vertices": 8},
        },
        role="final",
    )
    publish(
        "right",
        {
            "bbox": {"min": [15, 0, 0], "max": [25, 10, 10]},
            "mass_properties": {"volume": 1000},
            "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
        },
    )
    publish_feature("hole_a", kind="cylindrical_hole", diameter=6, center=[5, 5, 0])
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design)))
    return tmp_path / payload["artifact_dir"]


def evaluate_checks(tmp_path: Path, run_dir: Path, checks_yaml: str) -> tuple[dict, dict]:
    """Evaluate a requirements body and return (CLI payload, checks.json)."""

    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(f"units: mm\nchecks:\n{checks_yaml}", encoding="utf-8")
    payload = parse_stdout_json(
        run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements))
    )
    return payload, json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))


def test_dimension_non_scalar_target_fails_gracefully(tmp_path):
    """A target that resolves to a vector fails its check, not the evaluation."""

    run_dir = run_synthetic_model(tmp_path)
    payload, checks = evaluate_checks(
        tmp_path,
        run_dir,
        """
  - id: size_vector
    type: dimension
    target: obj.left.bbox.size
    equals: 10
  - id: size_x
    type: dimension
    target: obj.left.bbox.size.x
    equals: 10
""",
    )

    # The malformed check fails with a descriptive error while the valid check
    # in the same file still evaluates — the evaluation itself survives.
    assert payload["status"] == "fail"
    assert payload["failed"] == ["size_vector"]
    by_id = {check["id"]: check for check in checks["checks"]}
    assert by_id["size_vector"]["status"] == "fail"
    assert "obj.left.bbox.size" in by_id["size_vector"]["error"]
    assert by_id["size_x"]["status"] == "pass"


def test_topology_none_value_fails_gracefully(tmp_path):
    """A topology count of None (uncountable selector) fails, not crashes."""

    run_dir = run_synthetic_model(tmp_path)
    payload, checks = evaluate_checks(
        tmp_path,
        run_dir,
        """
  - id: left_faces
    type: topology
    target: obj.left.topology.faces
    equals: 6
""",
    )

    assert payload["status"] == "fail"
    assert checks["checks"][0]["status"] == "fail"
    assert "error" in checks["checks"][0]


def test_feature_dimension_missing_property_fails_gracefully(tmp_path):
    """A matched feature without the requested property fails descriptively."""

    run_dir = run_synthetic_model(tmp_path)
    payload, checks = evaluate_checks(
        tmp_path,
        run_dir,
        """
  - id: hole_depth
    type: feature_dimension
    selector:
      kind: cylindrical_hole
    property: depth
    equals: 4
    tolerance: 0.1
""",
    )

    assert payload["status"] == "fail"
    check = checks["checks"][0]
    assert check["status"] == "fail"
    assert "depth" in check["error"]
    assert "feat.hole_a" in check["error"]


def test_exact_clearance_without_step_export_fails_gracefully(tmp_path):
    """Exact clearance between labels lacking STEP exports fails descriptively.

    Synthetic dict publications never produce STEP files, so this is exactly the
    situation ``interference`` already degrades on; ``clearance`` must match.
    """

    run_dir = run_synthetic_model(tmp_path)
    payload, checks = evaluate_checks(
        tmp_path,
        run_dir,
        """
  - id: exact_gap
    type: clearance
    method: exact
    between: [obj.left, obj.right]
    min: 1
""",
    )

    assert payload["status"] == "fail"
    check = checks["checks"][0]
    assert check["status"] == "fail"
    assert check["method"] == "exact"
    assert "STEP" in check["error"]


def test_center_of_mass_scalar_index_target_fails_gracefully(tmp_path):
    """A path that indexes into a scalar fails its check, not the evaluation."""

    run_dir = run_synthetic_model(tmp_path)
    payload, checks = evaluate_checks(
        tmp_path,
        run_dir,
        """
  - id: com_bad_path
    type: center_of_mass
    target: obj.left.bbox.size.x.y
    expected: [0, 0, 0]
    tolerance: 1
""",
    )

    assert payload["status"] == "fail"
    check = checks["checks"][0]
    assert check["status"] == "fail"
    assert "error" in check


def test_loop_forwards_timeout_to_evaluate(tmp_path, monkeypatch):
    """``cadx loop`` must pass its --timeout-seconds budget to evaluate_run.

    A parametric check re-runs the design during evaluation, so an evaluate leg
    stuck on the 30 s default ignores the caller's budget. The loop is driven
    with stubbed stages so only the argument plumbing is under test.
    """

    from cadx import loop

    seen: dict[str, float] = {}

    def fake_run_design(source, params, artifact_root, timeout_seconds):
        return {"status": "ok", "artifact_dir": str(tmp_path / "run"), "errors": []}

    def fake_render_run(run_dir):
        return {"status": "ok"}

    def fake_evaluate_run(run_dir, requirements, timeout=30.0):
        seen["timeout"] = timeout
        return {"status": "pass", "report_path": "", "checks_path": ""}

    monkeypatch.setattr(loop, "run_design", fake_run_design)
    monkeypatch.setattr(loop, "render_run", fake_render_run)
    monkeypatch.setattr(loop, "evaluate_run", fake_evaluate_run)

    result = loop.loop_until_done(
        tmp_path / "design.py",
        tmp_path / "params.yaml",
        tmp_path / "requirements.yaml",
        tmp_path / "artifacts",
        tmp_path / "loop.json",
        None,
        1,
        7.5,
    )

    assert result["status"] == "pass"
    assert seen["timeout"] == 7.5


def write_broken_step_run(tmp_path: Path) -> Path:
    """Create a run directory whose diagnostics reference an unreadable STEP."""

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    broken = run_dir / "broken.step"
    broken.write_bytes(b"this is not a STEP file")
    diagnostics = {
        "schema_version": "1.0",
        "status": "ok",
        "units": "mm",
        "published": [
            {
                "id": "obj.broken",
                "label": "broken",
                "role": "final",
                "bbox": {"min": [0, 0, 0], "max": [10, 10, 10], "size": [10, 10, 10]},
            }
        ],
        "features": [],
        "warnings": [],
        "errors": [],
        "exports": [{"label": "broken", "format": "step", "path": str(broken), "units": "mm"}],
    }
    (run_dir / "diagnostics.json").write_text(json.dumps(diagnostics), encoding="utf-8")
    return run_dir


def test_inspect_survives_unreadable_step_export(tmp_path):
    """One corrupt STEP export degrades feature detection, not the inspection."""

    pytest.importorskip("build123d")
    from cadx.inspector import inspect_run

    run_dir = write_broken_step_run(tmp_path)
    summary = inspect_run(run_dir)

    assert summary["status"] == "ok"
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    assert [obj["label"] for obj in spatial["objects"]] == ["broken"]
    warnings = spatial.get("warnings", [])
    assert any(warning["type"] == "feature_detection_failed" for warning in warnings)


def test_render_survives_unreadable_step_export(tmp_path):
    """One corrupt STEP export skips projections but still renders the sheet."""

    pytest.importorskip("build123d")
    pytest.importorskip("PIL")
    from cadx.renderer import render_run

    run_dir = write_broken_step_run(tmp_path)
    # Pre-write spatial.json so this test isolates the renderer's own STEP
    # ingestion rather than re-testing the inspector fix above.
    spatial = {
        "schema_version": "1.0",
        "units": "mm",
        "objects": [
            {
                "id": "obj.broken",
                "label": "broken",
                "role": "final",
                "bbox": {"min": [0, 0, 0], "max": [10, 10, 10], "size": [10, 10, 10]},
            }
        ],
        "features": [],
    }
    (run_dir / "spatial.json").write_text(json.dumps(spatial), encoding="utf-8")

    payload = render_run(run_dir)

    assert payload["status"] == "ok"
    assert Path(payload["contact_sheet"]).is_file()
    manifest = json.loads((run_dir / "views" / "render_manifest.json").read_text(encoding="utf-8"))
    assert manifest["views"] == []
    assert any(warning["type"] == "render_step_failed" for warning in manifest.get("warnings", []))
