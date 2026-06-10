"""ADR 0012: explicit and automatically detected features must deduplicate.

The starter project written by ``cadx init`` publishes its mount holes
explicitly while automatic STEP detection finds the same holes in the
geometry. Without reconciliation the same physical hole is counted twice and
the starter project fails its own requirements.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytest.importorskip("build123d")


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run cadx through the same subprocess path agents use."""

    repo_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}
    return subprocess.run(
        [sys.executable, "-m", "cadx.cli", *args],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def parse_stdout_json(result: subprocess.CompletedProcess[str]) -> dict:
    """Parse cadx JSON output."""

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_init_starter_project_passes_its_own_requirements(tmp_path):
    """The out-of-the-box starter flow must converge without any edits.

    This is the harness acceptance criterion that caught the ADR 0012 bug:
    explicit publications and automatic detection both reported the starter
    plate's two mount holes, so ``feature_count`` observed four holes.
    """

    parse_stdout_json(run_cadx(tmp_path, "init"))
    payload = parse_stdout_json(run_cadx(tmp_path, "run", "design.py", "--params", "params.yaml"))
    assert payload["status"] == "ok"

    run_dir = tmp_path / payload["artifact_dir"]
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    holes = [feature for feature in spatial["features"] if feature["kind"] == "cylindrical_hole"]

    # Exactly the two explicit publications survive, corroborated by geometry.
    assert {feature["id"] for feature in holes} == {"feat.mount_hole_left", "feat.mount_hole_right"}
    assert all(feature.get("confirmed_by_detection") for feature in holes)

    evaluation = parse_stdout_json(
        run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", "requirements.yaml")
    )
    assert evaluation["status"] == "pass"
    assert evaluation["failed"] == []


def test_unmatched_explicit_feature_is_preserved_not_merged(tmp_path):
    """Deduplication must stay conservative.

    An explicit feature whose location matches no real geometry is a
    discrepancy the agent should see, so it must survive alongside the
    automatically detected features instead of being merged into one of them.
    """

    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import *
from cadx import publish, publish_feature


def build(params):
    with BuildPart() as model:
        Box(40, 20, 4)
        with Locations((-10, 0, 0), (10, 0, 0)):
            Cylinder(2, 8, mode=Mode.SUBTRACT)

    publish("plate", model.part, role="final")
    # Same kind and diameter as the real holes, but at a location where no
    # hole exists. Only position separates it from the detected features.
    publish_feature("phantom_hole", kind="cylindrical_hole", diameter=4, center=[0, 5, 0])
    return model.part
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")

    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    run_dir = tmp_path / payload["artifact_dir"]
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    holes = [feature for feature in spatial["features"] if feature["kind"] == "cylindrical_hole"]

    assert len(holes) == 3
    phantom = next(feature for feature in holes if feature["id"] == "feat.phantom_hole")
    assert not phantom.get("confirmed_by_detection")
    detected = [feature for feature in holes if feature.get("detected")]
    assert len(detected) == 2
