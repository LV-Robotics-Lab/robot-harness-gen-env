"""Generate real qualification for legacy ``text2env.validate@2.0.0``."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from .artifacts import LocalArtifactStore
from .events import RecordingEventSink
from .handlers.text2env_validate_v2 import (
    Text2EnvValidateV2Handler,
    verify_validate_v2_lineage,
)
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
from .registry import QualificationCandidate, SkillRegistry
from .schemas.common import ArtifactRef, RunStatus, SkillQualification
from .schemas.text2env_validate_v2 import Text2EnvValidateV2Input, Text2EnvValidateV2Output
from .validate_v2_dependencies import (
    TEXT2ENV_VALIDATE_V2_SKILL_REF,
    Text2EnvValidateV2DependencyResolver,
)

_CASE_ID = "real-can-on-plate-replay-evidence"
_REGRESSION_COMMAND = "pytest -q tests/self_improving/harness/test_golden_validate_submit.py"
_IMPLEMENTATION_PATHS = (
    "self_improving/harness/artifacts.py",
    "self_improving/harness/events.py",
    "self_improving/harness/handlers/text2env_validate_v2.py",
    "self_improving/harness/qualification.py",
    "self_improving/harness/qualify_validate.py",
    "self_improving/harness/registry.py",
    "self_improving/harness/run_receipts.py",
    "self_improving/harness/runtime_assets.py",
    "self_improving/harness/schemas/base.py",
    "self_improving/harness/schemas/common.py",
    "self_improving/harness/schemas/text2env.py",
    "self_improving/harness/schemas/text2env_validate_v2.py",
    "self_improving/harness/validate_v2.py",
    "self_improving/harness/validate_v2_dependencies.py",
    "self_improving/validate_v2_snapshot.py",
)
_RUN_IDS = (
    UUID("40000000-0000-4000-8000-000000000001"),
    UUID("40000000-0000-4000-8000-000000000002"),
)


class ValidateQualificationError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ValidateQualificationSettings:
    bundle_root: Path
    scratch_root: Path
    distribution_root: Path
    scene_gen_root: Path
    ledger_contract_root: Path
    evidence_artifact_root: Path
    fixed_input: Text2EnvValidateV2Input


@dataclass(frozen=True, slots=True)
class ValidateQualificationResult:
    bundle_root: Path
    qualification: SkillQualification
    report: QualificationReportV1
    manifest: ImplementationManifestV1


@dataclass(frozen=True, slots=True)
class _SourceSnapshot:
    files: tuple[ImplementationFileV1, ...]
    scene_gen_sha256: str
    ledger_sha256: str


def generate_validate_qualification(
    settings: ValidateQualificationSettings,
) -> ValidateQualificationResult:
    """Run two real recomputations, negative cases, and a fresh-CAS reload."""

    if type(settings) is not ValidateQualificationSettings:
        raise TypeError("settings must be ValidateQualificationSettings")
    bundle_root = settings.bundle_root.expanduser().absolute()
    scratch_root = settings.scratch_root.expanduser().absolute()
    if bundle_root.exists():
        raise ValidateQualificationError("bundle_exists", "qualification bundle must be new")
    if scratch_root.exists():
        raise ValidateQualificationError("scratch_exists", "qualification scratch root must be new")
    if type(settings.fixed_input) is not Text2EnvValidateV2Input:
        raise ValidateQualificationError("input_invalid", "fixed_input must be exact typed input")

    before = _snapshot_sources(settings)
    scratch_root.mkdir(parents=True)
    source = LocalArtifactStore(settings.evidence_artifact_root.expanduser().resolve(strict=True))
    fresh = LocalArtifactStore(scratch_root / "fresh-cas")
    _copy_cas(source, fresh)
    fixed_input = Text2EnvValidateV2Input.model_validate(
        settings.fixed_input.model_dump(mode="python")
    )
    lineage = verify_validate_v2_lineage(fresh, fixed_input)
    resolver = Text2EnvValidateV2DependencyResolver(
        distribution_root=settings.distribution_root.expanduser().resolve(strict=True)
    )
    dependencies = resolver.resolve(TEXT2ENV_VALIDATE_V2_SKILL_REF, fixed_input)
    handler = Text2EnvValidateV2Handler(
        artifact_store=fresh,
        scratch_parent=_mkdir(scratch_root / "candidate-work"),
        policy_dependency=dependencies[0],
    )
    run_ids = iter(_RUN_IDS)
    registry = SkillRegistry(
        artifact_resolver=fresh,
        dependency_resolver=resolver,
        event_sink=RecordingEventSink(),
        clock=_SequenceClock(),
        run_id_factory=lambda: next(run_ids),
    )
    candidate = QualificationCandidate(
        skill_id="text2env.validate",
        version="2.0.0",
        input_schema="harness.text2env_validate_input.v2",
        output_schema="harness.text2env_validate_output.v2",
        max_attempts=1,
    )
    evaluations = tuple(
        registry.evaluate_candidate(candidate, handler, fixed_input.model_dump(mode="json"))
        for _ in _RUN_IDS
    )
    if any(item.state.status is not RunStatus.SUCCEEDED for item in evaluations):
        raise ValidateQualificationError(
            "candidate_failed",
            "the real validate candidate did not complete successfully twice",
        )
    outputs = tuple(
        Text2EnvValidateV2Output.model_validate(item.state.output) for item in evaluations
    )
    if outputs[0] != outputs[1] or not outputs[0].publishable:
        raise ValidateQualificationError(
            "candidate_not_repeatable",
            "the fixed real replay did not produce one repeatable passing decision",
        )
    rejection_reasons = _negative_cases(fresh, fixed_input, scratch_root)
    after = _snapshot_sources(settings)
    if before != after:
        raise ValidateQualificationError(
            "source_changed",
            "implementation or source-contract bytes changed during qualification",
        )

    manifest = _build_manifest(after)
    refs = {
        name: getattr(fixed_input, name).sha256
        for name in type(fixed_input).model_fields
        if name != "gate_profile"
    }
    checks = (
        QualificationCheckV1(
            name="evidence.complete_lineage",
            status="pass",
            evidence={
                "compile_run_id": str(lineage.compile.run_state.run_id),
                "replay_run_id": str(lineage.replay.run_state.run_id),
                "input_refs": refs,
            },
        ),
        QualificationCheckV1(
            name="evidence.fresh_cas_reload",
            status="pass",
            evidence={
                "copied_object_count": _cas_object_count(fresh),
                "external_paths_used": False,
            },
        ),
        QualificationCheckV1(
            name="evidence.negative_cases",
            status="pass",
            evidence={"rejected": rejection_reasons},
        ),
        QualificationCheckV1(
            name="physics.real_recomputation",
            status="pass",
            evidence={
                "gate_profile": fixed_input.gate_profile,
                "publishable": outputs[0].publishable,
                "validation_report_sha256": outputs[0].validation_report.sha256,
                "validation_status": outputs[0].validation_status.value,
            },
        ),
        QualificationCheckV1(
            name="registry.candidate_repeatability",
            status="pass",
            evidence={
                "run_ids": [str(item.state.run_id) for item in evaluations],
                "decision_sha256": outputs[0].validation_decision_receipt.sha256,
                "same_typed_output": True,
            },
        ),
        QualificationCheckV1(
            name="source.stability",
            status="pass",
            evidence={
                "changed_during_qualification": False,
                "implementation_file_count": len(after.files),
                "scene_gen_tree_sha256": after.scene_gen_sha256,
                "ledger_contract_tree_sha256": after.ledger_sha256,
            },
        ),
    )
    report = QualificationReportV1(
        schema_version=QUALIFICATION_REPORT_SCHEMA_ID,
        skill_ref=TEXT2ENV_VALIDATE_V2_SKILL_REF,
        status="pass",
        deterministic_case_id=_CASE_ID,
        regression_command=_REGRESSION_COMMAND,
        implementation_sha256=manifest.bundle_sha256,
        scene_gen_tree_sha256=after.scene_gen_sha256,
        ledger_contract_tree_sha256=after.ledger_sha256,
        checks=checks,
    )
    report_bytes = _document_bytes(report)
    qualification = SkillQualification(
        skill_ref=TEXT2ENV_VALIDATE_V2_SKILL_REF,
        status="pass",
        deterministic_case_id=_CASE_ID,
        regression_command=_REGRESSION_COMMAND,
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
    )
    _write_bundle(
        bundle_root,
        {
            "manifest.json": _document_bytes(manifest),
            "qualification.json": _document_bytes(qualification),
            "report.json": report_bytes,
        },
    )
    return ValidateQualificationResult(
        bundle_root=bundle_root.resolve(),
        qualification=qualification,
        report=report,
        manifest=manifest,
    )


def _negative_cases(
    store: LocalArtifactStore,
    fixed: Text2EnvValidateV2Input,
    scratch: Path,
) -> dict[str, str]:
    bad_runtime = _put_json(
        store,
        scratch,
        {},
        name="negative_runtime_evidence",
        schema_version="robotwin.scene_runtime_evidence.v2",
    )
    bad_media = _put_json(
        store,
        scratch,
        {"schema_version": "harness.replay_media_verification.v1", "verification": {}},
        name="negative_media_verification",
        schema_version="harness.replay_media_verification.v1",
    )
    cases = {
        "swapped_replay_run": fixed.model_copy(
            update={"replay_run_receipt": fixed.compile_run_receipt}
        ),
        "wrong_runtime_evidence": fixed.model_copy(update={"runtime_evidence": bad_runtime}),
        "wrong_media_verification": fixed.model_copy(
            update={"media_verification_receipt": bad_media}
        ),
    }
    rejected: dict[str, str] = {}
    for name, value in cases.items():
        try:
            verify_validate_v2_lineage(store, value)
        except Exception as error:
            rejected[name] = type(error).__name__
        else:
            raise ValidateQualificationError(
                "negative_case_accepted",
                f"validate lineage accepted negative case {name}",
            )
    return rejected


def _snapshot_sources(settings: ValidateQualificationSettings) -> _SourceSnapshot:
    root = settings.distribution_root.expanduser().resolve(strict=True)
    files: list[ImplementationFileV1] = []
    for relative in _IMPLEMENTATION_PATHS:
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
            raise ValidateQualificationError(
                "implementation_path_invalid",
                f"unsafe implementation path: {relative}",
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
        skill_ref=TEXT2ENV_VALIDATE_V2_SKILL_REF,
        files=snapshot.files,
        bundle_sha256="0" * 64,
        scene_gen_tree_sha256=snapshot.scene_gen_sha256,
        ledger_contract_tree_sha256=snapshot.ledger_sha256,
    )
    return provisional.model_copy(
        update={"bundle_sha256": _implementation_bundle_sha256(provisional)}
    )


def _copy_cas(source: LocalArtifactStore, destination: LocalArtifactStore) -> None:
    source_root = source.root / "sha256"
    if not source_root.is_dir():
        raise ValidateQualificationError("evidence_missing", "source CAS has no objects")
    shutil.copytree(source_root, destination.root / "sha256", dirs_exist_ok=True)


def _cas_object_count(store: LocalArtifactStore) -> int:
    return sum(1 for item in (store.root / "sha256").glob("*/*") if item.is_file())


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True)
    return path.resolve()


def _put_json(
    store: LocalArtifactStore,
    scratch: Path,
    value: object,
    *,
    name: str,
    schema_version: str,
) -> ArtifactRef:
    payload = _canonical_bytes(value)
    path = scratch / f".{name}.json"
    path.write_bytes(payload)
    try:
        return store.put_file(
            path,
            name=name,
            media_type="application/json",
            schema_version=schema_version,
        )
    finally:
        path.unlink(missing_ok=True)


def _write_bundle(root: Path, documents: dict[str, bytes]) -> None:
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    published = False
    try:
        for name in sorted(documents):
            with (staging / name).open("xb") as stream:
                stream.write(documents[name])
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(staging, root)
        published = True
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)


def _document_bytes(value: object) -> bytes:
    return _canonical_bytes(value.model_dump(mode="json"))


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


class _SequenceClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 10, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.value
        self.value += timedelta(milliseconds=1)
        return value


__all__ = [
    "ValidateQualificationError",
    "ValidateQualificationResult",
    "ValidateQualificationSettings",
    "generate_validate_qualification",
]
