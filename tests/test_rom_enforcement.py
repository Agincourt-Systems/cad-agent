"""ADR 0039: enforceable joint limits in the motion-envelope sweep (D-009).

A mate driven outside its declared range emits only a ``mate_out_of_range``
warning, so joint limits cannot fail a gate. The ``parametric`` check gains an
opt-in ``fail_on_range_violation``: when enabled, a swept pose that violates its
declared range fails that set (and thus the aggregate) with a message naming the
mate and the value. Default stays warn-only. Kernel-free: a synthetic prismatic
mate exercises the whole path without build123d.
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


SYNTHETIC_BLOCK = """
        {
            "bbox": {"min": [0, 0, 0], "max": [10, 10, 10]},
            "mass_properties": {"volume": 1000},
            "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
        }
"""


# A synthetic prismatic slider whose travel is driven from params and whose
# declared travel_range is (0, 20). Swept to travel=30 it is out of range.
DESIGN_BODY = f"""
from cadx import publish, mate


def build(params):
    publish("base", {SYNTHETIC_BLOCK}, role="final")
    publish("slider", {SYNTHETIC_BLOCK},
            mate=mate(to="base", kind="prismatic", anchor=[0, 0, 0], target=[0, 0, 0],
                      travel=params.get("t", 0), travel_range=(0, 20)))
"""


def _write_design(tmp_path: Path, params: str = "t: 0\n") -> None:
    (tmp_path / "design.py").write_text(DESIGN_BODY, encoding="utf-8")
    (tmp_path / "params.yaml").write_text(params, encoding="utf-8")


def _requirements(fail_on_range: bool) -> str:
    option = "\n    fail_on_range_violation: true" if fail_on_range else ""
    return f"""
units: mm
checks:
  - id: rom
    type: parametric{option}
    params:
      - t: 10
      - t: 30
    checks:
      - id: size_ok
        type: dimension
        target: obj.slider.bbox.size.x
        equals: 10
"""


def _run(tmp_path: Path) -> Path:
    payload = json.loads(run_cadx(tmp_path, "run", str(tmp_path / "design.py")).stdout)
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


def test_range_violation_warns_but_passes_by_default(tmp_path):
    """Without the option, an out-of-range swept pose warns but does not fail."""

    _write_design(tmp_path)
    (tmp_path / "requirements.yaml").write_text(_requirements(False), encoding="utf-8")
    run_dir = _run(tmp_path)

    result = run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", "requirements.yaml")
    assert result.returncode == 0, result.stderr
    evaluation = json.loads(result.stdout)
    assert evaluation["status"] == "pass", evaluation

    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))
    sweep = checks["checks"][0]
    assert sweep["type"] == "parametric"
    assert [entry["status"] for entry in sweep["sets"]] == ["pass", "pass"]

    # The out-of-range pose still emits the warning (warn-only default): confirm
    # by running the design directly at the offending travel.
    _write_design(tmp_path, params="t: 30\n")
    oor_dir = tmp_path / json.loads(run_cadx(tmp_path, "run", str(tmp_path / "design.py")).stdout)["artifact_dir"]
    diagnostics = json.loads((oor_dir / "diagnostics.json").read_text(encoding="utf-8"))
    oor = [w for w in diagnostics["warnings"] if w["type"] == "mate_out_of_range"]
    assert oor and oor[0]["label"] == "slider"


def test_fail_on_range_violation_fails_and_names_mate(tmp_path):
    """With the option, the out-of-range set fails and names the mate and value."""

    _write_design(tmp_path)
    (tmp_path / "requirements.yaml").write_text(_requirements(True), encoding="utf-8")
    run_dir = _run(tmp_path)

    result = run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", "requirements.yaml")
    assert result.returncode == 0, result.stderr
    evaluation = json.loads(result.stdout)
    assert evaluation["status"] == "fail", evaluation

    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))
    sweep = checks["checks"][0]
    assert sweep["type"] == "parametric"
    # In-range set passes; out-of-range set fails purely on the range violation.
    assert [entry["status"] for entry in sweep["sets"]] == ["pass", "fail"]

    violations = sweep["sets"][1]["range_violations"]
    assert violations and violations[0]["label"] == "slider"
    message = violations[0]["message"]
    assert "30" in message and "20" in message and "travel_range" in message
