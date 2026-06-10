# ADR 0003: Real View Rendering

## Status

Accepted for implementation.

## Context

ADR 0001 created a placeholder contact sheet from spatial metrics. ADR 0002
added real `build123d` execution and exact CAD exports. The next loop-closing
gap is visual: the agent should be able to inspect deterministic rendered views
that come from the actual CAD artifact, not from a schematic rectangle.

Rendering should not re-execute arbitrary design code when a STEP export is
already available. Loading the STEP artifact gives a stable representation of
the run result and keeps rendering tied to the same artifact a downstream CAD
tool would consume.

## Decision

Extend `cadx render` to:

- Load STEP exports from the run directory using `build123d.import_step`.
- Generate hidden-line SVG projections for ISO, top, front, and right views.
- Generate section SVG projections through the model center on XY, XZ, and YZ
  planes.
- Keep the PNG contact sheet, but make it reference the real SVG views.
- Write `views/render_manifest.json` so agents can discover visual artifacts
  without relying on filename conventions alone.

The first section implementation uses principal planes through the origin,
which matches centered build123d primitives and can be extended to explicit
section origins later.

## Success Criteria

- A test using a real `build123d` box fails before implementation and passes
  after implementation.
- `cadx render` produces `iso.svg`, `top.svg`, `front.svg`, and `right.svg`.
- `cadx render` produces `section_xy.svg`, `section_xz.svg`, and
  `section_yz.svg`.
- The SVGs contain visible projected geometry.
- The render payload and manifest list generated view and section artifacts.
- Existing ADR 0001 and ADR 0002 tests continue to pass.

## Consequences

- Agents gain a deterministic visual channel without opening an interactive
  viewer.
- Rendering is based on exported STEP artifacts, so it cannot silently diverge
  from the exact CAD file produced by the run.
- PNG rasterization remains a simplified contact sheet until a dedicated SVG
  rasterization or shaded-rendering backend is added, but the actual visual
  geometry is available as SVG.

## After Action Report

The red-state rendering test first failed because `cadx render` returned only
the PNG contact sheet and had no manifest. After projection rendering was
implemented, the test was tightened to require section artifacts; that failed
because the manifest had no `sections` key.

The final implementation loads the first STEP export with `build123d.import_step`,
generates ISO/top/front/right hidden-line SVG projections, intersects the shape
with XY/XZ/YZ principal planes, projects those sections to SVG, and writes a
render manifest. The focused rendering test passed, and the full suite passed
with 5 tests.
