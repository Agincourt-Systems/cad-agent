"""Spatial inspection for run artifacts.

The first inspector consumes the normalized publications captured during
``cadx run``. Future versions can augment this with automatic build123d
topology detection, but explicit publications provide the stable feature IDs an
agent needs immediately.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cadx.files import read_json, write_json


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


def inspect_run(run_dir: Path) -> dict[str, Any]:
    """Write ``spatial.json`` for a run and return a compact summary."""

    diagnostics = read_json(run_dir / "diagnostics.json")
    objects = [_with_bbox_size(dict(obj)) for obj in diagnostics.get("published", [])]
    features = list(diagnostics.get("features", [])) + _auto_detect_features(diagnostics, run_dir)
    spatial = {
        "schema_version": "1.0",
        "units": diagnostics.get("units", "mm"),
        "objects": objects,
        "features": features,
    }
    write_json(run_dir / "spatial.json", spatial)
    return {
        "status": "ok",
        "spatial_path": str(run_dir / "spatial.json"),
        "objects": len(objects),
        "features": len(features),
    }
