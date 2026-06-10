"""Deterministic visual artifact generation.

The MVP renderer creates a contact sheet from spatial metrics. When richer CAD
rendering is available, this module can be extended to compose glTF screenshots
and hidden-line projections into the same contact-sheet contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cadx.files import read_json


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


def render_run(run_dir: Path) -> dict[str, Any]:
    """Create the visual contact sheet for a run."""

    spatial_path = run_dir / "spatial.json"
    if not spatial_path.exists():
        from cadx.inspector import inspect_run

        inspect_run(run_dir)

    spatial = read_json(spatial_path)
    contact_sheet = run_dir / "views" / "contact.png"
    _draw_with_pillow(contact_sheet, spatial)
    return {
        "status": "ok",
        "contact_sheet": str(contact_sheet),
        "views": [str(contact_sheet)],
    }
