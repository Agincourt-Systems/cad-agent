"""Bill-of-materials aggregation.

``cadx bom <run_dir>`` joins the purchasing metadata a design declares via
``publish_part_meta`` with geometry facts derived from the run's artifacts
(flat-pattern area from the STEP export, bounding box and hole count from
``spatial.json``) into deterministic ``bom.csv`` and ``bom.json`` files grouped by
vendor with totals. It is a read-only aggregator over an existing run directory,
so it never re-executes the design.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from cadx.files import read_json, write_json


# Fixed column order so ``bom.csv`` is byte-stable across runs and platforms.
_COLUMNS = (
    "vendor",
    "label",
    "part_number",
    "material",
    "thickness_mm",
    "finish",
    "process",
    "qty",
    "area_mm2",
    "bbox_x_mm",
    "bbox_y_mm",
    "bbox_z_mm",
    "hole_count",
    "unit_cost",
    "ext_cost",
    "source_url",
)


def _resolve_export_path(run_dir: Path, export_path: str) -> Path:
    """Resolve a recorded export path from the current process."""

    path = Path(export_path)
    if path.exists() or path.is_absolute():
        return path
    return run_dir / path.name


def _step_index(diagnostics: dict[str, Any], run_dir: Path) -> dict[str, Path]:
    """Map published labels to their STEP export paths."""

    return {
        export["label"]: _resolve_export_path(run_dir, export["path"])
        for export in diagnostics.get("exports", [])
        if export.get("format") == "step"
    }


def _dxf_area_index(diagnostics: dict[str, Any]) -> dict[str, float]:
    """Map labels to the flat-pattern area recorded on their DXF export.

    The DXF exporter records the true flat (developed) area, which for a
    sheet-metal part is the unfolded blank — not the largest face of the folded
    solid. Preferring it keeps the quoted area correct for both flat and bent
    parts.
    """

    return {
        export["label"]: export["area_mm2"]
        for export in diagnostics.get("exports", [])
        if export.get("format") == "dxf" and export.get("area_mm2") is not None and export.get("label")
    }


def _flat_area(step_path: Path) -> float | None:
    """Largest planar-face area (the flat-pattern area) of a STEP solid, or ``None``.

    For a constant-thickness sheet part the largest planar face is the flat cut
    profile, which is what a laser shop quotes on. Any failure (missing kernel,
    unreadable STEP, no planar face) returns ``None`` so the BOM degrades rather
    than crashes.
    """

    try:
        from build123d import import_step

        shape = import_step(str(step_path))
        planar = [face.area for face in shape.faces() if str(getattr(face, "geom_type", "")).endswith("PLANE")]
        return float(max(planar)) if planar else None
    except Exception:
        return None


def _hole_count(label: str, features: list[dict[str, Any]], single_object: bool) -> int:
    """Count cylindrical holes attributed to the object.

    Detected features always carry a ``source_object``; an explicit publication
    carries none, so when a run has a single object any unsourced hole is
    attributed to it. Multi-object runs only count source-tagged holes.
    """

    source = f"obj.{label}"
    count = 0
    for feature in features:
        if feature.get("kind") != "cylindrical_hole":
            continue
        feature_source = feature.get("source_object")
        if feature_source == source or (feature_source is None and single_object):
            count += 1
    return count


def _row_for(obj: dict[str, Any], meta: dict[str, Any], area: float | None, hole_count: int) -> dict[str, Any]:
    """Build one BOM row from a spatial object joined with its part metadata."""

    qty_value = meta.get("qty")
    qty = 1 if qty_value is None else int(qty_value)  # honor an explicit 0; only default when absent
    unit_cost = meta.get("unit_cost")
    ext_cost = qty * float(unit_cost) if unit_cost is not None else None
    size = obj.get("bbox", {}).get("size")
    bbox_mm = [float(component) for component in size] if size else None
    return {
        "vendor": meta.get("vendor"),
        "label": obj["label"],
        "part_number": meta.get("part_number"),
        "material": meta.get("material"),
        "thickness_mm": meta.get("thickness_mm"),
        "finish": meta.get("finish"),
        "process": meta.get("process"),
        "qty": qty,
        "area_mm2": area,
        "bbox_mm": bbox_mm,
        "hole_count": hole_count,
        "unit_cost": unit_cost,
        "ext_cost": ext_cost,
        "source_url": meta.get("source_url"),
    }


def _vendor_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group rows by vendor with per-vendor quantity, area, and cost totals."""

    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row["vendor"] or ""
        group = groups.setdefault(
            key,
            {"vendor": row["vendor"], "total_qty": 0, "total_area_mm2": 0.0, "total_ext_cost": 0.0},
        )
        group["total_qty"] += row["qty"]
        if row["area_mm2"] is not None:
            group["total_area_mm2"] += row["area_mm2"] * row["qty"]
        if row["ext_cost"] is not None:
            group["total_ext_cost"] += row["ext_cost"]
    return [groups[key] for key in sorted(groups)]


def _csv_value(row: dict[str, Any], column: str) -> Any:
    """Resolve a CSV cell value, splitting the bbox vector into its components."""

    if column in ("bbox_x_mm", "bbox_y_mm", "bbox_z_mm"):
        bbox = row["bbox_mm"]
        if not bbox:
            return None
        return bbox[{"bbox_x_mm": 0, "bbox_y_mm": 1, "bbox_z_mm": 2}[column]]
    return row.get(column)


def _write_csv(run_dir: Path, rows: list[dict[str, Any]]) -> Path:
    """Write a deterministic, fixed-column ``bom.csv``.

    ``newline=""`` plus ``lineterminator="\\n"`` defeats the csv module's
    platform-dependent ``\\r\\n`` default so re-running yields byte-identical
    output; ``None`` renders as the empty string.
    """

    path = run_dir / "bom.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(_COLUMNS)
        for row in rows:
            writer.writerow(["" if (value := _csv_value(row, column)) is None else str(value) for column in _COLUMNS])
    return path


def build_bom(run_dir: Path) -> dict[str, Any]:
    """Aggregate part metadata and derived geometry into ``bom.csv``/``bom.json``."""

    run_dir = Path(run_dir)
    diagnostics = read_json(run_dir / "diagnostics.json")
    spatial = read_json(run_dir / "spatial.json")
    objects = spatial.get("objects", [])
    features = spatial.get("features", [])
    meta_index = {record["label"]: record for record in diagnostics.get("part_meta", [])}
    step_index = _step_index(diagnostics, run_dir)
    dxf_area = _dxf_area_index(diagnostics)
    single_object = len(objects) == 1

    warnings: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for obj in objects:
        label = obj["label"]
        # Prefer the recorded flat-pattern area; fall back to the largest planar
        # face of the STEP solid only when no DXF area was recorded.
        area = dxf_area.get(label)
        if area is None and label in step_index:
            area = _flat_area(step_index[label])
        if area is None:
            warnings.append({"type": "bom_area_unavailable", "label": label, "message": "no flat-pattern area"})
        rows.append(_row_for(obj, meta_index.get(label, {}), area, _hole_count(label, features, single_object)))

    # Surface metadata that names a part which was never published, so a typo'd
    # label is not silently dropped from the order.
    published_labels = {obj["label"] for obj in objects}
    for label in sorted(meta_index.keys() - published_labels):
        warnings.append({"type": "bom_orphan_part_meta", "label": label, "message": "no published object"})

    rows.sort(key=lambda row: ((row["vendor"] or ""), row["label"]))
    vendors = _vendor_groups(rows)
    totals = {
        "qty": sum(row["qty"] for row in rows),
        "area_mm2": float(sum(row["area_mm2"] * row["qty"] for row in rows if row["area_mm2"] is not None)),
        "ext_cost": float(sum(row["ext_cost"] for row in rows if row["ext_cost"] is not None)),
    }

    csv_path = _write_csv(run_dir, rows)
    json_path = run_dir / "bom.json"
    write_json(
        json_path,
        {
            "schema_version": "1.0",
            "units": "mm",
            "rows": rows,
            "vendors": vendors,
            "totals": totals,
            "warnings": warnings,
        },
    )
    return {
        "status": "ok",
        "rows": len(rows),
        "vendors": len(vendors),
        "bom_csv_path": str(csv_path),
        "bom_json_path": str(json_path),
        "warnings": warnings,
    }
