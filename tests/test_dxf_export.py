"""ADR 0013: DXF flat-pattern export and explicit export units (D1 + D9).

These tests drive a design through the real ``cadx run`` subprocess path that
agents use, then parse the emitted DXF with ``ezdxf`` and inspect
``diagnostics.json``. They require a real CAD kernel and the ezdxf parser, so
both are gated with ``importorskip``.

Red before implementation: ``publish_flat`` and the DXF writer do not exist, so
no ``*.dxf`` artifact and no ``format == "dxf"`` export record are produced, and
existing export records carry no ``units`` key. Green after.
"""

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest


pytest.importorskip("build123d")
ezdxf = pytest.importorskip("ezdxf")


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run cadx through the same subprocess path agents use."""

    repo_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}
    return subprocess.run(
        [sys.executable, "-m", "cadx.cli", *args],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def parse_stdout_json(result: subprocess.CompletedProcess[str]) -> dict:
    """Parse cadx JSON output, surfacing stderr on failure."""

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _write_params(tmp_path: Path) -> None:
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")


def _run_design(tmp_path: Path, source: str) -> dict:
    """Write a design, run it, and return its parsed diagnostics.json."""

    design = tmp_path / "design.py"
    design.write_text(source, encoding="utf-8")
    _write_params(tmp_path)
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    run_dir = tmp_path / payload["artifact_dir"]
    diagnostics = json.loads((run_dir / "diagnostics.json").read_text(encoding="utf-8"))
    return {"run_dir": run_dir, "diagnostics": diagnostics}


# A plate published explicitly as a flat profile: 40 x 20 outline, two Ø4 holes.
PLATE_FLAT_DESIGN = """
from build123d import *
from cadx import publish, publish_flat


def build(params):
    with BuildSketch() as sk:
        Rectangle(40, 20)
        with Locations((-10, 0), (10, 0)):
            Circle(2, mode=Mode.SUBTRACT)
    publish_flat("plate", sk.sketch, layer="cut", thickness_mm=3.0)
    return sk.sketch
"""

# A filleted plate: the outline is LINE + ARC segments, exercising the
# arc-aware closed-loop check (a SendCutSend part is usually fillet-cornered).
PLATE_ROUNDED_DESIGN = """
from build123d import *
from cadx import publish_flat


def build(params):
    with BuildSketch() as sk:
        RectangleRounded(40, 20, 4)
        with Locations((-10, 0), (10, 0)):
            Circle(2, mode=Mode.SUBTRACT)
    publish_flat("plate", sk.sketch, layer="cut", thickness_mm=3.0)
    return sk.sketch
"""

# A solid prism published normally (no publish_flat): auto-flatten should fire.
PRISM_SOLID_DESIGN = """
from build123d import *
from cadx import publish


def build(params):
    with BuildPart() as model:
        Box(40, 20, 3)
        with Locations((-10, 0, 0), (10, 0, 0)):
            Cylinder(2, 8, mode=Mode.SUBTRACT)
    publish("plate", model.part, role="final")
    return model.part
"""

# A stepped solid with two distinct thicknesses: not a constant-thickness prism.
STEPPED_SOLID_DESIGN = """
from build123d import *
from cadx import publish


def build(params):
    with BuildPart() as model:
        Box(40, 20, 4)
        with Locations((0, 0, 7)):
            Box(10, 10, 10)
    publish("stepped", model.part, role="final")
    return model.part
"""


def _dxf_records(diagnostics: dict) -> list[dict]:
    return [e for e in diagnostics["exports"] if e.get("format") == "dxf"]


def _read_dxf(run_dir: Path, record: dict):
    path = Path(record["path"])
    if not path.is_absolute():
        path = run_dir / path.name
    assert path.exists(), f"DXF path does not resolve: {record['path']}"
    return ezdxf.readfile(str(path))


def _outline_endpoints(entity):
    """Return the (x, y) endpoints of a boundary LINE or ARC entity."""

    kind = entity.dxftype()
    if kind == "LINE":
        return [(entity.dxf.start[0], entity.dxf.start[1]), (entity.dxf.end[0], entity.dxf.end[1])]
    if kind == "ARC":
        start, end = entity.start_point, entity.end_point
        return [(start[0], start[1]), (end[0], end[1])]
    return []


def _closed_loop_count(msp) -> int:
    """Whether the boundary entities form exactly one closed loop.

    A SendCutSend-clean outline is a single closed contour. build123d emits the
    outline as connected LINE and ARC segments (a filleted plate is LINE + ARC,
    a sharp plate is LINE only), so we check that every segment endpoint is
    shared by exactly two segments — an open or branching contour leaves some
    endpoint with degree 1 or 3.
    """

    deg: Counter = Counter()
    for entity in msp:
        for point in _outline_endpoints(entity):
            deg[(round(point[0], 3), round(point[1], 3))] += 1
    if not deg:
        return 0
    if not all(count == 2 for count in deg.values()):
        return -1  # open / branching contour
    return 1


def test_dxf_export_basic(tmp_path):
    """A published plate yields a DXF with one closed outline and two holes."""

    result = _run_design(tmp_path, PLATE_FLAT_DESIGN)
    records = _dxf_records(result["diagnostics"])
    assert records, "no dxf export record produced"
    doc = _read_dxf(result["run_dir"], records[0])
    msp = doc.modelspace()

    circles = list(msp.query("CIRCLE"))
    assert len(circles) == 2
    for circle in circles:
        assert abs(circle.dxf.radius - 2.0) <= 0.01

    # Exactly one closed outer contour formed by the boundary (non-circle) entities.
    assert _closed_loop_count(msp) == 1


def test_dxf_filleted_outline_is_closed(tmp_path):
    """A fillet-cornered plate still exports one closed LINE+ARC contour."""

    result = _run_design(tmp_path, PLATE_ROUNDED_DESIGN)
    records = _dxf_records(result["diagnostics"])
    assert records
    msp = _read_dxf(result["run_dir"], records[0]).modelspace()

    assert len(list(msp.query("ARC"))) > 0  # the corners are real arcs, not lines
    assert len(list(msp.query("CIRCLE"))) == 2
    assert _closed_loop_count(msp) == 1


def test_dxf_units_mm(tmp_path):
    """DXF header declares millimeters and the model bbox is in mm."""

    result = _run_design(tmp_path, PLATE_FLAT_DESIGN)
    records = _dxf_records(result["diagnostics"])
    doc = _read_dxf(result["run_dir"], records[0])
    assert doc.header.get("$INSUNITS") == 4  # 4 == millimeters

    from ezdxf import bbox

    extents = bbox.extents(doc.modelspace())
    size_x = extents.extmax[0] - extents.extmin[0]
    size_y = extents.extmax[1] - extents.extmin[1]
    assert abs(size_x - 40.0) <= 0.05
    assert abs(size_y - 20.0) <= 0.05


def test_dxf_export_recorded(tmp_path):
    """diagnostics records a dxf export with a resolvable path and mm units."""

    result = _run_design(tmp_path, PLATE_FLAT_DESIGN)
    records = _dxf_records(result["diagnostics"])
    assert len(records) == 1
    record = records[0]
    assert record["units"] == "mm"
    assert record.get("layer") == "cut"
    path = Path(record["path"])
    if not path.is_absolute():
        path = result["run_dir"] / path.name
    assert path.exists()


def test_dxf_autoflatten_emits_for_prism(tmp_path):
    """A plain solid prism gets a DXF for free via auto-flatten."""

    result = _run_design(tmp_path, PRISM_SOLID_DESIGN)
    records = _dxf_records(result["diagnostics"])
    assert records, "auto-flatten produced no dxf record for a constant-thickness prism"
    doc = _read_dxf(result["run_dir"], records[0])
    circles = list(doc.modelspace().query("CIRCLE"))
    assert len(circles) == 2
    for circle in circles:
        assert abs(circle.dxf.radius - 2.0) <= 0.01


def test_dxf_autoflatten_skips_nonprismatic(tmp_path):
    """A non-uniform-thickness solid warns and never hard-fails the run."""

    result = _run_design(tmp_path, STEPPED_SOLID_DESIGN)
    diagnostics = result["diagnostics"]
    assert diagnostics["status"] == "ok"

    skips = [w for w in diagnostics["warnings"] if w.get("type") == "autoflatten_skipped"]
    assert skips, f"expected an autoflatten_skipped warning, got {diagnostics['warnings']}"
    assert _dxf_records(diagnostics) == []


def test_export_units(tmp_path):
    """Every export record carries units==mm and the DXF header agrees."""

    result = _run_design(tmp_path, PRISM_SOLID_DESIGN)
    diagnostics = result["diagnostics"]

    assert diagnostics["exports"], "no exports recorded"
    for record in diagnostics["exports"]:
        assert record.get("units") == "mm", record

    formats = {record["format"] for record in diagnostics["exports"]}
    assert {"step", "stl", "glb", "dxf"} <= formats

    dxf_record = _dxf_records(diagnostics)[0]
    doc = _read_dxf(result["run_dir"], dxf_record)
    assert doc.header.get("$INSUNITS") == 4
