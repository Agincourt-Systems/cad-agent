"""ADR 0044: frame-consistent sheet DFM + bend-arc slot suppression (D-023).

Two halves of one deficiency, exercised end-to-end through the CLI:

  (2) A folded sheet-metal part's bend regions are swept annular sectors, so each
      bend contributes inner/outer partial-cylinder faces. The obround-slot
      detector must NOT pair these into phantom ``kind="slot"`` features, while a
      real slot cut in a flat plate must still be detected.

  (1) ``hole_to_bend`` (flat-pattern frame) and ``hole_to_edge`` (folded bbox by
      default) must be combinable into one coherent manufacturability check on a
      bent part. A ``frame: flat`` check with the developed blank extent measures
      ``hole_to_edge`` in the flat frame so both rules agree.

These need a real CAD kernel to fold the part and to detect topology, so the whole
module is skipped without build123d.
"""

import json
import math
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


pytest.importorskip("build123d")


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
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _run_design(tmp_path: Path, body: str) -> Path:
    """Write and run a design whose ``build`` body is ``body``; return run dir."""

    tmp_path.mkdir(parents=True, exist_ok=True)
    design = tmp_path / "design.py"
    design.write_text(textwrap.dedent(body), encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


def _spatial(run_dir: Path) -> dict:
    return json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))


def _slots(run_dir: Path) -> list[dict]:
    return [f for f in _spatial(run_dir)["features"] if f.get("kind") == "slot"]


def _developed_length(flanges, radius, k_factor, thickness, angle=90.0) -> float:
    """Developed blank length: sum of flanges plus each bend allowance."""

    n_bends = len(flanges) - 1
    ba = (math.pi / 180.0) * angle * (radius + k_factor * thickness)
    return sum(flanges) + n_bends * ba


# --- Half (2): bend-arc slot suppression -------------------------------------


def test_folded_bend_arcs_are_not_slots(tmp_path):
    """A folded 1-bend part and a folded 2-bend U-channel yield ZERO slots.

    Without suppression the 2-bend part's inner-arc pair and outer-arc pair each
    detect as a phantom obround slot; the fix must remove both while leaving the
    1-bend part (already zero) untouched.
    """

    run_1bend = _run_design(
        tmp_path / "b1",
        """
        from cadx import publish_sheet_metal
        from cadx.sheetmetal import bend


        def build(params):
            part = bend(40, 60, angle_deg=90, inside_radius=2.29,
                        k_factor=0.44, thickness=2.29, width=30)
            publish_sheet_metal("bracket", part, role="final")
            return part.folded
        """,
    )
    assert _slots(run_1bend) == []

    run_2bend = _run_design(
        tmp_path / "b2",
        """
        from cadx import publish_sheet_metal
        from cadx.sheetmetal import bend_chain


        def build(params):
            part = bend_chain(
                [40, 50, 40],
                [
                    {"angle_deg": 90, "inside_radius": 2.29, "k_factor": 0.44, "direction": "up"},
                    {"angle_deg": 90, "inside_radius": 2.29, "k_factor": 0.44, "direction": "up"},
                ],
                thickness=2.29,
                width=30,
            )
            publish_sheet_metal("uchannel", part, role="final")
            return part.folded
        """,
    )
    assert _slots(run_2bend) == []


def test_real_slot_on_flat_part_still_detected(tmp_path):
    """A real obround slot in a flat plate is still detected (no over-suppression).

    The plate has no bend features, so the sheet-metal gate never fires and the
    genuine slot survives detection as exactly one ``kind="slot"``.
    """

    run_dir = _run_design(
        tmp_path,
        """
        from build123d import BuildPart, BuildSketch, Box, SlotOverall, extrude, Mode, Plane
        from cadx import publish


        def build(params):
            with BuildPart() as model:
                Box(80, 40, 3)
                with BuildSketch(Plane.XY):
                    SlotOverall(20, 6)
                extrude(amount=10, mode=Mode.SUBTRACT, both=True)
            publish("plate", model.part, role="final")
            return model.part
        """,
    )
    slots = _slots(run_dir)
    assert len(slots) == 1
    assert slots[0]["width"] == pytest.approx(6)
    assert slots[0]["length"] == pytest.approx(20)


# --- Half (1): frame-consistent hole_to_edge ---------------------------------


def _bracket_design(hole_x: float, hole_d: float = 5.0) -> str:
    """A single-bend bracket plus a hole authored in FLAT-pattern coordinates."""

    return f"""
        from cadx import publish_sheet_metal, publish_feature
        from cadx.sheetmetal import bend


        def build(params):
            part = bend(20, 60, angle_deg=90, inside_radius=3,
                        k_factor=0.44, thickness=2.29, width=30)
            publish_sheet_metal("bracket", part, role="final")
            publish_feature(
                "hole",
                kind="cylindrical_hole",
                diameter={hole_d},
                center=[{hole_x}, 0, 0],
                axis=[0, 0, 1],
                through=True,
                source_object="obj.bracket",
            )
            return part.folded
    """


def _evaluate(tmp_path: Path, run_dir: Path, requirements_yaml: str) -> dict:
    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(textwrap.dedent(requirements_yaml), encoding="utf-8")
    parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))
    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))
    assert len(checks["checks"]) == 1
    return checks["checks"][0]


def _rule_failures(check: dict, rule: str) -> set[str]:
    return {f for v in check.get("violations", []) if v["rule"] == rule for f in v["features"]}


def test_hole_to_bend_and_hole_to_edge_coherent_in_flat_frame(tmp_path):
    """A flat-frame hole passes hole_to_bend AND hole_to_edge in one check.

    The hole sits at flat x=35 on a ~72 mm developed blank — clear of both the
    bend line (~10 mm) and the blank edges. Under ``frame: flat`` both rules pass.
    The SAME check without ``frame: flat`` measures hole_to_edge against the folded
    bbox, where flat x=35 lies far outside the part, so it false-positives — the
    incoherence D-023 reports.
    """

    developed = _developed_length([20, 60], 3, 0.44, 2.29)
    run_dir = _run_design(tmp_path, _bracket_design(hole_x=35))

    flat_check = _evaluate(
        tmp_path,
        run_dir,
        f"""
        units: mm
        checks:
          - id: bracket_dfm
            type: manufacturability
            object: obj.bracket
            thickness: 2.29
            frame: flat
            blank_length: {developed}
            blank_width: 30
            rules:
              - rule: hole_to_bend
              - rule: hole_to_edge
        """,
    )
    assert flat_check["status"] == "pass", flat_check
    assert _rule_failures(flat_check, "hole_to_edge") == set()
    assert _rule_failures(flat_check, "hole_to_bend") == set()

    # Same rules, no frame:flat -> hole_to_edge uses the folded bbox and
    # false-positives on the flat-frame hole. This is the bug being fixed.
    folded_check = _evaluate(
        tmp_path,
        run_dir,
        """
        units: mm
        checks:
          - id: bracket_dfm
            type: manufacturability
            object: obj.bracket
            thickness: 2.29
            rules:
              - rule: hole_to_bend
              - rule: hole_to_edge
        """,
    )
    assert folded_check["status"] == "fail"
    assert "feat.hole" in _rule_failures(folded_check, "hole_to_edge")


def test_flat_frame_hole_to_edge_catches_real_violation(tmp_path):
    """Under frame:flat, a hole truly crowding the flat blank edge still fails.

    A hole at flat x=1.5 (radius 2.5) overhangs the leading blank edge at x=0, so
    the frame-aware hole_to_edge must still flag it — the rule is coherent, not
    merely always-pass.
    """

    developed = _developed_length([20, 60], 3, 0.44, 2.29)
    run_dir = _run_design(tmp_path, _bracket_design(hole_x=1.5))
    check = _evaluate(
        tmp_path,
        run_dir,
        f"""
        units: mm
        checks:
          - id: bracket_dfm
            type: manufacturability
            object: obj.bracket
            thickness: 2.29
            frame: flat
            blank_length: {developed}
            blank_width: 30
            rules:
              - rule: hole_to_edge
        """,
    )
    assert check["status"] == "fail"
    assert "feat.hole" in _rule_failures(check, "hole_to_edge")
