"""Deterministic visual artifact generation.

The MVP renderer creates a contact sheet from spatial metrics. When richer CAD
rendering is available, this module can be extended to compose glTF screenshots
and hidden-line projections into the same contact-sheet contract.
"""

from __future__ import annotations

from pathlib import Path
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


def _draw_with_pillow(path: Path, spatial: dict[str, Any]) -> None:
    """Render a simple but informative contact sheet with Pillow."""

    from PIL import Image, ImageDraw, ImageFont

    width, height = 1000, 700
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    title = "CAD Agent Contact Sheet"
    draw.text((24, 20), title, fill=(20, 20, 20), font=font)

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

    for label, box in panels:
        draw.rectangle(box, outline=(40, 40, 40), width=2)
        draw.text((box[0] + 10, box[1] + 10), label, fill=(40, 40, 40), font=font)
        # The placeholder geometry rectangle is scaled from the first object
        # bbox. It is deterministic and gives agents a visual anchor even
        # before true shaded CAD rendering is installed.
        if objects:
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


def _write_projection_svg(shape: Any, target: Path, viewport_origin: tuple[int, int, int]) -> None:
    """Write one hidden-line SVG projection from a build123d shape."""

    from build123d import ExportSVG, LineType

    visible, hidden = shape.project_to_viewport(viewport_origin)
    exporter = ExportSVG(scale=10, margin=2)
    exporter.add_layer("Visible", line_color=(20, 20, 20))
    exporter.add_layer("Hidden", line_color=(130, 130, 130), line_type=LineType.ISO_DOT)
    exporter.add_shape(visible, layer="Visible")
    if len(hidden) > 0:
        exporter.add_shape(hidden, layer="Hidden")
    target.parent.mkdir(parents=True, exist_ok=True)
    exporter.write(target)


def _render_step_artifacts(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate deterministic SVG views and sections from the first STEP export.

    The first implementation renders one primary object because the MVP
    publishes a single final part. The manifest keeps object labels so a future
    multi-part renderer can extend this without changing the agent contract.
    """

    exports = _step_exports(run_dir)
    if not exports:
        return [], []

    from build123d import Plane, import_step

    export = exports[0]
    shape = import_step(export["path"])
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
    return rendered, sections


def render_run(run_dir: Path) -> dict[str, Any]:
    """Create the visual contact sheet for a run."""

    spatial_path = run_dir / "spatial.json"
    if not spatial_path.exists():
        from cadx.inspector import inspect_run

        inspect_run(run_dir)

    spatial = read_json(spatial_path)
    views, sections = _render_step_artifacts(run_dir)
    contact_sheet = run_dir / "views" / "contact.png"
    _draw_with_pillow(contact_sheet, spatial)
    manifest = {
        "schema_version": "1.0",
        "status": "ok",
        "contact_sheet": str(contact_sheet),
        "views": views,
        "sections": sections,
    }
    manifest_path = run_dir / "views" / "render_manifest.json"
    write_json(manifest_path, manifest)
    return {
        "status": "ok",
        "contact_sheet": str(contact_sheet),
        "manifest": str(manifest_path),
        "views": [str(contact_sheet)] + [view["path"] for view in views] + [section["path"] for section in sections],
    }
