"""ADR 0023: combined assembly export and multi-part rendering.

A placed multi-part run must yield one combined ``assembly.step``/``.stl``/
``.glb`` artifact (records flagged ``assembly: true``), the renderer must draw
the whole assembly instead of whichever part exported first, the inspector
must not re-detect every feature from the combined STEP, and the contact-sheet
summary must describe the assembly extent. Single-part runs stay byte-stable.
"""

import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest


pytest.importorskip("build123d")
pytest.importorskip("PIL")


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke cadx through its public CLI."""

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
    """Parse successful cadx JSON output."""

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def run_two_part_model(tmp_path: Path) -> Path:
    """Run a real two-part placed design and return its run directory."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import Box, Location
from cadx import publish


def build(params):
    publish("base", Box(20, 20, 5), role="final")
    publish("top", Box(20, 20, 5), placement=Location((0, 0, 20)))
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design)))
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


def assembly_records(diagnostics: dict) -> list[dict]:
    """Export records carrying the ADR 0023 assembly flag."""

    return [export for export in diagnostics.get("exports", []) if export.get("assembly")]


def test_multi_part_run_exports_combined_assembly(tmp_path):
    """A placed two-part run yields flagged assembly exports, undoubled
    feature detection, and assembly-labeled rendered views."""

    from build123d import import_step

    run_dir = run_two_part_model(tmp_path)
    diagnostics = json.loads((run_dir / "diagnostics.json").read_text(encoding="utf-8"))

    flagged = assembly_records(diagnostics)
    assert sorted(record["format"] for record in flagged) == ["glb", "step", "stl"]
    assert all(record["label"] == "assembly" for record in flagged)

    # The combined STEP holds both placed solids: one per part, with the
    # placement baked in. Algebra-mode boxes are origin-centered, so the base
    # spans z -2.5..2.5 and the located top spans z 17.5..22.5.
    assembly_step = run_dir / "assembly.step"
    assert assembly_step.is_file()
    shape = import_step(str(assembly_step))
    assert len(shape.solids()) == 2
    assert shape.bounding_box().max.Z == pytest.approx(22.5, abs=1e-6)

    # Feature detection must come from the per-part exports only: two boxes
    # have 12 planar faces total. Ingesting the assembly STEP too would
    # double that to 24 under a bogus source object.
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    datums = [feature for feature in spatial["features"] if feature["kind"] == "planar_datum"]
    assert len(datums) == 12
    assert not any(feature.get("source_object") == "obj.assembly" for feature in spatial["features"])

    # Rendering draws the assembly, and says so in the manifest.
    parse_stdout_json(run_cadx(tmp_path, "render", str(run_dir)))
    manifest = json.loads((run_dir / "views" / "render_manifest.json").read_text(encoding="utf-8"))
    assert manifest["views"], "expected projected views"
    assert all(view["label"] == "assembly" for view in manifest["views"])
    assert manifest["rasters"][0]["label"] == "assembly"
    # The shaded raster is fed by the assembly STL, which carries both parts'
    # triangles (12 per box).
    stl_record = next(record for record in flagged if record["format"] == "stl")
    # Export records are cwd-relative (the project dir the CLI ran in).
    data = (tmp_path / stl_record["path"]).read_bytes()
    assert struct.unpack_from("<I", data, 80)[0] == 24


def write_part_and_assembly_steps(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Write one-part and two-part STEP/STL files for fabricated run dirs."""

    from build123d import Box, Compound, Location, export_step, export_stl

    part = Box(10, 10, 10)
    placed = Box(10, 10, 10).located(Location((0, 0, 20)))
    combined = Compound(children=[part, placed])
    part_step = tmp_path / "part.step"
    assembly_step = tmp_path / "assembly.step"
    part_stl = tmp_path / "part.stl"
    assembly_stl = tmp_path / "assembly.stl"
    export_step(part, str(part_step))
    export_step(combined, str(assembly_step))
    export_stl(part, str(part_stl))
    export_stl(combined, str(assembly_stl))
    return part_step, assembly_step, part_stl, assembly_stl


def fabricate_run_dir(tmp_path: Path, exports: list[dict]) -> Path:
    """Create a minimal run directory around the given export records."""

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    objects = [
        {
            "id": "obj.part",
            "label": "part",
            "role": "final",
            "bbox": {"min": [-5, -5, -5], "max": [5, 5, 5], "size": [10, 10, 10]},
        },
        {
            "id": "obj.other",
            "label": "other",
            "role": "part",
            "bbox": {"min": [-5, -5, 15], "max": [5, 5, 25], "size": [10, 10, 10]},
        },
    ]
    diagnostics = {
        "schema_version": "1.0",
        "status": "ok",
        "units": "mm",
        "published": objects,
        "features": [],
        "warnings": [],
        "errors": [],
        "exports": exports,
    }
    (run_dir / "diagnostics.json").write_text(json.dumps(diagnostics), encoding="utf-8")
    spatial = {"schema_version": "1.0", "units": "mm", "objects": objects, "features": []}
    (run_dir / "spatial.json").write_text(json.dumps(spatial), encoding="utf-8")
    return run_dir


def test_inspect_ignores_assembly_export_for_feature_detection(tmp_path):
    """The combined STEP is excluded from detection; per-part exports feed it."""

    from cadx.inspector import inspect_run

    part_step, assembly_step, _, _ = write_part_and_assembly_steps(tmp_path)
    run_dir = fabricate_run_dir(
        tmp_path,
        [
            {"label": "part", "format": "step", "path": str(part_step), "units": "mm"},
            {
                "label": "assembly",
                "format": "step",
                "path": str(assembly_step),
                "units": "mm",
                "assembly": True,
            },
        ],
    )

    inspect_run(run_dir)
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))

    # One box: 6 planar datums. Ingesting the assembly STEP too would add 12
    # more (both boxes again) under source_object obj.assembly.
    datums = [feature for feature in spatial["features"] if feature["kind"] == "planar_datum"]
    assert len(datums) == 6
    assert all(feature["source_object"] == "obj.part" for feature in datums)


def test_render_prefers_assembly_export(tmp_path):
    """Projections and the shaded raster come from the assembly artifacts."""

    from cadx.renderer import render_run

    part_step, assembly_step, part_stl, assembly_stl = write_part_and_assembly_steps(tmp_path)
    run_dir = fabricate_run_dir(
        tmp_path,
        [
            {"label": "part", "format": "step", "path": str(part_step), "units": "mm"},
            {"label": "part", "format": "stl", "path": str(part_stl), "units": "mm"},
            {
                "label": "assembly",
                "format": "step",
                "path": str(assembly_step),
                "units": "mm",
                "assembly": True,
            },
            {
                "label": "assembly",
                "format": "stl",
                "path": str(assembly_stl),
                "units": "mm",
                "assembly": True,
            },
        ],
    )

    payload = render_run(run_dir)

    assert payload["status"] == "ok"
    manifest = json.loads((run_dir / "views" / "render_manifest.json").read_text(encoding="utf-8"))
    assert manifest["views"], "expected projected views"
    assert all(view["label"] == "assembly" for view in manifest["views"])
    assert all(view["source"] == str(assembly_step) for view in manifest["views"])
    assert manifest["rasters"][0]["label"] == "assembly"
    assert manifest["rasters"][0]["source"] == str(assembly_stl)


def test_summary_line_reports_assembly_extent():
    """Single-object text is unchanged; multi-object reports the union bbox."""

    from cadx.renderer import _summary_line

    single = {
        "units": "mm",
        "objects": [
            {
                "label": "part",
                "bbox": {"min": [0, 0, 0], "max": [10, 10, 10], "size": [10, 10, 10]},
                "topology": {"faces": 6, "edges": 12},
            }
        ],
        "features": [],
    }
    assert _summary_line(single) == "units=mm | objects=1 | features=0 | bbox=10 x 10 x 10 | faces=6 | edges=12"

    double = {
        "units": "mm",
        "objects": [
            {
                "label": "part",
                "bbox": {"min": [0, 0, 0], "max": [10, 10, 10], "size": [10, 10, 10]},
            },
            {
                "label": "other",
                "bbox": {"min": [0, 0, 20], "max": [10, 10, 30], "size": [10, 10, 10]},
            },
        ],
        "features": [],
    }
    line = _summary_line(double)
    assert "objects=2" in line
    assert "assembly_bbox=10 x 10 x 30" in line


def test_part_labeled_assembly_skips_combined_export(tmp_path):
    """A part named 'assembly' is never clobbered by the combined export."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import Box, Location
from cadx import publish


def build(params):
    publish("assembly", Box(20, 20, 5), role="final")
    publish("top", Box(20, 20, 5), placement=Location((0, 0, 20)))
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design)))
    run_dir = tmp_path / payload["artifact_dir"]
    diagnostics = json.loads((run_dir / "diagnostics.json").read_text(encoding="utf-8"))

    assert assembly_records(diagnostics) == []
    assert any(
        warning["type"] == "assembly_export_skipped" for warning in diagnostics["warnings"]
    )
    # The part's own exports are intact: exactly one STEP record labeled
    # 'assembly', unflagged, and the file holds that single part.
    from build123d import import_step

    step_records = [
        export
        for export in diagnostics["exports"]
        if export["format"] == "step" and export["label"] == "assembly"
    ]
    assert len(step_records) == 1
    # Export records are cwd-relative (the project dir the CLI ran in).
    assert len(import_step(str(tmp_path / step_records[0]["path"])).solids()) == 1


def test_single_part_run_keeps_export_contract(tmp_path):
    """Non-regression pin: single-part runs gain no assembly artifacts."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import Box
from cadx import publish


def build(params):
    publish("box", Box(20, 20, 5), role="final")
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design)))
    run_dir = tmp_path / payload["artifact_dir"]
    diagnostics = json.loads((run_dir / "diagnostics.json").read_text(encoding="utf-8"))

    assert assembly_records(diagnostics) == []
    assert not (run_dir / "assembly.step").exists()
    assert not (run_dir / "assembly.glb").exists()
