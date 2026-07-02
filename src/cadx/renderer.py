"""Deterministic visual artifact generation.

The MVP renderer creates a contact sheet from spatial metrics. When richer CAD
rendering is available, this module can be extended to compose glTF screenshots
and hidden-line projections into the same contact-sheet contract.
"""

from __future__ import annotations

from math import sqrt
from pathlib import Path
import struct
from typing import Any

from cadx.files import read_json, write_json


VIEWPORTS = {
    "iso": (30, -30, 20),
    "top": (0, 0, 100),
    "front": (0, -100, 0),
    "right": (100, 0, 0),
}

SECTION_VIEWPORTS = {
    "section_xy": ("XY", (0, 0, 100)),
    "section_xz": ("XZ", (0, -100, 0)),
    "section_yz": ("YZ", (100, 0, 0)),
}


def _placeholder_rectangle(draw: Any, box: tuple[int, int, int, int], objects: list[dict[str, Any]]) -> None:
    """Draw the deterministic bbox-scaled placeholder rectangle for a panel.

    Used only for panels with no real raster to embed (synthetic designs, or
    views the headless renderer cannot rasterize), so an agent still gets a
    visual anchor.
    """

    if not objects:
        return
    bbox = objects[0]["bbox"]
    size = bbox.get("size", [1, 1, 1])
    max_size = max(size) or 1
    panel_w = box[2] - box[0] - 70
    panel_h = box[3] - box[1] - 70
    rect_w = max(20, int(panel_w * (size[0] / max_size)))
    rect_h = max(20, int(panel_h * (size[1] / max_size)))
    cx = (box[0] + box[2]) // 2
    cy = (box[1] + box[3]) // 2
    draw.rectangle(
        (cx - rect_w // 2, cy - rect_h // 2, cx + rect_w // 2, cy + rect_h // 2),
        outline=(22, 94, 150),
        width=3,
    )


def _load_shaded_raster(rasters: list[dict[str, Any]] | None) -> tuple[Any, str | None]:
    """Open the shaded isometric raster (if rendered) for contact-sheet embedding."""

    from PIL import Image

    shaded = next((raster for raster in (rasters or []) if raster.get("name") == "shaded_iso"), None)
    if not shaded:
        return None, None
    shaded_path = Path(shaded["path"])
    if not shaded_path.exists():
        return None, None
    try:
        return Image.open(shaded_path).convert("RGB"), str(shaded["path"])
    except Exception:
        return None, None


# Panels that embed the real shaded raster when one is available.
_SHADED_PANELS = {"ISO SHADED"}


def _draw_with_pillow(
    path: Path, spatial: dict[str, Any], rasters: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Render the contact sheet, embedding the real shaded raster where possible.

    Returns one ``contact_panels`` record per panel describing whether the real
    geometry raster was embedded (``source == "shaded_iso"``) or the placeholder
    fallback was used (``source is None``). The shaded isometric raster is the
    only real view that can be embedded headless — there is no SVG rasterizer
    available — so the orthographic/section panels keep the placeholder anchor.
    """

    from PIL import Image, ImageDraw, ImageFont

    width, height = 1000, 700
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.text((24, 20), "CAD Agent Contact Sheet", fill=(20, 20, 20), font=font)

    objects = spatial.get("objects", [])
    features = spatial.get("features", [])
    panels = [
        ("ISO SHADED", (24, 60, 320, 260)),
        ("TOP", (344, 60, 656, 260)),
        ("FRONT", (680, 60, 976, 260)),
        ("SECTION XY", (24, 300, 320, 500)),
        ("SECTION XZ", (344, 300, 656, 500)),
        ("CHECK OVERLAY", (680, 300, 976, 500)),
    ]

    shaded_image, shaded_source = _load_shaded_raster(rasters)
    contact_panels: list[dict[str, Any]] = []
    for label, box in panels:
        embedded = False
        if shaded_image is not None and label in _SHADED_PANELS:
            # Fit the real raster into the panel interior (below the label band).
            interior = (box[0] + 6, box[1] + 24, box[2] - 6, box[3] - 6)
            interior_w = interior[2] - interior[0]
            interior_h = interior[3] - interior[1]
            thumb = shaded_image.copy()
            thumb.thumbnail((interior_w, interior_h))
            offset_x = interior[0] + (interior_w - thumb.width) // 2
            offset_y = interior[1] + (interior_h - thumb.height) // 2
            image.paste(thumb, (offset_x, offset_y))
            embedded = True

        # Draw the frame and label on top of any embedded raster.
        draw.rectangle(box, outline=(40, 40, 40), width=2)
        draw.text((box[0] + 10, box[1] + 10), label, fill=(40, 40, 40), font=font)
        if embedded:
            contact_panels.append({"label": label, "source": "shaded_iso", "path": shaded_source})
        else:
            _placeholder_rectangle(draw, box, objects)
            contact_panels.append({"label": label, "source": None, "path": None})

    summary = f"units={spatial.get('units', 'mm')} | objects={len(objects)} | features={len(features)}"
    if objects:
        first = objects[0]
        size = first["bbox"].get("size", ["?", "?", "?"])
        topology = first.get("topology", {})
        summary += f" | bbox={size[0]} x {size[1]} x {size[2]}"
        summary += f" | faces={topology.get('faces', '?')} | edges={topology.get('edges', '?')}"
    draw.text((24, 560), summary, fill=(20, 20, 20), font=font)

    for index, feature in enumerate(features[:8]):
        draw.text((24, 590 + index * 14), f"{feature['id']}: {feature.get('kind')}", fill=(120, 20, 20), font=font)

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return contact_panels


def _resolve_export_path(run_dir: Path, export_path: str) -> Path:
    """Resolve export paths saved in diagnostics from either cwd or run dir.

    Older diagnostics may contain relative paths such as
    ``artifacts/runs/0001/box.step``. Rendering is often invoked with an
    absolute run directory, so the resolver tries the recorded path first and
    then falls back to a same-directory filename lookup.
    """

    path = Path(export_path)
    if path.exists():
        return path
    if path.is_absolute():
        return path
    return run_dir / path.name


def _step_exports(run_dir: Path) -> list[dict[str, Any]]:
    """Return STEP exports from diagnostics, normalized to local paths."""

    diagnostics_path = run_dir / "diagnostics.json"
    if not diagnostics_path.exists():
        return []
    diagnostics = read_json(diagnostics_path)
    exports: list[dict[str, Any]] = []
    for export in diagnostics.get("exports", []):
        if export.get("format") == "step":
            exports.append({**export, "path": str(_resolve_export_path(run_dir, export["path"]))})
    return exports


def _stl_exports(run_dir: Path) -> list[dict[str, Any]]:
    """Return STL exports from diagnostics, normalized to local paths."""

    diagnostics_path = run_dir / "diagnostics.json"
    if not diagnostics_path.exists():
        return []
    diagnostics = read_json(diagnostics_path)
    exports: list[dict[str, Any]] = []
    for export in diagnostics.get("exports", []):
        if export.get("format") == "stl":
            exports.append({**export, "path": str(_resolve_export_path(run_dir, export["path"]))})
    return exports


def _normalize(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    """Return a unit vector, preserving zero vectors."""

    length = sqrt(sum(component * component for component in vector))
    if length == 0:
        return (0.0, 0.0, 0.0)
    return tuple(component / length for component in vector)


def _triangle_normal(vertices: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    """Compute a triangle normal from its vertices."""

    a, b, c = vertices
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    normal = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    return _normalize(normal)


def _read_binary_stl(path: Path) -> list[list[tuple[float, float, float]]]:
    """Read triangles from a binary STL file."""

    data = path.read_bytes()
    if len(data) < 84:
        return []
    triangle_count = struct.unpack_from("<I", data, 80)[0]
    triangles: list[list[tuple[float, float, float]]] = []
    offset = 84
    for _ in range(triangle_count):
        if offset + 50 > len(data):
            break
        floats = struct.unpack_from("<12f", data, offset)
        vertices = [
            (floats[3], floats[4], floats[5]),
            (floats[6], floats[7], floats[8]),
            (floats[9], floats[10], floats[11]),
        ]
        triangles.append(vertices)
        offset += 50
    return triangles


def _project_iso(point: tuple[float, float, float]) -> tuple[float, float, float]:
    """Project a 3D point into deterministic isometric screen coordinates."""

    x, y, z = point
    screen_x = (x - y) * 0.8660254038
    screen_y = (x + y) * 0.5 - z
    depth = x + y + z
    return screen_x, screen_y, depth


def _render_stl_shaded(stl_path: Path, target: Path, size: tuple[int, int] = (900, 650)) -> None:
    """Render a simple shaded isometric PNG from STL triangles.

    This is a deterministic software rasterizer. It is intentionally small and
    dependency-light so shaded output works in headless environments where VTK
    or browser rendering may not be available.
    """

    from PIL import Image, ImageDraw

    triangles = _read_binary_stl(stl_path)
    image = Image.new("RGB", size, "white")
    if not triangles:
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        return

    projected = [
        {
            "points": [_project_iso(vertex) for vertex in triangle],
            "normal": _triangle_normal(triangle),
        }
        for triangle in triangles
    ]
    all_x = [point[0] for triangle in projected for point in triangle["points"]]
    all_y = [point[1] for triangle in projected for point in triangle["points"]]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    margin = 48
    scale = min(
        (size[0] - margin * 2) / max(max_x - min_x, 1e-6),
        (size[1] - margin * 2) / max(max_y - min_y, 1e-6),
    )

    light = _normalize((0.35, -0.45, 0.82))
    base = (66, 132, 184)
    draw = ImageDraw.Draw(image)
    for triangle in sorted(projected, key=lambda item: sum(point[2] for point in item["points"])):
        points_2d = [
            (
                margin + (point[0] - min_x) * scale,
                margin + (point[1] - min_y) * scale,
            )
            for point in triangle["points"]
        ]
        normal = triangle["normal"]
        shade = 0.35 + 0.65 * max(0.0, sum(normal[index] * light[index] for index in range(3)))
        color = tuple(max(0, min(255, int(component * shade))) for component in base)
        draw.polygon(points_2d, fill=color, outline=(32, 54, 72))

    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)


def _write_projection_svg(shape: Any, target: Path, viewport_origin: tuple[int, int, int]) -> None:
    """Write one hidden-line SVG projection from a build123d shape."""

    from build123d import ExportSVG, LineType

    exporter = ExportSVG(scale=10, margin=2)
    exporter.add_layer("Visible", line_color=(20, 20, 20))
    exporter.add_layer("Hidden", line_color=(130, 130, 130), line_type=LineType.ISO_DOT)

    shapes = [shape] if hasattr(shape, "project_to_viewport") else list(shape)
    for item in shapes:
        visible, hidden = item.project_to_viewport(viewport_origin)
        if len(visible) > 0:
            exporter.add_shape(visible, layer="Visible")
        if len(hidden) > 0:
            exporter.add_shape(hidden, layer="Hidden")
    target.parent.mkdir(parents=True, exist_ok=True)
    exporter.write(target)


def _render_step_artifacts(
    run_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate deterministic SVG views and sections from the first STEP export.

    The first implementation renders one primary object because the MVP
    publishes a single final part. The manifest keeps object labels so a future
    multi-part renderer can extend this without changing the agent contract.

    Returns ``(views, sections, warnings)``. An unreadable STEP export skips
    the projected views with a ``render_step_failed`` warning instead of
    aborting the render — the contact sheet from ``spatial.json`` still gets
    produced. ``import_step`` on garbage can either raise or quietly return an
    empty shape depending on the OCCT version (projecting the empty shape then
    fails much later), so an import with no faces is treated as the same
    failure up front.
    """

    exports = _step_exports(run_dir)
    if not exports:
        return [], [], []

    from build123d import Plane, import_step

    export = exports[0]
    try:
        shape = import_step(export["path"])
        if not list(shape.faces()):
            raise ValueError("STEP import produced no geometry")
    except Exception as exc:
        return [], [], [
            {
                "type": "render_step_failed",
                "label": export.get("label"),
                "path": export["path"],
                "message": str(exc),
            }
        ]
    views_dir = run_dir / "views"
    rendered: list[dict[str, Any]] = []
    for name, origin in VIEWPORTS.items():
        target = views_dir / f"{name}.svg"
        _write_projection_svg(shape, target, origin)
        rendered.append(
            {
                "name": name,
                "path": str(target),
                "source": export["path"],
                "source_format": "step",
                "label": export.get("label"),
                "viewport_origin": list(origin),
            }
        )

    sections: list[dict[str, Any]] = []
    for name, (plane_name, origin) in SECTION_VIEWPORTS.items():
        plane = getattr(Plane, plane_name)
        section_shape = shape.intersect(plane)
        if section_shape is None:
            continue
        target = views_dir / f"{name}.svg"
        _write_projection_svg(section_shape, target, origin)
        sections.append(
            {
                "name": name,
                "path": str(target),
                "source": export["path"],
                "source_format": "step",
                "label": export.get("label"),
                "plane": plane_name,
                "viewport_origin": list(origin),
            }
        )
    return rendered, sections, []


def _render_raster_artifacts(run_dir: Path) -> list[dict[str, Any]]:
    """Render shaded raster artifacts from STL exports."""

    exports = _stl_exports(run_dir)
    if not exports:
        return []

    export = exports[0]
    target = run_dir / "views" / "shaded_iso.png"
    _render_stl_shaded(Path(export["path"]), target)
    return [
        {
            "name": "shaded_iso",
            "path": str(target),
            "source": export["path"],
            "source_format": "stl",
            "label": export.get("label"),
            "camera": "isometric",
        }
    ]


def render_run(run_dir: Path) -> dict[str, Any]:
    """Create the visual contact sheet for a run."""

    spatial_path = run_dir / "spatial.json"
    if not spatial_path.exists():
        from cadx.inspector import inspect_run

        inspect_run(run_dir)

    spatial = read_json(spatial_path)
    views, sections, warnings = _render_step_artifacts(run_dir)
    rasters = _render_raster_artifacts(run_dir)
    contact_sheet = run_dir / "views" / "contact.png"
    contact_panels = _draw_with_pillow(contact_sheet, spatial, rasters=rasters)
    manifest = {
        "schema_version": "1.0",
        "status": "ok",
        "contact_sheet": str(contact_sheet),
        "views": views,
        "sections": sections,
        "rasters": rasters,
        "contact_panels": contact_panels,
        "warnings": warnings,
    }
    manifest_path = run_dir / "views" / "render_manifest.json"
    write_json(manifest_path, manifest)
    return {
        "status": "ok",
        "contact_sheet": str(contact_sheet),
        "manifest": str(manifest_path),
        "views": [str(contact_sheet)]
        + [view["path"] for view in views]
        + [section["path"] for section in sections]
        + [raster["path"] for raster in rasters],
    }
