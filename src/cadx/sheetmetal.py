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

from dataclasses import dataclass
from math import atan2, cos, degrees, pi, radians, sin
from typing import Any


@dataclass(frozen=True)
class SheetMetalPart:
    """A sheet-metal part: folded solid, flat pattern, and bend table.

    ``folded`` is a build123d ``Part`` suitable for ``publish_sheet_metal``;
    ``flat_profile`` is a build123d ``Sketch`` (the single cut outline);
    ``bend_lines`` are the build123d ``Edge`` objects drawn on the DXF ``bend``
    layer (one per bend); and ``bends`` is the JSON-safe bend table written to
    ``bends.json`` (one row per bend).
    """

    developed_length: float
    folded: Any
    flat_profile: Any
    bend_lines: list[Any]
    bends: list[dict[str, Any]]


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
    px, pz = 0.0, thickness / 2.0  # current centreline point
    phi = 0.0  # heading angle, CCW from +x toward +z
    segments: list[tuple[str, Any]] = []
    count = len(flanges)
    for index, length in enumerate(flanges):
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
    return extrude(sketch.sketch, width)


def bend_chain(
    flanges: list[float],
    bends: list[dict[str, Any]],
    *,
    thickness: float,
    width: float,
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

    from build123d import Line, Pos, Rectangle

    # A single (developed_length x width) blank with x running 0..developed_length.
    flat_profile = Pos(developed_length / 2.0, 0) * Rectangle(developed_length, width)

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
    )

    return SheetMetalPart(
        developed_length=developed_length,
        folded=folded,
        flat_profile=flat_profile,
        bend_lines=bend_lines,
        bends=bend_rows,
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
) -> SheetMetalPart:
    """Model a two-flange (one-bend) sheet-metal strip.

    ``flange_a``/``flange_b`` are the outside flange lengths in mm, ``width`` is
    the part width across the bend, and the remaining keywords are the bend
    parameters. ``direction`` is ``"up"`` or ``"down"`` and sets the fold sense of
    the folded solid and the bend-table note.

    This is a thin convenience over :func:`bend_chain` (ADR 0032): a two-flange,
    single-bend chain. The developed length is ``flange_a + BA + flange_b`` and the
    single bend line sits at ``flange_a + BA/2`` (the bend-region centerline).
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
    )
