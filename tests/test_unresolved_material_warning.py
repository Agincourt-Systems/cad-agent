"""Acceptance tests for ADR 0045: warn on unresolved material.

Covers D-016 of docs/specs/arm-deficiencies.md: a part that declares a material
the built-in table cannot resolve AND carries no explicit density must surface a
structured ``material_unresolved`` warning on the run's warnings channel — the
missing signal that ADR 0035 left only as a per-object ``density_resolved: false``
fact. Three sibling cases must stay silent: a resolving material, an explicit
density (even alongside an unknown material name), and no material at all.

These assert real numeric/structural behavior and fail before the ADR is
implemented (``_apply_material_density`` returns ``None`` today, so it emits no
warnings and cannot be iterated for one).
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


def _apply():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.runner import _apply_material_density

    return _apply_material_density


# --------------------------------------------------------------------------
# Unit level (no CAD kernel): the four cases of the warning contract.
# --------------------------------------------------------------------------


def test_unresolved_material_emits_warning():
    """Declared-but-unknown material with no density -> one warning + no mass."""

    apply = _apply()
    records = [
        {
            "label": "link",
            "role": "part",
            "metadata": {"material": "unobtanium"},
            "mass_properties": {"volume": 1000.0},
        }
    ]
    warnings = apply(records, part_meta=[])

    # Exactly one warning, mirroring the mate_out_of_range shape and naming both
    # the offending part and the material string.
    material_warnings = [w for w in warnings if w.get("type") == "material_unresolved"]
    assert len(material_warnings) == 1, warnings
    warning = material_warnings[0]
    assert warning["label"] == "link"
    assert warning["material"] == "unobtanium"
    assert "unobtanium" in warning["message"]

    # ADR 0035 behavior preserved: unresolved recorded, no guessed density/mass.
    meta = records[0]["metadata"]
    assert meta["density_resolved"] is False
    assert "density" not in meta
    assert "mass" not in records[0]["mass_properties"]


def test_resolved_material_emits_no_warning():
    """A recognized material resolves silently -> empty warning list."""

    apply = _apply()
    records = [
        {
            "label": "rail",
            "role": "part",
            "metadata": {"material": "6061-T6"},
            "mass_properties": {"volume": 1000.0},
        }
    ]
    warnings = apply(records, part_meta=[])
    assert warnings == []
    # Density still resolved (ADR 0035 unchanged).
    assert records[0]["metadata"]["density"] == pytest.approx(0.00270)


def test_explicit_density_with_unknown_material_no_warning():
    """Explicit density wins and suppresses the miss warning for a bad name."""

    apply = _apply()
    records = [
        {
            "label": "custom",
            "role": "part",
            "metadata": {"material": "unobtanium", "density": 0.004},
            "mass_properties": {"volume": 1000.0},
        }
    ]
    warnings = apply(records, part_meta=[])
    assert warnings == []
    meta = records[0]["metadata"]
    assert meta["density"] == pytest.approx(0.004)  # explicit kept
    assert meta["density_source"] == "explicit"
    # No unresolved flag: explicit intent is not second-guessed.
    assert "density_resolved" not in meta


def test_no_material_declared_no_warning():
    """A part that declares neither material nor density stays silent and clean."""

    apply = _apply()
    records = [
        {
            "label": "blank",
            "role": "part",
            "mass_properties": {"volume": 1000.0},
        }
    ]
    warnings = apply(records, part_meta=[])
    assert warnings == []
    # Byte-unchanged: no metadata key synthesized (ADR 0035 shape stability).
    assert "metadata" not in records[0]


# --------------------------------------------------------------------------
# End-to-end through `cadx run` (real geometry): warning reaches diagnostics.
# --------------------------------------------------------------------------

build123d = pytest.importorskip("build123d")


def test_unresolved_material_warning_reaches_diagnostics(tmp_path):
    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import Box
from cadx import publish


def build(params):
    part = Box(10, 20, 30)
    publish("bracket", part, role="final", material="unobtanium")
    return part
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    # A warning does not change the run status: it stays ok.
    assert payload["status"] == "ok", payload
    run_dir = tmp_path / payload["artifact_dir"]

    diagnostics = json.loads((run_dir / "diagnostics.json").read_text())
    material_warnings = [w for w in diagnostics["warnings"] if w.get("type") == "material_unresolved"]
    assert len(material_warnings) == 1, diagnostics["warnings"]
    assert material_warnings[0]["label"] == "bracket"
    assert material_warnings[0]["material"] == "unobtanium"
