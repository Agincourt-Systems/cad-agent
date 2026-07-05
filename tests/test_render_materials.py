"""ADR 0027: appearance materials for shaded renders.

Per-part appearance (preset name or ``#rrggbb``) declared via
``publish(appearance=...)`` or implied by ``publish_part_meta(material=...)``
colors the shaded raster and every ``cadx shots`` camera. Undeclared parts
cycle a palette whose first entry is the legacy blue with the legacy shading
formula, keeping single-part default output pixel-identical. Pixel assertions
are statistical (color-family populations), matching the ADR 0011/0019 style.
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


def color_population(image_path: Path, predicate) -> int:
    """Count pixels satisfying a color predicate."""

    from PIL import Image

    image = Image.open(image_path).convert("RGB")
    colors = image.getcolors(maxcolors=10**6)
    assert colors is not None
    return sum(count for count, color in colors if predicate(*color))


def is_red_family(r: int, g: int, b: int) -> bool:
    return r > 120 and g < 60 and b < 60


def is_neutral_gray(r: int, g: int, b: int) -> bool:
    return 60 < r < 215 and max(r, g, b) - min(r, g, b) < 25


def is_legacy_blue_family(r: int, g: int, b: int) -> bool:
    return b > r + 30 and b > 90


def is_palette_orange_family(r: int, g: int, b: int) -> bool:
    return r > 100 and r > b + 30 and b < g < r


# ---------------------------------------------------------------- unit tier


def test_materials_presets_and_hex_resolution():
    """Core presets exist with their distinguishing fields; hex resolves."""

    from cadx.materials import DEFAULT_PALETTE, MATERIALS, resolve_appearance

    for name in ("steel", "stainless_steel", "aluminum", "brass", "black_oxide", "carbon_fiber", "glass"):
        assert name in MATERIALS, name

    steel_name, steel = resolve_appearance("steel")
    assert steel_name == "steel"
    assert steel["specular"] > 0

    carbon = resolve_appearance("carbon_fiber")[1]
    assert carbon.get("two_tone") is not None

    glass = resolve_appearance("glass")[1]
    assert glass.get("alpha") is not None and glass["alpha"] < 255

    hex_name, hex_spec = resolve_appearance("#ff8800")
    assert hex_name == "#ff8800"
    assert tuple(hex_spec["color"]) == (255, 136, 0)
    assert hex_spec.get("specular", 0) == 0

    assert resolve_appearance("unobtanium") is None

    # The palette's first entry is the legacy blue with the legacy
    # diffuse-only spec, so undeclared single parts render as before.
    assert tuple(DEFAULT_PALETTE[0]["color"]) == (66, 132, 184)
    assert DEFAULT_PALETTE[0].get("specular", 0) == 0
    assert len(DEFAULT_PALETTE) >= 4


def test_part_meta_material_maps_to_presets():
    """BOM material strings imply presets; stainless wins over steel."""

    from cadx.materials import material_for_part_meta

    assert material_for_part_meta("6061-T6 Aluminum") == "aluminum"
    assert material_for_part_meta("304 Stainless Steel") == "stainless_steel"
    assert material_for_part_meta("Steel, mild") == "steel"
    assert material_for_part_meta("carbon fiber sheet 2mm") == "carbon_fiber"
    assert material_for_part_meta("FR4") is None
    assert material_for_part_meta(None) is None


def test_two_tone_varies_by_facet():
    """Carbon fiber picks its alternate tone by facet centroid, deterministically."""

    from cadx.materials import resolve_appearance
    from cadx.renderer import _shade_triangle

    spec = resolve_appearance("carbon_fiber")[1]
    normal = (0.0, 0.0, 1.0)
    light = (0.0, 0.0, 1.0)
    view = (0.0, 0.0, 1.0)
    fill_a = _shade_triangle(spec, normal, (0.0, 0.0, 0.0), light, view)[0]
    fill_b = _shade_triangle(spec, normal, (2.0, 0.0, 0.0), light, view)[0]
    fill_a_again = _shade_triangle(spec, normal, (0.0, 0.0, 0.0), light, view)[0]

    assert fill_a != fill_b
    assert fill_a == fill_a_again


def test_glass_alpha_blends_over_background(tmp_path):
    """A translucent facet shows a blend of itself and what lies behind it."""

    pytest.importorskip("PIL")
    from cadx.materials import resolve_appearance
    from cadx.renderer import _render_shaded

    red = {"color": (255, 0, 0)}
    glass = resolve_appearance("glass")[1]
    # Opaque red slab facing +Z at z=0; glass facet floating above at z=10
    # covering the middle. Painter order (iso depth) draws the glass last.
    square = [
        [(-20.0, -20.0, 0.0), (20.0, -20.0, 0.0), (20.0, 20.0, 0.0)],
        [(-20.0, -20.0, 0.0), (20.0, 20.0, 0.0), (-20.0, 20.0, 0.0)],
    ]
    glass_facet = [[(-30.0, -30.0, 10.0), (30.0, -30.0, 10.0), (0.0, 40.0, 10.0)]]

    def blend_pixels(path: Path) -> int:
        return color_population(path, lambda r, g, b: 100 < r < 240 and 40 < g < 200 and 30 < b < 200)

    without = tmp_path / "without.png"
    _render_shaded([{"triangles": square, "spec": red, "label": "slab"}], without, (300, 240))
    with_glass = tmp_path / "with.png"
    _render_shaded(
        [
            {"triangles": square, "spec": red, "label": "slab"},
            {"triangles": glass_facet, "spec": glass, "label": "lens"},
        ],
        with_glass,
        (300, 240),
    )

    assert blend_pixels(without) == 0
    assert blend_pixels(with_glass) > 50


# ------------------------------------------------------------- kernel tier


def write_two_part_project(tmp_path: Path, left_kwargs: str, right_kwargs: str) -> None:
    (tmp_path / "design.py").write_text(
        f"""
from build123d import Box, Location
from cadx import publish


def build(params):
    publish("left", Box(30, 20, 10), role="final"{left_kwargs})
    publish("right", Box(30, 20, 10), placement=Location((40, 0, 0)){right_kwargs})
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")


def run_and_render(tmp_path: Path) -> tuple[Path, dict]:
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(tmp_path / "design.py")))
    assert payload["status"] == "ok", payload
    run_dir = tmp_path / payload["artifact_dir"]
    parse_stdout_json(run_cadx(tmp_path, "render", str(run_dir)))
    manifest = json.loads((run_dir / "views" / "render_manifest.json").read_text(encoding="utf-8"))
    return run_dir, manifest


def test_declared_appearances_color_the_render(tmp_path):
    """Explicit appearances produce their color populations and part records."""

    pytest.importorskip("build123d")
    pytest.importorskip("PIL")
    write_two_part_project(tmp_path, ', appearance="#ff0000"', ', appearance="steel"')
    run_dir, manifest = run_and_render(tmp_path)

    shaded = run_dir / "views" / "shaded_iso.png"
    assert color_population(shaded, is_red_family) > 200
    assert color_population(shaded, is_neutral_gray) > 200

    parts = {(part["label"], part["appearance"]) for part in manifest["rasters"][0]["parts"]}
    assert parts == {("left", "#ff0000"), ("right", "steel")}


def test_shots_render_materials(tmp_path):
    """Materials apply to every shots camera, not just the legacy iso."""

    pytest.importorskip("build123d")
    pytest.importorskip("PIL")
    write_two_part_project(tmp_path, ', appearance="#ff0000"', ', appearance="steel"')
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(tmp_path / "design.py")))
    run_dir = tmp_path / payload["artifact_dir"]

    shots = parse_stdout_json(run_cadx(tmp_path, "shots", str(run_dir), "--views", "side"))
    side = Path(shots["shots"][0]["path"])
    if not side.is_absolute():
        side = tmp_path / side

    # The side camera faces the ambient-lit side of the boxes (the fixed
    # light sits on -Y), so red shades darker here than in the iso view.
    assert color_population(side, lambda r, g, b: r > 60 and g < 40 and b < 40 and r > 2 * g) > 100
    assert {part["appearance"] for part in shots["parts"]} == {"#ff0000", "steel"}


def test_unknown_appearance_warns_and_falls_back(tmp_path):
    """A typo'd appearance renders via the palette with a manifest warning."""

    pytest.importorskip("build123d")
    pytest.importorskip("PIL")
    write_two_part_project(tmp_path, ', appearance="unobtanium"', "")
    run_dir, manifest = run_and_render(tmp_path)

    warnings = [w for w in manifest["warnings"] if w["type"] == "appearance_unknown"]
    assert warnings and warnings[0]["label"] == "left"
    assert "unobtanium" in warnings[0]["message"]
    left = next(part for part in manifest["rasters"][0]["parts"] if part["label"] == "left")
    assert left["appearance"].startswith("palette")
    # Still renders: the part gets the legacy blue palette slot.
    assert color_population(run_dir / "views" / "shaded_iso.png", is_legacy_blue_family) > 200


def test_undeclared_assembly_gets_distinct_palette_colors(tmp_path):
    """A bare two-part run renders distinguishable parts with no declarations."""

    pytest.importorskip("build123d")
    pytest.importorskip("PIL")
    write_two_part_project(tmp_path, "", "")
    run_dir, manifest = run_and_render(tmp_path)

    shaded = run_dir / "views" / "shaded_iso.png"
    assert color_population(shaded, is_legacy_blue_family) > 200
    assert color_population(shaded, is_palette_orange_family) > 200


def test_part_meta_material_colors_the_render(tmp_path):
    """BOM metadata alone implies the preset — no appearance keyword needed."""

    pytest.importorskip("build123d")
    pytest.importorskip("PIL")
    (tmp_path / "design.py").write_text(
        """
from build123d import Box
from cadx import publish, publish_part_meta


def build(params):
    publish("plate", Box(30, 20, 10), role="final")
    publish_part_meta("plate", vendor="SendCutSend", material="6061-T6 Aluminum")
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    run_dir, manifest = run_and_render(tmp_path)

    plate = next(part for part in manifest["rasters"][0]["parts"] if part["label"] == "plate")
    assert plate["appearance"] == "aluminum"
    assert color_population(run_dir / "views" / "shaded_iso.png", is_neutral_gray) > 200
