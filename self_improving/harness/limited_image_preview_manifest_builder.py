"""Assemble one limited preview candidate from the existing Golden journal.

This module neither publishes a manifest nor grants publication authority.  It
selects immutable evidence already committed by the Golden workflow and records
the controller's measured, non-overlapping wall-time attribution.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .golden_store import GoldenRunCorruptionError, SQLiteGoldenRunStore, StoredWorkflowEntry
from .image_ingest import ImageMediaConsumerVerifier
from .schemas.common import ArtifactRef, RunState, RunStatus
from .schemas.genesis_fresh_capture import (
    GenesisFreshCaptureInputV1,
    GenesisFreshCaptureReceiptV1,
)
from .schemas.genesis_scene_application_v2 import GenesisSceneReplayInputV2
from .schemas.genesis_scene_execution_v2 import (
    GenesisSceneExecutionReceiptV2,
    GenesisSceneExecutionRequestV2,
)
from .schemas.genesis_scene_execution_v3 import (
    GenesisSceneCompileBindingV2,
    GenesisSceneExecutionReceiptV3,
    GenesisSceneExecutionRequestV3,
)
from .schemas.image2env_genesis_compile import (
    Image2EnvGenesisCompileReceiptV1,
    Image2EnvGenesisCompileRequestV1,
)
from .schemas.limited_image_preview import (
    LimitedImagePreviewManifestV1,
    LimitedImagePreviewStageV1,
    LimitedImagePreviewTimingV1,
    limited_image_preview_manifest_sha256,
)
from .schemas.limited_image_preview_execution import (
    LimitedSceneValidateInputV1,
    LimitedSceneValidationV1,
)
from .schemas.native_prompt_revision import (
    Image2EnvGenesisCompileReceiptV2,
    Image2EnvGenesisCompileRequestV2,
    PromptRevisionV3,
)
from .schemas.revised_image_compile_application import RevisedCompileInvocationV1
from .schemas.workflow import RunCommandRequest, RunSnapshot, WorkflowStartReceipt
from .schemas.workflow_operation import ToolResultV2, WorkflowOperationReceiptV2
from .schemas.x2env import ImageSourceMedia
from .schemas.x2env_compile_evidence import SceneInterpretationProposalV1

_NORMAL_ACTIONS = (
    "image2env.compile@2.0.0",
    "image2env.replay@2.0.0",
    "genesis.observe@3.0.0",
    "image2env.validate@3.0.0",
)
_REPAIR_PREFIX = (
    "image2env.compile@2.0.0",
    "image2env.replay@2.0.0",
    "image2env.validate@3.0.0",
    "genesis.observe@3.0.0",
)
_MCP_FOR_ACTION = {
    "image2env.compile@2.0.0": "image2env_compile_v2_0_0",
    "image2env.replay@2.0.0": "image2env_replay_v2_0_0",
    "genesis.observe@3.0.0": "genesis_observe_v3_0_0",
    "image2env.validate@3.0.0": "image2env_validate_v3_0_0",
    "advisory": "harness_advisory_record_v2",
    "prompt_revision": "harness_prompt_revision_record_v3",
    "image2env.compile@3.0.0": "image2env_compile_v3_0_0",
    "image2env.replay@3.0.0": "image2env_replay_v3_0_0",
}
_STATUS_TOOL = "image2env_preview_status_v1"
_LOOKUP_TOOL = "image2env_preview_asset_lookup_v1"
_STAGES = {
    _STATUS_TOOL: None,
    _LOOKUP_TOOL: "asset_lookup",
    "image2env_compile_v2_0_0": "compile",
    "image2env_compile_v3_0_0": "compile",
    "image2env_replay_v2_0_0": "replay",
    "image2env_replay_v3_0_0": "replay",
    "genesis_observe_v3_0_0": "observe",
    "image2env_validate_v3_0_0": "validate",
    "harness_advisory_record_v2": None,
    "harness_prompt_revision_record_v3": None,
}
_STATUS_STAGES = {
    "start",
    "asset_lookup",
    "compile",
    "replay",
    "observe",
    "validate",
    "advisory",
    "prompt_revision",
}


class LimitedImagePreviewManifestBuildError(ValueError):
    """The committed workflow cannot produce one exact preview candidate."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


def build_limited_image_preview_manifest(
    *,
    artifact_store: LocalArtifactStore,
    workflow_store: SQLiteGoldenRunStore,
    snapshot: RunSnapshot,
    input_record: ArtifactRef,
    lookup: ArtifactRef,
    project_id: str,
    timing_samples: Sequence[Mapping[str, object]],
) -> LimitedImagePreviewManifestV1:
    """Return a self-hashed candidate assembled from one terminal Golden head."""

    if (
        type(artifact_store) is not LocalArtifactStore
        or type(workflow_store) is not SQLiteGoldenRunStore
    ):
        raise TypeError("manifest builder requires exact shared Golden and CAS authorities")
    if (
        type(snapshot) is not RunSnapshot
        or type(input_record) is not ArtifactRef
        or type(lookup) is not ArtifactRef
    ):
        raise TypeError("manifest builder requires exact workflow and ArtifactRef inputs")
    try:
        persisted = workflow_store.read_snapshot(
            principal_id=snapshot.principal_id,
            workflow_run_id=snapshot.workflow_run_id,
        )
        if persisted is None or persisted.snapshot != snapshot:
            _fail("snapshot_drift", "manifest snapshot is not the current durable Golden head")
        if (
            snapshot.active_operation is not None
            or snapshot.requested_profile != "genesis.rigid_scene@1"
            or snapshot.revision == 0
            or snapshot.status.value != "active"
        ):
            _fail("nonterminal_workflow", "preview assembly requires a terminal rigid-scene head")
        entries = workflow_store.read_entries(
            principal_id=snapshot.principal_id,
            workflow_run_id=snapshot.workflow_run_id,
        )
        heads = _receipt_heads(artifact_store, snapshot)
        _verify_journal(artifact_store, snapshot, entries, heads)
        if input_record.schema_version is not None or lookup.schema_version is not None:
            _fail("artifact_type", "preview input and lookup must remain untyped binding records")
        input_value = _read_untyped_record(artifact_store, input_record, "preview input")
        lookup_value = _read_untyped_record(artifact_store, lookup, "preview lookup")
        source_media, prompt, representation, qualification, source_scene = _inputs(
            snapshot, input_record, lookup, input_value, lookup_value
        )
        for ref in (prompt, representation, qualification, source_scene):
            artifact_store.resolve(ref)
        artifact_store.resolve(prompt).path.read_bytes().decode("utf-8", errors="strict")
        ImageMediaConsumerVerifier(
            artifact_store,
            max_source_bytes=source_media.source.bytes,
            max_decoded_pixels=source_media.width * source_media.height,
        ).verify(source_media)
        source_media_ref = _put_source_media(artifact_store, source_media)

        operations = [entry.operation for entry in entries if entry.operation is not None]
        controls = [entry.control for entry in entries if entry.control is not None]
        actions = tuple(entry.action_ref for entry in entries)
        mode = "normal" if actions == _NORMAL_ACTIONS else "repair"
        if mode == "repair":
            _repair_actions(actions)

        initial_compile = _one_operation(operations, "image2env.compile@2.0.0")
        initial_replay = _one_operation(operations, "image2env.replay@2.0.0")
        validations = [op for op in operations if op.skill_ref == "image2env.validate@3.0.0"]
        if len(validations) != (1 if mode == "normal" else 2):
            _fail("journal_shape", "workflow requires the exact initial/final validation count")
        initial_validation = validations[0]
        for operation in operations:
            if mode == "repair" and operation is initial_validation:
                if operation.status not in {RunStatus.BLOCKED, RunStatus.FAILED}:
                    _fail(
                        "semantic_mismatch_missing",
                        "repair requires an explicitly rejected initial validation",
                    )
            elif operation.status is not RunStatus.SUCCEEDED:
                _fail("nonterminal_workflow", "all publishable stage operations must complete")

        revision_ref: ArtifactRef | None = None
        revision: PromptRevisionV3 | None = None
        if mode == "repair":
            revisions = [
                control for control in controls if control.control_kind == "prompt_revision"
            ]
            if len(revisions) != 1:
                _fail("journal_shape", "repair requires one committed PromptRevisionV3")
            revision_ref = revisions[0].receipt
            revision = _read_model(
                artifact_store, revision_ref, PromptRevisionV3, "prompt revision"
            )
            if len(revision.observation_citations) != 1:
                _fail("observation_lineage", "repair requires one exact fresh observation citation")
            observation_ref = revision.observation_citations[0].receipt
        else:
            observed = [op for op in operations if op.skill_ref == "genesis.observe@3.0.0"]
            if len(observed) != 1:
                _fail("journal_shape", "normal preview requires one exact observation operation")
            observation_ref, _ = _operation_output(
                artifact_store,
                observed[0],
                GenesisFreshCaptureReceiptV1,
                "fresh observation",
            )
        observation_operation = _operation_containing(
            artifact_store,
            operations,
            observation_ref,
            GenesisFreshCaptureReceiptV1,
        )
        initial = _stage(
            artifact_store,
            phase="initial",
            compile_operation=initial_compile,
            replay_operation=initial_replay,
            observation_operation=observation_operation,
            validation_operation=initial_validation,
            prompt_revision=None,
            workflow_head=heads[initial_validation.committed_revision],
        )
        if initial.compile_request.source_scene != source_scene:
            _fail("lookup_lineage", "initial compile source differs from the audited lookup")

        revised_stage = None
        if mode == "repair":
            assert revision_ref is not None and revision is not None
            revised_compile = _one_operation(operations, "image2env.compile@3.0.0")
            revised_replay = _one_operation(operations, "image2env.replay@3.0.0")
            final_validation = validations[1]
            revised_stage = _stage(
                artifact_store,
                phase="revised",
                compile_operation=revised_compile,
                replay_operation=revised_replay,
                observation_operation=None,
                validation_operation=final_validation,
                prompt_revision=revision_ref,
                workflow_head=heads[final_validation.committed_revision],
            )
            if revised_stage.compile_request.compile_input.attempt.prompt_revision != revision_ref:
                _fail("revision_lineage", "revised compile did not consume the committed revision")
            requested_layout = {
                "x_m": revision.layout_proposal.position_xy_m[0],
                "y_m": revision.layout_proposal.position_xy_m[1],
                "yaw_rad": revision.layout_proposal.yaw_rad,
            }
        else:
            requested_layout = initial.validation.actual_layout

        if initial.compile_request.compile_input.source_media != source_media:
            _fail("input_lineage", "initial compile differs from the original image evidence")
        proposal_ref = initial.compile_request.compile_input.interpretation_proposal
        proposal = _read_model(
            artifact_store,
            proposal_ref,
            SceneInterpretationProposalV1,
            "initial asset query",
        )
        if (
            proposal.effective_prompt != prompt
            or proposal.source_media_sha256 != source_media.source.sha256
        ):
            _fail("input_lineage", "initial proposal differs from the original prompt or image")
        if initial.compile_receipt.qualification != qualification:
            _fail("lookup_lineage", "initial compile qualification differs from audited lookup")

        timings = _timings(entries, timing_samples)
        data: dict[str, Any] = {
            "schema_version": "harness.limited_image_preview_manifest.v1",
            "preview_id": "0" * 64,
            "project_id": project_id,
            "workflow_run_id": snapshot.workflow_run_id,
            "principal_id": snapshot.principal_id,
            "mode": mode,
            "scope": "limited_image_exact_reuse_rigid_scene",
            "requested_profile": "genesis.rigid_scene@1",
            "requested_layout": requested_layout,
            "source_media": source_media_ref,
            "raw_image": source_media.source,
            "input_text": prompt,
            "asset_query": proposal_ref,
            "asset_representation": representation,
            "asset_qualification": qualification,
            "initial_stage": initial.model,
            "revised_stage": None if revised_stage is None else revised_stage.model,
            "timings": timings,
            "total_duration_ns": sum(item.duration_ns for item in timings),
            "authority": "validated_limited_preview_only",
            "robot_policy_evaluated": False,
        }
        data["preview_id"] = limited_image_preview_manifest_sha256(data)
        return LimitedImagePreviewManifestV1.model_validate(data)
    except LimitedImagePreviewManifestBuildError:
        raise
    except (
        ArtifactResolutionError,
        GoldenRunCorruptionError,
        ValidationError,
        OSError,
        ValueError,
    ) as error:
        raise LimitedImagePreviewManifestBuildError(
            "manifest_assembly_failed", f"limited preview manifest assembly failed: {error}"
        ) from error


class _StageEvidence:
    def __init__(
        self,
        *,
        model: LimitedImagePreviewStageV1,
        compile_request: Image2EnvGenesisCompileRequestV1 | Image2EnvGenesisCompileRequestV2,
        compile_receipt: Image2EnvGenesisCompileReceiptV1 | Image2EnvGenesisCompileReceiptV2,
        validation: LimitedSceneValidationV1,
    ) -> None:
        self.model = model
        self.compile_request = compile_request
        self.compile_receipt = compile_receipt
        self.validation = validation


def _stage(
    store: LocalArtifactStore,
    *,
    phase: str,
    compile_operation: Any,
    replay_operation: Any,
    observation_operation: Any | None,
    validation_operation: Any,
    prompt_revision: ArtifactRef | None,
    workflow_head: ArtifactRef,
) -> _StageEvidence:
    if phase == "initial":
        compile_receipt_ref, compile_receipt = _operation_output(
            store, compile_operation, Image2EnvGenesisCompileReceiptV1, "initial compile"
        )
        compile_request = _read_model(
            store, compile_receipt.request, Image2EnvGenesisCompileRequestV1, "initial request"
        )
        replay_receipt_ref, replay_receipt = _operation_output(
            store, replay_operation, GenesisSceneExecutionReceiptV2, "initial replay"
        )
        replay_request = _read_model(
            store, replay_receipt.request, GenesisSceneExecutionRequestV2, "initial replay request"
        )
        command = _operation_command(store, compile_operation)
        if Image2EnvGenesisCompileRequestV1.model_validate(command.parameters) != compile_request:
            _fail("command_lineage", "initial compile command differs from its receipt request")
        replay_input = GenesisSceneReplayInputV2.model_validate(
            _operation_command(store, replay_operation).parameters
        )
        if (
            replay_input.execution_request != replay_receipt.request
            or replay_input.compile_run_id != compile_operation.child_run_id
        ):
            _fail("command_lineage", "initial replay command differs from its compile lineage")
        if observation_operation is None:
            _fail("observation_lineage", "initial stage requires a fresh observation")
        observation_ref, observation = _operation_output(
            store, observation_operation, GenesisFreshCaptureReceiptV1, "fresh observation"
        )
        observe_input = GenesisFreshCaptureInputV1.model_validate(
            _operation_command(store, observation_operation).parameters
        )
        if (
            observation.replay_receipt != replay_receipt_ref
            or observation.replay_run_id != replay_operation.child_run_id
            or observe_input.replay_run_id != replay_operation.child_run_id
        ):
            _fail("observation_lineage", "fresh observation differs from the initial replay")
        compile_skill = "image2env.compile@2.0.0"
    else:
        compile_receipt_ref, compile_receipt = _operation_output(
            store, compile_operation, Image2EnvGenesisCompileReceiptV2, "revised compile"
        )
        compile_request = _read_model(
            store, compile_receipt.request, Image2EnvGenesisCompileRequestV2, "revised request"
        )
        replay_receipt_ref, replay_receipt = _operation_output(
            store, replay_operation, GenesisSceneExecutionReceiptV3, "revised replay"
        )
        replay_request = _read_model(
            store, replay_receipt.request, GenesisSceneExecutionRequestV3, "revised replay request"
        )
        envelope = RevisedCompileInvocationV1.model_validate(
            _operation_command(store, compile_operation).parameters
        )
        if envelope.request != compile_receipt.request:
            _fail("command_lineage", "revised compile command differs from its receipt request")
        replay_input = GenesisSceneCompileBindingV2.model_validate(
            _operation_command(store, replay_operation).parameters
        )
        if (
            replay_input.execution_request != replay_receipt.request
            or replay_input.compile_run_id != compile_operation.child_run_id
            or replay_input.compile_receipt != compile_receipt_ref
        ):
            _fail("command_lineage", "revised replay command differs from its compile lineage")
        observation_ref = None
        compile_skill = "image2env.compile@3.0.0"
    if (
        replay_request.compile_receipt != compile_receipt_ref
        or replay_request.scene != compile_receipt.scene
        or replay_request.asset_qualification != compile_receipt.qualification
    ):
        _fail("native_lineage", "replay request differs from the exact compile output")
    validation_ref, validation = _operation_output(
        store, validation_operation, LimitedSceneValidationV1, f"{phase} validation"
    )
    validate_input = LimitedSceneValidateInputV1.model_validate(
        _operation_command(store, validation_operation).parameters
    )
    if (
        validate_input.execution_receipt != replay_receipt_ref
        or validation.execution_receipt != replay_receipt_ref
        or validation.expected_layout != validate_input.expected_layout
    ):
        _fail("validation_lineage", "validation differs from its exact replay command")
    model = LimitedImagePreviewStageV1(
        phase=phase,
        compile_skill_ref=compile_skill,
        compile_request=compile_receipt.request,
        compile_receipt=compile_receipt_ref,
        prompt_revision=prompt_revision,
        scene=compile_receipt.scene,
        runtime_manifest=replay_request.runtime_manifest,
        replay_receipt=replay_receipt_ref,
        replay=replay_receipt.replay,
        replay_report=replay_receipt.report,
        baseline_preview=replay_receipt.baseline_preview,
        half_dt_preview=replay_receipt.half_dt_preview,
        retained_media_index=replay_receipt.retained_index,
        fresh_observation=observation_ref,
        validation=validation_ref,
        validation_result=validation,
        workflow_head=workflow_head,
    )
    return _StageEvidence(
        model=model,
        compile_request=compile_request,
        compile_receipt=compile_receipt,
        validation=validation,
    )


def _operation_output(
    store: LocalArtifactStore, operation: Any, model: type[BaseModel], label: str
):
    if operation.tool_result is None:
        _fail("missing_tool_result", f"{label} has no terminal ToolResult")
    result = _read_model(store, operation.tool_result, ToolResultV2, f"{label} ToolResult")
    state = _read_model(store, result.run_state, RunState, f"{label} RunState")
    if (
        result.workflow_run_id != operation.workflow_run_id
        or result.operation_id != operation.operation_id
        or result.skill_ref != operation.skill_ref
        or result.child_run_id != operation.child_run_id
        or result.status != operation.status
        or state.run_id != operation.child_run_id
        or f"{state.skill_id}@{state.skill_version}" != operation.skill_ref
        or state.status != operation.status
    ):
        _fail("tool_result_lineage", f"{label} result differs from the Golden operation")
    refs = [*state.artifacts, *result.diagnostics]
    for event in state.events:
        refs.extend(event.artifact_refs)
    unique: dict[str, ArtifactRef] = {}
    for ref in refs:
        previous = unique.setdefault(ref.sha256, ref)
        if previous != ref:
            _fail("artifact_identity_drift", f"{label} repeats one digest with changed metadata")
    matches = {
        sha256: ref
        for sha256, ref in unique.items()
        if ref.schema_version == model.model_config["json_schema_extra"]["$id"]
    }
    if len(matches) != 1:
        _fail("typed_output_missing", f"{label} must retain one exact typed output artifact")
    ref = next(iter(matches.values()))
    value = _read_model(store, ref, model, label)
    if result.status is RunStatus.SUCCEEDED:
        if (
            result.typed_output != value.model_dump(mode="json")
            or state.output != result.typed_output
        ):
            _fail("typed_output_drift", f"{label} inline output differs from immutable CAS")
    elif (
        model is not LimitedSceneValidationV1
        or result.typed_output is not None
        or state.output is not None
    ):
        _fail("unexpected_failure", "only the initial semantic validation may be terminal failed")
    elif ref not in result.diagnostics or not any(
        ref in event.artifact_refs for event in state.events
    ):
        _fail(
            "failure_evidence_missing", "failed semantic validation is not retained in diagnostics"
        )
    return ref, value


def _operation_containing(store, operations, ref, model):
    matches = []
    for operation in operations:
        if operation.skill_ref != "genesis.observe@3.0.0":
            continue
        candidate, _ = _operation_output(store, operation, model, "fresh observation")
        if candidate == ref:
            matches.append(operation)
    if len(matches) != 1:
        _fail("observation_lineage", "observation citation must identify one Golden operation")
    return matches[0]


def _operation_command(store, operation) -> RunCommandRequest:
    command = _read_model(store, operation.command, RunCommandRequest, "operation command")
    if (
        command.command_id != operation.operation_id
        or command.workflow_run_id != operation.workflow_run_id
        or command.principal_id != operation.principal_id
        or command.skill_ref != operation.skill_ref
        or command.expected_workflow_revision != operation.base_revision
    ):
        _fail("command_lineage", "operation command differs from its durable journal entry")
    return command


def _inputs(snapshot, input_ref, lookup_ref, input_value, lookup_value):
    if set(input_value) != {
        "kind",
        "workflow_id",
        "source_media",
        "effective_prompt",
        "catalog_ref",
    }:
        _fail("input_shape", "preview input has unexpected or missing fields")
    if set(lookup_value) != {
        "kind",
        "workflow_id",
        "input_ref",
        "catalog_ref",
        "query",
        "asset_id",
        "representation",
        "qualification",
        "source_scene",
    }:
        _fail("lookup_shape", "preview lookup has unexpected or missing fields")
    media = ImageSourceMedia.model_validate(input_value["source_media"])
    prompt = ArtifactRef.model_validate(input_value["effective_prompt"])
    catalog = ArtifactRef.model_validate(input_value["catalog_ref"])
    representation = ArtifactRef.model_validate(lookup_value["representation"])
    qualification = ArtifactRef.model_validate(lookup_value["qualification"])
    source_scene = ArtifactRef.model_validate(lookup_value["source_scene"])
    if (
        input_value["kind"] != "limited_preview_input.v1"
        or str(input_value["workflow_id"]) != str(snapshot.workflow_run_id)
        or lookup_value["kind"] != "limited_preview_lookup.v1"
        or str(lookup_value["workflow_id"]) != str(snapshot.workflow_run_id)
        or ArtifactRef.model_validate(lookup_value["input_ref"]) != input_ref
        or ArtifactRef.model_validate(lookup_value["catalog_ref"]) != catalog
        or lookup_value["query"] != "mouse"
        or type(lookup_value["asset_id"]) is not str
        or not lookup_value["asset_id"]
    ):
        _fail("input_lookup_lineage", "input and audited lookup do not share one workflow")
    return media, prompt, representation, qualification, source_scene


def _repair_actions(actions: tuple[str, ...]) -> None:
    if len(actions) < 9 or actions[:4] != _REPAIR_PREFIX:
        _fail(
            "journal_shape",
            "repair must validate the initial replay before diagnostic observation",
        )
    tail = (
        "advisory",
        "prompt_revision",
        "image2env.compile@3.0.0",
        "image2env.replay@3.0.0",
        "image2env.validate@3.0.0",
    )
    if actions[-5:] != tail or any(action != "genesis.observe@3.0.0" for action in actions[4:-5]):
        _fail("journal_shape", "repair journal has an unsupported stage or order")


def _one_operation(operations, skill_ref):
    matches = [operation for operation in operations if operation.skill_ref == skill_ref]
    if len(matches) != 1:
        _fail("journal_shape", f"workflow requires one exact {skill_ref} operation")
    return matches[0]


def _verify_journal(store, snapshot, entries, heads) -> None:
    if len(entries) != snapshot.revision or tuple(
        entry.base_revision for entry in entries
    ) != tuple(range(snapshot.revision)):
        _fail("journal_revision", "Golden journal is not a complete contiguous revision chain")
    for index, entry in enumerate(entries, start=1):
        item = entry.operation if entry.operation is not None else entry.control
        if item is None or entry.workflow_run_id != snapshot.workflow_run_id:
            _fail("journal_entry", "Golden journal entry is incomplete or foreign")
        if (
            item.workflow_run_id != snapshot.workflow_run_id
            or item.principal_id != snapshot.principal_id
        ):
            _fail("journal_entry", "Golden journal detail is outside workflow authority")
        if item.committed_revision != index:
            _fail("journal_entry", "Golden journal detail differs from its receipt chain")
        if entry.operation is not None:
            head = _read_model(
                store,
                heads[index],
                WorkflowOperationReceiptV2,
                "journal operation receipt",
            )
            if (
                head.operation_id != entry.operation.operation_id
                or head.command != entry.operation.command
                or head.tool_result != entry.operation.tool_result
                or head.status != entry.operation.status
            ):
                _fail("journal_entry", "operation differs from its immutable workflow receipt")
        elif heads.get(index) != entry.control.receipt:
            _fail("journal_entry", "control differs from its immutable workflow receipt")
    if heads.get(snapshot.revision) != snapshot.receipt_head:
        _fail("receipt_head", "snapshot head differs from the complete receipt chain")


def _receipt_heads(store: LocalArtifactStore, snapshot: RunSnapshot) -> dict[int, ArtifactRef]:
    heads: dict[int, ArtifactRef] = {}
    current = snapshot.receipt_head
    seen: set[str] = set()
    while current.schema_version != "harness.workflow_start_receipt.v1":
        if current.sha256 in seen:
            _fail("receipt_cycle", "workflow receipt chain contains a cycle")
        seen.add(current.sha256)
        value = _read_untyped_record(store, current, "workflow receipt")
        revision = value.get("committed_revision")
        if type(revision) is not int or revision in heads:
            _fail("receipt_revision", "workflow receipt revisions are missing or duplicated")
        if (
            value.get("workflow_run_id") != str(snapshot.workflow_run_id)
            or value.get("principal_id") != snapshot.principal_id
        ):
            _fail("receipt_authority", "workflow receipt belongs to another workflow or principal")
        if current.schema_version == "harness.workflow_operation_receipt.v2":
            _read_model(store, current, WorkflowOperationReceiptV2, "workflow operation receipt")
        elif current.schema_version == "harness.external_agent_advisory_receipt.v2":
            from .schemas.golden_control_v2 import ExternalAgentAdvisoryReceiptV2

            _read_model(store, current, ExternalAgentAdvisoryReceiptV2, "advisory receipt")
        elif current.schema_version == "harness.prompt_revision.v3":
            _read_model(store, current, PromptRevisionV3, "prompt revision receipt")
        else:
            _fail("receipt_schema", "workflow receipt chain contains an unsupported receipt")
        heads[revision] = current
        current = ArtifactRef.model_validate(value["predecessor_receipt"])
    start = _read_model(store, current, WorkflowStartReceipt, "workflow start receipt")
    if (
        start.workflow_run_id != snapshot.workflow_run_id
        or start.principal_id != snapshot.principal_id
    ):
        _fail("receipt_authority", "workflow start receipt belongs to another authority")
    if tuple(sorted(heads)) != tuple(range(1, snapshot.revision + 1)):
        _fail("receipt_revision", "workflow receipt chain is not contiguous")
    return heads


def _timings(entries: Sequence[StoredWorkflowEntry], samples: Sequence[Mapping[str, object]]):
    if isinstance(samples, (str, bytes)) or not isinstance(samples, Sequence) or not samples:
        _fail("timing_missing", "preview requires measured controller timing samples")
    expected_actions = [_LOOKUP_TOOL, *(_MCP_FOR_ACTION[entry.action_ref] for entry in entries)]
    observed_actions: list[str] = []
    input_ns = 0
    compile_ns = 0
    runtime_ns = 0
    simulation_ns = 0
    cas_ns = 0
    codex_ns = 0
    validate_ns = 0
    input_count = 0
    for sample in samples:
        if not isinstance(sample, Mapping):
            _fail("timing_shape", "timing sample must be a mapping")
        allowed = {
            "backend_elapsed_ns",
            "calls",
            "attribution_complete",
            "failure",
            "valid",
            "input_elapsed_ns",
            "wall_basis",
        }
        if set(sample) - allowed or set(sample) < allowed - {"wall_basis"}:
            _fail("timing_shape", "timing sample has unexpected or missing fields")
        if sample["valid"] is not True:
            _fail("timing_incomplete", "preview timings require valid measured wall time")
        backend = _positive_ns(sample["backend_elapsed_ns"], "backend_elapsed_ns")
        value = sample["input_elapsed_ns"]
        if value is not None:
            input_ns += _positive_ns(value, "input_elapsed_ns")
            input_count += 1
        calls = sample["calls"]
        if not isinstance(calls, list):
            _fail("timing_shape", "controller calls must be one measured list")
        failure = sample["failure"]
        complete = sample["attribution_complete"]
        if failure is not None or complete is not True:
            if (
                type(failure) is not str
                or not failure
                or complete is not False
                or calls
                or (
                    "wall_basis" in sample
                    and sample["wall_basis"] != "controller_started_to_observed_owned_exit"
                )
            ):
                _fail(
                    "timing_incomplete",
                    "failed controller wall has no trustworthy stage attribution",
                )
            # A pre-business failed attempt contributes its complete measured wall to
            # the unattributed Codex/MCP residual.  No stage split is manufactured.
            codex_ns += backend
            continue
        if "wall_basis" in sample or not calls:
            _fail("timing_incomplete", "completed attempt requires exact attributed calls")
        transport_total = 0
        for call in calls:
            if not isinstance(call, Mapping) or set(call) != {
                "tool",
                "stage",
                "transport_elapsed_ns",
                "service_elapsed_ns",
            }:
                _fail("timing_shape", "tool timing has unexpected or missing fields")
            tool = call["tool"]
            if type(tool) is not str or tool not in _STAGES:
                _fail("timing_tool", "tool timing contains an unsupported exact tool")
            stage = call["stage"]
            expected_stage = _STAGES[tool]
            if tool == _STATUS_TOOL:
                if stage not in _STATUS_STAGES or call["service_elapsed_ns"] is not None:
                    _fail("timing_stage", "status timing has an invalid workflow stage")
            elif stage != expected_stage:
                _fail("timing_stage", "tool timing differs from its exact service stage")
            transport = _positive_ns(call["transport_elapsed_ns"], "transport_elapsed_ns")
            transport_total += transport
            service_value = call["service_elapsed_ns"]
            service = (
                0 if service_value is None else _positive_ns(service_value, "service_elapsed_ns")
            )
            if service > transport:
                _fail("timing_overlap", "service duration exceeds its enclosing transport")
            if tool not in {_STATUS_TOOL}:
                observed_actions.append(tool)
            if tool in {_STATUS_TOOL, _LOOKUP_TOOL}:
                if service:
                    _fail("timing_stage", "read-only preview tools cannot claim child service time")
                cas_ns += transport
            elif tool.startswith("image2env_compile_"):
                if not service:
                    _fail("timing_missing", "compile timing requires measured child service time")
                compile_ns += service
                runtime_ns += transport - service
            elif tool.startswith("image2env_replay_") or tool == "genesis_observe_v3_0_0":
                if not service:
                    _fail(
                        "timing_missing", "simulation timing requires measured child service time"
                    )
                simulation_ns += service
                runtime_ns += transport - service
            elif tool == "image2env_validate_v3_0_0":
                if not service:
                    _fail(
                        "timing_missing", "validation timing requires measured child service time"
                    )
                validate_ns += transport
            else:
                if service:
                    _fail("timing_stage", "control timing cannot claim child Skill time")
                codex_ns += transport
        if transport_total > backend:
            _fail("timing_overlap", "tool transports exceed the measured Codex backend wall")
        codex_ns += backend - transport_total
    if input_count != 1 or observed_actions != expected_actions:
        _fail("timing_lineage", "timed exact tool sequence differs from the Golden journal")
    values = (
        ("input_asset", input_ns),
        ("compile", compile_ns),
        ("runtime_integrity", runtime_ns),
        ("simulation_render", simulation_ns),
        ("cas_deep_read", cas_ns),
        ("codex_mcp", codex_ns),
        ("validate_publish", validate_ns),
    )
    if any(value <= 0 for _, value in values):
        _fail("timing_missing", "every fixed preview timing segment requires measured time")
    return tuple(
        LimitedImagePreviewTimingV1(segment=name, duration_ns=value) for name, value in values
    )


def _positive_ns(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        _fail("timing_value", f"{label} must be a positive exact integer")
    return value


def _put_source_media(store: LocalArtifactStore, media: ImageSourceMedia) -> ArtifactRef:
    payload = _canonical(media.model_dump(mode="json"))
    with tempfile.TemporaryDirectory(prefix="limited-preview-manifest-") as directory:
        path = Path(directory) / "image-source-media.json"
        path.write_bytes(payload)
        ref = store.put_file(
            path,
            name=path.name,
            media_type="application/json",
            schema_version="harness.image_source_media.v1",
        )
    if _read_model(store, ref, ImageSourceMedia, "image source media") != media:
        _fail("source_media_drift", "published source-media support differs from input evidence")
    return ref


def _read_model(store: LocalArtifactStore, ref: ArtifactRef, model: type[BaseModel], label: str):
    schema = model.model_config["json_schema_extra"]["$id"]
    if (
        ref.schema_version != schema
        or ref.media_type != "application/json"
        or ref.uri != f"artifact://sha256/{ref.sha256}"
    ):
        _fail("artifact_type", f"{label} has the wrong immutable CAS type")
    payload = store.resolve(ref).path.read_bytes()
    value = model.model_validate_json(payload)
    if payload not in (
        _canonical(value.model_dump(mode="json")),
        _canonical(value.model_dump(mode="json")) + b"\n",
    ):
        _fail("noncanonical_json", f"{label} is not canonical JSON")
    return value


def _read_untyped_record(store: LocalArtifactStore, ref: ArtifactRef, label: str) -> dict[str, Any]:
    if ref.uri != f"artifact://sha256/{ref.sha256}" or ref.media_type != "application/json":
        _fail("artifact_type", f"{label} is not immutable CAS JSON")
    payload = store.resolve(ref).path.read_bytes()
    value = json.loads(payload)
    if not isinstance(value, dict) or payload not in (_canonical(value), _canonical(value) + b"\n"):
        _fail("noncanonical_json", f"{label} is not a canonical JSON object")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _fail(reason: str, message: str):
    raise LimitedImagePreviewManifestBuildError(reason, message)


__all__ = [
    "LimitedImagePreviewManifestBuildError",
    "build_limited_image_preview_manifest",
]
