"""ADR 0013: focused unit tests for the runner DXF helpers.

These exercise the export-helper branches that the end-to-end subprocess tests
in ``test_dxf_export.py`` do not isolate cleanly: a malformed profile becoming a
non-fatal ``flat_export_failed`` warning, and the auto-flatten prism detector's
accept and reject decisions. They require a CAD kernel for the geometry cases.
"""

import pytest


pytest.importorskip("build123d")

from cadx import runner  # noqa: E402  (imported after the kernel guard)


def test_export_flats_records_failure_as_warning(tmp_path):
    """A profile the DXF writer cannot handle is a warning, not a crash."""

    # ``object()`` has neither ``center`` nor ``translate`` so the writer raises;
    # the helper must catch it and degrade to a warning with no export record.
    flats = [{"label": "bad", "profile": object(), "layer": "cut", "thickness_mm": None}]
    exports, warnings = runner._export_flats(flats, tmp_path)

    assert exports == []
    assert len(warnings) == 1
    assert warnings[0]["type"] == "flat_export_failed"
    assert warnings[0]["label"] == "bad"
    assert not (tmp_path / "bad.dxf").exists()


def test_export_flats_rejects_nonplanar_profile_as_warning(tmp_path):
    """A solid (or any non-planar shape) handed to publish_flat must not write a
    silently-degenerate DXF; it becomes a flat_export_failed warning instead."""

    from build123d import BuildPart, Box

    with BuildPart() as model:
        Box(40, 20, 4)  # a solid is not a flat profile

    flats = [{"label": "solid", "profile": model.part, "layer": "cut", "thickness_mm": None}]
    exports, warnings = runner._export_flats(flats, tmp_path)

    assert exports == []
    assert len(warnings) == 1
    assert warnings[0]["type"] == "flat_export_failed"
    assert not (tmp_path / "solid.dxf").exists()


def test_flatten_to_xy_localizes_offplane_face():
    """A face modeled on a non-XY plane is brought onto z=0 (not just translated)."""

    from build123d import BuildSketch, Plane, Rectangle

    with BuildSketch(Plane.YZ) as sk:
        Rectangle(20, 12)

    flattened = runner._flatten_to_xy(sk.sketch.faces()[0])
    bbox = flattened.bounding_box()
    assert max(abs(bbox.min.Z), abs(bbox.max.Z)) <= 1e-5


def test_auto_flat_profile_accepts_constant_thickness_prism():
    """A holed plate is recognised as a constant-thickness prism."""

    from build123d import BuildPart, Box, Cylinder, Locations, Mode

    with BuildPart() as model:
        Box(40, 20, 3)
        with Locations((-10, 0, 0), (10, 0, 0)):
            Cylinder(2, 8, mode=Mode.SUBTRACT)

    profile, thickness, reason = runner._auto_flat_profile(model.part)
    assert reason is None
    assert profile is not None
    assert abs(thickness - 3.0) <= 1e-6


def test_auto_flat_profile_rejects_stepped_solid():
    """A two-thickness solid is rejected with a human-readable reason."""

    from build123d import BuildPart, Box, Locations

    with BuildPart() as model:
        Box(40, 20, 4)
        with Locations((0, 0, 7)):
            Box(10, 10, 10)

    profile, thickness, reason = runner._auto_flat_profile(model.part)
    assert profile is None
    assert isinstance(reason, str) and reason


def test_export_flats_writes_dxf(tmp_path):
    """The explicit-flat success path writes a parseable mm DXF in process."""

    ezdxf = pytest.importorskip("ezdxf")
    from build123d import BuildSketch, Rectangle, Circle, Locations, Mode

    with BuildSketch() as sk:
        Rectangle(30, 12)
        with Locations((-8, 0), (8, 0)):
            Circle(1.5, mode=Mode.SUBTRACT)

    flats = [{"label": "shim", "profile": sk.sketch, "layer": "cut", "thickness_mm": 2.0}]
    exports, warnings = runner._export_flats(flats, tmp_path)

    assert warnings == []
    assert len(exports) == 1
    record = exports[0]
    assert record["format"] == "dxf" and record["units"] == "mm" and record["layer"] == "cut"
    doc = ezdxf.readfile(str(tmp_path / "shim.dxf"))
    assert doc.header.get("$INSUNITS") == 4
    assert len(list(doc.modelspace().query("CIRCLE"))) == 2


def test_auto_export_flats_emits_for_prism_and_skips_dict(tmp_path):
    """Auto-flatten writes a DXF for a real prism and ignores synthetic dicts."""

    from build123d import BuildPart, Box

    with BuildPart() as model:
        Box(24, 16, 2)

    entries = [
        {"label": "plate", "object": model.part},
        {"label": "synthetic", "object": {"bbox": {"min": [0, 0, 0], "max": [1, 1, 1]}}},
    ]
    exports, warnings = runner._auto_export_flats(entries, set(), tmp_path)

    labels = {record["label"] for record in exports}
    assert labels == {"plate"}  # the dict publication is skipped, not flattened
    assert (tmp_path / "plate.dxf").exists()
    assert abs(exports[0]["thickness_mm"] - 2.0) <= 1e-6
    assert warnings == []


def test_auto_export_flats_skips_nonprismatic_with_warning(tmp_path):
    """A stepped solid yields an advisory warning and no DXF, never a failure."""

    from build123d import BuildPart, Box, Locations

    with BuildPart() as model:
        Box(40, 20, 4)
        with Locations((0, 0, 7)):
            Box(10, 10, 10)

    exports, warnings = runner._auto_export_flats(
        [{"label": "stepped", "object": model.part}], set(), tmp_path
    )
    assert exports == []
    assert len(warnings) == 1
    assert warnings[0]["type"] == "autoflatten_skipped"
    assert warnings[0]["label"] == "stepped"
    assert not (tmp_path / "stepped.dxf").exists()


def test_auto_export_flats_respects_explicit_and_sheetmetal(tmp_path):
    """Auto-flatten yields to explicit flats and to sheet-metal entries."""

    from build123d import BuildPart, Box

    with BuildPart() as model:
        Box(24, 16, 2)

    # Same label already published as an explicit flat -> auto-flatten must skip.
    explicit = runner._auto_export_flats(
        [{"label": "plate", "object": model.part}], {"plate"}, tmp_path
    )
    # An entry carrying an internal sheet-metal "flat" key (ADR 0016) -> skip too.
    sheet = runner._auto_export_flats(
        [{"label": "bracket", "object": model.part, "flat": {"profile": None}}], set(), tmp_path
    )

    assert explicit == ([], [])
    assert sheet == ([], [])
    assert not (tmp_path / "plate.dxf").exists()
    assert not (tmp_path / "bracket.dxf").exists()
