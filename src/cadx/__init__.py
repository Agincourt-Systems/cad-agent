"""Public authoring helpers for CAD design scripts.

Design files executed by the harness import from this module directly. Keeping
the API small is intentional: an agent only needs to publish named objects and
critical features, while the harness owns serialization, rendering, and
evaluation. The helpers are usable even when ``build123d`` is not installed,
which keeps tests and synthetic examples lightweight.
"""

from cadx.registry import (
    clear_registry,
    mate,
    publish,
    publish_feature,
    publish_flat,
    publish_part_meta,
    publish_sheet_metal,
    snapshot_registry,
)

__all__ = [
    "clear_registry",
    "mate",
    "publish",
    "publish_feature",
    "publish_flat",
    "publish_part_meta",
    "publish_sheet_metal",
    "snapshot_registry",
]
