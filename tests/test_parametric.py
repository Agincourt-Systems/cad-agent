"""ADR 0020: parametric sweep check and documented check-type surface.

The evaluator must implement a ``parametric`` requirement check that re-runs the
design under several parameter sets and aggregates a set of ordinary spatial
sub-checks across every set. It must also keep its documented failure mode for
unknown check types and surface the supported types in the README so a design
author is not surprised by a raised error.

These tests use synthetic dictionary publications whose geometry depends on the
incoming ``params`` so that a sweep genuinely exercises different geometry per
parameter set without requiring a CAD kernel.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


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


def write_width_design(tmp_path: Path) -> Path:
    """A design whose published plate width equals ``params['width']``.

    The width-dependent bbox is what makes a sweep observable: each parameter
    set produces a different ``obj.plate.bbox.size.x`` in its own spatial.json.
    """

    design = tmp_path / "design.py"
    design.write_text(
        """
from cadx import publish


def build(params):
    width = float(params["width"])
    publish(
        "plate",
        {
            "bbox": {"min": [0, 0, 0], "max": [width, 20, 4]},
            "mass_properties": {"volume": width * 80},
            "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
        },
        role="final",
    )
""",
        encoding="utf-8",
    )
    return design


def run_width_model(tmp_path: Path, width: float) -> Path:
    """Run the width design once and return its run directory."""

    design = write_width_design(tmp_path)
    (tmp_path / "params.yaml").write_text(f"width: {width}\n", encoding="utf-8")
    payload = parse_stdout_json(
        run_cadx(tmp_path, "run", str(design), "--params", "params.yaml")
    )
    assert payload["status"] == "ok"
    return tmp_path / payload["artifact_dir"]


def write_requirements(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "requirements.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_parametric_sweep_all_sets_pass(tmp_path):
    """Two in-range parameter sets both pass and are aggregated correctly."""

    run_dir = run_width_model(tmp_path, width=40)
    requirements = write_requirements(
        tmp_path,
        """
units: mm
checks:
  - id: stack_up
    type: parametric
    params:
      - {width: 38}
      - {width: 42}
    checks:
      - id: width_in_range
        type: dimension
        target: obj.plate.bbox.size.x
        min: 36
        max: 44
""",
    )

    result = parse_stdout_json(
        run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements))
    )
    assert result["status"] == "pass"
    assert result["failed"] == []

    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))
    sweep = next(c for c in checks["checks"] if c["id"] == "stack_up")
    assert sweep["type"] == "parametric"
    assert sweep["status"] == "pass"
    assert sweep["passed"] == 2
    assert sweep["total"] == 2

    # Each set is recorded with the params it ran and its sub-check observations.
    observed_widths = sorted(
        set_record["checks"][0]["observed"] for set_record in sweep["sets"]
    )
    assert observed_widths == [38, 42]
    assert {set_record["params"]["width"] for set_record in sweep["sets"]} == {38, 42}
    assert all(set_record["status"] == "pass" for set_record in sweep["sets"])


def test_parametric_sweep_out_of_range_fails(tmp_path):
    """A single out-of-range parameter set fails the aggregated check."""

    run_dir = run_width_model(tmp_path, width=40)
    requirements = write_requirements(
        tmp_path,
        """
units: mm
checks:
  - id: stack_up
    type: parametric
    params:
      - {width: 38}
      - {width: 99}
    checks:
      - id: width_in_range
        type: dimension
        target: obj.plate.bbox.size.x
        min: 36
        max: 44
""",
    )

    result = parse_stdout_json(
        run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements))
    )
    assert result["status"] == "fail"
    assert "stack_up" in result["failed"]

    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))
    sweep = next(c for c in checks["checks"] if c["id"] == "stack_up")
    assert sweep["status"] == "fail"
    assert sweep["passed"] == 1
    assert sweep["total"] == 2

    failing = [s for s in sweep["sets"] if s["status"] != "pass"]
    assert len(failing) == 1
    assert failing[0]["params"]["width"] == 99


def test_sweep_subcommand(tmp_path):
    """`cadx sweep` evaluates only parametric checks and prints the verdict."""

    run_dir = run_width_model(tmp_path, width=40)
    requirements = write_requirements(
        tmp_path,
        """
units: mm
checks:
  - id: stack_up
    type: parametric
    params:
      - {width: 38}
      - {width: 42}
    checks:
      - id: width_in_range
        type: dimension
        target: obj.plate.bbox.size.x
        min: 36
        max: 44
""",
    )

    result = parse_stdout_json(
        run_cadx(tmp_path, "sweep", str(run_dir), "--requirements", str(requirements))
    )
    assert result["status"] == "pass"
    # The aggregated payload exposes the per-check parametric verdicts.
    sweeps = result["sweeps"]
    assert len(sweeps) == 1
    assert sweeps[0]["id"] == "stack_up"
    assert sweeps[0]["passed"] == 2
    assert sweeps[0]["total"] == 2


def test_unknown_check_type_errors(tmp_path):
    """An unknown requirement type yields a clear ValueError naming the type."""

    run_dir = run_width_model(tmp_path, width=40)
    requirements = write_requirements(
        tmp_path,
        """
units: mm
checks:
  - id: mystery
    type: bogus
    target: obj.plate.bbox.size.x
""",
    )

    result = run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements))
    assert result.returncode != 0
    assert "bogus" in result.stderr


def test_readme_lists_supported_check_types():
    """The README documents supported types and flags symmetry/visual as TODO."""

    repo_root = Path(__file__).resolve().parents[1]
    readme = (repo_root / "README.md").read_text(encoding="utf-8").lower()

    for supported in (
        "dimension",
        "topology",
        "clearance",
        "feature_count",
        "feature_dimension",
        "parametric",
    ):
        assert supported in readme

    # symmetry and visual must be explicitly named as not yet supported.
    assert "symmetry" in readme
    assert "visual" in readme


def test_parametric_set_run_failure_is_recorded(tmp_path):
    """A parameter set whose re-run errors is recorded with run_status, not a crash."""

    run_dir = run_width_model(tmp_path, width=40)
    requirements = write_requirements(
        tmp_path,
        """
units: mm
checks:
  - id: stack_up
    type: parametric
    params:
      - {width: 38}
      - {}
    checks:
      - id: width_in_range
        type: dimension
        target: obj.plate.bbox.size.x
        min: 36
        max: 44
""",
    )

    result = parse_stdout_json(
        run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements))
    )
    assert result["status"] == "fail"

    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))
    sweep = next(c for c in checks["checks"] if c["id"] == "stack_up")
    assert sweep["status"] == "fail"
    # The set whose design raised (missing width) carries a non-ok run_status and
    # no sub-checks, rather than crashing the whole evaluation.
    errored = [s for s in sweep["sets"] if s.get("run_status") not in (None, "ok")]
    assert errored
    assert errored[0]["checks"] == []
