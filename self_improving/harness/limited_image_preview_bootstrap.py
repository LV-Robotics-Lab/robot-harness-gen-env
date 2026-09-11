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
from .limited_image_preview_factory import (
    NativeImageApplicationSettings,
    PreviewProjectionReferences,
)
from .schemas.common import ArtifactRef
from .schemas.limited_image_preview_execution import PreviewLayout

_NATIVE_SKILLS = (
    "genesis.observe@3.0.0",
    "image2env.compile@2.0.0",
    "image2env.replay@2.0.0",
)
_MAX_IMPORTED_OBJECT_BYTES = 1_048_576
_STAGES = (
    "planned",
    "imports_complete",
    "native_projections_complete",
    "revised_projection_complete",
    "shared_replay_complete",
    "shared_validation_complete",
    "shared_qualification_complete",
    "publish_candidate_complete",
    "publish_qualification_complete",
    "registry_complete",
    "complete",
)


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
    stage: Literal[
        "planned",
        "imports_complete",
        "native_projections_complete",
        "revised_projection_complete",
        "shared_replay_complete",
        "shared_validation_complete",
        "shared_qualification_complete",
        "publish_candidate_complete",
        "publish_qualification_complete",
        "registry_complete",
        "complete",
    ]
    imported_objects: tuple[ArtifactRef, ...] = ()
    native_projections: dict[
        Literal[
            "genesis.observe@3.0.0",
            "image2env.compile@2.0.0",
            "image2env.replay@2.0.0",
        ],
        PreviewProjectionReferences,
    ] = Field(default_factory=dict)
    revised_projection: PreviewProjectionReferences | None = None
    replay_execution_request: ArtifactRef | None = None
    replay_candidate_run_id: UUID | None = None
    replay_receipt: ArtifactRef | None = None
    validation_candidate_run_id: UUID | None = None
    validation_result: ArtifactRef | None = None
    shared_qualifications: dict[
        Literal[
            "image2env.replay@3.0.0",
            "image2env.validate@3.0.0",
            "image2env.preview_publish@1.0.0",
        ],
        ArtifactRef,
    ] = Field(default_factory=dict)
    publish_candidate_run_id: UUID | None = None
    registry_snapshot: ArtifactRef | None = None
    service_settings_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    deployment_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def stage_is_complete(self):
        rank = _STAGES.index(self.stage)
        if rank == 0 and self.imported_objects:
            raise ValueError("planned bootstrap cannot claim imported objects")
        if rank >= 1 and len(self.imported_objects) != 10:
            raise ValueError("completed import stage requires exactly ten objects")
        if len({item.sha256 for item in self.imported_objects}) != len(self.imported_objects):
            raise ValueError("bootstrap imported object identities must be unique")
        required_native = set(_NATIVE_SKILLS)
        if rank >= 2 and set(self.native_projections) != required_native:
            raise ValueError("native projection checkpoint requires all three exact Skills")
        if rank >= 3 and self.revised_projection is None:
            raise ValueError("revised projection checkpoint is incomplete")
        replay = (
            self.replay_execution_request,
            self.replay_candidate_run_id,
            self.replay_receipt,
        )
        if rank >= 4 and any(item is None for item in replay):
            raise ValueError("shared replay checkpoint is incomplete")
        if rank >= 5 and (
            self.validation_candidate_run_id is None or self.validation_result is None
        ):
            raise ValueError("shared validation checkpoint is incomplete")
        shared_core = {"image2env.replay@3.0.0", "image2env.validate@3.0.0"}
        if rank >= 6 and not shared_core.issubset(self.shared_qualifications):
            raise ValueError("shared replay and validation qualifications are incomplete")
        if rank >= 7 and self.publish_candidate_run_id is None:
            raise ValueError("publish candidate checkpoint is incomplete")
        if rank >= 8 and "image2env.preview_publish@1.0.0" not in self.shared_qualifications:
            raise ValueError("publish qualification checkpoint is incomplete")
        if rank >= 9 and self.registry_snapshot is None:
            raise ValueError("registry checkpoint is incomplete")
        if rank >= 10 and (self.service_settings_sha256 is None or self.deployment_sha256 is None):
            raise ValueError("completed bootstrap release files are incomplete")
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


def _record_checkpoint(
    plan: LimitedImagePreviewBootstrapPlan,
    checkpoint: BootstrapCheckpoint,
    stage: str,
    **changes,
) -> BootstrapCheckpoint:
    wanted_rank = _STAGES.index(stage)
    current_rank = _STAGES.index(checkpoint.stage)
    if wanted_rank > current_rank + 1:
        raise ValueError("bootstrap checkpoint stages cannot be skipped")
    data = checkpoint.model_dump(mode="python")
    for name, value in changes.items():
        current = data[name]
        if current not in (None, {}, ()) and current != value:
            raise ValueError(f"bootstrap checkpoint {name} differs from reverified authority")
        data[name] = value
    if wanted_rank > current_rank:
        data["stage"] = stage
    updated = BootstrapCheckpoint.model_validate(data)
    if updated != checkpoint:
        _save_checkpoint(plan, updated)
    return updated


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


def _projection_references(projection) -> PreviewProjectionReferences:
    return PreviewProjectionReferences(
        admission_ref=projection.admission_ref,
        descriptor_ref=projection.descriptor_ref,
        qualification_ref=projection.qualification_ref,
        qualification_report_ref=projection.qualification_report_ref,
    )


def _verified_admission(
    store: LocalArtifactStore,
    ref: ArtifactRef,
    *,
    skill_ref: str,
    descriptor_ref: ArtifactRef | None = None,
):
    from .schemas.typed_qualification_admission import TypedQualificationAdmissionV1
    from .typed_qualification_admission import verify_typed_qualification_admission

    raw = store.resolve(ref).path.read_bytes()
    admission = TypedQualificationAdmissionV1.model_validate_json(raw)
    verified = verify_typed_qualification_admission(artifact_store=store, admission=admission)
    if verified.admission_ref != ref or verified.admission.skill_ref != skill_ref:
        raise ValueError("bootstrap typed admission differs from its fixed Skill identity")
    if descriptor_ref is not None and verified.admission.descriptor != descriptor_ref:
        raise ValueError("bootstrap typed admission differs from its fixed descriptor")
    return verified


def _restore_applications(plan: LimitedImagePreviewBootstrapPlan):
    from .mcp_native_assembly import restore_native_image_applications
    from .revised_image_compile_application import RevisedImageCompileApplication
    from .revised_image_compiler import RevisedImage2EnvGenesisCompiler

    native = restore_native_image_applications(plan.native_settings)
    revised = RevisedImageCompileApplication(
        plan.revised_state_root,
        RevisedImage2EnvGenesisCompiler(native.store, native.replay.producer.compiler),
        qualification=plan.revised_qualification,
    )
    receipt_ref, receipt = revised.read(plan.revised_registered_run_id)
    if (
        receipt_ref != plan.revised_registered_receipt
        or receipt.request != plan.revised_registered_request
    ):
        raise ValueError("bootstrap revised registered run differs from its fixed plan")
    admission = revised.admit()
    if admission.admission_ref != plan.revised_admission_ref:
        raise ValueError("bootstrap current revised admission differs from its fixed plan")
    return native, revised, admission


def _native_projection_objects(
    plan: LimitedImagePreviewBootstrapPlan,
    checkpoint: BootstrapCheckpoint,
    native,
):
    from .limited_observation_registry_projection import (
        issue_limited_observation_registry_projection,
        load_limited_observation_registry_projection,
    )
    from .native_skill_projection import issue_native_skill_projection, load_native_skill_projection

    seeds = {seed.skill_ref: seed for seed in plan.native_projection_seeds}
    bindings = {binding.skill_ref: binding for binding in native.execution_bindings()}
    projections = {}
    for skill_ref in _NATIVE_SKILLS:
        seed = seeds[skill_ref]
        admission = _verified_admission(
            native.store,
            seed.admission_ref,
            skill_ref=skill_ref,
            descriptor_ref=seed.descriptor_ref,
        )
        saved = checkpoint.native_projections.get(skill_ref)
        if skill_ref == "genesis.observe@3.0.0":
            if saved is None:
                projection = issue_limited_observation_registry_projection(
                    application=native.observe,
                    admission=admission,
                    replay_authority=native.replay.run_store,
                )
            else:
                projection = load_limited_observation_registry_projection(
                    application=native.observe,
                    replay_authority=native.replay.run_store,
                    **saved.model_dump(mode="python"),
                )
        elif saved is None:
            projection = issue_native_skill_projection(
                artifact_store=native.store,
                admission=admission,
                native_binding=bindings[skill_ref],
            )
        else:
            projection = load_native_skill_projection(
                artifact_store=native.store,
                native_binding=bindings[skill_ref],
                **saved.model_dump(mode="python"),
            )
        projections[skill_ref] = projection
    return projections


def _revised_projection_object(checkpoint, revised, admission):
    from .limited_image_preview_policy import LimitedImagePreviewPolicy
    from .revised_compile_registry_projection import (
        issue_revised_compile_registry_projection,
        load_revised_compile_registry_projection,
    )

    policy = LimitedImagePreviewPolicy(revised, "image2env.compile@3.0.0", {})
    saved = checkpoint.revised_projection
    if saved is None:
        return issue_revised_compile_registry_projection(
            application=revised,
            admission=admission,
            policy=policy,
        )
    return load_revised_compile_registry_projection(
        application=revised,
        policy=policy,
        **saved.model_dump(mode="python"),
    )


def _qualification_library(plan, store, compiler):
    from .limited_image_preview_library import (
        create_limited_image_preview_library,
        load_limited_image_preview_library,
    )

    root = plan.qualification_library_root
    if not root.exists() or (root.is_dir() and not any(root.iterdir())):
        return create_limited_image_preview_library(
            artifact_store=store,
            compiler=compiler,
            root=root,
            project_id=plan.publish_seed.project_id,
        )
    return load_limited_image_preview_library(
        artifact_store=store,
        compiler=compiler,
        root=root,
        project_id=plan.publish_seed.project_id,
    )


def _shared_application(plan, native, revised):
    from .genesis_scene_execution_v3 import GenesisSceneExecutionProducerV3
    from .limited_image_preview_application import LimitedImagePreviewApplication

    library = _qualification_library(plan, native.store, revised.compiler)
    return LimitedImagePreviewApplication(
        plan.shared_state_root,
        revised,
        GenesisSceneExecutionProducerV3(
            native.store,
            revised.compiler,
            plan.native_settings.scene_manifest,
        ),
        roots=plan.native_settings.scene_roots,
        library=library,
    )


def _shared_replay_input(plan, shared, revised):
    from .schemas.genesis_scene_execution_v3 import (
        GenesisSceneCompileBindingV2,
        GenesisSceneExecutionRequestV3,
    )
    from .schemas.native_prompt_revision import Image2EnvGenesisCompileRequestV2

    receipt_ref, receipt = revised.read(plan.revised_registered_run_id)
    request = shared.read_artifact(
        plan.revised_registered_request, Image2EnvGenesisCompileRequestV2
    )
    proposal = request.compile_input.attempt.revision.layout_proposal
    actual_layout = PreviewLayout(
        x_m=proposal.position_xy_m[0],
        y_m=proposal.position_xy_m[1],
        yaw_rad=proposal.yaw_rad,
    )
    if actual_layout != plan.expected_revised_layout:
        raise ValueError("bootstrap revised request layout differs from the operator plan")
    execution = GenesisSceneExecutionRequestV3(
        schema_version="harness.genesis_scene_execution_request.v3",
        scene=receipt.scene,
        compile_receipt=receipt_ref,
        runtime_manifest=plan.native_settings.scene_manifest,
        asset_qualification=receipt.qualification,
    )
    execution_ref = shared.put(execution, "bootstrap-scene-execution-request-v3.json")
    binding = GenesisSceneCompileBindingV2(
        schema_version="harness.genesis_scene_compile_binding.v2",
        compile_skill_ref="image2env.compile@3.0.0",
        compile_run_id=plan.revised_registered_run_id,
        compile_request=plan.revised_registered_request,
        compile_receipt=plan.revised_registered_receipt,
        execution_request=execution_ref,
    )
    return binding, execution_ref


def _journal_run_ids(path: Path, skill_ref: str) -> tuple[UUID, ...]:
    from .event_journal import SQLiteEventJournal

    skill_id, version = skill_ref.split("@")
    journal = SQLiteEventJournal(path)
    cursor = 0
    run_ids = set()
    while True:
        page = journal.read(after_event_id=cursor, limit=200)
        for stored in page.events:
            envelope = stored.envelope
            if envelope.skill_id == skill_id and envelope.skill_version == version:
                run_ids.add(envelope.run_id)
        cursor = page.last_event_id
        if not page.has_more:
            break
    return tuple(sorted(run_ids, key=str))


def _verify_candidate(application, skill_ref: str, parameters, run_id: UUID):
    from .schemas.common import RunStatus

    invocation = application.invocation(run_id)
    state = application.run_state(run_id)
    if invocation is None or state is None:
        raise ValueError(f"bootstrap candidate {run_id} has an incomplete durable lifecycle")
    if (
        f"{invocation.skill_id}@{invocation.skill_version}" != skill_ref
        or f"{state.skill_id}@{state.skill_version}" != skill_ref
        or invocation.effective_parameters != parameters.model_dump(mode="json")
        or state.invocation_digest != invocation.invocation_digest
        or not state.events
        or any(not event.stage.startswith("qualification_candidate.") for event in state.events)
    ):
        raise ValueError(f"bootstrap candidate {run_id} differs from its exact input or Skill")
    if state.status is not RunStatus.SUCCEEDED:
        raise ValueError(
            f"bootstrap candidate {run_id} is terminal {state.status}; automatic rerun is forbidden"
        )
    return state


def _find_candidate(application, skill_ref: str, parameters):
    matches = []
    pending = []
    for run_id in _journal_run_ids(application.run_store.path, skill_ref):
        invocation = application.invocation(run_id)
        if invocation is None or invocation.effective_parameters != parameters.model_dump(
            mode="json"
        ):
            continue
        state = application.run_state(run_id)
        if state is None:
            pending.append(run_id)
            continue
        if not state.events or any(
            not event.stage.startswith("qualification_candidate.") for event in state.events
        ):
            continue
        if state.status.value == "succeeded":
            _verify_candidate(application, skill_ref, parameters, run_id)
            matches.append(run_id)
        else:
            pending.append(run_id)
    if pending:
        raise ValueError(
            "bootstrap found a matching incomplete or failed candidate; "
            "automatic rerun is forbidden: " + ",".join(str(value) for value in pending)
        )
    if len(matches) > 1:
        raise ValueError("bootstrap found multiple matching candidate terminals")
    return matches[0] if matches else None


def _candidate_output_ref(application, state, schema_version: str) -> ArtifactRef:
    refs = tuple(ref for ref in state.artifacts if ref.schema_version == schema_version)
    if len(refs) != 1:
        raise ValueError("bootstrap candidate does not retain exactly one typed output")
    application.artifact_store.resolve(refs[0])
    return refs[0]


def _replay_candidate(shared, binding, *, saved_run_id=None):
    from .genesis_scene_execution_v3 import GenesisSceneExecutionVerifierV3

    run_id = saved_run_id or _find_candidate(shared, "image2env.replay@3.0.0", binding)
    if run_id is None:
        evaluation = shared.evaluate("image2env.replay@3.0.0", binding)
        run_id = evaluation.state.run_id
    state = _verify_candidate(shared, "image2env.replay@3.0.0", binding, run_id)
    receipt = _candidate_output_ref(shared, state, "harness.genesis_scene_execution_receipt.v3")
    GenesisSceneExecutionVerifierV3(shared.artifact_store, shared.producer.compiler).read_receipt(
        receipt
    )
    return run_id, receipt


def _validation_candidate(shared, value, *, saved_run_id=None):
    from .schemas.limited_image_preview_execution import LimitedSceneValidationV1

    run_id = saved_run_id or _find_candidate(shared, "image2env.validate@3.0.0", value)
    if run_id is None:
        evaluation = shared.evaluate("image2env.validate@3.0.0", value)
        run_id = evaluation.state.run_id
    state = _verify_candidate(shared, "image2env.validate@3.0.0", value, run_id)
    result_ref = _candidate_output_ref(shared, state, "harness.limited_scene_validation.v1")
    result = shared.read_artifact(result_ref, LimitedSceneValidationV1)
    if result.status != "passed" or result.expected_layout != value.expected_layout:
        raise ValueError("bootstrap saved validation did not pass the exact revised layout")
    return run_id, result_ref


def _publish_candidate(shared, manifest, *, saved_run_id=None):
    run_id = saved_run_id or _find_candidate(shared, "image2env.preview_publish@1.0.0", manifest)
    if run_id is None:
        evaluation = shared.evaluate("image2env.preview_publish@1.0.0", manifest)
        run_id = evaluation.state.run_id
    state = _verify_candidate(shared, "image2env.preview_publish@1.0.0", manifest, run_id)
    ref = _candidate_output_ref(shared, state, "harness.limited_image_preview_manifest.v1")
    published = shared.library.get(manifest.preview_id)
    if published.manifest != ref:
        raise ValueError("bootstrap publish candidate differs from qualification library")
    return run_id


def _bind_shared_qualifications(shared, values) -> None:
    for skill_ref, ref in sorted(values.items()):
        descriptor = shared.bind(ref)
        if f"{descriptor.skill_id}@{descriptor.version}" != skill_ref:
            raise ValueError("bootstrap shared qualification bound another exact Skill")


def _shared_registry_entry(shared, skill_ref: str, qualification_ref: ArtifactRef):
    from .qualification import QualificationReportV1
    from .schemas.common import SkillQualification
    from .schemas.registry_snapshot import QualifiedSkillSnapshot

    shared.bind(qualification_ref)
    descriptors = tuple(
        descriptor
        for descriptor in shared.skills
        if f"{descriptor.skill_id}@{descriptor.version}" == skill_ref
    )
    if len(descriptors) != 1 or descriptors[0].qualification_artifact != qualification_ref:
        raise ValueError("bootstrap shared descriptor differs from its qualification")
    descriptor = descriptors[0]
    slug = skill_ref.replace("@", "-").replace(".", "-")
    descriptor_ref = shared.put(descriptor, f"{slug}-descriptor.json")
    qualification = shared.read_artifact(qualification_ref, SkillQualification)
    report_path = shared.artifact_store.resolve_digest(qualification.report_sha256)
    report = QualificationReportV1.model_validate_json(report_path.read_bytes())
    if report.skill_ref != skill_ref:
        raise ValueError("bootstrap shared qualification report names another Skill")
    report_ref = shared.artifact_store.put_file(
        report_path,
        name=f"{slug}-qualification-report.json",
        media_type="application/json",
        schema_version="harness.skill_qualification_report.v1",
    )
    shared.read_artifact(report_ref, QualificationReportV1)
    return QualifiedSkillSnapshot(
        skill_ref=skill_ref,
        mcp_tool_name=descriptor.mcp_tool_name,
        descriptor_ref=descriptor_ref,
        qualification_ref=qualification_ref,
        qualification_report_ref=report_ref,
    )


def _publish_registry(store, entries) -> ArtifactRef:
    from .schemas.registry_snapshot import RegistrySnapshot

    snapshot = RegistrySnapshot(
        schema_version="harness.registry_snapshot.v1",
        qualified_skills=tuple(sorted(entries, key=lambda value: value.mcp_tool_name)),
    )
    if len(snapshot.qualified_skills) != 7:
        raise ValueError("limited preview Registry must contain exactly seven qualified Skills")
    expected = {
        "genesis.observe@3.0.0",
        "image2env.compile@2.0.0",
        "image2env.compile@3.0.0",
        "image2env.preview_publish@1.0.0",
        "image2env.replay@2.0.0",
        "image2env.replay@3.0.0",
        "image2env.validate@3.0.0",
    }
    if {entry.skill_ref for entry in snapshot.qualified_skills} != expected:
        raise ValueError("limited preview Registry differs from the seven exact supported Skills")
    with tempfile.TemporaryDirectory(prefix="preview-bootstrap-registry-") as directory:
        path = Path(directory) / "registry-snapshot.json"
        path.write_bytes(_canonical(snapshot))
        ref = store.put_file(
            path,
            name=path.name,
            media_type="application/json",
            schema_version="harness.registry_snapshot.v1",
        )
    raw = store.resolve(ref).path.read_bytes()
    if raw != _canonical(RegistrySnapshot.model_validate_json(raw)):
        raise ValueError("bootstrap Registry snapshot is not canonical")
    for entry in snapshot.qualified_skills:
        for artifact in (
            entry.descriptor_ref,
            entry.qualification_ref,
            entry.qualification_report_ref,
        ):
            store.resolve(artifact)
    return ref


def _service_settings(
    plan,
    registry_snapshot,
    native_projection_refs,
    revised_projection_ref,
    shared_qualifications,
):
    from .limited_image_preview_factory import LimitedImagePreviewSettings

    return LimitedImagePreviewSettings(
        **plan.native_settings.model_dump(mode="python"),
        schema_version="harness.limited_image_preview_settings.v1",
        principal_id=plan.principal_id,
        project_id=plan.project_id,
        golden_database_path=plan.golden_database_path,
        registry_snapshot=registry_snapshot,
        asset_catalog=plan.asset_catalog,
        source_scene=plan.source_scene,
        qualification_run_id=plan.qualification_run_id,
        table_center_m=plan.table_center_m,
        table_size_m=plan.table_size_m,
        table_friction=plan.table_friction,
        seed=plan.seed,
        revised_state_root=plan.revised_state_root,
        revised_qualification=plan.revised_qualification,
        shared_state_root=plan.shared_state_root,
        library_root=plan.library_root,
        native_projections=native_projection_refs,
        revised_projection=revised_projection_ref,
        shared_qualifications=shared_qualifications,
    )


def _deployment(plan, service_settings_path):
    from .limited_image_preview_cli import PreviewDeployment

    return PreviewDeployment(
        service_settings=service_settings_path,
        **plan.deployment.model_dump(mode="python"),
    )


def _write_release_directory(plan, settings, deployment):
    settings_payload = _canonical(settings)
    deployment_payload = _canonical(deployment)
    expected = {
        "service-settings.json": settings_payload,
        "deployment.json": deployment_payload,
    }
    if plan.output_directory.exists():
        if not plan.output_directory.is_dir():
            raise ValueError("bootstrap release destination is not a directory")
        actual_names = {path.name for path in plan.output_directory.iterdir()}
        if actual_names != set(expected):
            raise ValueError("existing bootstrap release directory has an unexpected file set")
        for name, payload in expected.items():
            if (plan.output_directory / name).read_bytes() != payload:
                raise ValueError("existing bootstrap release differs from reverified authorities")
        return (
            hashlib.sha256(settings_payload).hexdigest(),
            hashlib.sha256(deployment_payload).hexdigest(),
        )

    staging = Path(
        tempfile.mkdtemp(prefix=f".{plan.output_directory.name}.", dir=plan.output_directory.parent)
    )
    try:
        for name, payload in expected.items():
            path = staging / name
            with path.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(path, 0o444)
        directory = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        os.replace(staging, plan.output_directory)
        parent = os.open(plan.output_directory.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if staging.exists():
            for path in staging.iterdir():
                path.unlink()
            staging.rmdir()
    return (
        hashlib.sha256(settings_payload).hexdigest(),
        hashlib.sha256(deployment_payload).hexdigest(),
    )


def check_limited_image_preview_bootstrap(plan_path: Path) -> BootstrapCheckResult:
    """Pure-read verification of the operator plan and all pre-existing identities."""

    plan, raw = _read_plan(Path(plan_path))
    plan_sha256 = hashlib.sha256(raw).hexdigest()
    checkpoint = (
        _load_checkpoint(plan, plan_sha256=plan_sha256) if plan.checkpoint_path.exists() else None
    )
    if plan.library_root.exists() and (checkpoint is None or checkpoint.stage != "complete"):
        raise ValueError("production library_root must not exist before bootstrap completes")
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
    status = "ready"
    if existing_output:
        expected_names = {"deployment.json", "service-settings.json"}
        if {path.name for path in plan.output_directory.iterdir()} != expected_names:
            raise ValueError("bootstrap release directory has an unexpected file set")
        from .limited_image_preview_cli import PreviewDeployment
        from .limited_image_preview_factory import LimitedImagePreviewSettings

        settings_payload = (plan.output_directory / "service-settings.json").read_bytes()
        deployment_payload = (plan.output_directory / "deployment.json").read_bytes()
        settings = LimitedImagePreviewSettings.model_validate_json(settings_payload)
        deployment = PreviewDeployment.model_validate_json(deployment_payload)
        if settings_payload != _canonical(settings) or deployment_payload != _canonical(deployment):
            raise ValueError("bootstrap release directory is not strict canonical JSON")
        if deployment.service_settings != plan.output_directory / "service-settings.json":
            raise ValueError("bootstrap release deployment points at another service settings file")
        if checkpoint is not None and checkpoint.stage == "complete":
            if (
                checkpoint.service_settings_sha256 != hashlib.sha256(settings_payload).hexdigest()
                or checkpoint.deployment_sha256 != hashlib.sha256(deployment_payload).hexdigest()
            ):
                raise ValueError("bootstrap release directory differs from its checkpoint")
            status = "complete"
    return BootstrapCheckResult(
        schema_version="harness.limited_image_preview_bootstrap_check.v1",
        status=status,
        plan_sha256=plan_sha256,
        repository_commit=repository_commit,
        source_objects_verified=10,
        missing_target_imports=missing,
        existing_checkpoint=checkpoint is not None,
        existing_output=existing_output,
    )


def bootstrap_limited_image_preview(plan_path: Path) -> BootstrapCheckpoint:
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
    checkpoint = _record_checkpoint(
        plan,
        checkpoint,
        "imports_complete",
        imported_objects=imported_refs,
    )

    # Every recovery below reopens the public CAS/SQLite authorities.  The checkpoint
    # only remembers identities and never substitutes for their deep readers.
    native, revised, revised_admission = _restore_applications(plan)

    native_projections = _native_projection_objects(plan, checkpoint, native)
    native_projection_refs = {
        skill_ref: _projection_references(projection)
        for skill_ref, projection in native_projections.items()
    }
    checkpoint = _record_checkpoint(
        plan,
        checkpoint,
        "native_projections_complete",
        native_projections=native_projection_refs,
    )

    revised_projection = _revised_projection_object(checkpoint, revised, revised_admission)
    revised_projection_ref = _projection_references(revised_projection)
    checkpoint = _record_checkpoint(
        plan,
        checkpoint,
        "revised_projection_complete",
        revised_projection=revised_projection_ref,
    )

    shared = _shared_application(plan, native, revised)
    replay_binding, replay_execution_request = _shared_replay_input(plan, shared, revised)
    replay_run_id, replay_receipt = _replay_candidate(
        shared,
        replay_binding,
        saved_run_id=checkpoint.replay_candidate_run_id,
    )
    checkpoint = _record_checkpoint(
        plan,
        checkpoint,
        "shared_replay_complete",
        replay_execution_request=replay_execution_request,
        replay_candidate_run_id=replay_run_id,
        replay_receipt=replay_receipt,
    )

    from .schemas.limited_image_preview import LimitedImagePreviewManifestV1
    from .schemas.limited_image_preview_execution import LimitedSceneValidateInputV1

    validation_input = LimitedSceneValidateInputV1(
        schema_version="harness.limited_scene_validate_input.v1",
        execution_receipt=replay_receipt,
        expected_layout=plan.expected_revised_layout,
    )
    validation_run_id, validation_result = _validation_candidate(
        shared,
        validation_input,
        saved_run_id=checkpoint.validation_candidate_run_id,
    )
    checkpoint = _record_checkpoint(
        plan,
        checkpoint,
        "shared_validation_complete",
        validation_candidate_run_id=validation_run_id,
        validation_result=validation_result,
    )

    core_skills = {"image2env.replay@3.0.0", "image2env.validate@3.0.0"}
    existing_core = {
        skill_ref: ref
        for skill_ref, ref in checkpoint.shared_qualifications.items()
        if skill_ref in core_skills
    }
    if existing_core:
        if set(existing_core) != core_skills:
            raise ValueError("bootstrap checkpoint has a partial shared qualification set")
        core_qualifications = existing_core
        _bind_shared_qualifications(shared, core_qualifications)
    else:
        qualified = shared.qualify(replay_run_id, validation_run_id)
        if set(qualified) != core_skills:
            raise ValueError("bootstrap shared qualification returned another Skill set")
        core_qualifications = qualified
        _bind_shared_qualifications(shared, core_qualifications)
    combined_qualifications = {
        **checkpoint.shared_qualifications,
        **core_qualifications,
    }
    checkpoint = _record_checkpoint(
        plan,
        checkpoint,
        "shared_qualification_complete",
        shared_qualifications=combined_qualifications,
    )

    manifest = shared.read_artifact(plan.publish_seed.manifest_ref, LimitedImagePreviewManifestV1)
    if manifest.project_id != plan.publish_seed.project_id:
        raise ValueError("bootstrap saved preview belongs to another qualification project")
    publish_run_id = _publish_candidate(
        shared,
        manifest,
        saved_run_id=checkpoint.publish_candidate_run_id,
    )
    checkpoint = _record_checkpoint(
        plan,
        checkpoint,
        "publish_candidate_complete",
        publish_candidate_run_id=publish_run_id,
    )

    publish_skill = "image2env.preview_publish@1.0.0"
    publish_qualification = checkpoint.shared_qualifications.get(publish_skill)
    if publish_qualification is None:
        publish_qualification = shared.qualify_publish(publish_run_id)
    combined_qualifications = {
        **core_qualifications,
        publish_skill: publish_qualification,
    }
    _bind_shared_qualifications(shared, combined_qualifications)
    checkpoint = _record_checkpoint(
        plan,
        checkpoint,
        "publish_qualification_complete",
        shared_qualifications=combined_qualifications,
    )

    entries = [projection.registry_entry for projection in native_projections.values()]
    entries.append(revised_projection.registry_entry)
    entries.extend(
        _shared_registry_entry(shared, skill_ref, ref)
        for skill_ref, ref in sorted(combined_qualifications.items())
    )
    registry_snapshot = _publish_registry(native.store, entries)
    checkpoint = _record_checkpoint(
        plan,
        checkpoint,
        "registry_complete",
        registry_snapshot=registry_snapshot,
    )

    settings = _service_settings(
        plan,
        registry_snapshot,
        native_projection_refs,
        revised_projection_ref,
        combined_qualifications,
    )
    settings_path = plan.output_directory / "service-settings.json"
    deployment = _deployment(plan, settings_path)
    settings_sha256, deployment_sha256 = _write_release_directory(
        plan,
        settings,
        deployment,
    )
    return _record_checkpoint(
        plan,
        checkpoint,
        "complete",
        service_settings_sha256=settings_sha256,
        deployment_sha256=deployment_sha256,
    )


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
