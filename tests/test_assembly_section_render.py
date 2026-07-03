"""Regression: `cadx render` must not crash on an assembly whose section
is empty (docs/log/2026-07-03-assembly-section-render-crash.md).

Two layered defects were fixed in the renderer:

* **A** — sections were taken with ``Compound.intersect(Plane)``, which
  returned an empty shape for the real multi-solid airframe assembly even
  though the child solids each intersected the plane.  ``_plane_section``
  now folds over ``shape.solids()`` and recombines, which is correct for
  every case.
* **B** — projecting an *empty* shape from a viewport camera on the +Z
  axis (``top`` / ``section_xy`` at ``(0,0,100)``) crashed inside OCCT
  (``gp_Dir::Crossed`` zero norm) because the default ``viewport_up``
  became collinear with the look direction.  ``_write_projection_svg`` now
  skips empty shapes (returning ``False``) and the section loop's old
  ``is None`` guard — which does not catch a non-``None`` *empty* compound
  — no longer decides whether to project.
"""

import json
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


# --- Defect A: per-solid section fold ---------------------------------------

def test_plane_section_folds_over_solids():
    from build123d import Box, Compound, Plane, Pos

    # Two solids that both cross the XY plane, apart in X.
    comp = Compound(children=[Box(20, 20, 20), Pos(60, 0, 0) * Box(20, 20, 20)])
    section = renderer._plane_section(comp, Plane.XY)
    assert section is not None
    # One rectangular cut face per solid.
    assert len(list(section.faces())) == 2


def test_plane_section_returns_none_when_nothing_crosses():
    from build123d import Box, Compound, Plane, Pos

    # Both solids sit entirely above z=0, so the XY section is empty.
    comp = Compound(children=[Pos(0, 0, 50) * Box(20, 20, 20),
                              Pos(60, 0, 50) * Box(20, 20, 20)])
    assert renderer._plane_section(comp, Plane.XY) is None


# --- Defect B: empty-shape projection is crash-safe -------------------------

def test_write_projection_svg_skips_empty_shape(tmp_path):
    from build123d import Box, Pos

    # A shape entirely above z=0 has an empty XY section; projecting that
    # empty section from the +Z camera used to raise gp_Dir::Crossed.
    empty = (Pos(0, 0, 50) * Box(10, 10, 10)).intersect(__import__("build123d").Plane.XY)
    target = tmp_path / "empty.svg"
    wrote = renderer._write_projection_svg(empty, target, (0, 0, 100))
    assert wrote is False
    assert not target.exists()


def test_write_projection_svg_writes_real_shape(tmp_path):
    from build123d import Box

    target = tmp_path / "box.svg"
    wrote = renderer._write_projection_svg(Box(10, 10, 10), target, (0, 0, 100))
    assert wrote is True
    assert target.is_file()


# --- End-to-end: multi-part run with an empty section renders cleanly -------

def test_render_multipart_empty_section_does_not_crash(tmp_path):
    """A two-part assembly whose XY section is empty must render (contact
    sheet + shaded iso) with the empty section cleanly omitted, not crash."""
    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import Box, Location
from cadx import publish


def build(params):
    # Both parts sit entirely above z=0 -> the XY section is empty, and the
    # section_xy viewport camera is on the +Z axis (the crash trigger).
    publish("lower", Box(40, 40, 10), placement=Location((0, 0, 30)), role="final")
    publish("upper", Box(40, 40, 10), placement=Location((0, 0, 60)))
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")

    run = run_cadx(tmp_path, "run", str(design), "--params", "params.yaml")
    assert run.returncode == 0, run.stderr
    run_dir = tmp_path / json.loads(run.stdout)["artifact_dir"]
    assert (run_dir / "assembly.step").is_file()  # ADR-0023 combined export

    render = run_cadx(tmp_path, "render", str(run_dir))
    assert render.returncode == 0, f"render crashed:\n{render.stderr}"

    manifest = json.loads((run_dir / "views" / "render_manifest.json").read_text())
    assert (run_dir / "views" / "contact.png").is_file()
    assert (run_dir / "views" / "shaded_iso.png").is_file()
    # The empty XY section must be skipped, not emitted or crashed on.
    assert "section_xy" not in {s["name"] for s in manifest["sections"]}
    assert not manifest["warnings"]
