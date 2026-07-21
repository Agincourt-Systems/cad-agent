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
from math import cos, degrees, pi, radians, sin
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


def _folded_single(
    flange_a: float,
    flange_b: float,
    *,
    angle_deg: float,
    thickness: float,
    width: float,
    direction: str,
) -> Any:
    """Closed-form folded solid for a single bend (ADR 0016), preserved verbatim.

    Keeping this construction exactly as ADR 0016 shipped it means the single-bend
    envelope and every existing single-bend test are unchanged when ``bend()``
    delegates to ``bend_chain()``. For a 90-degree bend the envelope is exactly
    ``(flange_a + thickness, width, flange_b)`` regardless of fold direction; a
    non-right-angle bend rotates flange B about the bend axis for a representative
    valid solid.
    """

    from build123d import Axis, Box, Pos

    fold_sign = 1.0 if direction == "up" else -1.0
    base = Pos(flange_a / 2.0, 0, thickness / 2.0) * Box(flange_a, width, thickness)
    if abs(angle_deg - 90.0) < 1e-9:
        # Exact rectilinear envelope (flange_a + t, width, flange_b) for the
        # common right-angle bend, independent of fold direction: a standing wall
        # of thickness t at x in [flange_a, flange_a + t], aligned to the base
        # bottom (up) or top (down) so the base thickness is absorbed either way
        # and the Z extent stays flange_b. This trades inside-radius corner
        # fidelity for a closed-form, predictable envelope.
        wall_center_z = flange_b / 2.0 if direction == "up" else thickness - flange_b / 2.0
        wall = Pos(flange_a + thickness / 2.0, 0, wall_center_z) * Box(thickness, width, flange_b)
    else:
        # General angle: flange B lies flat past the bend, then rotates up about
        # the bend axis. The envelope is a representative valid solid rather than
        # a closed form (no test pins a non-right-angle envelope).
        wall = Pos(flange_a + flange_b / 2.0, 0, thickness / 2.0) * Box(flange_b, width, thickness)
        wall = wall.rotate(Axis((flange_a, 0, thickness / 2.0), (0, 1, 0)), fold_sign * angle_deg)
    return base + wall


def _folded_chain(
    flanges: list[float],
    angles_deg: list[float],
    directions: list[str],
    *,
    thickness: float,
    width: float,
) -> Any:
    """Connected folded solid for a chain of two or more bends (ADR 0032).

    The part is folded in the XZ cross-section plane; every bend axis is parallel
    to the width (``y``) axis, which matches the U-channel / clevis geometry the
    deficiency targets. A running "pen" walks the flange chain: it starts at the
    origin heading ``+x`` and, at each bend, turns its heading by the bend angle
    (``+`` for ``up``, ``-`` for ``down``). Each flange is a ``Box`` of its outer
    length rotated so its length points along the current heading.

    The material thickness is centred on each flange's mid-plane (``z`` in
    ``[-t/2, t/2]`` before rotation). Centring makes consecutive flanges overlap in
    a ``t x t`` corner region, so the boolean union is a **single connected solid**
    — the property the interference / center-of-mass machinery requires — with a
    sharp outer corner. The envelope is therefore correct to within one material
    thickness; bend-region material (and exact envelopes) are ADR 0034's concern.

    ``build123d`` rotates ``+x`` toward ``-z`` under a positive rotation about
    ``+y`` (verified empirically), so a heading angle ``phi`` (measured CCW from
    ``+x`` toward ``+z``) is realised by rotating the axis-aligned box by
    ``-degrees(phi)`` about ``+y``.
    """

    from build123d import Axis, Box, Pos

    px, pz = 0.0, 0.0  # current hinge point in the folded XZ plane
    phi = 0.0  # current heading angle, CCW from +x toward +z
    solids: list[Any] = []
    count = len(flanges)
    for index, length in enumerate(flanges):
        heading_x, heading_z = cos(phi), sin(phi)
        center_x = px + heading_x * length / 2.0
        center_z = pz + heading_z * length / 2.0
        # Axis-aligned flange: length along x, width along y, thickness along z
        # centred on z = 0; then rotate about +y so its length points along phi.
        box = Pos(center_x, 0, center_z) * Box(length, width, thickness)
        box = box.rotate(Axis((center_x, 0, center_z), (0, 1, 0)), -degrees(phi))
        solids.append(box)
        # Advance the pen to the far end of this flange.
        px += heading_x * length
        pz += heading_z * length
        # Turn the heading for the next flange (no turn after the last flange).
        if index < count - 1:
            turn = 1.0 if directions[index] == "up" else -1.0
            phi += turn * radians(angles_deg[index])

    folded = solids[0]
    for solid in solids[1:]:
        folded = folded + solid
    return folded


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
    region — and spans the full width. The folded solid is a single connected
    solid: the closed-form single-bend construction for one bend, or a rigid-fold
    walk for two or more.
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

    if len(flanges) == 2:
        folded = _folded_single(
            flanges[0],
            flanges[1],
            angle_deg=angles_deg[0],
            thickness=thickness,
            width=width,
            direction=directions[0],
        )
    else:
        folded = _folded_chain(
            flanges,
            angles_deg,
            directions,
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
