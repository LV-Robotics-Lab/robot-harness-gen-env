"""Prepare a trusted replay input from one successful System 2 compile turn.

This module is a handoff, not an executor.  It replays the compile history and
receipt closure from CAS, cross-binds those records to the supplied world
state, and publishes the two path-free JSON records needed by a later replay.
It never starts a replay, simulator, validation, or publication decision.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from pydantic import UUID4, Field

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas import (
    ArtifactRef,
    RunStatus,
    RuntimeConfig,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
    Text2EnvReplayInput,
)
from self_improving.harness.schemas.base import HarnessModel, Seed, Sha256
from self_improving.harness.system2.dispatcher import (
    TrustedToolReceipt,
    verify_trusted_tool_receipt,
)
from self_improving.harness.system2.domain import (
    StateDelta,
    System2ToolResult,
    TrustedWorldState,
    apply_state_delta,
)
from self_improving.harness.system2.history import verify_planner_history_authority
from self_improving.system2_compile_turn import System2CompileTurnResult

_COMPILE_SKILL_REF = "text2env.compile@1.0.0"
_ENVIRONMENT_PACKAGE_SCHEMA = "harness.environment_package.v1"
_REQUEST_PROVENANCE_SCHEMA = "harness.text2env_request_provenance.v1"


def _canonical_json_bytes(value: object) -> bytes:
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


def _canonical_equal(left: object, right: object) -> bool:
    return _canonical_json_bytes(left) == _canonical_json_bytes(right)


class _Text2EnvRequestProvenance(HarnessModel):
    """Path-free request-to-package identity for later validation."""

    schema_version: Literal["harness.text2env_request_provenance.v1"] = _REQUEST_PROVENANCE_SCHEMA
    request: str = Field(strict=True, min_length=3, max_length=2000)
    seed: Seed
    compile_skill_ref: Literal["text2env.compile@1.0.0"] = _COMPILE_SKILL_REF
    compile_run_id: UUID4
    compile_invocation_digest: Sha256
    compile_trusted_receipt: ArtifactRef
    compile_history_authority: ArtifactRef
    compile_world_state_sha256: Sha256
    environment_package: ArtifactRef


@dataclass(frozen=True, slots=True)
class PreparedSystem2Replay:
    """Verified replay input plus its canonical handoff evidence refs."""

    replay_input: Text2EnvReplayInput
    environment_package_ref: ArtifactRef
    request_provenance_ref: ArtifactRef


class System2ReplayHandoffError(RuntimeError):
    """The compile evidence could not authorize a replay handoff."""

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(f"System 2 replay handoff stopped: {reason}")


class System2ReplayHandoff:
    """Deep module verifying compile lineage and preparing replay inputs."""

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        scratch_root: Path,
    ) -> None:
        if type(artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be an exact LocalArtifactStore")
        if not isinstance(scratch_root, Path):
            raise TypeError("scratch_root must be a Path")
        self._artifact_store = artifact_store
        self._scratch_root = scratch_root.expanduser().resolve()

    def prepare(
        self,
        *,
        compile_result: System2CompileTurnResult,
        runtime_config: RuntimeConfig,
    ) -> PreparedSystem2Replay:
        """Verify one compile closure and prepare, but do not execute, replay."""

        trusted_result = self._validate_result_shape(compile_result)
        trusted_runtime = self._validate_runtime_config(runtime_config)
        verified = self._verify_receipt(trusted_result)
        history = self._verify_history(trusted_result)
        matching_entries = tuple(
            entry
            for entry in history.entries
            if entry.receipt == trusted_result.tool_result.trusted_receipt
        )
        if len(matching_entries) != 1:
            raise System2ReplayHandoffError(reason="compile_result_mismatch")
        typed_input = cast(Text2EnvCompileInput, verified.typed_input)
        typed_output = cast(Text2EnvCompileOutput, verified.typed_output)
        self._verify_result_bindings(
            trusted_result,
            history.lineage,
            verified.receipt,
        )

        replay_input = Text2EnvReplayInput(
            environment_package=typed_output.environment_package,
            runtime_config=trusted_runtime,
        )
        try:
            package_ref = self._publish_json(
                replay_input.environment_package,
                name="environment_package",
                schema_version=_ENVIRONMENT_PACKAGE_SCHEMA,
            )
            provenance = _Text2EnvRequestProvenance(
                request=typed_input.request,
                seed=typed_input.seed,
                compile_run_id=verified.run_state.run_id,
                compile_invocation_digest=verified.run_state.invocation_digest,
                compile_trusted_receipt=trusted_result.tool_result.trusted_receipt,
                compile_history_authority=trusted_result.history_authority,
                compile_world_state_sha256=trusted_result.world_state.state_sha256,
                environment_package=package_ref,
            )
            provenance_ref = self._publish_json(
                provenance,
                name="text2env_request_provenance",
                schema_version=_REQUEST_PROVENANCE_SCHEMA,
            )
        except Exception as error:
            raise System2ReplayHandoffError(reason="artifact_write_failed") from error
        return PreparedSystem2Replay(
            replay_input=replay_input,
            environment_package_ref=package_ref,
            request_provenance_ref=provenance_ref,
        )

    def _validate_result_shape(
        self,
        compile_result: System2CompileTurnResult,
    ) -> System2CompileTurnResult:
        if type(compile_result) is not System2CompileTurnResult:
            raise System2ReplayHandoffError(reason="compile_result_invalid")
        try:
            state = TrustedWorldState.model_validate(
                compile_result.world_state.model_dump(mode="python")
            )
            tool_result = System2ToolResult.model_validate(
                compile_result.tool_result.model_dump(mode="python")
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise System2ReplayHandoffError(reason="compile_result_invalid") from error
        if (
            state.version != 2
            or tool_result.status is not RunStatus.SUCCEEDED
            or compile_result.history_authority is None
        ):
            raise System2ReplayHandoffError(reason="compile_not_succeeded")
        try:
            history_authority = ArtifactRef.model_validate(
                compile_result.history_authority.model_dump(mode="python")
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise System2ReplayHandoffError(reason="compile_result_invalid") from error
        return System2CompileTurnResult(
            world_state=state,
            history_authority=history_authority,
            tool_result=tool_result,
        )

    @staticmethod
    def _validate_runtime_config(runtime_config: RuntimeConfig) -> RuntimeConfig:
        if type(runtime_config) is not RuntimeConfig:
            raise System2ReplayHandoffError(reason="runtime_config_invalid")
        values = (
            runtime_config.precheck_steps,
            runtime_config.settle_steps,
            runtime_config.contact_window_steps,
            runtime_config.video_frames,
            runtime_config.fps,
        )
        if any(type(value) is not int for value in values):
            raise System2ReplayHandoffError(reason="runtime_config_invalid")
        try:
            return RuntimeConfig.model_validate(runtime_config.model_dump(mode="python"))
        except (AttributeError, TypeError, ValueError) as error:
            raise System2ReplayHandoffError(reason="runtime_config_invalid") from error

    def _verify_history(self, result: System2CompileTurnResult):
        assert result.history_authority is not None
        try:
            history = verify_planner_history_authority(
                artifact_store=self._artifact_store,
                authority_ref=result.history_authority,
                current_state=result.world_state,
            )
        except (OSError, TypeError, ValueError) as error:
            raise System2ReplayHandoffError(reason="compile_history_mismatch") from error
        return history

    def _verify_receipt(self, result: System2CompileTurnResult):
        try:
            return verify_trusted_tool_receipt(
                self._artifact_store,
                result.tool_result.trusted_receipt,
            )
        except (OSError, TypeError, ValueError) as error:
            raise System2ReplayHandoffError(reason="compile_receipt_invalid") from error

    def _verify_result_bindings(
        self,
        result: System2CompileTurnResult,
        lineage: tuple[TrustedWorldState, ...],
        receipt: TrustedToolReceipt,
    ) -> None:
        tool_result = result.tool_result
        if (
            lineage[-1] != result.world_state
            or receipt.status is not RunStatus.SUCCEEDED
            or receipt.skill.skill_ref != _COMPILE_SKILL_REF
            or receipt.run_state != tool_result.run_state
            or receipt.invocation != tool_result.invocation
            or receipt.status is not tool_result.status
            or receipt.started_at != tool_result.started_at
            or receipt.ended_at != tool_result.ended_at
            or not _canonical_equal(receipt.typed_output, tool_result.typed_output)
        ):
            raise System2ReplayHandoffError(reason="compile_result_mismatch")
        try:
            rebuilt_state = apply_state_delta(
                lineage[-2],
                cast(StateDelta, tool_result.state_delta),
                artifact_store=self._artifact_store,
            )
        except (OSError, TypeError, ValueError) as error:
            raise System2ReplayHandoffError(reason="compile_result_mismatch") from error
        if rebuilt_state != lineage[-1]:
            raise System2ReplayHandoffError(reason="compile_result_mismatch")
        # The verified complete history already re-derives every world-state
        # fact from its user evidence or trusted receipt and applies the entire
        # compile claim set.  The verified receipt likewise rebuilds the typed
        # Invocation, terminal RunState, output, and supporting CAS closure.

    def _publish_json(
        self,
        value: HarnessModel,
        *,
        name: str,
        schema_version: str,
    ) -> ArtifactRef:
        payload = _canonical_json_bytes(value.model_dump(mode="json"))
        self._scratch_root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._scratch_root,
            prefix=".system2-replay-handoff-",
            suffix=".json",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            ref = self._artifact_store.put_file(
                temporary_path,
                name=name,
                media_type="application/json",
                schema_version=schema_version,
            )
        finally:
            temporary_path.unlink(missing_ok=True)
        reread = self._artifact_store.resolve(ref).path.read_bytes()
        if reread != payload:
            raise System2ReplayHandoffError(reason="artifact_write_failed")
        return ref


__all__ = [
    "PreparedSystem2Replay",
    "System2ReplayHandoff",
    "System2ReplayHandoffError",
]
