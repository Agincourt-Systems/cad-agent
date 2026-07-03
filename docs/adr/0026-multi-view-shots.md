# ADR 0026: Multi-View Shaded Screenshots (`cadx shots`)

## Status

Accepted for implementation.

## Context

`_render_stl_shaded` (ADR 0011) is the only real, headless-safe raster the
harness produces, and it is **isometric-only**: it hard-codes `_project_iso`.
ADR 0023 made `render` shade the combined assembly STL, so the one shaded PNG
now shows the whole design — but from a single fixed angle.

That single angle is not enough when a human or agent wants to *communicate*
or *sanity-check* a shape. Downstream this bites immediately: building the
`chupacabra-configuration` airframe README, the shaded iso alone could not
show the wing planform (a top-down view) or the fin profile (a side view), so
a throwaway per-project script (`tools/render_screenshots.py`) was written to
render orthographic shaded views from `assembly.stl`. That script is
generally useful and does not belong copy-pasted in every consumer repo — it
belongs in cadx.

The existing rasterizer already has every piece needed: a binary-STL reader,
a triangle-normal shader, a painter's-order fill, and bbox auto-framing. Only
the *projection* is fixed. Generalising the projection and exposing named
cameras is a small, additive change.

## Decision

### Generalise the shaded rasterizer

- `_render_stl_shaded(stl_path, target, size=..., project=_project_iso)`
  gains a `project` callable parameter — a function
  `(x, y, z) -> (screen_x, screen_y, depth)` where `screen_y` grows downward
  (image convention) and larger `depth` is nearer the camera (painter's order
  paints far→near). The default is `_project_iso`, so **existing `render`
  output is byte-for-byte unchanged** (the call site passes no projector).
- A `SHADED_CAMERAS` registry maps camera names to projectors built from an
  orthonormal `(right, up, forward)` basis via `_orthographic_projector`:
  `screen_x = p·right`, `screen_y = -(p·up)`, `depth = p·forward`. `iso`
  stays the exact legacy `_project_iso` (not a basis approximation) so it is
  identical to today's output.
  - `iso` — legacy oblique isometric.
  - `top` — camera on +Z looking down: right +X, up +Y (plan / wing planform).
  - `side` — camera on +Y: right +X, up +Z (elevation / fin profile).
  - `front` — camera on −X (nose toward viewer): right +Y, up +Z.
  - `rear` — camera on +X: right −Y, up +Z.

### New `cadx shots` command

- `render_shots(run_dir, views=None, out_dir=None) -> dict` renders one shaded
  PNG per requested camera from the run's **primary STL** — the combined
  `assembly.stl` when present (reusing `_stl_exports` + `_primary_export`, so
  multi-part runs shoot the whole assembly exactly like `render`), else the
  single part. Files are written to `out_dir` (default `<run_dir>/views`) as
  `shaded_<camera>.png`. Default `views` is `["iso", "side", "top"]` — the
  three that make a part legible; `iso` reuses/overwrites the same
  `shaded_iso.png` `render` writes, so the two commands stay consistent.
- CLI: `cadx shots <run_dir> [--views iso,side,top,front,rear] [--out DIR]`.
  Unknown camera names fail fast with a `ValueError` naming the bad view and
  listing the valid set (matching how `evaluate` rejects unknown check types).
  The command prints the standard single JSON object: `{status, source,
  label, shots: [{name, camera, path}, ...]}`.
- `render` is **unchanged** — `shots` is an opt-in, on-demand command. Keeping
  it separate avoids bloating every `render` with N extra rasters and keeps
  the contact-sheet contract stable.

## Success Criteria

Written so the new tests fail before implementation and pass after:

1. `_render_stl_shaded` with the default projector still writes a valid,
   non-blank PNG (legacy path intact); passing an explicit `project`
   produces a different image for a shape that is asymmetric across cameras.
2. `SHADED_CAMERAS` resolves the documented names; an orthographic projector
   maps a known point as specified (e.g. `top` puts +Y at a smaller
   `screen_y` than −Y).
3. `cadx shots` on a two-part assembly writes `shaded_iso/side/top.png`, each
   a valid non-blank PNG, sourced from `assembly.stl`, with a manifest listing
   them.
4. **Cameras genuinely differ**: for a part long in X, wide in Y, thin in Z,
   the rendered content of the `top` view is taller (in non-white pixel
   extent) than the `side` view — proving the projection, not just the
   filename, changed.
5. `--views` selects a subset; an unknown view name exits non-zero with a
   message naming the offending view.

## Consequences

- Consumers (e.g. `chupacabra-configuration`) delete their local screenshot
  scripts and call `cadx shots`. One rasterizer, one place to fix.
- Still a deterministic software rasterizer — no VTK/GPU dependency, works
  headless. Flat-shaded, no perspective; adequate for sanity/README use, not
  photoreal.
- Future cameras (custom azimuth/elevation, per-part isolation) can extend
  `SHADED_CAMERAS`/`render_shots` without touching `render`.

## AAR — 2026-07-03

Landed on `feature/adr-0026-multi-view-shots`. All five success criteria
have passing tests in `tests/test_multi_view_shots.py`; the full suite is
green and the legacy `render` output path is untouched (the `render`
call site passes no projector, so `_render_stl_shaded` defaults to
`_project_iso`).

### What worked

* **The generalisation was almost entirely projection.** Splitting the
  fixed `_project_iso` call out into a `project` parameter + a
  `SHADED_CAMERAS` registry reused the existing STL reader, shader,
  painter's fill, and auto-framing wholesale — no rasterizer rewrite.
* **Behavioural test caught what a filename check would miss.** Rendering
  a plate that is wide in Y and thin in Z and asserting the `top` view's
  content is proportionally taller than the `side` view's proves the
  *projection* changed, not just the output name. Verified against the
  real Chupacabra airframe: `top` shows the wing planform, `side` the fin
  profile, `front` the circular body with the cruciform/wing cross.
* **`shots` reuses `_primary_export`,** so a multi-part run shoots the
  combined `assembly.stl` exactly like `render` — no separate assembly
  handling.

### Deviations

* None from the decision. The default view set is `iso,side,top` (front/
  rear available via `--views`) — the three that make a part legible,
  matching the throwaway script this replaces.

### Follow-up

* `chupacabra-configuration` can delete `tools/render_screenshots.py` and
  call `cadx shots` (left to that repo's owner; the script still works).
