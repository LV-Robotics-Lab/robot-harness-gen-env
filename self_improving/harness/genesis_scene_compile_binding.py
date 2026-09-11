"""Resolve the registered compile invocation behind a scene input, without re-execution."""

from dataclasses import dataclass

from .genesis_scene_execution_v2 import _read
from .image2env_genesis_compile_application import Image2EnvGenesisCompileApplication
from .schemas.genesis_scene_application_v2 import GenesisSceneReplayInputV2
from .schemas.genesis_scene_execution_v2 import GenesisSceneExecutionRequestV2
from .schemas.image2env_genesis_compile import (
    IMAGE_GENESIS_COMPILE_RECEIPT_ID,
    Image2EnvGenesisCompileReceiptV1,
    Image2EnvGenesisCompileRequestV1,
)


@dataclass(frozen=True)
class GenesisSceneCompileBinding:
    artifact_store: object
    compile_application: object
    approved_manifest: object

    @property
    def qualification_ref(self):
        if type(self.compile_application) is not Image2EnvGenesisCompileApplication:
            raise TypeError("scene input requires the exact trusted compile application")
        skills = self.compile_application.skills
        if len(skills) != 1 or (skills[0].skill_id, skills[0].version) != (
            "image2env.compile",
            "2.0.0",
        ):
            raise ValueError("scene input requires a registered native compile Skill")
        return skills[0].qualification_artifact

    def resolve_input(self, parameters):
        """Revalidate a known successful registered terminal before invoking its pure recovery."""
        self.qualification_ref
        if type(parameters) is not GenesisSceneReplayInputV2:
            raise TypeError("scene input requires the exact v2 registered-compile envelope")
        state = self.compile_application.run_state(parameters.compile_run_id)
        invocation = self.compile_application.invocation(parameters.compile_run_id)
        if (
            state is None
            or invocation is None
            or state.status.value != "succeeded"
            or (state.skill_id, state.skill_version) != ("image2env.compile", "2.0.0")
            or state.invocation_digest != invocation.invocation_digest
            or any(event.stage.startswith("qualification_candidate.") for event in state.events)
        ):
            raise ValueError(
                "scene input requires an already registered successful compile terminal"
            )
        original = Image2EnvGenesisCompileRequestV1.model_validate(invocation.effective_parameters)
        recovered = self.compile_application.invoke_typed(
            original, run_id=parameters.compile_run_id
        )
        if recovered != state:
            raise ValueError("compile terminal changed during pure recovery")
        refs = tuple(
            ref for ref in state.artifacts if ref.schema_version == IMAGE_GENESIS_COMPILE_RECEIPT_ID
        )
        if len(refs) != 1:
            raise ValueError("registered compile terminal must retain one native receipt")
        receipt = _read(self.artifact_store, refs[0], Image2EnvGenesisCompileReceiptV1)
        if receipt.model_dump(mode="json") != state.output:
            raise ValueError("registered compile output differs from its native receipt")
        request = _read(
            self.artifact_store, parameters.execution_request, GenesisSceneExecutionRequestV2
        )
        if (
            request.compile_receipt != refs[0]
            or request.scene != receipt.scene
            or request.asset_qualification != receipt.qualification
            or request.runtime_manifest != self.approved_manifest
        ):
            raise ValueError(
                "scene request differs from its registered compile lineage or approved runtime"
            )
        return parameters.execution_request
