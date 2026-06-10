"""Run-to-run comparison helpers.

Comparison gives agents a low-noise way to understand whether a code edit
changed the intended geometry. The first implementation focuses on stable
object metrics that are available from ``spatial.json``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cadx.files import read_json


def _object_index(spatial: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return objects keyed by their stable object id."""

    return {obj["id"]: obj for obj in spatial.get("objects", [])}


def _vector_delta(left: list[float], right: list[float]) -> list[float]:
    """Subtract two equal-length numeric vectors without adding numpy."""

    return [right_value - left_value for left_value, right_value in zip(left, right)]


def compare_runs(left_run_dir: Path, right_run_dir: Path) -> dict[str, Any]:
    """Compare two run directories that already contain ``spatial.json``."""

    left = read_json(left_run_dir / "spatial.json")
    right = read_json(right_run_dir / "spatial.json")
    left_objects = _object_index(left)
    right_objects = _object_index(right)

    object_changes: dict[str, dict[str, Any]] = {}
    for object_id in sorted(left_objects.keys() & right_objects.keys()):
        left_size = left_objects[object_id]["bbox"]["size"]
        right_size = right_objects[object_id]["bbox"]["size"]
        object_changes[object_id] = {"bbox.size": _vector_delta(left_size, right_size)}

    return {
        "status": "ok",
        "left": str(left_run_dir),
        "right": str(right_run_dir),
        "changes": {"objects": object_changes},
    }
