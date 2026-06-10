"""Requirement evaluation for inspected CAD runs.

The evaluator intentionally works from ``spatial.json`` instead of CAD objects.
This gives the coding agent deterministic observations even when it cannot
load or visually inspect the underlying geometry.
"""

from __future__ import annotations

from pathlib import Path
from math import sqrt
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


def _numeric_expectation(check: dict[str, Any]) -> Any:
    """Return a compact expected value for check output.

    Existing exact checks keep the scalar `equals` value for backward
    compatibility. Range checks return only the supplied range boundaries.
    """

    if "equals" in check:
        return check["equals"]
    expected: dict[str, Any] = {}
    if "min" in check:
        expected["min"] = check["min"]
    if "max" in check:
        expected["max"] = check["max"]
    return expected


def _numeric_status(observed: float, check: dict[str, Any]) -> bool:
    """Evaluate equals/min/max numeric clauses with optional tolerance."""

    tolerance = float(check.get("tolerance", 0))
    if "equals" in check and not _within_tolerance(float(observed), float(check["equals"]), tolerance):
        return False
    if "min" in check and float(observed) < float(check["min"]) - tolerance:
        return False
    if "max" in check and float(observed) > float(check["max"]) + tolerance:
        return False
    return True


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
    """Evaluate a scalar dimension exact or range check."""

    observed = _resolve_dimension(spatial, check["target"])
    expected = _numeric_expectation(check)
    tolerance = check.get("tolerance", 0)
    passed = _numeric_status(float(observed), check)
    return {
        "id": check["id"],
        "type": "dimension",
        "status": "pass" if passed else "fail",
        "observed": observed,
        "expected": expected,
        "tolerance": tolerance,
    }


def _check_topology(spatial: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Evaluate topology counts through the same target-path grammar."""

    observed = _resolve_dimension(spatial, check["target"])
    expected = _numeric_expectation(check)
    tolerance = check.get("tolerance", 0)
    passed = _numeric_status(float(observed), check)
    return {
        "id": check["id"],
        "type": "topology",
        "status": "pass" if passed else "fail",
        "observed": observed,
        "expected": expected,
        "tolerance": tolerance,
    }


def _object_by_ref(spatial: dict[str, Any], ref: str) -> dict[str, Any]:
    """Resolve object references like `obj.left` from a check."""

    if not ref.startswith("obj."):
        raise ValueError(f"unsupported object reference {ref!r}")
    label = ref.split(".", 1)[1]
    return _object_index(spatial)[label]


def _aabb_clearance(left: dict[str, Any], right: dict[str, Any]) -> float:
    """Compute axis-aligned bounding-box clearance between two objects.

    The result is exact for separated boxes and zero for overlapping boxes.
    It is an approximation of real shape clearance, but it is deterministic
    from `spatial.json` and cheap enough for the default evaluator path.
    """

    left_min = left["bbox"]["min"]
    left_max = left["bbox"]["max"]
    right_min = right["bbox"]["min"]
    right_max = right["bbox"]["max"]
    gaps = [
        max(right_min[axis] - left_max[axis], left_min[axis] - right_max[axis], 0)
        for axis in range(3)
    ]
    return sqrt(sum(gap * gap for gap in gaps))


def _check_clearance(spatial: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Evaluate minimum AABB clearance between two spatial objects."""

    left_ref, right_ref = check["between"]
    observed = _aabb_clearance(_object_by_ref(spatial, left_ref), _object_by_ref(spatial, right_ref))
    expected = _numeric_expectation(check)
    tolerance = check.get("tolerance", 0)
    passed = _numeric_status(observed, check)
    return {
        "id": check["id"],
        "type": "clearance",
        "status": "pass" if passed else "fail",
        "observed": observed,
        "expected": expected,
        "tolerance": tolerance,
        "between": check["between"],
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
    if check_type == "topology":
        return _check_topology(spatial, check)
    if check_type == "clearance":
        return _check_clearance(spatial, check)
    if check_type == "feature_count":
        return _check_feature_count(spatial, check)
    if check_type == "feature_dimension":
        return _check_feature_dimension(spatial, check)
    raise ValueError(f"unsupported check type {check_type!r}")


def _run_relative(path: Path, run_dir: Path) -> str:
    """Return a stable path label for reports.

    The report is stored inside the run directory, so relative artifact paths
    are easier for agents and humans to scan than absolute temporary paths.
    """

    try:
        return path.relative_to(run_dir).as_posix()
    except ValueError:
        return str(path)


def _existing_artifacts(run_dir: Path) -> list[tuple[str, Path]]:
    """List relevant artifacts that already exist for this run.

    Rendering is a separate command. The evaluator records visual artifacts only
    when they exist so `evaluate` remains useful before and after `render`.
    """

    candidates = [
        ("diagnostics", run_dir / "diagnostics.json"),
        ("spatial", run_dir / "spatial.json"),
        ("checks", run_dir / "checks.json"),
        ("contact_sheet", run_dir / "views" / "contact.png"),
        ("render_manifest", run_dir / "views" / "render_manifest.json"),
    ]
    return [(label, path) for label, path in candidates if path.exists()]


def _write_report(run_dir: Path, payload: dict[str, Any]) -> Path:
    """Write the concise Markdown report agents use for convergence context."""

    status = payload["status"].upper()
    lines = [
        "# CAD Agent Run Report",
        "",
        f"Status: {status}",
        f"Checks: {payload['passed']}/{payload['total']} passed",
        "",
    ]

    failed_checks = [check for check in payload["checks"] if check["status"] != "pass"]
    if failed_checks:
        lines.extend(["## Failed Checks", ""])
        for check in failed_checks:
            lines.extend(
                [
                    f"- {check['id']} ({check['type']})",
                    f"  observed: {check.get('observed')}",
                    f"  expected: {check.get('expected')}",
                ]
            )
            if "tolerance" in check:
                lines.append(f"  tolerance: {check['tolerance']}")
        lines.append("")
    else:
        lines.extend(["## Failed Checks", "", "None.", ""])

    lines.extend(["## Artifacts", ""])
    for label, path in _existing_artifacts(run_dir):
        lines.append(f"- {label}: {_run_relative(path, run_dir)}")
    lines.append("")

    report_path = run_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


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
    report_path = _write_report(run_dir, payload)
    return {
        "status": payload["status"],
        "passed": payload["passed"],
        "total": payload["total"],
        "failed": failed,
        "checks_path": str(run_dir / "checks.json"),
        "report_path": str(report_path),
    }
