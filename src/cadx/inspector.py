"""Spatial inspection for run artifacts.

The first inspector consumes the normalized publications captured during
``cadx run``. Future versions can augment this with automatic build123d
topology detection, but explicit publications provide the stable feature IDs an
agent needs immediately.
"""

from __future__ import annotations

from math import sqrt
from pathlib import Path
from typing import Any

from cadx.files import read_json, write_json


# Maximum size and position mismatch, in model units (mm), for an explicit
# publication and a detected feature to be treated as the same physical
# feature. Tight enough that two real features are never merged, loose enough
# to absorb STEP round-trip noise. Publications further off than this keep
# double-counting on purpose: that discrepancy is signal for the agent.
_DEDUP_TOLERANCE = 0.05

# Size properties compared during deduplication when both sides publish them.
_DEDUP_SIZE_PROPERTIES = ("diameter", "width", "length")

# Kinds whose published "center" may legitimately sit anywhere along the
# feature axis, so matching measures radial distance to the detected axis
# line instead of point-to-point distance.
_AXIAL_KINDS = {"cylindrical_hole", "cylindrical_boss"}

# Radius tolerance (mm) for recognising a folded-solid re-detection of an
# authored sheet-metal hole (D-029, ADR 0051). A folded sheet blank's only
# full-cylinder faces are its authored bores, so a size match on the owning sheet
# part is a reliable duplicate signal without any cross-frame geometry. Kept in
# the same 0.05 mm band as _DEDUP_TOLERANCE so the two matchers agree on "same
# size" and neither ever merges two physically distinct features.
_SHEET_HOLE_RADIUS_TOLERANCE = 0.05

# Roles that are reference/keep-out geometry rather than physical parts, so they
# do not contribute to the assembly's aggregate mass. Every other role
# (including the idiomatic "part" and "final") is a physical part.
_NON_PHYSICAL_ROLES = {"fixture", "reference", "datum", "keepout"}


def _with_bbox_size(obj: dict[str, Any]) -> dict[str, Any]:
    """Ensure every object bbox includes a ``size`` vector."""

    bbox = obj.setdefault("bbox", {})
    if "size" not in bbox and "min" in bbox and "max" in bbox:
        bbox["size"] = [max_v - min_v for min_v, max_v in zip(bbox["min"], bbox["max"])]
    return obj


def _vector_from(value: Any) -> list[float]:
    """Convert build123d vector-like values to JSON-safe coordinates."""

    if isinstance(value, (list, tuple)):
        return [float(value[0]), float(value[1]), float(value[2])]
    return [float(value.X), float(value.Y), float(value.Z)]


def _bbox_dict(raw: Any) -> dict[str, list[float]]:
    """Normalize a build123d bounding box object."""

    min_value = raw.min() if callable(getattr(raw, "min", None)) else raw.min
    max_value = raw.max() if callable(getattr(raw, "max", None)) else raw.max
    minimum = _vector_from(min_value)
    maximum = _vector_from(max_value)
    return {
        "min": minimum,
        "max": maximum,
        "size": [max_v - min_v for min_v, max_v in zip(minimum, maximum)],
    }


def _resolve_export_path(run_dir: Path, export_path: str) -> Path:
    """Resolve export paths saved by earlier run versions."""

    path = Path(export_path)
    if path.exists():
        return path
    if path.is_absolute():
        return path
    return run_dir / path.name


def _step_exports(diagnostics: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    """Return STEP export records with paths usable from the current process.

    The combined assembly export (ADR 0023) is excluded: feature detection
    must run once per part, not a second time over the same geometry under a
    bogus ``obj.assembly`` source object.
    """

    return [
        {**export, "path": str(_resolve_export_path(run_dir, export["path"]))}
        for export in diagnostics.get("exports", [])
        if export.get("format") == "step" and not export.get("assembly")
    ]


def _dominant_axis(direction: list[float]) -> int:
    """Return the index of the axis most aligned with a direction vector."""

    return max(range(3), key=lambda index: abs(direction[index]))


def _center_from_bbox(bbox: dict[str, list[float]]) -> list[float]:
    """Return a bounding-box center point."""

    return [
        (min_value + max_value) / 2
        for min_value, max_value in zip(bbox["min"], bbox["max"])
    ]


def _is_close(left: float, right: float, tolerance: float = 1e-5) -> bool:
    """Small absolute comparison helper for topology-derived values."""

    return abs(left - right) <= tolerance


def _is_full_cylindrical_face(face_bbox: dict[str, list[float]], radius: float, axis_index: int) -> bool:
    """Identify full cylindrical faces versus partial slot-end cylinders."""

    diameter = radius * 2
    perpendicular_axes = [axis for axis in range(3) if axis != axis_index]
    return all(_is_close(face_bbox["size"][axis], diameter, 1e-4) for axis in perpendicular_axes)


def _plane_feature(face: Any, label: str, index: int) -> dict[str, Any]:
    """Create a planar datum feature from a planar face."""

    bbox = _bbox_dict(face.bounding_box())
    try:
        normal = _vector_from(face.normal_at())
    except Exception:
        normal = [0.0, 0.0, 0.0]
    return {
        "id": f"feat.auto_{label}_planar_datum_{index}",
        "kind": "planar_datum",
        "source_object": f"obj.{label}",
        "center": _center_from_bbox(bbox),
        "normal": normal,
        "area": float(face.area),
        "bbox": bbox,
        "detected": True,
    }


def _axes_parallel(axis_a: list[float], axis_b: list[float]) -> bool:
    """True when two direction vectors point along the same line (either sense)."""

    dot = sum(a * b for a, b in zip(axis_a, axis_b))
    norm_a = sqrt(sum(a * a for a in axis_a))
    norm_b = sqrt(sum(b * b for b in axis_b))
    if norm_a == 0 or norm_b == 0:
        return False
    return abs(dot) >= norm_a * norm_b * (1 - 1e-4)


def _same_axis_line(cyl_a: dict[str, Any], cyl_b: dict[str, Any]) -> bool:
    """True when two partial cylinders share one axis of rotation LINE.

    Not merely a parallel direction: the two axis positions must also lie on the
    same line, i.e. the vector between them is parallel to the (shared) direction
    (its perpendicular component is ~0). This is the geometric signature of the
    inner and outer faces of one bent ribbon, which are concentric about a common
    bend centre — a real obround slot's two ends sit on parallel but DISPLACED
    axes, so they never share a line.
    """

    if not _axes_parallel(cyl_a["axis"], cyl_b["axis"]):
        return False
    axis = cyl_a["axis"]
    norm = sqrt(sum(component * component for component in axis))
    if norm == 0:
        return False
    unit = [component / norm for component in axis]
    delta = [pa - pb for pa, pb in zip(cyl_a["axis_pos"], cyl_b["axis_pos"])]
    along = sum(d * u for d, u in zip(delta, unit))
    perpendicular_squared = sum(d * d for d in delta) - along * along
    return sqrt(max(perpendicular_squared, 0.0)) <= 1e-3


def _bend_arc_flags(partial_cylinders: list[dict[str, Any]], bend_radii: list[float]) -> list[bool]:
    """Mark partial cylinders that are bend-region arcs, not slot ends (D-023).

    A folded bend region (ADR 0034) is a swept annular sector whose inner face
    (radius ``rho - t/2``) and outer face (``rho + t/2``) are two partial cylinders
    that are **concentric** (share one axis line, :func:`_same_axis_line`) but have
    **different radii**. A real obround slot's two ends have EQUAL radii on
    displaced axes, so they never form such a pair. A partial cylinder is therefore
    a bend arc when some other partial cylinder is concentric with it at a
    different radius.

    The pair is additionally corroborated against the part's published bend table:
    with the measured wall thickness ``t = |r_outer - r_inner|`` (the arc pair spans
    exactly one wall), the inner radius must fall within ``0.5*t`` of some bend's
    ``inside_radius`` (because ``r_inner = inside_radius + (k - 0.5)*t`` for a
    k-factor in ``[0, 1]``). This ties suppression to the bend features and derives
    thickness from the geometry itself, so no external thickness is needed here.
    """

    flags = [False] * len(partial_cylinders)
    for i, cyl in enumerate(partial_cylinders):
        for j, other in enumerate(partial_cylinders):
            if i == j:
                continue
            radius_gap = abs(cyl["radius"] - other["radius"])
            if radius_gap <= 1e-3:
                continue  # Equal radius -> a slot-end partner, not inner/outer.
            if not _same_axis_line(cyl, other):
                continue  # Not concentric -> not one bent ribbon's two faces.
            inner_radius = min(cyl["radius"], other["radius"])
            if any(abs(bend_radius - inner_radius) <= 0.5 * radius_gap + 1e-3 for bend_radius in bend_radii):
                flags[i] = True
                break
    return flags


def _slot_features(partial_cylinders: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    """Detect simple obround slots from paired partial cylindrical end faces."""

    slots: list[dict[str, Any]] = []
    used: set[int] = set()
    for left_index, left in enumerate(partial_cylinders):
        if left_index in used:
            continue
        for right_index, right in enumerate(partial_cylinders[left_index + 1 :], start=left_index + 1):
            if right_index in used:
                continue
            if not _is_close(left["radius"], right["radius"], 1e-4):
                continue
            if left["axis_index"] != right["axis_index"] or not left["through"] or not right["through"]:
                continue

            delta = [right["center"][axis] - left["center"][axis] for axis in range(3)]
            slot_axis = _dominant_axis(delta)
            stable_axes = [axis for axis in range(3) if axis not in {left["axis_index"], slot_axis}]
            if any(not _is_close(left["center"][axis], right["center"][axis], 1e-4) for axis in stable_axes):
                continue

            length = abs(delta[slot_axis])
            slots.append(
                {
                    "kind": "slot",
                    "source_object": f"obj.{label}",
                    "center": [
                        (left["center"][axis] + right["center"][axis]) / 2
                        for axis in range(3)
                    ],
                    "axis": [
                        1.0 if axis == slot_axis and delta[axis] >= 0 else -1.0 if axis == slot_axis else 0.0
                        for axis in range(3)
                    ],
                    "width": left["radius"] * 2,
                    "length": length,
                    "through": True,
                    "detected": True,
                }
            )
            used.update({left_index, right_index})
            break

    for index, feature in enumerate(slots, start=1):
        feature["id"] = f"feat.auto_{label}_slot_{index}"
    return slots


def _detected_topology_features(
    shape: Any, label: str, bend_radii: list[float] | None = None
) -> list[dict[str, Any]]:
    """Detect planar datums, cylindrical holes, bosses, and simple slots.

    ``bend_radii`` is the list of published bend ``inside_radius`` values for this
    part (empty/None for a non-sheet-metal part). When present, partial-cylinder
    faces that are folded bend arcs are excluded from obround-slot detection so the
    bend regions do not masquerade as phantom slots (D-023, ADR 0044).
    """

    object_bbox = _bbox_dict(shape.bounding_box())
    detected: list[dict[str, Any]] = []
    partial_cylinders: list[dict[str, Any]] = []
    plane_index = 1

    for face in shape.faces():
        geom_type = str(getattr(face, "geom_type", ""))
        if geom_type.endswith("PLANE"):
            detected.append(_plane_feature(face, label, plane_index))
            plane_index += 1
            continue
        if not geom_type.endswith("CYLINDER"):
            continue

        face_bbox = _bbox_dict(face.bounding_box())
        axis = _vector_from(face.axis_of_rotation.direction)
        axis_index = _dominant_axis(axis)
        through = face_bbox["size"][axis_index] >= object_bbox["size"][axis_index] - 1e-5
        radius = float(face.radius)
        full_cylinder = _is_full_cylindrical_face(face_bbox, radius, axis_index)
        center = _center_from_bbox(face_bbox) if full_cylinder else _vector_from(face.center())
        if full_cylinder:
            kind = "cylindrical_hole" if through else "cylindrical_boss"
            feature = {
                "kind": kind,
                "source_object": f"obj.{label}",
                "center": center,
                "axis": axis,
                "diameter": radius * 2,
                "through": through,
                "detected": True,
            }
            if kind == "cylindrical_boss":
                feature["height"] = face_bbox["size"][axis_index]
            detected.append(feature)
        else:
            partial_cylinders.append(
                {
                    "center": center,
                    "axis": axis,
                    # Position of a point on the axis of rotation LINE, used to tell
                    # concentric bend-arc faces (same line) from displaced slot ends.
                    "axis_pos": _vector_from(face.axis_of_rotation.position),
                    "axis_index": axis_index,
                    "radius": radius,
                    "through": face_bbox["size"][axis_index] > 1e-5,
                }
            )

    # On a sheet-metal part, drop the bend-region arcs before slot pairing so the
    # folded bends do not detect as phantom obround slots (D-023). A flat part has
    # no bend features (empty ``bend_radii``), so every partial cylinder survives.
    slot_candidates = partial_cylinders
    if bend_radii:
        arc_flags = _bend_arc_flags(partial_cylinders, bend_radii)
        slot_candidates = [cyl for cyl, is_arc in zip(partial_cylinders, arc_flags) if not is_arc]
    detected.extend(_slot_features(slot_candidates, label))
    id_counters: dict[str, int] = {}
    for feature in detected:
        id_counters[feature["kind"]] = id_counters.get(feature["kind"], 0) + 1
        feature.setdefault("id", f"feat.auto_{label}_{feature['kind']}_{id_counters[feature['kind']]}")
    return detected


def _auto_detect_features(
    diagnostics: dict[str, Any], run_dir: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load STEP exports and return detected features plus ingestion warnings.

    Each export is guarded individually: a corrupt or truncated STEP file must
    degrade detection for that one object, not abort the whole inspection (and,
    since the worker inspects inside its main try-block, not convert a
    successful build into a failed run). ``import_step`` on garbage can either
    raise or quietly return an *empty* shape depending on the OCCT version, so
    an import that yields no faces is treated as the same failure.
    """

    try:
        from build123d import import_step
    except Exception:
        return [], []

    # Map each part label to its published bend inside-radii (ADR 0033 emits a
    # kind="bend" feature per bend). A part with bends is a sheet-metal part whose
    # folded bend arcs must be kept out of obround-slot detection (D-023).
    bend_radii_by_label: dict[str, list[float]] = {}
    for feature in diagnostics.get("features", []):
        if feature.get("kind") != "bend":
            continue
        source = feature.get("source_object")
        if not isinstance(source, str) or not source.startswith("obj."):
            continue
        radius = feature.get("inside_radius", feature.get("radius"))
        if radius is None:
            continue
        bend_radii_by_label.setdefault(source.split(".", 1)[1], []).append(float(radius))

    detected: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for export in _step_exports(diagnostics, run_dir):
        try:
            shape = import_step(export["path"])
            if not list(shape.faces()):
                raise ValueError("STEP import produced no geometry")
            label = export.get("label", "object")
            detected.extend(
                _detected_topology_features(shape, label, bend_radii_by_label.get(label))
            )
        except Exception as exc:
            warnings.append(
                {
                    "type": "feature_detection_failed",
                    "label": export.get("label"),
                    "path": export["path"],
                    "message": str(exc),
                }
            )
    return detected, warnings


def _coerce_point(value: Any) -> list[float] | None:
    """Return a 3-component float point, or ``None`` when malformed.

    Explicit ``publish_feature`` properties are arbitrary user data, so the
    matcher validates instead of assuming the spatial schema.
    """

    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return [float(component) for component in value]
    except (TypeError, ValueError):
        return None


def _sizes_match(explicit: dict[str, Any], detected: dict[str, Any]) -> bool:
    """Compare size properties present on both features within tolerance."""

    for name in _DEDUP_SIZE_PROPERTIES:
        if name in explicit and name in detected:
            try:
                mismatch = abs(float(explicit[name]) - float(detected[name]))
            except (TypeError, ValueError):
                return False
            if mismatch > _DEDUP_TOLERANCE:
                return False
    return True


def _center_distance(explicit_center: list[float], detected: dict[str, Any]) -> float:
    """Distance from an explicit center to the detected feature's location.

    Cylindrical features are identified by their axis line: the component of
    the offset along the detected axis is discarded because publications may
    place the "center" at either face or at mid-depth. Other kinds compare
    plain point-to-point distance.
    """

    detected_center = detected["center"]
    delta = [explicit_c - detected_c for explicit_c, detected_c in zip(explicit_center, detected_center)]
    axis = _coerce_point(detected.get("axis")) if detected.get("kind") in _AXIAL_KINDS else None
    if axis is not None:
        length = sqrt(sum(component * component for component in axis))
        if length > 0:
            unit = [component / length for component in axis]
            axial = sum(d * a for d, a in zip(delta, unit))
            radial_squared = sum(d * d for d in delta) - axial * axial
            return sqrt(max(radial_squared, 0.0))
    return sqrt(sum(d * d for d in delta))


def _is_duplicate(explicit: dict[str, Any], detected: dict[str, Any]) -> bool:
    """Decide whether a detected feature re-observes an explicit publication."""

    if explicit.get("kind") != detected.get("kind"):
        return False
    # Features that name different source objects are physically distinct parts'
    # features and must never be merged, even when coaxial. Two plates stacked
    # for bolting (ADR 0014) have aligned holes whose axes coincide, so the
    # axis-line matching below would otherwise collapse one part's holes into the
    # other's. An explicit publication without a source_object is unconstrained
    # here so ADR 0012's explicit/detected corroboration still applies.
    explicit_source = explicit.get("source_object")
    detected_source = detected.get("source_object")
    if explicit_source is not None and detected_source is not None and explicit_source != detected_source:
        return False
    explicit_center = _coerce_point(explicit.get("center"))
    detected_center = _coerce_point(detected.get("center"))
    if explicit_center is None or detected_center is None:
        return False
    if not _sizes_match(explicit, detected):
        return False
    return _center_distance(explicit_center, detected) <= _DEDUP_TOLERANCE


def _authored_sheet_hole_radii(
    explicit_features: list[dict[str, Any]],
) -> dict[str, list[float]]:
    """Map each folded sheet part to the radii of its authored holes (D-029).

    A source object is a folded sheet-metal part when it carries at least one
    published ``kind="bend"`` feature (ADR 0033). Its authored fastener holes are
    the ``kind="cylindrical_hole"`` publications on the same object, emitted in
    the FLAT-pattern frame by the ADR 0040 ``holes=`` API. Returning their radii
    lets :func:`_is_sheet_hole_redetection` recognise a folded-frame re-detection
    of the same bore. A part with bends but no authored holes, or holes but no
    bends, yields no entry — both gates must hold for suppression to fire.
    """

    sheet_sources = {
        feature.get("source_object")
        for feature in explicit_features
        if feature.get("kind") == "bend" and isinstance(feature.get("source_object"), str)
    }
    radii: dict[str, list[float]] = {}
    for feature in explicit_features:
        if feature.get("kind") != "cylindrical_hole":
            continue
        source = feature.get("source_object")
        if source not in sheet_sources:
            continue
        diameter = feature.get("diameter")
        if diameter is None:
            continue
        try:
            radii.setdefault(source, []).append(float(diameter) / 2.0)
        except (TypeError, ValueError):
            continue
    return radii


def _is_sheet_hole_redetection(
    detected: dict[str, Any], authored_radii: dict[str, list[float]]
) -> bool:
    """True when a detected cylinder re-observes an authored sheet-metal hole.

    D-029: an authored hole (ADR 0040 ``holes=`` API) is published in the
    flat-pattern frame but is also bored out of the folded solid, so STEP
    auto-detection re-observes it in the FOLDED frame — usually as a
    ``cylindrical_boss`` (its through-axis is the thin sheet thickness, far
    shorter than the part's longest extent, so the "through" test fails). That
    re-detection cannot deduplicate against the flat publication (different frame,
    often a different kind), and it false-positives ``hole_to_bend`` /
    ``hole_to_edge``.

    The discriminator is deliberately a radius match on the owning sheet part, not
    a cross-frame axis-line match: the flat and folded axes of an angled-flange
    hole genuinely differ, and reconstructing the fold transform would need the
    sheet-metal authoring data that lives outside the inspector. It is sufficient
    because a folded sheet blank's only full-cylinder faces are its authored
    bores, so a size match on such a part uniquely identifies the duplicate (same
    sheet-metal-gated reasoning ADR 0044 uses to suppress bend arcs).
    """

    if not detected.get("detected"):
        return False
    if detected.get("kind") not in _AXIAL_KINDS:  # cylindrical_hole / cylindrical_boss
        return False
    radii = authored_radii.get(detected.get("source_object"))
    if not radii:
        return False
    diameter = detected.get("diameter")
    if diameter is None:
        return False
    try:
        radius = float(diameter) / 2.0
    except (TypeError, ValueError):
        return False
    return any(abs(radius - authored) <= _SHEET_HOLE_RADIUS_TOLERANCE for authored in radii)


def _confirm_authored_hole(merged: list[dict[str, Any]], detected: dict[str, Any]) -> None:
    """Mark the authored sheet hole that a suppressed re-detection corroborates.

    Emits the same ``confirmed_by_detection`` signal the ADR 0012 dedup emits, so
    an agent still learns the authored hole reached the solid even though the
    re-detection itself is dropped. Picks the first not-yet-confirmed authored hole
    of matching radius on the same source, so several equal-diameter holes are each
    confirmed at most once.
    """

    source = detected.get("source_object")
    try:
        radius = float(detected["diameter"]) / 2.0
    except (KeyError, TypeError, ValueError):
        return
    for feature in merged:
        if feature.get("kind") != "cylindrical_hole" or feature.get("detected"):
            continue
        if feature.get("source_object") != source or feature.get("confirmed_by_detection"):
            continue
        diameter = feature.get("diameter")
        if diameter is None:
            continue
        try:
            authored = float(diameter) / 2.0
        except (TypeError, ValueError):
            continue
        if abs(authored - radius) <= _SHEET_HOLE_RADIUS_TOLERANCE:
            feature["confirmed_by_detection"] = True
            return


def _merge_features(
    explicit_features: list[dict[str, Any]],
    detected_features: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combine both feature channels without double-counting geometry.

    Explicit publications are the source of truth and always survive with
    their stable ids. A detected feature that matches one of them is dropped
    and the publication is marked ``confirmed_by_detection`` so agents can
    distinguish corroborated publications from unverified intent. Unmatched
    features from either channel pass through unchanged.

    ADR 0051 (D-029): a detected cylindrical feature that re-observes an authored
    sheet-metal hole (see :func:`_is_sheet_hole_redetection`) is dropped even when
    it does not match the flat publication point-to-point, because it lives in the
    folded frame and would false-positive the sheet DFM rules. The corroborated
    authored hole is still marked ``confirmed_by_detection``. This is gated on the
    part being a folded sheet part that carries authored holes, so non-sheet
    detection and the ADR 0012 dedup are unchanged.
    """

    merged = [dict(feature) for feature in explicit_features]
    authored_radii = _authored_sheet_hole_radii(explicit_features)
    for detected in detected_features:
        match = next((feature for feature in merged if _is_duplicate(feature, detected)), None)
        if match is not None:
            match["confirmed_by_detection"] = True
            continue
        if _is_sheet_hole_redetection(detected, authored_radii):
            # Drop the folded-frame duplicate; corroborate the authored hole.
            _confirm_authored_hole(merged, detected)
            continue
        merged.append(detected)
    return merged


def _coerce_matrix(value: Any) -> list[list[float]] | None:
    """Return a 3x3 float matrix, or ``None`` when malformed.

    Guards the assembly-inertia aggregation (ADR 0036) against a part whose
    ``matrix_of_inertia`` is absent or the wrong shape: such a part cannot
    contribute its spin term, so the aggregate must decline rather than
    silently under-report.
    """

    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    matrix: list[list[float]] = []
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            return None
        try:
            matrix.append([float(component) for component in row])
        except (TypeError, ValueError):
            return None
    return matrix


def _aggregate_inertia(
    parts: list[tuple[float, list[float], float | None, list[list[float]]]],
    center_of_mass: list[float],
    all_have_density: bool,
) -> list[list[float]]:
    """Compose an aggregate inertia tensor about the assembly center of mass.

    Each part carries a unit-density geometric tensor ``G`` (mm^5, about its own
    centroid, world axes; ADR 0015 computes it on the *placed* object so no
    body-frame rotation is hidden). Because every ``G`` is already in world axes,
    aggregation needs no rotation — only density scaling and a parallel-axis
    *translation*:

        I  =  sum_i [ w_i * G_i  +  m_i * ( |d_i|^2 * E3  -  d_i (x) d_i ) ]

    with ``d_i = c_i - com``. When every part has a density (``all_have_density``)
    the weights are physical (``w_i = rho_i``, ``m_i = rho_i * V_i``) and the
    result is a mass moment in g*mm^2; otherwise the volume itself is the weight
    (``w_i = 1``, ``m_i = V_i``) and the result is a geometric second moment in
    mm^5 — the inertial analogue of the volume-weighted center of mass, so the
    two degrade together (see ADR 0036).
    """

    tensor = [[0.0, 0.0, 0.0] for _ in range(3)]
    for volume, center, density, geometric in parts:
        if all_have_density:
            # density is guaranteed non-None in this branch (all_have_density).
            weight = float(density)  # type: ignore[arg-type]
            point_mass = weight * volume
        else:
            weight = 1.0
            point_mass = volume

        offset = [center[axis] - center_of_mass[axis] for axis in range(3)]
        d_squared = sum(component * component for component in offset)
        for i in range(3):
            for j in range(3):
                # Scaled own-centroid spin term plus the parallel-axis transfer.
                identity = 1.0 if i == j else 0.0
                transfer = point_mass * (d_squared * identity - offset[i] * offset[j])
                tensor[i][j] += weight * geometric[i][j] + transfer
    return tensor


def _assembly_center_of_mass(
    objects: list[dict[str, Any]], include_roles: list[str] | None = None
) -> dict[str, Any] | None:
    """Mass-weighted center of mass (and inertia) across published part objects.

    Each contributing part needs a positive ``mass_properties.volume`` and a
    3-vector ``mass_properties.center_of_mass``. A part's weight is
    ``density * volume`` when a positive ``metadata.density`` is present, else its
    volume (a uniform-density approximation). Weighting is reported as ``"mass"``
    only when *every* contributing part supplied a positive density, otherwise
    ``"volume"`` — so a uniform-density guess is never silently presented as a
    true mass center. ADR 0035's material table supplies real densities; until
    then this degrades gracefully. Returns ``None`` when no part qualifies.

    ADR 0036 adds an aggregate ``inertia`` tensor about the assembly center of
    mass, composed by :func:`_aggregate_inertia` from the same qualifying parts
    so the center of mass and the inertia are always mutually consistent. The
    ``inertia`` block is emitted only when *every* qualifying part exposes a valid
    3x3 ``matrix_of_inertia`` — otherwise the aggregate would omit a part's spin
    term and under-report, so it is dropped rather than published wrong.

    ADR 0046 (deficiency D-026) makes the aggregate self-describing and
    overridable. ``include_roles`` names roles that are normally non-physical
    (``_NON_PHYSICAL_ROLES``) but should be counted for this run — an opted-in
    ``fixture`` then flows through the *same* qualifying list, so mass, center of
    mass, and inertia all reflect it consistently. The returned record gains
    ``included_roles`` (sorted distinct roles that contributed) and ``excluded``
    (one ``{label, role}`` per object dropped *by the role filter*, in publication
    order); a part skipped for missing volume/centroid is a data skip, not a role
    exclusion, so it is deliberately absent from ``excluded``.
    """

    # Roles the caller opted back in (ADR 0046): the still-excluded set is the
    # default non-physical set minus anything explicitly requested. A role not in
    # the non-physical set (e.g. "part") is a harmless no-op here.
    opted_in = set(include_roles or ())
    excluded_roles = _NON_PHYSICAL_ROLES - opted_in

    # First pass: collect qualifying parts with their volume, centroid, any
    # positive density, and their (optional) geometric inertia tensor. Track the
    # roles that contributed and the objects the role filter dropped, so the
    # aggregate can describe itself.
    qualifying: list[tuple[float, list[float], float | None, list[list[float]] | None]] = []
    included_role_set: set[str] = set()
    excluded: list[dict[str, Any]] = []
    for obj in objects:
        # Aggregate every physical part. The primary part is idiomatically
        # published as role="final" (the starter design and most assemblies do),
        # so excluding all non-"part" roles would silently drop the heaviest part
        # from the assembly center of mass and skew a stability/load-cell check.
        role = obj.get("role", "part")
        if role in excluded_roles:
            # A role exclusion is a deliberate, reportable drop (D-026).
            excluded.append({"label": obj.get("label"), "role": role})
            continue
        mass_properties = obj.get("mass_properties", {})
        volume = mass_properties.get("volume")
        center = _coerce_point(mass_properties.get("center_of_mass"))
        if not isinstance(volume, (int, float)) or volume <= 0 or center is None:
            # A data skip (no volume/centroid) is NOT a role exclusion, so it is
            # not recorded in ``excluded``.
            continue
        density = obj.get("metadata", {}).get("density")
        density = float(density) if isinstance(density, (int, float)) and density > 0 else None
        inertia = _coerce_matrix(mass_properties.get("matrix_of_inertia"))
        qualifying.append((float(volume), center, density, inertia))
        included_role_set.add(role)

    if not qualifying:
        return None

    # Mass-weight only when EVERY part supplied a positive density. In any mixed
    # or absent-density case, weight purely by volume so the result is a
    # consistent uniform-density centroid rather than a unit-inconsistent hybrid
    # of mass weights and volume weights.
    all_have_density = all(density is not None for _, _, density, _ in qualifying)
    contributions = [
        (volume * density if all_have_density else volume, center)
        for volume, center, density, _ in qualifying
    ]
    total = sum(weight for weight, _ in contributions)
    if total <= 0:
        return None
    center_of_mass = [
        sum(weight * center[axis] for weight, center in contributions) / total
        for axis in range(3)
    ]
    assembly: dict[str, Any] = {
        "center_of_mass": center_of_mass,
        "mass": total,
        "weighting": "mass" if all_have_density else "volume",
        # part_count is the number of INCLUDED contributing parts (ADR 0046 makes
        # this unambiguous alongside included_roles/excluded).
        "part_count": len(contributions),
        # ADR 0046 (D-026): make the aggregate self-describing. included_roles is
        # the sorted distinct roles that actually contributed; excluded lists every
        # object the role filter dropped ({label, role}), so a stability/CoM check
        # can see at a glance what mass it is (and is not) validating.
        "included_roles": sorted(included_role_set),
        "excluded": excluded,
    }

    # ADR 0036: aggregate inertia only when every qualifying part carries a valid
    # 3x3 tensor, so the published tensor is always a complete sum.
    if all(inertia is not None for _, _, _, inertia in qualifying):
        parts = [
            (volume, center, density, inertia)
            for volume, center, density, inertia in qualifying
        ]
        # ADR 0037: make the aggregate self-describing. Its units follow the same
        # weighting decision that produced it — a mass moment in g*mm^2 when every
        # part had a density, else a unit-density geometric second moment in mm^5
        # (the inertial analogue of the volume-weighted center of mass).
        assembly["inertia"] = {
            "tensor": _aggregate_inertia(parts, center_of_mass, all_have_density),
            "units": "g*mm^2" if all_have_density else "mm^5",
            "density": "mass-weighted" if all_have_density else "unit (geometric)",
            "about": "assembly center of mass",
            "axes": "world",
        }
    return assembly


def inspect_run(run_dir: Path) -> dict[str, Any]:
    """Write ``spatial.json`` for a run and return a compact summary."""

    diagnostics = read_json(run_dir / "diagnostics.json")
    objects = [_with_bbox_size(dict(obj)) for obj in diagnostics.get("published", [])]
    detected, detection_warnings = _auto_detect_features(diagnostics, run_dir)
    features = _merge_features(list(diagnostics.get("features", [])), detected)
    spatial = {
        "schema_version": "1.0",
        "units": diagnostics.get("units", "mm"),
        "objects": objects,
        "features": features,
    }
    # Ingestion warnings are agent-visible facts about this run, so they live in
    # spatial.json; the key is additive and only present when something failed.
    if detection_warnings:
        spatial["warnings"] = detection_warnings
    # ADR 0046: run-level assembly options (persisted into diagnostics by the
    # worker) let a design opt a normally non-physical role into the aggregate.
    assembly_opts = diagnostics.get("assembly_options") or {}
    include_roles = assembly_opts.get("include_roles")
    assembly = _assembly_center_of_mass(objects, include_roles=include_roles)
    if assembly is not None:
        spatial["assembly"] = assembly
    write_json(run_dir / "spatial.json", spatial)
    return {
        "status": "ok",
        "spatial_path": str(run_dir / "spatial.json"),
        "objects": len(objects),
        "features": len(features),
    }
