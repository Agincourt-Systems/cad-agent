# ADR 0027: Appearance Materials for Shaded Renders

## Status

Accepted for implementation.

## Context

The shaded rasterizer (ADR 0011, generalized to multiple cameras by ADR 0026)
paints every triangle one hard-coded blue. Two consequences hurt exactly the
artifact people share:

1. **Assemblies read as one blob.** ADR 0023/0026 render the whole assembly,
   but every part is the same color, so a screenshot cannot show where the
   bracket ends and the fastener begins.
2. **Nothing looks like what it is.** A README hero shot of a steel bracket
   with black-oxide bolts and an acrylic window renders as uniform blue.
   The user explicitly wants appearance-only materials — carbon fiber,
   steel, aluminum, fasteners, lens glass — for good-looking screenshots,
   with no simulation implied.

Two structural facts shape the design. The combined ``assembly.stl`` has no
part boundaries, so per-part color requires compositing from the *per-part*
STL exports (identical geometry — placements are baked into each part's
export — merged into one global painter's-order draw). And both ADR 0011/0026
tests and the contact-sheet tests pin today's output statistically
(blue-dominant, legacy formula ``0.35 + 0.65·(n·light)``), so the default
appearance must reproduce the legacy shading *exactly*.

## Decision

### A materials table, not a rendering engine

New module ``cadx.materials``:

- ``MATERIALS``: named presets — metals (``steel``, ``stainless_steel``,
  ``aluminum``, ``titanium``, ``brass``, ``copper``, ``gold``,
  ``zinc_plated``), fastener/finish colors (``black_oxide``,
  ``anodized_black``/``_red``/``_blue``), ``carbon_fiber`` (two-tone weave
  impression), ``glass`` (translucent, for lenses/windows), ``rubber``, and
  ``plastic_<color>`` variants. Each preset is a small shading spec:
  ``color``, ``ambient``, ``diffuse``, ``specular``, ``shininess``, optional
  ``two_tone`` (secondary color hashed per-facet — reads as a weave at facet
  scale) and ``alpha`` (translucency).
- ``resolve_appearance(value)``: a preset name or a ``#rrggbb`` hex literal
  (hex gets the legacy diffuse-only spec). Unknown names resolve to ``None``
  so callers degrade with a warning, never a crash.
- ``material_for_part_meta(material)``: fuzzy substring mapping so the BOM
  channel's real-world strings ("6061-T6 Aluminum", "304 Stainless Steel")
  pick the right preset for free (longest/most-specific match first, so
  stainless wins over steel).
- ``DEFAULT_PALETTE``: what undeclared parts get, cycled by part index. Its
  first entry is the legacy blue with the legacy diffuse-only spec — a
  single-part render with no declarations is **pixel-identical to today** —
  and later entries are distinct hues with the same legacy spec, so a bare
  multi-part assembly finally renders with distinguishable parts.

### Appearance is declared where parts are published

Resolution order per part, most explicit wins:

1. ``publish(label, obj, appearance="steel")`` — rides the existing
   ``**metadata`` channel (no signature change) and is therefore already
   recorded on the spatial object.
2. ``publish_part_meta(material=...)`` via the fuzzy mapping — the BOM
   metadata most designs already declare.
3. ``DEFAULT_PALETTE[index]``.

An unknown declared appearance falls back to the palette with an
``appearance_unknown`` warning in the render manifest / shots payload.

### The rasterizer composites per-part batches

- A shared ``_render_shaded(batches, target, size, project)`` merges each
  part's triangles (tagged with its material spec) into one global
  painter's-order sort, then shades per material: legacy diffuse term plus a
  Blinn-style specular highlight toward the camera (each ``SHADED_CAMERAS``
  projector knows its view vector; specular 0 reduces exactly to the legacy
  formula). ``two_tone`` picks the alternate color by a deterministic hash of
  the facet centroid; ``alpha`` triangles are alpha-composited in paint order
  so glass shows what sits behind it. The canvas stays RGB when every batch
  is opaque, keeping legacy byte-stability.
- ``_render_stl_shaded`` (public to tests since ADR 0026) keeps its exact
  signature and becomes a one-batch wrapper over the composite path.
- ``render`` and ``shots`` both build batches from the per-part STL exports
  (falling back to the primary STL when a run has no per-part exports). The
  pinned payload fields — raster record/shots ``source`` and ``label``
  naming ``assembly.stl`` — are unchanged: the assembly STL remains the
  canonical single-file statement of what was drawn; the records additionally
  gain a ``parts`` list naming each part's resolved appearance.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_materials_presets_and_hex_resolution`: core presets exist with their
  distinguishing fields (``carbon_fiber.two_tone``, ``glass.alpha`` < 255),
  ``#ff8800`` resolves to that color with the legacy spec, unknown names
  resolve to ``None``. Fails today: no ``cadx.materials`` module.
- `test_part_meta_material_maps_to_presets`: "6061-T6 Aluminum" → aluminum,
  "304 Stainless Steel" → stainless (not plain steel), "carbon fiber sheet"
  → carbon_fiber, unmapped strings → ``None``.
- `test_two_tone_varies_by_facet`: same normal, different centroids → the
  carbon-fiber facet color differs deterministically.
- `test_glass_alpha_blends_over_background`: a translucent triangle painted
  over an opaque one yields a blend of both colors, not either pure color
  (drives ``_render_shaded`` directly with synthetic batches — no kernel).
- `test_declared_appearances_color_the_render` (kernel, flagship): a
  two-part run with ``appearance="#ff0000"`` and ``appearance="steel"``
  renders reddish and neutral-gray pixel populations, and the raster record's
  ``parts`` names both appearances.
- `test_shots_render_materials` (kernel): a non-iso camera shows the same
  declared colors — materials apply to every view.
- `test_unknown_appearance_warns_and_falls_back` (kernel): appearance
  ``"unobtanium"`` renders via palette with an ``appearance_unknown``
  warning in the manifest.
- `test_undeclared_assembly_gets_distinct_palette_colors` (kernel): a bare
  two-part run shows legacy-blue and palette-orange pixel populations —
  parts are distinguishable with zero declarations.
- Existing ADR 0011/0023/0026 render tests pass unchanged (single-part
  default output pixel-identical; pinned ``source``/``label`` fields kept).

## Consequences

- Screenshot-bearing artifacts (shaded views, shots, the contact sheet that
  embeds them) show assemblies as visually distinct, material-plausible
  parts, with zero required authoring: palette by default, one keyword for
  intent, and BOM metadata already implies the right look for free.
- The appearance vocabulary is a data table — adding a preset is one dict
  entry, and agents can discover valid names from ``cadx.materials`` or the
  README list. Appearance is recorded in run artifacts, so downstream
  consumers (e.g. a future glTF material pass) can reuse the declaration.
- Deliberately out of scope: physically-based rendering, textures/UV maps,
  per-face appearance within one part, and coloring the exported GLB — each
  can layer on the same declaration channel later.

## After Action Report

All nine success-criteria tests were red before implementation. One
correction during green was to a *test*, not the code: the side camera faces
the boxes' ambient-lit flank (the fixed light vector sits on −Y), so declared
red shades to (89, 0, 0) there and the pixel predicate needed view-appropriate
thresholds — a useful reminder that material color and lit color differ per
camera.

One addition came from looking at the actual output rather than the tests: a
showcase render (aluminum plate via ``part_meta``, carbon frame, steel
barrel, glass lens, black-oxide bolts, brass knob) showed cylinder flanks
striped navy, because every facet still drew the fixed legacy outline color.
Presets now carry a darkened ``outline`` of their own color; the legacy spec,
palette, and hex appearances keep the original ``(32, 54, 72)`` so undeclared
output stays byte-identical. The re-rendered showcase reads correctly —
metals as metals, the lens visibly translucent over the barrel.

Full suite green on final code (see LOG for the count; 9 new tests). The
pinned ADR 0011/0023/0026 statistical and payload contracts all held without
modification: single-part default output is pixel-identical, and
``source``/``label`` still name the primary STL while pixels composite from
the per-part exports.

Deferred as decided: PBR/textures/UV, per-face appearance, and glTF material
export — the appearance declaration is recorded in run artifacts, so a
future glTF pass can consume it without a new authoring channel.
