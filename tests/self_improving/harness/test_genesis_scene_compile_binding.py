"""Only a trusted registered compile terminal can feed the final scene application."""

from uuid import uuid4

import pytest

from self_improving.harness.genesis_scene_compile_binding import GenesisSceneCompileBinding
from self_improving.harness.schemas.genesis_scene_application_v2 import GenesisSceneReplayInputV2
from tests.self_improving.harness.test_genesis_scene_execution import reference


def test_binding_rejects_caller_supplied_compile_authority():
    value = GenesisSceneReplayInputV2.model_validate(
        dict(
            schema_version="harness.genesis_scene_replay_input.v2",
            compile_run_id=uuid4(),
            execution_request=reference("harness.genesis_scene_execution_request.v2"),
        )
    )
    binding = GenesisSceneCompileBinding(None, None, None)
    with pytest.raises(TypeError, match="exact trusted compile application"):
        binding.resolve_input(value)
