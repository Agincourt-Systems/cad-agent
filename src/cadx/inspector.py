"""Spatial inspection for run artifacts.

The first inspector consumes the normalized publications captured during
``cadx run``. Future versions can augment this with automatic build123d
topology detection, but explicit publications provide the stable feature IDs an
agent needs immediately.
"""

from __future__ import annotations

from math import sqrt
from pathlib import Path
from typing import Any

from cadx.files import read_json, write_json


# Maximum size and position mismatch, in model units (mm), for an explicit
# publication and a detected feature to be treated as the same physical
# feature. Tight enough that two real features are never merged, loose enough
# to absorb STEP round-trip noise. Publications further off than this keep
# double-counting on purpose: that discrepancy is signal for the agent.
_DEDUP_TOLERANCE = 0.05

# Size properties compared during deduplication when both sides publish them.
_DEDUP_SIZE_PROPERTIES = ("diameter", "width", "length")

# Kinds whose published "center" may legitimately sit anywhere along the
# feature axis, so matching measures radial distance to the detected axis
# line instead of point-to-point distance.
_AXIAL_KINDS = {"cylindrical_hole", "cylindrical_boss"}

# Roles that are reference/keep-out geometry rather than physical parts, so they
# do not contribute to the assembly's aggregate mass. Every other role
# (including the idiomatic "part" and "final") is a physical part.
_NON_PHYSICAL_ROLES = {"fixture", "reference", "datum", "keepout"}


def _with_bbox_size(obj: dict[str, Any]) -> dict[str, Any]:
    """Ensure every object bbox includes a ``size`` vector."""

    bbox = obj.setdefault("bbox", {})
    if "size" not in bbox and "min" in bbox and "max" in bbox:
        bbox["size"] = [max_v - min_v for min_v, max_v in zip(bbox["min"], bbox["max"])]
    return obj


def _vector_from(value: Any) -> list[float]:
    """Convert build123d vector-like values to JSON-safe coordinates."""

    if isinstance(value, (list, tuple)):
        return [float(value[0]), float(value[1]), float(value[2])]
    return [float(value.X), float(value.Y), float(value.Z)]


def _bbox_dict(raw: Any) -> dict[str, list[float]]:
    """Normalize a build123d bounding box object."""

    min_value = raw.min() if callable(getattr(raw, "min", None)) else raw.min
    max_value = raw.max() if callable(getattr(raw, "max", None)) else raw.max
    minimum = _vector_from(min_value)
    maximum = _vector_from(max_value)
    return {
        "min": minimum,
        "max": maximum,
        "size": [max_v - min_v for min_v, max_v in zip(minimum, maximum)],
    }


def _resolve_export_path(run_dir: Path, export_path: str) -> Path:
    """Resolve export paths saved by earlier run versions."""

    path = Path(export_path)
    if path.exists():
        return path
    if path.is_absolute():
        return path
    return run_dir / path.name


def _step_exports(diagnostics: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    """Return STEP export records with paths usable from the current process."""

    return [
        {**export, "path": str(_resolve_export_path(run_dir, export["path"]))}
        for export in diagnostics.get("exports", [])
        if export.get("format") == "step"
    ]


def _dominant_axis(direction: list[float]) -> int:
    """Return the index of the axis most aligned with a direction vector."""

    return max(range(3), key=lambda index: abs(direction[index]))


def _center_from_bbox(bbox: dict[str, list[float]]) -> list[float]:
    """Return a bounding-box center point."""

    return [
        (min_value + max_value) / 2
        for min_value, max_value in zip(bbox["min"], bbox["max"])
    ]


def _is_close(left: float, right: float, tolerance: float = 1e-5) -> bool:
    """Small absolute comparison helper for topology-derived values."""

    return abs(left - right) <= tolerance


def _is_full_cylindrical_face(face_bbox: dict[str, list[float]], radius: float, axis_index: int) -> bool:
    """Identify full cylindrical faces versus partial slot-end cylinders."""

    diameter = radius * 2
    perpendicular_axes = [axis for axis in range(3) if axis != axis_index]
    return all(_is_close(face_bbox["size"][axis], diameter, 1e-4) for axis in perpendicular_axes)


def _plane_feature(face: Any, label: str, index: int) -> dict[str, Any]:
    """Create a planar datum feature from a planar face."""

    bbox = _bbox_dict(face.bounding_box())
    try:
        normal = _vector_from(face.normal_at())
    except Exception:
        normal = [0.0, 0.0, 0.0]
    return {
        "id": f"feat.auto_{label}_planar_datum_{index}",
        "kind": "planar_datum",
        "source_object": f"obj.{label}",
        "center": _center_from_bbox(bbox),
        "normal": normal,
        "area": float(face.area),
        "bbox": bbox,
        "detected": True,
    }


def _slot_features(partial_cylinders: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    """Detect simple obround slots from paired partial cylindrical end faces."""

    slots: list[dict[str, Any]] = []
    used: set[int] = set()
    for left_index, left in enumerate(partial_cylinders):
        if left_index in used:
            continue
        for right_index, right in enumerate(partial_cylinders[left_index + 1 :], start=left_index + 1):
            if right_index in used:
                continue
            if not _is_close(left["radius"], right["radius"], 1e-4):
                continue
            if left["axis_index"] != right["axis_index"] or not left["through"] or not right["through"]:
                continue

            delta = [right["center"][axis] - left["center"][axis] for axis in range(3)]
            slot_axis = _dominant_axis(delta)
            stable_axes = [axis for axis in range(3) if axis not in {left["axis_index"], slot_axis}]
            if any(not _is_close(left["center"][axis], right["center"][axis], 1e-4) for axis in stable_axes):
                continue

            length = abs(delta[slot_axis])
            slots.append(
                {
                    "kind": "slot",
                    "source_object": f"obj.{label}",
                    "center": [
                        (left["center"][axis] + right["center"][axis]) / 2
                        for axis in range(3)
                    ],
                    "axis": [
                        1.0 if axis == slot_axis and delta[axis] >= 0 else -1.0 if axis == slot_axis else 0.0
                        for axis in range(3)
                    ],
                    "width": left["radius"] * 2,
                    "length": length,
                    "through": True,
                    "detected": True,
                }
            )
            used.update({left_index, right_index})
            break

    for index, feature in enumerate(slots, start=1):
        feature["id"] = f"feat.auto_{label}_slot_{index}"
    return slots


def _detected_topology_features(shape: Any, label: str) -> list[dict[str, Any]]:
    """Detect planar datums, cylindrical holes, bosses, and simple slots."""

    object_bbox = _bbox_dict(shape.bounding_box())
    detected: list[dict[str, Any]] = []
    partial_cylinders: list[dict[str, Any]] = []
    plane_index = 1

    for face in shape.faces():
        geom_type = str(getattr(face, "geom_type", ""))
        if geom_type.endswith("PLANE"):
            detected.append(_plane_feature(face, label, plane_index))
            plane_index += 1
            continue
        if not geom_type.endswith("CYLINDER"):
            continue

        face_bbox = _bbox_dict(face.bounding_box())
        axis = _vector_from(face.axis_of_rotation.direction)
        axis_index = _dominant_axis(axis)
        through = face_bbox["size"][axis_index] >= object_bbox["size"][axis_index] - 1e-5
        radius = float(face.radius)
        full_cylinder = _is_full_cylindrical_face(face_bbox, radius, axis_index)
        center = _center_from_bbox(face_bbox) if full_cylinder else _vector_from(face.center())
        if full_cylinder:
            kind = "cylindrical_hole" if through else "cylindrical_boss"
            feature = {
                "kind": kind,
                "source_object": f"obj.{label}",
                "center": center,
                "axis": axis,
                "diameter": radius * 2,
                "through": through,
                "detected": True,
            }
            if kind == "cylindrical_boss":
                feature["height"] = face_bbox["size"][axis_index]
            detected.append(feature)
        else:
            partial_cylinders.append(
                {
                    "center": center,
                    "axis": axis,
                    "axis_index": axis_index,
                    "radius": radius,
                    "through": face_bbox["size"][axis_index] > 1e-5,
                }
            )

    detected.extend(_slot_features(partial_cylinders, label))
    id_counters: dict[str, int] = {}
    for feature in detected:
        id_counters[feature["kind"]] = id_counters.get(feature["kind"], 0) + 1
        feature.setdefault("id", f"feat.auto_{label}_{feature['kind']}_{id_counters[feature['kind']]}")
    return detected


def _auto_detect_features(diagnostics: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    """Load STEP exports and return automatically detected features."""

    try:
        from build123d import import_step
    except Exception:
        return []

    detected: list[dict[str, Any]] = []
    for export in _step_exports(diagnostics, run_dir):
        shape = import_step(export["path"])
        detected.extend(_detected_topology_features(shape, export.get("label", "object")))
    return detected


def _coerce_point(value: Any) -> list[float] | None:
    """Return a 3-component float point, or ``None`` when malformed.

    Explicit ``publish_feature`` properties are arbitrary user data, so the
    matcher validates instead of assuming the spatial schema.
    """

    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return [float(component) for component in value]
    except (TypeError, ValueError):
        return None


def _sizes_match(explicit: dict[str, Any], detected: dict[str, Any]) -> bool:
    """Compare size properties present on both features within tolerance."""

    for name in _DEDUP_SIZE_PROPERTIES:
        if name in explicit and name in detected:
            try:
                mismatch = abs(float(explicit[name]) - float(detected[name]))
            except (TypeError, ValueError):
                return False
            if mismatch > _DEDUP_TOLERANCE:
                return False
    return True


def _center_distance(explicit_center: list[float], detected: dict[str, Any]) -> float:
    """Distance from an explicit center to the detected feature's location.

    Cylindrical features are identified by their axis line: the component of
    the offset along the detected axis is discarded because publications may
    place the "center" at either face or at mid-depth. Other kinds compare
    plain point-to-point distance.
    """

    detected_center = detected["center"]
    delta = [explicit_c - detected_c for explicit_c, detected_c in zip(explicit_center, detected_center)]
    axis = _coerce_point(detected.get("axis")) if detected.get("kind") in _AXIAL_KINDS else None
    if axis is not None:
        length = sqrt(sum(component * component for component in axis))
        if length > 0:
            unit = [component / length for component in axis]
            axial = sum(d * a for d, a in zip(delta, unit))
            radial_squared = sum(d * d for d in delta) - axial * axial
            return sqrt(max(radial_squared, 0.0))
    return sqrt(sum(d * d for d in delta))


def _is_duplicate(explicit: dict[str, Any], detected: dict[str, Any]) -> bool:
    """Decide whether a detected feature re-observes an explicit publication."""

    if explicit.get("kind") != detected.get("kind"):
        return False
    # Features that name different source objects are physically distinct parts'
    # features and must never be merged, even when coaxial. Two plates stacked
    # for bolting (ADR 0014) have aligned holes whose axes coincide, so the
    # axis-line matching below would otherwise collapse one part's holes into the
    # other's. An explicit publication without a source_object is unconstrained
    # here so ADR 0012's explicit/detected corroboration still applies.
    explicit_source = explicit.get("source_object")
    detected_source = detected.get("source_object")
    if explicit_source is not None and detected_source is not None and explicit_source != detected_source:
        return False
    explicit_center = _coerce_point(explicit.get("center"))
    detected_center = _coerce_point(detected.get("center"))
    if explicit_center is None or detected_center is None:
        return False
    if not _sizes_match(explicit, detected):
        return False
    return _center_distance(explicit_center, detected) <= _DEDUP_TOLERANCE


def _merge_features(
    explicit_features: list[dict[str, Any]],
    detected_features: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combine both feature channels without double-counting geometry.

    Explicit publications are the source of truth and always survive with
    their stable ids. A detected feature that matches one of them is dropped
    and the publication is marked ``confirmed_by_detection`` so agents can
    distinguish corroborated publications from unverified intent. Unmatched
    features from either channel pass through unchanged.
    """

    merged = [dict(feature) for feature in explicit_features]
    for detected in detected_features:
        match = next((feature for feature in merged if _is_duplicate(feature, detected)), None)
        if match is None:
            merged.append(detected)
        else:
            match["confirmed_by_detection"] = True
    return merged


def _assembly_center_of_mass(objects: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Mass-weighted center of mass across published part-role objects.

    Each contributing part needs a positive ``mass_properties.volume`` and a
    3-vector ``mass_properties.center_of_mass``. A part's weight is
    ``density * volume`` when a positive ``metadata.density`` is present, else its
    volume (a uniform-density approximation). Weighting is reported as ``"mass"``
    only when *every* contributing part supplied a positive density, otherwise
    ``"volume"`` — so a uniform-density guess is never silently presented as a
    true mass center. ADR 0017's part metadata supplies real densities later;
    until then this degrades gracefully. Returns ``None`` when no part qualifies.
    """

    # First pass: collect qualifying parts with their volume, centroid, and any
    # positive density.
    qualifying: list[tuple[float, list[float], float | None]] = []
    for obj in objects:
        # Aggregate every physical part. The primary part is idiomatically
        # published as role="final" (the starter design and most assemblies do),
        # so excluding all non-"part" roles would silently drop the heaviest part
        # from the assembly center of mass and skew a stability/load-cell check.
        if obj.get("role", "part") in _NON_PHYSICAL_ROLES:
            continue
        mass_properties = obj.get("mass_properties", {})
        volume = mass_properties.get("volume")
        center = _coerce_point(mass_properties.get("center_of_mass"))
        if not isinstance(volume, (int, float)) or volume <= 0 or center is None:
            continue
        density = obj.get("metadata", {}).get("density")
        density = float(density) if isinstance(density, (int, float)) and density > 0 else None
        qualifying.append((float(volume), center, density))

    if not qualifying:
        return None

    # Mass-weight only when EVERY part supplied a positive density. In any mixed
    # or absent-density case, weight purely by volume so the result is a
    # consistent uniform-density centroid rather than a unit-inconsistent hybrid
    # of mass weights and volume weights.
    all_have_density = all(density is not None for _, _, density in qualifying)
    contributions = [
        (volume * density if all_have_density else volume, center)
        for volume, center, density in qualifying
    ]
    total = sum(weight for weight, _ in contributions)
    if total <= 0:
        return None
    center_of_mass = [
        sum(weight * center[axis] for weight, center in contributions) / total
        for axis in range(3)
    ]
    return {
        "center_of_mass": center_of_mass,
        "mass": total,
        "weighting": "mass" if all_have_density else "volume",
        "part_count": len(contributions),
    }


def inspect_run(run_dir: Path) -> dict[str, Any]:
    """Write ``spatial.json`` for a run and return a compact summary."""

    diagnostics = read_json(run_dir / "diagnostics.json")
    objects = [_with_bbox_size(dict(obj)) for obj in diagnostics.get("published", [])]
    features = _merge_features(
        list(diagnostics.get("features", [])),
        _auto_detect_features(diagnostics, run_dir),
    )
    spatial = {
        "schema_version": "1.0",
        "units": diagnostics.get("units", "mm"),
        "objects": objects,
        "features": features,
    }
    assembly = _assembly_center_of_mass(objects)
    if assembly is not None:
        spatial["assembly"] = assembly
    write_json(run_dir / "spatial.json", spatial)
    return {
        "status": "ok",
        "spatial_path": str(run_dir / "spatial.json"),
        "objects": len(objects),
        "features": len(features),
    }
