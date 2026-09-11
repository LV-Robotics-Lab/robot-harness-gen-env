"""Assemble an existing qualified image line without adding state authority."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from .genesis_fresh_capture_application import GenesisFreshCaptureApplication
from .genesis_scene_application_v2 import GenesisSceneApplication
from .genesis_scene_compile_binding import GenesisSceneCompileBinding
from .genesis_scene_validation_application import GenesisSceneValidationApplication
from .image2env_genesis_compile_application import Image2EnvGenesisCompileApplication
from .schemas.common import ArtifactRef
from .schemas.genesis_fresh_capture import (
    FRESH_CAPTURE_INPUT_ID,
    GenesisFreshCaptureInputV1,
    GenesisFreshCaptureReceiptV1,
)
from .schemas.genesis_scene_application_v2 import GenesisSceneReplayInputV2
from .schemas.genesis_scene_assessment import (
    SCENE_VALIDATE_INPUT_ID,
    GenesisSceneValidateInputV1,
    GenesisSceneValidationV1,
)
from .schemas.genesis_scene_execution_v2 import (
    GenesisSceneExecutionReceiptV2,
    GenesisSceneExecutionRequestV2,
)
from .schemas.image2env_genesis_compile import Image2EnvGenesisCompileReceiptV1


class GoldenWorkflowDeliveryExpired(ValueError):
    """Written bytes are retained failure evidence, never a successful delivery."""

    def __init__(self, unpublished_summary_ref=None):
        self.unpublished_summary_ref = unpublished_summary_ref
        super().__init__("workflow observation expired before delivery completed")


@dataclass(frozen=True)
class GoldenWorkflowDelivery:
    """A delivery record; this record does not itself establish physical authority."""

    summary_ref: ArtifactRef
    delivered_at: datetime
    expires_at: datetime

    def __post_init__(self):
        if any(
            value.tzinfo is None or value.utcoffset().total_seconds() != 0
            for value in (self.delivered_at, self.expires_at)
        ):
            raise ValueError("workflow delivery timestamps must be UTC")
        if self.delivered_at > self.expires_at:
            raise GoldenWorkflowDeliveryExpired(self.summary_ref)


class Image2EnvGoldenWorkflow:
    """Call the exact production apps and retain their checked, immutable lineage."""

    def __init__(
        self,
        *,
        compile_application,
        replay_application,
        observation_application,
        validation_application,
    ):
        if type(compile_application) is not Image2EnvGenesisCompileApplication:
            raise TypeError("workflow requires the production compile application")
        if type(replay_application) is not GenesisSceneApplication:
            raise TypeError("workflow requires the production replay application")
        if type(observation_application) is not GenesisFreshCaptureApplication:
            raise TypeError("workflow requires the production observation application")
        if type(validation_application) is not GenesisSceneValidationApplication:
            raise TypeError("workflow requires the production validation application")
        if (
            validation_application.assessment.application is not replay_application
            or observation_application.replay_application is not replay_application
        ):
            raise ValueError("workflow assessment must use the same replay authority")
        self.compile_application = compile_application
        self.replay_application = replay_application
        self.observation_application = observation_application
        self.validation_application = validation_application
        self.assessment = validation_application.assessment
        self.artifact_store = self.assessment.artifact_store

    def run(
        self, parameters, *, replay_run_id, observation_run_id, validation_run_id, ttl_seconds=300
    ):
        """Execute replay, immediately observe, validate, and publish an audit summary."""
        if type(parameters) is not GenesisSceneReplayInputV2:
            raise TypeError("workflow requires the exact registered-compile replay input")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
            raise ValueError("workflow observation TTL must be an integer from 1 to 300")
        applications = (
            ("compile", "image2env.compile", "2.0.0", self.compile_application),
            ("replay", "image2env.replay", "2.0.0", self.replay_application),
            ("observe", "genesis.observe", "3.0.0", self.observation_application),
            ("validate", "image2env.validate", "2.0.0", self.validation_application),
        )
        qualifications = {}
        for stage, skill_id, version, application in applications:
            descriptors = application.skills
            if len(descriptors) != 1 or (
                descriptors[0].skill_id,
                descriptors[0].version,
            ) != (skill_id, version):
                raise ValueError("workflow requires all four qualified native Skills")
            qualifications[stage] = descriptors[0].qualification_artifact.model_dump(mode="json")
        binding = GenesisSceneCompileBinding(
            self.artifact_store,
            self.compile_application,
            self.replay_application.producer.approved_manifest,
        )
        binding.resolve_input(parameters)
        compile_state = self.compile_application.run_state(parameters.compile_run_id)
        replay_state = self.replay_application.invoke(parameters, run_id=replay_run_id)
        if replay_state.status.value != "succeeded":
            raise ValueError("workflow replay did not reach a successful terminal")
        observation_state = self.observation_application.invoke(
            GenesisFreshCaptureInputV1(
                schema_version=FRESH_CAPTURE_INPUT_ID,
                replay_run_id=replay_run_id,
                ttl_seconds=ttl_seconds,
            ),
            run_id=observation_run_id,
        )
        if observation_state.status.value != "succeeded":
            raise ValueError("workflow observation did not reach a successful terminal")
        validation_state = self.validation_application.invoke(
            GenesisSceneValidateInputV1(
                schema_version=SCENE_VALIDATE_INPUT_ID, replay_run_id=replay_run_id
            ),
            run_id=validation_run_id,
        )
        if validation_state.status.value != "succeeded":
            raise ValueError("workflow validation did not reach a successful terminal")
        # Each invoke already deep-verifies its result. Only verify the small
        # immutable outputs and their linkage here; never restart that deep scan.
        compile_ref, compiled = self._receipt(compile_state, Image2EnvGenesisCompileReceiptV1)
        receipt_ref, receipt = self._receipt(replay_state, GenesisSceneExecutionReceiptV2)
        observation_ref, observation = self._receipt(
            observation_state, GenesisFreshCaptureReceiptV1
        )
        validation_ref, validation = self._receipt(validation_state, GenesisSceneValidationV1)
        request = GenesisSceneExecutionRequestV2.model_validate_json(
            self.artifact_store.resolve(receipt.request).path.read_bytes()
        )
        if (
            receipt.request != parameters.execution_request
            or request.compile_receipt != compile_ref
            or request.scene != compiled.scene
            or request.asset_qualification != compiled.qualification
            or validation.replay_run_id != replay_run_id
            or observation.replay_run_id != replay_run_id
            or observation.replay_receipt != receipt_ref
            or validation.execution_receipt != receipt_ref
            or validation.scene != request.scene
            or validation.replay != receipt.replay
            or validation.report != receipt.report
        ):
            raise ValueError("workflow observation, validation or execution lineage differs")
        run_ids = (parameters.compile_run_id, replay_run_id, observation_run_id, validation_run_id)
        checked_states = (compile_state, replay_state, observation_state, validation_state)
        stages = {}
        for (stage, skill_id, version, application), run_id, checked_state in zip(
            applications, run_ids, checked_states, strict=True
        ):
            state = application.run_state(run_id)
            invocation = application.invocation(run_id)
            if (
                state is None
                or invocation is None
                or state.status.value != "succeeded"
                or state != checked_state
                or (state.skill_id, state.skill_version) != (skill_id, version)
                or state.invocation_digest != invocation.invocation_digest
                or not state.events
                or any(e.stage.startswith("qualification_candidate.") for e in state.events)
            ):
                raise ValueError("workflow requires registered durable terminals")
            stages[stage] = {
                "run_id": str(run_id),
                "qualification": qualifications[stage],
                "state": state.model_dump(mode="json"),
                "invocation": invocation.model_dump(mode="json"),
            }
        summary = {
            "format": "image2env.golden_workflow_assembly.v1",
            "authority": "checked_application_lineage_only",
            "scope": "genesis.rigid_scene@1",
            "stages": stages,
            "compile_request": compile_state.output["request"],
            "scene": request.scene.model_dump(mode="json"),
            "compile_receipt": request.compile_receipt.model_dump(mode="json"),
            "execution_request": receipt.request.model_dump(mode="json"),
            "execution_receipt": receipt_ref.model_dump(mode="json"),
            "physics_report": receipt.report.model_dump(mode="json"),
            "baseline_preview": receipt.baseline_preview.model_dump(mode="json"),
            "half_dt_preview": receipt.half_dt_preview.model_dump(mode="json"),
            "retained_index": receipt.retained_index.model_dump(mode="json"),
            "fresh_observation": observation_ref.model_dump(mode="json"),
            "validation": validation_ref.model_dump(mode="json"),
        }
        with TemporaryDirectory(prefix="image2env-golden-workflow-") as directory:
            path = Path(directory) / "golden-workflow.json"
            path.write_bytes(
                json.dumps(
                    summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
            )
            if datetime.now(timezone.utc) > observation.expires_at:
                raise GoldenWorkflowDeliveryExpired()
            summary_ref = self.artifact_store.put_file(
                path, name=path.name, media_type="application/json", schema_version=None
            )
            return GoldenWorkflowDelivery(
                summary_ref, datetime.now(timezone.utc), observation.expires_at
            )

    def _receipt(self, state, model):
        value = model.model_validate(state.output)
        refs = tuple(ref for ref in state.artifacts if ref.schema_version == value.schema_version)
        if len(refs) != 1:
            raise ValueError("workflow terminal must retain exactly one typed receipt")
        raw = self.artifact_store.resolve(refs[0]).path.read_bytes()
        if raw != json.dumps(
            value.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8"):
            raise ValueError("workflow terminal output differs from its canonical CAS receipt")
        return refs[0], value
