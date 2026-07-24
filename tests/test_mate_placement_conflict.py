"""ADR 0053 (D-028): warn when a mate discards a part's own placement.

A part built as ``Pos(...) * shape`` carries its own transform on the build123d
object's ``.location``. Publishing it with a ``mate=`` makes the runner pose it
with ``obj.located(placement)``, which sets the location *absolutely* and throws
the own transform away. Before this ADR that happened silently. This ADR emits a
structured ``placement_overridden_by_mate`` warning (the ADR 0045
``material_unresolved`` pattern) without changing where the part is posed.
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


def read_diagnostics(tmp_path: Path, payload: dict) -> dict:
    run_dir = tmp_path / payload["artifact_dir"]
    return json.loads((run_dir / "diagnostics.json").read_text(encoding="utf-8"))


def placement_override_warnings(diagnostics: dict) -> list[dict]:
    return [w for w in diagnostics.get("warnings", []) if w.get("type") == "placement_overridden_by_mate"]


# --------------------------------------------------------------------------
# Kernel-free unit test of the detection helper.
# --------------------------------------------------------------------------


def test_own_placement_nonidentity_helper():
    """The helper flags a non-identity ``.location`` and nothing else."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.runner import _own_placement_is_nonidentity

    class _Loc:
        def __init__(self, position, orientation):
            self.position = position
            self.orientation = orientation

    class _Shape:
        def __init__(self, location):
            self.location = location

    # build123d spells identity orientation as (-0.0, 0.0, -0.0); it is identity.
    identity = _Shape(_Loc((0.0, 0.0, 0.0), (-0.0, 0.0, -0.0)))
    translated = _Shape(_Loc((50.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
    rotated = _Shape(_Loc((0.0, 0.0, 0.0), (0.0, 0.0, 30.0)))

    assert _own_placement_is_nonidentity(translated) is True
    assert _own_placement_is_nonidentity(rotated) is True
    assert _own_placement_is_nonidentity(identity) is False
    # No .location, or an unreadable one -> never raises, returns False.
    assert _own_placement_is_nonidentity(object()) is False
    assert _own_placement_is_nonidentity(_Shape(None)) is False
    assert _own_placement_is_nonidentity({"not": "a shape"}) is False


# --------------------------------------------------------------------------
# Real geometry (build123d): the reported failure.
# --------------------------------------------------------------------------

build123d = pytest.importorskip("build123d")


def test_mate_overriding_own_placement_warns(tmp_path):
    """A ``Pos(...)``-transformed part that is also mated warns exactly once."""

    payload = run_design(
        tmp_path,
        """
from build123d import Box, Pos, Location
from cadx import publish, mate


def build(params):
    publish("base", Box(20, 20, 6), role="final", placement=Location((100, 0, 0)))
    # arm carries its OWN transform (Pos) AND a mate -> the mate discards the Pos.
    publish("arm", Pos(50, 0, 0) * Box(30, 8, 4),
            mate=mate(to="base", anchor=Location((-10, 0, 0)),
                      target=Location((0, 0, 20))))
""",
    )
    assert payload["status"] == "ok", payload
    diagnostics = read_diagnostics(tmp_path, payload)
    warnings = placement_override_warnings(diagnostics)
    assert len(warnings) == 1, diagnostics.get("warnings")
    assert warnings[0]["label"] == "arm"
    assert "discards" in warnings[0]["message"]


def test_mate_without_own_placement_is_quiet(tmp_path):
    """A plain (identity-location) mated part emits no override warning."""

    payload = run_design(
        tmp_path,
        """
from build123d import Box, Location
from cadx import publish, mate


def build(params):
    publish("base", Box(20, 20, 6), role="final", placement=Location((100, 0, 0)))
    publish("arm", Box(30, 8, 4),
            mate=mate(to="base", anchor=Location((-10, 0, 0)),
                      target=Location((0, 0, 20))))
""",
    )
    assert payload["status"] == "ok", payload
    diagnostics = read_diagnostics(tmp_path, payload)
    assert placement_override_warnings(diagnostics) == []
