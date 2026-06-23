"""ADR 0013: the flat-publication registry channel (pure-Python, no CAD kernel).

``publish_flat`` records a flat profile in a dedicated channel so the worker can
emit a DXF for it. These tests assert the channel is captured by
``snapshot_registry`` and reset by ``clear_registry`` without requiring
build123d, mirroring how the existing registry tests keep core bookkeeping
independent of the CAD kernel.
"""

from cadx import publish, publish_flat
from cadx.registry import clear_registry, snapshot_registry


def test_publish_flat_is_captured_in_snapshot():
    """A flat publication appears under the new ``flats`` snapshot key."""

    clear_registry()
    # A dict stands in for a build123d profile so this test needs no CAD kernel.
    publish_flat("plate", {"fake": "profile"}, layer="cut", thickness_mm=2.5, vendor="acme")

    snapshot = snapshot_registry()
    assert "flats" in snapshot
    assert len(snapshot["flats"]) == 1
    flat = snapshot["flats"][0]
    assert flat["label"] == "plate"
    assert flat["layer"] == "cut"
    assert flat["thickness_mm"] == 2.5
    # Extra keyword metadata is preserved for downstream BOM/DFM consumers.
    assert flat["metadata"]["vendor"] == "acme"

    # The existing channels remain intact and untouched.
    assert snapshot["published"] == []
    assert snapshot["features"] == []


def test_clear_registry_resets_flats():
    """``clear_registry`` empties the flats channel alongside the others."""

    clear_registry()
    publish("part", {"bbox": {"min": [0, 0, 0], "max": [1, 1, 1]}})
    publish_flat("plate", {"fake": "profile"})
    assert snapshot_registry()["flats"]

    clear_registry()
    snapshot = snapshot_registry()
    assert snapshot["flats"] == []
    assert snapshot["published"] == []
