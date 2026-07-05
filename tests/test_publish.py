"""ADR 0029: publish run directories to apexmesh.

Pure tests: plan building over a synthetic run dir and execution sequencing
against a fake client. No network, no build123d, no apexmesh install needed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cadx.publish import AlreadyPublishedError, build_plan, execute_plan

RUN_NAME = "0042"
PROJECT_DIR = "chupacabra-configuration"


def make_run_dir(tmp_path: Path, *, errors=(), with_bom=True, with_views=True) -> Path:
    """A structurally faithful miniature of artifacts/runs/NNNN."""

    run_dir = tmp_path / PROJECT_DIR / "artifacts" / "runs" / RUN_NAME
    run_dir.mkdir(parents=True)

    exports = []
    for label in ("assembly", "body"):
        for fmt in ("step", "stl", "glb"):
            path = run_dir / f"{label}.{fmt}"
            path.write_text(f"{label}-{fmt}")
            exports.append({
                "format": fmt,
                "label": label,
                "path": f"artifacts/runs/{RUN_NAME}/{label}.{fmt}",
                "units": "mm",
            })
    (run_dir / "diagnostics.json").write_text(json.dumps({
        "errors": list(errors),
        "exports": exports,
        "part_meta": [],
    }))
    (run_dir / "checks.json").write_text(json.dumps({
        "checks": [
            {"id": "len", "status": "pass", "type": "dimension"},
            {"id": "dia", "status": "pass", "type": "dimension"},
            {"id": "clear", "status": "fail", "type": "clearance"},
        ],
    }))
    (run_dir / "source_snapshot.py").write_text("# design\n")
    (run_dir / "params.resolved.yaml").write_text("body_diameter_m: 0.18\n")
    (run_dir / "spatial.json").write_text('{"objects": []}')
    (run_dir / "report.md").write_text("# report\n")
    if with_views:
        views = run_dir / "views"
        views.mkdir()
        (views / "contact.png").write_bytes(b"\x89PNG")
        (views / "shaded_iso.png").write_bytes(b"\x89PNG")
    if with_bom:
        (run_dir / "bom.json").write_text(json.dumps({
            "schema_version": "1.0",
            "units": "mm",
            "rows": [
                {"vendor": "SendCutSend", "label": "body", "part_number": "SCS-100",
                 "material": "6061-T6", "thickness_mm": 3.0, "finish": None,
                 "process": "laser", "qty": 1, "area_mm2": 1200.5,
                 "bbox_mm": [2163.9, 174.6, 174.6], "hole_count": 4,
                 "unit_cost": 42.0, "ext_cost": 42.0, "source_url": None},
                {"vendor": None, "label": "fin", "part_number": None,
                 "material": "5052", "thickness_mm": 2.0, "finish": None,
                 "process": "laser", "qty": 4, "area_mm2": 300.0,
                 "bbox_mm": [200.0, 150.0, 2.0], "hole_count": 0,
                 "unit_cost": None, "ext_cost": None, "source_url": None},
            ],
            "vendors": [], "totals": {"qty": 5}, "warnings": [],
        }))
        (run_dir / "bom.csv").write_text("vendor,label\n")
    return run_dir


class TestBuildPlan:
    def test_run_identity_and_metrics(self, tmp_path):
        plan = build_plan(make_run_dir(tmp_path))
        assert plan.external_ref == f"{PROJECT_DIR}:{RUN_NAME}"
        assert plan.status == "succeeded"
        assert plan.metrics["checks_passed"] == 2
        assert plan.metrics["checks_failed"] == 1
        assert plan.metrics["n_exports"] == 6
        assert plan.metrics["n_errors"] == 0

    def test_errors_mean_failed_status(self, tmp_path):
        plan = build_plan(make_run_dir(tmp_path, errors=[{"stage": "export", "message": "boom"}]))
        assert plan.status == "failed"
        assert plan.metrics["n_errors"] == 1

    def test_exports_become_cad_artifacts_with_label_roles(self, tmp_path):
        plan = build_plan(make_run_dir(tmp_path))
        outputs = {(a.path.name, a.kind, a.role) for a in plan.artifacts if a.direction == "output"}
        assert ("assembly.step", "cad_step", "assembly") in outputs
        assert ("body.glb", "cad_glb", "body") in outputs
        assert ("assembly.stl", "cad_stl", "assembly") in outputs

    def test_inputs_are_source_and_params(self, tmp_path):
        plan = build_plan(make_run_dir(tmp_path))
        inputs = {(a.path.name, a.kind, a.role) for a in plan.artifacts if a.direction == "input"}
        assert inputs == {
            ("source_snapshot.py", "config", "design_source"),
            ("params.resolved.yaml", "config", "params"),
        }

    def test_manifests_report_and_views(self, tmp_path):
        plan = build_plan(make_run_dir(tmp_path))
        by_name = {a.path.name: a for a in plan.artifacts}
        assert by_name["checks.json"].kind == "manifest"
        assert by_name["bom.json"].kind == "manifest"
        assert by_name["report.md"].kind == "report"
        assert by_name["contact.png"].kind == "image"
        assert by_name["contact.png"].role == "view"

    def test_bom_rows_become_part_specs(self, tmp_path):
        plan = build_plan(make_run_dir(tmp_path))
        assert plan.assembly_part_number == "CHUPACABRA-CONFIGURATION-ASSY"
        by_label = {p.label: p for p in plan.bom_parts}
        assert by_label["body"].part_number == "SCS-100"  # declared wins
        assert by_label["fin"].part_number == "CHUPACABRA-CONFIGURATION-fin"
        assert by_label["fin"].quantity == 4
        assert by_label["body"].attributes["material"] == "6061-T6"
        assert plan.revision == RUN_NAME

    def test_missing_bom_and_views_are_fine(self, tmp_path):
        plan = build_plan(make_run_dir(tmp_path, with_bom=False, with_views=False))
        names = {a.path.name for a in plan.artifacts}
        assert "bom.json" not in names
        assert "contact.png" not in names
        assert plan.bom_parts == []

    def test_missing_diagnostics_is_fatal(self, tmp_path):
        run_dir = make_run_dir(tmp_path)
        (run_dir / "diagnostics.json").unlink()
        with pytest.raises(FileNotFoundError):
            build_plan(run_dir)


class FakeApiError(Exception):
    def __init__(self, status, message=""):
        super().__init__(message)
        self.status = status
        self.message = message


class FakeClient:
    """Records calls; simulates 409s for existing part revisions."""

    def __init__(self, existing_runs=None, conflict_revisions=()):
        self.calls = []
        self._existing_runs = existing_runs or []
        self._conflict_revisions = set(conflict_revisions)
        self._counter = 0

    def _id(self, prefix):
        self._counter += 1
        return f"{prefix}-{self._counter}"

    def list_runs(self, project_id, tool=None, status=None):
        self.calls.append(("list_runs", tool))
        return self._existing_runs

    def start_run(self, project_id, tool, tool_version=None, external_ref=None, params=None):
        self.calls.append(("start_run", tool, external_ref))
        return {"id": self._id("run")}

    def upload_artifact(self, project_id, file_path, kind=None, metadata=None):
        self.calls.append(("upload_artifact", Path(file_path).name, kind))
        return {"id": self._id("art")}

    def attach_run_io(self, project_id, run_id, direction, artifact_id=None, node_id=None, role=None):
        self.calls.append(("attach_run_io", direction, role))
        return {"id": self._id("io")}

    def upsert_part(self, project_id, part_number, name, description=None):
        self.calls.append(("upsert_part", part_number))
        return {"id": f"part-{part_number}", "partNumber": part_number}

    def create_part_revision(self, project_id, part_id, revision, attributes=None):
        self.calls.append(("create_part_revision", part_id, revision))
        if (part_id, revision) in self._conflict_revisions:
            raise FakeApiError(409, "exists")
        return {"id": self._id("rev")}

    def request(self, method, path, body=None, params=None):
        # Only used to re-fetch parts after a revision conflict.
        self.calls.append(("request", method, path))
        return [
            {"id": f"part-{pn}", "partNumber": pn,
             "revisions": [{"id": f"rev-existing-{pn}", "revision": RUN_NAME}]}
            for pn in ("SCS-100", "CHUPACABRA-CONFIGURATION-fin", "CHUPACABRA-CONFIGURATION-ASSY")
        ]

    def create_bom_line(self, project_id, parent_revision_id, child_revision_id, quantity, **fields):
        self.calls.append(("create_bom_line", parent_revision_id, child_revision_id, quantity))
        return {"id": self._id("line")}

    def update_run(self, project_id, run_id, **fields):
        self.calls.append(("update_run", fields.get("status")))
        return {"id": run_id, **fields}


class TestExecutePlan:
    def test_sequencing_artifacts_then_bom_then_finish(self, tmp_path, monkeypatch):
        import cadx.publish as publish_mod
        monkeypatch.setattr(publish_mod, "_api_error_type", lambda: FakeApiError)
        plan = build_plan(make_run_dir(tmp_path))
        client = FakeClient()
        result = execute_plan(client, "proj-1", plan)

        kinds = [c[0] for c in client.calls]
        assert kinds[0] == "list_runs"
        assert kinds[1] == "start_run"
        assert client.calls[-1] == ("update_run", "succeeded")
        # 2 inputs + 6 exports + checks/diagnostics/spatial/report/bom.json/
        # bom.csv + 2 views = 16 uploads.
        assert kinds.count("upload_artifact") == 16
        # Assembly + 2 row parts; a revision each; a line per row.
        assert kinds.count("upsert_part") == 3
        assert kinds.count("create_part_revision") == 3
        assert kinds.count("create_bom_line") == 2
        assert result["bom_parts"] == 2

    def test_failed_run_publishes_with_failed_status(self, tmp_path):
        plan = build_plan(make_run_dir(tmp_path, errors=[{"m": "boom"}], with_bom=False))
        client = FakeClient()
        execute_plan(client, "proj-1", plan)
        assert client.calls[-1] == ("update_run", "failed")

    def test_republish_guard_and_force(self, tmp_path):
        plan = build_plan(make_run_dir(tmp_path))
        existing = [{"id": "r0", "externalRef": plan.external_ref, "status": "succeeded"}]
        with pytest.raises(AlreadyPublishedError):
            execute_plan(FakeClient(existing_runs=existing), "proj-1", plan)
        result = execute_plan(FakeClient(existing_runs=existing), "proj-1", plan, force=True)
        assert result["run_id"].startswith("run-")

    def test_revision_conflict_reuses_existing_revision(self, tmp_path, monkeypatch):
        import cadx.publish as publish_mod
        monkeypatch.setattr(publish_mod, "_api_error_type", lambda: FakeApiError)
        plan = build_plan(make_run_dir(tmp_path))
        client = FakeClient(conflict_revisions={(f"part-SCS-100", RUN_NAME)})
        result = execute_plan(client, "proj-1", plan)
        # The conflicted revision resolved to the existing one and BOM lines
        # were still created for every row.
        assert [c for c in client.calls if c[0] == "create_bom_line"]
        assert result["bom_parts"] == 2
