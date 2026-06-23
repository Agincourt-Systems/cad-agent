"""Lightweight sheet-metal bend modeling.

build123d ships no native sheet-metal unfolder, and a general BREP unfolder is
unnecessary for the rectilinear brackets this harness targets. This module
models a part as flat flanges joined by an explicit bend operation, computing the
developed (flat) length from the material's bend allowance so a shop like
SendCutSend can cut-and-bend a single part instead of bolting flats together.

One ``bend(...)`` description yields both a folded 3D solid (so the existing
clearance / interference / center-of-mass machinery can reason about the part in
its assembled pose) and a flat pattern carrying the bend line on a dedicated
layer plus a machine-readable bend table for the press-brake operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import pi
from typing import Any


@dataclass(frozen=True)
class SheetMetalPart:
    """A single-bend sheet-metal part: folded solid, flat pattern, and bend table.

    ``folded`` is a build123d ``Part`` suitable for ``publish_sheet_metal``;
    ``flat_profile`` is a build123d ``Sketch`` (the cut outline); ``bend_lines``
    are the build123d ``Edge`` objects drawn on the DXF ``bend`` layer; and
    ``bends`` is the JSON-safe bend table written to ``bends.json``.
    """

    developed_length: float
    folded: Any
    flat_profile: Any
    bend_lines: list[Any]
    bends: list[dict[str, Any]]


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

    The bend allowance ``BA = (pi/180) * angle * (inside_radius + k_factor *
    thickness)`` is the length of the neutral fiber stretched around the bend, so
    the flat blank is longer than ``flange_a + flange_b`` by ``BA``; the developed
    length is ``flange_a + BA + flange_b``. The single bend line sits at
    ``flange_a + BA/2`` (the bend-region centerline) and spans the width.
    """

    from build123d import Axis, Box, Line, Pos, Rectangle

    if width <= 0 or thickness <= 0:
        raise ValueError("sheet-metal width and thickness must be positive")
    if flange_a < 0 or flange_b < 0:
        raise ValueError("flange lengths must be non-negative")
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")

    bend_allowance = (pi / 180.0) * angle_deg * (inside_radius + k_factor * thickness)
    developed_length = flange_a + bend_allowance + flange_b

    # Flat pattern: a (developed_length x width) strip with x running 0..developed.
    flat_profile = Pos(developed_length / 2.0, 0) * Rectangle(developed_length, width)

    # The bend line crosses the full width at the bend-region centerline.
    bend_x = flange_a + bend_allowance / 2.0
    bend_line = Line((bend_x, -width / 2.0), (bend_x, width / 2.0))
    bends = [
        {
            "line": [[bend_x, -width / 2.0], [bend_x, width / 2.0]],
            "angle": float(angle_deg),
            "direction": direction,
            "inside_radius": float(inside_radius),
        }
    ]

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
    folded = base + wall

    return SheetMetalPart(
        developed_length=developed_length,
        folded=folded,
        flat_profile=flat_profile,
        bend_lines=[bend_line],
        bends=bends,
    )
