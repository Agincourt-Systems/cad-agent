# ADR 0011: Shaded Raster Rendering

## Status

Accepted for implementation.

## Context

ADR 0003 added deterministic SVG projections and section views. Those are useful
for exact outlines, but agents and humans still benefit from a shaded raster
view that conveys 3D form quickly. VTK is installed through the CAD dependency
stack, but offscreen VTK rendering aborts on `fjord` because there is no X
server.

The harness already exports STL meshes. A small software rasterizer can provide
a deterministic shaded isometric screenshot from those meshes without GUI
dependencies.

## Decision

Extend `cadx render` to parse the first STL export and write
`views/shaded_iso.png`. The render manifest will list raster artifacts under a
`rasters` key. The existing SVG projections, section SVGs, and contact sheet
remain unchanged.

After implementation, add a generated CAD screenshot image under `docs/images/`
and reference it from the README.

## Success Criteria

- Tests fail before implementation because the manifest has no shaded raster.
- `cadx render` produces a nonblank shaded PNG from a real STL export.
- The render manifest lists the raster artifact.
- README includes the generated CAD output screenshot.
- Existing ADR 0001 through ADR 0010 tests continue to pass.

## Consequences

- Shaded rendering works in headless environments without a browser or X server.
- The software rasterizer is intentionally simple; it is for agent inspection,
  not photorealistic rendering.

## After Action Report

The initial red-state test failed because `render_manifest.json` had no
`rasters` key. VTK offscreen rendering was evaluated first, but it aborts on
`fjord` without an X server. The implementation therefore added a deterministic
software rasterizer that parses binary STL triangles, projects them into an
isometric camera, shades faces by normal, and writes `views/shaded_iso.png`.

While generating the README screenshot, a slotted model exposed a section
projection edge case where `build123d.intersect()` returned a `ShapeList`; the
SVG projection writer now handles both single shapes and shape lists. The README
now includes `docs/images/cad-output.png`.

The focused ADR 0011 test passed, and the full suite passed with 17 tests.
