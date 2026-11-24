"""Publishing two objects under one label is an error at the design line.

Every export is written to ``<label>.<ext>`` and every check resolves
``obj.<label>`` through a label-keyed index, so a second ``publish`` with the
same label silently overwrote the first part's STEP/STL/GLB/DXF and shadowed it
in ``dimension``/``clearance`` checks (reproduced in the 2026-11-24 review; see
LOG.md). The registry now refuses the second publication.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cadx.registry import clear_registry, publish, publish_flat, publish_sheet_metal, snapshot_registry


_BOX = {"bbox": {"min": [0, 0, 0], "max": [1, 1, 1]}}


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def _sheet_part() -> SimpleNamespace:
    """A minimal stand-in for ``SheetMetalPart`` (no kernel needed)."""

    return SimpleNamespace(
        developed_length=10.0, folded=dict(_BOX), flat_profile=None, bend_lines=[], bends=[], holes=[]
    )


def test_second_publish_with_the_same_label_raises():
    publish("plate", dict(_BOX))

    with pytest.raises(ValueError, match="plate"):
        publish("plate", dict(_BOX))

    assert [entry["label"] for entry in snapshot_registry()["published"]] == ["plate"]


def test_sheet_metal_and_plain_publications_share_one_label_namespace():
    publish("bracket", dict(_BOX))

    with pytest.raises(ValueError, match="bracket"):
        publish_sheet_metal("bracket", _sheet_part())

    clear_registry()
    publish_sheet_metal("bracket", _sheet_part())
    with pytest.raises(ValueError, match="bracket"):
        publish("bracket", dict(_BOX))


def test_distinct_labels_and_an_explicit_flat_for_a_part_are_still_allowed():
    """``publish_flat`` reuses a part's label on purpose: it names that part's DXF."""

    publish("plate", dict(_BOX))
    publish("cover", dict(_BOX))
    publish_flat("plate", profile=None)

    snapshot = snapshot_registry()
    assert [entry["label"] for entry in snapshot["published"]] == ["plate", "cover"]
    assert [entry["label"] for entry in snapshot["flats"]] == ["plate"]


def test_clear_registry_releases_labels():
    publish("plate", dict(_BOX))
    clear_registry()

    publish("plate", dict(_BOX))  # must not raise
