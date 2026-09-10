"""Controlled contract tracer for the unqualified ``genesis.observe`` candidate."""

from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.genesis_observe import (
    ControlledImageCapture,
    ControlledObservationCapture,
    ControlledObservationCaptureAdapter,
    ControlledObservationValue,
    ControlledObserveApplicationSettings,
    create_controlled_genesis_observe_application,
)
from self_improving.harness.schemas.common import ArtifactRef, DependencyRef, RunStatus
from self_improving.harness.schemas.genesis_observe import (
    GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID,
    GenesisObservationCapturePlan,
    GenesisObservationReceipt,
    GenesisObservationValue,
    GenesisObserveInput,
    GenesisObserveOutput,
)

_T0 = datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)
_RUN_ID = UUID("51000000-0000-4000-8000-000000000001")
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class _Clock:
    def __init__(self) -> None:
        self._index = 0

    def __call__(self) -> datetime:
        value = _T0 + timedelta(seconds=self._index)
        self._index += 1
        return value


class _CaptureAdapter(ControlledObservationCaptureAdapter):
    identity = DependencyRef(
        name="genesis.observe.controlled_capture", version="1", sha256="a" * 64
    )

    def __init__(self) -> None:
        self.calls = 0

    def capture(
        self,
        request: GenesisObserveInput,
        *,
        captured_at: datetime,
    ) -> ControlledObservationCapture:
        self.calls += 1
        assert request.capture_plan.camera_ids == ("observer",)
        return ControlledObservationCapture(
            captured_at=captured_at,
            images=(
                ControlledImageCapture(
                    camera_id="observer",
                    payload=_PNG,
                    media_type="image/png",
                ),
            ),
            observations=(
                ControlledObservationValue(
                    key="observation.scene_visible",
                    value=True,
                ),
            ),
        )


class _FixedCaptureAdapter(ControlledObservationCaptureAdapter):
    identity = DependencyRef(
        name="genesis.observe.controlled_capture", version="1", sha256="b" * 64
    )

    def __init__(self, capture: ControlledObservationCapture) -> None:
        self.capture_value = capture
        self.calls = 0

    def capture(
        self,
        request: GenesisObserveInput,
        *,
        captured_at: datetime,
    ) -> ControlledObservationCapture:
        self.calls += 1
        return self.capture_value


def _put(
    store: LocalArtifactStore,
    tmp_path: Path,
    *,
    name: str,
    schema_version: str,
    payload: bytes = b"{}\n",
) -> ArtifactRef:
    source = tmp_path / name
    source.write_bytes(payload)
    return store.put_file(
        source,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _input(
    store: LocalArtifactStore, tmp_path: Path, *, ttl_seconds: int = 60
) -> GenesisObserveInput:
    return GenesisObserveInput(
        schema_version="harness.genesis_observe_input.v1",
        environment_package=_put(
            store,
            tmp_path,
            name="environment-package.json",
            schema_version="harness.x2env_environment_package.v1",
        ),
        replay_workflow_receipt=_put(
            store,
            tmp_path,
            name="replay-workflow-receipt.json",
            schema_version="harness.workflow_operation_receipt.v2",
        ),
        replay_runtime_receipt=_put(
            store,
            tmp_path,
            name="replay-runtime-receipt.json",
            schema_version="harness.x2env_runtime_receipt.v1",
        ),
        checkpoint=None,
        capture_plan=GenesisObservationCapturePlan(
            camera_ids=("observer",),
            stills_per_camera=1,
            video_frames=0,
            fps=None,
            post_restore_steps=1,
        ),
        requested_observation_keys=("observation.scene_visible",),
        ttl_seconds=ttl_seconds,
    )


def _image_ref(name: str = "observation.png", digest: str = "1" * 64) -> ArtifactRef:
    return ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{digest}",
        media_type="image/png",
        sha256=digest,
        bytes=1,
        schema_version=None,
    )


def _verification_ref(digest: str = "2" * 64) -> ArtifactRef:
    return ArtifactRef(
        name="verification.json",
        uri=f"artifact://sha256/{digest}",
        media_type="application/json",
        sha256=digest,
        bytes=2,
        schema_version="harness.genesis_observation_media_verification.v1",
    )


def _receipt(
    request: GenesisObserveInput,
    *,
    images: tuple[ArtifactRef, ...] | None = None,
    video: ArtifactRef | None = None,
    observations: tuple[GenesisObservationValue, ...] | None = None,
    captured_at: datetime = _T0,
    valid_until: datetime | None = None,
    simulator_executed: bool = False,
    post_restore_steps_executed: int = 0,
) -> GenesisObservationReceipt:
    images = images or (_image_ref(),)
    valid_until = valid_until or captured_at + timedelta(seconds=request.ttl_seconds)
    observations = observations or (
        GenesisObservationValue(
            key="observation.scene_visible",
            value=True,
            captured_at=captured_at,
            valid_until=valid_until,
            payload_refs=images,
        ),
    )
    return GenesisObservationReceipt(
        schema_version=GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID,
        observation_child_run_id=_RUN_ID,
        environment_package=request.environment_package,
        replay_workflow_receipt=request.replay_workflow_receipt,
        replay_runtime_receipt=request.replay_runtime_receipt,
        checkpoint=request.checkpoint,
        capture_plan=request.capture_plan,
        requested_observation_keys=request.requested_observation_keys,
        captured_at=captured_at,
        valid_until=valid_until,
        ttl_seconds=request.ttl_seconds,
        images=images,
        video=video,
        media_verification=_verification_ref(),
        observations=observations,
        simulator_executed=simulator_executed,
        post_restore_steps_executed=post_restore_steps_executed,
    )


def test_controlled_observe_candidate_publishes_fresh_capture_without_simulator_claim(
    tmp_path: Path,
) -> None:
    evidence = LocalArtifactStore(tmp_path / "evidence")
    adapter = _CaptureAdapter()
    application = create_controlled_genesis_observe_application(
        ControlledObserveApplicationSettings(
            state_root=tmp_path / "observe-state",
            evidence_artifact_root=evidence.root,
        ),
        capture_adapter=adapter,
        clock=_Clock(),
        run_id_factory=lambda: _RUN_ID,
    )
    request = _input(evidence, tmp_path)

    evaluation = application.evaluate(request)

    assert evaluation.mode == "qualification_candidate"
    assert evaluation.candidate.skill_id == "genesis.observe"
    assert evaluation.candidate.version == "1.0.0"
    assert application.skills == ()
    assert adapter.calls == 1
    assert evaluation.state.status is RunStatus.SUCCEEDED
    output = GenesisObserveOutput.model_validate(evaluation.state.output)
    assert output.simulator_executed is False
    assert output.captured_at == _T0 + timedelta(seconds=1)
    assert output.valid_until == output.captured_at + timedelta(seconds=60)
    assert [image.name for image in output.images] == ["observation_observer.png"]
    assert evidence.resolve(output.images[0]).path.read_bytes() == _PNG
    receipt = GenesisObservationReceipt.model_validate_json(
        evidence.resolve(output.observation_receipt).path.read_bytes()
    )
    assert receipt.schema_version == GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID
    assert receipt.observation_child_run_id == _RUN_ID
    assert receipt.environment_package == request.environment_package
    assert receipt.replay_workflow_receipt == request.replay_workflow_receipt
    assert receipt.replay_runtime_receipt == request.replay_runtime_receipt
    assert receipt.capture_plan == request.capture_plan
    assert receipt.requested_observation_keys == request.requested_observation_keys
    assert [item.key for item in receipt.observations] == ["observation.scene_visible"]
    assert receipt.observations[0].value is True
    assert receipt.observations[0].captured_at == receipt.captured_at
    assert receipt.observations[0].valid_until == receipt.valid_until
    assert receipt.observations[0].payload_refs == output.images
    assert receipt.images == output.images
    assert receipt.media_verification == output.media_verification
    assert receipt.simulator_executed is False
    assert receipt.post_restore_steps_executed == 0
    assert set(evaluation.state.artifacts) == {
        output.observation_receipt,
        output.media_verification,
        *output.images,
    }
    assert application.invocation(_RUN_ID) == evaluation.invocation
    assert application.run_state(_RUN_ID) == evaluation.state
    assert application.artifact_root == evidence.root
    assert application.authority_path == (tmp_path / "observe-state/harness.sqlite3").resolve()
    assert application.resolve_artifact(output.images[0]).read_bytes() == _PNG


@pytest.mark.parametrize("ttl_seconds", [0, 301, True])
def test_observe_ttl_is_a_strict_bounded_integer(
    tmp_path: Path,
    ttl_seconds: object,
) -> None:
    store = LocalArtifactStore(tmp_path / "evidence")
    with pytest.raises(ValidationError):
        _input(store, tmp_path, ttl_seconds=ttl_seconds)  # type: ignore[arg-type]


def test_capture_plan_rejects_unsorted_duplicate_or_incoherent_video_fields() -> None:
    base = {
        "camera_ids": ("observer",),
        "stills_per_camera": 1,
        "video_frames": 0,
        "fps": None,
        "post_restore_steps": 1,
    }
    for update in (
        {"camera_ids": ("wrist", "observer")},
        {"camera_ids": ("observer", "observer")},
        {"video_frames": 0, "fps": 12},
        {"video_frames": 12, "fps": None},
    ):
        with pytest.raises(ValidationError):
            GenesisObservationCapturePlan(**(base | update))


def test_observe_input_requires_sorted_unique_keys_and_typed_optional_checkpoint(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "evidence")
    request = _input(store, tmp_path)
    for keys in (
        ("observation.z", "observation.a"),
        ("observation.a", "observation.a"),
    ):
        with pytest.raises(ValidationError, match="requested_observation_keys"):
            GenesisObserveInput.model_validate(
                request.model_copy(update={"requested_observation_keys": keys}).model_dump(
                    mode="python"
                )
            )
    checkpoint = _put(
        store,
        tmp_path,
        name="checkpoint.json",
        schema_version="harness.genesis_checkpoint.v1",
    )
    with_checkpoint = GenesisObserveInput.model_validate(
        request.model_copy(update={"checkpoint": checkpoint}).model_dump(mode="python")
    )
    assert with_checkpoint.source_artifacts() == (
        request.environment_package,
        request.replay_workflow_receipt,
        request.replay_runtime_receipt,
        checkpoint,
    )
    wrong = checkpoint.model_copy(update={"schema_version": "harness.fresh_observation.v1"})
    with pytest.raises(ValidationError, match="checkpoint.schema_version"):
        GenesisObserveInput.model_validate(
            request.model_copy(update={"checkpoint": wrong}).model_dump(mode="python")
        )


def test_observe_input_rejects_non_cas_or_wrongly_typed_sources(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "evidence")
    request = _input(store, tmp_path)
    wrong = request.environment_package.model_copy(update={"uri": "file:///tmp/package.json"})
    with pytest.raises(ValidationError, match="environment_package.uri"):
        GenesisObserveInput.model_validate(
            request.model_copy(update={"environment_package": wrong}).model_dump(mode="python")
        )
    wrong = request.replay_runtime_receipt.model_copy(
        update={"schema_version": "harness.fresh_observation.v1"}
    )
    with pytest.raises(ValidationError, match="replay_runtime_receipt.schema_version"):
        GenesisObserveInput.model_validate(
            request.model_copy(update={"replay_runtime_receipt": wrong}).model_dump(mode="python")
        )
    wrong = request.environment_package.model_copy(update={"media_type": "text/plain"})
    with pytest.raises(ValidationError, match="environment_package.media_type"):
        GenesisObserveInput.model_validate(
            request.model_copy(update={"environment_package": wrong}).model_dump(mode="python")
        )


def test_controlled_application_blocks_unavailable_source_before_capture(tmp_path: Path) -> None:
    evidence = LocalArtifactStore(tmp_path / "evidence")
    adapter = _CaptureAdapter()
    application = create_controlled_genesis_observe_application(
        ControlledObserveApplicationSettings(
            state_root=tmp_path / "observe-state",
            evidence_artifact_root=evidence.root,
        ),
        capture_adapter=adapter,
        clock=_Clock(),
        run_id_factory=lambda: _RUN_ID,
    )
    request = _input(evidence, tmp_path)
    missing = request.replay_runtime_receipt.model_copy(
        update={
            "sha256": "f" * 64,
            "uri": f"artifact://sha256/{'f' * 64}",
        }
    )

    evaluation = application.evaluate(
        request.model_copy(update={"replay_runtime_receipt": missing})
    )

    assert evaluation.state.status is RunStatus.BLOCKED
    assert evaluation.state.output is None
    assert evaluation.state.blocker is not None
    assert evaluation.state.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert adapter.calls == 0


def test_receipt_rejects_controlled_capture_that_claims_runtime_steps(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "evidence")
    request = _input(store, tmp_path)
    image = request.environment_package.model_copy(
        update={"name": "observation.png", "media_type": "image/png", "schema_version": None}
    )
    verification = request.environment_package.model_copy(
        update={
            "name": "verification.json",
            "schema_version": "harness.genesis_observation_media_verification.v1",
        }
    )
    with pytest.raises(ValidationError, match="controlled observation cannot claim runtime steps"):
        GenesisObservationReceipt(
            schema_version=GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID,
            observation_child_run_id=_RUN_ID,
            environment_package=request.environment_package,
            replay_workflow_receipt=request.replay_workflow_receipt,
            replay_runtime_receipt=request.replay_runtime_receipt,
            checkpoint=None,
            capture_plan=request.capture_plan,
            requested_observation_keys=request.requested_observation_keys,
            captured_at=_T0,
            valid_until=_T0 + timedelta(seconds=60),
            ttl_seconds=60,
            images=(image,),
            video=None,
            media_verification=verification,
            observations=(
                GenesisObservationValue(
                    key="observation.scene_visible",
                    value=True,
                    captured_at=_T0,
                    valid_until=_T0 + timedelta(seconds=60),
                    payload_refs=(image,),
                ),
            ),
            simulator_executed=False,
            post_restore_steps_executed=1,
        )


def test_receipt_accepts_exact_runtime_step_claim_and_optional_video(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "evidence")
    request = _input(store, tmp_path).model_copy(
        update={
            "capture_plan": GenesisObservationCapturePlan(
                camera_ids=("observer",),
                stills_per_camera=1,
                video_frames=12,
                fps=12,
                post_restore_steps=3,
            )
        }
    )
    video = ArtifactRef(
        name="observation.mp4",
        uri=f"artifact://sha256/{'3' * 64}",
        media_type="video/mp4",
        sha256="3" * 64,
        bytes=3,
        schema_version=None,
    )
    observation = GenesisObservationValue(
        key="observation.scene_visible",
        value=True,
        captured_at=_T0,
        valid_until=_T0 + timedelta(seconds=60),
        payload_refs=(_image_ref(), video),
    )
    receipt = _receipt(
        request,
        video=video,
        observations=(observation,),
        simulator_executed=True,
        post_restore_steps_executed=3,
    )
    assert receipt.video == video
    assert receipt.simulator_executed is True


def test_observation_value_rejects_invalid_time_and_payload_order() -> None:
    for payload in (
        {
            "key": "observation.scene_visible",
            "value": True,
            "captured_at": _T0,
            "valid_until": _T0,
            "payload_refs": (_image_ref(),),
        },
        {
            "key": "observation.scene_visible",
            "value": True,
            "captured_at": _T0,
            "valid_until": _T0 + timedelta(seconds=1),
            "payload_refs": (_image_ref("z.png", "f" * 64), _image_ref("a.png", "1" * 64)),
        },
    ):
        with pytest.raises(ValidationError):
            GenesisObservationValue(**payload)


def test_receipt_rejects_mismatched_count_video_keys_times_payload_and_ttl(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "evidence")
    request = _input(store, tmp_path)
    base = _receipt(request)
    foreign = _image_ref("foreign.png", "f" * 64)
    mutations = (
        ({"images": (_image_ref(), _image_ref("second.png", "3" * 64))}, "one still"),
        (
            {
                "video": ArtifactRef(
                    name="x.mp4",
                    uri=f"artifact://sha256/{'4' * 64}",
                    media_type="video/mp4",
                    sha256="4" * 64,
                    bytes=1,
                    schema_version=None,
                )
            },
            "video must exist",
        ),
        ({"requested_observation_keys": ("observation.other",)}, "observation keys"),
        ({"valid_until": _T0 + timedelta(seconds=59)}, "plus ttl_seconds"),
        (
            {
                "observations": (
                    base.observations[0].model_copy(
                        update={"captured_at": _T0 + timedelta(seconds=1)}
                    ),
                )
            },
            "observation times",
        ),
        (
            {
                "observations": (
                    base.observations[0].model_copy(update={"payload_refs": (foreign,)}),
                )
            },
            "belong to receipt media",
        ),
        ({"simulator_executed": True, "post_restore_steps_executed": 2}, "every requested"),
    )
    for update, message in mutations:
        with pytest.raises(ValidationError, match=message):
            GenesisObservationReceipt.model_validate(
                base.model_copy(update=update).model_dump(mode="python")
            )


def test_output_rejects_wrong_receipt_media_or_capture_window(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "evidence")
    request = _input(store, tmp_path)
    receipt = _receipt(request)
    receipt_ref = request.environment_package.model_copy(
        update={
            "name": "observation-receipt.json",
            "schema_version": GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID,
        }
    )
    output = GenesisObserveOutput(
        schema_version="harness.genesis_observe_output.v1",
        observation_receipt=receipt_ref,
        images=receipt.images,
        video=None,
        media_verification=receipt.media_verification,
        captured_at=receipt.captured_at,
        valid_until=receipt.valid_until,
        simulator_executed=False,
    )
    for update in (
        {"observation_receipt": receipt_ref.model_copy(update={"schema_version": None})},
        {"images": (_image_ref("z.png", "f" * 64), _image_ref("a.png", "1" * 64))},
        {"images": (_image_ref().model_copy(update={"media_type": "application/octet-stream"}),)},
        {"captured_at": _T0.astimezone(timezone(timedelta(hours=8)))},
        {"valid_until": output.captured_at},
    ):
        with pytest.raises(ValidationError):
            GenesisObserveOutput.model_validate(
                output.model_copy(update=update).model_dump(mode="python")
            )


@pytest.mark.parametrize(
    "capture,code",
    [
        (
            ControlledObservationCapture(
                captured_at=_T0 + timedelta(seconds=9),
                images=(ControlledImageCapture("observer", _PNG),),
                observations=(ControlledObservationValue("observation.scene_visible", True),),
            ),
            "HARN_OBSERVATION_UNBOUND",
        ),
        (
            ControlledObservationCapture(
                captured_at=_T0 + timedelta(seconds=1),
                images=(ControlledImageCapture("wrist", _PNG),),
                observations=(ControlledObservationValue("observation.scene_visible", True),),
            ),
            "HARN_OBSERVATION_UNBOUND",
        ),
        (
            ControlledObservationCapture(
                captured_at=_T0 + timedelta(seconds=1),
                images=(ControlledImageCapture("observer", _PNG),),
                observations=(ControlledObservationValue("observation.other", True),),
            ),
            "HARN_OBSERVATION_UNBOUND",
        ),
        (
            ControlledObservationCapture(
                captured_at=_T0 + timedelta(seconds=1),
                images=(ControlledImageCapture("observer", b"not-png"),),
                observations=(ControlledObservationValue("observation.scene_visible", True),),
            ),
            "HARN_OBSERVATION_UNBOUND",
        ),
    ],
)
def test_controlled_application_rejects_unbound_capture(
    tmp_path: Path,
    capture: ControlledObservationCapture,
    code: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "evidence")
    adapter = _FixedCaptureAdapter(capture)
    application = create_controlled_genesis_observe_application(
        ControlledObserveApplicationSettings(tmp_path / "state", store.root),
        capture_adapter=adapter,
        clock=_Clock(),
        run_id_factory=lambda: _RUN_ID,
    )
    result = application.evaluate(_input(store, tmp_path)).state
    assert result.status is RunStatus.BLOCKED
    assert result.blocker is not None
    assert result.blocker.code == code


def test_controlled_candidate_rejects_video_without_calling_capture(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "evidence")
    adapter = _CaptureAdapter()
    application = create_controlled_genesis_observe_application(
        ControlledObserveApplicationSettings(tmp_path / "state", store.root),
        capture_adapter=adapter,
        clock=_Clock(),
        run_id_factory=lambda: _RUN_ID,
    )
    request = _input(store, tmp_path).model_copy(
        update={
            "capture_plan": GenesisObservationCapturePlan(
                camera_ids=("observer",),
                stills_per_camera=1,
                video_frames=2,
                fps=12,
                post_restore_steps=1,
            )
        }
    )
    result = application.evaluate(request).state
    assert result.status is RunStatus.BLOCKED
    assert result.blocker is not None
    assert result.blocker.code == "HARN_OBSERVATION_CAPTURE_UNSUPPORTED"
    assert adapter.calls == 0


def test_controlled_capture_value_and_factory_reject_invalid_configuration(tmp_path: Path) -> None:
    for arguments in (
        {"camera_id": "", "payload": _PNG},
        {"camera_id": "Observer", "payload": _PNG},
        {"camera_id": "observer", "payload": b""},
        {"camera_id": "observer", "payload": _PNG, "media_type": "image/jpeg"},
    ):
        with pytest.raises((TypeError, ValueError)):
            ControlledImageCapture(**arguments)
    with pytest.raises(TypeError):
        ControlledObservationValue("", True)
    with pytest.raises(TypeError):
        ControlledObservationValue("observation.value", object())  # type: ignore[arg-type]
    for capture in (
        {
            "captured_at": _T0,
            "images": (),
            "observations": (ControlledObservationValue("observation.a", True),),
        },
        {
            "captured_at": _T0,
            "images": (
                ControlledImageCapture("wrist", _PNG),
                ControlledImageCapture("observer", _PNG),
            ),
            "observations": (ControlledObservationValue("observation.a", True),),
        },
        {
            "captured_at": _T0,
            "images": (ControlledImageCapture("observer", _PNG),),
            "observations": (),
        },
        {
            "captured_at": _T0,
            "images": (ControlledImageCapture("observer", _PNG),),
            "observations": (
                ControlledObservationValue("observation.z", True),
                ControlledObservationValue("observation.a", True),
            ),
        },
        {
            "captured_at": datetime(2026, 9, 11, 1, 0),
            "images": (ControlledImageCapture("observer", _PNG),),
            "observations": (ControlledObservationValue("observation.a", True),),
        },
    ):
        with pytest.raises(ValueError):
            ControlledObservationCapture(**capture)

    store = LocalArtifactStore(tmp_path / "evidence")
    adapter = _CaptureAdapter()
    settings = ControlledObserveApplicationSettings(tmp_path / "state", store.root)
    with pytest.raises(TypeError, match="settings"):
        create_controlled_genesis_observe_application(
            object(),  # type: ignore[arg-type]
            capture_adapter=adapter,
            clock=_Clock(),
            run_id_factory=lambda: _RUN_ID,
        )
    with pytest.raises(TypeError, match="capture_adapter"):
        create_controlled_genesis_observe_application(
            settings,
            capture_adapter=object(),  # type: ignore[arg-type]
            clock=_Clock(),
            run_id_factory=lambda: _RUN_ID,
        )

    class _BadIdentity(_CaptureAdapter):
        identity = object()  # type: ignore[assignment]

    with pytest.raises(TypeError, match="identity"):
        create_controlled_genesis_observe_application(
            settings,
            capture_adapter=_BadIdentity(),
            clock=_Clock(),
            run_id_factory=lambda: _RUN_ID,
        )
    with pytest.raises(TypeError, match="callable"):
        create_controlled_genesis_observe_application(
            settings,
            capture_adapter=adapter,
            clock=None,  # type: ignore[arg-type]
            run_id_factory=lambda: _RUN_ID,
        )
    bad_root = tmp_path / "not-a-directory"
    bad_root.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="state_root"):
        create_controlled_genesis_observe_application(
            replace(settings, state_root=bad_root),
            capture_adapter=adapter,
            clock=_Clock(),
            run_id_factory=lambda: _RUN_ID,
        )


def test_controlled_application_requires_exact_input_type(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "evidence")
    application = create_controlled_genesis_observe_application(
        ControlledObserveApplicationSettings(tmp_path / "state", store.root),
        capture_adapter=_CaptureAdapter(),
        clock=_Clock(),
        run_id_factory=lambda: _RUN_ID,
    )
    with pytest.raises(TypeError, match="exact GenesisObserveInput"):
        application.evaluate(object())  # type: ignore[arg-type]
