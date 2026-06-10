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


def inspect_run(run_dir: Path) -> dict[str, Any]:
    """Write ``spatial.json`` for a run and return a compact summary."""

    diagnostics = read_json(run_dir / "diagnostics.json")
    objects = [_with_bbox_size(dict(obj)) for obj in diagnostics.get("published", [])]
    features = list(diagnostics.get("features", []))
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
