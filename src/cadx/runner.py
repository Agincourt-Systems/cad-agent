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
