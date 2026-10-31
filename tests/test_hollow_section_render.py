"""Regression: ``cadx render`` must not crash on multi-part hollow assemblies.

Two defects, both reached by sectioning a hollow part:

* **ShapeList fold** — on build123d 0.10 ``Solid.intersect(Plane)`` returns a
  ``ShapeList`` whenever the cut yields several disjoint faces.  A tube cut
  along its own axis is the simplest case: one face per wall.
  ``_plane_section`` appended that list as a single child, so
  ``Compound(children=...)`` raised ``anytree`` ``TreeError`` ("Cannot add
  non-node object ..."), and a single hollow solid returned a bare list
  instead of a shape.
* **Grazed-section camera** — ``project_to_viewport`` looks from the camera
  towards ``shape.center()``.  A section whose centroid lands exactly on the
  camera axis — any part symmetric about that axis grazed by its section
  plane — makes the look direction collinear with the default
  ``viewport_up`` of ``(0, 0, 1)``, and OCCT raises
  ``gp_Dir::Crossed() - result vector has zero norm``.
"""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytest.importorskip("build123d")
pytest.importorskip("PIL")

from cadx import renderer  # noqa: E402


def run_cadx(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}
    return subprocess.run(
        [sys.executable, "-m", "cadx.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def hollow_tube(outer: float = 12, inner: float = 9, height: float = 40):
    """A tube whose axis is +Z, so an axis-containing plane cuts both walls."""

    from build123d import Cylinder

    return Cylinder(outer, height) - Cylinder(inner, height)


# --- Defect A: a ShapeList section piece ------------------------------------

def test_plane_section_of_one_hollow_solid_returns_a_shape():
    from build123d import Plane

    section = renderer._plane_section(hollow_tube(), Plane.XZ)

    # Both walls are cut, as two disjoint faces, and the result is a shape the
    # caller can project — not the bare ShapeList that intersect() handed back.
    assert section is not None
    assert hasattr(section, "project_to_viewport")
    assert len(list(section.faces())) == 2


def test_plane_section_folds_shapelist_pieces_across_solids():
    from build123d import Compound, Plane, Pos

    # Two tubes, apart in X; the XZ plane contains both axes, so each solid
    # sections into two faces and the fold has to flatten four pieces.
    assembly = Compound(children=[hollow_tube(), Pos(60, 0, 0) * hollow_tube()])

    section = renderer._plane_section(assembly, Plane.XZ)

    assert section is not None
    assert len(list(section.faces())) == 4


# --- Defect B: a section centred on the camera axis -------------------------

def axis_centred_section():
    """The ``section_xy`` cut of a hollow housing grazed by the XY plane.

    The housing is a rectangular shell symmetric about +Z whose underside sits
    on ``z = 0``, so the ``section_xy`` plane grazes that face and the
    resulting frame-shaped section has its centroid exactly on the camera axis
    at ``(0, 0, 0)``.
    """

    from build123d import Box, Plane, Pos

    housing = Pos(0, 0, 10) * (Box(60, 40, 20) - Pos(0, 0, 2) * Box(50, 30, 20))
    return renderer._plane_section(housing, Plane.XY)


def test_axis_centred_section_is_on_the_camera_axis():
    """Guard the premise: the fixture really is the degenerate camera case."""

    section = axis_centred_section()
    assert section is not None
    center = section.center()
    assert (abs(center.X), abs(center.Y)) == (0.0, 0.0)


def test_write_projection_svg_renders_axis_centred_section(tmp_path):
    section = axis_centred_section()
    target = tmp_path / "section_xy.svg"

    wrote = renderer._write_projection_svg(section, target, (0, 0, 100))

    assert wrote is True
    svg = target.read_text(encoding="utf-8")
    assert "<svg" in svg
    assert "<path" in svg or "<line" in svg or "<circle" in svg


# --- End to end -------------------------------------------------------------

HOLLOW_DESIGN = '''
"""Multi-part hollow assembly whose section planes graze the parts."""
from build123d import *
from cadx import publish

# Hollow housing, underside flush with z=0: the section_xy plane grazes it and
# the section is centred on the camera axis.
housing = Pos(0, 0, 10) * (Box(60, 40, 20) - Pos(0, 0, 2) * Box(50, 30, 20))
publish("housing", housing, role="final")

# Bored tube centred on the origin: the XZ and YZ planes cut it into two
# disjoint faces, so the section fold sees a ShapeList.
publish("tube", Pos(0, 0, 30) * (Cylinder(12, 40) - Cylinder(9, 40)))

# Spherical shell tangent to the XY plane.
publish("dome", Pos(40, 0, 12) * (Sphere(12) - Sphere(9)))
'''


def test_render_multipart_hollow_assembly(tmp_path):
    design = tmp_path / "design.py"
    design.write_text(HOLLOW_DESIGN, encoding="utf-8")

    run = run_cadx(tmp_path, "run", str(design))
    assert run.returncode == 0, run.stderr
    run_dir = Path(json.loads(run.stdout.strip().splitlines()[-1])["artifact_dir"])

    rendered = run_cadx(tmp_path, "render", str(run_dir))
    assert rendered.returncode == 0, rendered.stderr
    payload = json.loads(rendered.stdout.strip().splitlines()[-1])

    assert payload["status"] == "ok"
    manifest = json.loads((tmp_path / payload["manifest"]).read_text(encoding="utf-8"))
    # Every section plane cuts this assembly, so all three must be rendered.
    assert {section["name"] for section in manifest["sections"]} == {
        "section_xy",
        "section_xz",
        "section_yz",
    }
    for section in manifest["sections"]:
        assert (tmp_path / section["path"]).is_file()


# --- Defect C: a degenerate arc in the projected edge set -------------------

def nearly_closed_arc(gap_mm: float = 3e-7, radius: float = 10.0):
    """A circular arc whose gap is too small for the SVG exporter to express.

    ``ExportSVG`` rounds path points to 1e-6 mm, while OCCT only calls a curve
    closed once its ends are within 1e-7 mm.  An arc in between is open to
    OCCT and a single point to the exporter, which is the collapse that breaks
    ``svgpathtools.Arc``.  Note the arc itself is a full 62.8 mm circle, so the
    exporter's own ``length < 1e-6`` guard does not see it.
    """

    from build123d import Edge

    return Edge.make_circle(radius, start_angle=0, end_angle=360 - math.degrees(gap_mm / radius))


def flat_ellipse_arc():
    """An ellipse arc with a zero minor radius: a circle seen exactly edge-on."""

    from build123d import Edge

    return Edge.make_ellipse(x_radius=10, y_radius=0, start_angle=0, end_angle=90)


def export_edges(edges, target: Path) -> None:
    """Run edges through the exporter exactly as ``_write_projection_svg`` does."""

    from build123d import ExportSVG

    exporter = ExportSVG(scale=10, margin=2, precision=renderer.SVG_PRECISION)
    exporter.add_layer("Visible", line_color=(20, 20, 20))
    exporter.add_shape(list(edges), layer="Visible")
    exporter.write(target)


@pytest.mark.parametrize("degenerate", [nearly_closed_arc, flat_ellipse_arc])
def test_degenerate_arc_aborts_the_svg_exporter(degenerate, tmp_path):
    """Guard the premise: this is the crash the arc filter exists to stop."""

    from build123d import Edge

    with pytest.raises(AssertionError):
        export_edges([Edge.make_line((0, 0, 0), (10, 0, 0)), degenerate()],
                     tmp_path / "raw.svg")


def test_svg_exportable_edges_closes_a_collapsed_arc():
    arc = nearly_closed_arc()

    kept = renderer._svg_exportable_edges([arc])

    # The near-complete circle is kept, as the closed circle it rounds to —
    # dropping it would erase a 10 mm radius circle from the drawing.
    assert len(kept) == 1
    assert kept[0].is_closed is True
    assert kept[0].length == pytest.approx(arc.length, abs=1e-6)


def test_svg_exportable_edges_drops_undrawable_arcs():
    from build123d import Edge

    line = Edge.make_line((0, 0, 0), (10, 0, 0))
    circle = Edge.make_circle(8)
    arc = Edge.make_circle(8, start_angle=0, end_angle=45)
    undrawable = [
        flat_ellipse_arc(),      # zero minor radius
        Edge.make_circle(0.0),   # zero radius
        Edge.make_circle(1e-9),  # below the exporter's own 1e-6 arc floor
    ]

    kept = renderer._svg_exportable_edges([line, *undrawable, circle, arc])

    # Real geometry survives, in its original order; nothing undrawable does.
    assert [edge.length for edge in kept] == [line.length, circle.length, arc.length]


@pytest.mark.parametrize("degenerate", [nearly_closed_arc, flat_ellipse_arc])
def test_filtered_edges_export_cleanly(degenerate, tmp_path):
    from build123d import Edge

    edges = [Edge.make_line((0, 0, 0), (10, 0, 0)), degenerate()]
    target = tmp_path / "filtered.svg"

    export_edges(renderer._svg_exportable_edges(edges), target)

    assert "<line" in target.read_text(encoding="utf-8")
