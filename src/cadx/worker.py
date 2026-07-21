"""Subprocess worker for executing design Python.

The parent `cadx run` command owns the stable CLI contract. This module is the
unsafe execution boundary: it imports user CAD code, captures publications,
exports artifacts, writes diagnostics, and exits with a simple status code.
"""

from __future__ import annotations

import argparse
import traceback
from pathlib import Path
from typing import Any

from cadx.files import load_yaml, write_json
from cadx.runner import (
    _apply_material_density,
    _auto_export_flats,
    _execute_design,
    _export_assembly,
    _export_bend_table,
    _export_build123d_object,
    _export_flats,
    _export_sheet_metal,
    _normalize_published,
    _resolve_mates,
    _runtime_metadata,
)


def _write_error_diagnostics(
    run_dir: Path,
    source_path: Path,
    params: dict[str, Any],
    exc: BaseException,
) -> None:
    """Persist structured failure details for the parent process."""

    write_json(
        run_dir / "diagnostics.json",
        {
            "schema_version": "1.0",
            "status": "error",
            "units": "mm",
            "runtime": _runtime_metadata(),
            "source": str(source_path),
            "params": params,
            "published": [],
            "features": [],
            "part_meta": [],
            "errors": [
                {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                }
            ],
            "warnings": [],
            "exports": [],
        },
    )


def execute_worker(source_path: Path, run_dir: Path) -> int:
    """Execute a design source inside the worker process."""

    params = load_yaml(run_dir / "params.resolved.yaml")
    try:
        raw_registry = _execute_design(source_path, params)
        # ADR 0024: resolve declarative mates into placements before any
        # normalization or export observes the geometry.
        mate_warnings = _resolve_mates(raw_registry["published"])
        published = [_normalize_published(entry) for entry in raw_registry["published"]]
        # ADR 0035: resolve declared materials to densities and per-part masses
        # before diagnostics is written, so the implied densities reach the
        # assembly center-of-mass aggregation performed by inspect_run.
        # ADR 0045: the same pass returns material_unresolved warnings for any
        # material that did not resolve (and had no explicit density); fold them
        # into the run's warnings channel so a gate can assert on them.
        material_warnings = _apply_material_density(published, raw_registry.get("part_meta", []))
        exports: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = list(mate_warnings)
        warnings.extend(material_warnings)
        for entry in raw_registry["published"]:
            entry_exports, entry_warnings = _export_build123d_object(entry, run_dir)
            exports.extend(entry_exports)
            warnings.extend(entry_warnings)

        # ADR 0013: emit 2D DXF flat patterns for laser/waterjet fabrication.
        # Explicit publish_flat profiles take precedence; remaining published
        # solids of uniform thickness are auto-flattened for free.
        flats = raw_registry.get("flats", [])
        explicit_flat_labels = {flat["label"] for flat in flats}
        flat_exports, flat_warnings = _export_flats(flats, run_dir)
        auto_exports, auto_warnings = _auto_export_flats(
            raw_registry["published"], explicit_flat_labels, run_dir
        )
        exports.extend(flat_exports)
        exports.extend(auto_exports)
        warnings.extend(flat_warnings)
        warnings.extend(auto_warnings)

        # ADR 0016: sheet-metal parts emit a combined cut+bend DXF each, plus one
        # aggregated bends.json bend table for the whole run. Their internal
        # "flat" key already made auto-flatten skip them.
        sheet_metal_entries = [entry for entry in raw_registry["published"] if entry.get("flat")]
        for entry in sheet_metal_entries:
            sheet_exports, sheet_warnings = _export_sheet_metal(entry, run_dir)
            exports.extend(sheet_exports)
            warnings.extend(sheet_warnings)
        bend_table_exports, bend_table_warnings = _export_bend_table(sheet_metal_entries, run_dir)
        exports.extend(bend_table_exports)
        warnings.extend(bend_table_warnings)

        # ADR 0023: one combined artifact of the whole placed assembly for
        # viewing and downstream consumers; the per-part exports above remain
        # the fabrication truth.
        assembly_exports, assembly_warnings = _export_assembly(raw_registry["published"], run_dir)
        exports.extend(assembly_exports)
        warnings.extend(assembly_warnings)

        diagnostics = {
            "schema_version": "1.0",
            "status": "ok",
            "units": "mm",
            "runtime": _runtime_metadata(),
            "source": str(source_path),
            "params": params,
            "published": published,
            "features": raw_registry["features"],
            "part_meta": raw_registry.get("part_meta", []),
            "errors": [],
            "warnings": warnings,
            "exports": exports,
        }
        # ADR 0046: persist run-level assembly options so inspect_run reads them
        # whether it runs now or later via `cadx inspect`. Written only when the
        # design declared some, so a run without them stays byte-identical.
        assembly_opts = raw_registry.get("assembly_options") or {}
        if assembly_opts:
            diagnostics["assembly_options"] = assembly_opts
        write_json(run_dir / "diagnostics.json", diagnostics)

        from cadx.inspector import inspect_run

        inspect_run(run_dir)
        return 0
    except Exception as exc:
        _write_error_diagnostics(run_dir, source_path, params, exc)
        return 1


def main(argv: list[str] | None = None) -> int:
    """Parse worker arguments and execute the design."""

    parser = argparse.ArgumentParser(prog="cadx.worker")
    parser.add_argument("source", type=Path)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    return execute_worker(args.source, args.run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
