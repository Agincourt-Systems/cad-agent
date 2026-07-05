"""ADR 0028: configurable light direction for `cadx shots`.

``--light X,Y,Z`` steers the rasterizer light per invocation and
``--light camera`` follows each view's camera so every shot is front-lit;
omitted, output is byte-identical to the legacy fixed light. The flagship
test pins the motivating fix: the ADR 0027 side view renders declared red at
ambient level under the default light and brightly under camera light.
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
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def bright_red_population(image_path: Path) -> int:
    from PIL import Image

    image = Image.open(image_path).convert("RGB")
    colors = image.getcolors(maxcolors=10**6)
    assert colors is not None
    return sum(count for count, (r, g, b) in colors if r > 150 and g < 60 and b < 60)


def test_resolve_shot_light():
    """None -> default; vectors parse and normalize; camera follows the view."""

    from cadx import renderer

    default = renderer._resolve_shot_light(None, renderer.SHADED_CAMERAS["iso"])
    assert default == pytest.approx(renderer._normalize(renderer.DEFAULT_LIGHT))

    up = renderer._resolve_shot_light("0,0,1", renderer.SHADED_CAMERAS["iso"])
    assert up == pytest.approx((0.0, 0.0, 1.0))

    scaled = renderer._resolve_shot_light("0,0,5", renderer.SHADED_CAMERAS["iso"])
    assert scaled == pytest.approx((0.0, 0.0, 1.0))

    side = renderer.SHADED_CAMERAS["side"]
    assert renderer._resolve_shot_light("camera", side) == pytest.approx(side.view)

    with pytest.raises(ValueError, match="banana"):
        renderer._resolve_shot_light("banana", side)
    with pytest.raises(ValueError, match="0,0"):
        renderer._resolve_shot_light("0,0", side)
    with pytest.raises(ValueError, match="0,0,0"):
        renderer._resolve_shot_light("0,0,0", side)


def test_default_light_is_byte_stable(tmp_path):
    """Omitting light matches passing DEFAULT_LIGHT explicitly, byte for byte."""

    pytest.importorskip("PIL")
    from cadx import renderer

    square = [
        [(-20.0, -20.0, 0.0), (20.0, -20.0, 0.0), (20.0, 20.0, 10.0)],
        [(-20.0, -20.0, 0.0), (20.0, 20.0, 10.0), (-20.0, 20.0, 0.0)],
    ]
    batches = [{"triangles": square, "spec": {"color": (66, 132, 184)}, "label": None}]
    implicit = tmp_path / "implicit.png"
    explicit = tmp_path / "explicit.png"
    renderer._render_shaded(batches, implicit, (200, 160))
    renderer._render_shaded(
        batches, explicit, (200, 160), light=renderer._normalize(renderer.DEFAULT_LIGHT)
    )

    assert implicit.read_bytes() == explicit.read_bytes()


def write_red_steel_project(tmp_path: Path) -> Path:
    (tmp_path / "design.py").write_text(
        """
from build123d import Box, Location
from cadx import publish


def build(params):
    publish("left", Box(30, 20, 10), role="final", appearance="#ff0000")
    publish("right", Box(30, 20, 10), placement=Location((40, 0, 0)), appearance="steel")
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(tmp_path / "design.py")))
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


def shot_path(tmp_path: Path, payload: dict, index: int = 0) -> Path:
    path = Path(payload["shots"][index]["path"])
    return path if path.is_absolute() else tmp_path / path


def test_camera_light_brightens_side_view(tmp_path):
    """The dark side view becomes front-lit with one flag."""

    pytest.importorskip("build123d")
    pytest.importorskip("PIL")
    run_dir = write_red_steel_project(tmp_path)

    default = parse_stdout_json(run_cadx(tmp_path, "shots", str(run_dir), "--views", "side"))
    lit = parse_stdout_json(
        run_cadx(tmp_path, "shots", str(run_dir), "--views", "side", "--light", "camera", "--out", "lit")
    )

    assert bright_red_population(shot_path(tmp_path, default)) == 0
    assert bright_red_population(shot_path(tmp_path, lit)) > 100
    # The payload records both the request and the per-shot resolved vector:
    # the side camera's view is +Y.
    assert lit["light"] == "camera"
    assert lit["shots"][0]["light"] == pytest.approx([0.0, 1.0, 0.0])


def test_explicit_light_vector_accepted(tmp_path):
    """An explicit vector lights the flank and is recorded normalized."""

    pytest.importorskip("build123d")
    pytest.importorskip("PIL")
    run_dir = write_red_steel_project(tmp_path)

    payload = parse_stdout_json(
        run_cadx(tmp_path, "shots", str(run_dir), "--views", "side", "--light", "0,1,0.4")
    )

    assert bright_red_population(shot_path(tmp_path, payload)) > 100
    recorded = payload["shots"][0]["light"]
    assert recorded == pytest.approx([0.0, 0.9284766908852594, 0.37139067635410373])


def test_invalid_light_rejected(tmp_path):
    """Garbage light specs fail fast, naming the bad value."""

    pytest.importorskip("build123d")
    run_dir = write_red_steel_project(tmp_path)

    result = run_cadx(tmp_path, "shots", str(run_dir), "--light", "banana")
    assert result.returncode != 0
    assert "banana" in (result.stderr + result.stdout)
