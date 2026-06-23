# ADR 0019: Real-Geometry Contact Sheet

## Status

Accepted for implementation.

## Context

Deficiency D7 in `docs/ca-sheet-metal-fixes.md` observes that
`renderer._draw_with_pillow()` draws scaled placeholder **rectangles** as panel
stand-ins, not real geometry. True geometry already exists elsewhere in the run:
`views/shaded_iso.png` (the ADR 0011 software STL rasterizer) and the per-view
hidden-line SVG projections (ADR 0008). The contact sheet — the one artifact the
spec promises is "understandable without opening an interactive viewer" — does
not embed any of it, so a reviewer sees blue outline boxes instead of the part.

The constraint is headless rendering: there is no SVG rasterizer available
(`cairosvg` and `svglib` are both absent in this environment, verified), so the
SVG projections cannot be turned into pixels here. The shaded isometric raster is
already a PNG, so it *can* be composed directly with Pillow.

## Decision

Embed the real `views/shaded_iso.png` raster into the contact sheet's shaded
(ISO) panel instead of the placeholder rectangle, and record the composition in
`render_manifest.json`.

- `_draw_with_pillow(path, spatial, rasters=None)` gains the rendered raster
  records (the list `render_run` already builds via `_render_raster_artifacts`).
  When the shaded raster exists, it is opened, thumbnailed to the ISO panel
  interior preserving aspect ratio, and pasted centered; the frame and label are
  drawn on top. Panels with no real raster (synthetic designs, or the
  orthographic/section views that cannot be rasterized headless) keep the
  deterministic placeholder rectangle.
- The function returns a `contact_panels` list — one record per panel,
  `{label, source, path}` — where `source == "shaded_iso"` (with the resolvable
  raster path) for an embedded panel and `source is None` for a placeholder
  fallback. `render_run` folds it into the manifest under a new additive
  `contact_panels` key.

No other module changes; the `render` subcommand already calls `render_run`, and
its return contract and the `views/contact.png` path are unchanged.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_contact_sheet_uses_real_views`: for a real plate-with-holes part, the
  cropped ISO panel of `contact.png` has a non-white fraction > 0.20, more than
  15 distinct colors, and a blue-dominant non-white mean (the shaded base color).
  Fails today: the placeholder panel is ~0.06 filled with ~3 colors.
- `test_render_manifest_records_embedded_panel_source`: the manifest gains
  `contact_panels`, and the ISO panel records `source == "shaded_iso"` with a
  resolvable `shaded_iso.png` path. Fails today: the key does not exist.
- `test_contact_sheet_falls_back_for_synthetic_design`: a dict-only design (no
  STL) still renders a contact sheet; `rasters == []` and the ISO panel records
  `source is None` (the placeholder fallback).
- Existing ADR 0001–0018 tests continue to pass; the manifest gains only the
  additive `contact_panels` key and the contact-sheet path is unchanged.

## Consequences

- The contact sheet's ISO panel now shows the real shaded part, so a reviewer can
  read geometry from the single contact image as the spec intends.
- The orthographic and section panels remain placeholders because no headless SVG
  rasterizer is available; the real SVG projections still exist as standalone
  `views/*.svg` artifacts. A dimensioned per-part drawing is left as future work.
- `contact_panels` makes the composition machine-inspectable: an agent can tell
  which panels embed real geometry versus a placeholder.

## After Action Report

The red state failed as predicted: the ISO panel was a near-white placeholder
(~0.06 non-white, ~3 colors) and the manifest had no `contact_panels` key.

The implementation pastes the thumbnailed shaded raster into the ISO panel: a
900×650, ~43%-filled raster thumbnails to ~235×170 and, centered in the panel,
lifts the panel crop to ~0.29 non-white with the shading gradient's many colors
and blue-dominant mean — comfortably past the 0.20 / 15-color thresholds. Drawing
the frame and label after the paste keeps the layout identical. Both branches are
covered: the real-geometry test exercises embedding, and the synthetic dict design
(no STL → `rasters == []`) exercises the placeholder fallback and the
`source is None` record. The existing ADR 0008/0011 rendering tests, which assert
on the SVG/raster artifacts and the unchanged manifest keys, continue to pass. The
full suite passed with 90 tests (87 prior + 3 new), no regressions.
