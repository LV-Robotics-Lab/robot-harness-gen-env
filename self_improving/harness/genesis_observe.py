"""Controlled candidate application for the ``genesis.observe`` contract.

This module deliberately does not register or qualify a production Skill.  It runs
the exact schemas through the shared candidate execution kernel while publishing a
real CAS/media/receipt closure marked ``simulator_executed=false``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID

from PIL import Image
from pydantic import JsonValue, TypeAdapter, ValidationError

from .artifacts import LocalArtifactStore
from .event_journal import SQLiteEventJournal
from .registry import (
    ControlledRunIdentityError,
    ControlledRunPendingError,
    HandlerResult,
    QualificationCandidate,
    QualificationCandidateEvaluation,
    RunContext,
    SkillBlocked,
    SkillRegistry,
    StaticDependencyResolver,
)
from .run_store import SQLiteRunStore
from .schemas.common import (
    ArtifactRef,
    Blocker,
    DependencyRef,
    Invocation,
    RegisteredSkillDescriptor,
    RunState,
    SkillDescriptor,
)
from .schemas.genesis_observe import (
    GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID,
    GENESIS_OBSERVE_INPUT_SCHEMA_ID,
    GENESIS_OBSERVE_OUTPUT_SCHEMA_ID,
    OBSERVATION_MEDIA_VERIFICATION_SCHEMA,
    GenesisObservationReceipt,
    GenesisObservationValue,
    GenesisObserveInput,
    GenesisObserveOutput,
)

GENESIS_OBSERVE_SKILL_REF = "genesis.observe@1.0.0"
GENESIS_OBSERVE_MCP_TOOL_NAME = "genesis_observe_v1_0_0"
_JSON_VALUE = TypeAdapter(JsonValue)


@dataclass(frozen=True, slots=True)
class ControlledImageCapture:
    """One byte-exact still returned by a controlled capture adapter."""

    camera_id: str
    payload: bytes
    media_type: str = "image/png"

    def __post_init__(self) -> None:
        if not self.camera_id or self.camera_id != self.camera_id.lower():
            raise ValueError("controlled camera_id must be a non-empty lowercase name")
        if type(self.payload) is not bytes or not self.payload:
            raise TypeError("controlled image payload must be non-empty exact bytes")
        if self.media_type != "image/png":
            raise ValueError("controlled image media_type must be image/png")


@dataclass(frozen=True, slots=True)
class ControlledObservationValue:
    """One explicitly supplied fact value; it is never inferred from image bytes."""

    key: str
    value: JsonValue

    def __post_init__(self) -> None:
        if type(self.key) is not str or not self.key:
            raise TypeError("controlled observation key must be a non-empty exact string")
        try:
            _JSON_VALUE.validate_python(self.value, strict=True)
        except ValidationError as error:
            raise TypeError("controlled observation value must be strict JSON") from error


@dataclass(frozen=True, slots=True)
class ControlledObservationCapture:
    """Controlled stills and caller-specified values captured at one instant."""

    captured_at: datetime
    images: tuple[ControlledImageCapture, ...]
    observations: tuple[ControlledObservationValue, ...]

    def __post_init__(self) -> None:
        _require_utc(self.captured_at, label="controlled captured_at")
        camera_ids = tuple(item.camera_id for item in self.images)
        if not camera_ids:
            raise ValueError("controlled capture must contain at least one image")
        if camera_ids != tuple(sorted(camera_ids)) or len(camera_ids) != len(set(camera_ids)):
            raise ValueError("controlled images must be sorted and unique by camera_id")
        keys = tuple(item.key for item in self.observations)
        if not keys:
            raise ValueError("controlled capture must contain at least one observation")
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("controlled observations must be sorted and unique by key")


@runtime_checkable
class ControlledObservationCaptureAdapter(Protocol):
    """Internal seam supplying controlled bytes and explicit fact values."""

    identity: DependencyRef

    def capture(
        self,
        request: GenesisObserveInput,
        *,
        captured_at: datetime,
    ) -> ControlledObservationCapture: ...


@dataclass(frozen=True, slots=True)
class ControlledObserveApplicationSettings:
    """Durable state and shared evidence CAS used by the candidate application."""

    state_root: Path
    evidence_artifact_root: Path


class ControlledObserveApplicationRunIdentityError(RuntimeError):
    """A caller-selected controlled child identity conflicts with durable state."""


class ControlledObserveApplicationRunPendingError(RuntimeError):
    """A controlled child was reserved but has no recoverable terminal state yet."""


class ControlledGenesisObserveApplication:
    """Unqualified candidate facade using the normal Registry execution kernel."""

    durable_run_state = True

    def __init__(
        self,
        *,
        state_root: Path,
        artifact_store: LocalArtifactStore,
        registry: SkillRegistry,
        run_store: SQLiteRunStore,
        capture_adapter: ControlledObservationCaptureAdapter,
        clock: Callable[[], datetime],
    ) -> None:
        self._state_root = state_root
        self._artifact_store = artifact_store
        self._registry = registry
        self._run_store = run_store
        self._capture_adapter = capture_adapter
        self._clock = clock
        self._candidate = QualificationCandidate(
            skill_id="genesis.observe",
            version="1.0.0",
            input_schema=GENESIS_OBSERVE_INPUT_SCHEMA_ID,
            output_schema=GENESIS_OBSERVE_OUTPUT_SCHEMA_ID,
            max_attempts=1,
        )

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]:
        """Expose only an explicitly bound controlled qualification, if present."""

        return self._registry.list()  # type: ignore[return-value]

    @property
    def artifact_root(self) -> Path:
        return self._artifact_store.root

    @property
    def authority_path(self) -> Path:
        return self._run_store.path.resolve()

    def evaluate(self, parameters: GenesisObserveInput) -> QualificationCandidateEvaluation:
        """Exercise the exact candidate without registering production authority."""

        if self.skills:
            raise RuntimeError("a Golden-bound controlled application is not a candidate evaluator")
        if type(parameters) is not GenesisObserveInput:
            raise TypeError("controlled observe requires exact GenesisObserveInput")
        trusted = GenesisObserveInput.model_validate(parameters.model_dump(mode="python"))
        return self._registry.evaluate_candidate(
            self._candidate,
            self._capture,
            trusted.model_dump(mode="json"),
        )

    def invoke_typed(self, parameters: GenesisObserveInput, *, run_id: UUID) -> RunState:
        """Execute or recover one explicitly qualified controlled Golden child."""

        if type(parameters) is not GenesisObserveInput:
            raise TypeError("controlled observe requires exact GenesisObserveInput")
        if len(self.skills) != 1:
            raise RuntimeError("controlled observe has no Golden-bound qualification")
        try:
            return self._registry.invoke_with_run_id(
                "genesis.observe",
                "1.0.0",
                parameters.model_dump(mode="json"),
                run_id=run_id,
            )
        except ControlledRunIdentityError as error:
            raise ControlledObserveApplicationRunIdentityError(str(error)) from error
        except ControlledRunPendingError as error:
            raise ControlledObserveApplicationRunPendingError(str(error)) from error

    def _bind_controlled_descriptor(self, descriptor: SkillDescriptor) -> None:
        """Install one test-controlled qualification without entering production assembly."""

        if (
            descriptor.skill_id != "genesis.observe"
            or descriptor.version != "1.0.0"
            or descriptor.input_schema != GENESIS_OBSERVE_INPUT_SCHEMA_ID
            or descriptor.output_schema != GENESIS_OBSERVE_OUTPUT_SCHEMA_ID
            or descriptor.mcp_tool_name != GENESIS_OBSERVE_MCP_TOOL_NAME
        ):
            raise ValueError(
                "controlled qualification does not describe exact genesis.observe@1.0.0"
            )
        current = self.skills
        if current:
            if current != (descriptor,):
                raise ValueError("controlled observe is already bound to another qualification")
            return
        self._registry.register(descriptor, self._capture)

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self._run_store.read_invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return self._run_store.read_run_state(run_id)

    def resolve_artifact(self, artifact: ArtifactRef) -> Path:
        return self._artifact_store.resolve(artifact).path

    def _capture(self, value: GenesisObserveInput, context: RunContext) -> HandlerResult:
        if value.capture_plan.video_frames:
            raise SkillBlocked(
                _blocker(
                    code="HARN_OBSERVATION_CAPTURE_UNSUPPORTED",
                    message="controlled observation candidate supports still capture only",
                    details={"video_frames": value.capture_plan.video_frames},
                )
            )
        captured_at = self._clock()
        capture = self._capture_adapter.capture(value, captured_at=captured_at)
        if capture.captured_at != captured_at:
            raise SkillBlocked(
                _blocker(
                    code="HARN_OBSERVATION_UNBOUND",
                    message="controlled capture timestamp differs from its invocation boundary",
                    details={},
                )
            )
        camera_ids = tuple(item.camera_id for item in capture.images)
        if camera_ids != value.capture_plan.camera_ids:
            raise SkillBlocked(
                _blocker(
                    code="HARN_OBSERVATION_UNBOUND",
                    message="controlled capture cameras differ from the requested capture plan",
                    details={"camera_ids": list(camera_ids)},
                )
            )
        observation_keys = tuple(item.key for item in capture.observations)
        if observation_keys != value.requested_observation_keys:
            raise SkillBlocked(
                _blocker(
                    code="HARN_OBSERVATION_UNBOUND",
                    message="controlled observation keys differ from the exact request",
                    details={"observation_keys": list(observation_keys)},
                )
            )

        attempt_root = self._state_root / "work" / str(context.run_id)
        attempt_root.mkdir(parents=True, exist_ok=False)
        images, probes = self._publish_images(capture.images, attempt_root)
        verification = self._publish_json(
            attempt_root / "media-verification.json",
            {
                "schema_version": OBSERVATION_MEDIA_VERIFICATION_SCHEMA,
                "simulator_executed": False,
                "fully_decoded": True,
                "images": probes,
                "video": None,
            },
            name="observation_media_verification.json",
            schema_version=OBSERVATION_MEDIA_VERIFICATION_SCHEMA,
        )
        context.emit(
            "observation.media.captured",
            artifact_refs=(*images, verification),
        )
        valid_until = captured_at + timedelta(seconds=value.ttl_seconds)
        observations = tuple(
            GenesisObservationValue(
                key=item.key,
                value=item.value,
                captured_at=captured_at,
                valid_until=valid_until,
                payload_refs=images,
            )
            for item in capture.observations
        )
        receipt = GenesisObservationReceipt(
            schema_version=GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID,
            observation_child_run_id=context.run_id,
            environment_package=value.environment_package,
            replay_workflow_receipt=value.replay_workflow_receipt,
            replay_runtime_receipt=value.replay_runtime_receipt,
            checkpoint=value.checkpoint,
            capture_plan=value.capture_plan,
            requested_observation_keys=value.requested_observation_keys,
            captured_at=captured_at,
            valid_until=valid_until,
            ttl_seconds=value.ttl_seconds,
            images=images,
            video=None,
            media_verification=verification,
            observations=observations,
            simulator_executed=False,
            post_restore_steps_executed=0,
        )
        receipt_ref = self._publish_json(
            attempt_root / "observation-receipt.json",
            receipt.model_dump(mode="json"),
            name="genesis_observation_receipt.json",
            schema_version=GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID,
        )
        output = GenesisObserveOutput(
            schema_version=GENESIS_OBSERVE_OUTPUT_SCHEMA_ID,
            observation_receipt=receipt_ref,
            images=images,
            video=None,
            media_verification=verification,
            captured_at=captured_at,
            valid_until=valid_until,
            simulator_executed=False,
        )
        return HandlerResult(output=output, artifacts=(receipt_ref, verification, *images))

    def _publish_images(
        self,
        captured: tuple[ControlledImageCapture, ...],
        root: Path,
    ) -> tuple[tuple[ArtifactRef, ...], list[dict[str, object]]]:
        refs: list[ArtifactRef] = []
        probes: list[dict[str, object]] = []
        for item in captured:
            try:
                with Image.open(BytesIO(item.payload)) as image:
                    image.verify()
                with Image.open(BytesIO(item.payload)) as image:
                    width, height = image.size
                    mode = image.mode
            except Exception as error:
                raise SkillBlocked(
                    _blocker(
                        code="HARN_OBSERVATION_UNBOUND",
                        message="controlled capture image did not decode completely",
                        details={"camera_id": item.camera_id, "error": str(error)},
                    )
                ) from error
            source = root / f"{item.camera_id}.png"
            source.write_bytes(item.payload)
            ref = self._artifact_store.put_file(
                source,
                name=f"observation_{item.camera_id}.png",
                media_type=item.media_type,
                schema_version=None,
            )
            refs.append(ref)
            probes.append(
                {
                    "camera_id": item.camera_id,
                    "image": ref.model_dump(mode="json"),
                    "width": width,
                    "height": height,
                    "mode": mode,
                }
            )
        return tuple(refs), probes

    def _publish_json(
        self,
        path: Path,
        value: object,
        *,
        name: str,
        schema_version: str,
    ) -> ArtifactRef:
        path.write_bytes(_canonical_json_bytes(value))
        return self._artifact_store.put_file(
            path,
            name=name,
            media_type="application/json",
            schema_version=schema_version,
        )


def create_controlled_genesis_observe_application(
    settings: ControlledObserveApplicationSettings,
    *,
    capture_adapter: ControlledObservationCaptureAdapter,
    clock: Callable[[], datetime],
    run_id_factory: Callable[[], UUID],
) -> ControlledGenesisObserveApplication:
    """Create an explicitly unqualified candidate over durable shared seams."""

    if type(settings) is not ControlledObserveApplicationSettings:
        raise TypeError("settings must be exact ControlledObserveApplicationSettings")
    if not isinstance(capture_adapter, ControlledObservationCaptureAdapter):
        raise TypeError("capture_adapter must satisfy ControlledObservationCaptureAdapter")
    if type(capture_adapter.identity) is not DependencyRef:
        raise TypeError("capture_adapter.identity must be an exact DependencyRef")
    if not callable(clock) or not callable(run_id_factory):
        raise TypeError("clock and run_id_factory must be callable")
    state_root = Path(settings.state_root).expanduser().resolve()
    evidence_root = Path(settings.evidence_artifact_root).expanduser().resolve()
    for root, label in ((state_root, "state_root"), (evidence_root, "evidence_artifact_root")):
        if root.exists() and (not root.is_dir() or root.is_symlink()):
            raise ValueError(f"{label} must be a non-symlink directory")
        root.mkdir(parents=True, exist_ok=True)
    artifact_store = LocalArtifactStore(evidence_root)
    database = state_root / "harness.sqlite3"
    events = SQLiteEventJournal(database)
    runs = SQLiteRunStore(database)
    registry = SkillRegistry(
        artifact_resolver=artifact_store,
        dependency_resolver=StaticDependencyResolver(
            dependencies=({GENESIS_OBSERVE_SKILL_REF: (capture_adapter.identity,)})
        ),
        event_sink=events,
        clock=clock,
        run_id_factory=run_id_factory,
        run_store=runs,
    )
    return ControlledGenesisObserveApplication(
        state_root=state_root,
        artifact_store=artifact_store,
        registry=registry,
        run_store=runs,
        capture_adapter=capture_adapter,
        clock=clock,
    )


def _blocker(*, code: str, message: str, details: dict[str, JsonValue]) -> Blocker:
    return Blocker(
        code=code,
        message=message,
        stage="observation.capture",
        retryable=False,
        details=details,
        unknowns=(),
        artifact_refs=(),
    )


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


def _require_utc(value: datetime, *, label: str) -> None:
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{label} must use UTC")


__all__ = [
    "GENESIS_OBSERVE_MCP_TOOL_NAME",
    "GENESIS_OBSERVE_SKILL_REF",
    "ControlledGenesisObserveApplication",
    "ControlledImageCapture",
    "ControlledObservationCapture",
    "ControlledObservationCaptureAdapter",
    "ControlledObservationValue",
    "ControlledObserveApplicationSettings",
    "ControlledObserveApplicationRunIdentityError",
    "ControlledObserveApplicationRunPendingError",
    "create_controlled_genesis_observe_application",
]
