"""Appearance material presets for the shaded rasterizer (ADR 0027).

These are *looks*, not physics: each preset is a small shading spec the
software rasterizer understands. ``color`` is the diffuse base; ``ambient`` /
``diffuse`` weight the legacy lighting term; ``specular``/``shininess`` add a
Blinn-style highlight toward the camera (0 reduces exactly to the legacy
formula); ``two_tone`` supplies an alternate facet color chosen by a
deterministic centroid hash (reads as a weave/speckle at facet scale); and
``alpha`` below 255 makes facets translucent (lenses, windows).

Adding a preset is one dict entry. Unknown names resolve to ``None`` so
callers degrade with a warning rather than a crash.
"""

from __future__ import annotations

from typing import Any


# The exact legacy shading weights (ADR 0011): 0.35 + 0.65 * (n . light),
# no specular. Presets that keep these weights shade identically to the
# original renderer apart from their base color.
_LEGACY = {"ambient": 0.35, "diffuse": 0.65, "specular": 0.0, "shininess": 16}


def _legacy_colored(color: tuple[int, int, int]) -> dict[str, Any]:
    """A diffuse-only spec: the legacy formula with a different base color."""

    return {"color": color, **_LEGACY}


MATERIALS: dict[str, dict[str, Any]] = {
    # Bare and finished metals.
    "steel": {"outline": (53, 55, 59), "color": (118, 122, 130), "ambient": 0.30, "diffuse": 0.55, "specular": 0.45, "shininess": 24},
    "stainless_steel": {"outline": (68, 71, 73), "color": (152, 157, 162), "ambient": 0.32, "diffuse": 0.50, "specular": 0.55, "shininess": 32},
    "aluminum": {"outline": (77, 78, 81), "color": (170, 174, 180), "ambient": 0.38, "diffuse": 0.52, "specular": 0.35, "shininess": 18},
    "titanium": {"outline": (61, 59, 57), "color": (136, 131, 126), "ambient": 0.32, "diffuse": 0.55, "specular": 0.30, "shininess": 20},
    "brass": {"outline": (81, 63, 29), "color": (181, 141, 64), "ambient": 0.34, "diffuse": 0.58, "specular": 0.40, "shininess": 22},
    "copper": {"outline": (79, 47, 34), "color": (176, 104, 76), "ambient": 0.34, "diffuse": 0.58, "specular": 0.40, "shininess": 22},
    "gold": {"outline": (96, 77, 30), "color": (214, 170, 66), "ambient": 0.36, "diffuse": 0.55, "specular": 0.55, "shininess": 28},
    "zinc_plated": {"outline": (64, 68, 71), "color": (142, 152, 158), "ambient": 0.36, "diffuse": 0.52, "specular": 0.30, "shininess": 16},
    # Fastener / finish blacks.
    "black_oxide": {"outline": (18, 18, 20), "color": (40, 40, 44), "ambient": 0.24, "diffuse": 0.36, "specular": 0.50, "shininess": 30},
    "anodized_black": {"outline": (22, 23, 25), "color": (50, 52, 56), "ambient": 0.28, "diffuse": 0.45, "specular": 0.30, "shininess": 20},
    "anodized_red": {"outline": (68, 19, 21), "color": (150, 42, 46), "ambient": 0.30, "diffuse": 0.52, "specular": 0.30, "shininess": 20},
    "anodized_blue": {"outline": (21, 32, 64), "color": (46, 72, 142), "ambient": 0.30, "diffuse": 0.52, "specular": 0.30, "shininess": 20},
    # Composites and non-metals.
    "carbon_fiber": {
        "outline": (20, 21, 23),
        "color": (44, 46, 50),
        "two_tone": (68, 72, 78),
        "ambient": 0.28,
        "diffuse": 0.50,
        "specular": 0.35,
        "shininess": 26,
    },
    "glass": {"outline": (90, 101, 106), "color": (200, 225, 235), "alpha": 110, "ambient": 0.45, "diffuse": 0.35, "specular": 0.60, "shininess": 40},
    "rubber": {"outline": (19, 19, 19), "color": (42, 42, 42), "ambient": 0.30, "diffuse": 0.55, "specular": 0.05, "shininess": 4},
    "plastic_black": _legacy_colored((36, 36, 40)),
    "plastic_white": _legacy_colored((233, 233, 230)),
    "plastic_red": _legacy_colored((190, 52, 44)),
    "plastic_green": _legacy_colored((70, 150, 80)),
    "plastic_blue": _legacy_colored((52, 92, 180)),
    "plastic_orange": _legacy_colored((230, 126, 34)),
    "plastic_yellow": _legacy_colored((222, 186, 60)),
}


# What undeclared parts get, cycled by part index. The first entry is the
# legacy blue with the legacy weights, so a single-part render with no
# declarations is pixel-identical to the pre-ADR-0027 output; the rest are
# distinct hues with the same weights so bare assemblies read as parts.
DEFAULT_PALETTE: list[dict[str, Any]] = [
    {"name": "palette_blue", **_legacy_colored((66, 132, 184))},
    {"name": "palette_orange", **_legacy_colored((204, 120, 60))},
    {"name": "palette_green", **_legacy_colored((96, 160, 96))},
    {"name": "palette_purple", **_legacy_colored((150, 110, 170))},
    {"name": "palette_gold", **_legacy_colored((180, 150, 70))},
    {"name": "palette_teal", **_legacy_colored((90, 150, 160))},
]


# Substring → preset for `publish_part_meta(material=...)` strings. Ordered:
# the first hit wins, so more specific names precede their substrings
# ("stainless" before "steel").
_PART_META_HINTS: tuple[tuple[str, str], ...] = (
    ("stainless", "stainless_steel"),
    ("steel", "steel"),
    ("aluminum", "aluminum"),
    ("aluminium", "aluminum"),
    ("titanium", "titanium"),
    ("brass", "brass"),
    ("copper", "copper"),
    ("gold", "gold"),
    ("zinc", "zinc_plated"),
    ("carbon", "carbon_fiber"),
    ("glass", "glass"),
    ("acrylic", "glass"),
    ("polycarbonate", "glass"),
    ("rubber", "rubber"),
    ("nylon", "plastic_white"),
    ("abs", "plastic_black"),
    ("pla", "plastic_white"),
)


def _hex_color(value: str) -> tuple[int, int, int] | None:
    """Parse ``#rrggbb`` (or ``#rgb``) into an RGB tuple, else ``None``."""

    text = value.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def resolve_appearance(value: Any) -> tuple[str, dict[str, Any]] | None:
    """Resolve a declared appearance to ``(name, spec)``, or ``None``.

    Accepts a preset name from :data:`MATERIALS` or a hex color literal
    (which gets the legacy diffuse-only weights). ``None`` means the caller
    should fall back to the palette and warn.
    """

    if not isinstance(value, str):
        return None
    if value in MATERIALS:
        return value, MATERIALS[value]
    if value.startswith("#"):
        color = _hex_color(value)
        if color is not None:
            return value, _legacy_colored(color)
    return None


def material_for_part_meta(material: Any) -> str | None:
    """Map a BOM material string to a preset name by substring hints."""

    if not isinstance(material, str):
        return None
    lowered = material.lower()
    for hint, preset in _PART_META_HINTS:
        if hint in lowered:
            return preset
    return None
