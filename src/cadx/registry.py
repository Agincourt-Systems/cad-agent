"""In-process registry used while executing a design file.

``build123d`` objects are often difficult to rediscover automatically after a
script has finished because feature intent is encoded in Python control flow.
The registry gives design code an explicit, low-friction way to name final
objects and critical features.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_PUBLISHED: list[dict[str, Any]] = []
_FEATURES: list[dict[str, Any]] = []
_FLATS: list[dict[str, Any]] = []
_PART_META: list[dict[str, Any]] = []


def clear_registry() -> None:
    """Reset the registry before each design execution."""

    _PUBLISHED.clear()
    _FEATURES.clear()
    _FLATS.clear()
    _PART_META.clear()


def mate(
    to: str,
    *,
    anchor: Any = None,
    target: Any = None,
    joint: str | None = None,
    target_joint: str | None = None,
) -> dict[str, Any]:
    """Declare a rigid mate: this part's frame coincides with one on ``to``.

    Two spellings of the same primitive (ADR 0024). Explicit frames:
    ``anchor`` is a build123d ``Location`` (or bare 3-sequence) on this part
    and ``target`` one on the already-published part ``to``; the harness
    derives the placement ``parent_placement * target * anchor⁻¹``. Native
    joints: ``joint``/``target_joint`` name ``RigidJoint``s created on the two
    shapes, whose ``relative_location``s become the frames (``target_joint``
    defaults to ``joint``). The mate resolves to an ordinary placement before
    inspection and export, so every downstream stage is unchanged.
    """

    if joint is None and (anchor is None or target is None):
        raise ValueError("mate() needs either joint=... or both anchor=... and target=...")
    if joint is not None and (anchor is not None or target is not None):
        raise ValueError("mate() takes either joint names or anchor/target frames, not both")
    if joint is not None:
        return {"to": to, "joint": joint, "target_joint": target_joint or joint}
    return {"to": to, "anchor": anchor, "target": target}


def publish(
    label: str, obj: Any, role: str = "part", placement: Any = None, mate: Any = None, **metadata: Any
) -> None:
    """Publish a named object for inspection and export.

    ``obj`` may be a real build123d shape or a dictionary containing already
    normalized geometry facts. The dictionary form keeps unit tests independent
    from a full CAD kernel installation.

    ``placement`` is an optional build123d ``Location`` that positions the part
    in a common assembly frame. When supplied, the harness applies it before
    computing bounding boxes, mass properties, and exports, so cross-part checks
    (hole alignment, interference) observe every part in one coordinate system.

    ``mate`` is the declarative alternative (ADR 0024): a :func:`mate` spec
    naming another published part and a pair of frames; the harness resolves it
    into a placement. The two are mutually exclusive because a part cannot have
    both a hand-computed transform and a derived one.
    """

    if placement is not None and mate is not None:
        raise ValueError("publish() accepts either placement or mate, not both")
    entry: dict[str, Any] = {
        "label": label,
        "role": role,
        "object": obj,
        "placement": placement,
        "metadata": metadata,
    }
    if mate is not None:
        entry["mate"] = dict(mate)
    _PUBLISHED.append(entry)


def publish_flat(label: str, profile: Any, *, layer: str = "cut", thickness_mm: float | None = None, **meta: Any) -> None:
    """Publish a 2D flat profile for DXF (laser/waterjet) export.

    ``profile`` is the flat cut outline plus any interior cutout wires, expressed
    as a build123d ``Sketch``, ``Face``, or planar ``Compound``. SendCutSend and
    similar shops consume 2D vector files (DXF) for flat parts, so this channel
    exists alongside the 3D ``publish`` channel: the worker writes one
    ``<label>.dxf`` per flat publication.

    The profile is stored by reference rather than deep-copied because it wraps
    an Open Cascade handle, exactly as ``publish`` keeps the live object. Extra
    keyword metadata is preserved for downstream BOM/DFM consumers.
    """

    _FLATS.append(
        {
            "label": label,
            "profile": profile,
            "layer": layer,
            "thickness_mm": thickness_mm,
            "metadata": meta,
        }
    )


def publish_sheet_metal(label: str, part: Any, *, layer: str = "cut", role: str = "part", **metadata: Any) -> None:
    """Publish a folded sheet-metal part: 3D solid + flat pattern + bend table.

    ``part`` is a :class:`cadx.sheetmetal.SheetMetalPart`. Its folded solid is
    stored as the published object so the existing STEP/STL/GLB exports and
    spatial checks see the assembled pose, while its flat pattern, bend lines, and
    bend table travel under an internal ``flat`` key that the runner consumes to
    emit the combined cut+bend DXF and the ``bends.json`` bend table. The internal
    ``flat`` key also makes auto-flatten (ADR 0013) skip this entry, so the bend
    DXF is never overwritten by a naive flatten of the folded solid.
    """

    _PUBLISHED.append(
        {
            "label": label,
            "role": role,
            "object": part.folded,
            "placement": None,
            "metadata": metadata,
            "flat": {
                "profile": part.flat_profile,
                "layer": layer,
                "bend_lines": part.bend_lines,
                "bends": part.bends,
            },
        }
    )


def publish_feature(feature_id: str, kind: str, **properties: Any) -> None:
    """Publish a critical feature such as a hole, slot, boss, rib, or datum."""

    feature = {"id": f"feat.{feature_id}" if not feature_id.startswith("feat.") else feature_id, "kind": kind}
    feature.update(properties)
    _FEATURES.append(feature)


def publish_part_meta(
    label: str,
    *,
    vendor: str | None = None,
    material: str | None = None,
    thickness_mm: float | None = None,
    finish: str | None = None,
    qty: int = 1,
    source_url: str | None = None,
    unit_cost: float | None = None,
    part_number: str | None = None,
    process: str | None = None,
) -> None:
    """Declare purchasing/manufacturing metadata for a published part.

    The harness joins this with auto-derived geometry facts (flat-pattern area,
    bounding box, hole count) into a bill of materials via ``cadx bom``. All ten
    fields are stored explicitly — defaulting to ``None`` (or ``qty=1``) — so the
    diagnostics record shape is fixed regardless of which keywords the author
    supplied, which keeps the downstream BOM aggregation simple and total.
    """

    _PART_META.append(
        {
            "label": label,
            "vendor": vendor,
            "material": material,
            "thickness_mm": thickness_mm,
            "finish": finish,
            "qty": qty,
            "source_url": source_url,
            "unit_cost": unit_cost,
            "part_number": part_number,
            "process": process,
        }
    )


def snapshot_registry() -> dict[str, Any]:
    """Return a defensive copy of all publications captured this run."""

    # Published CAD objects may wrap Open Cascade handles that are expensive or
    # impossible to deepcopy. Preserve the object references for export and
    # normalization while copying the JSON-like metadata around them.
    published = []
    for entry in _PUBLISHED:
        snapshot_entry = {
            "label": entry["label"],
            "role": entry["role"],
            "object": entry["object"],
            # The Location is carried by reference like the object: it wraps an
            # Open Cascade transform that is not deepcopy-safe.
            "placement": entry.get("placement"),
            "metadata": deepcopy(entry["metadata"]),
        }
        # Sheet-metal publications attach a flat pattern + bend table whose
        # build123d shapes must also be carried by reference, not deepcopied.
        if entry.get("flat") is not None:
            snapshot_entry["flat"] = entry["flat"]
        # Mate specs may contain build123d Locations, carried by reference
        # like placements; the runner resolves them into placements.
        if entry.get("mate") is not None:
            snapshot_entry["mate"] = entry["mate"]
        published.append(snapshot_entry)
    # Flat profiles keep their build123d object by reference (Open Cascade
    # handles are not deepcopy-safe) while their JSON-like metadata is copied.
    flats = [
        {
            "label": entry["label"],
            "profile": entry["profile"],
            "layer": entry["layer"],
            "thickness_mm": entry["thickness_mm"],
            "metadata": deepcopy(entry["metadata"]),
        }
        for entry in _FLATS
    ]
    return {
        "published": published,
        "features": deepcopy(_FEATURES),
        "flats": flats,
        "part_meta": deepcopy(_PART_META),
    }
