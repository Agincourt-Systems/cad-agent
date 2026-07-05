# ADR 0028: Configurable Light Direction for Shots

## Status

Accepted for implementation.

## Context

The shaded rasterizer hard-codes its light direction,
``(0.35, -0.45, 0.82)`` — up-and-behind-left, chosen once in ADR 0011 for the
isometric view. ADR 0026 added orthographic cameras, and ADR 0027's material
work made the consequence visible: the ``side`` camera faces the model's
*ambient-lit* flank (the fixed light sits on −Y), so a red part shades to
(89, 0, 0) there and every side/rear screenshot is murky regardless of
material. For screenshot work — the whole point of ``shots`` — the light
must be steerable per invocation, and ideally should be able to follow the
camera so each view is front-lit.

``render``'s contact-sheet raster is a diagnostic artifact with pinned
statistical tests; it keeps the fixed default. This is a ``shots``-surface
feature.

## Decision

- ``DEFAULT_LIGHT = (0.35, -0.45, 0.82)`` becomes a named module constant;
  ``_render_shaded`` (and the ``_render_stl_shaded`` wrapper) accept an
  optional ``light`` vector, defaulting to it — omitted, output is
  byte-identical to today.
- ``render_shots(..., light=None)`` and ``cadx shots --light <spec>`` accept:
  - ``"X,Y,Z"`` — an explicit direction (normalized; points from the model
    toward the light);
  - ``"camera"`` — resolved **per view** to that camera's view vector, so
    every shot in the invocation is front-lit by its own camera. This is the
    genuinely per-shot mode and the one-flag fix for dark side views;
  - omitted — the legacy default.
  An unparseable spec raises ``ValueError`` naming it, matching the
  unknown-view fail-fast behavior (CLI exit non-zero).
- Each shot record in the payload carries the resolved, normalized ``light``
  vector actually used, and the payload echoes the requested ``light`` spec —
  screenshots stay reproducible from their recorded parameters.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_resolve_shot_light`: ``None`` → the default; ``"0,0,1"`` → (0,0,1);
  ``"camera"`` → the projector's view vector; garbage raises ``ValueError``.
  Fails today: the resolver does not exist.
- `test_default_light_is_byte_stable`: ``_render_shaded`` with no ``light``
  produces bytes identical to passing ``DEFAULT_LIGHT`` explicitly.
- `test_camera_light_brightens_side_view` (kernel, flagship): on the ADR 0027
  red/steel project, the default side shot has no bright-red population while
  ``--light camera`` produces one — the dark-flank problem is one flag away.
- `test_explicit_light_vector_accepted` (kernel): ``--light 0,1,0.4`` on the
  side view also yields bright-red pixels, and the shot record's ``light``
  matches the normalized vector.
- `test_invalid_light_rejected` (kernel): ``--light banana`` exits non-zero
  naming the bad spec.
- Existing ADR 0011/0026/0027 tests pass unchanged (default path untouched).

## Consequences

- Screenshot lighting becomes a recorded, reproducible parameter instead of
  a baked-in constant; ``--light camera`` makes every view legible with no
  vector arithmetic by the user.
- ``render`` and the contact sheet are deliberately unchanged — diagnostics
  stay comparable across runs. If a future need arises, the same ``light``
  parameter is already plumbed through the shared rasterizer.
- Deferred: multiple lights, shadows, per-view light maps in one invocation
  (run ``shots`` per view instead), and material-aware exposure.

## After Action Report

Four of the five success-criteria tests were red before implementation; the
fifth (`test_invalid_light_rejected`) was green-before because argparse
itself rejects an unknown ``--light`` flag with the bad value in its error —
after implementation it exercises the real resolver path instead, so it
remains a valid pin.

The flagship comparison was verified visually on the ADR 0027 materials
showcase: the default side view renders murky near-black across every
material, while ``--light camera`` shows aluminum, steel, brass, and carbon
correctly. One interaction observed and deliberately documented rather than
engineered away: pure front light drives the Blinn half-vector onto the
surface normal for every camera-facing face, maximizing specular — glossy
dark materials (carbon fiber) read lighter than in the iso view. The README
records the off-axis recipe (e.g. ``0.3,1,0.5``) for a softer key light;
``"camera"`` keeps its simple, predictable meaning of exactly the view
vector, which the payload records per shot.

Full suite green on final code (see LOG for the count; 5 new tests). The
default-light path is byte-stable (pinned by test), and ``render``/contact
sheets are untouched by design.
