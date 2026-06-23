"""ADR 0017: BOM and manufacturing package.

A design declares purchasing metadata via ``publish_part_meta`` and
``cadx bom <run_dir>`` aggregates that metadata with auto-derived geometry
facts (flat-pattern area, bounding box, hole count) into deterministic
``bom.csv`` and ``bom.json`` artifacts grouped by vendor with totals.

These tests fail before implementation because ``publish_part_meta`` is not
exported from ``cadx`` and the ``bom`` subcommand does not exist.
"""

import csv
import io
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytest.importorskip("build123d")


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
    """Parse cadx JSON output, asserting a clean exit."""

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


PLATE_DESIGN = """
from build123d import *
from cadx import publish, publish_feature, publish_part_meta


def build(params):
    with BuildPart() as plate:
        with BuildSketch():
            Rectangle(80, 20)
            with GridLocations(60, 0, 2, 1):
                Circle(3, mode=Mode.SUBTRACT)
        extrude(amount=4)

    publish("plate", plate.part, role="final")
    publish_feature("mh_left", kind="cylindrical_hole", diameter=6, center=[-30, 0, 2])
    publish_feature("mh_right", kind="cylindrical_hole", diameter=6, center=[30, 0, 2])
    publish_part_meta(
        "plate",
        vendor="SendCutSend",
        material="6061-T6",
        thickness_mm=4,
        finish="none",
        qty=3,
        unit_cost=7.5,
        part_number="SRM-PLATE-01",
        process="laser",
    )
    return plate.part
"""


def run_plate(tmp_path: Path) -> Path:
    design = tmp_path / "design.py"
    design.write_text(PLATE_DESIGN, encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok"
    return tmp_path / payload["artifact_dir"]


def test_part_meta_recorded_in_diagnostics(tmp_path):
    """publish_part_meta must surface a record in diagnostics.json."""

    run_dir = run_plate(tmp_path)
    diagnostics = json.loads((run_dir / "diagnostics.json").read_text(encoding="utf-8"))
    part_meta = {record["label"]: record for record in diagnostics["part_meta"]}
    assert "plate" in part_meta
    plate = part_meta["plate"]
    assert plate["vendor"] == "SendCutSend"
    assert plate["material"] == "6061-T6"
    assert float(plate["thickness_mm"]) == 4
    assert int(plate["qty"]) == 3
    assert float(plate["unit_cost"]) == 7.5
    assert plate["part_number"] == "SRM-PLATE-01"


def test_bom_rows(tmp_path):
    """Each published part appears with material/thickness/qty + computed area;
    vendor totals equal the sum of member rows."""

    run_dir = run_plate(tmp_path)
    summary = parse_stdout_json(run_cadx(tmp_path, "bom", str(run_dir)))

    bom = json.loads((run_dir / "bom.json").read_text(encoding="utf-8"))

    # Locate the plate row regardless of the grouping container shape.
    rows = bom["rows"]
    plate = next(row for row in rows if row["label"] == "plate")

    assert plate["material"] == "6061-T6"
    assert float(plate["thickness_mm"]) == 4
    assert int(plate["qty"]) == 3
    assert plate["hole_count"] == 2

    expected_area = 80 * 20 - 2 * math.pi * 9  # rectangle minus two radius-3 holes
    assert plate["area_mm2"] == pytest.approx(expected_area, abs=0.01)

    # Vendor grouping carries totals that equal the sum of its member rows.
    vendor_group = next(group for group in bom["vendors"] if group["vendor"] == "SendCutSend")
    member_rows = [row for row in rows if row["vendor"] == "SendCutSend"]
    assert vendor_group["total_qty"] == sum(int(row["qty"]) for row in member_rows)
    assert vendor_group["total_area_mm2"] == pytest.approx(
        sum(row["area_mm2"] * int(row["qty"]) for row in member_rows), abs=0.01
    )
    assert vendor_group["total_ext_cost"] == pytest.approx(3 * 7.5, abs=1e-6)

    # The summary line reflects the same facts.
    assert summary["status"] == "ok"
    assert summary["rows"] == len(rows)
    assert Path(summary["bom_csv_path"]).name == "bom.csv"


def test_bom_deterministic(tmp_path):
    """Re-running cadx bom yields a byte-identical bom.csv that round-trips."""

    run_dir = run_plate(tmp_path)

    parse_stdout_json(run_cadx(tmp_path, "bom", str(run_dir)))
    first = (run_dir / "bom.csv").read_bytes()

    parse_stdout_json(run_cadx(tmp_path, "bom", str(run_dir)))
    second = (run_dir / "bom.csv").read_bytes()

    assert first == second

    # Stable column order and parseable rows.
    reader = csv.reader(io.StringIO(first.decode("utf-8")))
    header = next(reader)
    assert header[0] == "vendor"
    assert header[1] == "label"
    assert "material" in header
    assert "thickness_mm" in header
    assert "qty" in header
    assert "area_mm2" in header
    assert "hole_count" in header

    body = [row for row in reader if row]
    plate_rows = [row for row in body if row[1] == "plate"]
    assert len(plate_rows) == 1


def test_bom_handles_part_without_meta_and_without_step(tmp_path):
    """A synthetic dict publication (no STEP, no part_meta) must not crash bom;
    its area is left empty and a warning is recorded."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from cadx import publish


def build(params):
    publish(
        "synthetic",
        {
            "bbox": {"min": [0, 0, 0], "max": [10, 20, 3]},
            "mass_properties": {"volume": 600},
            "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
        },
        role="final",
    )
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    run_dir = tmp_path / payload["artifact_dir"]

    summary = parse_stdout_json(run_cadx(tmp_path, "bom", str(run_dir)))
    assert summary["status"] == "ok"

    bom = json.loads((run_dir / "bom.json").read_text(encoding="utf-8"))
    synthetic = next(row for row in bom["rows"] if row["label"] == "synthetic")
    # No metadata declared: qty defaults to 1, purchasing fields empty.
    assert int(synthetic["qty"]) == 1
    assert synthetic["material"] in (None, "")
    # No STEP export: area could not be derived.
    assert synthetic["area_mm2"] in (None, "")
    # bbox still derived from spatial.json.
    assert synthetic["bbox_mm"] == [10.0, 20.0, 3.0]


def test_bom_qty_zero_preserved_and_orphan_meta_warns(tmp_path):
    """An explicit qty=0 is kept (not defaulted to 1), and metadata for a part
    that was never published surfaces an orphan warning instead of vanishing."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from cadx import publish, publish_part_meta


def build(params):
    publish(
        "real",
        {"bbox": {"min": [0, 0, 0], "max": [10, 10, 2]}, "mass_properties": {"volume": 200},
         "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8}},
        role="part",
    )
    publish_part_meta("real", vendor="V", qty=0, unit_cost=5.0)
    publish_part_meta("ghost", vendor="V", qty=5, unit_cost=2.0)  # never published
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", "design.py", "--params", "params.yaml"))
    run_dir = tmp_path / payload["artifact_dir"]

    parse_stdout_json(run_cadx(tmp_path, "bom", str(run_dir)))
    bom = json.loads((run_dir / "bom.json").read_text(encoding="utf-8"))

    real = next(row for row in bom["rows"] if row["label"] == "real")
    assert int(real["qty"]) == 0  # not silently bumped to 1
    assert real["ext_cost"] == 0  # 0 * unit_cost

    orphan_warnings = [w for w in bom["warnings"] if w.get("type") == "bom_orphan_part_meta"]
    assert any(w["label"] == "ghost" for w in orphan_warnings)


def test_bom_sheet_metal_area_is_flat_pattern(tmp_path):
    """A bent part's BOM area is the unfolded flat-pattern area, not the largest
    face of the folded solid."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from cadx import publish_part_meta, publish_sheet_metal
from cadx.sheetmetal import bend


def build(params):
    part = bend(40, 25, angle_deg=90, inside_radius=3, k_factor=0.44, thickness=3, width=30)
    publish_sheet_metal("bracket", part)
    publish_part_meta("bracket", vendor="SendCutSend", qty=1)
    return part.folded
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", "design.py", "--params", "params.yaml"))
    run_dir = tmp_path / payload["artifact_dir"]

    parse_stdout_json(run_cadx(tmp_path, "bom", str(run_dir)))
    bom = json.loads((run_dir / "bom.json").read_text(encoding="utf-8"))
    bracket = next(row for row in bom["rows"] if row["label"] == "bracket")

    # developed_length = 40 + (pi/180)*90*(3 + 0.44*3) + 25 ~= 71.786; flat area
    # ~= 71.786 * 30 ~= 2153.6 mm^2, far above the 1200 mm^2 base-flange face.
    developed = 40 + (math.pi / 180.0) * 90.0 * (3 + 0.44 * 3) + 25
    assert bracket["area_mm2"] == pytest.approx(developed * 30, rel=1e-3)
    assert bracket["area_mm2"] > 1500


def test_bom_multi_vendor_grouping(tmp_path):
    """Rows group by vendor (including an unassigned vendor) with summed quantities,
    and a multi-object run only counts source-tagged holes."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from cadx import publish, publish_part_meta


def _block(mx, my):
    return {
        "bbox": {"min": [0, 0, 0], "max": [mx, my, 2]},
        "mass_properties": {"volume": mx * my * 2},
        "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
    }


def build(params):
    publish("a", _block(10, 10), role="part")
    publish("b", _block(20, 10), role="part")
    publish("c", _block(5, 5), role="part")
    publish_part_meta("a", vendor="AcmeMetals", qty=2)
    publish_part_meta("b", vendor="AcmeMetals", qty=1)
    # part c declares no metadata, so it groups under an unassigned vendor.
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", "design.py", "--params", "params.yaml"))
    run_dir = tmp_path / payload["artifact_dir"]

    summary = parse_stdout_json(run_cadx(tmp_path, "bom", str(run_dir)))
    assert summary["rows"] == 3
    assert summary["vendors"] == 2  # AcmeMetals + unassigned

    bom = json.loads((run_dir / "bom.json").read_text(encoding="utf-8"))
    by_vendor = {group["vendor"]: group for group in bom["vendors"]}
    assert by_vendor["AcmeMetals"]["total_qty"] == 3  # 2 + 1
    assert None in by_vendor  # part c, unassigned
    # Multi-object run with no source-tagged holes attributes none.
    assert all(row["hole_count"] == 0 for row in bom["rows"])
