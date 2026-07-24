"""ADR 0056 (D-033): one documented "use this for robotics" inertia field.

spatial.json ships three inertia tensors; a robotics consumer must know that a
URDF <inertial> wants the body-frame, mass-scaled inertia_link_frame_mass. This
ADR adds a self-describing recommended_use pointer inside
inertia_link_frame_mass_semantics (emitted behavior -> red-green) and a worked
doc docs/inertia-consumers.md.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
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


def run_design(tmp_path: Path, body: str) -> dict:
    (tmp_path / "design.py").write_text(body, encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    result = run_cadx(tmp_path, "run", str(tmp_path / "design.py"))
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def single_object(tmp_path: Path, payload: dict) -> dict:
    run_dir = tmp_path / payload["artifact_dir"]
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    [obj] = spatial["objects"]
    return obj


def test_inertia_consumers_doc_exists():
    """The worked robotics-inertia doc ships in the repo."""

    repo_root = Path(__file__).resolve().parents[1]
    doc = repo_root / "docs" / "inertia-consumers.md"
    assert doc.exists(), "docs/inertia-consumers.md is missing"
    text = doc.read_text(encoding="utf-8")
    assert "inertia_link_frame_mass" in text
    assert "<inertial>" in text


build123d = pytest.importorskip("build123d")


def test_recommended_use_points_at_link_frame_mass(tmp_path):
    """The mass-tensor semantics record carries a recommended_use pointer that
    names the doc and the URDF use."""

    payload = run_design(
        tmp_path,
        """
from build123d import Box
from cadx import publish


def build(params):
    publish("block", Box(10, 20, 30), role="final", density=0.0027)
""",
    )
    assert payload["status"] == "ok", payload
    semantics = single_object(tmp_path, payload)["mass_properties"]["inertia_link_frame_mass_semantics"]
    recommended = semantics.get("recommended_use")
    assert isinstance(recommended, str) and recommended
    assert "inertia-consumers.md" in recommended
    assert "URDF" in recommended or "<inertial>" in recommended
    # The honest units/frame facts remain beside the new pointer.
    assert semantics["units"] == "g*mm^2"
    assert semantics["axes"] == "link (body) frame"


def test_no_recommended_use_without_density(tmp_path):
    """A density-free part emits no mass-tensor semantics at all."""

    payload = run_design(
        tmp_path,
        """
from build123d import Box
from cadx import publish


def build(params):
    publish("block", Box(10, 20, 30), role="final")
""",
    )
    assert payload["status"] == "ok", payload
    mp = single_object(tmp_path, payload)["mass_properties"]
    assert "inertia_link_frame_mass_semantics" not in mp
