"""Read qualified durable replay terminals and assess their rigid-scene evidence."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from .genesis_scene_application_v2 import GenesisSceneApplication
from .genesis_scene_execution_v2 import GenesisSceneExecutionVerifierV2
from .schemas.genesis_scene_application_v2 import GenesisSceneReplayInputV2
from .schemas.genesis_scene_assessment import (
    SCENE_FRESH_OBSERVATION_ID,
    SCENE_VALIDATION_ID,
    GenesisSceneFreshObservationV1,
    GenesisSceneValidationV1,
)
from .schemas.genesis_scene_execution_v2 import (
    SCENE_EXECUTION_RECEIPT_V2_ID,
    GenesisSceneExecutionReceiptV2,
    GenesisSceneProcessObservationV2,
)


def require_fresh_capture(captured_at, *, now, ttl_seconds):
    """Check an actual camera event without replacing or extending its timestamp."""
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
        raise ValueError("fresh observation TTL must be an integer from 1 to 300 seconds")
    if (
        captured_at.tzinfo is None
        or now.tzinfo is None
        or captured_at.utcoffset().total_seconds() != 0
        or now.utcoffset().total_seconds() != 0
        or not 0 <= (now - captured_at).total_seconds() <= ttl_seconds
    ):
        raise ValueError("capture is not fresh within the requested TTL")
    return captured_at


class GenesisSceneAssessment:
    """One trusted source of replay terminals; no additional mutable run authority."""

    def __init__(self, application):
        if type(application) is not GenesisSceneApplication:
            raise TypeError("assessment requires the trusted GenesisSceneApplication")
        self.application = application
        self.artifact_store = application.producer.artifact_store

    def read_replay(self, run_id):
        """Deeply read one registered replay from the configured durable authority."""
        return self._read_replay(run_id, candidate=False)

    def read_candidate_replay(self, run_id):
        """Read qualification evidence only; never accept a production terminal here."""
        return self._read_replay(run_id, candidate=True)

    def _read_replay(self, run_id, *, candidate):
        app = self.application
        descriptors = app.skills
        if not candidate and (
            len(descriptors) != 1
            or descriptors[0].skill_id != "image2env.replay"
            or descriptors[0].version != "2.0.0"
        ):
            raise ValueError("assessment requires a qualified replay application")
        state, invocation = app.run_state(run_id), app.invocation(run_id)
        if (
            state is None
            or invocation is None
            or state.status.value != "succeeded"
            or state.skill_id != "image2env.replay"
            or state.skill_version != "2.0.0"
            or state.invocation_digest != invocation.invocation_digest
            or not state.events
            or any(
                event.stage.startswith("qualification_candidate.") != candidate
                for event in state.events
            )
        ):
            raise ValueError("assessment requires a successful registered replay terminal")
        if invocation.dependencies != app.resolver.dependencies["image2env.replay@2.0.0"]:
            raise ValueError("assessment replay implementation dependencies differ")
        refs = [
            ref for ref in state.artifacts if ref.schema_version == SCENE_EXECUTION_RECEIPT_V2_ID
        ]
        if len(refs) != 1:
            raise ValueError("assessment terminal must contain exactly one execution receipt")
        receipt = GenesisSceneExecutionReceiptV2.model_validate_json(
            self.artifact_store.resolve(refs[0]).path.read_bytes()
        )
        if state.output != receipt.model_dump(mode="json"):
            raise ValueError("assessment terminal output differs from its receipt")
        closure = GenesisSceneExecutionVerifierV2(
            self.artifact_store, app.producer.compiler
        ).read_receipt(refs[0])
        if (
            app.resolve_input(
                GenesisSceneReplayInputV2.model_validate(invocation.effective_parameters)
            )
            != receipt.request
            or closure.request.runtime_manifest != app.producer.approved_manifest
            or closure.report.status != "passed"
        ):
            raise ValueError("assessment request, approved runtime or rigid-scene report differs")
        return refs[0], receipt, closure, invocation

    def validate(self, run_id):
        """Deeply recheck physics and all media without claiming a new simulation run."""
        return self._validate(run_id, candidate=False)

    def validate_candidate(self, run_id):
        return self._validate(run_id, candidate=True)

    def _validate(self, run_id, *, candidate):
        ref, receipt, closure, invocation = self._read_replay(run_id, candidate=candidate)
        result = GenesisSceneValidationV1(
            schema_version=SCENE_VALIDATION_ID,
            scope="genesis.rigid_scene@1",
            status="passed",
            replay_run_id=run_id,
            invocation_digest=invocation.invocation_digest,
            execution_receipt=ref,
            scene=closure.request.scene,
            replay=receipt.replay,
            report=receipt.report,
            assessed_at=datetime.now(timezone.utc),
            simulator_rerun=False,
            robot_policy_evaluated=False,
            candidate_only=candidate,
        )
        with TemporaryDirectory(prefix="genesis-scene-validation-") as directory:
            path = Path(directory) / "scene-validation.json"
            path.write_text(
                json.dumps(result.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            )
            return self.artifact_store.put_file(
                path,
                name=path.name,
                media_type="application/json",
                schema_version=SCENE_VALIDATION_ID,
            )

    def observe(self, run_id, *, ttl_seconds):
        """Read the actual final camera event; an expired run needs a new real capture."""
        return self._observe(run_id, ttl_seconds=ttl_seconds, candidate=False)

    def observe_candidate(self, run_id, *, ttl_seconds):
        return self._observe(run_id, ttl_seconds=ttl_seconds, candidate=True)

    def _observe(self, run_id, *, ttl_seconds, candidate):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
            raise ValueError("fresh observation TTL must be an integer from 1 to 300 seconds")
        ref, _, closure, _ = self._read_replay(run_id, candidate=candidate)
        observation_ref = closure.launch_result.half_dt_observation
        observation = GenesisSceneProcessObservationV2.model_validate_json(
            self.artifact_store.resolve(observation_ref).path.read_bytes()
        )
        capture = observation.captures[-1]
        now = datetime.now(timezone.utc)
        require_fresh_capture(capture.captured_at, now=now, ttl_seconds=ttl_seconds)
        result = GenesisSceneFreshObservationV1(
            schema_version=SCENE_FRESH_OBSERVATION_ID,
            replay_run_id=run_id,
            execution_receipt=ref,
            process_observation=observation_ref,
            image=capture.image,
            captured_at=capture.captured_at,
            checked_at=now,
            expires_at=capture.captured_at + timedelta(seconds=ttl_seconds),
            simulation_time_s=capture.time_s,
            step=capture.step,
            profile="half_dt",
            simulator_rerun=False,
            candidate_only=candidate,
        )
        with TemporaryDirectory(prefix="genesis-scene-observation-") as directory:
            path = Path(directory) / "fresh-observation.json"
            path.write_text(
                json.dumps(result.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            )
            return self.artifact_store.put_file(
                path,
                name=path.name,
                media_type="application/json",
                schema_version=SCENE_FRESH_OBSERVATION_ID,
            )
