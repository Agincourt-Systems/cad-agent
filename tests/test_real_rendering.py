import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytest.importorskip("build123d")


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Exercise the CLI the same way a coding agent would."""

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
    """Parse cadx's machine-readable stdout."""

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def make_box_run(tmp_path: Path) -> Path:
    """Create a simple real CAD run that render can consume."""

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
    return tmp_path / payload["artifact_dir"]


def test_render_generates_real_projection_svgs_from_step_export(tmp_path):
    run_dir = make_box_run(tmp_path)

    payload = parse_stdout_json(run_cadx(tmp_path, "render", str(run_dir)))
    manifest_path = tmp_path / payload["manifest"]
    manifest = json.loads(manifest_path.read_text())

    assert payload["status"] == "ok"
    assert Path(tmp_path / payload["contact_sheet"]).is_file()
    assert {view["name"] for view in manifest["views"]} == {"iso", "top", "front", "right"}
    assert {section["name"] for section in manifest["sections"]} == {
        "section_xy",
        "section_xz",
        "section_yz",
    }

    for artifact in [*manifest["views"], *manifest["sections"]]:
        svg_path = tmp_path / artifact["path"]
        svg = svg_path.read_text(encoding="utf-8")
        assert svg_path.is_file()
        assert "<svg" in svg
        assert "<path" in svg or "<line" in svg
        assert artifact["source_format"] == "step"
