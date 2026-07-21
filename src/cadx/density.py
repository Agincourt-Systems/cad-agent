"""Built-in material -> density table (ADR 0035).

This module answers one physical question: *given a material name, what is its
density in g/mm³?* It is deliberately separate from ``cadx.materials`` (which
maps material names to **rendering** presets, ADR 0027) because density and
appearance are independent axes — a bare 6061 bracket and a red-anodized 6061
bracket share a density but not a look, and two parts can share a look while
being different alloys. Keeping them apart stops a change to one from silently
perturbing the other.

Units are **g/mm³** throughout, chosen to match the existing
``publish(..., density=<g/mm³>)`` keyword and the ADR 0015 assembly
center-of-mass aggregation, so a looked-up density is drop-in interchangeable
with an author-supplied one (mass = volume[mm³] × density[g/mm³] = grams).

Lookup is intentionally *stricter* than the render module's loose substring
hints: a material string is normalized and matched either by its whole collapsed
form (``"6061-T6"`` → ``"6061t6"``) or by a whole alphanumeric token
(``"304"`` inside ``"304 Stainless"``). Whole-token matching is what stops
``"plastic"`` from resolving to ``"pla"`` — a substring scan would get that
wrong. The table is a hand-curated ordered list so a name that could plausibly
touch two entries resolves to the first (most specific) one deterministically.
"""

from __future__ import annotations

import re
from typing import Any, Optional


class Material:
    """One row of the density table: a canonical display key, its density in
    g/mm³, and the set of normalized aliases that resolve to it.

    ``aliases`` are pre-normalized (see :func:`_normalize`) so matching is a
    plain set membership test at lookup time. Each alias is matched two ways:
    as the whole collapsed material string, or as one whole token of it.
    """

    def __init__(self, key: str, density_g_per_mm3: float, aliases: tuple[str, ...]):
        self.key = key
        self.density = density_g_per_mm3
        self.aliases = frozenset(aliases)


# Ordered most-specific first. Densities are standard handbook values in g/cm³
# converted to g/mm³ (÷1000); see ADR 0035 for the source table. These are
# nominal room-temperature densities — temper/anneal differences are within the
# rounding here and out of scope.
_MATERIALS: tuple[Material, ...] = (
    # Aluminum alloys.
    Material("6061-T6", 0.00270, ("6061", "6061t6", "al6061", "aluminum6061", "aluminium6061")),
    Material("5052-H32", 0.00268, ("5052", "5052h32", "al5052", "aluminum5052", "aluminium5052")),
    # Steels — 304 stainless and 1018 mild/low-carbon.
    Material("304", 0.00800, ("304", "304ss", "ss304", "304stainless", "stainless304", "type304")),
    Material("1018", 0.00787, ("1018", "1018steel", "mildsteel", "carbonsteel", "lowcarbonsteel")),
    # Copper alloy.
    Material("brass", 0.00850, ("brass", "c260", "c360", "c26000", "cartridgebrass")),
    # Titanium alloy (grade 5). "titanium" alone defaults here — it is the alloy
    # the arm project means when it says titanium.
    Material("Ti-6Al-4V", 0.00443, ("ti6al4v", "ti64", "titanium", "6al4v", "grade5titanium", "titaniumgrade5")),
    # Common FDM / injection polymers.
    Material("ABS", 0.00104, ("abs",)),
    Material("PLA", 0.00124, ("pla",)),
    Material("PETG", 0.00127, ("petg", "pet-g")),
)


def _normalize(name: str) -> tuple[str, set[str]]:
    """Return ``(collapsed, tokens)`` for a raw material string.

    ``collapsed`` is the lowercased string with every non-alphanumeric character
    removed (``"Ti-6Al-4V"`` → ``"ti6al4v"``); ``tokens`` is the set of
    lowercased alphanumeric runs (``"304 Stainless"`` → ``{"304", "stainless"}``).
    Matching against both lets a canonical spelling collapse-match while a
    multi-word real-world string token-matches on its defining part.
    """

    lowered = name.lower()
    collapsed = re.sub(r"[^a-z0-9]", "", lowered)
    tokens = set(re.findall(r"[a-z0-9]+", lowered))
    return collapsed, tokens


def resolve_density(name: Any) -> Optional[tuple[str, float]]:
    """Resolve a material name to ``(canonical_key, density_g_per_mm3)``.

    Returns ``None`` for a non-string, or a name that matches no table row — the
    caller then guesses nothing (ADR 0035: an unknown material must never be
    silently assigned a density). Matching scans the curated table in order and
    returns the first row whose collapsed form equals the material's collapsed
    form, or one of whose aliases is a whole token of the material string.
    """

    if not isinstance(name, str):
        return None
    collapsed, tokens = _normalize(name)
    if not collapsed:
        return None
    for material in _MATERIALS:
        if collapsed in material.aliases or (material.aliases & tokens):
            return material.key, material.density
    return None
