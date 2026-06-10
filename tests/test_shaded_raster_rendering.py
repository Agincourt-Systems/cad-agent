import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image


pytest.importorskip("build123d")


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run cadx through the CLI."""

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


def test_render_writes_shaded_png_from_stl_export(tmp_path):
    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import *
from cadx import publish


def build(params):
    with BuildPart() as model:
        Box(30, 20, 5)
        with BuildSketch(Plane.XY):
            SlotOverall(12, 4)
        extrude(amount=10, mode=Mode.SUBTRACT, both=True)
        with Locations((6, 0, 5)):
            Cylinder(4, 8)

    publish("demo", model.part, role="final")
    return model.part
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")

    run_payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    render_payload = parse_stdout_json(run_cadx(tmp_path, "render", str(tmp_path / run_payload["artifact_dir"])))
    manifest = json.loads((tmp_path / render_payload["manifest"]).read_text(encoding="utf-8"))

    assert {raster["name"] for raster in manifest["rasters"]} == {"shaded_iso"}
    raster_path = tmp_path / manifest["rasters"][0]["path"]
    assert raster_path.is_file()

    image = Image.open(raster_path).convert("RGB")
    colors = image.getcolors(maxcolors=1_000_000)
    non_white = sum(count for count, color in colors if color != (255, 255, 255))

    assert image.size == (900, 650)
    assert non_white > image.width * image.height * 0.05
    assert len(colors) > 20
