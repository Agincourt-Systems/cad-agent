"""ADR 0057: per-feature tolerance / fit metadata (D-032).

A precision feature carries a fit (``16 H7`` bearing seat, ``8 g6`` dead shaft),
not just a nominal size. These tests pin that ``publish_feature`` accepts an
optional ``tolerance`` dict, validates it lightly at publish time, carries it
verbatim into ``spatial.json``, and that ``cadx bom`` rolls the toleranced
features up into a ``tolerances`` array in ``bom.json``.

They fail before implementation because ``publish_feature`` performs no
tolerance validation (unknown keys / bad types are silently accepted) and
``build_bom`` emits no ``tolerances`` array.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


# Validation is exercised through the pure-Python authoring API, which imports
# without a CAD kernel; the round-trip / BOM tests need the real run pipeline.
from cadx import clear_registry, publish_feature, snapshot_registry


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke cadx through its public CLI, as agents do."""

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
    """Parse successful cadx JSON output."""

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Publish-time validation (pure API, no CAD kernel required)
# ---------------------------------------------------------------------------


def test_fit_string_passes_verbatim():
    """An ISO fit designation is stored exactly as authored — no lookup, no mangle."""

    clear_registry()
    publish_feature("seat", "cylindrical_hole", diameter=16, tolerance={"fit": "H7", "nominal": 16.0})
    publish_feature("shaft", "cylindrical_boss", diameter=8, tolerance={"fit": "g6", "nominal": 8.0})
    features = {f["id"]: f for f in snapshot_registry()["features"]}
    assert features["feat.seat"]["tolerance"] == {"fit": "H7", "nominal": 16.0}
    assert features["feat.shaft"]["tolerance"] == {"fit": "g6", "nominal": 8.0}


def test_discrete_deviations_accepted():
    """Discrete +/- deviations and a note round-trip; numbers normalize to float."""

    clear_registry()
    publish_feature(
        "bore",
        "cylindrical_hole",
        diameter=12,
        tolerance={"tol_plus": 0.02, "tol_minus": -0.01, "note": "press fit"},
    )
    tol = snapshot_registry()["features"][0]["tolerance"]
    assert tol == {"tol_plus": 0.02, "tol_minus": -0.01, "note": "press fit"}
    assert isinstance(tol["tol_plus"], float)
    assert isinstance(tol["tol_minus"], float)


def test_untoleranced_feature_has_no_tolerance_key():
    """A feature without tolerance is byte-identical to before this ADR."""

    clear_registry()
    publish_feature("plain", "cylindrical_hole", diameter=6, center=[0, 0, 2])
    feature = snapshot_registry()["features"][0]
    assert "tolerance" not in feature


def test_unknown_tolerance_key_rejected():
    """A typo'd key fails loudly and names the offender rather than vanishing."""

    clear_registry()
    with pytest.raises(ValueError) as exc:
        publish_feature("seat", "cylindrical_hole", tolerance={"ft": "H7"})
    assert "ft" in str(exc.value)


def test_non_numeric_deviation_rejected():
    """A deviation that is not a number is rejected."""

    clear_registry()
    with pytest.raises(ValueError):
        publish_feature("seat", "cylindrical_hole", tolerance={"tol_plus": "big"})


def test_boolean_deviation_rejected():
    """A boolean is never a size, even though bool is an int subclass."""

    clear_registry()
    with pytest.raises(ValueError):
        publish_feature("seat", "cylindrical_hole", tolerance={"nominal": True})


def test_non_string_fit_rejected():
    """A fit must be a string designation."""

    clear_registry()
    with pytest.raises(ValueError):
        publish_feature("seat", "cylindrical_hole", tolerance={"fit": 7})


def test_empty_tolerance_rejected():
    """An empty tolerance dict carries no fit and is almost always a mistake."""

    clear_registry()
    with pytest.raises(ValueError):
        publish_feature("seat", "cylindrical_hole", tolerance={})


def test_tolerance_must_be_mapping():
    """A non-dict tolerance is rejected."""

    clear_registry()
    with pytest.raises(ValueError):
        publish_feature("seat", "cylindrical_hole", tolerance="H7")


# ---------------------------------------------------------------------------
# Round-trip through a real `cadx run` into spatial.json
# ---------------------------------------------------------------------------


pytest.importorskip("build123d")


_TOLERANCED_DESIGN = """
from build123d import *
from cadx import publish, publish_feature, publish_part_meta


def build(params):
    with BuildPart() as plate:
        with BuildSketch():
            Rectangle(80, 20)
            with GridLocations(60, 0, 2, 1):
                Circle(8, mode=Mode.SUBTRACT)
        extrude(amount=4)

    publish("plate", plate.part, role="final")
    # A 16 H7 bearing seat and an 8 g6 dead shaft — the D-032 exemplars.
    publish_feature(
        "bearing_seat",
        kind="cylindrical_hole",
        diameter=16,
        center=[-30, 0, 2],
        source_object="obj.plate",
        tolerance={"fit": "H7", "nominal": 16.0},
    )
    publish_feature(
        "dead_shaft",
        kind="cylindrical_hole",
        diameter=8,
        center=[30, 0, 2],
        source_object="obj.plate",
        tolerance={"fit": "g6", "nominal": 8.0, "note": "slip fit"},
    )
    publish_feature("plain_hole", kind="cylindrical_hole", diameter=6, center=[0, 0, 2])
    publish_part_meta("plate", vendor="SendCutSend", material="6061-T6", thickness_mm=4, qty=1)
    return plate.part
"""


def _run_toleranced(tmp_path: Path) -> Path:
    design = tmp_path / "design.py"
    design.write_text(_TOLERANCED_DESIGN, encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


def test_tolerance_roundtrips_into_spatial(tmp_path):
    """The tolerance dict reaches spatial.json on its feature record, verbatim."""

    run_dir = _run_toleranced(tmp_path)
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    features = {f["id"]: f for f in spatial["features"]}

    assert features["feat.bearing_seat"]["tolerance"] == {"fit": "H7", "nominal": 16.0}
    assert features["feat.dead_shaft"]["tolerance"] == {
        "fit": "g6",
        "nominal": 8.0,
        "note": "slip fit",
    }
    # The untoleranced feature is unchanged.
    assert "tolerance" not in features["feat.plain_hole"]


def test_bom_tolerances_summary(tmp_path):
    """`cadx bom` rolls toleranced features into a `tolerances` array in bom.json."""

    run_dir = _run_toleranced(tmp_path)
    parse_stdout_json(run_cadx(tmp_path, "bom", str(run_dir)))
    bom = json.loads((run_dir / "bom.json").read_text(encoding="utf-8"))

    tolerances = {entry["feature"]: entry for entry in bom["tolerances"]}
    # Only the two toleranced features appear; the plain hole does not.
    assert set(tolerances) == {"feat.bearing_seat", "feat.dead_shaft"}

    seat = tolerances["feat.bearing_seat"]
    assert seat["object"] == "plate"
    assert seat["kind"] == "cylindrical_hole"
    assert seat["tolerance"] == {"fit": "H7", "nominal": 16.0}

    shaft = tolerances["feat.dead_shaft"]
    assert shaft["tolerance"]["fit"] == "g6"

    # Deterministic ordering by feature id.
    assert [e["feature"] for e in bom["tolerances"]] == sorted(e["feature"] for e in bom["tolerances"])
