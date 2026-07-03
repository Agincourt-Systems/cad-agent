"""ADR 0026: multi-view shaded screenshots (`cadx shots`).

The shaded rasterizer was isometric-only.  These tests pin the
generalisation: named orthographic cameras, a byte-stable legacy iso
default, and a `cadx shots` command that renders several shaded PNGs of the
combined assembly.  The key behavioural test proves the cameras genuinely
differ (a plate wide in Y but thin in Z reads tall in `top` and flat in
`side`), not merely that the filenames differ.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops


pytest.importorskip("build123d")

from cadx import renderer  # noqa: E402


def run_cadx(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}
    return subprocess.run(
        [sys.executable, "-m", "cadx.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def parse_json(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def content_bbox(path: Path) -> tuple[int, int]:
    """Width, height of the non-white content region of a PNG."""
    image = Image.open(path).convert("RGB")
    white = Image.new("RGB", image.size, (255, 255, 255))
    bbox = ImageChops.difference(image, white).getbbox()
    assert bbox is not None, f"{path} is blank"
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def run_plate_assembly(tmp_path: Path) -> Path:
    """A two-part model long in X (80), wide in Y (60), thin in Z (6)."""
    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import Box, Location
from cadx import publish


def build(params):
    publish("plate", Box(80, 60, 6), role="final")
    publish("stud", Box(6, 6, 20), placement=Location((0, 0, 13)))
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


# --- Criterion 2: named cameras & orthographic projection -------------------

def test_shaded_cameras_registry_has_documented_views():
    assert {"iso", "top", "side", "front", "rear"} <= set(renderer.SHADED_CAMERAS)


def test_iso_camera_is_the_legacy_projection():
    p = (3.0, -5.0, 2.0)
    assert renderer.SHADED_CAMERAS["iso"](p) == renderer._project_iso(p)


def test_top_camera_puts_plus_y_higher_on_image():
    top = renderer.SHADED_CAMERAS["top"]
    # screen_y grows downward, so +Y (up in a plan view) must map lower.
    assert top((0.0, 10.0, 0.0))[1] < top((0.0, -10.0, 0.0))[1]


# --- Criterion 1: default projector unchanged, explicit projector differs ---

def test_default_projector_writes_nonblank_png(tmp_path):
    run_dir = run_plate_assembly(tmp_path)
    stl = run_dir / "assembly.stl"
    target = tmp_path / "legacy.png"
    renderer._render_stl_shaded(stl, target)  # no projector -> legacy iso
    assert content_bbox(target)[0] > 0


def test_explicit_projector_changes_the_image(tmp_path):
    run_dir = run_plate_assembly(tmp_path)
    stl = run_dir / "assembly.stl"
    iso_png = tmp_path / "iso.png"
    top_png = tmp_path / "top.png"
    renderer._render_stl_shaded(stl, iso_png, project=renderer.SHADED_CAMERAS["iso"])
    renderer._render_stl_shaded(stl, top_png, project=renderer.SHADED_CAMERAS["top"])
    assert iso_png.read_bytes() != top_png.read_bytes()


# --- Criterion 4: cameras genuinely differ (behavioural) --------------------

def test_top_and_side_views_have_different_content_shape(tmp_path):
    run_dir = run_plate_assembly(tmp_path)
    stl = run_dir / "assembly.stl"
    top_png = tmp_path / "top.png"
    side_png = tmp_path / "side.png"
    renderer._render_stl_shaded(stl, top_png, project=renderer.SHADED_CAMERAS["top"])
    renderer._render_stl_shaded(stl, side_png, project=renderer.SHADED_CAMERAS["side"])
    tw, th = content_bbox(top_png)
    sw, sh = content_bbox(side_png)
    # Plate is wide in Y (60) and thin in Z (6): the top view (up=+Y) is much
    # taller relative to width than the side view (up=+Z).
    assert (th / tw) > (sh / sw) * 2, ((tw, th), (sw, sh))


# --- Criterion 3 & 5: the `cadx shots` command ------------------------------

def test_shots_command_renders_assembly_views(tmp_path):
    run_dir = run_plate_assembly(tmp_path)
    payload = parse_json(run_cadx(tmp_path, "shots", str(run_dir)))
    assert payload["status"] == "ok", payload
    names = {shot["name"] for shot in payload["shots"]}
    assert names == {"iso", "side", "top"}  # default set
    for shot in payload["shots"]:
        assert Path(shot["path"]).is_file()
        assert content_bbox(Path(shot["path"]))[0] > 0
    # Multi-part run -> shot the combined assembly, like `render`.
    assert Path(payload["source"]).name == "assembly.stl"
    assert payload["label"] == "assembly"


def test_shots_views_flag_selects_subset(tmp_path):
    run_dir = run_plate_assembly(tmp_path)
    payload = parse_json(run_cadx(tmp_path, "shots", str(run_dir), "--views", "front,rear"))
    assert {shot["name"] for shot in payload["shots"]} == {"front", "rear"}


def test_shots_rejects_unknown_view(tmp_path):
    run_dir = run_plate_assembly(tmp_path)
    result = run_cadx(tmp_path, "shots", str(run_dir), "--views", "iso,banana")
    assert result.returncode != 0
    assert "banana" in (result.stderr + result.stdout)
