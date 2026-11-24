"""Feature detection must not depend on how a part is posed in the assembly.

Detection runs on the *placed* STEP export. The full-cylinder and ``through``
tests compared world axis-aligned bounding boxes, so they were only correct for
a cylinder whose axis lay along X, Y, or Z. A plate with two holes placed at a
30 degree tilt lost both holes and gained one phantom 60 mm ``slot`` instead
(reproduced in the 2026-11-24 review; see LOG.md). These tests assert the
property directly: a rigid placement changes where features are, not what
they are.
"""

from __future__ import annotations

from math import sqrt

import pytest

build123d = pytest.importorskip("build123d")

from build123d import Box, BuildPart, BuildSketch, Circle, Cylinder, GridLocations, Location, Mode, Pos, Rectangle, extrude  # noqa: E402

from cadx.inspector import _detected_topology_features  # noqa: E402


def _plate_with_two_holes():
    """80 x 20 x 4 plate with two 6 mm through holes 60 mm apart along X."""

    with BuildPart() as plate:
        with BuildSketch():
            Rectangle(80, 20)
            with GridLocations(60, 0, 2, 1):
                Circle(3, mode=Mode.SUBTRACT)
        extrude(amount=4)
    return plate.part


def _block_with_blind_hole_and_boss():
    """A block with one blind 8 mm hole and one 10 mm boss, both along Z."""

    block = Box(40, 40, 20)
    block -= Pos(-10, 0, 5) * Cylinder(radius=4, height=10)  # blind: opens on the top face only
    block += Pos(10, 0, 12.5) * Cylinder(radius=5, height=5)  # boss standing 5 mm proud
    return block


def _non_planar(features: list[dict]) -> list[dict]:
    return [feature for feature in features if feature["kind"] != "planar_datum"]


def _summary(features: list[dict]) -> list[tuple]:
    """Pose-independent description of the detected non-planar features."""

    return sorted(
        (feature["kind"], round(feature.get("diameter", 0.0), 4), feature.get("through"))
        for feature in _non_planar(features)
    )


# Placements chosen so the hole axis leaves the principal axes in different
# ways: about the hole-spacing axis (the reported case), about the other
# in-plane axis, and a compound rotation with a translation.
_PLACEMENTS = [
    pytest.param(Location((0, 0, 0), (30, 0, 0)), id="tilt-x-30"),
    pytest.param(Location((0, 0, 0), (0, 45, 0)), id="tilt-y-45"),
    pytest.param(Location((12, -7, 3), (20, 35, 50)), id="compound"),
]


@pytest.mark.parametrize("placement", _PLACEMENTS)
def test_tilted_plate_keeps_its_two_through_holes(placement):
    plate = _plate_with_two_holes()
    reference = _summary(_detected_topology_features(plate, "plate"))
    assert reference == [("cylindrical_hole", 6.0, True), ("cylindrical_hole", 6.0, True)]

    tilted = _detected_topology_features(plate.located(placement), "plate")

    assert _summary(tilted) == reference
    assert not [feature for feature in tilted if feature["kind"] == "slot"]


@pytest.mark.parametrize("placement", _PLACEMENTS)
def test_tilted_hole_centers_and_axes_follow_the_placement(placement):
    plate = _plate_with_two_holes()
    holes = _non_planar(_detected_topology_features(plate.located(placement), "plate"))

    # The hole axis is the plate normal: local Z carried into the world.
    expected_axis = (placement * Location((0, 0, 1))).position - placement.position
    expected_centers = [tuple((placement * Location((x, 0, 2))).position) for x in (-30, 30)]

    assert len(holes) == 2
    for hole in holes:
        dot = sum(a * b for a, b in zip(hole["axis"], tuple(expected_axis)))
        assert abs(abs(dot) - 1.0) < 1e-6  # parallel either way round
    for expected in expected_centers:
        nearest = min(sqrt(sum((a - b) ** 2 for a, b in zip(hole["center"], expected))) for hole in holes)
        assert nearest < 1e-4


@pytest.mark.parametrize("placement", _PLACEMENTS)
def test_tilted_blind_hole_and_boss_are_not_reported_as_through(placement):
    block = _block_with_blind_hole_and_boss()
    reference = _summary(_detected_topology_features(block, "block"))
    # Neither cylinder spans the part along its axis, so neither is a through hole.
    assert reference == [("cylindrical_boss", 8.0, False), ("cylindrical_boss", 10.0, False)]

    tilted = _detected_topology_features(block.located(placement), "block")

    assert _summary(tilted) == reference
    heights = sorted(round(feature["height"], 4) for feature in _non_planar(tilted))
    assert heights == [5.0, 10.0]


def test_tilted_obround_slot_is_still_a_slot_not_two_holes():
    """Half-cylinder slot ends stay partial however the part is posed."""

    with BuildPart() as slotted:
        with BuildSketch():
            Rectangle(80, 20)
            Rectangle(20, 6, mode=Mode.SUBTRACT)
            with GridLocations(20, 0, 2, 1):
                Circle(3, mode=Mode.SUBTRACT)
        extrude(amount=4)
    flat = _non_planar(_detected_topology_features(slotted.part, "p"))
    assert [feature["kind"] for feature in flat] == ["slot"]

    tilted = _non_planar(_detected_topology_features(slotted.part.located(Location((0, 0, 0), (30, 0, 0))), "p"))

    assert [feature["kind"] for feature in tilted] == ["slot"]
    assert tilted[0]["width"] == pytest.approx(flat[0]["width"], abs=1e-4) == pytest.approx(6.0, abs=1e-4)
    assert tilted[0]["length"] == pytest.approx(flat[0]["length"], abs=1e-4)


def test_axis_aligned_detection_is_unchanged():
    """The principal-axis path keeps its exact records (ids, centers, order)."""

    features = _non_planar(_detected_topology_features(_plate_with_two_holes(), "plate"))

    assert [feature["id"] for feature in features] == [
        "feat.auto_plate_cylindrical_hole_1",
        "feat.auto_plate_cylindrical_hole_2",
    ]
    centers = sorted(tuple(feature["center"]) for feature in features)
    assert centers[0] == pytest.approx((-30.0, 0.0, 2.0), abs=1e-9)
    assert centers[1] == pytest.approx((30.0, 0.0, 2.0), abs=1e-9)
    assert all(abs(feature["axis"][2]) == 1.0 for feature in features)
