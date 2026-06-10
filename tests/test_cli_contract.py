import json
import os
import subprocess
import sys
from pathlib import Path


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI from the source checkout as an external agent would."""

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


def test_init_creates_agent_editable_project_files(tmp_path):
    result = run_cadx(tmp_path, "init")
    payload = parse_stdout_json(result)

    assert payload["status"] == "ok"
    assert (tmp_path / "design.py").exists()
    assert (tmp_path / "params.yaml").exists()
    assert (tmp_path / "requirements.yaml").exists()


def test_run_executes_design_and_writes_agent_artifacts(tmp_path):
    (tmp_path / "design.py").write_text(
        """
from cadx import publish, publish_feature


def build(params):
    publish(
        "plate",
        {
            "bbox": {"min": [0, 0, 0], "max": [80, 20, 4]},
            "mass_properties": {"volume": 6080, "area": 3840},
            "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
        },
        role="final",
    )
    publish_feature("mount_hole_left", kind="cylindrical_hole", diameter=6, center=[20, 10, 2])
    publish_feature("mount_hole_right", kind="cylindrical_hole", diameter=6, center=[60, 10, 2])
    return None
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("width_mm: 80\n", encoding="utf-8")

    result = run_cadx(tmp_path, "run", "design.py", "--params", "params.yaml")
    payload = parse_stdout_json(result)

    run_dir = tmp_path / payload["artifact_dir"]
    assert payload["status"] == "ok"
    assert payload["run_id"] == "0001"
    assert (run_dir / "source_snapshot.py").exists()
    assert (run_dir / "params.resolved.yaml").exists()
    assert (run_dir / "diagnostics.json").exists()

    diagnostics = json.loads((run_dir / "diagnostics.json").read_text())
    assert diagnostics["published"][0]["label"] == "plate"
    assert diagnostics["features"][1]["id"] == "feat.mount_hole_right"


def test_inspect_render_evaluate_and_compare_close_the_loop(tmp_path):
    design = tmp_path / "design.py"
    design.write_text(
        """
from cadx import publish, publish_feature


def build(params):
    publish(
        "plate",
        {
            "bbox": {"min": [0, 0, 0], "max": [80, 20, 4]},
            "mass_properties": {"volume": 6080, "area": 3840},
            "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
        },
        role="final",
    )
    publish_feature("mount_hole_left", kind="cylindrical_hole", diameter=5, center=[20, 10, 2])
    publish_feature("mount_hole_right", kind="cylindrical_hole", diameter=5, center=[60, 10, 2])
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("width_mm: 80\n", encoding="utf-8")
    (tmp_path / "requirements.yaml").write_text(
        """
units: mm
checks:
  - id: width
    type: dimension
    target: obj.plate.bbox.size.x
    equals: 80
    tolerance: 0.1
  - id: hole_count
    type: feature_count
    kind: cylindrical_hole
    equals: 2
  - id: hole_diameter
    type: feature_dimension
    selector:
      kind: cylindrical_hole
    property: diameter
    equals: 6
    tolerance: 0.1
""",
        encoding="utf-8",
    )

    first = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    run_dir = tmp_path / first["artifact_dir"]

    inspect_payload = parse_stdout_json(run_cadx(tmp_path, "inspect", str(run_dir)))
    assert inspect_payload["objects"] == 1
    assert json.loads((run_dir / "spatial.json").read_text())["objects"][0]["bbox"]["size"] == [80, 20, 4]

    render_payload = parse_stdout_json(run_cadx(tmp_path, "render", str(run_dir)))
    assert Path(tmp_path / render_payload["contact_sheet"]).exists()

    evaluate_payload = parse_stdout_json(
        run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", "requirements.yaml")
    )
    assert evaluate_payload["status"] == "fail"
    assert evaluate_payload["failed"] == ["hole_diameter"]

    design.write_text(design.read_text().replace("diameter=5", "diameter=6"), encoding="utf-8")
    second = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    second_dir = tmp_path / second["artifact_dir"]
    parse_stdout_json(run_cadx(tmp_path, "inspect", str(second_dir)))

    compare_payload = parse_stdout_json(run_cadx(tmp_path, "compare", str(run_dir), str(second_dir)))
    assert compare_payload["status"] == "ok"
    assert compare_payload["changes"]["objects"]["obj.plate"]["bbox.size"] == [0, 0, 0]
