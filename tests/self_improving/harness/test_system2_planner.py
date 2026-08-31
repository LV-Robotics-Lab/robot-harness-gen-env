from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas import ArtifactRef
from self_improving.harness.system2.context import (
    ContextBudget,
    PlannerSkillCard,
    compile_planner_context,
)
from self_improving.harness.system2.domain import WorldFact, build_world_state
from self_improving.harness.system2.planner import (
    LocalPlannerArtifactPublisher,
    PlannerExecutionError,
    PlannerExecutionReceipt,
    PlannerProgressEvent,
    PlannerProviderIdentity,
    PlannerProviderResponse,
    PlannerUsage,
    System2Planner,
    provider_identity_sha256,
)

_T0 = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)


def _artifact(name: str, digest: str, schema: str) -> ArtifactRef:
    return ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{digest}",
        media_type="application/json",
        sha256=digest,
        bytes=10,
        schema_version=schema,
    )


def _context():
    objective = WorldFact(
        key="task.objective",
        value="put a can on a plate",
        source_kind="user_input",
        source_artifact=_artifact("request", "1" * 64, "harness.user_input.v1"),
        observed_at=_T0,
        valid_until=None,
    )
    state = build_world_state((objective,), as_of=_T0)
    context = compile_planner_context(
        state=state,
        skills=(
            PlannerSkillCard(
                skill_ref="text2env.compile@1.0.0",
                purpose="Compile a typed environment package.",
                input_schema="harness.text2env_compile_input.v1",
                output_schema="harness.text2env_compile_output.v1",
                qualification_sha256="2" * 64,
                max_attempts=1,
            ),
        ),
        history=(),
        required_fact_keys=(),
        budget=ContextBudget(max_facts=2, max_history=0, max_context_bytes=16_000),
    )
    return state, context


def _identity(*, revision: str = "3" * 64) -> PlannerProviderIdentity:
    return PlannerProviderIdentity(
        provider_id="qwen_local",
        model_id="Qwen/Qwen2.5-VL-3B-Instruct",
        model_revision=revision,
        model_snapshot_sha256="6" * 64,
        implementation_sha256="4" * 64,
        inference={"do_sample": False, "max_new_tokens": 512},
        network_access=False,
    )


def _decision_bytes(context) -> bytes:
    return (
        json.dumps(
            {
                "schema_version": "harness.planner_decision.v1",
                "base_state_sha256": context.world_state_sha256,
                "context_sha256": context.context_sha256,
                "action": "invoke_skill",
                "skill_ref": "text2env.compile@1.0.0",
                "parameters": {
                    "request": "put a can on a plate",
                    "seed": 7,
                    "asset_catalog": _artifact(
                        "asset_catalog",
                        "a" * 64,
                        "robotwin.asset_catalog.v1",
                    ).model_dump(mode="json"),
                    "config": {"generate_missing_assets": False},
                },
                "observation_keys": [],
                "stop_reason": None,
                "summary": "Compile before replay.",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


@dataclass
class RecordingPublisher:
    payloads: dict[str, bytes] = field(default_factory=dict)
    refs: list[ArtifactRef] = field(default_factory=list)
    fail_on: str | None = None

    def publish(
        self,
        payload: bytes,
        *,
        name: str,
        media_type: str,
        schema_version: str,
    ) -> ArtifactRef:
        if self.fail_on == name:
            raise OSError("injected storage failure")
        digest = hashlib.sha256(payload).hexdigest()
        ref = ArtifactRef(
            name=name,
            uri=f"artifact://sha256/{digest}",
            media_type=media_type,
            sha256=digest,
            bytes=len(payload),
            schema_version=schema_version,
        )
        self.payloads[digest] = payload
        self.refs.append(ref)
        return ref


@dataclass
class FakeProvider:
    raw: bytes
    identity_value: PlannerProviderIdentity = field(default_factory=_identity)
    error: Exception | None = None
    drift: bool = False
    calls: int = 0
    prompts: list[bytes] = field(default_factory=list)
    identity_reads: int = 0
    progress_stage: object = "loading"
    response_override: object | None = None

    @property
    def identity(self) -> PlannerProviderIdentity:
        self.identity_reads += 1
        if self.drift and self.identity_reads > 1:
            return _identity(revision="5" * 64)
        return self.identity_value

    def invoke(
        self,
        prompt: bytes,
        progress: Callable[[str], None],
    ) -> PlannerProviderResponse:
        self.calls += 1
        self.prompts.append(prompt)
        progress(cast(str, self.progress_stage))
        if self.error is not None:
            raise self.error
        if self.response_override is not None:
            return cast(PlannerProviderResponse, self.response_override)
        return PlannerProviderResponse(
            raw=self.raw,
            usage=PlannerUsage(
                input_tokens=400,
                output_tokens=80,
                wall_time_ms=25,
                peak_vram_bytes=7_000_000_000,
                finish_reason="stop",
            ),
        )


class IncrementingClock:
    def __init__(self) -> None:
        self.current = _T0

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(milliseconds=1)
        return value


def _planner(
    provider: FakeProvider,
    publisher: RecordingPublisher,
    *,
    progress: Callable[[PlannerProgressEvent], None] | None = None,
) -> System2Planner:
    return System2Planner(
        provider=provider,
        publisher=publisher,
        progress=progress,
        clock=IncrementingClock(),
        uuid_factory=lambda: UUID("00000000-0000-0000-0000-000000000123"),
        max_response_bytes=65_536,
    )


def _receipt(publisher: RecordingPublisher, ref: ArtifactRef) -> PlannerExecutionReceipt:
    return PlannerExecutionReceipt.model_validate_json(publisher.payloads[ref.sha256])


def _successful_receipt_payload() -> dict[str, Any]:
    _, context = _context()
    publisher = RecordingPublisher()
    execution = _planner(FakeProvider(_decision_bytes(context)), publisher).plan(context)
    return _receipt(publisher, execution.receipt_ref).model_dump(mode="python")


def test_planner_publishes_prompt_raw_decision_receipt_and_real_progress() -> None:
    _, context = _context()
    provider = FakeProvider(_decision_bytes(context))
    publisher = RecordingPublisher()
    observed: list[PlannerProgressEvent] = []

    execution = _planner(provider, publisher, progress=observed.append).plan(context)

    assert execution.decision.skill_ref == "text2env.compile@1.0.0"
    assert provider.calls == 1
    assert json.loads(provider.prompts[0])["context"]["context_sha256"] == context.context_sha256
    assert tuple(ref.name for ref in publisher.refs) == (
        "planner_prompt",
        "planner_raw_response",
        "planner_decision",
        "planner_execution_receipt",
    )
    receipt = _receipt(publisher, execution.receipt_ref)
    assert receipt.status == "succeeded"
    assert receipt.context_sha256 == context.context_sha256
    assert receipt.provider_identity_sha256 == provider_identity_sha256(_identity())
    assert receipt.decision_sha256 == execution.decision_sha256
    assert receipt.event_stages == tuple(event.stage for event in execution.events[:-1])
    assert tuple(event.seq for event in execution.events) == tuple(
        range(1, len(execution.events) + 1)
    )
    assert tuple(event.stage for event in observed) == tuple(
        event.stage for event in execution.events
    )
    assert execution.events[-1].stage == "receipt.published"


def test_provider_failure_still_publishes_terminal_failure_receipt() -> None:
    _, context = _context()
    provider = FakeProvider(b"", error=RuntimeError("model crashed with private path"))
    publisher = RecordingPublisher()

    with pytest.raises(PlannerExecutionError) as captured:
        _planner(provider, publisher).plan(context)

    error = captured.value
    assert error.reason == "provider_failed"
    assert error.receipt_ref is not None
    receipt = _receipt(publisher, error.receipt_ref)
    assert receipt.status == "failed"
    assert receipt.failure_reason == "provider_failed"
    assert receipt.raw_response is None
    assert b"private path" not in publisher.payloads[error.receipt_ref.sha256]


def test_invalid_model_output_is_preserved_as_untrusted_failure_evidence() -> None:
    _, context = _context()
    provider = FakeProvider(b"not-json")
    publisher = RecordingPublisher()

    with pytest.raises(PlannerExecutionError) as captured:
        _planner(provider, publisher).plan(context)

    error = captured.value
    assert error.reason == "response_invalid"
    receipt = _receipt(publisher, error.receipt_ref)
    assert receipt.raw_response is not None
    assert publisher.payloads[receipt.raw_response.sha256] == b"not-json"
    assert receipt.decision is None


def test_provider_identity_drift_fails_closed_after_preserving_raw_output() -> None:
    _, context = _context()
    provider = FakeProvider(_decision_bytes(context), drift=True)
    publisher = RecordingPublisher()

    with pytest.raises(PlannerExecutionError) as captured:
        _planner(provider, publisher).plan(context)

    receipt = _receipt(publisher, captured.value.receipt_ref)
    assert captured.value.reason == "provider_identity_drift"
    assert receipt.failure_reason == "provider_identity_drift"
    assert receipt.raw_response is not None
    assert receipt.decision is None


def test_observer_failure_is_recorded_without_changing_planner_result() -> None:
    _, context = _context()
    provider = FakeProvider(_decision_bytes(context))
    publisher = RecordingPublisher()

    def broken_observer(event: PlannerProgressEvent) -> None:
        if event.stage in {"provider.started", "decision.published"}:
            raise RuntimeError("frontend disconnected")

    execution = _planner(provider, publisher, progress=broken_observer).plan(context)
    receipt = _receipt(publisher, execution.receipt_ref)

    assert receipt.status == "succeeded"
    assert receipt.observer_failure_stages == ("provider.started", "decision.published")


@pytest.mark.parametrize(
    "failure_name,provider_calls,has_receipt",
    [
        ("planner_prompt", 0, False),
        ("planner_raw_response", 1, True),
        ("planner_decision", 1, True),
        ("planner_execution_receipt", 1, False),
    ],
)
def test_storage_failures_never_return_an_unreceipted_success(
    failure_name: str,
    provider_calls: int,
    has_receipt: bool,
) -> None:
    _, context = _context()
    provider = FakeProvider(_decision_bytes(context))
    publisher = RecordingPublisher(fail_on=failure_name)

    with pytest.raises(PlannerExecutionError) as captured:
        _planner(provider, publisher).plan(context)

    assert captured.value.reason == "evidence_publish_failed"
    assert provider.calls == provider_calls
    assert (captured.value.receipt_ref is not None) is has_receipt
    if has_receipt:
        receipt = _receipt(publisher, captured.value.receipt_ref)
        assert receipt.failure_reason == "evidence_publish_failed"


def test_local_publisher_round_trips_exact_bytes_through_cas(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    publisher = LocalPlannerArtifactPublisher(
        artifact_store=store,
        scratch_root=tmp_path / "scratch",
    )
    payload = b'{"schema_version":"harness.test.v1"}\n'

    ref = publisher.publish(
        payload,
        name="test",
        media_type="application/json",
        schema_version="harness.test.v1",
    )

    assert store.resolve(ref).path.read_bytes() == payload
    assert not tuple((tmp_path / "scratch").glob(".planner.*"))


def test_local_publisher_rejects_non_bytes_without_creating_scratch(tmp_path: Path) -> None:
    publisher = LocalPlannerArtifactPublisher(
        artifact_store=LocalArtifactStore(tmp_path / "cas"),
        scratch_root=tmp_path / "scratch",
    )

    with pytest.raises(TypeError, match="payload must be bytes"):
        publisher.publish(
            cast(bytes, bytearray(b"not immutable")),
            name="test",
            media_type="application/octet-stream",
            schema_version="harness.test.v1",
        )

    assert not (tmp_path / "scratch").exists()


@pytest.mark.parametrize(
    "raw,usage",
    [
        (
            bytearray(b"{}"),
            PlannerUsage(
                input_tokens=0,
                output_tokens=0,
                wall_time_ms=0,
                peak_vram_bytes=None,
                finish_reason="stop",
            ),
        ),
        (b"{}", {"finish_reason": "stop"}),
    ],
)
def test_provider_response_requires_exact_immutable_types(raw: object, usage: object) -> None:
    with pytest.raises(TypeError):
        PlannerProviderResponse(raw=cast(bytes, raw), usage=cast(PlannerUsage, usage))


@pytest.mark.parametrize("max_response_bytes", [0, -1, True, 1.5])
def test_planner_rejects_invalid_response_limits(max_response_bytes: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        System2Planner(
            provider=FakeProvider(b"{}"),
            publisher=RecordingPublisher(),
            max_response_bytes=cast(int, max_response_bytes),
        )


def test_default_clock_and_uuid_factory_produce_a_valid_receipt() -> None:
    _, context = _context()
    publisher = RecordingPublisher()

    execution = System2Planner(
        provider=FakeProvider(_decision_bytes(context)),
        publisher=publisher,
    ).plan(context)

    assert execution.receipt.started_at.tzinfo is not None
    assert execution.receipt.ended_at >= execution.receipt.started_at
    assert isinstance(execution.call_id, UUID)


def test_invalid_retained_context_fails_before_any_artifact_or_provider_call() -> None:
    _, context = _context()
    corrupted = context.model_copy(update={"context_sha256": "0" * 64})
    provider = FakeProvider(_decision_bytes(context))
    publisher = RecordingPublisher()

    with pytest.raises(PlannerExecutionError) as captured:
        _planner(provider, publisher).plan(corrupted)

    assert captured.value.reason == "context_invalid"
    assert captured.value.receipt_ref is None
    assert provider.calls == 0
    assert publisher.refs == []


def test_invalid_provider_identity_fails_before_provider_call() -> None:
    _, context = _context()
    provider = FakeProvider(_decision_bytes(context))
    provider.identity_value = cast(PlannerProviderIdentity, {"not": "an identity"})
    publisher = RecordingPublisher()

    with pytest.raises(PlannerExecutionError) as captured:
        _planner(provider, publisher).plan(context)

    assert captured.value.reason == "provider_identity_invalid"
    assert captured.value.receipt_ref is None
    assert provider.calls == 0
    assert tuple(ref.name for ref in publisher.refs) == ("planner_prompt",)


@pytest.mark.parametrize("stage", ["bad stage", "x", 7])
def test_invalid_provider_progress_is_a_receipted_provider_failure(stage: object) -> None:
    _, context = _context()
    provider = FakeProvider(_decision_bytes(context), progress_stage=stage)
    publisher = RecordingPublisher()

    with pytest.raises(PlannerExecutionError) as captured:
        _planner(provider, publisher).plan(context)

    assert captured.value.reason == "provider_failed"
    receipt = _receipt(publisher, captured.value.receipt_ref)
    assert receipt.failure_stage == "provider.invoke"
    assert "provider.bad" not in receipt.event_stages


def test_wrong_provider_response_type_is_a_receipted_provider_failure() -> None:
    _, context = _context()
    provider = FakeProvider(_decision_bytes(context), response_override={"raw": b"{}"})
    publisher = RecordingPublisher()

    with pytest.raises(PlannerExecutionError) as captured:
        _planner(provider, publisher).plan(context)

    assert captured.value.reason == "provider_failed"
    assert _receipt(publisher, captured.value.receipt_ref).failure_type == "TypeError"


def test_failure_receipt_publish_failure_never_masks_primary_failure() -> None:
    _, context = _context()
    provider = FakeProvider(b"", error=RuntimeError("provider down"))
    publisher = RecordingPublisher(fail_on="planner_execution_receipt")

    with pytest.raises(PlannerExecutionError) as captured:
        _planner(provider, publisher).plan(context)

    assert captured.value.reason == "provider_failed"
    assert captured.value.cause_type == "RuntimeError"
    assert captured.value.receipt_ref is None


@pytest.mark.parametrize(
    "mutation",
    [
        {"name": "wrong"},
        {"uri": "artifact://sha256/" + "f" * 64},
        {"media_type": "text/plain"},
        {"sha256": "f" * 64},
        {"bytes": 999},
        {"schema_version": "harness.wrong.v1"},
        "not-an-artifact-ref",
    ],
)
def test_false_publisher_identity_fails_before_provider_call(mutation: object) -> None:
    _, context = _context()
    provider = FakeProvider(_decision_bytes(context))

    class FalsePublisher(RecordingPublisher):
        def publish(
            self,
            payload: bytes,
            *,
            name: str,
            media_type: str,
            schema_version: str,
        ) -> ArtifactRef:
            ref = super().publish(
                payload,
                name=name,
                media_type=media_type,
                schema_version=schema_version,
            )
            if isinstance(mutation, str):
                return cast(ArtifactRef, mutation)
            return ref.model_copy(update=mutation)

    with pytest.raises(PlannerExecutionError) as captured:
        _planner(provider, FalsePublisher()).plan(context)

    assert captured.value.reason == "evidence_publish_failed"
    assert captured.value.receipt_ref is None
    assert provider.calls == 0


@pytest.mark.parametrize(
    "attack",
    [
        "non_utc_start",
        "ended_before_started",
        "identity_digest",
        "prompt_uri",
        "prompt_schema",
        "prompt_media",
        "event_start",
        "observer_stage",
        "failed_contradiction",
    ],
)
def test_receipt_rejects_cross_binding_and_terminal_shape_attacks(attack: str) -> None:
    payload = _successful_receipt_payload()
    if attack == "non_utc_start":
        payload["started_at"] = _T0.astimezone(timezone(timedelta(hours=8)))
    elif attack == "ended_before_started":
        payload["ended_at"] = payload["started_at"] - timedelta(seconds=1)
    elif attack == "identity_digest":
        payload["provider_identity_sha256"] = "0" * 64
    elif attack == "prompt_uri":
        payload["prompt"]["uri"] = "file:///tmp/prompt"
    elif attack == "prompt_schema":
        payload["prompt"]["schema_version"] = "harness.wrong.v1"
    elif attack == "prompt_media":
        payload["prompt"]["media_type"] = "text/plain"
    elif attack == "event_start":
        payload["event_stages"] = ()
    elif attack == "observer_stage":
        payload["observer_failure_stages"] = ("provider.ghost",)
    else:
        payload.update(
            status="failed",
            failure_reason="provider_failed",
            failure_stage="provider.invoke",
            failure_type="RuntimeError",
        )

    with pytest.raises(ValidationError):
        PlannerExecutionReceipt.model_validate(payload)


def test_progress_event_rejects_non_utc_and_non_cas_artifacts() -> None:
    with pytest.raises(ValidationError):
        PlannerProgressEvent(
            call_id=UUID(int=1),
            seq=1,
            timestamp=_T0.astimezone(timezone(timedelta(hours=8))),
            stage="context.validated",
            artifact_refs=(),
        )
    with pytest.raises(ValidationError):
        PlannerProgressEvent(
            call_id=UUID(int=1),
            seq=1,
            timestamp=_T0,
            stage="context.validated",
            artifact_refs=(
                _artifact("request", "1" * 64, "harness.user_input.v1").model_copy(
                    update={"uri": "file:///tmp/request"}
                ),
            ),
        )


@pytest.mark.parametrize(
    "attack",
    ["identity_space", "usage_reason", "negative_clock", "receipt_shape", "progress_artifact"],
)
def test_planner_models_reject_noncanonical_or_inconsistent_records(attack: str) -> None:
    if attack == "identity_space":
        payload = _identity().model_dump(mode="python")
        payload["provider_id"] = " padded "
        with pytest.raises(ValidationError):
            PlannerProviderIdentity.model_validate(payload)
    elif attack == "usage_reason":
        with pytest.raises(ValidationError):
            PlannerUsage(
                input_tokens=1,
                output_tokens=1,
                wall_time_ms=1,
                peak_vram_bytes=None,
                finish_reason=" ",
            )
    elif attack == "negative_clock":
        payload = _identity().model_dump(mode="python")
        assert provider_identity_sha256(_identity()) == provider_identity_sha256(_identity())
        payload["network_access"] = True
        assert PlannerProviderIdentity.model_validate(payload).network_access is True
    elif attack == "receipt_shape":
        _, context = _context()
        provider = FakeProvider(_decision_bytes(context))
        publisher = RecordingPublisher()
        execution = _planner(provider, publisher).plan(context)
        payload = _receipt(publisher, execution.receipt_ref).model_dump(mode="python")
        payload["decision"] = None
        with pytest.raises(ValidationError):
            PlannerExecutionReceipt.model_validate(payload)
    else:
        with pytest.raises(ValidationError):
            PlannerProgressEvent(
                call_id=UUID(int=1),
                seq=1,
                timestamp=_T0,
                stage="bad stage",
                artifact_refs=(),
            )
