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
from cadx.files import read_json, write_json, write_yaml


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


def _resolve_dimension_or_error(spatial: dict[str, Any], check: dict[str, Any]) -> tuple[Any, dict[str, Any] | None]:
    """Resolve a target value, or return a graceful failed-check record.

    A mistyped target (e.g. ``obj.plate.solids`` missing the ``.topology``
    segment) is a common authoring error; it fails the one check with a
    descriptive error rather than raising and aborting the whole evaluation,
    matching how the assembly checks degrade on bad selectors.
    """

    try:
        return _resolve_dimension(spatial, check["target"]), None
    except (KeyError, ValueError, IndexError, TypeError) as exc:
        return None, {
            "id": check["id"],
            "type": check["type"],
            "status": "fail",
            "error": f"could not resolve target {check['target']!r}: {exc}",
        }


def _scalar_or_error(observed: Any, check: dict[str, Any]) -> tuple[float | None, dict[str, Any] | None]:
    """Coerce a resolved target value to a scalar, or build a failed record.

    Resolution can succeed and still hand back something no numeric clause can
    judge: a vector (the author forgot a trailing ``.x``) or ``None`` (a
    topology selector the object could not count). Those fail the one check
    with a descriptive error, completing the graceful-degradation contract that
    ``_resolve_dimension_or_error`` starts.
    """

    try:
        return float(observed), None
    except (TypeError, ValueError):
        return None, {
            "id": check["id"],
            "type": check["type"],
            "status": "fail",
            "observed": observed,
            "error": f"target {check['target']!r} resolved to non-numeric value {observed!r}",
        }


def _part_frame_target(check: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve the effective target path for a check's optional ``frame`` (ADR 0049).

    ``frame`` defaults to ``"world"`` — byte-identical to pre-ADR behavior, the
    target is used unchanged. ``frame: "part"`` (D-025) measures the same
    dimension in the part's own frame by rewriting the target's ``bbox`` path
    segment to ``bbox_local`` (the part-frame bounding box recorded at publish
    time), so ``obj.x.bbox.size.y`` becomes ``obj.x.bbox_local.size.y``. Any
    other value fails the one check loudly with a descriptive error rather than
    silently measuring in the wrong frame — the exact failure D-025 removes.

    Returns ``(effective_target, None)`` on success or ``(None, error_record)``.
    """

    frame = check.get("frame", "world")
    target = check["target"]
    if frame == "world":
        return target, None
    if frame == "part":
        # Redirect the bbox segment to bbox_local; only the first match is
        # rewritten so a label literally named "bbox" cannot be corrupted.
        segments = target.split(".")
        rewritten = False
        for index, segment in enumerate(segments):
            if segment == "bbox":
                segments[index] = "bbox_local"
                rewritten = True
                break
        if not rewritten:
            return None, {
                "id": check["id"],
                "type": check["type"],
                "status": "fail",
                "error": f"frame 'part' requires a bbox target; {target!r} has no 'bbox' segment",
            }
        return ".".join(segments), None
    return None, {
        "id": check["id"],
        "type": check["type"],
        "status": "fail",
        "error": f"unsupported frame {frame!r}; expected 'world' or 'part'",
    }


def _check_scalar_target(spatial: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a scalar exact/range check (``dimension`` and ``topology``)."""

    effective_target, frame_error = _part_frame_target(check)
    if frame_error is not None:
        return frame_error
    # Resolve against the (possibly frame-rewritten) target without mutating the
    # caller's check dict.
    check = {**check, "target": effective_target}
    observed, error = _resolve_dimension_or_error(spatial, check)
    if error is not None:
        return error
    scalar, error = _scalar_or_error(observed, check)
    if error is not None:
        return error
    expected = _numeric_expectation(check)
    tolerance = check.get("tolerance", 0)
    passed = _numeric_status(scalar, check)
    return {
        "id": check["id"],
        "type": check["type"],
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


def _exact_clearance_failure(check: dict[str, Any], error: str) -> dict[str, Any]:
    """Failed exact-clearance record for unresolvable inputs.

    Matches ``interference``'s graceful degradation: a label with no STEP export
    (synthetic publication, failed export, or typo) fails this one check with a
    descriptive error instead of raising a bare ``KeyError`` out of the whole
    evaluation.
    """

    return {
        "id": check["id"],
        "type": "clearance",
        "method": "exact",
        "status": "fail",
        "error": error,
        "between": check["between"],
        "tolerance": check.get("tolerance", 0),
    }


def _check_exact_clearance(run_dir: Path, check: dict[str, Any]) -> dict[str, Any]:
    """Evaluate exact BREP clearance from exported STEP artifacts."""

    left_ref, right_ref = check["between"]
    try:
        left_label = _label_from_ref(left_ref)
        right_label = _label_from_ref(right_ref)
    except ValueError as exc:
        return _exact_clearance_failure(check, str(exc))
    try:
        exports = _step_export_index(run_dir)
    except Exception as exc:
        return _exact_clearance_failure(check, f"no readable diagnostics for STEP exports: {exc}")
    missing = [label for label in (left_label, right_label) if label not in exports]
    if missing:
        return _exact_clearance_failure(
            check, f"no STEP export for {', '.join(repr(label) for label in missing)}"
        )

    from build123d import import_step

    try:
        left_shape = import_step(exports[left_label])
        right_shape = import_step(exports[right_label])
        observed = float(left_shape.distance(right_shape))
    except Exception as exc:
        return _exact_clearance_failure(check, f"could not measure exact clearance: {exc}")
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
    result = {
        "id": check["id"],
        "type": "feature_dimension",
        "observed": observed,
        "expected": expected,
        "tolerance": tolerance,
    }
    # A selected feature may simply not carry the property (or carry a
    # non-numeric value); that is an authoring/publication mismatch, so the one
    # check fails with the offending feature named rather than crashing the
    # whole evaluation on float(None).
    values: list[float] = []
    for feature, value in zip(selected, observed):
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            result["status"] = "fail"
            result["error"] = (
                f"feature {feature.get('id')!r} has no numeric {property_name!r} (got {value!r})"
            )
            return result
    passed = bool(values) and all(
        _within_tolerance(value, float(expected), float(tolerance)) for value in values
    )
    result["status"] = "pass" if passed else "fail"
    return result


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
    except (KeyError, ValueError, IndexError, TypeError) as exc:
        # The same failure set _resolve_dimension_or_error guards: a path can
        # also index into a scalar (TypeError) or past a vector (IndexError).
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


def _run_param_set(run_dir: Path, params: dict[str, Any], sweep_root: Path, timeout: float) -> tuple[Path, str]:
    """Re-run the design's source snapshot with one parameter set.

    The swept design is the run's own ``source_snapshot.py``, so the geometry the
    sweep evaluates matches the geometry the original run produced. Returns the
    parameter set's run directory and its run status.
    """

    from cadx.runner import run_design

    params_path = sweep_root / "params.yaml"
    write_yaml(params_path, params)
    payload = run_design(run_dir / "source_snapshot.py", params_path, sweep_root, timeout)
    return Path(payload["artifact_dir"]), payload["status"]


def _range_violations(set_run_dir: Path) -> list[dict[str, Any]]:
    """Return a swept run's declared joint-limit violations (ADR 0039, D-009).

    Reads the set's ``diagnostics.json`` for ``mate_out_of_range`` warnings — the
    machine-visible record ADR 0025 already emits when a pose sits outside its
    declared ``angle_range`` / ``travel_range``. Each violation keeps the mate
    ``label`` and the warning ``message`` (which already names the pose value and
    the declared range), so a failing set report points straight at the joint
    that overshot. A run with no diagnostics degrades to "no violations": the
    absence of the warning file is not itself a limit violation.
    """

    try:
        diagnostics = read_json(set_run_dir / "diagnostics.json")
    except Exception:
        return []
    return [
        {"label": warning.get("label"), "message": warning.get("message")}
        for warning in diagnostics.get("warnings", [])
        if warning.get("type") == "mate_out_of_range"
    ]


def _check_parametric(run_dir: Path, check: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Run the design across parameter sets and aggregate ordinary sub-checks.

    Useful for tolerance/stack-up studies: each parameter set re-runs the design
    into ``<run_dir>/sweeps/<check id>/NNNN`` and the listed sub-checks are
    evaluated against that set's ``spatial.json``. The aggregate passes only when
    every set passes. The sweep directory is cleared each evaluation so re-running
    is idempotent.
    """

    import shutil

    sweep_root = run_dir / "sweeps" / check["id"]
    if sweep_root.exists():
        shutil.rmtree(sweep_root)
    sweep_root.mkdir(parents=True, exist_ok=True)

    # ADR 0039 (D-009): opt-in enforcement of declared joint limits. Off by
    # default so every existing sweep behaves byte-for-byte as before (an
    # out-of-range pose stays a warning and the geometry is still placed as
    # requested); when on, a swept pose outside its declared range fails its set.
    fail_on_range = bool(check.get("fail_on_range_violation", False))

    sets: list[dict[str, Any]] = []
    for params in check["params"]:
        set_run_dir, status = _run_param_set(run_dir, params, sweep_root, timeout)
        if status != "ok":
            sets.append({"params": params, "status": "fail", "run_status": status, "checks": []})
            continue
        spatial = read_json(set_run_dir / "spatial.json")
        sub_checks = [_evaluate_check(spatial, sub, set_run_dir, timeout) for sub in check.get("checks", [])]
        range_violations = _range_violations(set_run_dir) if fail_on_range else []
        set_passed = all(result["status"] == "pass" for result in sub_checks) and not range_violations
        set_record: dict[str, Any] = {
            "params": params,
            "status": "pass" if set_passed else "fail",
            "checks": sub_checks,
        }
        if range_violations:
            set_record["range_violations"] = range_violations
        sets.append(set_record)

    passed = sum(1 for entry in sets if entry["status"] == "pass")
    return {
        "id": check["id"],
        "type": "parametric",
        "status": "pass" if sets and passed == len(sets) else "fail",
        "passed": passed,
        "total": len(sets),
        "sets": sets,
    }


def _view_cone_error(check: dict[str, Any], error: str) -> dict[str, Any]:
    """Failed ``view_cone`` record for malformed/unresolvable configuration.

    Matches the loud-error contract of ADR 0049 (unknown ``frame:``) and the
    assembly checks: a mistyped axis/half-angle/target fails this one check with a
    descriptive message naming the bad value, never a silent pass and never an
    aborted run.
    """

    return {"id": check["id"], "type": "view_cone", "status": "fail", "error": error}


def _resolve_point(spatial: dict[str, Any], spec: Any) -> tuple[list[float] | None, str | None]:
    """Resolve a point specification to a world 3-vector, or an error message.

    A point is either an explicit ``[x, y, z]`` list or a reference string. The
    reference reuses the dimension resolver (``_resolve_dimension``) so any path
    that lands on a 3-vector works (e.g.
    ``obj.<label>.mass_properties.center_of_mass``). As a convenience,
    ``obj.<label>.center`` and ``obj.<label>.bbox.center`` resolve to the midpoint
    of the object's world bounding box — objects carry a ``bbox`` but no ``center``
    key, so the midpoint is computed rather than looked up.
    """

    if isinstance(spec, (list, tuple)):
        if len(spec) != 3:
            return None, f"point {spec!r} is not a 3-vector"
        try:
            return [float(component) for component in spec], None
        except (TypeError, ValueError):
            return None, f"point {spec!r} has non-numeric components"

    if isinstance(spec, str):
        # Convenience shorthand for a bbox centre: obj.<label>.center or
        # obj.<label>.bbox.center. Everything between the label and the trailing
        # "center" must be empty or exactly "bbox".
        parts = spec.split(".")
        if len(parts) >= 2 and parts[0] == "obj" and parts[-1] == "center" and parts[2:-1] in ([], ["bbox"]):
            obj = _object_index(spatial).get(parts[1])
            if obj is None:
                return None, f"no object {spec!r}"
            bbox = obj.get("bbox")
            if not bbox or "min" not in bbox or "max" not in bbox:
                return None, f"object {parts[1]!r} has no bbox for center"
            return [(low + high) / 2 for low, high in zip(bbox["min"], bbox["max"])], None
        try:
            value = _resolve_dimension(spatial, spec)
        except (KeyError, ValueError, IndexError, TypeError) as exc:
            return None, f"could not resolve point {spec!r}: {exc}"
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return None, f"point {spec!r} did not resolve to a 3-vector"
        try:
            return [float(component) for component in value], None
        except (TypeError, ValueError):
            return None, f"point {spec!r} resolved to a non-numeric value {value!r}"

    return None, f"unsupported point specification {spec!r}"


def _bbox_test_points(bbox: dict[str, list[float]]) -> list[list[float]]:
    """Return the 8 corners AND the centre of a bounding box (9 points).

    ADR 0052 tests a target's whole extent, not just its centre: a jaw whose
    centre is visible but whose tip pokes out of the FOV cone must fail. The
    corner index bits select the low/high face on each axis.
    """

    low = bbox["min"]
    high = bbox["max"]
    corners = [
        [
            high[0] if index & 1 else low[0],
            high[1] if index & 2 else low[1],
            high[2] if index & 4 else low[2],
        ]
        for index in range(8)
    ]
    center = [(low[axis] + high[axis]) / 2 for axis in range(3)]
    return corners + [center]


def _segment_intersects_aabb(
    start: list[float], end: list[float], low: list[float], high: list[float], eps: float = 1e-9
) -> bool:
    """Whether the open segment ``start``->``end`` crosses an axis-aligned box.

    Slab method: intersect the segment's parameter range ``[0, 1]`` with each
    axis slab of the box. A positive-length overlap strictly inside the open
    segment counts as a crossing; an endpoint merely touching the box face
    (overlap collapsing to ``t=0`` or ``t=1``) does not, so a point lying on an
    occluder's own surface is not treated as occluded by that occluder.
    """

    t_enter, t_exit = 0.0, 1.0
    for axis in range(3):
        direction = end[axis] - start[axis]
        if abs(direction) < eps:
            # Segment runs parallel to this slab; it can only cross the box if it
            # already lies within the slab's extent.
            if start[axis] < low[axis] - eps or start[axis] > high[axis] + eps:
                return False
            continue
        t_low = (low[axis] - start[axis]) / direction
        t_high = (high[axis] - start[axis]) / direction
        if t_low > t_high:
            t_low, t_high = t_high, t_low
        t_enter = max(t_enter, t_low)
        t_exit = min(t_exit, t_high)
        if t_enter > t_exit:
            return False
    # Require a real crossing that lies strictly between the endpoints.
    return t_exit - t_enter > eps and t_exit > eps and t_enter < 1.0 - eps


def _point_in_cone(
    point: list[float], apex: list[float], axis_unit: list[float], half_angle_deg: float
) -> tuple[bool, bool, float | None]:
    """Classify a point against a view cone.

    Returns ``(inside, behind, angle_deg)``. ``behind`` is true when the point is
    on the apex plane or behind it (``dot(point - apex, axis) <= 0``): a camera
    does not see behind itself, and the apex direction is undefined, so such a
    point is never inside. ``angle_deg`` is the angle between ``point - apex`` and
    the axis for a forward point, or ``None`` for a behind point.
    """

    vector = [point[axis] - apex[axis] for axis in range(3)]
    along = sum(component * unit for component, unit in zip(vector, axis_unit))
    if along <= 0:
        return False, True, None
    length = sqrt(sum(component * component for component in vector))
    # length > 0 here because along > 0 implies a non-zero vector.
    cosine = min(1.0, max(-1.0, along / length))
    angle_deg = degrees(acos(cosine))
    return angle_deg <= half_angle_deg, False, angle_deg


def _view_cone_target_points(
    spatial: dict[str, Any], target: Any
) -> tuple[list[list[float]] | None, str | None]:
    """Resolve a target to its test points, or an error message.

    An object reference ``obj.<label>`` contributes its bounding-box corners and
    centre (9 points); an explicit ``[x, y, z]`` list contributes that single
    point.
    """

    if isinstance(target, str):
        if not target.startswith("obj."):
            return None, f"unsupported target {target!r}; expected obj.<label> or a point"
        obj = _object_index(spatial).get(target.split(".", 1)[1])
        if obj is None:
            return None, f"no object {target!r}"
        bbox = obj.get("bbox")
        if not bbox or "min" not in bbox or "max" not in bbox:
            return None, f"target {target!r} has no bbox"
        return _bbox_test_points(bbox), None
    point, error = _resolve_point(spatial, target)
    if error is not None:
        return None, error
    return [point], None


def _check_view_cone(spatial: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Assert that targets lie inside a view cone, sightlines optionally clear.

    ADR 0052 (D-027): a field-of-view containment check. The cone is an apex
    point, an axis direction (normalized here), and a half-angle. Each target's
    test points (a target object's bbox corners+centre, or an explicit point) must
    fall inside the cone; with ``occluders`` present, the apex->point sightline
    must also miss every occluder's bounding box (an AABB approximation — see the
    ADR). Any malformed input fails the one check loudly rather than passing.
    """

    # --- Validate the cone axis. --------------------------------------------
    axis = check.get("axis")
    if axis is None:
        return _view_cone_error(check, "missing 'axis'; a view_cone needs a direction [x, y, z]")
    if not isinstance(axis, (list, tuple)) or len(axis) != 3:
        return _view_cone_error(check, f"axis {axis!r} is not a 3-vector")
    try:
        axis = [float(component) for component in axis]
    except (TypeError, ValueError):
        return _view_cone_error(check, f"axis {axis!r} has non-numeric components")
    axis_unit = _unit_vector(axis)
    if axis_unit == [0.0, 0.0, 0.0]:
        return _view_cone_error(check, f"axis {axis!r} is zero-length; a direction is required")

    # --- Validate the half-angle. -------------------------------------------
    if "half_angle_deg" not in check:
        return _view_cone_error(check, "missing 'half_angle_deg'")
    try:
        half_angle_deg = float(check["half_angle_deg"])
    except (TypeError, ValueError):
        return _view_cone_error(check, f"half_angle_deg {check['half_angle_deg']!r} is not numeric")
    if not 0 < half_angle_deg <= 180:
        return _view_cone_error(
            check, f"half_angle_deg {check['half_angle_deg']!r} must be in (0, 180]"
        )

    # --- Resolve the apex point. --------------------------------------------
    if "apex" not in check:
        return _view_cone_error(check, "missing 'apex'")
    apex, error = _resolve_point(spatial, check["apex"])
    if error is not None:
        return _view_cone_error(check, f"could not resolve apex: {error}")

    # --- Resolve occluder bounding boxes (optional). ------------------------
    occluders: list[tuple[list[float], list[float]]] = []
    for occluder in check.get("occluders", []) or []:
        if not isinstance(occluder, str) or not occluder.startswith("obj."):
            return _view_cone_error(check, f"unsupported occluder {occluder!r}; expected obj.<label>")
        obj = _object_index(spatial).get(occluder.split(".", 1)[1])
        if obj is None:
            return _view_cone_error(check, f"no occluder object {occluder!r}")
        bbox = obj.get("bbox")
        if not bbox or "min" not in bbox or "max" not in bbox:
            return _view_cone_error(check, f"occluder {occluder!r} has no bbox")
        occluders.append((bbox["min"], bbox["max"]))

    # --- Require at least one target. ---------------------------------------
    targets = check.get("targets")
    if not targets:
        return _view_cone_error(check, "missing 'targets'; a view_cone needs at least one target")

    # --- Test every target. -------------------------------------------------
    target_results: list[dict[str, Any]] = []
    for target in targets:
        points, error = _view_cone_target_points(spatial, target)
        if error is not None:
            return _view_cone_error(check, error)

        worst_angle: float | None = None
        worst_point: list[float] | None = None
        behind_point: list[float] | None = None
        any_occluded = False
        for point in points:
            inside, behind, angle_deg = _point_in_cone(point, apex, axis_unit, half_angle_deg)
            if behind:
                if behind_point is None:
                    behind_point = point
            elif angle_deg is not None and (worst_angle is None or angle_deg > worst_angle):
                worst_angle = angle_deg
                worst_point = point
            if occluders and any(
                _segment_intersects_aabb(apex, point, low, high) for low, high in occluders
            ):
                any_occluded = True

        # A target fails if any point is behind the apex, any point exceeds the
        # half-angle, or any sightline is occluded. Reasons are prioritized so the
        # most fundamental visibility failure is reported first.
        angle_exceeds = worst_angle is not None and worst_angle > half_angle_deg
        record: dict[str, Any] = {
            "target": list(target) if isinstance(target, (list, tuple)) else target,
            "angle_deg": worst_angle,
            "occluded": any_occluded,
        }
        if behind_point is not None:
            record["status"] = "fail"
            record["reason"] = "behind_apex"
            record["worst_point"] = behind_point
        elif angle_exceeds:
            record["status"] = "fail"
            record["reason"] = "angle_exceeds_half_angle"
            record["worst_point"] = worst_point
        elif any_occluded:
            record["status"] = "fail"
            record["reason"] = "occluded"
        else:
            record["status"] = "pass"
        target_results.append(record)

    passed = all(result["status"] == "pass" for result in target_results)
    return {
        "id": check["id"],
        "type": "view_cone",
        "status": "pass" if passed else "fail",
        "apex": apex,
        "axis": axis_unit,
        "half_angle_deg": half_angle_deg,
        "occlusion_method": "aabb",
        "targets": target_results,
    }


def _evaluate_check(
    spatial: dict[str, Any], check: dict[str, Any], run_dir: Path, timeout: float = 30.0
) -> dict[str, Any]:
    """Route a requirement entry to its evaluator."""

    check_type = check["type"]
    if check_type in ("dimension", "topology"):
        return _check_scalar_target(spatial, check)
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
    if check_type == "view_cone":
        return _check_view_cone(spatial, check)
    if check_type == "bend":
        return _check_bend(run_dir, check)
    if check_type == "manufacturability":
        return evaluate_manufacturability(spatial, check)
    if check_type == "parametric":
        return _check_parametric(run_dir, check, timeout)
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


def sweep_run(run_dir: Path, requirements_path: Path, timeout: float = 30.0) -> dict[str, Any]:
    """Evaluate only the ``parametric`` checks in a requirements file.

    A focused, read-mostly view that runs each parametric sweep and reports the
    aggregate verdicts without rewriting ``checks.json`` (which ``evaluate``
    owns).
    """

    requirements = yaml.safe_load(requirements_path.read_text(encoding="utf-8")) or {}
    parametric = [check for check in requirements.get("checks", []) if check.get("type") == "parametric"]
    sweeps = [_check_parametric(run_dir, check, timeout) for check in parametric]
    failed = [sweep["id"] for sweep in sweeps if sweep["status"] != "pass"]
    return {"status": "pass" if not failed else "fail", "sweeps": sweeps, "failed": failed}


def evaluate_run(run_dir: Path, requirements_path: Path, timeout: float = 30.0) -> dict[str, Any]:
    """Evaluate ``requirements.yaml`` against a run's spatial facts."""

    spatial = read_json(run_dir / "spatial.json")
    requirements = yaml.safe_load(requirements_path.read_text(encoding="utf-8")) or {}
    results = [_evaluate_check(spatial, check, run_dir, timeout) for check in requirements.get("checks", [])]
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
