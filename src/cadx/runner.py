"""Execution pipeline for CAD design source files.

The runner treats design files as executable Python and records enough context
for an agent to reproduce or diagnose every run: source snapshot, resolved
parameters, publications, feature metadata, runtime errors, and optional CAD
exports.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any

from cadx.files import load_yaml, next_run_dir, read_json, write_json, write_yaml
from cadx.registry import clear_registry, publish, snapshot_registry


def _vector_from(value: Any) -> list[float]:
    """Convert build123d vector-like values into plain JSON numbers."""

    if isinstance(value, (list, tuple)):
        return [float(value[0]), float(value[1]), float(value[2])]
    return [float(value.X), float(value.Y), float(value.Z)]


def _normalize_bbox(raw: Any) -> dict[str, list[float]]:
    """Normalize several likely build123d bounding-box representations."""

    if isinstance(raw, dict):
        bbox = dict(raw)
    else:
        min_value = getattr(raw, "min", None)
        max_value = getattr(raw, "max", None)
        if callable(min_value):
            min_value = min_value()
        if callable(max_value):
            max_value = max_value()
        bbox = {"min": _vector_from(min_value), "max": _vector_from(max_value)}

    if "size" not in bbox and "min" in bbox and "max" in bbox:
        bbox["size"] = [max_v - min_v for min_v, max_v in zip(bbox["min"], bbox["max"])]
    return bbox


def _shape_method(obj: Any, name: str) -> Any:
    """Call a build123d-style method if present; otherwise return ``None``."""

    method = getattr(obj, name, None)
    if method is None:
        return None
    return method() if callable(method) else method


def _count_selector(obj: Any, name: str) -> int | None:
    """Count topology selectors such as ``faces()`` when the object supports them."""

    selected = _shape_method(obj, name)
    if selected is None:
        return None
    try:
        return len(selected)
    except TypeError:
        return None


def _mass_properties(obj: Any) -> dict[str, Any]:
    """Compute JSON-safe mass properties for a real build123d solid.

    ``volume`` and ``area`` are always attempted; ``center_of_mass`` and
    ``matrix_of_inertia`` (both from build123d, the latter a property about the
    part centroid in model units) are added when available. Each addition is
    guarded so a shape that cannot produce one — or a kernel-free environment —
    simply omits the key rather than failing the run.
    """

    properties: dict[str, Any] = {
        "volume": getattr(obj, "volume", None),
        "area": getattr(obj, "area", None),
    }
    try:
        from build123d import CenterOf

        properties["center_of_mass"] = _vector_from(obj.center(CenterOf.MASS))
    except Exception:
        pass
    try:
        properties["matrix_of_inertia"] = [[float(component) for component in row] for row in obj.matrix_of_inertia]
    except Exception:
        pass
    return properties


def _placed_object(entry: dict[str, Any]) -> Any:
    """Return the published shape moved into its assembly placement, if any.

    Centralizing the placement transform here keeps bounding box, mass
    properties, exports, and STEP-backed feature detection all observing the
    *same* placed geometry, so cross-part checks (hole alignment, interference)
    reason in one assembly frame. Synthetic dict publications and unplaced
    objects are returned unchanged. ADR 0015 reuses this helper so center of mass
    is reported in the same frame as the bounding box.
    """

    obj = entry["object"]
    placement = entry.get("placement")
    if placement is None or isinstance(obj, dict):
        return obj
    located = getattr(obj, "located", None)
    if not callable(located):
        return obj
    try:
        return located(placement)
    except Exception:
        return obj


def _placement_position(placement: Any) -> list[float] | None:
    """Extract a ``[x, y, z]`` translation from a Location or plain mapping.

    Accepts a build123d ``Location`` (``placement.position``), a
    ``{"position": [...]}`` mapping, or a bare 3-sequence so that kernel-free
    synthetic publications can be placed in tests without build123d.
    """

    if placement is None:
        return None
    position = getattr(placement, "position", None)
    if position is not None:
        return _vector_from(position)
    if isinstance(placement, dict) and "position" in placement:
        return [float(component) for component in placement["position"]]
    if isinstance(placement, (list, tuple)) and len(placement) == 3:
        return [float(component) for component in placement]
    return None


def _placement_record(placement: Any) -> dict[str, list[float]] | None:
    """Build the JSON ``{"position", "orientation"}`` record for a placement."""

    position = _placement_position(placement)
    if position is None:
        return None
    orientation = getattr(placement, "orientation", None)
    if orientation is not None:
        orientation = _vector_from(orientation)
    elif isinstance(placement, dict):
        orientation = [float(component) for component in placement.get("orientation", [0.0, 0.0, 0.0])]
    else:
        orientation = [0.0, 0.0, 0.0]
    return {"position": position, "orientation": orientation}


def _location_from(value: Any) -> Any:
    """Coerce a mate frame (Location, 3-sequence, or position mapping) to a Location.

    Orientation is honored only on real ``Location`` values; sequence and
    mapping forms are translation-only by design.
    """

    from build123d import Location

    if isinstance(value, Location):
        return value
    position = _placement_position(value)
    if position is None:
        raise ValueError(f"cannot interpret {value!r} as a mate frame")
    return Location(tuple(position))


def _mate_frames(entry: dict[str, Any], parent: dict[str, Any]) -> tuple[Any, Any]:
    """Return the raw (anchor, target) frames a mate spec designates.

    The joint spelling reads ``RigidJoint.relative_location`` off both shapes;
    a missing joint name raises so the caller degrades it to a ``mate_failed``
    warning naming the part and joint.
    """

    spec = entry["mate"]
    if spec.get("joint") is not None:
        joints = getattr(entry["object"], "joints", None) or {}
        parent_joints = getattr(parent["object"], "joints", None) or {}
        joint_name = spec["joint"]
        target_name = spec.get("target_joint") or joint_name
        if joint_name not in joints:
            raise ValueError(f"no joint {joint_name!r} on {entry['label']!r}")
        if target_name not in parent_joints:
            raise ValueError(f"no joint {target_name!r} on {parent['label']!r}")
        return joints[joint_name].relative_location, parent_joints[target_name].relative_location
    return spec["anchor"], spec["target"]


def _mate_pose(spec: dict[str, Any]) -> tuple[str, float, float]:
    """Return ``(kind, angle, travel)`` for a mate spec, defaulting to rigid."""

    kind = spec.get("kind", "rigid")
    angle = float(spec.get("angle", 0.0) or 0.0)
    travel = float(spec.get("travel", 0.0) or 0.0)
    return kind, angle, travel


def _mate_placement(entry: dict[str, Any], parent: dict[str, Any]) -> Any:
    """Compute the placement a mate resolves to: ``parent * target * J * anchor⁻¹``.

    ``J`` is the ADR 0025 pose about the target frame's local Z —
    ``Location((0, 0, travel), (0, 0, angle))`` — and the identity for rigid
    mates, reducing exactly to ADR 0024. Synthetic dict publications use plain
    position arithmetic so the mate machinery stays testable without a CAD
    kernel: their frames carry no orientation, so prismatic travel is a Z
    offset while revolute/cylindrical (a rotation of a bare bbox has no
    defined meaning) raise into the ``mate_failed`` degradation path. Real
    shapes compose build123d Locations, which the ADR 0024/0025 kernel probes
    verified against ``RigidJoint.connect_to`` and hand-checked hinge poses.
    """

    anchor, target = _mate_frames(entry, parent)
    kind, angle, travel = _mate_pose(entry["mate"])
    if isinstance(entry["object"], dict):
        if kind in ("revolute", "cylindrical"):
            raise ValueError(
                f"{kind!r} mate requires real geometry; synthetic publications "
                "support rigid and prismatic mates only"
            )
        anchor_position = _placement_position(anchor)
        target_position = _placement_position(target)
        if anchor_position is None or target_position is None:
            raise ValueError("synthetic mates need 3-sequence anchor/target frames")
        parent_position = _placement_position(parent.get("placement")) or [0.0, 0.0, 0.0]
        pose_offset = [0.0, 0.0, travel]
        return [
            parent_value + target_value - anchor_value + pose_value
            for parent_value, target_value, anchor_value, pose_value in zip(
                parent_position, target_position, anchor_position, pose_offset
            )
        ]
    placement = _location_from(target)
    if kind != "rigid":
        from build123d import Location

        placement = placement * Location((0.0, 0.0, travel), (0.0, 0.0, angle))
    placement = placement * _location_from(anchor).inverse()
    parent_placement = parent.get("placement")
    if parent_placement is None:
        return placement
    return _location_from(parent_placement) * placement


def _pose_range_warnings(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Warn when a resolved pose sits outside its declared joint limits.

    The part is still placed at the requested pose — the geometry stays honest
    to what was asked and downstream checks see the offending configuration —
    so the warning is the machine-visible record of the limit violation.
    """

    spec = entry["mate"]
    warnings: list[dict[str, Any]] = []
    for pose_key, range_key in (("angle", "angle_range"), ("travel", "travel_range")):
        declared = spec.get(range_key)
        if declared is None:
            continue
        value = float(spec.get(pose_key, 0.0) or 0.0)
        low, high = float(declared[0]), float(declared[1])
        if not low <= value <= high:
            warnings.append(
                {
                    "type": "mate_out_of_range",
                    "label": entry["label"],
                    "message": f"{pose_key} {value:g} outside declared {range_key} "
                    f"[{low:g}, {high:g}] on {entry['label']!r}",
                }
            )
    return warnings


def _resolve_mates(published: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve declarative mates into ordinary placements (ADR 0024).

    Iterates to a fixpoint so chains resolve regardless of publish order. All
    failure modes degrade to warnings rather than run failures: an unknown
    target label, a parent that itself could not be placed, a frame/joint the
    resolver cannot read, and mate cycles (whatever is left when no progress
    is possible) each leave that part unplaced with a descriptive record.
    """

    index = {entry["label"]: entry for entry in published}
    pending = {entry["label"]: entry for entry in published if entry.get("mate")}
    unplaced: set[str] = set()
    warnings: list[dict[str, Any]] = []
    progressed = True
    while pending and progressed:
        progressed = False
        for label in list(pending):
            entry = pending[label]
            to = entry["mate"].get("to")
            parent = index.get(to)
            if parent is None:
                warnings.append(
                    {
                        "type": "mate_unresolved",
                        "label": label,
                        "message": f"no published object {to!r} to mate {label!r} to",
                    }
                )
                unplaced.add(label)
                del pending[label]
                progressed = True
                continue
            if to in pending:
                continue  # the parent's own mate must resolve first
            if to in unplaced:
                warnings.append(
                    {
                        "type": "mate_unresolved",
                        "label": label,
                        "message": f"mate target {to!r} is itself unplaced; {label!r} left unplaced",
                    }
                )
                unplaced.add(label)
                del pending[label]
                progressed = True
                continue
            try:
                entry["placement"] = _mate_placement(entry, parent)
                warnings.extend(_pose_range_warnings(entry))
            except Exception as exc:
                warnings.append({"type": "mate_failed", "label": label, "message": str(exc)})
                unplaced.add(label)
            del pending[label]
            progressed = True
    cycle_labels = sorted(pending)
    for label in cycle_labels:
        warnings.append(
            {
                "type": "mate_unresolved",
                "label": label,
                "message": f"mate cycle involving {cycle_labels}; {label!r} left unplaced",
            }
        )
    return warnings


def _translate_bbox(bbox: dict[str, list[float]], position: list[float]) -> dict[str, list[float]]:
    """Shift a synthetic bounding box by a placement translation."""

    shifted = dict(bbox)
    if "min" in bbox and "max" in bbox:
        shifted["min"] = [value + offset for value, offset in zip(bbox["min"], position)]
        shifted["max"] = [value + offset for value, offset in zip(bbox["max"], position)]
        shifted["size"] = [high - low for low, high in zip(shifted["min"], shifted["max"])]
    return shifted


def _normalize_published(entry: dict[str, Any]) -> dict[str, Any]:
    """Convert a registry publication to JSON-safe spatial facts."""

    label = entry["label"]
    obj = entry["object"]
    placement = entry.get("placement")
    if isinstance(obj, dict):
        normalized = dict(obj)
        position = _placement_position(placement)
        if position is not None and "bbox" in normalized:
            normalized["bbox"] = _translate_bbox(normalized["bbox"], position)
    else:
        placed = _placed_object(entry)
        bbox_method = getattr(placed, "bounding_box", None)
        bbox = _normalize_bbox(bbox_method() if callable(bbox_method) else getattr(placed, "bbox", {}))
        normalized = {
            "bbox": bbox,
            "mass_properties": _mass_properties(placed),
            "topology": {
                "solids": _count_selector(placed, "solids"),
                "faces": _count_selector(placed, "faces"),
                "edges": _count_selector(placed, "edges"),
                "vertices": _count_selector(placed, "vertices"),
            },
        }

    normalized["id"] = f"obj.{label}"
    normalized["label"] = label
    normalized["role"] = entry.get("role", "part")
    if entry.get("metadata"):
        normalized["metadata"] = entry["metadata"]
    if "bbox" in normalized:
        normalized["bbox"] = _normalize_bbox(normalized["bbox"])
    placement_record = _placement_record(placement)
    if placement_record is not None:
        normalized["placement"] = placement_record
    # Record the declared relationship (JSON-safe fields only) alongside the
    # resolved transform, so agents see why the part sits where it does. The
    # ADR 0025 pose fields ride along; rigid mates carry none of them.
    if entry.get("mate"):
        spec = entry["mate"]
        normalized["mate"] = {
            key: spec[key]
            for key in ("to", "joint", "target_joint", "kind", "angle", "travel", "angle_range", "travel_range")
            if spec.get(key) is not None
        }
    return normalized


def _export_build123d_object(entry: dict[str, Any], run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Export a real build123d object when build123d is available.

    Synthetic dictionary objects used by tests are intentionally skipped. Export
    failures are warnings instead of hard run failures because structured
    inspection may still be useful to the agent.
    """

    obj = _placed_object(entry)
    if isinstance(obj, dict):
        return [], []

    try:
        from build123d import Unit, export_gltf, export_step, export_stl
    except Exception as exc:
        return [], [
            {
                "type": "export_dependency_missing",
                "message": f"build123d exporters are unavailable: {exc}",
            }
        ]

    label = entry["label"]
    exports: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    # D9: declare millimeter units explicitly so a downstream agent never has to
    # guess scale. STEP and glTF accept an explicit ``unit``; ``export_stl`` has
    # no unit parameter because STL is a unitless format, so its record carries
    # ``units: "mm"`` to document the modeling unit without claiming the file
    # itself encodes one.
    for extension, exporter, kwargs in [
        ("step", export_step, {"unit": Unit.MM}),
        ("stl", export_stl, {}),
        ("glb", export_gltf, {"binary": True, "unit": Unit.MM}),
    ]:
        target = run_dir / f"{label}.{extension}"
        try:
            exporter(obj, target, **kwargs)
            exports.append({"label": label, "format": extension, "path": str(target), "units": "mm"})
        except Exception as exc:
            warnings.append(
                {
                    "type": "export_failed",
                    "format": extension,
                    "label": label,
                    "message": str(exc),
                }
            )
    return exports, warnings


def _export_assembly(
    published_entries: list[dict[str, Any]], run_dir: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Export the whole placed assembly as combined STEP/STL/GLB (ADR 0023).

    Emitted only when a run publishes two or more real shapes — a single part
    already *is* its own export, and synthetic dict publications carry no
    geometry. Every real shape is included regardless of role: fixtures and
    reference geometry are useful visual context, and physical-role filtering
    stays where it has semantic weight (mass aggregation, BOM). Records carry
    an additive ``assembly: true`` flag so consumers (inspector, renderer)
    include or exclude the combined artifact deliberately.

    A part itself labeled ``"assembly"`` would collide with the combined
    filenames on disk and shadow the flag's label in every label-keyed index,
    so the combined export is skipped with a warning in that (rare) case.
    """

    real_entries = [entry for entry in published_entries if not isinstance(entry["object"], dict)]
    if len(real_entries) < 2:
        return [], []
    if any(entry["label"] == "assembly" for entry in real_entries):
        return [], [
            {
                "type": "assembly_export_skipped",
                "message": "a published part is labeled 'assembly'; skipping the combined "
                "assembly export so the part's own files are not clobbered",
            }
        ]

    try:
        from build123d import Compound, Unit, export_gltf, export_step, export_stl
    except Exception as exc:
        return [], [
            {
                "type": "export_dependency_missing",
                "message": f"build123d exporters are unavailable: {exc}",
            }
        ]

    try:
        # located() already baked each placement into the per-part shapes, so
        # the compound is the assembly exactly as the checks observed it.
        combined = Compound(children=[_placed_object(entry) for entry in real_entries])
    except Exception as exc:
        return [], [
            {"type": "export_failed", "format": "assembly", "label": "assembly", "message": str(exc)}
        ]

    exports: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for extension, exporter, kwargs in [
        ("step", export_step, {"unit": Unit.MM}),
        ("stl", export_stl, {}),
        ("glb", export_gltf, {"binary": True, "unit": Unit.MM}),
    ]:
        target = run_dir / f"assembly.{extension}"
        try:
            exporter(combined, target, **kwargs)
            exports.append(
                {
                    "label": "assembly",
                    "format": extension,
                    "path": str(target),
                    "units": "mm",
                    "assembly": True,
                }
            )
        except Exception as exc:
            warnings.append(
                {"type": "export_failed", "format": extension, "label": "assembly", "message": str(exc)}
            )
    return exports, warnings


def _reference_face(profile: Any) -> Any:
    """Return a planar face defining ``profile``'s plane, if one is derivable.

    A ``Sketch`` or ``Face`` exposes its face(s) directly. A bare planar ``Wire``
    or ``Compound`` does not, so we try to build a face from it to recover the
    plane; this lets a flat published on any plane be localized correctly rather
    than only translated in z.
    """

    faces = getattr(profile, "faces", None)
    if callable(faces):
        candidate_faces = list(faces())
        if candidate_faces:
            return max(candidate_faces, key=lambda candidate: candidate.area)
    if str(getattr(profile, "geom_type", "")).endswith("PLANE"):
        return profile
    try:
        from build123d import Face

        return Face(profile)
    except Exception:
        return None


def _flatten_to_xy(profile: Any) -> Any:
    """Relocate a flat profile into the global XY plane at ``z = 0``.

    ``ExportDXF`` projects geometry onto the XY plane and silently drops points
    that lie off it (it only emits a stderr warning), so a profile modeled on
    any other plane must first be brought into local plane coordinates. We
    localize through the profile's own plane when one is derivable and fall back
    to a pure z-translation otherwise.

    A final planarity guard refuses to return a profile whose geometry still lies
    off the XY plane, so the caller degrades to a ``flat_export_failed`` warning
    instead of writing a silently-degenerate DXF. This catches the two ways a
    bad input slips through: a solid (or otherwise non-planar shape) handed to
    ``publish_flat``, and a face-less profile on a non-XY plane that the z-only
    fallback cannot rotate flat.
    """

    from build123d import Plane

    face = _reference_face(profile)
    flattened = None
    if face is not None:
        try:
            flattened = Plane(face).to_local_coords(profile)
        except Exception:
            flattened = None
    if flattened is None:
        center = profile.center()
        flattened = profile.translate((0, 0, -center.Z))

    bbox = flattened.bounding_box()
    if max(abs(bbox.min.Z), abs(bbox.max.Z)) > 1e-5:
        raise ValueError("flat profile is not planar in the XY plane; cannot export a clean DXF")
    return flattened


def _write_dxf(
    profile: Any,
    layer: str,
    target: Path,
    *,
    extra_layers: list[tuple[str, list[Any]]] | None = None,
) -> None:
    """Write one millimeter-unit DXF with ``profile`` on ``layer``.

    ``ExportDXF(unit=Unit.MM)`` records ``$INSUNITS == 4`` so importers scale the
    part correctly (D9). ``extra_layers`` is an ordered list of
    ``(layer_name, shapes)`` so later sheet-metal work (ADR 0016) can place bend
    lines on a separate layer through this single writer rather than forking a
    second DXF code path.
    """

    from build123d import ExportDXF, Unit

    dxf = ExportDXF(unit=Unit.MM)
    dxf.add_layer(layer)
    dxf.add_shape(profile, layer)
    for extra_layer, shapes in extra_layers or []:
        dxf.add_layer(extra_layer)
        for shape in shapes:
            dxf.add_shape(shape, extra_layer)
    dxf.write(target)


def _auto_flat_profile(obj: Any) -> tuple[Any, float | None, str | None]:
    """Derive a flat cut profile from a constant-thickness prismatic solid.

    Returns ``(profile, thickness, None)`` for an accepted prism, or
    ``(None, None, reason)`` when the solid is not a single-thickness prism so
    the caller can skip it with an advisory warning. Acceptance is a volume
    invariant (``volume == largest_face_area * thickness``) rather than a fragile
    face count, so it is robust to interior cutouts — a holed plate is still a
    prism — and to face ordering.
    """

    from build123d import Face

    try:
        planar = [face for face in obj.faces() if str(getattr(face, "geom_type", "")).endswith("PLANE")]
    except Exception as exc:
        return None, None, f"object exposes no planar faces: {exc}"
    if not planar:
        return None, None, "no planar faces"

    largest = max(planar, key=lambda face: face.area)
    normal = largest.normal_at()
    center = largest.center()

    opposite = None
    for face in planar:
        if face is largest:
            continue
        face_normal = face.normal_at()
        alignment = abs(
            normal.X * face_normal.X + normal.Y * face_normal.Y + normal.Z * face_normal.Z
        )
        equal_area = abs(face.area - largest.area) <= 1e-4 * max(largest.area, 1.0)
        if abs(alignment - 1.0) <= 1e-6 and equal_area:
            opposite = face
            break
    if opposite is None:
        return None, None, "no parallel equal-area face pair"

    offset = opposite.center() - center
    thickness = abs(offset.X * normal.X + offset.Y * normal.Y + offset.Z * normal.Z)
    if thickness <= 0:
        return None, None, "degenerate thickness"

    volume = float(obj.volume)
    if abs(volume - largest.area * thickness) > 1e-3 * max(volume, 1.0):
        return None, None, "not a constant-thickness prism"

    profile = Face(largest.outer_wire(), largest.inner_wires())
    return profile, float(thickness), None


def _profile_area(profile: Any) -> float | None:
    """Planar area of a flat profile (outer outline minus interior cutouts)."""

    try:
        return float(profile.area)
    except Exception:
        return None


def _flat_export_record(
    label: str, target: Path, layer: str, thickness_mm: float | None, area_mm2: float | None = None
) -> dict[str, Any]:
    """Build the diagnostics export record for a written DXF.

    ``area_mm2`` is the true flat-pattern area (after any unfold), which the BOM
    (ADR 0017) quotes on — recorded here because the exporter holds the flat
    profile, where for a sheet-metal part the folded solid's faces do not.
    """

    record: dict[str, Any] = {
        "label": label,
        "format": "dxf",
        "path": str(target),
        "layer": layer,
        "thickness_mm": thickness_mm,
        "units": "mm",
    }
    if area_mm2 is not None:
        record["area_mm2"] = area_mm2
    return record


def _export_flats(flats: list[dict[str, Any]], run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Write a DXF for each explicit ``publish_flat`` publication.

    A profile the writer cannot handle becomes a ``flat_export_failed`` warning
    rather than a run failure, matching ``_export_build123d_object`` — structured
    inspection of the rest of the run stays useful.
    """

    if not flats:
        return [], []

    try:
        from build123d import ExportDXF  # noqa: F401  availability probe
    except Exception as exc:
        return [], [{"type": "export_dependency_missing", "message": f"build123d DXF export is unavailable: {exc}"}]

    exports: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for flat in flats:
        label = flat["label"]
        try:
            profile = _flatten_to_xy(flat["profile"])
            target = run_dir / f"{label}.dxf"
            _write_dxf(profile, flat["layer"], target)
            exports.append(
                _flat_export_record(label, target, flat["layer"], flat.get("thickness_mm"), _profile_area(profile))
            )
        except Exception as exc:
            warnings.append({"type": "flat_export_failed", "label": label, "message": str(exc)})
    return exports, warnings


def _auto_export_flats(
    published_entries: list[dict[str, Any]],
    explicit_flat_labels: set[str],
    run_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Auto-derive a DXF for each published constant-thickness prismatic solid.

    Skips synthetic dict publications, anything already published as an explicit
    flat, and any entry carrying an internal sheet-metal ``flat`` key (ADR 0016
    writes that part's bend DXF elsewhere, so auto-flatten must not overwrite it).
    A non-prismatic solid is skipped with an advisory ``autoflatten_skipped``
    warning, never a failure: a part that cannot be flattened still inspects fine.
    """

    exports: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for entry in published_entries:
        label = entry["label"]
        obj = _placed_object(entry)
        if isinstance(obj, dict):
            continue
        if label in explicit_flat_labels or entry.get("flat") is not None:
            continue
        # Auto-flatten only applies to solids. A returned/published sketch or
        # face (volume 0) is not a flatten candidate, so skip it silently rather
        # than emitting a confusing ``autoflatten_skipped`` warning for an object
        # that was never a prism in the first place.
        try:
            volume = float(getattr(obj, "volume", 0.0) or 0.0)
        except Exception:
            volume = 0.0
        if volume <= 0:
            continue
        try:
            profile, thickness, reason = _auto_flat_profile(obj)
            if profile is None:
                warnings.append({"type": "autoflatten_skipped", "label": label, "message": reason})
                continue
            flat_profile = _flatten_to_xy(profile)
            target = run_dir / f"{label}.dxf"
            _write_dxf(flat_profile, "cut", target)
            exports.append(_flat_export_record(label, target, "cut", thickness, _profile_area(flat_profile)))
        except Exception as exc:
            warnings.append({"type": "flat_export_failed", "label": label, "message": str(exc)})
    return exports, warnings


def _export_sheet_metal(entry: dict[str, Any], run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Export one sheet-metal part's flat DXF (cut + bend layers).

    Reuses the ADR 0013 ``_write_dxf`` writer, passing the bend lines through its
    ``extra_layers`` hook so the cut outline and the bend lines share one DXF on
    separate layers. The sheet-metal profile and bend lines are built in the XY
    plane by ``bend()``, so no plane localization is applied — that keeps the bend
    lines in exact register with the cut outline. The bend *table* is written
    separately (once per run) by ``_export_bend_table``.
    """

    flat = entry.get("flat")
    if not flat:
        return [], []

    label = entry["label"]
    try:
        from build123d import ExportDXF  # noqa: F401  availability probe
    except Exception as exc:
        return [], [{"type": "export_dependency_missing", "message": f"build123d DXF export is unavailable: {exc}"}]

    bend_lines = flat.get("bend_lines") or []
    try:
        target = run_dir / f"{label}.dxf"
        extra_layers = [("bend", bend_lines)] if bend_lines else None
        _write_dxf(flat["profile"], flat["layer"], target, extra_layers=extra_layers)
        # The flat profile (not the folded solid) carries the true developed area.
        record = _flat_export_record(label, target, flat["layer"], None, _profile_area(flat["profile"]))
        if bend_lines:
            record["layers"] = [flat["layer"], "bend"]
        return [record], []
    except Exception as exc:
        return [], [{"type": "flat_export_failed", "label": label, "message": str(exc)}]


def _export_bend_table(
    sheet_metal_entries: list[dict[str, Any]], run_dir: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Write a single ``bends.json`` aggregating every sheet-metal part's bends.

    One run can publish several bent parts, so the bend table is written once per
    run with each row tagged by its part ``label`` (rather than one file per part
    that would clobber the previous). A single ``format:"bends"`` export record
    points at the aggregate.
    """

    rows: list[dict[str, Any]] = []
    for entry in sheet_metal_entries:
        flat = entry.get("flat")
        if not flat:
            continue
        for row in flat.get("bends", []):
            rows.append({**row, "label": entry["label"]})
    if not rows:
        return [], []

    try:
        bends_path = run_dir / "bends.json"
        write_json(bends_path, {"schema_version": "1.0", "units": "mm", "bends": rows})
        return [{"label": None, "format": "bends", "path": str(bends_path), "units": "mm"}], []
    except Exception as exc:
        return [], [{"type": "bend_table_failed", "message": str(exc)}]


def _runtime_metadata() -> dict[str, Any]:
    """Capture runtime versions that affect reproducibility."""

    try:
        import build123d

        build123d_version = getattr(build123d, "__version__", None)
    except Exception:
        build123d_version = None

    return {
        "python_version": sys.version.split()[0],
        "build123d_version": build123d_version,
    }


def _load_module(source_path: Path) -> ModuleType:
    """Load the design source as an isolated Python module."""

    module_name = f"_cadx_design_{source_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load design source {source_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _execute_design(source_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Execute source and return the raw registry snapshot."""

    clear_registry()
    module = _load_module(source_path)
    build = getattr(module, "build", None)
    result = build(params) if callable(build) else None

    registry = snapshot_registry()
    if result is not None and not registry["published"]:
        publish("result", result, role="final")
        registry = snapshot_registry()
    return registry


def _stream_text(stream: str | bytes | None) -> str:
    """Normalize subprocess output streams to text for diagnostics."""

    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode(errors="replace")
    return stream


def _base_diagnostics(source_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Create the common diagnostic envelope for parent-authored failures."""

    return {
        "schema_version": "1.0",
        "units": "mm",
        "runtime": _runtime_metadata(),
        "source": str(source_path),
        "params": params,
        "published": [],
        "features": [],
        "part_meta": [],
        "warnings": [],
        "exports": [],
    }


def _write_worker_failure(
    run_dir: Path,
    source_path: Path,
    params: dict[str, Any],
    status: str,
    error_type: str,
    message: str,
    stdout: str = "",
    stderr: str = "",
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Write diagnostics when the parent cannot rely on worker output."""

    diagnostics = {
        **_base_diagnostics(source_path, params),
        "status": status,
        "errors": [{"type": error_type, "message": message, "traceback": ""}],
        "captured_stdout": stdout,
        "captured_stderr": stderr,
    }
    if timeout_seconds is not None:
        diagnostics["timeout_seconds"] = timeout_seconds
    write_json(run_dir / "diagnostics.json", diagnostics)
    return diagnostics


def _attach_captured_streams(run_dir: Path, stdout: str, stderr: str) -> dict[str, Any]:
    """Add worker stdout/stderr to diagnostics without changing semantics."""

    diagnostics_path = run_dir / "diagnostics.json"
    diagnostics = read_json(diagnostics_path)
    diagnostics["captured_stdout"] = stdout
    diagnostics["captured_stderr"] = stderr
    write_json(diagnostics_path, diagnostics)
    return diagnostics


def _payload_from_diagnostics(run_id: str, run_dir: Path, diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Build the compact CLI payload from diagnostic facts."""

    return {
        "run_id": run_id,
        "status": diagnostics["status"],
        "artifact_dir": str(run_dir),
        "published": [obj["label"] for obj in diagnostics.get("published", [])],
        "errors": diagnostics.get("errors", []),
    }


def run_design(source: Path, params_path: Path, artifact_root: Path, timeout_seconds: float = 30) -> dict[str, Any]:
    """Run a design source and create a numbered artifact directory."""

    source_path = source.resolve()
    params = load_yaml(params_path)
    run_id, run_dir = next_run_dir(artifact_root)
    run_dir.mkdir(parents=True, exist_ok=False)

    shutil.copy2(source_path, run_dir / "source_snapshot.py")
    write_yaml(run_dir / "params.resolved.yaml", params)

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "cadx.worker", str(source_path), str(run_dir)],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        diagnostics = _write_worker_failure(
            run_dir,
            source_path,
            params,
            "timeout",
            "TimeoutExpired",
            f"design execution exceeded {timeout_seconds} seconds",
            _stream_text(exc.output),
            _stream_text(exc.stderr),
            timeout_seconds,
        )
        return _payload_from_diagnostics(run_id, run_dir, diagnostics)

    stdout = _stream_text(completed.stdout)
    stderr = _stream_text(completed.stderr)
    if not (run_dir / "diagnostics.json").exists():
        diagnostics = _write_worker_failure(
            run_dir,
            source_path,
            params,
            "error",
            "WorkerFailed",
            f"worker exited with {completed.returncode} before writing diagnostics",
            stdout,
            stderr,
        )
        return _payload_from_diagnostics(run_id, run_dir, diagnostics)

    diagnostics = _attach_captured_streams(run_dir, stdout, stderr)
    return _payload_from_diagnostics(run_id, run_dir, diagnostics)
