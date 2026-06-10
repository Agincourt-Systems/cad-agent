"""Execution pipeline for CAD design source files.

The runner treats design files as executable Python and records enough context
for an agent to reproduce or diagnose every run: source snapshot, resolved
parameters, publications, feature metadata, runtime errors, and optional CAD
exports.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any

from cadx.files import load_yaml, next_run_dir, write_json, write_yaml
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


def _normalize_published(entry: dict[str, Any]) -> dict[str, Any]:
    """Convert a registry publication to JSON-safe spatial facts."""

    label = entry["label"]
    obj = entry["object"]
    if isinstance(obj, dict):
        normalized = dict(obj)
    else:
        bbox_method = getattr(obj, "bounding_box", None)
        bbox = _normalize_bbox(bbox_method() if callable(bbox_method) else getattr(obj, "bbox", {}))
        normalized = {
            "bbox": bbox,
            "mass_properties": {
                "volume": getattr(obj, "volume", None),
                "area": getattr(obj, "area", None),
            },
            "topology": {
                "solids": _count_selector(obj, "solids"),
                "faces": _count_selector(obj, "faces"),
                "edges": _count_selector(obj, "edges"),
                "vertices": _count_selector(obj, "vertices"),
            },
        }

    normalized["id"] = f"obj.{label}"
    normalized["label"] = label
    normalized["role"] = entry.get("role", "part")
    if entry.get("metadata"):
        normalized["metadata"] = entry["metadata"]
    if "bbox" in normalized:
        normalized["bbox"] = _normalize_bbox(normalized["bbox"])
    return normalized


def _export_build123d_object(entry: dict[str, Any], run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Export a real build123d object when build123d is available.

    Synthetic dictionary objects used by tests are intentionally skipped. Export
    failures are warnings instead of hard run failures because structured
    inspection may still be useful to the agent.
    """

    obj = entry["object"]
    if isinstance(obj, dict):
        return [], []

    try:
        from build123d import export_gltf, export_step, export_stl
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
    for extension, exporter, kwargs in [
        ("step", export_step, {}),
        ("stl", export_stl, {}),
        ("glb", export_gltf, {"binary": True}),
    ]:
        target = run_dir / f"{label}.{extension}"
        try:
            exporter(obj, target, **kwargs)
            exports.append({"label": label, "format": extension, "path": str(target)})
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


def run_design(source: Path, params_path: Path, artifact_root: Path) -> dict[str, Any]:
    """Run a design source and create a numbered artifact directory."""

    source_path = source.resolve()
    params = load_yaml(params_path)
    run_id, run_dir = next_run_dir(artifact_root)
    run_dir.mkdir(parents=True, exist_ok=False)

    shutil.copy2(source_path, run_dir / "source_snapshot.py")
    write_yaml(run_dir / "params.resolved.yaml", params)

    try:
        raw_registry = _execute_design(source_path, params)
        published = [_normalize_published(entry) for entry in raw_registry["published"]]
        exports: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        for entry in raw_registry["published"]:
            entry_exports, entry_warnings = _export_build123d_object(entry, run_dir)
            exports.extend(entry_exports)
            warnings.extend(entry_warnings)
        diagnostics = {
            "schema_version": "1.0",
            "status": "ok",
            "units": "mm",
            "source": str(source_path),
            "params": params,
            "published": published,
            "features": raw_registry["features"],
            "errors": [],
            "warnings": warnings,
            "exports": exports,
        }
        write_json(run_dir / "diagnostics.json", diagnostics)
        return {
            "run_id": run_id,
            "status": "ok",
            "artifact_dir": str(run_dir),
            "published": [obj["label"] for obj in published],
            "errors": [],
        }
    except Exception as exc:  # pragma: no cover - covered by integration use, not MVP success path.
        diagnostics = {
            "schema_version": "1.0",
            "status": "error",
            "units": "mm",
            "source": str(source_path),
            "params": params,
            "published": [],
            "features": [],
            "errors": [
                {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                }
            ],
            "warnings": [],
            "exports": [],
        }
        write_json(run_dir / "diagnostics.json", diagnostics)
        return {
            "run_id": run_id,
            "status": "error",
            "artifact_dir": str(run_dir),
            "published": [],
            "errors": diagnostics["errors"],
        }
