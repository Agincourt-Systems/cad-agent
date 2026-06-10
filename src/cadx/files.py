"""Filesystem utilities for the harness.

This module centralizes file writes so command implementations stay focused on
CAD semantics. All writes are explicit and confined to the project directory or
the run directory selected by the user.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


STARTER_DESIGN = """\
from build123d import *
from cadx import publish, publish_feature


def build(params):
    width = params.get("width_mm", 80)
    height = params.get("height_mm", 20)
    thickness = params.get("thickness_mm", 4)

    with BuildPart() as plate:
        with BuildSketch():
            Rectangle(width, height)
            with GridLocations(width - 20, 0, 2, 1):
                Circle(3, mode=Mode.SUBTRACT)
        extrude(amount=thickness)

    publish("plate", plate.part, role="final")
    publish_feature("mount_hole_left", kind="cylindrical_hole", diameter=6, center=[-30, 0, thickness / 2])
    publish_feature("mount_hole_right", kind="cylindrical_hole", diameter=6, center=[30, 0, thickness / 2])
    return plate.part
"""

STARTER_PARAMS = """\
width_mm: 80
height_mm: 20
thickness_mm: 4
"""

STARTER_REQUIREMENTS = """\
units: mm
checks:
  - id: width
    type: dimension
    target: obj.plate.bbox.size.x
    equals: 80
    tolerance: 0.2
  - id: mount_holes
    type: feature_count
    kind: cylindrical_hole
    equals: 2
  - id: mount_hole_diameter
    type: feature_dimension
    selector:
      kind: cylindrical_hole
    property: diameter
    equals: 6
    tolerance: 0.1
"""


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write deterministic, pretty JSON for agent and human readers."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from disk."""

    return json.loads(path.read_text(encoding="utf-8"))


def load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML and normalize empty files to an empty mapping."""

    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    """Write resolved YAML in a stable key order."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")


def next_run_dir(artifact_root: Path) -> tuple[str, Path]:
    """Return the next zero-padded run id and directory path."""

    artifact_root.mkdir(parents=True, exist_ok=True)
    existing = [
        int(path.name)
        for path in artifact_root.iterdir()
        if path.is_dir() and path.name.isdigit()
    ]
    run_id = f"{(max(existing) + 1) if existing else 1:04d}"
    return run_id, artifact_root / run_id


def _write_if_missing(path: Path, content: str) -> bool:
    """Create a starter file without overwriting agent/user work."""

    if path.exists():
        return False
    path.write_text(content, encoding="utf-8")
    return True


def init_project(project_dir: Path) -> dict[str, Any]:
    """Create starter files for an agent-editable CAD project."""

    created = [
        name
        for name, content in {
            "design.py": STARTER_DESIGN,
            "params.yaml": STARTER_PARAMS,
            "requirements.yaml": STARTER_REQUIREMENTS,
        }.items()
        if _write_if_missing(project_dir / name, content)
    ]
    return {"status": "ok", "created": created}
