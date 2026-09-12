"""Capture and isolate one unreleased development preview evidence record."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .runtime_capability import RuntimeCapabilityError, _robotwin_identity
from .schemas.common import ArtifactRef
from .schemas.limited_image_preview_development import (
    DEVELOPMENT_EXECUTION_CONTEXT_SCHEMA_ID,
    DEVELOPMENT_EXECUTION_RECORD_SCHEMA_ID,
    DevelopmentCheckV1,
    DevelopmentEnvironmentVariableV1,
    DevelopmentExecutionContextV1,
    DevelopmentExecutionEnvironmentV1,
    DevelopmentExecutionRecordV1,
    DevelopmentGitSourceIdentityV1,
    development_execution_record_sha256,
)

_ENVIRONMENT_NAMES = (
    "CUDA_VISIBLE_DEVICES",
    "GENESIS_BACKEND",
    "MKL_NUM_THREADS",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "PYTHONHASHSEED",
    "PYTHONPATH",
)


def _canonical(value: object, *, newline: bool = False) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    raw = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return raw + (b"\n" if newline else b"")


def _publish_bytes(store, data, *, name, media_type, schema_version):
    with tempfile.TemporaryDirectory(prefix="preview-development-") as directory:
        path = Path(directory) / name
        path.write_bytes(data)
        return store.put_file(path, name=name, media_type=media_type, schema_version=schema_version)


def _sorted_refs(refs):
    return tuple(
        sorted(
            set(refs),
            key=lambda ref: (ref.sha256, ref.name, ref.media_type, ref.schema_version or ""),
        )
    )


def _materialized_path(ref):
    suffix = {
        "application/json": ".json",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "text/plain": ".txt",
        "video/mp4": ".mp4",
    }.get(ref.media_type, ".bin")
    return f"objects/sha256/{ref.sha256[:2]}/{ref.sha256}{suffix}"


def capture_git_source_identity(repository_root: Path) -> DevelopmentGitSourceIdentityV1:
    """Bind commit plus tracked/untracked bytes without storing source or file names."""

    root = Path(repository_root).expanduser()
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("development source must name an absolute repository root")
    root = root.resolve()
    try:
        values = _robotwin_identity(root)
    except RuntimeCapabilityError as error:
        raise ValueError(f"development repository root cannot be identified: {error}") from error
    return DevelopmentGitSourceIdentityV1(repository_locator=str(root), **values)


def _environment() -> DevelopmentExecutionEnvironmentV1:
    executable = Path(sys.executable).resolve(strict=True)
    data = executable.read_bytes()
    return DevelopmentExecutionEnvironmentV1(
        python_executable=str(executable),
        python_executable_sha256=hashlib.sha256(data).hexdigest(),
        python_executable_bytes=len(data),
        python_implementation=platform.python_implementation(),
        python_version=platform.python_version(),
        python_abi=sys.implementation.cache_tag or "unknown",
        platform=platform.platform(),
        variables=tuple(
            DevelopmentEnvironmentVariableV1(name=name, value=os.environ.get(name))
            for name in _ENVIRONMENT_NAMES
        ),
    )


@dataclass(frozen=True, slots=True)
class CapturedDevelopmentExecutionContext:
    context: DevelopmentExecutionContextV1
    ref: ArtifactRef


def capture_development_execution_context(
    *,
    artifact_store: LocalArtifactStore,
    repository_root: Path,
    settings_path: Path,
) -> CapturedDevelopmentExecutionContext:
    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("development context requires the exact preview CAS")
    path = Path(settings_path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("development settings must be an absolute regular file")
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("development settings must contain JSON") from error
    if not isinstance(value, dict):
        raise ValueError("development settings must contain a JSON object")
    configuration = _publish_bytes(
        artifact_store,
        raw,
        name="limited-image-preview-settings.json",
        media_type="application/json",
        schema_version=None,
    )
    context = DevelopmentExecutionContextV1(
        schema_version=DEVELOPMENT_EXECUTION_CONTEXT_SCHEMA_ID,
        execution_mode="development",
        qualification_status="development_unqualified",
        source_identity=capture_git_source_identity(repository_root),
        environment=_environment(),
        configuration=configuration,
    )
    ref = _publish_bytes(
        artifact_store,
        _canonical(context),
        name="development-execution-context.json",
        media_type="application/json",
        schema_version=DEVELOPMENT_EXECUTION_CONTEXT_SCHEMA_ID,
    )
    return CapturedDevelopmentExecutionContext(context=context, ref=ref)


def _check(name, status, detail, evidence=()):
    return DevelopmentCheckV1(
        name=name,
        status=status,
        detail=detail,
        evidence=_sorted_refs(evidence),
    )


def build_development_execution_record(
    *,
    workflow_run_id,
    principal_id,
    idempotency_key,
    workflow_head,
    context,
    context_ref,
    input_artifacts,
    output_artifacts,
    log_artifacts,
    physical_evidence,
    exact_skill_evidence,
    created_at,
):
    """Describe executed evidence and the three deliberately omitted release checks."""

    inputs = _sorted_refs(input_artifacts)
    outputs = _sorted_refs(output_artifacts)
    logs = _sorted_refs(log_artifacts)
    physics = _sorted_refs(physical_evidence)
    skills = _sorted_refs(exact_skill_evidence)
    checks = (
        _check(
            "input_output_hash_binding",
            "executed",
            "CAS binds request and result bytes",
            (*inputs, *outputs),
        ),
        _check(
            "exact_skill_qualification",
            "executed",
            "every executed Skill retains its qualified identity",
            skills,
        ),
        _check(
            "workflow_receipt_closure",
            "executed",
            "Golden journal retains the current workflow head",
            (workflow_head,),
        ),
        _check(
            "physical_validation", "executed", "physical evidence is retained separately", physics
        ),
        _check(
            "media_closure",
            "executed",
            "generated media remains content addressed",
            tuple(ref for ref in outputs if ref.media_type.startswith(("image/", "video/")))
            or outputs,
        ),
        _check(
            "codex_attempt_journal",
            "executed",
            "Codex attempts and failures remain inspectable",
            logs,
        ),
        _check(
            "development_source_capture",
            "executed",
            "git, interpreter, environment and settings are fixed",
            (context_ref, context.configuration),
        ),
        _check(
            "clean_release_tree", "omitted", "development may use a dirty outer deployment tree"
        ),
        _check(
            "formal_preview_library_admission",
            "omitted",
            "development artifacts are not admitted to the release library",
        ),
        _check(
            "formal_preview_publication",
            "omitted",
            "development results have no publication authority",
        ),
    )
    values = dict(
        schema_version=DEVELOPMENT_EXECUTION_RECORD_SCHEMA_ID,
        record_id="0" * 64,
        workflow_run_id=workflow_run_id,
        principal_id=principal_id,
        idempotency_key=idempotency_key,
        workspace="ephemeral",
        execution_mode="development",
        qualification_status="development_unqualified",
        exact_skills_qualified=True,
        changed_exact_skill_closure_allowed=False,
        formal_library_written=False,
        workflow_head=workflow_head,
        development_context=context_ref,
        source_identity=context.source_identity,
        environment=context.environment,
        configuration=context.configuration,
        input_artifacts=inputs,
        output_artifacts=outputs,
        log_artifacts=logs,
        physical_evidence=physics,
        checks=checks,
        created_at=created_at,
    )
    unhashed = DevelopmentExecutionRecordV1.model_construct(**values)
    values = unhashed.model_dump(mode="python")
    values["record_id"] = development_execution_record_sha256(unhashed)
    return DevelopmentExecutionRecordV1.model_validate(values)


class DevelopmentArtifactStore:
    """One isolated debug locator over CAS; it grants no workflow or release authority."""

    def __init__(self, *, artifact_store, root, context_ref):
        if type(artifact_store) is not LocalArtifactStore or type(context_ref) is not ArtifactRef:
            raise TypeError("development artifacts require the exact preview CAS and context ref")
        candidate = Path(root).expanduser()
        if not candidate.is_absolute() or ".." in candidate.parts or candidate.is_symlink():
            raise ValueError("development artifact root must be an absolute non-symlink path")
        self.store = artifact_store
        self.root = candidate.resolve()
        self.context_ref = context_ref

    def _path(self, workflow_id):
        return self.root / f"{UUID(str(workflow_id))}.json"

    def commit(self, record):
        if type(record) is not DevelopmentExecutionRecordV1:
            raise TypeError("development artifact commit requires the exact record")
        if record.development_context != self.context_ref:
            raise ValueError("development record belongs to another execution context")
        for ref in (
            record.workflow_head,
            record.development_context,
            record.configuration,
            *record.input_artifacts,
            *record.output_artifacts,
            *record.log_artifacts,
            *record.physical_evidence,
        ):
            self.store.resolve(ref)
        ref = _publish_bytes(
            self.store,
            _canonical(record),
            name="development-execution-record.json",
            media_type="application/json",
            schema_version=DEVELOPMENT_EXECUTION_RECORD_SCHEMA_ID,
        )
        path = self._path(record.workflow_run_id)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError("development artifact root changed to a symlink")
        association = _canonical(
            {
                "kind": "limited_preview_development_association.v1",
                "workflow_run_id": str(record.workflow_run_id),
                "context": self.context_ref.model_dump(mode="json"),
                "record": ref.model_dump(mode="json"),
            },
            newline=True,
        )
        descriptor, temporary_name = tempfile.mkstemp(prefix=".development.", dir=self.root)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(association)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                existing, value = self.read(record.workflow_run_id)
                if existing != ref or value != record:
                    raise ValueError("development workflow is already bound to another record")
                return existing
        finally:
            temporary.unlink(missing_ok=True)
        return ref

    def try_read(self, workflow_id):
        return None if not self._path(workflow_id).exists() else self.read(workflow_id)

    def read(self, workflow_id):
        path = self._path(workflow_id)
        if path.is_symlink() or not path.is_file():
            raise ValueError("development workflow has no isolated result")
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
            ref = ArtifactRef.model_validate(value["record"])
            context = ArtifactRef.model_validate(value["context"])
            record_raw = self.store.resolve(ref).path.read_bytes()
            record = DevelopmentExecutionRecordV1.model_validate_json(record_raw)
        except (
            ArtifactResolutionError,
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
            json.JSONDecodeError,
        ) as error:
            raise ValueError("development artifact association is corrupt") from error
        expected = {
            "kind": "limited_preview_development_association.v1",
            "workflow_run_id": str(UUID(str(workflow_id))),
            "context": self.context_ref.model_dump(mode="json"),
            "record": ref.model_dump(mode="json"),
        }
        if (
            raw != _canonical(expected, newline=True)
            or value != expected
            or context != self.context_ref
            or record_raw != _canonical(record)
            or record.workflow_run_id != UUID(str(workflow_id))
            or record.development_context != self.context_ref
        ):
            raise ValueError("development artifact association is corrupt")
        return ref, record

    def materialize(self, workflow_id, destination):
        """Copy one debug closure for review without opening a release library."""

        candidate = Path(destination).expanduser()
        if (
            not candidate.is_absolute()
            or ".." in candidate.parts
            or candidate.exists()
            or candidate.is_symlink()
        ):
            raise ValueError("development output must be an absolute new directory")
        target = candidate.resolve()
        record_ref, record = self.read(workflow_id)
        record_raw = self.store.resolve(record_ref).path.read_bytes()
        context_raw = self.store.resolve(self.context_ref).path.read_bytes()
        try:
            context = DevelopmentExecutionContextV1.model_validate_json(context_raw)
        except ValidationError as error:
            raise ValueError("development context is corrupt") from error
        if (
            record_raw != _canonical(record)
            or context_raw != _canonical(context)
            or context.source_identity != record.source_identity
            or context.environment != record.environment
            or context.configuration != record.configuration
        ):
            raise ValueError("development context differs from its result")

        groups = {
            "inputs": record.input_artifacts,
            "outputs": record.output_artifacts,
            "logs": record.log_artifacts,
            "physical_evidence": record.physical_evidence,
        }
        supporting = _sorted_refs(
            (
                record.workflow_head,
                record.configuration,
                *(ref for check in record.checks for ref in check.evidence),
            )
        )
        grouped = {**groups, "supporting": supporting}
        review = _sorted_refs((*record.output_artifacts, *record.physical_evidence))
        categories = {
            "images": tuple(ref for ref in review if ref.media_type.startswith("image/")),
            "videos": tuple(ref for ref in review if ref.media_type.startswith("video/")),
            "environment_packages": tuple(
                ref
                for ref in review
                if ref.schema_version
                in {"harness.environment_package.v1", "harness.x2env_environment_package.v1"}
            ),
            "validation_reports": tuple(
                ref for ref in review if ref.schema_version == "harness.limited_scene_validation.v1"
            ),
        }
        entrypoints = {
            "record": "development-record.json",
            "context": "development-context.json",
            **{
                name: sorted({_materialized_path(ref) for ref in refs})
                for name, refs in {**groups, **categories}.items()
            },
        }

        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
        try:
            (staging / "development-record.json").write_bytes(record_raw)
            (staging / "development-context.json").write_bytes(context_raw)
            members = {}
            for role, refs in grouped.items():
                for ref in refs:
                    relative = _materialized_path(ref)
                    member = members.setdefault(
                        relative,
                        {
                            "path": relative,
                            "sha256": ref.sha256,
                            "bytes": ref.bytes,
                            "refs": [],
                            "roles": [],
                        },
                    )
                    encoded = ref.model_dump(mode="json")
                    if encoded not in member["refs"]:
                        member["refs"].append(encoded)
                    if role not in member["roles"]:
                        member["roles"].append(role)
                    output = staging / relative
                    if not output.exists():
                        output.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(self.store.resolve(ref).path, output)
                    data = output.read_bytes()
                    if len(data) != ref.bytes or hashlib.sha256(data).hexdigest() != ref.sha256:
                        raise ValueError("materialized development member differs from CAS")
            for member in members.values():
                member["refs"].sort(
                    key=lambda ref: (
                        ref["sha256"],
                        ref["name"],
                        ref["media_type"],
                        ref.get("schema_version") or "",
                    )
                )
                member["roles"].sort()
            index = {
                "kind": "limited_preview_development_materialization.v1",
                "workflow_run_id": str(record.workflow_run_id),
                "qualification_status": "development_unqualified",
                "record_ref": record_ref.model_dump(mode="json"),
                "context_ref": self.context_ref.model_dump(mode="json"),
                "entrypoints": entrypoints,
                "members": [members[path] for path in sorted(members)],
            }
            (staging / "index.json").write_bytes(_canonical(index, newline=True))
            if target.exists():
                raise ValueError("development output must be an absolute new directory")
            os.replace(staging, target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return target


__all__ = [
    "CapturedDevelopmentExecutionContext",
    "DevelopmentArtifactStore",
    "build_development_execution_record",
    "capture_development_execution_context",
    "capture_git_source_identity",
]
