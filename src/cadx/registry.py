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


def clear_registry() -> None:
    """Reset the registry before each design execution."""

    _PUBLISHED.clear()
    _FEATURES.clear()
    _FLATS.clear()


def publish(label: str, obj: Any, role: str = "part", placement: Any = None, **metadata: Any) -> None:
    """Publish a named object for inspection and export.

    ``obj`` may be a real build123d shape or a dictionary containing already
    normalized geometry facts. The dictionary form keeps unit tests independent
    from a full CAD kernel installation.

    ``placement`` is an optional build123d ``Location`` that positions the part
    in a common assembly frame. When supplied, the harness applies it before
    computing bounding boxes, mass properties, and exports, so cross-part checks
    (hole alignment, interference) observe every part in one coordinate system.
    """

    _PUBLISHED.append({"label": label, "role": role, "object": obj, "placement": placement, "metadata": metadata})


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


def publish_feature(feature_id: str, kind: str, **properties: Any) -> None:
    """Publish a critical feature such as a hole, slot, boss, rib, or datum."""

    feature = {"id": f"feat.{feature_id}" if not feature_id.startswith("feat.") else feature_id, "kind": kind}
    feature.update(properties)
    _FEATURES.append(feature)


def snapshot_registry() -> dict[str, Any]:
    """Return a defensive copy of all publications captured this run."""

    # Published CAD objects may wrap Open Cascade handles that are expensive or
    # impossible to deepcopy. Preserve the object references for export and
    # normalization while copying the JSON-like metadata around them.
    published = [
        {
            "label": entry["label"],
            "role": entry["role"],
            "object": entry["object"],
            # The Location is carried by reference like the object: it wraps an
            # Open Cascade transform that is not deepcopy-safe.
            "placement": entry.get("placement"),
            "metadata": deepcopy(entry["metadata"]),
        }
        for entry in _PUBLISHED
    ]
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
    return {"published": published, "features": deepcopy(_FEATURES), "flats": flats}
