"""Requirement evaluation for inspected CAD runs.

The evaluator intentionally works from ``spatial.json`` instead of CAD objects.
This gives the coding agent deterministic observations even when it cannot
load or visually inspect the underlying geometry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from cadx.files import read_json, write_json


AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def _object_index(spatial: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index objects by labels used in requirement target paths."""

    return {obj["label"]: obj for obj in spatial.get("objects", [])}


def _within_tolerance(observed: float, expected: float, tolerance: float) -> bool:
    """Compare numeric values using an absolute tolerance in model units."""

    return abs(observed - expected) <= tolerance


def _resolve_dimension(spatial: dict[str, Any], target: str) -> Any:
    """Resolve target paths such as ``obj.plate.bbox.size.x``.

    The path grammar is deliberately small so agents can predict it. Object
    labels are addressed through ``obj.<label>`` and vector components may end
    in ``x``, ``y``, or ``z``.
    """

    parts = target.split(".")
    if len(parts) < 3 or parts[0] != "obj":
        raise ValueError(f"unsupported dimension target {target!r}")

    objects = _object_index(spatial)
    current: Any = objects[parts[1]]
    for part in parts[2:]:
        if isinstance(current, list) and part in AXIS_INDEX:
            current = current[AXIS_INDEX[part]]
        else:
            current = current[part]
    return current


def _check_dimension(spatial: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a scalar dimension check."""

    observed = _resolve_dimension(spatial, check["target"])
    expected = check["equals"]
    tolerance = check.get("tolerance", 0)
    passed = _within_tolerance(float(observed), float(expected), float(tolerance))
    return {
        "id": check["id"],
        "type": "dimension",
        "status": "pass" if passed else "fail",
        "observed": observed,
        "expected": expected,
        "tolerance": tolerance,
    }


def _check_feature_count(spatial: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Count features by kind, such as cylindrical holes."""

    observed = sum(1 for feature in spatial.get("features", []) if feature.get("kind") == check["kind"])
    expected = check["equals"]
    return {
        "id": check["id"],
        "type": "feature_count",
        "status": "pass" if observed == expected else "fail",
        "observed": observed,
        "expected": expected,
    }


def _check_feature_dimension(spatial: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a property across all features selected by kind."""

    selector = check.get("selector", {})
    selected = [
        feature
        for feature in spatial.get("features", [])
        if selector.get("kind") is None or feature.get("kind") == selector["kind"]
    ]
    property_name = check["property"]
    observed = [feature.get(property_name) for feature in selected]
    expected = check["equals"]
    tolerance = check.get("tolerance", 0)
    passed = bool(observed) and all(
        _within_tolerance(float(value), float(expected), float(tolerance)) for value in observed
    )
    return {
        "id": check["id"],
        "type": "feature_dimension",
        "status": "pass" if passed else "fail",
        "observed": observed,
        "expected": expected,
        "tolerance": tolerance,
    }


def _evaluate_check(spatial: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Route a requirement entry to its evaluator."""

    check_type = check["type"]
    if check_type == "dimension":
        return _check_dimension(spatial, check)
    if check_type == "feature_count":
        return _check_feature_count(spatial, check)
    if check_type == "feature_dimension":
        return _check_feature_dimension(spatial, check)
    raise ValueError(f"unsupported check type {check_type!r}")


def evaluate_run(run_dir: Path, requirements_path: Path) -> dict[str, Any]:
    """Evaluate ``requirements.yaml`` against a run's spatial facts."""

    spatial = read_json(run_dir / "spatial.json")
    requirements = yaml.safe_load(requirements_path.read_text(encoding="utf-8")) or {}
    results = [_evaluate_check(spatial, check) for check in requirements.get("checks", [])]
    failed = [result["id"] for result in results if result["status"] != "pass"]

    payload = {
        "schema_version": "1.0",
        "status": "pass" if not failed else "fail",
        "passed": len(results) - len(failed),
        "total": len(results),
        "failed": failed,
        "checks": results,
    }
    write_json(run_dir / "checks.json", payload)
    return {
        "status": payload["status"],
        "passed": payload["passed"],
        "total": payload["total"],
        "failed": failed,
        "checks_path": str(run_dir / "checks.json"),
    }
