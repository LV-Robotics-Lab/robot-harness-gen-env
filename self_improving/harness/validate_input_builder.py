"""Build the exact validate-v2 input from one verified Golden replay head."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pydantic import BaseModel

from .application import CompileApplication
from .artifacts import LocalArtifactStore
from .replay_application import ReplayApplication
from .run_receipts import PortableRunReceiptPublisher, load_portable_run_receipt
from .schemas.common import ArtifactRef
from .schemas.text2env import (
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
    Text2EnvReplayOutput,
)
from .schemas.text2env_validate_v2 import Text2EnvValidateV2Input
from .schemas.workflow import RunSnapshot
from .schemas.workflow_operation import (
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)


class ValidateInputBuildError(ValueError):
    """The requested workflow head cannot produce one exact validate input."""


class Text2EnvValidateV2InputBuilder:
    """Project trusted compile/replay authorities into validate-v2 evidence refs."""

    __slots__ = ("_compile", "_replay", "_store")

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        compile_application: CompileApplication,
        replay_application: ReplayApplication,
    ) -> None:
        if type(artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be the exact LocalArtifactStore")
        if type(compile_application) is not CompileApplication:
            raise TypeError("compile_application must be the exact CompileApplication")
        if type(replay_application) is not ReplayApplication:
            raise TypeError("replay_application must be the exact ReplayApplication")
        if (
            Path(compile_application.artifact_root).resolve() != artifact_store.root
            or Path(replay_application.artifact_root).resolve() != artifact_store.root
        ):
            raise ValueError("compile, replay, and builder must share one artifact store")
        self._store = artifact_store
        self._compile = compile_application
        self._replay = replay_application

    def build(self, snapshot: RunSnapshot) -> Text2EnvValidateV2Input:
        """Build a stable CAS-only input from an exact succeeded replay revision."""

        if type(snapshot) is not RunSnapshot:
            raise TypeError("snapshot must be the exact RunSnapshot")
        if snapshot.revision != 2 or snapshot.active_operation is not None:
            raise ValidateInputBuildError("validate input requires a terminal replay revision 2")
        replay_history = _read_model(
            self._store,
            snapshot.receipt_head,
            WorkflowOperationReceiptV2,
        )
        compile_history = _read_model(
            self._store,
            replay_history.predecessor_receipt,
            WorkflowOperationReceiptV2,
        )
        replay_tool = _read_model(
            self._store,
            replay_history.tool_receipt,
            TrustedToolReceiptV2,
        )
        compile_tool = _read_model(
            self._store,
            compile_history.tool_receipt,
            TrustedToolReceiptV2,
        )
        if (
            compile_tool.skill.skill_ref != "text2env.compile@1.0.0"
            or replay_tool.skill.skill_ref != "text2env.replay@1.0.0"
            or compile_history.workflow_run_id != snapshot.workflow_run_id
            or replay_history.workflow_run_id != snapshot.workflow_run_id
            or compile_history.base_revision != 0
            or compile_history.committed_revision != 1
            or replay_history.base_revision != 1
            or replay_history.committed_revision != 2
            or compile_history.operation_id == replay_history.operation_id
        ):
            raise ValidateInputBuildError("workflow receipt chain is not exact compile then replay")

        publisher = PortableRunReceiptPublisher(self._store)
        compile_portable = publisher.publish(self._compile, compile_tool.child_run_id)
        replay_portable = publisher.publish(self._replay, replay_tool.child_run_id)
        compile_loaded = load_portable_run_receipt(self._store, compile_portable)
        replay_loaded = load_portable_run_receipt(self._store, replay_portable)
        compile_state = compile_loaded.run_state
        replay_state = replay_loaded.run_state
        compile_invocation = compile_loaded.invocation
        compile_output = Text2EnvCompileOutput.model_validate(compile_state.output)
        replay_output = Text2EnvReplayOutput.model_validate(replay_state.output)
        package = _publish_model(
            self._store,
            compile_output.environment_package,
            name="environment_package",
            schema_version="harness.environment_package.v1",
        )
        artifacts = {item.schema_version: item for item in replay_state.artifacts}
        execution = artifacts["harness.text2env_replay_receipt.v1"]
        execution_document = _read_json(self._store, execution)
        media = _publish_json(
            self._store,
            {
                "schema_version": "harness.replay_media_verification.v1",
                "verification": execution_document["media_verification"],
            },
            name="media_verification_receipt",
            schema_version="harness.replay_media_verification.v1",
        )
        compile_input = Text2EnvCompileInput.model_validate(compile_invocation.effective_parameters)
        provenance = _publish_json(
            self._store,
            {
                "schema_version": "harness.text2env_request_provenance.v1",
                "request": compile_input.request,
                "seed": compile_input.seed,
                "compile_skill_ref": "text2env.compile@1.0.0",
                "compile_run_id": str(compile_state.run_id),
                "compile_invocation_digest": compile_invocation.invocation_digest,
                "compile_trusted_receipt": compile_history.tool_receipt.model_dump(mode="json"),
                "compile_history_authority": replay_history.predecessor_receipt.model_dump(
                    mode="json"
                ),
                "compile_world_state_sha256": compile_history.resulting_state_sha256,
                "environment_package": package.model_dump(mode="json"),
            },
            name="request_provenance",
            schema_version="harness.text2env_request_provenance.v1",
        )
        return Text2EnvValidateV2Input(
            environment_package=package,
            compile_run_receipt=compile_portable,
            compile_qualification=self._compile.skills[0].qualification_artifact,
            replay_run_receipt=replay_portable,
            replay_qualification=self._replay.skills[0].qualification_artifact,
            replay_execution_receipt=execution,
            runtime_evidence=replay_output.runtime_evidence,
            runtime_validation_report=artifacts["robotwin.scene_validation.v1"],
            runtime_asset_snapshot_manifest=artifacts["harness.runtime_asset_snapshot.v1"],
            media_verification_receipt=media,
            request_provenance=provenance,
            runtime_event_transcript=artifacts["harness.runtime_event_transcript.v1"],
        )


def _read_model(store: LocalArtifactStore, ref: ArtifactRef, model):
    payload = store.resolve(ref).path.read_bytes()
    value = model.model_validate_json(payload)
    if payload != _canonical_bytes(value):
        raise ValidateInputBuildError("workflow evidence must be canonical JSON")
    return value


def _read_json(store: LocalArtifactStore, ref: ArtifactRef) -> dict[str, object]:
    value = json.loads(store.resolve(ref).path.read_bytes())
    return dict(value)


def _publish_model(
    store: LocalArtifactStore,
    value: BaseModel,
    *,
    name: str,
    schema_version: str,
) -> ArtifactRef:
    return _publish_bytes(
        store,
        _canonical_bytes(value),
        name=name,
        schema_version=schema_version,
    )


def _publish_json(
    store: LocalArtifactStore,
    value: dict[str, object],
    *,
    name: str,
    schema_version: str,
) -> ArtifactRef:
    return _publish_bytes(
        store,
        _canonical_bytes(value),
        name=name,
        schema_version=schema_version,
    )


def _publish_bytes(
    store: LocalArtifactStore,
    payload: bytes,
    *,
    name: str,
    schema_version: str,
) -> ArtifactRef:
    with tempfile.NamedTemporaryFile() as staging:
        staging.write(payload)
        staging.flush()
        return store.put_file(
            Path(staging.name),
            name=name,
            media_type="application/json",
            schema_version=schema_version,
        )


def _canonical_bytes(value: BaseModel | dict[str, object]) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
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


__all__ = ["Text2EnvValidateV2InputBuilder", "ValidateInputBuildError"]
