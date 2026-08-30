"""Empirically qualify the fixed ``text2env.compile@1.0.0`` candidate.

This module deliberately runs the handler and its parameter-aware dependency
resolver directly.  A qualification receipt therefore cannot be used to admit
the candidate that is still being tested.  The fixed three-document bundle is
published only after three real compiles satisfy every gate below.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from scene_gen.catalog import AssetCatalog

from .artifacts import LocalArtifactStore
from .events import RecordingEventSink, RunRecorder
from .handlers.text2env_compile import Text2EnvCompileHandler
from .handlers.text2env_compile_dependencies import Text2EnvCompileDependencyResolver
from .qualification import (
    IMPLEMENTATION_MANIFEST_SCHEMA_ID,
    QUALIFICATION_REPORT_SCHEMA_ID,
    ImplementationFileV1,
    ImplementationManifestV1,
    QualificationCheckV1,
    QualificationReportV1,
    _file_snapshot,
    _implementation_bundle_sha256,
    _source_tree_sha256,
)
from .registry import HandlerResult, RunContext, _invocation_digest
from .schemas import (
    DependencyRef,
    RunStatus,
    SkillQualification,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
)

_SKILL_REF = "text2env.compile@1.0.0"
_CASE_ID = "generated-purple-hexagonal-pedestal-seed-77"
_REQUEST = "Place a purple hexagonal pedestal on the table."
_SEED = 77
_REGRESSION_COMMAND = "pytest -q tests/self_improving/harness/test_qualify_compile.py"
_RUN_IDS = (
    UUID("10000000-0000-4000-8000-000000000001"),
    UUID("10000000-0000-4000-8000-000000000002"),
    UUID("10000000-0000-4000-8000-000000000003"),
)
_IMPLEMENTATION_PATHS = (
    "self_improving/harness/artifacts.py",
    "self_improving/harness/assets.py",
    "self_improving/harness/events.py",
    "self_improving/harness/handlers/text2env_compile.py",
    "self_improving/harness/handlers/text2env_compile_dependencies.py",
    "self_improving/harness/qualification.py",
    "self_improving/harness/qualify_compile.py",
    "self_improving/harness/registry.py",
    "self_improving/harness/schemas/base.py",
    "self_improving/harness/schemas/common.py",
    "self_improving/harness/schemas/text2env.py",
)


class CompileQualificationError(RuntimeError):
    """The candidate failed a qualification precondition or acceptance gate."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class CompileQualificationSettings:
    """Explicit roots used to run and bind one deterministic qualification."""

    bundle_root: Path
    scratch_root: Path
    distribution_root: Path
    scene_gen_root: Path
    ledger_contract_root: Path
    admission_date: date


@dataclass(frozen=True)
class CompileQualificationResult:
    """The validated records atomically published by the generator."""

    bundle_root: Path
    qualification: SkillQualification
    report: QualificationReportV1
    manifest: ImplementationManifestV1


@dataclass(frozen=True)
class _CandidateRun:
    ordinal: int
    run_id: UUID
    parameters: Text2EnvCompileInput
    dependencies: tuple[DependencyRef, ...]
    invocation_sha256: str
    result: HandlerResult
    output: Text2EnvCompileOutput
    event_stages: tuple[str, ...]


@dataclass(frozen=True)
class _AcceptanceExecution:
    store: LocalArtifactStore
    runs: tuple[_CandidateRun, ...]


@dataclass(frozen=True)
class _SourceSnapshot:
    files: tuple[ImplementationFileV1, ...]
    scene_gen_sha256: str
    ledger_sha256: str


def generate_compile_qualification(
    settings: CompileQualificationSettings,
) -> CompileQualificationResult:
    """Run the real candidate three times and atomically emit its fixed bundle."""

    bundle_root = settings.bundle_root.expanduser().absolute()
    if bundle_root.exists():
        raise CompileQualificationError(
            "bundle_exists",
            f"qualification bundle already exists: {bundle_root}",
        )
    scratch_root = settings.scratch_root.expanduser().absolute()
    if scratch_root.exists():
        raise CompileQualificationError(
            "scratch_exists",
            f"qualification scratch root must be new: {scratch_root}",
        )
    if type(settings.admission_date) is not date:
        raise CompileQualificationError(
            "invalid_admission_date",
            "admission_date must be a date",
        )

    before = _snapshot_sources(settings)
    execution = _execute_candidate(settings, scratch_root=scratch_root)
    checks = _acceptance_checks(execution)
    after = _snapshot_sources(settings)
    if before != after:
        raise CompileQualificationError(
            "source_changed",
            "implementation or source-contract bytes changed during qualification",
        )

    source_check = QualificationCheckV1(
        name="source.stability",
        status="pass",
        evidence={
            "implementation_file_count": len(after.files),
            "scene_gen_tree_sha256": after.scene_gen_sha256,
            "ledger_contract_tree_sha256": after.ledger_sha256,
            "changed_during_qualification": False,
        },
    )
    manifest = _build_manifest(after)
    report = QualificationReportV1(
        schema_version=QUALIFICATION_REPORT_SCHEMA_ID,
        skill_ref=_SKILL_REF,
        status="pass",
        deterministic_case_id=_CASE_ID,
        regression_command=_REGRESSION_COMMAND,
        implementation_sha256=manifest.bundle_sha256,
        scene_gen_tree_sha256=after.scene_gen_sha256,
        ledger_contract_tree_sha256=after.ledger_sha256,
        checks=tuple(sorted((*checks, source_check), key=lambda item: item.name)),
    )
    report_bytes = _document_bytes(report)
    qualification = SkillQualification(
        skill_ref=_SKILL_REF,
        status="pass",
        deterministic_case_id=_CASE_ID,
        regression_command=_REGRESSION_COMMAND,
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
    )
    documents = {
        "manifest.json": _document_bytes(manifest),
        "qualification.json": _document_bytes(qualification),
        "report.json": report_bytes,
    }
    _write_bundle_atomically(bundle_root, documents)
    return CompileQualificationResult(
        bundle_root=bundle_root.resolve(),
        qualification=qualification,
        report=report,
        manifest=manifest,
    )


def _execute_candidate(
    settings: CompileQualificationSettings,
    *,
    scratch_root: Path,
) -> _AcceptanceExecution:
    scratch_root.mkdir(parents=True)
    store = LocalArtifactStore(scratch_root / "cas")
    catalog_path = scratch_root / "empty_catalog.json"
    catalog_path.write_text(
        json.dumps(
            AssetCatalog(
                robotwin_root=str((scratch_root / "RoboTwin").resolve()),
                objects_root=str((scratch_root / "objects").resolve()),
                entries=(),
            ).canonical_dict(),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    catalog = store.put_file(
        catalog_path,
        name="empty_asset_catalog",
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )
    handler = Text2EnvCompileHandler(
        artifact_store=store,
        work_root=scratch_root / "work",
        generated_staging_root=scratch_root / "generated-staging",
        asset_library_root=scratch_root / "asset-library",
        admission_date=settings.admission_date,
        allowed_asset_roots=(scratch_root.resolve(),),
    )
    resolver = Text2EnvCompileDependencyResolver(
        artifact_store=store,
        handler=handler,
        scene_gen_root=settings.scene_gen_root,
        ledger_contract_root=settings.ledger_contract_root,
    )
    parameters = Text2EnvCompileInput(
        request=_REQUEST,
        seed=_SEED,
        asset_catalog=catalog,
        config={"generate_missing_assets": True},
    )
    runs = tuple(
        _run_once(
            ordinal=ordinal,
            run_id=run_id,
            parameters=parameters,
            resolver=resolver,
            handler=handler,
            store=store,
        )
        for ordinal, run_id in enumerate(_RUN_IDS, start=1)
    )
    return _AcceptanceExecution(store=store, runs=runs)


def _run_once(
    *,
    ordinal: int,
    run_id: UUID,
    parameters: Text2EnvCompileInput,
    resolver: Text2EnvCompileDependencyResolver,
    handler: Text2EnvCompileHandler,
    store: LocalArtifactStore,
) -> _CandidateRun:
    dependencies = resolver.resolve(_SKILL_REF, parameters)
    invocation_sha256 = _invocation_digest(
        skill_id="text2env.compile",
        skill_version="1.0.0",
        effective_parameters=parameters,
        dependencies=dependencies,
        max_attempts=1,
    )
    sink = RecordingEventSink()
    clock = _SequenceClock(ordinal)
    recorder = RunRecorder(
        run_id=run_id,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=clock,
        sink=sink,
    )
    recorder.start(stage="preflight", attempt=1)
    try:
        result = handler(
            parameters,
            RunContext(
                run_id=run_id,
                attempt=1,
                _recorder=recorder,
                _artifact_resolver=store,
            ),
        )
        output = Text2EnvCompileOutput.model_validate(result.output)
    except Exception as error:
        raise CompileQualificationError(
            "candidate_execution_failed",
            f"qualification run {ordinal} failed: {type(error).__name__}: {error}",
        ) from error
    recorder.finish(
        status=RunStatus.SUCCEEDED,
        stage="completed",
        artifact_refs=result.artifacts,
    )
    return _CandidateRun(
        ordinal=ordinal,
        run_id=run_id,
        parameters=parameters,
        dependencies=dependencies,
        invocation_sha256=invocation_sha256,
        result=result,
        output=output,
        event_stages=tuple(item.event.stage for item in sink.events),
    )


def _acceptance_checks(
    execution: _AcceptanceExecution,
) -> tuple[QualificationCheckV1, ...]:
    runs = execution.runs
    _gate(
        len(runs) == 3,
        "run_count",
        f"qualification requires exactly three runs, observed {len(runs)}",
    )
    summaries = tuple(_inspect_run(run, execution.store) for run in runs)
    dispositions = [summary["admission"]["status"] for summary in summaries]
    _gate(
        dispositions == ["admitted", "reused", "reused"],
        "admission_lifecycle",
        f"expected admitted/reused/reused, observed {dispositions}",
    )
    asset_ids = [summary["admission"]["asset_id"] for summary in summaries]
    _gate(
        len(set(asset_ids)) == 1,
        "asset_identity_drift",
        f"generated asset identity changed across reuse: {asset_ids}",
    )

    second, third = runs[1], runs[2]
    second_output_sha = _canonical_sha256(second.output.model_dump(mode="json"))
    third_output_sha = _canonical_sha256(third.output.model_dump(mode="json"))
    second_parameter_sha = _canonical_sha256(second.parameters.model_dump(mode="json"))
    third_parameter_sha = _canonical_sha256(third.parameters.model_dump(mode="json"))
    second_dependency_sha = _canonical_sha256(
        [item.model_dump(mode="json") for item in second.dependencies]
    )
    third_dependency_sha = _canonical_sha256(
        [item.model_dump(mode="json") for item in third.dependencies]
    )
    _gate(
        second.output == third.output,
        "output_drift",
        "second and third typed outputs do not share one content identity",
    )
    _gate(
        second.parameters == third.parameters,
        "parameter_drift",
        "second and third effective typed inputs changed",
    )
    _gate(
        second.dependencies == third.dependencies,
        "dependency_drift",
        "second and third dependency receipts changed after asset reuse stabilized",
    )
    _gate(
        second.invocation_sha256 == third.invocation_sha256,
        "invocation_drift",
        "second and third invocation content identities changed",
    )
    _gate(
        runs[0].invocation_sha256 != second.invocation_sha256,
        "mutable_dependency_not_observed",
        "first admission did not change the asset-library dependency identity",
    )
    first_dependencies = {item.name: item.sha256 for item in runs[0].dependencies}
    second_dependencies = {item.name: item.sha256 for item in second.dependencies}
    changed_dependency_names = sorted(
        name
        for name in set(first_dependencies) | set(second_dependencies)
        if first_dependencies.get(name) != second_dependencies.get(name)
    )
    _gate(
        changed_dependency_names == ["asset-library-state"],
        "unexpected_mutable_dependency",
        "first admission must change only the asset-library-state dependency; "
        f"observed {changed_dependency_names}",
    )

    package_ids = [summary["package"]["package_id"] for summary in summaries]
    _gate(
        len(set(package_ids)) == 1,
        "package_identity_drift",
        f"compiled package identity changed across admission/reuse: {package_ids}",
    )

    ledger_digests = [summary["ledger"]["sha256"] for summary in summaries]
    _gate(
        len(set(ledger_digests)) == 1,
        "ledger_drift",
        f"admitted ledger changed across reuse: {ledger_digests}",
    )
    return (
        QualificationCheckV1(
            name="admission.lifecycle",
            status="pass",
            evidence={
                "expected": ["admitted", "reused", "reused"],
                "observed": dispositions,
                "asset_ids": asset_ids,
                "reports": [summary["admission"] for summary in summaries],
            },
        ),
        QualificationCheckV1(
            name="artifacts.cas_resolution",
            status="pass",
            evidence={
                "runs": [summary["artifacts"] for summary in summaries],
            },
        ),
        QualificationCheckV1(
            name="invocation.stability",
            status="pass",
            evidence={
                "first_invocation_sha256": runs[0].invocation_sha256,
                "second_invocation_sha256": second.invocation_sha256,
                "third_invocation_sha256": third.invocation_sha256,
                "second_dependency_sha256": second_dependency_sha,
                "third_dependency_sha256": third_dependency_sha,
                "second_parameter_sha256": second_parameter_sha,
                "third_parameter_sha256": third_parameter_sha,
                "dependency_count": len(second.dependencies),
                "first_to_second_changed_dependency_names": changed_dependency_names,
                "dependencies": [item.model_dump(mode="json") for item in second.dependencies],
                "second_output_sha256": second_output_sha,
                "third_output_sha256": third_output_sha,
            },
        ),
        QualificationCheckV1(
            name="ledger.v3_files",
            status="pass",
            evidence={
                "schema_version": summaries[-1]["ledger"]["schema_version"],
                "sha256": ledger_digests[-1],
                "file_record_count": summaries[-1]["ledger"]["file_record_count"],
                "check_files": True,
                "violation_count": 0,
            },
        ),
        QualificationCheckV1(
            name="package.binding",
            status="pass",
            evidence={
                "runs": [summary["package"] for summary in summaries],
            },
        ),
        QualificationCheckV1(
            name="static_validation.boundary",
            status="pass",
            evidence={
                "required_status": "incomplete",
                "physical_runtime_claimed": False,
                "runs": [summary["static_validation"] for summary in summaries],
            },
        ),
    )


def _inspect_run(run: _CandidateRun, store: LocalArtifactStore) -> dict[str, Any]:
    required_stages = {
        "compile.asset_admission.completed",
        "compile.completed",
        "compile.static_validation.completed",
    }
    missing_stages = sorted(required_stages.difference(run.event_stages))
    _gate(
        not missing_stages,
        "execution_events_missing",
        f"run {run.ordinal} did not emit required execution callbacks: {missing_stages}",
    )
    try:
        resolved_artifacts = [store.resolve(artifact) for artifact in run.result.artifacts]
    except Exception as error:
        raise CompileQualificationError(
            "artifact_unresolved",
            f"run {run.ordinal} returned an unavailable or corrupt CAS artifact: {error}",
        ) from error
    _gate(
        bool(resolved_artifacts),
        "missing_artifacts",
        f"run {run.ordinal} returned no artifacts",
    )
    by_sha256 = {item.ref.sha256: item.ref for item in resolved_artifacts}

    admission_refs = [
        item.ref
        for item in resolved_artifacts
        if item.ref.schema_version == "harness.generated_asset_admission.v1"
    ]
    _gate(
        len(admission_refs) == 1,
        "admission_report_count",
        f"run {run.ordinal} must return exactly one admission report",
    )
    admission_ref = admission_refs[0]
    admission = _load_json(store.resolve(admission_ref).path, label="admission report")
    assets = admission.get("assets")
    _gate(
        isinstance(assets, list) and len(assets) == 1,
        "admission_asset_count",
        f"run {run.ordinal} must admit or reuse exactly one generated asset",
    )
    disposition = assets[0].get("disposition")
    _gate(
        admission.get("status") == disposition,
        "admission_status_mismatch",
        f"run {run.ordinal} admission status and disposition disagree",
    )
    _gate(
        admission.get("physical_qualification") == "pending_settle",
        "admission_physical_boundary",
        f"run {run.ordinal} admission report overstated physical qualification",
    )
    _gate(
        assets[0].get("qualification") == "generation_qc_only",
        "asset_qualification_boundary",
        f"run {run.ordinal} generated asset overstated its qualification",
    )

    ledger_path = Path(str(assets[0].get("ledger_path", "")))
    ledger = _load_json(ledger_path, label="asset ledger")
    from .assets import _ledger_contract

    contract = _ledger_contract()
    violations = contract.validate_ledger(ledger, check_files=True)
    _gate(
        not violations,
        "ledger_invalid",
        f"run {run.ordinal} ledger violations: {_violation_values(violations)}",
    )
    file_record_count = sum(
        len(representation.get("files", []))
        for model in ledger.get("models", [])
        for representation in model.get("representations", [])
    )
    _gate(
        file_record_count > 0,
        "ledger_files_missing",
        f"run {run.ordinal} ledger has no representation files records",
    )
    ledger_sha256 = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
    _gate(
        assets[0].get("ledger_sha256") == ledger_sha256,
        "ledger_receipt_mismatch",
        f"run {run.ordinal} admission receipt does not bind the ledger bytes",
    )

    manifest_ref = run.output.environment_package.package_manifest
    manifest = _load_json(store.resolve(manifest_ref).path, label="package manifest")
    package_files = manifest.get("files")
    _gate(
        isinstance(package_files, list) and bool(package_files),
        "package_files_missing",
        f"run {run.ordinal} package manifest has no files",
    )
    missing_package_files = [
        record.get("path")
        for record in package_files
        if record.get("sha256") not in by_sha256
        or by_sha256[record["sha256"]].bytes != record.get("bytes")
    ]
    _gate(
        not missing_package_files,
        "package_artifact_unresolved",
        f"run {run.ordinal} package files are absent from CAS: {missing_package_files}",
    )
    package = run.output.environment_package
    bindings = {
        "package_id": package.package_id,
        "resolved_scene_sha256": manifest.get("resolved_scene_sha256"),
        "scene_spec_sha256": manifest.get("source_scene_spec_sha256"),
        "asset_catalog_sha256": manifest.get("asset_catalog_sha256"),
    }
    _gate(
        bindings["package_id"] == bindings["resolved_scene_sha256"] == package.resolved_scene_sha256
        and bindings["scene_spec_sha256"] == package.scene_spec_sha256
        and bindings["asset_catalog_sha256"] == package.asset_catalog.sha256,
        "package_binding",
        f"run {run.ordinal} package content identities disagree: {bindings}",
    )

    validation_ref = run.output.static_validation
    validation = _load_json(
        store.resolve(validation_ref).path,
        label="static validation report",
    )
    _gate(
        validation.get("status") == "incomplete"
        and validation.get("fail_count") == 0
        and isinstance(validation.get("not_run_count"), int)
        and validation["not_run_count"] > 0,
        "static_validation_boundary",
        f"run {run.ordinal} static validation must be honestly incomplete: {validation}",
    )
    return {
        "admission": {
            "status": admission["status"],
            "asset_id": assets[0].get("asset_id"),
            "report_sha256": admission_ref.sha256,
            "ledger_sha256": ledger_sha256,
            "physical_qualification": admission["physical_qualification"],
        },
        "artifacts": {
            "run": run.ordinal,
            "event_count": len(run.event_stages),
            "event_stages": list(run.event_stages),
            "resolved_count": len(resolved_artifacts),
            "unique_sha256_count": len(by_sha256),
            "all_content_verified": True,
        },
        "ledger": {
            "schema_version": ledger["schema_version"],
            "sha256": ledger_sha256,
            "file_record_count": file_record_count,
        },
        "package": {
            "run": run.ordinal,
            "package_id": package.package_id,
            "manifest_sha256": manifest_ref.sha256,
            "file_count": len(package_files),
            "all_files_in_cas": True,
        },
        "static_validation": {
            "run": run.ordinal,
            "report_sha256": validation_ref.sha256,
            "status": validation["status"],
            "fail_count": validation["fail_count"],
            "not_run_count": validation["not_run_count"],
        },
    }


def _snapshot_sources(settings: CompileQualificationSettings) -> _SourceSnapshot:
    distribution_root = settings.distribution_root.expanduser().resolve(strict=True)
    files: list[ImplementationFileV1] = []
    for relative in _IMPLEMENTATION_PATHS:
        candidate = distribution_root
        for part in Path(relative).parts:
            candidate = candidate / part
            if candidate.is_symlink():
                raise CompileQualificationError(
                    "implementation_path_invalid",
                    f"implementation path contains a symlink: {relative}",
                )
        path = candidate.resolve(strict=True)
        if not path.is_relative_to(distribution_root) or not path.is_file():
            raise CompileQualificationError(
                "implementation_path_invalid",
                f"implementation path is not a file below distribution root: {relative}",
            )
        size, sha256 = _file_snapshot(path)
        files.append(ImplementationFileV1(path=relative, bytes=size, sha256=sha256))
    return _SourceSnapshot(
        files=tuple(files),
        scene_gen_sha256=_source_tree_sha256(settings.scene_gen_root, label="scene_gen"),
        ledger_sha256=_source_tree_sha256(
            settings.ledger_contract_root,
            label="ledger contract",
        ),
    )


def _build_manifest(snapshot: _SourceSnapshot) -> ImplementationManifestV1:
    provisional = ImplementationManifestV1(
        schema_version=IMPLEMENTATION_MANIFEST_SCHEMA_ID,
        skill_ref=_SKILL_REF,
        files=snapshot.files,
        bundle_sha256="0" * 64,
        scene_gen_tree_sha256=snapshot.scene_gen_sha256,
        ledger_contract_tree_sha256=snapshot.ledger_sha256,
    )
    return provisional.model_copy(
        update={"bundle_sha256": _implementation_bundle_sha256(provisional)}
    )


def _write_bundle_atomically(bundle_root: Path, documents: dict[str, bytes]) -> None:
    bundle_root.parent.mkdir(parents=True, exist_ok=True)
    if bundle_root.exists():
        raise FileExistsError(f"qualification bundle already exists: {bundle_root}")
    staging = Path(tempfile.mkdtemp(prefix=f".{bundle_root.name}.", dir=bundle_root.parent))
    published = False
    try:
        for name in sorted(documents):
            path = staging / name
            with path.open("xb") as stream:
                stream.write(documents[name])
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(staging, bundle_root)
        published = True
        directory_fd = os.open(bundle_root.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        shutil.rmtree(bundle_root if published else staging, ignore_errors=True)
        raise


def _document_bytes(value: Any) -> bytes:
    payload = value.model_dump(mode="json")
    return (
        json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CompileQualificationError(
            "evidence_unreadable",
            f"{label} is missing or invalid: {path}",
        ) from error
    if not isinstance(value, dict):
        raise CompileQualificationError(
            "evidence_shape",
            f"{label} must contain a JSON object: {path}",
        )
    return value


def _gate(condition: bool, reason: str, message: str) -> None:
    if not condition:
        raise CompileQualificationError(reason, message)


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _violation_values(violations: list[Any]) -> list[dict[str, str]]:
    return [{"path": str(item.path), "code": str(item.code)} for item in violations[:8]]


class _SequenceClock:
    def __init__(self, ordinal: int) -> None:
        self._value = datetime(2026, 8, 31, ordinal, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self._value
        self._value += timedelta(milliseconds=1)
        return value
