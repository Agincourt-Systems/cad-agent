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


def _detected_cylindrical_features(shape: Any, label: str) -> list[dict[str, Any]]:
    """Detect cylindrical through holes from a build123d shape.

    This intentionally starts with a conservative signal: cylindrical faces.
    It classifies a cylinder as through when its face bounding box spans the
    parent object bounding box along the cylinder axis.
    """

    object_bbox = _bbox_dict(shape.bounding_box())
    detected: list[dict[str, Any]] = []
    for face in shape.faces():
        if not str(getattr(face, "geom_type", "")).endswith("CYLINDER"):
            continue

        face_bbox = _bbox_dict(face.bounding_box())
        center = [
            (min_value + max_value) / 2
            for min_value, max_value in zip(face_bbox["min"], face_bbox["max"])
        ]
        axis = _vector_from(face.axis_of_rotation.direction)
        axis_index = _dominant_axis(axis)
        through = face_bbox["size"][axis_index] >= object_bbox["size"][axis_index] - 1e-5
        detected.append(
            {
                "kind": "cylindrical_hole",
                "source_object": f"obj.{label}",
                "center": center,
                "axis": axis,
                "diameter": float(face.radius) * 2,
                "through": through,
                "detected": True,
            }
        )

    detected.sort(key=lambda feature: (feature["center"], feature["diameter"]))
    for index, feature in enumerate(detected, start=1):
        feature["id"] = f"feat.auto_{label}_cylindrical_hole_{index}"
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
        detected.extend(_detected_cylindrical_features(shape, export.get("label", "object")))
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
