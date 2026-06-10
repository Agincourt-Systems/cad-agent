import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


build123d = pytest.importorskip("build123d")


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run cadx in a subprocess so the test matches agent behavior."""

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
    """Decode the single JSON object cadx prints for agents."""

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_run_exports_real_build123d_part_and_writes_spatial_facts(tmp_path):
    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import *
from cadx import publish


def build(params):
    with BuildPart() as model:
        Box(10, 20, 5)

    publish("box", model.part, role="final")
    return model.part
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")

    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    run_dir = tmp_path / payload["artifact_dir"]

    assert payload["status"] == "ok"
    assert (run_dir / "box.step").is_file()
    assert (run_dir / "box.stl").is_file()
    assert (run_dir / "box.glb").is_file()
    assert (run_dir / "spatial.json").is_file()

    diagnostics = json.loads((run_dir / "diagnostics.json").read_text())
    assert diagnostics["runtime"]["build123d_version"] == build123d.__version__
    assert {export["format"] for export in diagnostics["exports"]} == {"step", "stl", "glb"}

    spatial = json.loads((run_dir / "spatial.json").read_text())
    [obj] = spatial["objects"]
    assert obj["label"] == "box"
    assert obj["bbox"]["size"] == pytest.approx([10, 20, 5])
    assert obj["mass_properties"]["volume"] == pytest.approx(1000)
    assert obj["topology"]["solids"] == 1
    assert obj["topology"]["faces"] == 6
    assert obj["topology"]["edges"] == 12
    assert obj["topology"]["vertices"] == 8
