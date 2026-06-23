"""Red tests for ADR 0019: real-geometry contact sheet (deficiency D7).

The contact sheet's ISO panel must embed the real shaded raster
(views/shaded_iso.png) instead of a placeholder rectangle. These tests
exercise the full CLI run -> render flow with real build123d geometry and
assert on contact.png pixels and on the render_manifest contract.

Before ADR 0019: the ISO panel is a near-white box with a single blue
placeholder rectangle outline (~0.06 non-white fraction, ~3 distinct colors),
so the diversity/fraction/manifest assertions fail.
After ADR 0019: the ISO panel reproduces the shaded raster's pixel signature
(high non-white fraction, many distinct shading colors, blue-dominant hue) and
render_manifest records the embedded source.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image


pytest.importorskip("build123d")


# Contact-sheet ISO panel interior, matching the renderer panel box
# ("ISO SHADED", (24, 60, 320, 260)). We sample inside the frame.
ISO_PANEL_BOX = (24, 60, 320, 260)


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run cadx through the CLI exactly as an agent would."""

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


def parse_stdout_json(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def make_real_run(tmp_path: Path) -> Path:
    """Create a real plate-with-hole run that exports STL."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import *
from cadx import publish


def build(params):
    with BuildPart() as model:
        Box(40, 25, 4)
        with Locations((10, 0, 0), (-10, 0, 0)):
            Cylinder(3, 20, mode=Mode.SUBTRACT)
    publish("plate", model.part, role="final")
    return model.part
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(
        run_cadx(tmp_path, "run", str(design), "--params", "params.yaml")
    )
    return tmp_path / payload["artifact_dir"]


def _non_white_stats(image: Image.Image):
    """Return (non_white_fraction, distinct_color_count, mean_rgb_of_nonwhite)."""

    rgb = image.convert("RGB")
    colors = rgb.getcolors(maxcolors=10 ** 6)
    assert colors is not None
    total = rgb.width * rgb.height
    non_white = [(count, col) for count, col in colors if col != (255, 255, 255)]
    non_white_count = sum(count for count, _ in non_white)
    if non_white_count:
        r = sum(count * col[0] for count, col in non_white) / non_white_count
        g = sum(count * col[1] for count, col in non_white) / non_white_count
        b = sum(count * col[2] for count, col in non_white) / non_white_count
    else:
        r = g = b = 0.0
    return non_white_count / total, len(colors), (r, g, b)


def test_contact_sheet_uses_real_views(tmp_path):
    run_dir = make_real_run(tmp_path)
    payload = parse_stdout_json(run_cadx(tmp_path, "render", str(run_dir)))

    contact_path = tmp_path / payload["contact_sheet"]
    assert contact_path.is_file()

    shaded_path = run_dir / "views" / "shaded_iso.png"
    assert shaded_path.is_file(), "ADR 0011 shaded raster must exist before composition"
    shaded_frac, shaded_colors, _ = _non_white_stats(Image.open(shaded_path))
    # Sanity: the shaded raster really is a rich, mostly-filled image.
    assert shaded_frac > 0.2
    assert shaded_colors > 15

    contact = Image.open(contact_path)
    iso_panel = contact.crop(ISO_PANEL_BOX)
    frac, distinct, mean_rgb = _non_white_stats(iso_panel)

    # A placeholder panel is mostly white (~0.06 filled) with ~3 colors. A real
    # embedded shaded raster fills a large fraction and carries the lambert
    # shading gradient (many distinct colors).
    assert frac > 0.20, (
        f"ISO panel is mostly white ({frac:.3f} filled) -> still a placeholder"
    )
    assert distinct > 15, (
        f"ISO panel has only {distinct} colors -> a flat placeholder outline, "
        "not the shaded gradient"
    )

    # The shaded part's signature is blue-dominant (base (66,132,184)); a white
    # panel with a thin outline cannot satisfy fraction + diversity + hue.
    r, g, b = mean_rgb
    assert b > r and b > g, (
        f"ISO panel non-white mean {mean_rgb} is not blue-dominant -> "
        "not derived from shaded_iso.png"
    )


def test_render_manifest_records_embedded_panel_source(tmp_path):
    run_dir = make_real_run(tmp_path)
    payload = parse_stdout_json(run_cadx(tmp_path, "render", str(run_dir)))

    manifest = json.loads((tmp_path / payload["manifest"]).read_text(encoding="utf-8"))

    # Additive contract: existing keys remain.
    for key in ("contact_sheet", "views", "sections", "rasters"):
        assert key in manifest

    assert "contact_panels" in manifest, "ADR 0019 adds contact_panels to manifest"
    panels = manifest["contact_panels"]
    assert isinstance(panels, list) and panels

    iso = [p for p in panels if "ISO" in p.get("label", "").upper()]
    assert iso, "an ISO panel must be recorded"
    iso = iso[0]
    assert iso.get("source") == "shaded_iso", iso
    embedded_path = tmp_path / iso["path"]
    assert embedded_path.is_file()
    assert embedded_path.name == "shaded_iso.png"


def test_contact_sheet_falls_back_for_synthetic_design(tmp_path):
    """A dict-only design (no STL) still renders a contact sheet via fallback."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from cadx import publish


def build(params):
    publish(
        "synthetic",
        {
            "bbox": {"min": [0, 0, 0], "max": [10, 5, 2], "size": [10, 5, 2]},
            "mass_properties": {"volume": 100.0, "area": 160.0},
            "topology": {"faces": 6, "edges": 12, "vertices": 8},
        },
        role="final",
    )
    return None
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(
        run_cadx(tmp_path, "run", str(design), "--params", "params.yaml")
    )
    run_dir = tmp_path / payload["artifact_dir"]

    render_payload = parse_stdout_json(run_cadx(tmp_path, "render", str(run_dir)))
    contact_path = tmp_path / render_payload["contact_sheet"]
    assert contact_path.is_file()

    manifest = json.loads(
        (tmp_path / render_payload["manifest"]).read_text(encoding="utf-8")
    )
    # No STL -> no shaded raster -> ISO panel uses the placeholder fallback.
    assert manifest["rasters"] == []
    panels = manifest.get("contact_panels", [])
    iso = [p for p in panels if "ISO" in p.get("label", "").upper()]
    assert iso, "panel records exist even in fallback"
    assert iso[0].get("source") is None
