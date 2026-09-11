"""Shared modified-scene replay, actual frame observation and deep validation.

This module owns no journal or qualification. Callers journal each returned stage
in their existing workflow. A saved-output runner proves consistency only; the
fixed registered runner alone reports actual execution. Observation timestamps
come from the final half-dt capture, never from the time a reader opened it.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic

from PIL import Image

from .genesis_scene_execution_v3 import (
    GenesisSceneExecutionProducerV3,
    GenesisSceneExecutionVerifierV3,
    read_scene_run_evidence_v3,
)
from .revised_image_compile_application import RevisedImageCompileApplication
from .schemas.genesis_scene_execution_v3 import (
    GenesisSceneExecutionReceiptV3,
    GenesisSceneExecutionRequestV3,
    GenesisSceneLaunchResultV3,
    GenesisSceneProcessObservationV3,
)


def compare_preview_layout(actual, expected):
    """One millimetre/radian gate; yaw uses shortest world-Z angular distance."""
    if expected is None:
        return "pass"
    distance = math.hypot(actual.x_m - expected.x_m, actual.y_m - expected.y_m)
    yaw_delta = math.atan2(
        math.sin(actual.yaw_rad - expected.yaw_rad),
        math.cos(actual.yaw_rad - expected.yaw_rad),
    )
    return "pass" if distance <= 0.001 and abs(yaw_delta) <= 0.001 else "mismatch"


def validate_preview_receipt(store, compiler, parameters):
    """Deep native physics/media plus separate explicit user-layout semantics.

    The selected public verifier rechecks the declared scene's full measured
    geometry and the terminal trace/contact/settling/media evidence. Layout
    comparison uses the requested initial placement, not an invented final pose.
    """
    from .genesis_scene_execution_v2 import GenesisSceneExecutionVerifierV2
    from .gujie_scene_adapter import read_gujie_tabletop_scene
    from .schemas.genesis_scene_execution_v2 import GenesisSceneExecutionReceiptV2
    from .schemas.limited_image_preview_execution import (
        LimitedSceneValidateInputV1,
        LimitedSceneValidationV1,
        PreviewLayout,
    )

    if type(parameters) is not LimitedSceneValidateInputV1:
        raise TypeError("preview validation requires exact typed input")
    native_v3 = parameters.execution_receipt.schema_version.endswith(".v3")
    model = GenesisSceneExecutionReceiptV3 if native_v3 else GenesisSceneExecutionReceiptV2
    receipt = _read(store, parameters.execution_receipt, model)
    verifier = (GenesisSceneExecutionVerifierV3 if native_v3 else GenesisSceneExecutionVerifierV2)(
        store, compiler
    )
    closure = verifier.read_receipt(parameters.execution_receipt)
    if closure.report.status != "passed":
        raise ValueError("physical scene validation did not pass")
    scene = read_gujie_tabletop_scene(store, closure.request.scene)
    w, x, y, z = scene.orientation_wxyz
    actual = PreviewLayout(
        x_m=scene.position_m[0],
        y_m=scene.position_m[1],
        yaw_rad=math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)),
    )
    semantic = compare_preview_layout(actual, parameters.expected_layout)
    return LimitedSceneValidationV1(
        schema_version="harness.limited_scene_validation.v1",
        execution_receipt=parameters.execution_receipt,
        scene=closure.request.scene,
        physical_report=receipt.report,
        expected_layout=parameters.expected_layout,
        actual_layout=actual,
        position_tolerance_m=0.001,
        yaw_tolerance_rad=0.001,
        semantic_status=semantic,
        status="passed" if semantic == "pass" else "failed",
        physical_scope="dual_dt_trace_contact_settling_and_media",
    )


def _read(store, ref, model):
    raw = store.resolve(ref).path.read_bytes()
    value = model.model_validate_json(raw)
    expected = json.dumps(
        value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    if raw != expected or ref.schema_version != value.schema_version:
        raise ValueError("preview evidence must be exact canonical typed CAS")
    return value


@dataclass(frozen=True)
class PreviewReplay:
    request: object
    launch: object
    execution_authority: bool
    elapsed_seconds: float


@dataclass(frozen=True)
class PreviewObservation:
    request: object
    launch: object
    image: object
    captured_at: datetime
    expires_at: datetime
    checked_at: datetime
    pixels_sha256: str
    elapsed_seconds: float
    authority: str = "observed_replay_final_frame"


@dataclass(frozen=True)
class PreviewValidation:
    request: object
    launch: object
    report: object
    execution_authority: bool
    elapsed_seconds: float
    profile: str = "genesis.rigid_scene@1"


class PreviewExecutionError(ValueError):
    def __init__(self, stage, cause, elapsed_seconds):
        super().__init__(str(cause))
        self.stage = stage
        self.elapsed_seconds = elapsed_seconds
        self.diagnostic_ref = getattr(cause, "diagnostic_ref", None)


class RegisteredRevisedSceneRunner:
    """Exact compiler authority plus existing v3 producer; no new Skill app stack."""

    execution_authority = True

    def __init__(self, application, producer, *, compile_run_id, roots):
        if type(application) is not RevisedImageCompileApplication or type(producer) is not (
            GenesisSceneExecutionProducerV3
        ):
            raise TypeError("preview requires exact registered compile and scene producer")
        if application.compiler is not producer.compiler:
            raise ValueError("preview compiler and producer must share the exact compiler")
        self.application = application
        self.producer = producer
        self.compile_run_id = compile_run_id
        self.roots = roots

    def run(self, request_ref):
        store = self.application.artifact_store
        request = _read(store, request_ref, GenesisSceneExecutionRequestV3)
        receipt_ref, compiled = self.application.read(self.compile_run_id)
        if (
            request.compile_receipt != receipt_ref
            or request.scene != compiled.scene
            or request.asset_qualification != compiled.qualification
        ):
            raise ValueError("preview must consume the registered modified compile")
        live = self.producer.produce(request_ref, roots=self.roots)
        receipt = _read(store, live.receipt_ref, GenesisSceneExecutionReceiptV3)
        return receipt.launch_result


class LimitedImagePreviewExecution:
    """One runner seam shared by replay, observation and validation callers.

    Fake runners are useful for saved-evidence tests. They cannot grant live
    authority regardless of a caller-supplied attribute. The workflow, not this
    stateless execution module, owns idempotency and explicit retry decisions.
    """

    def __init__(self, artifact_store, runner):
        self.artifact_store = artifact_store
        self.runner = runner

    def replay(self, request_ref):
        started = monotonic()
        try:
            _read(self.artifact_store, request_ref, GenesisSceneExecutionRequestV3)
            launch_ref = self.runner.run(request_ref)
            launch = _read(self.artifact_store, launch_ref, GenesisSceneLaunchResultV3)
            if launch.request != request_ref:
                raise ValueError("runner returned a foreign request")
            return PreviewReplay(
                request_ref,
                launch_ref,
                type(self.runner) is RegisteredRevisedSceneRunner,
                monotonic() - started,
            )
        except Exception as cause:
            raise PreviewExecutionError("replay", cause, monotonic() - started) from cause

    def observe(self, replay, *, now=None, ttl_seconds=300):
        started = monotonic()
        try:
            if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
                raise ValueError("observation TTL must be between 1 and 300 seconds")
            launch = _read(self.artifact_store, replay.launch, GenesisSceneLaunchResultV3)
            observation = _read(
                self.artifact_store, launch.half_dt_observation, GenesisSceneProcessObservationV3
            )
            if launch.request != replay.request or observation.request != replay.request:
                raise ValueError("observation must share modified replay lineage")
            capture = observation.captures[-1]
            checked = now if now is not None else datetime.now(timezone.utc)
            expires = capture.captured_at + timedelta(seconds=ttl_seconds)
            if not capture.captured_at <= checked <= expires:
                raise ValueError("actual replay frame observation is stale or from the future")
            with Image.open(self.artifact_store.resolve(capture.image).path) as image:
                if image.mode != "RGB" or image.size != (capture.width, capture.height):
                    raise ValueError("actual frame dimensions or mode differ")
                if hashlib.sha256(image.tobytes()).hexdigest() != capture.pixels_sha256:
                    raise ValueError("actual frame pixels differ")
            return PreviewObservation(
                replay.request,
                replay.launch,
                capture.image,
                capture.captured_at,
                expires,
                checked,
                capture.pixels_sha256,
                monotonic() - started,
            )
        except Exception as cause:
            raise PreviewExecutionError("observe", cause, monotonic() - started) from cause

    def validate(self, replay):
        started = monotonic()
        try:
            if type(self.runner) is RegisteredRevisedSceneRunner:
                closure = GenesisSceneExecutionVerifierV3(
                    self.artifact_store, self.runner.application.compiler
                ).verify_launch(replay.request, replay.launch)
            else:
                closure = read_scene_run_evidence_v3(
                    self.artifact_store, replay.request, replay.launch
                )
            if closure.report.status != "passed":
                raise ValueError("modified scene trace or media validation did not pass")
            return PreviewValidation(
                replay.request,
                replay.launch,
                closure.report,
                type(self.runner) is RegisteredRevisedSceneRunner,
                monotonic() - started,
            )
        except Exception as cause:
            raise PreviewExecutionError("validate", cause, monotonic() - started) from cause
