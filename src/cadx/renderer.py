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


def _bounds_union(objects: list[dict[str, Any]]) -> dict[str, list[float]] | None:
    """Union bounding box across all objects, or ``None`` without min/max data.

    For a single object this is exactly its own bbox, so single-part output is
    unchanged; for an assembly it is the extent of the whole placed design.
    """

    mins = [obj["bbox"]["min"] for obj in objects if "min" in obj.get("bbox", {}) and "max" in obj.get("bbox", {})]
    maxs = [obj["bbox"]["max"] for obj in objects if "min" in obj.get("bbox", {}) and "max" in obj.get("bbox", {})]
    if not mins:
        return None
    low = [min(vector[axis] for vector in mins) for axis in range(3)]
    high = [max(vector[axis] for vector in maxs) for axis in range(3)]
    return {"min": low, "max": high, "size": [high_v - low_v for low_v, high_v in zip(low, high)]}


def _summary_line(spatial: dict[str, Any]) -> str:
    """Contact-sheet summary text, unit-testable without decoding PNG bytes.

    One object keeps the original per-part line (bbox + face/edge counts). An
    assembly reports the union extent instead — per-part topology counts would
    be misleading attributed to the whole, and the assembly's overall envelope
    is what a human sanity-checks first.
    """

    objects = spatial.get("objects", [])
    features = spatial.get("features", [])
    summary = f"units={spatial.get('units', 'mm')} | objects={len(objects)} | features={len(features)}"
    if len(objects) == 1:
        first = objects[0]
        size = first["bbox"].get("size", ["?", "?", "?"])
        topology = first.get("topology", {})
        summary += f" | bbox={size[0]} x {size[1]} x {size[2]}"
        summary += f" | faces={topology.get('faces', '?')} | edges={topology.get('edges', '?')}"
    elif objects:
        union = _bounds_union(objects)
        if union is not None:
            size = union["size"]
            summary += f" | assembly_bbox={size[0]:g} x {size[1]:g} x {size[2]:g}"
    return summary


def _placeholder_rectangle(draw: Any, box: tuple[int, int, int, int], objects: list[dict[str, Any]]) -> None:
    """Draw the deterministic bbox-scaled placeholder rectangle for a panel.

    Used only for panels with no real raster to embed (synthetic designs, or
    views the headless renderer cannot rasterize), so an agent still gets a
    visual anchor. The rectangle is proportioned to the union bbox so an
    assembly's anchor reflects the whole design, not its first part.
    """

    if not objects:
        return
    bbox = _bounds_union(objects) or objects[0]["bbox"]
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

    draw.text((24, 560), _summary_line(spatial), fill=(20, 20, 20), font=font)

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


def _primary_export(exports: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the export to render: the combined assembly when one exists.

    ADR 0023 flags the whole-assembly artifact with ``assembly: true``; when a
    run has one, the projections/raster should show the assembled design. A
    single-part run keeps the original first-export behavior.
    """

    return next((export for export in exports if export.get("assembly")), exports[0])


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


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _orthographic_projector(right, up, forward):
    """Build a projector for an orthonormal camera basis (ADR 0026).

    ``screen_x = p·right`` and ``screen_y = -(p·up)`` — negated because image
    ``y`` grows downward, so the camera ``up`` axis maps to *smaller* screen
    ``y`` (higher on the page). ``depth = p·forward`` with ``forward`` pointing
    from the model toward the camera, so larger depth is nearer and the
    painter's-order fill (far→near) paints near triangles last.
    """

    right = _normalize(right)
    up = _normalize(up)
    forward = _normalize(forward)

    def project(point):
        return (_dot(point, right), -_dot(point, up), _dot(point, forward))

    # The camera's view direction, used by material specular highlights
    # (ADR 0027). ``forward`` points from the model toward the camera.
    project.view = forward
    return project


# Named cameras for `cadx shots`.  ``iso`` is the exact legacy projection (not
# a basis approximation) so existing output is unchanged; the rest are
# orthographic elevations/plans built from (right, up, forward) bases.
SHADED_CAMERAS = {
    "iso": _project_iso,
    "top": _orthographic_projector((1, 0, 0), (0, 1, 0), (0, 0, 1)),    # look down -Z
    "side": _orthographic_projector((1, 0, 0), (0, 0, 1), (0, 1, 0)),   # look along -Y
    "front": _orthographic_projector((0, 1, 0), (0, 0, 1), (-1, 0, 0)),  # nose toward viewer
    "rear": _orthographic_projector((0, -1, 0), (0, 0, 1), (1, 0, 0)),  # tail toward viewer
}

# The legacy oblique iso projects along roughly (1, 1, 1); material specular
# highlights (ADR 0027) use this as its view direction.
_project_iso.view = _normalize((1.0, 1.0, 1.0))

# The fixed light direction chosen in ADR 0011 (up-and-behind-left, tuned for
# the iso view). `render` always uses it; `shots --light` can override it
# (ADR 0028). Points from the model toward the light.
DEFAULT_LIGHT = (0.35, -0.45, 0.82)


def _resolve_shot_light(spec: Any, project: Any) -> tuple[float, float, float]:
    """Resolve a shot light spec to a normalized direction (ADR 0028).

    ``None`` keeps the legacy default; ``"camera"`` follows the given
    projector's view vector (front-lighting that camera); ``"X,Y,Z"`` (or a
    3-sequence) is an explicit direction, normalized. Anything else raises a
    ``ValueError`` naming the bad spec, matching the unknown-view fail-fast.
    """

    if spec is None:
        return _normalize(DEFAULT_LIGHT)
    if isinstance(spec, str):
        if spec == "camera":
            return getattr(project, "view", _project_iso.view)
        parts = [part.strip() for part in spec.split(",")]
        if len(parts) != 3:
            raise ValueError(f"light must be 'camera' or 'X,Y,Z', got {spec!r}")
        try:
            vector = tuple(float(part) for part in parts)
        except ValueError as exc:
            raise ValueError(f"light must be 'camera' or 'X,Y,Z', got {spec!r}") from exc
    elif isinstance(spec, (list, tuple)) and len(spec) == 3:
        vector = tuple(float(component) for component in spec)
    else:
        raise ValueError(f"light must be 'camera' or 'X,Y,Z', got {spec!r}")
    normalized = _normalize(vector)
    if normalized == (0.0, 0.0, 0.0):
        raise ValueError(f"light vector must be non-zero, got {spec!r}")
    return normalized

# The exact pre-ADR-0027 look: legacy blue, legacy lighting weights, no
# specular. ``_render_stl_shaded`` uses this so its output stays byte-stable.
_LEGACY_SPEC = {"color": (66, 132, 184), "ambient": 0.35, "diffuse": 0.65, "specular": 0.0}


def _shade_triangle(
    spec: dict[str, Any],
    normal: tuple[float, float, float],
    centroid: tuple[float, float, float],
    light: tuple[float, float, float],
    view: tuple[float, float, float],
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Shade one facet under a material spec; returns ``(fill, outline)``.

    With the legacy weights and zero specular this computes exactly the ADR
    0011 formula, so default-material output is unchanged. ``two_tone`` picks
    the alternate color from a deterministic centroid hash (a ~1.25 mm checker
    that reads as a weave/speckle at facet scale); the specular term is a
    Blinn-style highlight that pushes the diffuse color toward white.
    """

    from math import floor

    color = spec["color"]
    two_tone = spec.get("two_tone")
    if two_tone is not None:
        weave = int(floor(centroid[0] * 0.8) + floor(centroid[1] * 0.8) + floor(centroid[2] * 0.8)) % 2
        if weave:
            color = two_tone
    intensity = float(spec.get("ambient", 0.35)) + float(spec.get("diffuse", 0.65)) * max(0.0, _dot(normal, light))
    shaded = [component * intensity for component in color]
    specular = float(spec.get("specular", 0.0))
    if specular > 0.0:
        half = _normalize(tuple(l + v for l, v in zip(light, view)))
        highlight = max(0.0, _dot(normal, half)) ** float(spec.get("shininess", 16))
        shaded = [component + (255.0 - component) * specular * highlight for component in shaded]
    fill = tuple(max(0, min(255, int(component))) for component in shaded)
    outline = tuple(spec.get("outline", (32, 54, 72)))
    return fill, outline


def _render_shaded(
    batches: list[dict[str, Any]],
    target: Path,
    size: tuple[int, int] = (900, 650),
    project=_project_iso,
    light: tuple[float, float, float] | None = None,
) -> None:
    """Rasterize per-part triangle batches with per-material shading.

    Every batch is ``{"triangles", "spec", "label"}``; all facets merge into
    one global painter's-order sort so parts occlude each other correctly.
    Translucent specs (``alpha`` < 255) are alpha-composited in paint order —
    glass shows what sits behind it — and the canvas stays plain RGB whenever
    every batch is opaque, preserving legacy byte-stability.
    """

    from PIL import Image, ImageDraw

    faces: list[dict[str, Any]] = []
    for batch in batches:
        spec = batch["spec"]
        for triangle in batch["triangles"]:
            faces.append(
                {
                    "points": [project(vertex) for vertex in triangle],
                    "normal": _triangle_normal(triangle),
                    "centroid": tuple(sum(vertex[axis] for vertex in triangle) / 3.0 for axis in range(3)),
                    "spec": spec,
                }
            )

    translucent = any(int(batch["spec"].get("alpha", 255)) < 255 for batch in batches)
    mode = "RGBA" if translucent else "RGB"
    image = Image.new(mode, size, (255, 255, 255, 255) if translucent else "white")
    if not faces:
        target.parent.mkdir(parents=True, exist_ok=True)
        image.convert("RGB").save(target) if translucent else image.save(target)
        return

    all_x = [point[0] for face in faces for point in face["points"]]
    all_y = [point[1] for face in faces for point in face["points"]]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    margin = 48
    scale = min(
        (size[0] - margin * 2) / max(max_x - min_x, 1e-6),
        (size[1] - margin * 2) / max(max_y - min_y, 1e-6),
    )

    light = _normalize(DEFAULT_LIGHT) if light is None else light
    view = getattr(project, "view", _project_iso.view)
    draw = ImageDraw.Draw(image)
    for face in sorted(faces, key=lambda item: sum(point[2] for point in item["points"])):
        points_2d = [
            (
                margin + (point[0] - min_x) * scale,
                margin + (point[1] - min_y) * scale,
            )
            for point in face["points"]
        ]
        fill, outline = _shade_triangle(face["spec"], face["normal"], face["centroid"], light, view)
        alpha = int(face["spec"].get("alpha", 255))
        if translucent and alpha < 255:
            overlay = Image.new("RGBA", size, (0, 0, 0, 0))
            ImageDraw.Draw(overlay).polygon(points_2d, fill=(*fill, alpha), outline=(*outline, alpha))
            image.alpha_composite(overlay)
            draw = ImageDraw.Draw(image)
        elif translucent:
            draw.polygon(points_2d, fill=(*fill, 255), outline=(*outline, 255))
        else:
            draw.polygon(points_2d, fill=fill, outline=outline)

    target.parent.mkdir(parents=True, exist_ok=True)
    (image.convert("RGB") if translucent else image).save(target)


def _render_stl_shaded(
    stl_path: Path,
    target: Path,
    size: tuple[int, int] = (900, 650),
    project=_project_iso,
) -> None:
    """Render a simple shaded PNG from STL triangles.

    This is a deterministic software rasterizer. It is intentionally small and
    dependency-light so shaded output works in headless environments where VTK
    or browser rendering may not be available.

    ``project`` maps a 3D point to ``(screen_x, screen_y, depth)`` and defaults
    to the legacy isometric projection, so callers that pass no projector get
    byte-identical output to before (ADR 0026). Named cameras live in
    ``SHADED_CAMERAS``. Since ADR 0027 this is a one-batch wrapper over
    ``_render_shaded`` using the exact legacy material, which keeps the output
    byte-identical for this single-source path too.
    """

    triangles = _read_binary_stl(stl_path)
    _render_shaded([{"triangles": triangles, "spec": dict(_LEGACY_SPEC), "label": None}], target, size, project)


def _shaded_batches(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build per-part shaded batches with resolved appearances (ADR 0027).

    Returns ``(batches, parts, warnings)``. Triangles come from the per-part
    STL exports (placements are baked in, so they compose in the assembly
    frame); the combined assembly STL has no part boundaries and is used only
    as a fallback when no per-part exports exist. Appearance resolution per
    part, most explicit first: ``publish(appearance=...)`` metadata, then a
    preset implied by ``publish_part_meta(material=...)``, then the default
    palette by part index. Unknown declared names degrade to the palette with
    an ``appearance_unknown`` warning.
    """

    from cadx.materials import DEFAULT_PALETTE, MATERIALS, material_for_part_meta, resolve_appearance

    exports = _stl_exports(run_dir)
    if not exports:
        return [], [], []
    part_exports = [export for export in exports if not export.get("assembly")]
    if not part_exports:
        part_exports = [_primary_export(exports)]

    metadata_by_label: dict[Any, dict[str, Any]] = {}
    spatial_order: dict[Any, int] = {}
    spatial_path = run_dir / "spatial.json"
    if spatial_path.exists():
        spatial = read_json(spatial_path)
        for index, obj in enumerate(spatial.get("objects", [])):
            metadata_by_label[obj.get("label")] = obj.get("metadata") or {}
            spatial_order[obj.get("label")] = index
    material_by_label: dict[Any, Any] = {}
    diagnostics_path = run_dir / "diagnostics.json"
    if diagnostics_path.exists():
        diagnostics = read_json(diagnostics_path)
        for record in diagnostics.get("part_meta", []):
            material_by_label[record.get("label")] = record.get("material")

    # Stable palette assignment: follow the published object order.
    part_exports = sorted(part_exports, key=lambda export: spatial_order.get(export.get("label"), 1_000_000))

    batches: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for index, export in enumerate(part_exports):
        label = export.get("label")
        name: str | None = None
        spec: dict[str, Any] | None = None
        declared = metadata_by_label.get(label, {}).get("appearance")
        if declared is not None:
            resolved = resolve_appearance(declared)
            if resolved is None:
                warnings.append(
                    {
                        "type": "appearance_unknown",
                        "label": label,
                        "message": f"unknown appearance {declared!r} on {label!r}; using the default palette",
                    }
                )
            else:
                name, spec = resolved
        if spec is None:
            preset = material_for_part_meta(material_by_label.get(label))
            if preset is not None:
                name, spec = preset, MATERIALS[preset]
        if spec is None:
            palette = DEFAULT_PALETTE[index % len(DEFAULT_PALETTE)]
            name, spec = palette["name"], palette
        try:
            triangles = _read_binary_stl(Path(export["path"]))
        except Exception as exc:
            warnings.append({"type": "raster_part_failed", "label": label, "message": str(exc)})
            continue
        batches.append({"triangles": triangles, "spec": spec, "label": label})
        parts.append({"label": label, "appearance": name})
    return batches, parts, warnings


def _nonempty(shape: Any) -> bool:
    """True if a build123d shape carries any projectable geometry.

    ``project_to_viewport`` on an empty shape collapses its look point to the
    origin; for a viewport camera on the +Z axis (top / section_xy at
    ``(0,0,100)``) the look direction then lands (anti)parallel to the default
    ``viewport_up=(0,0,1)`` and OCCT's ``gp_Dir::Crossed`` throws "zero norm".
    Guarding on emptiness here removes that crash class regardless of why the
    shape came in empty (e.g. an empty ``Compound.intersect`` result).
    """

    if shape is None:
        return False
    try:
        return bool(list(shape.faces()) or list(shape.edges()))
    except Exception:
        return False


def _plane_section(shape: Any, plane: Any) -> Any:
    """Section a shape by a plane, folding per-solid so a Compound sections.

    ``Compound.intersect(Plane)`` returns an empty shape on build123d 0.10 even
    when the child solids each intersect the plane, so the ADR-0023 combined
    assembly export produced empty section views.  Intersecting each solid and
    recombining the non-empty pieces restores real assembly sections; for a
    single-solid import ``.solids()`` yields the one solid, so single-part
    behaviour is unchanged.  Returns ``None`` when nothing crosses the plane.
    """

    from build123d import Compound

    solids = list(shape.solids())
    if not solids:
        piece = shape.intersect(plane)
        return piece if _nonempty(piece) else None
    pieces = []
    for solid in solids:
        piece = solid.intersect(plane)
        if _nonempty(piece):
            pieces.append(piece)
    if not pieces:
        return None
    return pieces[0] if len(pieces) == 1 else Compound(children=pieces)


def _write_projection_svg(shape: Any, target: Path, viewport_origin: tuple[int, int, int]) -> bool:
    """Write one hidden-line SVG projection from a build123d shape.

    Returns ``True`` if a file was written, ``False`` if the shape had no
    projectable geometry (the caller should then not record the view).
    """

    from build123d import ExportSVG, LineType

    shapes = [shape] if hasattr(shape, "project_to_viewport") else list(shape)
    shapes = [item for item in shapes if _nonempty(item)]
    if not shapes:
        return False

    exporter = ExportSVG(scale=10, margin=2)
    exporter.add_layer("Visible", line_color=(20, 20, 20))
    exporter.add_layer("Hidden", line_color=(130, 130, 130), line_type=LineType.ISO_DOT)

    for item in shapes:
        visible, hidden = item.project_to_viewport(viewport_origin)
        if len(visible) > 0:
            exporter.add_shape(visible, layer="Visible")
        if len(hidden) > 0:
            exporter.add_shape(hidden, layer="Hidden")
    target.parent.mkdir(parents=True, exist_ok=True)
    exporter.write(target)
    return True


def _render_step_artifacts(
    run_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate deterministic SVG views and sections from one STEP export.

    The rendered source is the combined assembly export when the run has one
    (ADR 0023), else the first per-part export; the manifest's ``label`` names
    which. One source keeps the view set bounded regardless of part count.

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

    export = _primary_export(exports)
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
        if not _write_projection_svg(shape, target, origin):
            continue
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
        # Fold the section per-solid: Compound.intersect(Plane) is empty on
        # build123d 0.10, so a combined assembly export would otherwise yield
        # empty (and crash-prone) section views (see docs/log 2026-07-03).
        section_shape = _plane_section(shape, plane)
        if section_shape is None:
            continue
        target = views_dir / f"{name}.svg"
        if not _write_projection_svg(section_shape, target, origin):
            continue
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


def _render_raster_artifacts(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Render shaded raster artifacts from STL exports.

    Pixels composite from the per-part STLs so each part shades with its
    resolved appearance (ADR 0027); ``source``/``label`` keep naming the
    primary STL — still the canonical single-file statement of what was drawn
    — and the record's ``parts`` list names each part's appearance.
    """

    exports = _stl_exports(run_dir)
    if not exports:
        return [], []

    export = _primary_export(exports)
    batches, parts, warnings = _shaded_batches(run_dir)
    target = run_dir / "views" / "shaded_iso.png"
    _render_shaded(batches, target)
    return [
        {
            "name": "shaded_iso",
            "path": str(target),
            "source": export["path"],
            "source_format": "stl",
            "label": export.get("label"),
            "camera": "isometric",
            "parts": parts,
        }
    ], warnings


def render_run(run_dir: Path) -> dict[str, Any]:
    """Create the visual contact sheet for a run."""

    spatial_path = run_dir / "spatial.json"
    if not spatial_path.exists():
        from cadx.inspector import inspect_run

        inspect_run(run_dir)

    spatial = read_json(spatial_path)
    views, sections, warnings = _render_step_artifacts(run_dir)
    rasters, raster_warnings = _render_raster_artifacts(run_dir)
    warnings = warnings + raster_warnings
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


DEFAULT_SHOT_VIEWS = ("iso", "side", "top")


def render_shots(
    run_dir: Path,
    views: list[str] | None = None,
    out_dir: Path | None = None,
    light: Any = None,
) -> dict[str, Any]:
    """Render shaded PNG screenshots of a run from several named cameras.

    The source is the run's primary STL — the combined ``assembly.stl`` when
    present (so a multi-part run is shot as a whole assembly, exactly like
    ``render``), else the single part.  One ``shaded_<camera>.png`` is written
    per requested view (default ``iso``/``side``/``top``) into ``out_dir``
    (default ``<run_dir>/views``).  ADR 0026.
    """

    names = list(views) if views else list(DEFAULT_SHOT_VIEWS)
    unknown = [name for name in names if name not in SHADED_CAMERAS]
    if unknown:
        valid = ", ".join(sorted(SHADED_CAMERAS))
        raise ValueError(f"unknown shot view(s) {unknown}: choose from {valid}")
    # Resolve the light per view up front (ADR 0028): "camera" follows each
    # view's own camera, and a bad spec fails before any file is written.
    lights = {name: _resolve_shot_light(light, SHADED_CAMERAS[name]) for name in names}

    exports = _stl_exports(run_dir)
    if not exports:
        return {
            "status": "ok",
            "source": None,
            "label": None,
            "shots": [],
            "parts": [],
            "warnings": [],
            "light": light,
        }

    export = _primary_export(exports)
    target_dir = out_dir if out_dir is not None else run_dir / "views"

    # Per-part material batches (ADR 0027): every camera shades each part
    # with its resolved appearance, exactly like `render`'s shaded raster.
    batches, parts, warnings = _shaded_batches(run_dir)
    shots: list[dict[str, Any]] = []
    for name in names:
        target = target_dir / f"shaded_{name}.png"
        _render_shaded(batches, target, project=SHADED_CAMERAS[name], light=lights[name])
        shots.append({"name": name, "camera": name, "path": str(target), "light": list(lights[name])})

    return {
        "status": "ok",
        "source": export["path"],
        "label": export.get("label"),
        "shots": shots,
        "parts": parts,
        "warnings": warnings,
        "light": light,
    }
