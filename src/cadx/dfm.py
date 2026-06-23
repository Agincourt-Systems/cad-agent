"""Laser/sheet manufacturability (DFM) rules.

Implements the spec's ``manufacturability`` requirement type as deterministic,
pure-Python rules over ``spatial.json`` features and object bounding boxes. No CAD
kernel is needed: a hole's diameter, a feature's center and axis, and the owning
object's bounding box are enough to flag the common laser/waterjet DFM violations
a shop like SendCutSend rejects — undersized holes, holes/slots too close to an
edge or to each other, and (for explicitly published ``bend`` features) minimum
bend radius and hole-to-bend distance.

Each rule's limit is either an explicit absolute ``min`` or ``factor * thickness``
(defaulting to the per-rule factor in ``DEFAULT_FACTORS``); thickness defaults to
the owning object's smallest bounding-box dimension. A rule whose features are
absent (e.g. no ``slot`` or ``bend`` features) simply contributes no violations.

Note on bends: ADR 0016 records a part's bends in ``bends.json`` (consumed by the
``bend`` check), not as ``spatial.json`` features. The ``min_bend_radius`` and
``hole_to_bend`` rules here therefore operate on explicitly published
``kind="bend"`` features, so they are inert for the standard sheet-metal flow
until such a feature is published.
"""

from __future__ import annotations

from math import sqrt
from typing import Any


# Default rule limits as a multiple of material thickness.
DEFAULT_FACTORS = {
    "min_hole_diameter": 1.0,
    "min_slot_width": 1.0,
    "min_web": 1.0,
    "hole_to_edge": 1.0,
    "min_bend_radius": 1.0,
    "hole_to_bend": 2.0,
}

_CYLINDRICAL = {"cylindrical_hole", "cylindrical_boss"}
_EDGE_KINDS = _CYLINDRICAL | {"slot"}


def _object_index(spatial: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {obj["label"]: obj for obj in spatial.get("objects", [])}


def _owning_object(index: dict[str, dict[str, Any]], feature: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve ``feature['source_object']`` (``obj.<label>``) to its object dict."""

    source = feature.get("source_object")
    if not isinstance(source, str) or not source.startswith("obj."):
        return None
    return index.get(source.split(".", 1)[1])


def _axis_index(feature: dict[str, Any]) -> int:
    """Dominant axis of the feature's axis vector (default z when absent)."""

    axis = feature.get("axis")
    if not isinstance(axis, (list, tuple)) or len(axis) != 3:
        return 2
    return max(range(3), key=lambda index: abs(axis[index]))


def _half_extent(feature: dict[str, Any]) -> float:
    """Radius of a hole/boss or half-width of a slot (its narrow dimension)."""

    kind = feature.get("kind")
    if kind in _CYLINDRICAL and feature.get("diameter") is not None:
        return float(feature["diameter"]) / 2.0
    if kind == "slot" and feature.get("width") is not None:
        return float(feature["width"]) / 2.0
    return 0.0


def _half_extent_along(feature: dict[str, Any], axis: int) -> float:
    """Half-extent of a feature along a specific in-plane axis.

    A hole is circular, so its half-extent is the radius along every axis. A
    detected slot is elongated along its ``axis`` (the inspector records the
    elongation direction there) with ``length`` along that axis and ``width``
    across it, so edge clearance must subtract the correct one per axis.
    """

    kind = feature.get("kind")
    if kind in _CYLINDRICAL and feature.get("diameter") is not None:
        return float(feature["diameter"]) / 2.0
    if kind == "slot":
        if axis == _axis_index(feature) and feature.get("length") is not None:
            return float(feature["length"]) / 2.0
        if feature.get("width") is not None:
            return float(feature["width"]) / 2.0
    return 0.0


def _thickness_axis(obj: dict[str, Any]) -> int | None:
    """The sheet's thickness axis: the owning object's thinnest bbox dimension."""

    size = obj.get("bbox", {}).get("size")
    if not size:
        return None
    return min(range(3), key=lambda axis: size[axis])


def _resolve_thickness(check: dict[str, Any], obj: dict[str, Any] | None) -> float | None:
    """Resolve material thickness: explicit ``check['thickness']`` or the owning
    object's smallest bounding-box dimension."""

    if check.get("thickness") is not None:
        return float(check["thickness"])
    if obj is not None:
        size = obj.get("bbox", {}).get("size")
        if size:
            return float(min(size))
    return None


def _limit(rule: dict[str, Any], thickness: float | None) -> float | None:
    """Resolve a rule's limit: absolute ``min`` or ``factor * thickness``."""

    if rule.get("min") is not None:
        return float(rule["min"])
    if thickness is None:
        return None
    factor = rule.get("factor", DEFAULT_FACTORS.get(rule["rule"], 1.0))
    return float(factor) * thickness


def _edge_clearance(feature: dict[str, Any], obj: dict[str, Any]) -> float | None:
    """Smallest in-plane gap from a feature's edge to the owning object's edge.

    The in-plane axes are the two that are not the part's thickness axis (derived
    from the owning object's bounding box, not the feature's own axis — a detected
    slot's ``axis`` is its elongation direction, not the through axis). The
    feature's half-extent along each in-plane axis is subtracted, so a slot is
    measured by its length along its long axis and its width across it.
    """

    bbox = obj.get("bbox", {})
    bbox_min = bbox.get("min")
    bbox_max = bbox.get("max")
    center = feature.get("center")
    thickness_axis = _thickness_axis(obj)
    if not bbox_min or not bbox_max or not center or thickness_axis is None:
        return None
    clearances = [
        min(center[axis] - bbox_min[axis], bbox_max[axis] - center[axis]) - _half_extent_along(feature, axis)
        for axis in range(3)
        if axis != thickness_axis
    ]
    return min(clearances) if clearances else None


def _pair_gap(feature_a: dict[str, Any], feature_b: dict[str, Any]) -> float | None:
    """Edge-to-edge gap (the web) between two features' centers."""

    center_a = feature_a.get("center")
    center_b = feature_b.get("center")
    if not center_a or not center_b:
        return None
    distance = sqrt(sum((a - b) ** 2 for a, b in zip(center_a, center_b)))
    return distance - _half_extent(feature_a) - _half_extent(feature_b)


def _point_segment_distance(point: list[float], start: list[float], end: list[float]) -> float:
    """Shortest distance from a 2D point to a 2D segment."""

    px, py = point[0], point[1]
    ax, ay = start[0], start[1]
    bx, by = end[0], end[1]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return sqrt((px - ax) ** 2 + (py - ay) ** 2)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return sqrt((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2)


def _violation(rule: str, features: list[str], observed: float, limit: float) -> dict[str, Any]:
    return {"rule": rule, "features": features, "observed": observed, "limit": limit}


def _rule_min_size(rule, features, index, check, kind, prop):
    """min_hole_diameter / min_slot_width: a feature's size below the limit."""

    violations = []
    for feature in features:
        if feature.get("kind") != kind or feature.get(prop) is None:
            continue
        limit = _limit(rule, _resolve_thickness(check, _owning_object(index, feature)))
        if limit is None:
            continue
        observed = float(feature[prop])
        if observed < limit:
            violations.append(_violation(rule["rule"], [feature["id"]], observed, limit))
    return violations


def _rule_hole_to_edge(rule, features, index, check):
    """A hole/slot closer to an edge than the limit."""

    violations = []
    for feature in features:
        if feature.get("kind") not in _EDGE_KINDS:
            continue
        obj = _owning_object(index, feature)
        if obj is None:
            continue
        clearance = _edge_clearance(feature, obj)
        limit = _limit(rule, _resolve_thickness(check, obj))
        if clearance is None or limit is None:
            continue
        if clearance < limit:
            violations.append(_violation(rule["rule"], [feature["id"]], clearance, limit))
    return violations


def _rule_min_web(rule, features, index, check):
    """Two features on the same object whose edges are closer than the limit."""

    relevant = [feature for feature in features if feature.get("kind") in _EDGE_KINDS]
    violations = []
    for i in range(len(relevant)):
        for j in range(i + 1, len(relevant)):
            feature_a, feature_b = relevant[i], relevant[j]
            source_a = feature_a.get("source_object")
            # Only pair features that share a named owning object; two unsourced
            # features (None == None) are not necessarily on the same part.
            if source_a is None or source_a != feature_b.get("source_object"):
                continue
            gap = _pair_gap(feature_a, feature_b)
            limit = _limit(rule, _resolve_thickness(check, _owning_object(index, feature_a)))
            if gap is None or limit is None:
                continue
            if gap < limit:
                violations.append(_violation(rule["rule"], [feature_a["id"], feature_b["id"]], gap, limit))
    return violations


def _rule_min_bend_radius(rule, features, index, check):
    """An explicitly published bend feature with an inside radius below the limit."""

    violations = []
    for feature in features:
        if feature.get("kind") != "bend":
            continue
        observed = feature.get("inside_radius", feature.get("radius"))
        if observed is None:
            continue
        limit = _limit(rule, _resolve_thickness(check, _owning_object(index, feature)))
        if limit is None:
            continue
        if float(observed) < limit:
            violations.append(_violation(rule["rule"], [feature["id"]], float(observed), limit))
    return violations


def _rule_hole_to_bend(rule, features, index, check):
    """A hole too close to a bend line on the same object.

    Operates on explicitly published ``bend`` features that carry a 2D ``line``
    (``[[x0, y0], [x1, y1]]``); the in-plane distance from the hole edge to that
    line must clear the limit.
    """

    bends = [feature for feature in features if feature.get("kind") == "bend" and feature.get("line")]
    holes = [feature for feature in features if feature.get("kind") in _CYLINDRICAL]
    violations = []
    for hole in holes:
        center = hole.get("center")
        if not center:
            continue
        for bend in bends:
            if hole.get("source_object") != bend.get("source_object"):
                continue
            line = bend["line"]
            distance = _point_segment_distance(center, line[0], line[1]) - _half_extent(hole)
            limit = _limit(rule, _resolve_thickness(check, _owning_object(index, hole)))
            if limit is None:
                continue
            if distance < limit:
                violations.append(_violation(rule["rule"], [hole["id"], bend["id"]], distance, limit))
    return violations


_RULES = {
    "min_hole_diameter": lambda rule, features, index, check: _rule_min_size(
        rule, features, index, check, "cylindrical_hole", "diameter"
    ),
    "min_slot_width": lambda rule, features, index, check: _rule_min_size(
        rule, features, index, check, "slot", "width"
    ),
    "hole_to_edge": _rule_hole_to_edge,
    "min_web": _rule_min_web,
    "min_bend_radius": _rule_min_bend_radius,
    "hole_to_bend": _rule_hole_to_bend,
}


def evaluate_manufacturability(spatial: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a ``manufacturability`` requirement against spatial features.

    Optional ``object``/``kind`` filter the features considered. Each rule entry
    (`{rule, min?, factor?, severity?}`) produces zero or more violations; a
    ``severity: warn`` rule surfaces in ``warnings`` without failing the check.
    """

    index = _object_index(spatial)
    object_filter = check.get("object")
    kind_filter = check.get("kind")
    selected = [
        feature
        for feature in spatial.get("features", [])
        if (object_filter is None or feature.get("source_object") == object_filter)
        and (kind_filter is None or feature.get("kind") == kind_filter)
    ]

    # The reported scalar thickness resolves against the check's object when
    # given, else the first owning object encountered (per-feature thickness is
    # still resolved per owning object inside each rule).
    reported_obj = None
    if isinstance(object_filter, str) and object_filter.startswith("obj."):
        reported_obj = index.get(object_filter.split(".", 1)[1])
    if reported_obj is None:
        for feature in selected:
            owner = _owning_object(index, feature)
            if owner is not None:
                reported_obj = owner
                break

    violations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for rule in check.get("rules", []):
        evaluator = _RULES.get(rule["rule"])
        if evaluator is None:
            continue
        severity = rule.get("severity", "fail")
        for found in evaluator(rule, selected, index, check):
            found["severity"] = severity
            (warnings if severity == "warn" else violations).append(found)

    return {
        "id": check["id"],
        "type": "manufacturability",
        "status": "fail" if violations else "pass",
        "material": check.get("material"),
        "thickness": _resolve_thickness(check, reported_obj),
        "violations": violations,
        "warnings": warnings,
    }
