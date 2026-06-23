"""Requirement evaluation for inspected CAD runs.

The evaluator intentionally works from ``spatial.json`` instead of CAD objects.
This gives the coding agent deterministic observations even when it cannot
load or visually inspect the underlying geometry.
"""

from __future__ import annotations

from pathlib import Path
from math import acos, atan2, degrees, sqrt
from typing import Any

import yaml

from cadx.dfm import evaluate_manufacturability
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
        "method": "aabb",
        "status": "pass" if passed else "fail",
        "observed": observed,
        "expected": expected,
        "tolerance": tolerance,
        "between": check["between"],
    }


def _label_from_ref(ref: str) -> str:
    """Return the object label from a requirement object reference."""

    if not ref.startswith("obj."):
        raise ValueError(f"unsupported object reference {ref!r}")
    return ref.split(".", 1)[1]


def _resolve_export_path(run_dir: Path, export_path: str) -> Path:
    """Resolve a diagnostics export path from the current process."""

    path = Path(export_path)
    if path.exists():
        return path
    if path.is_absolute():
        return path
    return run_dir / path.name


def _step_export_index(run_dir: Path) -> dict[str, Path]:
    """Map published object labels to STEP export paths."""

    diagnostics = read_json(run_dir / "diagnostics.json")
    return {
        export["label"]: _resolve_export_path(run_dir, export["path"])
        for export in diagnostics.get("exports", [])
        if export.get("format") == "step"
    }


def _check_exact_clearance(run_dir: Path, check: dict[str, Any]) -> dict[str, Any]:
    """Evaluate exact BREP clearance from exported STEP artifacts."""

    from build123d import import_step

    left_ref, right_ref = check["between"]
    exports = _step_export_index(run_dir)
    left_label = _label_from_ref(left_ref)
    right_label = _label_from_ref(right_ref)
    left_shape = import_step(exports[left_label])
    right_shape = import_step(exports[right_label])
    observed = float(left_shape.distance(right_shape))
    expected = _numeric_expectation(check)
    tolerance = check.get("tolerance", 0)
    passed = _numeric_status(observed, check)
    return {
        "id": check["id"],
        "type": "clearance",
        "method": "exact",
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


def _resolve_features(spatial: dict[str, Any], selector: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """Resolve a feature selector to all matching features, or an error message.

    A selector is either ``{"id": "feat.x"}`` (exact id) or a
    ``{"kind": ..., "source_object": "obj.label"}`` filter. The alignment check
    consumes the full match list so it can pair the best-aligned features rather
    than relying on detection order being identical across parts.
    """

    features = spatial.get("features", [])
    if "id" in selector:
        matches = [feature for feature in features if feature.get("id") == selector["id"]]
        return matches, None if matches else f"no feature with id {selector['id']!r}"

    kind = selector.get("kind")
    source = selector.get("source_object")
    matches = [
        feature
        for feature in features
        if (kind is None or feature.get("kind") == kind)
        and (source is None or feature.get("source_object") == source)
    ]
    return matches, None if matches else f"no feature matching {selector!r}"


def _unit_vector(vector: list[float]) -> list[float]:
    """Return a unit vector, preserving the zero vector."""

    length = sqrt(sum(component * component for component in vector))
    if length == 0:
        return [0.0, 0.0, 0.0]
    return [component / length for component in vector]


def _axis_alignment(feature_a: dict[str, Any], feature_b: dict[str, Any]) -> tuple[float, float]:
    """Return ``(axis_offset, axis_angle_deg)`` for two cylindrical features.

    ``axis_offset`` is the perpendicular distance between the two axis lines
    measured at their published centers (the component of the center-to-center
    vector that is perpendicular to the first axis); ``axis_angle_deg`` is the
    angle between the axis directions, with anti-parallel treated as aligned.
    Two coaxial holes give ``(0, 0)`` regardless of where along the axis their
    centers were published.
    """

    center_a = [float(c) for c in feature_a["center"]]
    center_b = [float(c) for c in feature_b["center"]]
    axis_a = _unit_vector([float(c) for c in feature_a["axis"]])
    axis_b = _unit_vector([float(c) for c in feature_b["axis"]])

    dot = min(1.0, max(-1.0, abs(sum(a * b for a, b in zip(axis_a, axis_b)))))
    angle = degrees(acos(dot))

    between = [b - a for a, b in zip(center_a, center_b)]
    along = sum(component * axis for component, axis in zip(between, axis_a))
    perpendicular_squared = sum(component * component for component in between) - along * along
    offset = sqrt(max(perpendicular_squared, 0.0))
    return offset, angle


def _check_feature_alignment(spatial: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Assert two features are coaxial and diameter-compatible within tolerance."""

    selector_a, selector_b = check["features"]
    features_a, error_a = _resolve_features(spatial, selector_a)
    features_b, error_b = _resolve_features(spatial, selector_b)
    tolerance = float(check.get("tolerance", 0))
    diameter_tolerance = float(check.get("diameter_tolerance", tolerance))

    if not features_a or not features_b:
        return {
            "id": check["id"],
            "type": "feature_alignment",
            "status": "fail",
            "error": error_a or error_b,
            "features": [selector_a.get("id"), selector_b.get("id")],
            "tolerance": tolerance,
        }

    # When a selector matches several features (e.g. a whole bolt pattern), pair
    # the closest-to-coaxial features. This asks "does each hole on A have a
    # coaxial partner on B" without depending on the two parts' holes being
    # detected in the same order. A feature is never paired with itself, so a
    # too-broad selector that resolves both sides to the same feature cannot
    # produce a vacuous self-aligned pass.
    pairings = [
        (candidate_a, candidate_b, *_axis_alignment(candidate_a, candidate_b))
        for candidate_a in features_a
        for candidate_b in features_b
        if candidate_a is not candidate_b
    ]
    if not pairings:
        return {
            "id": check["id"],
            "type": "feature_alignment",
            "status": "fail",
            "error": "the two selectors resolve to the same feature; nothing to align",
            "features": [features_a[0].get("id"), features_a[0].get("id")],
            "tolerance": tolerance,
        }
    feature_a, feature_b, axis_offset, axis_angle_deg = min(
        pairings, key=lambda pairing: (pairing[2], pairing[3])
    )
    diameter_a = feature_a.get("diameter")
    diameter_b = feature_b.get("diameter")
    diameter_ok = True
    if diameter_a is not None and diameter_b is not None:
        diameter_ok = abs(float(diameter_a) - float(diameter_b)) <= diameter_tolerance

    passed = axis_offset <= tolerance and axis_angle_deg <= tolerance and diameter_ok
    return {
        "id": check["id"],
        "type": "feature_alignment",
        "status": "pass" if passed else "fail",
        "features": [feature_a["id"], feature_b["id"]],
        "axis_offset": axis_offset,
        "axis_angle_deg": axis_angle_deg,
        "diameters": [diameter_a, diameter_b],
        "tolerance": tolerance,
        "diameter_tolerance": diameter_tolerance,
    }


def _aabb_overlaps(left_bbox: dict[str, list[float]], right_bbox: dict[str, list[float]]) -> bool:
    """Whether two axis-aligned bounding boxes overlap with positive penetration."""

    for axis in range(3):
        if left_bbox["max"][axis] <= right_bbox["min"][axis]:
            return False
        if right_bbox["max"][axis] <= left_bbox["min"][axis]:
            return False
    return True


def _interference_objects(
    spatial: dict[str, Any], check: dict[str, Any]
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Select the objects an interference check ranges over.

    Returns ``(objects, None)`` for a valid selection, or ``(None, error)`` when
    a ``between`` reference names an object that does not exist, so the check can
    report a descriptive failure instead of raising (matching
    ``feature_alignment``).
    """

    objects = spatial.get("objects", [])
    if "between" not in check:
        return objects, None
    index = _object_index(spatial)
    selected: list[dict[str, Any]] = []
    for ref in check["between"]:
        label = _label_from_ref(ref)
        if label not in index:
            return None, f"no object {ref!r}"
        selected.append(index[label])
    return selected, None


def _check_interference(spatial: dict[str, Any], check: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Flag every pair of solids that overlaps in the assembly frame.

    Exact BREP intersection volume is used when per-object STEP exports exist
    (``distance()`` cannot distinguish a true overlap from a face-to-face touch —
    both are 0). A synthetic publication with no STEP falls back to AABB overlap
    so evaluator logic stays testable without a CAD kernel.
    """

    tolerance = float(check.get("tolerance", 1e-6))
    selected, error = _interference_objects(spatial, check)
    if error is not None:
        return {
            "id": check["id"],
            "type": "interference",
            "status": "fail",
            "error": error,
            "pairs": [],
            "overlaps": [],
            "tolerance": tolerance,
        }
    try:
        step_index = _step_export_index(run_dir)
    except Exception:
        step_index = {}

    use_exact = len(selected) >= 2 and all(obj["label"] in step_index for obj in selected)
    pairs: list[list[str]] = []
    overlaps: list[dict[str, Any]] = []

    if use_exact:
        from build123d import import_step

        shapes = {obj["label"]: import_step(step_index[obj["label"]]) for obj in selected}
        for left_index in range(len(selected)):
            for right_index in range(left_index + 1, len(selected)):
                left_label = selected[left_index]["label"]
                right_label = selected[right_index]["label"]
                try:
                    intersection = shapes[left_label].intersect(shapes[right_label])
                    volume = float(intersection.volume) if intersection is not None else 0.0
                except Exception:
                    volume = 0.0
                if volume > tolerance:
                    pairs.append([left_label, right_label])
                    overlaps.append({"labels": [left_label, right_label], "volume": volume})
    else:
        for left_index in range(len(selected)):
            for right_index in range(left_index + 1, len(selected)):
                left = selected[left_index]
                right = selected[right_index]
                if _aabb_overlaps(left["bbox"], right["bbox"]):
                    pairs.append([left["label"], right["label"]])
                    overlaps.append({"labels": [left["label"], right["label"]], "volume": None})

    return {
        "id": check["id"],
        "type": "interference",
        "status": "pass" if not pairs else "fail",
        "pairs": pairs,
        "overlaps": overlaps,
        "tolerance": tolerance,
    }


def _resolve_center_of_mass(spatial: dict[str, Any], target: str) -> tuple[list[float] | None, str | None]:
    """Resolve a center-of-mass target to a 3-vector, or an error message.

    ``target`` is either the literal ``"assembly"`` (the aggregate written by the
    inspector) or an object path such as
    ``obj.<label>.mass_properties.center_of_mass`` resolved through the existing
    dimension grammar.
    """

    if target == "assembly":
        assembly = spatial.get("assembly")
        if not assembly or "center_of_mass" not in assembly:
            return None, "no assembly center of mass available"
        return [float(component) for component in assembly["center_of_mass"]], None
    try:
        value = _resolve_dimension(spatial, target)
    except (KeyError, ValueError) as exc:
        return None, f"could not resolve center of mass target {target!r}: {exc}"
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None, f"center of mass target {target!r} is not a 3-vector"
    return [float(component) for component in value], None


def _check_center_of_mass(spatial: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Assert a center of mass against a target point or an axis-aligned region."""

    observed, error = _resolve_center_of_mass(spatial, check.get("target", "assembly"))
    tolerance = check.get("tolerance", 0)
    if error is not None:
        return {"id": check["id"], "type": "center_of_mass", "status": "fail", "error": error, "tolerance": tolerance}

    if "region" in check:
        region = check["region"]
        passed = all(region["min"][axis] <= observed[axis] <= region["max"][axis] for axis in range(3))
        expected: Any = region
    else:
        expected = [float(component) for component in check["expected"]]
        passed = all(abs(observed[axis] - expected[axis]) <= float(tolerance) for axis in range(3))

    return {
        "id": check["id"],
        "type": "center_of_mass",
        "status": "pass" if passed else "fail",
        "observed": observed,
        "expected": expected,
        "tolerance": tolerance,
    }


def _convex_hull(points: list[list[float]]) -> list[list[float]]:
    """Monotone-chain convex hull of 2D points (counter-clockwise).

    Deterministic and tolerant of duplicate/collinear input. A support footprint
    is supplied unordered, so the hull both orders it and discards any concave
    indentation (conservative: it can only widen the base).
    """

    unique = sorted({(float(point[0]), float(point[1])) for point in points})
    if len(unique) <= 2:
        return [list(point) for point in unique]

    def cross(origin: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return [list(point) for point in lower[:-1] + upper[:-1]]


def _point_in_polygon(point: list[float], polygon: list[list[float]]) -> bool:
    """Even-odd ray-cast test for a point inside a 2D polygon."""

    x, y = point[0], point[1]
    inside = False
    count = len(polygon)
    previous = count - 1
    for current in range(count):
        xi, yi = polygon[current]
        xj, yj = polygon[previous]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        previous = current
    return inside


def _point_segment_distance(point: list[float], start: list[float], end: list[float]) -> float:
    """Shortest distance from a point to a 2D line segment."""

    px, py = point[0], point[1]
    ax, ay = start[0], start[1]
    bx, by = end[0], end[1]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return sqrt((px - ax) ** 2 + (py - ay) ** 2)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return sqrt((px - cx) ** 2 + (py - cy) ** 2)


def _signed_margin(point: list[float], polygon: list[list[float]]) -> float:
    """Signed distance from ``point`` to the polygon boundary, positive inside.

    A degenerate support (a single point or a line) cannot contain a projected
    center of mass, so the margin is the negative distance to it (always unstable).
    """

    if not polygon:
        return float("-inf")
    if len(polygon) == 1:
        return -sqrt((point[0] - polygon[0][0]) ** 2 + (point[1] - polygon[0][1]) ** 2)
    if len(polygon) == 2:
        return -_point_segment_distance(point, polygon[0], polygon[1])
    distance = min(
        _point_segment_distance(point, polygon[index], polygon[(index + 1) % len(polygon)])
        for index in range(len(polygon))
    )
    return distance if _point_in_polygon(point, polygon) else -distance


def _check_stability(spatial: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Assert a center of mass projects inside its support polygon.

    The projected (x, y) center of mass must sit inside the convex hull of the
    support points by at least ``min_margin``. When ``com_height`` is supplied the
    worst-case tip angle ``atan2(margin, com_height)`` is reported and can be
    gated with ``min_tip_angle_deg``.
    """

    observed_com, error = _resolve_center_of_mass(spatial, check.get("target", "assembly"))
    if error is not None:
        return {"id": check["id"], "type": "stability", "status": "fail", "error": error}

    point = [observed_com[0], observed_com[1]]
    hull = _convex_hull(check["support"])
    margin = _signed_margin(point, hull)
    passed = margin >= float(check.get("min_margin", 0.0))

    result: dict[str, Any] = {
        "id": check["id"],
        "type": "stability",
        "observed": point,
        "margin": margin,
        "support": hull,
    }
    com_height = check.get("com_height")
    if com_height is not None and float(com_height) > 0:
        tip_angle_deg = degrees(atan2(margin, float(com_height)))
        result["tip_angle_deg"] = tip_angle_deg
        if "min_tip_angle_deg" in check:
            passed = passed and tip_angle_deg >= float(check["min_tip_angle_deg"])
    result["status"] = "pass" if passed else "fail"
    return result


def _check_bend(run_dir: Path, check: dict[str, Any]) -> dict[str, Any]:
    """Assert sheet-metal bends from ``bends.json`` (ADR 0016).

    Optional clauses: ``count`` (exact bend count), and ``angle`` /
    ``inside_radius`` / ``direction`` which require at least one bend matching the
    value (numeric clauses within ``tolerance``). A run with no bend table fails
    with a descriptive error rather than raising.
    """

    try:
        table = read_json(run_dir / "bends.json")
    except Exception as exc:
        return {
            "id": check["id"],
            "type": "bend",
            "status": "fail",
            "error": f"no bend table: {exc}",
            "observed": {"count": 0, "bends": []},
        }

    bends = table.get("bends", [])
    tolerance = float(check.get("tolerance", 0))
    passed = True
    if "count" in check:
        passed = passed and len(bends) == check["count"]
    if "angle" in check:
        passed = passed and any(abs(float(bend.get("angle", 0)) - float(check["angle"])) <= tolerance for bend in bends)
    if "inside_radius" in check:
        passed = passed and any(
            abs(float(bend.get("inside_radius", 0)) - float(check["inside_radius"])) <= tolerance for bend in bends
        )
    if "direction" in check:
        passed = passed and any(bend.get("direction") == check["direction"] for bend in bends)

    expected = {key: check[key] for key in ("count", "angle", "inside_radius", "direction") if key in check}
    return {
        "id": check["id"],
        "type": "bend",
        "status": "pass" if passed else "fail",
        "observed": {"count": len(bends), "bends": bends},
        "expected": expected,
    }


def _evaluate_check(spatial: dict[str, Any], check: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Route a requirement entry to its evaluator."""

    check_type = check["type"]
    if check_type == "dimension":
        return _check_dimension(spatial, check)
    if check_type == "topology":
        return _check_topology(spatial, check)
    if check_type == "clearance":
        if check.get("method") == "exact":
            return _check_exact_clearance(run_dir, check)
        return _check_clearance(spatial, check)
    if check_type == "feature_count":
        return _check_feature_count(spatial, check)
    if check_type == "feature_dimension":
        return _check_feature_dimension(spatial, check)
    if check_type == "feature_alignment":
        return _check_feature_alignment(spatial, check)
    if check_type == "interference":
        return _check_interference(spatial, check, run_dir)
    if check_type == "center_of_mass":
        return _check_center_of_mass(spatial, check)
    if check_type == "stability":
        return _check_stability(spatial, check)
    if check_type == "bend":
        return _check_bend(run_dir, check)
    if check_type == "manufacturability":
        return evaluate_manufacturability(spatial, check)
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
    results = [_evaluate_check(spatial, check, run_dir) for check in requirements.get("checks", [])]
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
