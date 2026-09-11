"""Limited observation projects existing native authority, never arbitrary applications."""

import json
import os
from pathlib import Path
from uuid import UUID

import pytest

from self_improving.harness.limited_observation_registry_projection import (
    LimitedObservationRegistryProjection,
    issue_limited_observation_registry_projection,
    load_limited_observation_registry_projection,
)


def test_projection_rejects_arbitrary_application_before_reading_admission(tmp_path):
    with pytest.raises(TypeError, match="exact registered capture application"):
        issue_limited_observation_registry_projection(
            application=object(), admission=object(), replay_authority=object()
        )


def test_reload_rejects_arbitrary_application_without_cas_or_sqlite_writes(tmp_path):
    with pytest.raises(TypeError, match="exact registered capture application"):
        load_limited_observation_registry_projection(
            application=object(),
            replay_authority=object(),
            admission_ref=object(),
            descriptor_ref=object(),
            qualification_ref=object(),
            qualification_report_ref=object(),
        )
    assert not tuple(tmp_path.iterdir())


def test_projection_cannot_be_constructed_from_caller_documents():
    with pytest.raises(TypeError, match="require their verifier"):
        LimitedObservationRegistryProjection(None, None, None, None, (), None, None, object())


def test_saved_camera_admission_projects_and_reloads_without_execution():
    """Optional isolated M1 deployment: qualification/child read only, no invoke or Genesis."""
    config = os.environ.get("LIMITED_OBSERVATION_PROJECTION_CONFIG")
    if not config:
        pytest.skip("requires operator-isolated saved application settings and admission ref")
    from self_improving.harness.mcp_native_assembly import (
        NativeImageApplicationSettings,
        restore_native_image_applications,
    )
    from self_improving.harness.qualification import QualificationReportV1
    from self_improving.harness.schemas.common import ArtifactRef
    from self_improving.harness.schemas.typed_qualification_admission import (
        TypedQualificationAdmissionV1,
    )
    from self_improving.harness.schemas.workflow_operation import QualifiedSkillExecutionV2
    from self_improving.harness.typed_qualification_admission import (
        verify_typed_qualification_admission,
    )

    payload = json.loads(Path(config).read_bytes())
    settings = NativeImageApplicationSettings.model_validate_json(json.dumps(payload["settings"]))
    # Empty roots guarantee this test cannot launch a simulator even accidentally.
    assert settings.scene_roots == {} and settings.capture_roots == {}
    apps = restore_native_image_applications(settings)
    ref = ArtifactRef.model_validate(payload["admission_ref"])
    admission = verify_typed_qualification_admission(
        artifact_store=apps.store,
        admission=TypedQualificationAdmissionV1.model_validate_json(
            apps.store.resolve(ref).path.read_bytes()
        ),
    )
    projection = issue_limited_observation_registry_projection(
        application=apps.observe, admission=admission, replay_authority=apps.replay.run_store
    )
    report = QualificationReportV1.model_validate_json(
        apps.store.resolve(projection.qualification_report_ref).path.read_bytes()
    )
    evidence = report.checks[0].evidence
    assert report.skill_ref == "genesis.observe@3.0.0"
    assert evidence["typed_admission"] == ref.model_dump(mode="json")
    assert evidence["native_qualification"] == admission.admission.qualification.model_dump(
        mode="json"
    )
    assert evidence["native_evidence_policy"]["implementation_sha256"] == (
        projection.binding.policy.evidence_policy_implementation_sha256
    )
    assert {r.name for r in projection.implementation_sources} >= {
        "limited_observation_registry_projection.py",
        "limited_image_preview_workflow_policies.py",
    }
    restored = load_limited_observation_registry_projection(
        application=apps.observe,
        replay_authority=apps.replay.run_store,
        admission_ref=ref,
        descriptor_ref=projection.descriptor_ref,
        qualification_ref=projection.qualification_ref,
        qualification_report_ref=projection.qualification_report_ref,
    )
    assert restored.registry_entry == projection.registry_entry
    restored.binding.evidence_hook.verify_registry_entry(projection.registry_entry)
    child_id = UUID(payload["registered_child_run_id"])
    lifecycle = apps.observe.run_store.read_controlled_lifecycle(child_id)
    descriptor = restored.binding.application.skills[0]
    policy = restored.binding.policy
    qualified = QualifiedSkillExecutionV2(
        schema_version="harness.qualified_skill_execution.v2",
        skill_ref="genesis.observe@3.0.0",
        descriptor=restored.descriptor_ref,
        input_schema=descriptor.input_schema,
        output_schema=descriptor.output_schema,
        implementation_sha256=descriptor.implementation_sha256,
        qualification_artifact=restored.qualification_ref,
        qualification_report=restored.qualification_report_ref,
        max_attempts=descriptor.max_attempts,
        evidence_policy_id=policy.evidence_policy_id,
        evidence_policy_version=policy.evidence_policy_version,
        evidence_policy_implementation_sha256=policy.evidence_policy_implementation_sha256,
    )
    kwargs = dict(
        lifecycle=lifecycle,
        skill_ref="genesis.observe@3.0.0",
        child_run_id=child_id,
        command_parameters=lifecycle.invocation.effective_parameters,
    )
    assert (
        restored.binding.evidence_hook.verify_child_lifecycle(**kwargs, qualified=qualified) == ()
    )
    with pytest.raises(ValueError, match="policy identity changed"):
        restored.binding.evidence_hook.verify_child_lifecycle(
            **kwargs,
            qualified=qualified.model_copy(
                update={"evidence_policy_implementation_sha256": "0" * 64}
            ),
        )
    with pytest.raises(ValueError, match="typed admission intent"):
        restored.binding.evidence_hook.verify_child_lifecycle(
            **{**kwargs, "child_run_id": UUID("00000000-0000-4000-8000-000000000001")},
            qualified=qualified,
        )
    assert apps.observe.run_store.read_controlled_lifecycle(child_id) == lifecycle
    with pytest.raises(ValueError, match="reference drift"):
        load_limited_observation_registry_projection(
            application=apps.observe,
            replay_authority=apps.replay.run_store,
            admission_ref=ref,
            descriptor_ref=admission.admission.descriptor,
            qualification_ref=projection.qualification_ref,
            qualification_report_ref=projection.qualification_report_ref,
        )
