"""Revised compile lineage stays explicit without changing frozen scene runners."""

import pytest

from self_improving.harness.schemas.genesis_scene_execution_v3 import (
    GenesisSceneCompileBindingV2,
    GenesisSceneExecutionRequestV3,
)
from tests.self_improving.harness.test_genesis_scene_execution import reference


def test_revised_execution_requires_new_compile_receipt_and_request():
    payload = dict(
        schema_version="harness.genesis_scene_execution_request.v3",
        scene=reference("harness.genesis_scene_spec.v1"),
        compile_receipt=reference("harness.image2env_genesis_compile_receipt.v2"),
        runtime_manifest=reference("harness.genesis_runtime_manifest.v1"),
        asset_qualification=reference("harness.asset_qualification.v1"),
    )
    request = GenesisSceneExecutionRequestV3.model_validate(payload)
    assert request.compile_receipt.schema_version.endswith(".v2")
    payload["compile_receipt"] = reference("harness.image2env_genesis_compile_receipt.v1")
    with pytest.raises(ValueError):
        GenesisSceneExecutionRequestV3.model_validate(payload)


def test_compile_binding_declares_registered_v3_lineage_without_claiming_authority():
    from uuid import uuid4

    binding = GenesisSceneCompileBindingV2(
        schema_version="harness.genesis_scene_compile_binding.v2",
        compile_skill_ref="image2env.compile@3.0.0",
        compile_run_id=uuid4(),
        compile_request=reference("harness.image2env_genesis_compile_request.v2"),
        compile_receipt=reference("harness.image2env_genesis_compile_receipt.v2"),
        execution_request=reference("harness.genesis_scene_execution_request.v3"),
    )
    assert binding.compile_skill_ref == "image2env.compile@3.0.0"
    with pytest.raises(ValueError):
        GenesisSceneCompileBindingV2.model_validate(
            {**binding.model_dump(mode="json"), "compile_skill_ref": "image2env.compile@2.0.0"}
        )
