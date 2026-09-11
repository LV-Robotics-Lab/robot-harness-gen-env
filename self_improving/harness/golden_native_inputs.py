"""Exact native inputs derived from one current Golden head and its child authority."""

import json
import tempfile
from pathlib import Path

from .artifacts import LocalArtifactStore
from .genesis_runtime_manifest import read_runtime_manifest
from .golden_execution import GoldenExecutionCatalog
from .golden_run import GoldenRunHarness
from .schemas.common import ArtifactRef, Invocation, RunState, RunStatus
from .schemas.genesis_fresh_capture import GenesisFreshCaptureInputV1, GenesisFreshCaptureReceiptV1
from .schemas.genesis_scene_application_v2 import GenesisSceneReplayInputV2
from .schemas.genesis_scene_assessment import GenesisSceneValidateInputV1
from .schemas.genesis_scene_execution_v2 import (
    GenesisSceneExecutionReceiptV2,
    GenesisSceneExecutionRequestV2,
)
from .schemas.image2env_genesis_compile import (
    Image2EnvGenesisCompileReceiptV1,
    Image2EnvGenesisCompileRequestV1,
)
from .schemas.workflow import RunReadRequest, RunSnapshot, RunStartRequest
from .schemas.workflow_operation import (
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)
from .schemas.x2env_compile_evidence import SceneInterpretationProposalV1
from .trusted_state import WorldFactEvidence


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class NativeImageWorkflowInputs:
    """Server-owned templates and runtime; caller supplies only a verified current snapshot.

    The four bound build methods are MCPWorkflowSkillInputProvider callbacks.
    They do not invoke Skills or grant simulator authority.
    """

    def __init__(
        self, *, harness, store, execution_catalog, compile_template_ref, approved_runtime_manifest
    ):
        if (
            type(harness) is not GoldenRunHarness
            or type(store) is not LocalArtifactStore
            or type(execution_catalog) is not GoldenExecutionCatalog
        ):
            raise TypeError("native inputs require exact Golden authority, catalog and CAS")
        self._harness = harness
        self._store = store
        self._catalog = execution_catalog
        self._template = compile_template_ref
        self._manifest = approved_runtime_manifest
        if (
            store.root != harness.artifact_root
            or execution_catalog is not harness.execution_catalog
        ):
            raise ValueError("native builders require the Golden authority's exact CAS and catalog")
        self._configuration()

    def _bytes(self, ref):
        if ref.uri != f"artifact://sha256/{ref.sha256}":
            raise ValueError("native input references require exact CAS identities")
        return self._store.resolve(ref).path.read_bytes()

    def _read(self, ref, model):
        payload = self._bytes(ref)
        value = model.model_validate_json(payload)
        expected = {
            RunState: "harness.run_state.v1",
            Invocation: "harness.skill_invocation.v1",
        }.get(model)
        if expected is None:
            expected = value.schema_version
        if ref.schema_version != expected or ref.media_type != "application/json":
            raise ValueError("artifact type differs from exact input contract")
        if payload not in (
            _canonical(value.model_dump(mode="json")),
            _canonical(value.model_dump(mode="json")) + b"\n",
        ):
            raise ValueError("input evidence must be canonical JSON")
        return value

    def _closure(self, value, seen):
        if isinstance(value, dict):
            if {"uri", "sha256", "bytes", "media_type", "schema_version", "name"} <= value.keys():
                ref = ArtifactRef.model_validate(value)
                payload = self._bytes(ref)
                if ref.sha256 not in seen:
                    seen.add(ref.sha256)
                    if ref.media_type == "application/json":
                        self._closure(json.loads(payload), seen)
            else:
                for item in value.values():
                    self._closure(item, seen)
        elif isinstance(value, list):
            for item in value:
                self._closure(item, seen)

    def _configuration(self):
        template = self._read(self._template, Image2EnvGenesisCompileRequestV1)
        self._closure(template.model_dump(mode="json"), set())
        proposal = self._read(
            template.compile_input.interpretation_proposal, SceneInterpretationProposalV1
        )
        objective = self._store.resolve(proposal.effective_prompt).path.read_text(encoding="utf-8")
        read_runtime_manifest(self._store, self._manifest)
        return template, objective

    def _current(self, snapshot):
        if type(snapshot) is not RunSnapshot:
            raise TypeError("build requires an exact RunSnapshot")
        request = RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=snapshot.principal_id,
            workflow_run_id=snapshot.workflow_run_id,
        )
        if self._harness.read(request) != snapshot or snapshot.active_operation is not None:
            raise ValueError("input snapshot is not the current idle workflow head")
        # Golden's public reader has reverified the owned CAS closure. The constructor
        # binds this store's root to that same public authority, including for publication.
        return request

    def build_compile(self, snapshot: RunSnapshot) -> Image2EnvGenesisCompileRequestV1:
        self._current(snapshot)
        template, objective = self._configuration()
        if snapshot.revision != 0:
            raise ValueError("initial-only compile requires the initial workflow head")
        self._initial_binding(snapshot, template, objective)
        return template

    def _initial_binding(self, snapshot, template, objective):
        start = self._read(snapshot.start_request_ref, RunStartRequest)
        facts = [self._read(ref, WorldFactEvidence) for ref in start.initial_fact_evidence]
        for key, expected in (
            ("request.image_source", template.compile_input.source_media.model_dump(mode="json")),
            ("request.objective", objective),
        ):
            values = [
                fact.value for fact in facts if fact.key == key and fact.source_kind == "user_input"
            ]
            if values != [expected]:
                raise ValueError("operator compile template differs from initial workflow facts")

    def _previous(self, snapshot, skill_ref, model):
        operation = self._read(snapshot.receipt_head, WorkflowOperationReceiptV2)
        tool = self._read(operation.tool_receipt, TrustedToolReceiptV2)
        result = self._read(operation.tool_result, ToolResultV2)
        state = self._read(tool.run_state, RunState)
        invocation = self._read(tool.invocation, Invocation)
        if (
            operation.workflow_run_id != snapshot.workflow_run_id
            or operation.principal_id != snapshot.principal_id
            or operation.committed_revision != snapshot.revision
            or operation.resulting_state_sha256 != snapshot.state_sha256
            or operation.resulting_state != snapshot.state_ref
            or operation.status is not RunStatus.SUCCEEDED
            or tool.workflow_run_id != snapshot.workflow_run_id
            or tool.principal_id != snapshot.principal_id
            or tool.skill.skill_ref != skill_ref
            or not (operation.operation_id == tool.operation_id == result.operation_id)
            or not (tool.child_run_id == result.child_run_id == state.run_id == invocation.run_id)
            or tool.run_state != result.run_state
            or tool.invocation != result.invocation
            or state.status is not RunStatus.SUCCEEDED
            or tool.status is not RunStatus.SUCCEEDED
            or f"{state.skill_id}@{state.skill_version}" != skill_ref
            or state.invocation_digest != invocation.invocation_digest
            or tool.typed_output != state.output
            or result.typed_output != state.output
        ):
            raise ValueError("native input predecessor differs from owned successful Skill")
        authority = self._catalog.child_authority_for(skill_ref)
        if authority is None:
            raise ValueError("native input requires durable child authority")
        lifecycle = authority.read_controlled_lifecycle(state.run_id)
        if (
            lifecycle.intent is None
            or lifecycle.terminal != state
            or lifecycle.invocation != invocation
        ):
            raise ValueError("native predecessor differs from durable child lifecycle")
        output = model.model_validate(state.output)
        refs = [ref for ref in state.artifacts if ref.schema_version == output.schema_version]
        if len(refs) != 1 or refs[0] not in tool.supporting_artifacts:
            raise ValueError("native predecessor requires one exact output receipt")
        if self._read(refs[0], model) != output:
            raise ValueError("native predecessor payload differs from its receipt")
        self._current(snapshot)
        return state.run_id, refs[0], output, invocation, operation

    def _parent_output(self, operation, skill_ref, model):
        parent = self._read(operation.predecessor_receipt, WorkflowOperationReceiptV2)
        tool = self._read(parent.tool_receipt, TrustedToolReceiptV2)
        # The current public Golden read already reverified this complete predecessor
        # chain. Here bind its semantic output to the downstream request, not a latest run.
        if tool.skill.skill_ref != skill_ref or tool.status is not RunStatus.SUCCEEDED:
            raise ValueError("native input requires the exact successful parent Skill")
        value = model.model_validate(tool.typed_output)
        refs = [
            ref for ref in tool.supporting_artifacts if ref.schema_version == value.schema_version
        ]
        if len(refs) != 1 or self._read(refs[0], model) != value:
            raise ValueError("native parent requires one exact typed receipt")
        return tool.child_run_id, refs[0], value

    def build_replay(self, snapshot: RunSnapshot) -> GenesisSceneReplayInputV2:
        run_id, ref, receipt, invocation, _ = self._previous(
            snapshot, "image2env.compile@2.0.0", Image2EnvGenesisCompileReceiptV1
        )
        template, objective = self._configuration()
        self._initial_binding(snapshot, template, objective)
        if (
            invocation.effective_parameters != template.model_dump(mode="json")
            or self._read(receipt.request, Image2EnvGenesisCompileRequestV1) != template
        ):
            raise ValueError("compile predecessor differs from approved template")
        request = GenesisSceneExecutionRequestV2(
            schema_version="harness.genesis_scene_execution_request.v2",
            scene=receipt.scene,
            compile_receipt=ref,
            runtime_manifest=self._manifest,
            asset_qualification=receipt.qualification,
        )
        self._closure(request.model_dump(mode="json"), set())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene-execution-request.json"
            path.write_bytes(_canonical(request.model_dump(mode="json")))
            execution = self._store.put_file(
                path,
                name=path.name,
                media_type="application/json",
                schema_version=request.schema_version,
            )
        return GenesisSceneReplayInputV2(
            schema_version="harness.genesis_scene_replay_input.v2",
            compile_run_id=run_id,
            execution_request=execution,
        )

    def build_observe(self, snapshot: RunSnapshot) -> GenesisFreshCaptureInputV1:
        template, objective = self._configuration()
        self._initial_binding(snapshot, template, objective)
        run_id, _, receipt, invocation, operation = self._previous(
            snapshot, "image2env.replay@2.0.0", GenesisSceneExecutionReceiptV2
        )
        inputs = GenesisSceneReplayInputV2.model_validate(invocation.effective_parameters)
        request = self._read(inputs.execution_request, GenesisSceneExecutionRequestV2)
        if (
            receipt.request != inputs.execution_request
            or request.runtime_manifest != self._manifest
        ):
            raise ValueError("replay predecessor uses a different execution request or runtime")
        compile_id, compile_ref, compiled = self._parent_output(
            operation, "image2env.compile@2.0.0", Image2EnvGenesisCompileReceiptV1
        )
        if (
            inputs.compile_run_id != compile_id
            or request.compile_receipt != compile_ref
            or request.scene != compiled.scene
            or request.asset_qualification != compiled.qualification
        ):
            raise ValueError("replay input differs from its own workflow compile parent")
        return GenesisFreshCaptureInputV1(
            schema_version="harness.genesis_fresh_capture_input.v1",
            replay_run_id=run_id,
            ttl_seconds=300,
        )

    def build_validate(self, snapshot: RunSnapshot) -> GenesisSceneValidateInputV1:
        template, objective = self._configuration()
        self._initial_binding(snapshot, template, objective)
        _, _, receipt, invocation, operation = self._previous(
            snapshot, "genesis.observe@3.0.0", GenesisFreshCaptureReceiptV1
        )
        inputs = GenesisFreshCaptureInputV1.model_validate(invocation.effective_parameters)
        if (
            receipt.replay_run_id != inputs.replay_run_id
            or receipt.ttl_seconds != inputs.ttl_seconds
        ):
            raise ValueError("observation predecessor differs from requested replay or TTL")
        replay_id, replay_ref, _ = self._parent_output(
            operation, "image2env.replay@2.0.0", GenesisSceneExecutionReceiptV2
        )
        if inputs.replay_run_id != replay_id or receipt.replay_receipt != replay_ref:
            raise ValueError("observation input differs from its own workflow replay parent")
        return GenesisSceneValidateInputV1(
            schema_version="harness.genesis_scene_validate_input.v1",
            replay_run_id=receipt.replay_run_id,
        )
