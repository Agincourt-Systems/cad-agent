"""Publish a run directory to apexmesh (ADR 0029).

Read-only over the run dir; append-only on the hub. Split into a pure
planning layer (build_plan — unit-testable without network or apexmesh)
and a thin execution layer (execute_plan) over the apexmesh-client
package, which is an optional dependency imported lazily.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from cadx.files import read_json


class AlreadyPublishedError(RuntimeError):
    """A succeeded cadx run with this external_ref already exists."""


def _api_error_type() -> type:
    """The apexmesh ApiError class, or a never-raised placeholder.

    Indirection keeps build_plan/execute_plan importable (and testable)
    without apexmesh-client installed; tests monkeypatch this.
    """

    try:
        from apexmesh_client import ApiError

        return ApiError
    except ImportError:  # pragma: no cover - exercised via monkeypatch

        class _NeverRaised(Exception):
            pass

        return _NeverRaised


_KIND_BY_FORMAT = {"step": "cad_step", "stl": "cad_stl", "glb": "cad_glb", "dxf": "cad_dxf"}

# (filename, kind, role) manifest-ish files included when present.
_AUX_FILES = (
    ("checks.json", "manifest", "checks"),
    ("diagnostics.json", "manifest", "diagnostics"),
    ("spatial.json", "manifest", "spatial"),
    ("bom.json", "manifest", "bom"),
    ("bom.csv", "manifest", "bom_csv"),
    ("report.md", "report", "report"),
)


@dataclass
class ArtifactSpec:
    path: Path
    kind: str
    direction: str  # 'input' | 'output'
    role: str


@dataclass
class BomPartSpec:
    """One bom.json row destined for apexmesh parts/revisions/lines."""

    label: str
    part_number: str
    quantity: int
    attributes: dict


@dataclass
class PublishPlan:
    external_ref: str
    tool_version: str
    status: str  # 'succeeded' | 'failed' (mirrors diagnostics errors)
    params: dict
    metrics: dict
    revision: str  # part-revision name = run number
    assembly_part_number: str
    artifacts: list[ArtifactSpec] = field(default_factory=list)
    bom_parts: list[BomPartSpec] = field(default_factory=list)


def _project_name(run_dir: Path) -> str:
    """artifacts/runs/NNNN sits three levels under the design project."""

    return run_dir.resolve().parent.parent.parent.name


def _tool_version() -> str:
    try:
        from importlib.metadata import version

        return f"cad-agent {version('cad-agent')}"
    except Exception:  # pragma: no cover - metadata missing in odd installs
        return "cad-agent unknown"


def build_plan(run_dir: Path, external_ref: Optional[str] = None) -> PublishPlan:
    """Pure: derive everything to publish from the run dir contents.

    diagnostics.json is required (it is the export index and the
    success/failure record); everything else degrades gracefully.
    """

    run_dir = Path(run_dir)
    diagnostics_path = run_dir / "diagnostics.json"
    if not diagnostics_path.exists():
        raise FileNotFoundError(f"{diagnostics_path} — not a cadx run directory?")
    diagnostics = read_json(diagnostics_path)

    checks: list[dict[str, Any]] = []
    checks_path = run_dir / "checks.json"
    if checks_path.exists():
        checks = read_json(checks_path).get("checks", [])
    passed = sum(1 for c in checks if c.get("status") == "pass")
    errors = diagnostics.get("errors", [])

    project = _project_name(run_dir)
    plan = PublishPlan(
        external_ref=external_ref or f"{project}:{run_dir.name}",
        tool_version=_tool_version(),
        status="failed" if errors else "succeeded",
        params={"run_dir": run_dir.name, "project": project},
        metrics={
            "checks_passed": passed,
            "checks_failed": len(checks) - passed,
            "n_exports": len(diagnostics.get("exports", [])),
            "n_errors": len(errors),
        },
        revision=run_dir.name,
        assembly_part_number=f"{project.upper()}-ASSY",
    )

    # Inputs: the run's exact reproducible definition.
    for name, role in (("source_snapshot.py", "design_source"), ("params.resolved.yaml", "params")):
        path = run_dir / name
        if path.exists():
            plan.artifacts.append(ArtifactSpec(path=path, kind="config", direction="input", role=role))

    # Outputs: every recorded export, resolved the same way cadx bom does.
    for export in diagnostics.get("exports", []):
        kind = _KIND_BY_FORMAT.get(export.get("format", ""))
        if kind is None:
            continue
        raw = Path(export["path"])
        path = raw if raw.exists() or raw.is_absolute() else run_dir / raw.name
        if path.exists():
            plan.artifacts.append(
                ArtifactSpec(path=path, kind=kind, direction="output", role=export.get("label", "export"))
            )

    for name, kind, role in _AUX_FILES:
        path = run_dir / name
        if path.exists():
            plan.artifacts.append(ArtifactSpec(path=path, kind=kind, direction="output", role=role))

    views = run_dir / "views"
    if views.is_dir():
        for png in sorted(views.glob("*.png")):
            plan.artifacts.append(ArtifactSpec(path=png, kind="image", direction="output", role="view"))

    # BOM rows -> part specs (assembly line quantities come from the rows).
    bom_path = run_dir / "bom.json"
    if bom_path.exists():
        for row in read_json(bom_path).get("rows", []):
            label = row["label"]
            plan.bom_parts.append(
                BomPartSpec(
                    label=label,
                    part_number=row.get("part_number") or f"{project.upper()}-{label}",
                    quantity=int(row.get("qty", 1)),
                    attributes={
                        key: row.get(key)
                        for key in (
                            "material",
                            "thickness_mm",
                            "finish",
                            "process",
                            "vendor",
                            "unit_cost",
                            "ext_cost",
                            "area_mm2",
                            "bbox_mm",
                            "hole_count",
                            "source_url",
                        )
                        if row.get(key) is not None
                    },
                )
            )

    return plan


def _revision_id(
    client: Any,
    project_id: str,
    part: dict,
    part_number: str,
    revision: str,
    attributes: Optional[dict] = None,
) -> str:
    """Create the revision; on a 409 (already revised by this run) reuse it.

    Attributes land only on first creation; a reuse keeps whatever the
    earlier publish wrote.
    """

    try:
        created = client.create_part_revision(
            project_id, part["id"], revision, attributes=attributes
        )
        return created["id"]
    except _api_error_type() as err:
        if getattr(err, "status", None) != 409:
            raise
        return _find_existing_revision(client, project_id, part_number, revision)


def execute_plan(client: Any, project_id: str, plan: PublishPlan, force: bool = False) -> dict:
    """Drive the apexmesh client. The run is registered first and finished
    last, so a crash mid-publish leaves an honest `running` run behind."""

    prior = client.list_runs(project_id, tool="cadx")
    already = [
        r
        for r in prior
        if r.get("externalRef") == plan.external_ref and r.get("status") == "succeeded"
    ]
    if already and not force:
        raise AlreadyPublishedError(
            f"run '{plan.external_ref}' already published as {already[0]['id']} "
            f"(use --force to publish again)"
        )

    run = client.start_run(
        project_id,
        tool="cadx",
        tool_version=plan.tool_version,
        external_ref=plan.external_ref,
        params=plan.params,
    )
    run_id = run["id"]

    n_artifacts = 0
    for spec in plan.artifacts:
        artifact = client.upload_artifact(project_id, str(spec.path), kind=spec.kind)
        client.attach_run_io(
            project_id, run_id, spec.direction, artifact_id=artifact["id"], role=spec.role
        )
        n_artifacts += 1

    n_bom = 0
    if plan.bom_parts:
        assembly = client.upsert_part(
            project_id, plan.assembly_part_number, f"{plan.params['project']} assembly"
        )
        assembly_rev = _revision_id(
            client, project_id, assembly, plan.assembly_part_number, plan.revision
        )
        for part_spec in plan.bom_parts:
            part = client.upsert_part(project_id, part_spec.part_number, part_spec.label)
            revision_id = _revision_id(
                client, project_id, part, part_spec.part_number, plan.revision,
                attributes=part_spec.attributes,
            )
            client.create_bom_line(project_id, assembly_rev, revision_id, part_spec.quantity)
            n_bom += 1

    client.update_run(project_id, run_id, status=plan.status, metrics=plan.metrics)

    return {
        "run_id": run_id,
        "external_ref": plan.external_ref,
        "status": plan.status,
        "artifacts": n_artifacts,
        "bom_parts": n_bom,
    }


def _find_existing_revision(client: Any, project_id: str, part_number: str, revision: str) -> str:
    for candidate in client.request("GET", f"projects/{project_id}/bom/parts"):
        if candidate["partNumber"] == part_number:
            for rev in candidate.get("revisions", []):
                if rev["revision"] == revision:
                    return rev["id"]
    raise KeyError(f"revision {revision} of {part_number} not found after conflict")
