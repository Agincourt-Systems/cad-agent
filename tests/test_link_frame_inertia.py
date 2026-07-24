"""Acceptance tests for ADR 0047: link-frame inertia emission.

Covers D-017 of docs/specs/arm-deficiencies.md: the per-part
``matrix_of_inertia`` is a unit-density geometric second moment in WORLD axes
at the placed pose (ADR 0037), so a URDF consumer must both density-scale AND
rotate it into the link body frame. This ADR adds, ADDITIVELY, a sibling
``inertia_link_frame`` tensor already rotated into the body frame (``Rᵀ·I·R``),
its own semantics record, and a mass-scaled ``inertia_link_frame_mass`` when a
density resolves.

These fail before the ADR (the new keys are absent) and pass after. The math is
the heart of the ADR, so the tests are numeric property checks (rotation and
translation invariance), not structural probes.
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


def parse_stdout_json(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _run_design(tmp_path: Path, body: str) -> dict:
    """Run a design body and return its single spatial object."""

    (tmp_path / "design.py").write_text(body, encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(tmp_path / "design.py"), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    run_dir = tmp_path / payload["artifact_dir"]
    return json.loads((run_dir / "spatial.json").read_text())


_LINK_SEMANTICS = {
    "units": "mm^5",
    "density": "unit (geometric)",
    "about": "part centroid",
    "axes": "link (body) frame",
}

_LINK_MASS_SEMANTICS = {
    "units": "g*mm^2",
    "density": "mass-weighted",
    "about": "part centroid",
    "axes": "link (body) frame",
    # ADR 0056 (D-033): a self-describing robotics pointer travels with the tensor.
    "recommended_use": (
        "URDF <inertial>: use this tensor (body-frame, mass-scaled, "
        "g*mm^2) with the part center of mass as <origin>. See "
        "docs/inertia-consumers.md."
    ),
}


def _max_abs_diff(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(3) for j in range(3))


# --------------------------------------------------------------------------
# Kernel-free structural guarantee.
# --------------------------------------------------------------------------


def test_link_frame_semantics_paired():
    """``inertia_link_frame`` and its semantics are emitted iff the tensor is;
    neither for a kernel-free object (extends the ADR 0015/0037 guarantee)."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.runner import _mass_properties

    props = _mass_properties(object())
    assert "inertia_link_frame" not in props
    assert "inertia_link_frame_semantics" not in props


# --------------------------------------------------------------------------
# Real geometry (build123d): the numeric heart of the ADR.
# --------------------------------------------------------------------------

build123d = pytest.importorskip("build123d")


def test_link_frame_equals_world_at_identity(tmp_path):
    """An identity-placed part: link-frame tensor == world tensor exactly."""

    spatial = _run_design(
        tmp_path,
        """
from build123d import Box
from cadx import publish


def build(params):
    publish("block", Box(10, 20, 30), role="final")
""",
    )
    [obj] = spatial["objects"]
    mp = obj["mass_properties"]
    world = mp["matrix_of_inertia"]
    link = mp["inertia_link_frame"]
    assert _max_abs_diff(world, link) == pytest.approx(0.0, abs=1e-6)
    assert mp["inertia_link_frame_semantics"] == _LINK_SEMANTICS


def test_link_frame_rotation_invariance(tmp_path):
    """A 30-deg-about-Z part: its link-frame tensor equals the identity twin's,
    and its off-diagonals vanish, even though the WORLD tensor has products of
    inertia. This is the D-017 fix's central property."""

    identity = _run_design(
        tmp_path,
        """
from build123d import Box
from cadx import publish


def build(params):
    publish("block", Box(10, 20, 30), role="final")
""",
    )
    twin_link = identity["objects"][0]["mass_properties"]["inertia_link_frame"]

    rotated = _run_design(
        tmp_path,
        """
from build123d import Box, Location
from cadx import publish


def build(params):
    publish("block", Box(10, 20, 30), role="final",
            placement=Location((5, 7, 3), (0, 0, 30)))
""",
    )
    mp = rotated["objects"][0]["mass_properties"]
    world = mp["matrix_of_inertia"]
    link = mp["inertia_link_frame"]

    # The world tensor carries a pose-artifact off-diagonal product of inertia.
    assert abs(world[0][1]) > 1.0
    # The link-frame tensor recovers the body frame: off-diagonals vanish and it
    # matches the identity-placed twin element-wise.
    assert abs(link[0][1]) == pytest.approx(0.0, abs=1e-5)
    assert abs(link[0][2]) == pytest.approx(0.0, abs=1e-5)
    assert abs(link[1][2]) == pytest.approx(0.0, abs=1e-5)
    assert _max_abs_diff(link, twin_link) == pytest.approx(0.0, abs=1e-4)


def test_link_frame_translation_invariance(tmp_path):
    """A purely translated part: both tensors match the untranslated twin (both
    are taken about the part centroid, so translation moves nothing)."""

    base = _run_design(
        tmp_path,
        """
from build123d import Box
from cadx import publish


def build(params):
    publish("block", Box(10, 20, 30), role="final")
""",
    )
    base_world = base["objects"][0]["mass_properties"]["matrix_of_inertia"]
    base_link = base["objects"][0]["mass_properties"]["inertia_link_frame"]

    moved = _run_design(
        tmp_path,
        """
from build123d import Box, Location
from cadx import publish


def build(params):
    publish("block", Box(10, 20, 30), role="final",
            placement=Location((100, -50, 25)))
""",
    )
    mp = moved["objects"][0]["mass_properties"]
    assert _max_abs_diff(mp["matrix_of_inertia"], base_world) == pytest.approx(0.0, abs=1e-4)
    assert _max_abs_diff(mp["inertia_link_frame"], base_link) == pytest.approx(0.0, abs=1e-4)


def test_link_frame_mass_when_density_resolves(tmp_path):
    """A part with a resolved density emits ``inertia_link_frame_mass`` equal to
    ``density · inertia_link_frame`` in g*mm^2; a density-free part emits none."""

    spatial = _run_design(
        tmp_path,
        """
from build123d import Box
from cadx import publish


def build(params):
    publish("block", Box(10, 20, 30), role="final", density=0.0027)
""",
    )
    mp = spatial["objects"][0]["mass_properties"]
    link = mp["inertia_link_frame"]
    link_mass = mp["inertia_link_frame_mass"]
    expected = [[0.0027 * link[i][j] for j in range(3)] for i in range(3)]
    assert _max_abs_diff(link_mass, expected) == pytest.approx(0.0, abs=1e-3)
    assert mp["inertia_link_frame_mass_semantics"] == _LINK_MASS_SEMANTICS

    # No density -> no mass-scaled tensor (byte-identical to pre-ADR shape).
    plain = _run_design(
        tmp_path,
        """
from build123d import Box
from cadx import publish


def build(params):
    publish("block", Box(10, 20, 30), role="final")
""",
    )
    plain_mp = plain["objects"][0]["mass_properties"]
    assert "inertia_link_frame_mass" not in plain_mp
    assert "inertia_link_frame_mass_semantics" not in plain_mp
