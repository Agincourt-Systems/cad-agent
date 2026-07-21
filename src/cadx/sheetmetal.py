"""Lightweight sheet-metal bend modeling.

build123d ships no native sheet-metal unfolder, and a general BREP unfolder is
unnecessary for the rectilinear brackets this harness targets. This module
models a part as flat flanges joined by explicit bend operations, computing the
developed (flat) length from the material's bend allowance so a shop like
SendCutSend can cut-and-bend a single part instead of bolting flats together.

One ``bend_chain(...)`` description yields both a folded 3D solid (so the existing
clearance / interference / center-of-mass machinery can reason about the part in
its assembled pose) and a flat pattern carrying the bend lines on a dedicated
layer plus a machine-readable bend table for the press-brake operator.

ADR 0016 shipped the single-bend ``bend(flange_a, flange_b, ...)`` helper. ADR
0032 (deficiency D-003) generalises the geometry to an ordered flange/bend
*chain* — U-channels and clevis brackets with two 90-degree bends cut as ONE flat
blank — and re-expresses ``bend`` as a thin wrapper over ``bend_chain`` so the
single-bend API and all its published behaviour are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, cos, degrees, pi, radians, sin
from typing import Any


@dataclass(frozen=True)
class SheetMetalPart:
    """A sheet-metal part: folded solid, flat pattern, and bend table.

    ``folded`` is a build123d ``Part`` suitable for ``publish_sheet_metal``;
    ``flat_profile`` is a build123d ``Sketch`` (the single cut outline, with any
    holes/cutouts subtracted as inner wires); ``bend_lines`` are the build123d
    ``Edge`` objects drawn on the DXF ``bend`` layer (one per bend); ``bends`` is
    the JSON-safe bend table written to ``bends.json`` (one row per bend); and
    ``holes`` is the JSON-safe list of flat-pattern holes/cutouts (ADR 0040) that
    ``publish_sheet_metal`` republishes as ``spatial.json`` features so the DFM
    rules bind. ``holes`` defaults to empty, so a part with no holes is identical
    to a pre-ADR-0040 part.
    """

    developed_length: float
    folded: Any
    flat_profile: Any
    bend_lines: list[Any]
    bends: list[dict[str, Any]]
    holes: list[dict[str, Any]] = field(default_factory=list)


def _bend_allowance(angle_deg: float, inside_radius: float, k_factor: float, thickness: float) -> float:
    """Length of the neutral fiber stretched around one bend.

    ``BA = (pi/180) * angle * (inside_radius + k_factor * thickness)``. The flat
    blank is longer than the sum of the flange lengths by the sum of the bend
    allowances, which is the whole point of the developed-length calculation.
    """

    return (pi / 180.0) * angle_deg * (inside_radius + k_factor * thickness)


def _folded_profile(
    flanges: list[float],
    angles_deg: list[float],
    directions: list[str],
    radii: list[float],
    k_factors: list[float],
    *,
    thickness: float,
    width: float,
    holes: list[dict[str, Any]] | None = None,
) -> Any:
    """Connected, volume-conserving folded solid for any bend chain (ADR 0034).

    The part is modelled as a **constant-thickness ribbon** swept along its neutral
    centreline in the XZ cross-section plane, then extruded by ``width`` along
    ``y``. The centreline is a walk of straight segments (one per flange, of length
    equal to the flange length) joined by circular arcs (one per bend). Each bend
    arc has the neutral radius ``rho = inside_radius + k_factor * thickness`` and
    sweeps the bend angle, turning left for ``up`` and right for ``down`` — so every
    bend region is a true annular sector and the arc's centreline length is exactly
    the bend allowance ``BA = radians(angle) * rho``.

    Why this conserves volume: ``build123d``'s ``trace(line_width=thickness)`` turns
    the centreline into a band of constant thickness, and ``extrude(..., width)``
    sweeps it out. By Pappus's theorem the volume is the ribbon's cross-sectional
    area times the width, and that area is ``thickness * (sum(flange lengths) +
    sum(BA)) == thickness * developed_length``. So the folded volume equals the
    conserved blank volume ``developed_length * thickness * width`` exactly (to
    numerical tolerance), for a single bend and for a chain alike — the ~4.9%
    bend-region deficit of the old sharp-corner model (D-005) is removed.

    Conserving volume keeps the folded straight runs at their flat lengths, so the
    arc offsets each downstream flange by the bend radius rather than collapsing it
    into a sharp corner: the single-bend bounding box becomes
    ``(flange_a + rho + t/2, width, flange_b + rho + t/2)``. The extrude produces a
    single face swept along one direction, hence one connected solid.
    """

    from build123d import BuildLine, BuildSketch, CenterArc, Line, Plane, extrude, trace

    # Walk the neutral centreline in a 2D (u == x, v == z) frame. The base flange
    # runs along +x with its lower fibre at z = 0 (centreline at z = t/2), matching
    # the historical "base sits on z = 0" convention as closely as the rounded model
    # allows. Segments are collected as explicit endpoints / arc parameters and
    # replayed into a single BuildLine so consecutive edges share exact endpoints.
    #
    # ADR 0040: record each flange's straight-run start point and heading so a
    # flange-local hole (flange index + along/across offsets) can be located on
    # the folded mid-surface for a clean boolean subtraction.
    px, pz = 0.0, thickness / 2.0  # current centreline point
    phi = 0.0  # heading angle, CCW from +x toward +z
    segments: list[tuple[str, Any]] = []
    flange_frames: list[tuple[float, float, float]] = []
    count = len(flanges)
    for index, length in enumerate(flanges):
        flange_frames.append((px, pz, phi))  # centreline start + heading of this flange
        heading_x, heading_z = cos(phi), sin(phi)
        end = (px + heading_x * length, pz + heading_z * length)
        segments.append(("line", ((px, pz), end)))
        px, pz = end
        if index >= count - 1:
            continue
        rho = radii[index] + k_factors[index] * thickness
        theta = angles_deg[index]
        if directions[index] == "up":
            # Left turn: arc centre is 90 deg CCW of the heading; signed sweep +theta.
            normal = (-sin(phi), cos(phi))
            sweep = theta
        else:
            # Right turn: arc centre is 90 deg CW of the heading; signed sweep -theta.
            normal = (sin(phi), -cos(phi))
            sweep = -theta
        center = (px + rho * normal[0], pz + rho * normal[1])
        start_angle = degrees(atan2(pz - center[1], px - center[0]))
        segments.append(("arc", (center, rho, start_angle, sweep)))
        # Advance the pen to the arc end and turn the heading.
        end_angle = radians(start_angle + sweep)
        px = center[0] + rho * cos(end_angle)
        pz = center[1] + rho * sin(end_angle)
        phi += radians(sweep)

    # Build the ribbon on the XZ plane so the extrude runs along +y (the width),
    # giving a bounding box of (x-extent, width, z-extent).
    with BuildSketch(Plane.XZ) as sketch:
        with BuildLine():
            for kind, data in segments:
                if kind == "line":
                    start, end = data
                    Line(start, end)
                else:
                    center, rho, start_angle, sweep = data
                    CenterArc(center, rho, start_angle, sweep)
        trace(line_width=thickness)
    solid = extrude(sketch.sketch, width)

    # ADR 0040: subtract each flange-local hole/cutout from the folded solid so
    # mass / volume / render agree with the fabricated blank. The extrude runs
    # along -y (Plane.XZ normal), so the folded strip spans y in [-width, 0]; the
    # width centreline (v = 0) therefore maps to y = -width/2. Each hole sits on
    # its flange's mid-surface at the recorded start point advanced ``u`` along the
    # heading, and pierces along the flange surface normal (-sin phi, 0, cos phi).
    # Because the hole lies wholly within the flat flange (bend-crossing is
    # rejected in ``bend_chain``), a cylinder/box of depth 3*t removes exactly the
    # prism ``area * t`` — the basis of ADR 0040's volume identity.
    for hole in holes or []:
        solid = _subtract_hole(solid, hole, flange_frames, thickness=thickness, width=width)
    return solid


def _subtract_hole(
    solid: Any,
    hole: dict[str, Any],
    flange_frames: list[tuple[float, float, float]],
    *,
    thickness: float,
    width: float,
) -> Any:
    """Cut one flat-pattern hole out of the folded solid at its flange's 3-D pose.

    ``hole`` carries its flange index and in-flange ``u`` (along the flange from
    the leading edge) / ``v`` (across the width from the centreline) offsets, plus
    the primitive (a round hole with ``diameter`` or a rectangular cutout with
    ``length``/``width``). The cutter is oriented by the flange's heading so it
    pierces perpendicular to the flange face.
    """

    from build123d import Box, Cylinder, Plane

    sx, sz, phi = flange_frames[hole["flange"]]
    heading = (cos(phi), 0.0, sin(phi))
    normal = (-sin(phi), 0.0, cos(phi))
    origin = (sx + hole["u"] * cos(phi), hole["v"] - width / 2.0, sz + hole["u"] * sin(phi))
    plane = Plane(origin=origin, x_dir=heading, z_dir=normal)
    depth = thickness * 3.0  # over-length so the cutter fully clears both faces
    if hole["kind"] == "cylindrical_hole":
        cutter = plane * Cylinder(radius=hole["diameter"] / 2.0, height=depth)
    else:  # rectangular cutout: length along the flange (u), width across (v)
        cutter = plane * Box(hole["length"], hole["width"], depth)
    return solid - cutter


def _resolve_holes(
    holes: list[dict[str, Any]] | None,
    flanges: list[float],
    flange_starts: list[float],
    bend_regions: list[tuple[float, float]],
    *,
    developed_length: float,
    width: float,
) -> list[dict[str, Any]]:
    """Validate and unfold flange-local holes into developed-blank coordinates.

    ADR 0040. Each ``hole`` is ``{"flange": j, "u": <along>, "v": <across>, ...}``
    where ``u`` runs from flange ``j``'s leading edge (its smaller developed x) and
    ``v`` from the width centreline. A round hole carries ``diameter``; a
    rectangular cutout carries ``length`` (along ``u``) and ``width`` (across
    ``v``). The developed centre is ``[flange_starts[j] + u, v, 0]``.

    Two hard guards keep the flat and folded geometry in agreement. A hole whose
    developed extent overlaps any bend-allowance region would wrap the radius when
    folded (its removed volume would not be a clean prism), and a hole running off
    the outline would not lie on the blank — both raise a clear ``ValueError``
    rather than silently producing a wrong part.
    """

    eps = 1e-9
    resolved: list[dict[str, Any]] = []
    for order, hole in enumerate(holes or []):
        flange = hole.get("flange")
        if not isinstance(flange, int) or not 0 <= flange < len(flanges):
            raise ValueError(f"hole {order}: 'flange' must be an index in [0, {len(flanges) - 1}], got {flange!r}")
        try:
            u = float(hole["u"])
            v = float(hole["v"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"hole {order}: needs numeric 'u' and 'v' offsets ({exc})") from exc

        has_diameter = hole.get("diameter") is not None
        has_rect = hole.get("length") is not None and hole.get("width") is not None
        if has_diameter == has_rect:
            raise ValueError(
                f"hole {order}: specify exactly one of 'diameter' (round) or "
                "'length'+'width' (rectangular cutout)"
            )
        if has_diameter:
            diameter = float(hole["diameter"])
            if diameter <= 0:
                raise ValueError(f"hole {order}: diameter must be positive")
            half_u = half_v = diameter / 2.0
            record: dict[str, Any] = {"kind": "cylindrical_hole", "diameter": diameter}
        else:
            length = float(hole["length"])
            hwidth = float(hole["width"])
            if length <= 0 or hwidth <= 0:
                raise ValueError(f"hole {order}: cutout length and width must be positive")
            half_u, half_v = length / 2.0, hwidth / 2.0
            record = {"kind": "cutout", "length": length, "width": hwidth}

        cx = flange_starts[flange] + u
        cy = v
        # Reject a hole that straddles a bend line: its developed x-extent must not
        # overlap any bend-allowance region.
        for j, (r_start, r_end) in enumerate(bend_regions):
            if cx - half_u < r_end - eps and cx + half_u > r_start + eps:
                raise ValueError(
                    f"hole {order} on flange {flange} crosses bend line {j} "
                    f"(developed x-extent [{cx - half_u:.3f}, {cx + half_u:.3f}] overlaps bend "
                    f"region [{r_start:.3f}, {r_end:.3f}]); a hole must lie within one flat flange"
                )
        # Reject a hole that runs off the blank outline (free edge or width).
        if cx - half_u < -eps or cx + half_u > developed_length + eps:
            raise ValueError(
                f"hole {order} on flange {flange} runs off the blank length "
                f"(developed x-extent [{cx - half_u:.3f}, {cx + half_u:.3f}] outside "
                f"[0, {developed_length:.3f}])"
            )
        if cy - half_v < -width / 2.0 - eps or cy + half_v > width / 2.0 + eps:
            raise ValueError(
                f"hole {order} on flange {flange} runs off the blank width "
                f"(across-width extent [{cy - half_v:.3f}, {cy + half_v:.3f}] outside "
                f"[{-width / 2.0:.3f}, {width / 2.0:.3f}])"
            )

        record.update({"flange": flange, "u": u, "v": v, "center": [cx, cy, 0.0]})
        resolved.append(record)
    return resolved


def bend_chain(
    flanges: list[float],
    bends: list[dict[str, Any]],
    *,
    thickness: float,
    width: float,
    holes: list[dict[str, Any]] | None = None,
) -> SheetMetalPart:
    """Model an ordered flange/bend chain as ONE flat blank + folded solid.

    ``flanges`` is an ordered list of ``N`` outside flange lengths (mm); ``bends``
    is a list of ``N-1`` bend descriptions, each a dict with keys ``angle_deg``,
    ``inside_radius``, ``k_factor``, and ``direction`` (``"up"`` or ``"down"``).

    The developed length is ``sum(flanges) + sum(BA_j)``: the single blank is
    longer than the sum of the flange lengths by the sum of the bend allowances,
    so the shared web of a U-channel is counted exactly once (never the per-pair
    double-count the deficiency flags). Bend ``j`` sits at developed position
    ``sum(flanges[0..j]) + sum(BA[0..j-1]) + BA_j/2`` — the centreline of its bend
    region — and spans the full width. The folded solid is a single connected,
    volume-conserving swept ribbon (:func:`_folded_profile`, ADR 0034).

    ``holes`` (ADR 0040, D-019) is an optional list of hole/cutout primitives in
    **flange-local** frames — ``{"flange": j, "u": <along>, "v": <across>,
    "diameter": d}`` for a round hole, or ``length``/``width`` in place of
    ``diameter`` for a rectangular cutout. Each is unfolded into the developed
    blank (subtracted from the flat DXF outline as an inner wire) and subtracted
    from the folded solid at its flange's 3-D pose, so the cut file, the DFM
    features, and the mass/volume all carry the same holes. A hole that would
    straddle a bend line, or run off the blank, raises a clear ``ValueError``.
    """

    if width <= 0 or thickness <= 0:
        raise ValueError("sheet-metal width and thickness must be positive")
    if len(flanges) < 2:
        raise ValueError("a bend chain needs at least two flanges")
    if len(bends) != len(flanges) - 1:
        raise ValueError(
            f"a chain of {len(flanges)} flanges needs {len(flanges) - 1} bends, got {len(bends)}"
        )
    if any(length < 0 for length in flanges):
        raise ValueError("flange lengths must be non-negative")

    angles_deg: list[float] = []
    directions: list[str] = []
    radii: list[float] = []
    k_factors: list[float] = []
    allowances: list[float] = []
    for spec in bends:
        direction = spec.get("direction", "up")
        if direction not in ("up", "down"):
            raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
        angle_deg = float(spec["angle_deg"])
        inside_radius = float(spec["inside_radius"])
        k_factor = float(spec["k_factor"])
        angles_deg.append(angle_deg)
        directions.append(direction)
        radii.append(inside_radius)
        k_factors.append(k_factor)
        allowances.append(_bend_allowance(angle_deg, inside_radius, k_factor, thickness))

    developed_length = sum(flanges) + sum(allowances)

    # Developed span of each flange and each bend region, shared by the flat
    # unfold (holes), the bend lines, and the folded-solid hole subtraction so all
    # three cannot drift. Flange j starts at sum(flanges[0..j-1]) + sum(BA[0..j-1]);
    # bend region j fills the BA gap between flange j and flange j+1.
    flange_starts: list[float] = []
    bend_regions: list[tuple[float, float]] = []
    cursor = 0.0
    for index, length in enumerate(flanges):
        flange_starts.append(cursor)
        cursor += length
        if index < len(allowances):
            bend_regions.append((cursor, cursor + allowances[index]))
            cursor += allowances[index]

    resolved_holes = _resolve_holes(
        holes, flanges, flange_starts, bend_regions, developed_length=developed_length, width=width
    )

    from build123d import Circle, Line, Pos, Rectangle

    # A single (developed_length x width) blank with x running 0..developed_length.
    # With no holes this is exactly the pre-ADR-0040 profile; holes are subtracted
    # as inner wires (algebra-mode ``-=``) so ExportDXF emits them on the cut layer
    # with the outline.
    flat_profile = Pos(developed_length / 2.0, 0) * Rectangle(developed_length, width)
    for hole in resolved_holes:
        cx, cy = hole["center"][0], hole["center"][1]
        if hole["kind"] == "cylindrical_hole":
            flat_profile -= Pos(cx, cy) * Circle(hole["diameter"] / 2.0)
        else:
            flat_profile -= Pos(cx, cy) * Rectangle(hole["length"], hole["width"])

    # Place one bend line per bend at the centreline of its bend region measured
    # along the developed axis: cumulative flange lengths up to and including this
    # bend's leading flange, plus every earlier bend allowance, plus half this
    # bend's own allowance.
    bend_lines: list[Any] = []
    bend_rows: list[dict[str, Any]] = []
    cumulative_flanges = 0.0
    cumulative_ba = 0.0
    for index, spec in enumerate(bends):
        cumulative_flanges += flanges[index]  # flange leading into this bend
        bend_x = cumulative_flanges + cumulative_ba + allowances[index] / 2.0
        cumulative_ba += allowances[index]

        bend_lines.append(Line((bend_x, -width / 2.0), (bend_x, width / 2.0)))
        bend_rows.append(
            {
                "line": [[bend_x, -width / 2.0], [bend_x, width / 2.0]],
                "angle": float(angles_deg[index]),
                "direction": directions[index],
                "inside_radius": float(spec["inside_radius"]),
            }
        )

    folded = _folded_profile(
        flanges,
        angles_deg,
        directions,
        radii,
        k_factors,
        thickness=thickness,
        width=width,
        holes=resolved_holes,
    )

    return SheetMetalPart(
        developed_length=developed_length,
        folded=folded,
        flat_profile=flat_profile,
        bend_lines=bend_lines,
        bends=bend_rows,
        holes=resolved_holes,
    )


def bend(
    flange_a: float,
    flange_b: float,
    *,
    angle_deg: float,
    inside_radius: float,
    k_factor: float,
    thickness: float,
    width: float,
    direction: str = "up",
    holes: list[dict[str, Any]] | None = None,
) -> SheetMetalPart:
    """Model a two-flange (one-bend) sheet-metal strip.

    ``flange_a``/``flange_b`` are the outside flange lengths in mm, ``width`` is
    the part width across the bend, and the remaining keywords are the bend
    parameters. ``direction`` is ``"up"`` or ``"down"`` and sets the fold sense of
    the folded solid and the bend-table note.

    This is a thin convenience over :func:`bend_chain` (ADR 0032): a two-flange,
    single-bend chain. The developed length is ``flange_a + BA + flange_b`` and the
    single bend line sits at ``flange_a + BA/2`` (the bend-region centerline).
    ``holes`` (ADR 0040) is passed straight through: flange 0 is ``flange_a`` and
    flange 1 is ``flange_b``.
    """

    if flange_a < 0 or flange_b < 0:
        raise ValueError("flange lengths must be non-negative")

    return bend_chain(
        [flange_a, flange_b],
        [
            {
                "angle_deg": angle_deg,
                "inside_radius": inside_radius,
                "k_factor": k_factor,
                "direction": direction,
            }
        ],
        thickness=thickness,
        width=width,
        holes=holes,
    )
