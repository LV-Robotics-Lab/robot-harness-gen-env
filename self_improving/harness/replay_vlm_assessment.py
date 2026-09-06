"""Auditable, advisory VLM assessment of already-completed replay evidence."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from scene_gen.schema import ResolvedSceneSpec

from .artifacts import ArtifactResolutionError, LocalArtifactStore, ResolvedArtifact
from .event_journal import EventPage, SQLiteEventJournal
from .registry import (
    HandlerResult,
    QualificationCandidate,
    RunContext,
    SkillBlocked,
    SkillRegistry,
    StaticDependencyResolver,
)
from .run_store import SQLiteRunStore
from .schemas import (
    Blocker,
    DependencyRef,
    Invocation,
    ReplayVlmAssessmentInput,
    ReplayVlmAssessmentOutputV2,
    RunState,
    RunStatus,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)
from .schemas.replay_vlm import (
    REPLAY_VLM_ASSESSMENT_INPUT_SCHEMA_ID,
    REPLAY_VLM_ASSESSMENT_OUTPUT_V2_SCHEMA_ID,
    REPLAY_VLM_ASSESSMENT_V2_SCHEMA_VERSION,
)

REPLAY_VLM_SKILL_REF = "text2env.replay_vlm@0.2.0"
REPLAY_VLM_PROMPT_VERSION = "replay_visible_advisory_v1"
REPLAY_VLM_PROMPT = """You are reviewing four time-ordered images from one robot simulation replay.
Use only visible evidence. Do not claim a physical validation pass. Check whether the requested
objects are visible, whether their visible spatial relation matches the task, and whether obvious
floating or penetration is visible. If any required fact is not visible, abstain for that check.
Return only one JSON object with keys: checks, overall, explanation. checks must contain
object_presence, penetration_or_floating, overall_prompt_match; each value must be pass, fail, or
abstain. overall must be pass, fail, or review_required."""

_IMAGE_NAMES = ("observer_start", "observer_mid", "observer_end", "preview_head")


@dataclass(frozen=True, slots=True)
class ReplayVlmProviderRequest:
    assessment_run_id: UUID
    replay_run_id: UUID
    replay_invocation_digest: str
    task_context: str
    resolved_scene_sha256: str
    prompt: str
    prompt_version: str
    prompt_sha256: str
    images: tuple[ResolvedArtifact, ...]


@dataclass(frozen=True, slots=True)
class ReplayVlmProviderAttempt:
    attempt: Literal[1, 2]
    advisory_status: Literal["pass", "fail", "abstain", "format_invalid"]
    prompt_version: str
    prompt_sha256: str
    repair_of_sha256: str | None
    raw_response: bytes
    parsed_response: Mapping[str, Any] | None
    resource_receipt: Mapping[str, Any]
    format_preservation_receipt: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ReplayVlmProviderResult:
    attempts: tuple[ReplayVlmProviderAttempt, ...]
    format_repair_prompt: bytes | None
    provider_receipt: Mapping[str, Any]
    model_receipt: Mapping[str, Any]
    resource_receipt: Mapping[str, Any]
    claims_physical_pass: Literal[False] = False

    def __post_init__(self) -> None:
        if len(self.attempts) not in {1, 2}:
            raise ValueError("provider must return one or two attempts")
        if tuple(item.attempt for item in self.attempts) != tuple(
            range(1, len(self.attempts) + 1)
        ):
            raise ValueError("provider attempt numbers must be consecutive from one")
        first = self.attempts[0]
        if first.repair_of_sha256 is not None:
            raise ValueError("the first provider attempt cannot repair an earlier response")
        if first.format_preservation_receipt is not None:
            raise ValueError("the first provider attempt cannot have a preservation receipt")
        if not first.raw_response:
            raise ValueError("provider attempt raw response must not be empty")
        if len(self.attempts) == 1:
            if self.format_repair_prompt is not None:
                raise ValueError("one provider attempt cannot have a format repair prompt")
        else:
            second = self.attempts[1]
            if first.advisory_status != "format_invalid":
                raise ValueError(
                    "a second provider attempt requires a format-invalid first attempt"
                )
            if not second.raw_response:
                raise ValueError("provider attempt raw response must not be empty")
            if not self.format_repair_prompt:
                raise ValueError("two provider attempts require one format repair prompt")
            first_sha256 = hashlib.sha256(first.raw_response).hexdigest()
            if second.repair_of_sha256 != first_sha256:
                raise ValueError("format repair is not bound to the first raw response")
            prompt_sha256 = hashlib.sha256(self.format_repair_prompt).hexdigest()
            if second.prompt_sha256 != prompt_sha256:
                raise ValueError("format repair prompt digest does not match its bytes")
            preservation = second.format_preservation_receipt
            if (
                not isinstance(preservation, Mapping)
                or type(preservation.get("preserved")) is not bool
            ):
                raise ValueError("format repair requires a preservation receipt")
            if not preservation["preserved"] and (
                second.advisory_status != "format_invalid" or second.parsed_response is not None
            ):
                raise ValueError("semantic drift must fail closed as format_invalid")
        if self.resource_receipt.get("visible_vlm_invocations") != len(self.attempts):
            raise ValueError("aggregate VLM invocation count does not match attempts")
        if self.resource_receipt.get("network_calls") != 0:
            raise ValueError("replay VLM provider must remain offline")

    @property
    def advisory_status(self) -> Literal["pass", "fail", "abstain", "format_invalid"]:
        return self.attempts[-1].advisory_status

    @property
    def raw_response(self) -> bytes:
        return self.attempts[-1].raw_response

    @property
    def parsed_response(self) -> Mapping[str, Any] | None:
        return self.attempts[-1].parsed_response


class ReplayVlmProvider(Protocol):
    dependencies: tuple[DependencyRef, ...]

    def assess(self, request: ReplayVlmProviderRequest) -> ReplayVlmProviderResult: ...


class ReplayVlmAssessmentApplication:
    """Run one unqualified assessment candidate through the normal audit kernel."""

    durable_run_state = True

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        event_journal: SQLiteEventJournal,
        run_store: SQLiteRunStore,
        provider: ReplayVlmProvider,
        clock: Callable[[], datetime] | None = None,
        run_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if not isinstance(artifact_store, LocalArtifactStore):
            raise TypeError("artifact_store must be LocalArtifactStore")
        if not isinstance(event_journal, SQLiteEventJournal):
            raise TypeError("event_journal must be SQLiteEventJournal")
        if not isinstance(run_store, SQLiteRunStore):
            raise TypeError("run_store must be SQLiteRunStore")
        dependencies = tuple(provider.dependencies)
        if not dependencies:
            raise ValueError("provider dependencies must not be empty")
        if len({item.name for item in dependencies}) != len(dependencies):
            raise ValueError("provider dependency names must be unique")
        self._artifact_store = artifact_store
        self._event_journal = event_journal
        self._run_store = run_store
        self._provider = provider
        self._candidate = QualificationCandidate(
            skill_id="text2env.replay_vlm",
            version="0.2.0",
            input_schema=REPLAY_VLM_ASSESSMENT_INPUT_SCHEMA_ID,
            output_schema=REPLAY_VLM_ASSESSMENT_OUTPUT_V2_SCHEMA_ID,
            max_attempts=1,
        )
        self._registry = SkillRegistry(
            artifact_resolver=artifact_store,
            dependency_resolver=StaticDependencyResolver(
                {REPLAY_VLM_SKILL_REF: tuple(sorted(dependencies, key=lambda item: item.name))}
            ),
            event_sink=event_journal,
            clock=clock or (lambda: datetime.now(timezone.utc)),
            run_id_factory=run_id_factory,
            run_store=run_store,
        )

    @property
    def artifact_root(self) -> Path:
        return self._artifact_store.root

    @property
    def journal_path(self) -> Path:
        return self._event_journal.path.resolve()

    def assess(self, value: ReplayVlmAssessmentInput) -> RunState:
        if not isinstance(value, ReplayVlmAssessmentInput):
            raise TypeError("assess requires ReplayVlmAssessmentInput")
        result = self._registry.evaluate_candidate(
            self._candidate,
            self._handle,
            value.model_dump(mode="json"),
        )
        return result.state

    def events(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> EventPage:
        return self._event_journal.read(
            after_event_id=after_event_id,
            run_id=run_id,
            limit=limit,
        )

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self._run_store.read_invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return self._run_store.read_run_state(run_id)

    def _handle(
        self,
        value: ReplayVlmAssessmentInput,
        context: RunContext,
    ) -> HandlerResult:
        replay_run_id = UUID(value.replay_run_id)
        source_invocation, source_state, replay_input, replay_output = self._load_replay(
            replay_run_id
        )
        resolved_scene, resolved_scene_artifact = self._load_resolved_scene(replay_input)
        runtime = self._artifact_store.resolve(replay_output.runtime_evidence)
        runtime_document = _json_object(runtime.path, "runtime evidence")
        if runtime_document.get("resolved_scene_sha256") != resolved_scene.digest():
            raise _blocked(
                "VLM_REPLAY_BINDING_INVALID",
                "runtime evidence is not bound to the replay resolved scene",
                stage="vlm.replay_evidence.bound",
            )
        images = self._select_images(replay_output, source_state)
        context.emit(
            "vlm.replay_evidence.bound",
            artifact_refs=(replay_output.runtime_evidence, *(item.ref for item in images)),
        )
        prompt_sha256 = hashlib.sha256(REPLAY_VLM_PROMPT.encode("utf-8")).hexdigest()
        request = ReplayVlmProviderRequest(
            assessment_run_id=context.run_id,
            replay_run_id=replay_run_id,
            replay_invocation_digest=source_invocation.invocation_digest,
            task_context=resolved_scene.request,
            resolved_scene_sha256=resolved_scene.digest(),
            prompt=REPLAY_VLM_PROMPT,
            prompt_version=REPLAY_VLM_PROMPT_VERSION,
            prompt_sha256=prompt_sha256,
            images=images,
        )
        context.emit("vlm.inference.started")
        result = self._provider.assess(request)
        if not isinstance(result, ReplayVlmProviderResult):
            raise TypeError("provider must return ReplayVlmProviderResult")
        if result.claims_physical_pass is not False:
            raise _blocked(
                "VLM_PHYSICAL_AUTHORITY_VIOLATION",
                "VLM provider attempted to claim physical validation authority",
                stage="vlm.inference",
            )
        first = result.attempts[0]
        if (
            first.prompt_version != request.prompt_version
            or first.prompt_sha256 != request.prompt_sha256
        ):
            raise ValueError("provider first attempt does not match the bound prompt")
        raw_artifacts = tuple(
            _put_bytes(
                self._artifact_store,
                attempt.raw_response,
                name=f"replay_vlm_raw_response_attempt_{attempt.attempt}",
                media_type="text/plain",
                schema_version=None,
            )
            for attempt in result.attempts
        )
        repair_prompt = (
            _put_bytes(
                self._artifact_store,
                result.format_repair_prompt,
                name="replay_vlm_format_repair_prompt",
                media_type="text/plain",
                schema_version="harness.replay_vlm_format_repair_prompt.v1",
            )
            if result.format_repair_prompt is not None
            else None
        )
        attempts = []
        for attempt, raw in zip(result.attempts, raw_artifacts, strict=True):
            attempts.append(
                {
                    "attempt": attempt.attempt,
                    "advisory_status": attempt.advisory_status,
                    "prompt_version": attempt.prompt_version,
                    "prompt_sha256": attempt.prompt_sha256,
                    "repair_of_sha256": attempt.repair_of_sha256,
                    "raw_response": raw.model_dump(mode="json"),
                    "parsed_response": (
                        _json_copy(attempt.parsed_response, "parsed_response")
                        if attempt.parsed_response is not None
                        else None
                    ),
                    "resource_receipt": _json_copy(
                        attempt.resource_receipt, "attempt resource_receipt"
                    ),
                    "format_preservation_receipt": (
                        _json_copy(
                            attempt.format_preservation_receipt,
                            "format_preservation_receipt",
                        )
                        if attempt.format_preservation_receipt is not None
                        else None
                    ),
                }
            )
        receipt = {
            "schema_version": REPLAY_VLM_ASSESSMENT_V2_SCHEMA_VERSION,
            "assessment_run_id": str(context.run_id),
            "source_replay": {
                "run_id": value.replay_run_id,
                "invocation_digest": source_invocation.invocation_digest,
                "resolved_scene_sha256": resolved_scene.digest(),
                "runtime_evidence": replay_output.runtime_evidence.model_dump(mode="json"),
            },
            "resolved_scene_artifact": resolved_scene_artifact.ref.model_dump(mode="json"),
            "images": [item.ref.model_dump(mode="json") for item in images],
            "prompt": {
                "version": REPLAY_VLM_PROMPT_VERSION,
                "sha256": prompt_sha256,
            },
            "dependencies": [item.model_dump(mode="json") for item in context.dependencies],
            "provider_receipt": _json_copy(result.provider_receipt, "provider_receipt"),
            "model_receipt": _json_copy(result.model_receipt, "model_receipt"),
            "resource_receipt": _json_copy(result.resource_receipt, "resource_receipt"),
            "attempts": attempts,
            "format_repair_prompt": (
                repair_prompt.model_dump(mode="json") if repair_prompt is not None else None
            ),
            "advisory_status": result.advisory_status,
            "physical_gate_authority": "deterministic_runtime_only",
            "claims_physical_pass": False,
        }
        assessment = _put_bytes(
            self._artifact_store,
            _canonical_json_bytes(receipt),
            name="replay_vlm_assessment",
            media_type="application/json",
            schema_version=REPLAY_VLM_ASSESSMENT_V2_SCHEMA_VERSION,
        )
        trailing_artifacts = raw_artifacts + ((repair_prompt,) if repair_prompt is not None else ())
        artifacts = (assessment, *trailing_artifacts)
        if len(result.attempts) == 2:
            context.emit("vlm.format_fallback.completed", artifact_refs=trailing_artifacts)
        context.emit("vlm.assessment.published", artifact_refs=artifacts)
        return HandlerResult(
            output=ReplayVlmAssessmentOutputV2(
                replay_run_id=value.replay_run_id,
                assessment=assessment,
                advisory_status=result.advisory_status,
                claims_physical_pass=False,
            ),
            artifacts=artifacts,
        )

    def _load_replay(
        self,
        replay_run_id: UUID,
    ) -> tuple[Invocation, RunState, Text2EnvReplayInput, Text2EnvReplayOutput]:
        invocation = self._run_store.read_invocation(replay_run_id)
        state = self._run_store.read_run_state(replay_run_id)
        if invocation is None or state is None:
            raise _blocked(
                "VLM_REPLAY_NOT_FOUND",
                "replay invocation and terminal state are required",
                stage="vlm.replay_evidence.bound",
            )
        if (
            invocation.skill_id != "text2env.replay"
            or invocation.skill_version != "1.0.0"
            or state.skill_id != invocation.skill_id
            or state.skill_version != invocation.skill_version
            or state.invocation_digest != invocation.invocation_digest
            or state.status is not RunStatus.SUCCEEDED
            or state.output is None
        ):
            raise _blocked(
                "VLM_REPLAY_NOT_ELIGIBLE",
                "source must be one successful text2env.replay@1.0.0 run",
                stage="vlm.replay_evidence.bound",
            )
        return (
            invocation,
            state,
            Text2EnvReplayInput.model_validate(invocation.effective_parameters),
            Text2EnvReplayOutput.model_validate(state.output),
        )

    def _load_resolved_scene(
        self,
        replay_input: Text2EnvReplayInput,
    ) -> tuple[ResolvedSceneSpec, ResolvedArtifact]:
        manifest_ref = replay_input.environment_package.package_manifest
        manifest_path = self._artifact_store.resolve(manifest_ref).path
        manifest = _json_object(manifest_path, "package manifest")
        records = manifest.get("files")
        if not isinstance(records, list):
            raise _blocked(
                "VLM_PACKAGE_INVALID",
                "package manifest has no member list",
                stage="vlm.replay_evidence.bound",
            )
        record = next(
            (
                item
                for item in records
                if isinstance(item, dict) and item.get("path") == "resolved_scene.json"
            ),
            None,
        )
        if record is None:
            raise _blocked(
                "VLM_PACKAGE_INVALID",
                "package manifest has no resolved_scene.json member",
                stage="vlm.replay_evidence.bound",
            )
        try:
            ref = self._artifact_ref_from_member(record)
            resolved_artifact = self._artifact_store.resolve(ref)
            resolved_scene = ResolvedSceneSpec.model_validate_json(
                resolved_artifact.path.read_bytes()
            )
        except Exception as error:
            raise _blocked(
                "VLM_PACKAGE_INVALID",
                f"resolved scene member cannot be verified: {error}",
                stage="vlm.replay_evidence.bound",
            ) from error
        if resolved_scene.digest() != replay_input.environment_package.resolved_scene_sha256:
            raise _blocked(
                "VLM_REPLAY_BINDING_INVALID",
                "resolved scene does not match EnvironmentPackage identity",
                stage="vlm.replay_evidence.bound",
            )
        return resolved_scene, resolved_artifact

    @staticmethod
    def _artifact_ref_from_member(record: Mapping[str, Any]):
        sha256 = record.get("sha256")
        size = record.get("bytes")
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise ValueError("invalid resolved scene member identity")
        from .schemas import ArtifactRef

        return ArtifactRef(
            name="resolved_scene",
            uri=f"artifact://sha256/{sha256}",
            media_type="application/json",
            sha256=sha256,
            bytes=size,
            schema_version="robotwin.resolved_scene.v1",
        )

    def _select_images(
        self,
        replay_output: Text2EnvReplayOutput,
        source_state: RunState,
    ) -> tuple[ResolvedArtifact, ...]:
        artifacts_by_name: dict[str, Any] = {}
        for ref in replay_output.replay_artifacts:
            if ref.name in artifacts_by_name:
                raise _blocked(
                    "VLM_REPLAY_MEDIA_INVALID",
                    f"duplicate replay artifact name: {ref.name}",
                    stage="vlm.replay_evidence.bound",
                )
            artifacts_by_name[ref.name] = ref
        state_refs = set(source_state.artifacts)
        selected: list[ResolvedArtifact] = []
        for name in _IMAGE_NAMES:
            ref = artifacts_by_name.get(name)
            if ref is None or ref.media_type not in {"image/png", "image/jpeg"}:
                raise _blocked(
                    "VLM_REPLAY_MEDIA_INVALID",
                    f"required verified replay image is unavailable: {name}",
                    stage="vlm.replay_evidence.bound",
                )
            if ref not in state_refs:
                raise _blocked(
                    "VLM_REPLAY_BINDING_INVALID",
                    f"replay output image is absent from terminal artifacts: {name}",
                    stage="vlm.replay_evidence.bound",
                )
            try:
                selected.append(self._artifact_store.resolve(ref))
            except ArtifactResolutionError as error:
                raise _blocked(
                    "VLM_REPLAY_MEDIA_INVALID",
                    f"required replay image failed CAS verification: {name}: {error}",
                    stage="vlm.replay_evidence.bound",
                ) from error
        return tuple(selected)


def _blocked(code: str, message: str, *, stage: str) -> SkillBlocked:
    return SkillBlocked(
        Blocker(
            code=code,
            message=message,
            stage=stage,
            retryable=False,
            details={},
            unknowns=(),
            artifact_refs=(),
        )
    )


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be one JSON object")
    return value


def _json_copy(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    try:
        copied = json.loads(json.dumps(dict(value), allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must contain only finite JSON values") from error
    if not isinstance(copied, dict):
        raise ValueError(f"{label} must be one JSON object")
    return copied


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _put_bytes(
    store: LocalArtifactStore,
    payload: bytes,
    *,
    name: str,
    media_type: str,
    schema_version: str | None,
):
    if not isinstance(payload, bytes):
        raise TypeError("artifact payload must be bytes")
    store.root.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary = tempfile.mkstemp(prefix=".replay-vlm-", dir=store.root)
    path = Path(temporary)
    try:
        with open(file_descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
        return store.put_file(
            path,
            name=name,
            media_type=media_type,
            schema_version=schema_version,
        )
    finally:
        path.unlink(missing_ok=True)


__all__ = [
    "REPLAY_VLM_PROMPT",
    "REPLAY_VLM_PROMPT_VERSION",
    "REPLAY_VLM_SKILL_REF",
    "ReplayVlmAssessmentApplication",
    "ReplayVlmProvider",
    "ReplayVlmProviderAttempt",
    "ReplayVlmProviderRequest",
    "ReplayVlmProviderResult",
]
