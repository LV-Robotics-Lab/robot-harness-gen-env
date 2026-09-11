"""One operator bootstrap for the bounded image exact-reuse preview deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .limited_image_preview_factory import NativeImageApplicationSettings
from .schemas.common import ArtifactRef
from .schemas.limited_image_preview_execution import PreviewLayout

_NATIVE_SKILLS = (
    "genesis.observe@3.0.0",
    "image2env.compile@2.0.0",
    "image2env.replay@2.0.0",
)
_MAX_IMPORTED_OBJECT_BYTES = 1_048_576


def _absolute(value: Path) -> Path:
    if not value.is_absolute() or ".." in value.parts:
        raise ValueError("bootstrap paths must be absolute without traversal")
    return value


class NativeProjectionSeed(BaseModel):
    """Original typed admission and descriptor copied byte-for-byte into deployment CAS."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    skill_ref: Literal[
        "genesis.observe@3.0.0",
        "image2env.compile@2.0.0",
        "image2env.replay@2.0.0",
    ]
    source_cas_root: Path
    admission_ref: ArtifactRef
    descriptor_ref: ArtifactRef

    _source_is_absolute = field_validator("source_cas_root")(_absolute)

    @model_validator(mode="after")
    def exact_documents(self):
        if self.admission_ref.schema_version != "harness.typed_qualification_admission.v1":
            raise ValueError("native seed must identify a typed qualification admission")
        if self.descriptor_ref.schema_version not in {
            "harness.skill_descriptor.v1",
            "harness.skill_descriptor.v2",
        }:
            raise ValueError("native seed must identify a supported Skill descriptor")
        return self


class PublishQualificationSeed(BaseModel):
    """The four missing saved-M1 objects needed by the already present deep closure."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_cas_root: Path
    manifest_ref: ArtifactRef
    supporting_refs: tuple[ArtifactRef, ArtifactRef, ArtifactRef]
    project_id: Literal["saved-m1-preview"]

    _source_is_absolute = field_validator("source_cas_root")(_absolute)

    @model_validator(mode="after")
    def exact_manifest(self):
        if self.manifest_ref.schema_version != "harness.limited_image_preview_manifest.v1":
            raise ValueError("publish seed must identify the saved preview manifest")
        refs = (self.manifest_ref, *self.supporting_refs)
        if len({item.sha256 for item in refs}) != 4:
            raise ValueError("publish seed must contain four distinct fixed CAS objects")
        return self


class BootstrapDeployment(BaseModel):
    """Internal Codex process facts; the user never supplies these per task."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    codex_executable: Path
    approved_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    server_python: Path
    server_workdir: Path
    model: str = Field(min_length=1)
    evidence_root: Path
    max_attempts: int = Field(gt=0)
    call_budget: int = Field(gt=0)
    attempt_seconds: int = Field(gt=0)

    _paths_are_absolute = field_validator(
        "codex_executable", "server_python", "server_workdir", "evidence_root"
    )(_absolute)


class LimitedImagePreviewBootstrapPlan(BaseModel):
    """All operator-selected identities needed for the one supported deployment."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["harness.limited_image_preview_bootstrap_plan.v1"]
    repository_root: Path
    expected_repository_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    native_settings: NativeImageApplicationSettings
    native_projection_seeds: tuple[NativeProjectionSeed, ...]
    asset_catalog: ArtifactRef
    source_scene: ArtifactRef
    qualification_run_id: UUID
    table_center_m: tuple[float, float, float]
    table_size_m: tuple[float, float, float]
    table_friction: float = Field(ge=0, allow_inf_nan=False)
    seed: int = Field(ge=0)
    principal_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    golden_database_path: Path
    revised_state_root: Path
    revised_qualification: ArtifactRef
    revised_admission_ref: ArtifactRef
    revised_registered_run_id: UUID
    revised_registered_request: ArtifactRef
    revised_registered_receipt: ArtifactRef
    expected_revised_layout: PreviewLayout
    shared_state_root: Path
    qualification_library_root: Path
    library_root: Path
    publish_seed: PublishQualificationSeed
    checkpoint_path: Path
    output_directory: Path
    deployment: BootstrapDeployment

    _paths_are_absolute = field_validator(
        "repository_root",
        "golden_database_path",
        "revised_state_root",
        "shared_state_root",
        "qualification_library_root",
        "library_root",
        "checkpoint_path",
        "output_directory",
    )(_absolute)

    @model_validator(mode="after")
    def exact_limited_topology(self):
        skills = tuple(item.skill_ref for item in self.native_projection_seeds)
        if len(skills) != 3 or set(skills) != set(_NATIVE_SKILLS):
            raise ValueError("bootstrap requires exactly three native projection seeds")
        if len(set(skills)) != len(skills):
            raise ValueError("native projection seed Skill identities must be unique")
        if self.revised_qualification.schema_version != "harness.skill_qualification.v1":
            raise ValueError("revised qualification must use the exact qualification schema")
        if (
            self.revised_admission_ref.schema_version != "harness.typed_qualification_admission.v1"
            or self.revised_registered_request.schema_version
            != "harness.image2env_genesis_compile_request.v2"
            or self.revised_registered_receipt.schema_version
            != "harness.image2env_genesis_compile_receipt.v2"
        ):
            raise ValueError("revised bootstrap identities use the wrong schemas")
        storage = (
            self.golden_database_path,
            self.revised_state_root,
            self.shared_state_root,
            self.qualification_library_root,
            self.library_root,
            self.checkpoint_path,
            self.output_directory,
        )
        if len({path.resolve() for path in storage}) != len(storage):
            raise ValueError("bootstrap output and authority paths must be distinct")
        return self


class BootstrapCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["harness.limited_image_preview_bootstrap_check.v1"]
    status: Literal["ready", "complete"]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_objects_verified: Literal[10]
    missing_target_imports: int = Field(ge=0, le=10)
    existing_checkpoint: bool
    existing_output: bool


class BootstrapCheckpoint(BaseModel):
    """Restart evidence; every referenced authority is re-read before use."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["harness.limited_image_preview_bootstrap_result.v1"]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    stage: Literal["planned", "imports_complete"]
    imported_objects: tuple[ArtifactRef, ...] = ()

    @model_validator(mode="after")
    def stage_is_complete(self):
        if self.stage == "planned" and self.imported_objects:
            raise ValueError("planned bootstrap cannot claim imported objects")
        if self.stage == "imports_complete" and len(self.imported_objects) != 10:
            raise ValueError("completed import stage requires exactly ten objects")
        if len({item.sha256 for item in self.imported_objects}) != len(self.imported_objects):
            raise ValueError("bootstrap imported object identities must be unique")
        return self


def _canonical(value: BaseModel) -> bytes:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )


def _read_plan(path: Path) -> tuple[LimitedImagePreviewBootstrapPlan, bytes]:
    raw = path.read_bytes()
    plan = LimitedImagePreviewBootstrapPlan.model_validate_json(raw)
    if raw != _canonical(plan):
        raise ValueError("bootstrap operator plan must be canonical JSON with one final newline")
    return plan, raw


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _load_checkpoint(
    plan: LimitedImagePreviewBootstrapPlan,
    *,
    plan_sha256: str,
) -> BootstrapCheckpoint:
    if not plan.checkpoint_path.exists():
        return BootstrapCheckpoint(
            schema_version="harness.limited_image_preview_bootstrap_result.v1",
            plan_sha256=plan_sha256,
            repository_commit=plan.expected_repository_commit,
            stage="planned",
        )
    raw = plan.checkpoint_path.read_bytes()
    checkpoint = BootstrapCheckpoint.model_validate_json(raw)
    if raw != _canonical(checkpoint):
        raise ValueError("bootstrap checkpoint is not canonical")
    if (
        checkpoint.plan_sha256 != plan_sha256
        or checkpoint.repository_commit != plan.expected_repository_commit
    ):
        raise ValueError("bootstrap checkpoint belongs to another operator plan or source commit")
    return checkpoint


def _save_checkpoint(plan: LimitedImagePreviewBootstrapPlan, value: BootstrapCheckpoint) -> None:
    _atomic_write(plan.checkpoint_path, _canonical(value))


def _git_value(repository: Path, *arguments: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise ValueError("bootstrap repository identity cannot be read")
    return process.stdout.strip()


def _resolve_source(store: LocalArtifactStore, ref: ArtifactRef) -> Path:
    if ref.bytes > _MAX_IMPORTED_OBJECT_BYTES:
        raise ValueError("bootstrap imports are limited to fixed small CAS objects")
    try:
        return store.resolve(ref).path
    except ArtifactResolutionError as error:
        raise ValueError(f"source CAS object is unavailable or corrupt: {ref.sha256}") from error


def _existing_target(store: LocalArtifactStore, ref: ArtifactRef) -> bool:
    path = store.root / "sha256" / ref.sha256[:2] / ref.sha256
    if not path.exists():
        return False
    store.resolve(ref)
    return True


def _required_target_refs(plan: LimitedImagePreviewBootstrapPlan) -> tuple[ArtifactRef, ...]:
    native = plan.native_settings
    return (
        native.compile_qualification,
        native.scene_qualification,
        native.observation_qualification,
        native.validation_qualification,
        native.asset_manifest,
        native.scene_manifest,
        native.capture_manifest,
        plan.asset_catalog,
        plan.source_scene,
        plan.revised_qualification,
        plan.revised_admission_ref,
        plan.revised_registered_request,
        plan.revised_registered_receipt,
    )


def _imports(
    plan: LimitedImagePreviewBootstrapPlan,
) -> tuple[tuple[Path, ArtifactRef], ...]:
    values = tuple(
        (seed.source_cas_root, ref)
        for seed in plan.native_projection_seeds
        for ref in (seed.admission_ref, seed.descriptor_ref)
    ) + tuple(
        (plan.publish_seed.source_cas_root, ref)
        for ref in (plan.publish_seed.manifest_ref, *plan.publish_seed.supporting_refs)
    )
    return tuple(sorted(values, key=lambda item: (item[1].sha256, item[1].name)))


def check_limited_image_preview_bootstrap(plan_path: Path) -> BootstrapCheckResult:
    """Pure-read verification of the operator plan and all pre-existing identities."""

    plan, raw = _read_plan(Path(plan_path))
    repository_commit = _git_value(plan.repository_root, "rev-parse", "HEAD")
    if repository_commit != plan.expected_repository_commit:
        raise ValueError("bootstrap repository commit differs from the operator plan")
    if _git_value(plan.repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("bootstrap repository has tracked changes")

    native = plan.native_settings
    target = LocalArtifactStore(native.cas_root)
    if not (target.root / "sha256").is_dir():
        raise ValueError("bootstrap target CAS does not exist")
    database_paths = (
        native.asset_state_path,
        native.compile_state_root / "harness.sqlite3",
        native.scene_state_root / "runs.sqlite",
        native.observation_state_root / "runs.sqlite",
        native.validation_state_root / "runs.sqlite",
        plan.revised_state_root / "runs.sqlite",
    )
    if any(not path.is_file() for path in database_paths):
        raise ValueError("bootstrap requires all existing native and revised SQLite authorities")
    runtime_roots = (*native.scene_roots.values(), *native.capture_roots.values())
    if any(not path.is_dir() for path in runtime_roots):
        raise ValueError("bootstrap runtime deployment roots are incomplete")
    for ref in _required_target_refs(plan):
        target.resolve(ref)

    missing = 0
    for root, ref in _imports(plan):
        _resolve_source(LocalArtifactStore(root), ref)
        missing += not _existing_target(target, ref)

    deployment = plan.deployment
    if not deployment.codex_executable.is_file() or not deployment.server_python.is_file():
        raise ValueError("bootstrap Codex or server executable is unavailable")
    if not deployment.server_workdir.is_dir():
        raise ValueError("bootstrap server workdir is unavailable")
    actual_executable_sha256 = hashlib.sha256(deployment.codex_executable.read_bytes()).hexdigest()
    if actual_executable_sha256 != deployment.approved_executable_sha256:
        raise ValueError("bootstrap Codex executable identity differs")
    for path in (
        plan.golden_database_path,
        plan.shared_state_root,
        plan.qualification_library_root,
        plan.library_root,
        plan.checkpoint_path,
        plan.output_directory,
        deployment.evidence_root,
    ):
        if not path.parent.is_dir():
            raise ValueError(f"bootstrap destination parent is unavailable: {path.parent}")

    existing_output = plan.output_directory.exists()
    if existing_output and not plan.output_directory.is_dir():
        raise ValueError("bootstrap output destination is not a directory")
    return BootstrapCheckResult(
        schema_version="harness.limited_image_preview_bootstrap_check.v1",
        status="complete" if existing_output else "ready",
        plan_sha256=hashlib.sha256(raw).hexdigest(),
        repository_commit=repository_commit,
        source_objects_verified=10,
        missing_target_imports=missing,
        existing_checkpoint=plan.checkpoint_path.exists(),
        existing_output=existing_output,
    )


def bootstrap_limited_image_preview(plan_path: Path):
    """Execute the one-time bootstrap, resuming only from reverified CAS/SQLite state."""

    checked = check_limited_image_preview_bootstrap(plan_path)
    plan, _ = _read_plan(Path(plan_path))
    checkpoint = _load_checkpoint(plan, plan_sha256=checked.plan_sha256)
    target = LocalArtifactStore(plan.native_settings.cas_root)
    imported = []
    for source_root, ref in _imports(plan):
        source = _resolve_source(LocalArtifactStore(source_root), ref)
        actual = target.put_file(
            source,
            name=ref.name,
            media_type=ref.media_type,
            schema_version=ref.schema_version,
        )
        if actual != ref:
            raise ValueError("bootstrap CAS import changed an object's declared identity")
        imported.append(actual)
    imported_refs = tuple(sorted(imported, key=lambda item: (item.sha256, item.name)))
    if checkpoint.stage == "imports_complete" and checkpoint.imported_objects != imported_refs:
        raise ValueError("bootstrap imported object checkpoint differs from verified target CAS")
    checkpoint = checkpoint.model_copy(
        update={"stage": "imports_complete", "imported_objects": imported_refs}
    )
    _save_checkpoint(plan, checkpoint)

    # The next public restore is deliberately after the durable import checkpoint.
    # Empty or corrupt operator SQLite files fail here without losing import progress.
    from .mcp_native_assembly import restore_native_image_applications

    restore_native_image_applications(plan.native_settings)
    raise NotImplementedError("projection and qualification execution follows in the next slice")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = (
            check_limited_image_preview_bootstrap(arguments.plan)
            if arguments.check
            else bootstrap_limited_image_preview(arguments.plan)
        )
    except Exception as error:
        print(
            json.dumps(
                {"status": "failed", "error": type(error).__name__, "message": str(error)},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            result.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()


__all__ = [
    "BootstrapCheckResult",
    "BootstrapCheckpoint",
    "BootstrapDeployment",
    "LimitedImagePreviewBootstrapPlan",
    "NativeProjectionSeed",
    "PublishQualificationSeed",
    "bootstrap_limited_image_preview",
    "check_limited_image_preview_bootstrap",
    "run_cli",
]
