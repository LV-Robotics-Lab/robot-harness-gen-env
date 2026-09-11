"""Sealed Golden bindings for the four exact native image applications."""

from dataclasses import dataclass

from .genesis_fresh_capture_application import GenesisFreshCaptureApplication
from .genesis_scene_application_v2 import GenesisSceneApplication
from .genesis_scene_validation_application import GenesisSceneValidationApplication
from .golden_execution import (
    GoldenExecutionBinding,
    _create_golden_execution_binding,
    create_golden_execution_catalog,
)
from .golden_image2env_policy import NativeImageEvidencePolicy
from .image2env_genesis_compile_application import Image2EnvGenesisCompileApplication
from .schemas.common import SkillQualification


@dataclass(frozen=True)
class _NativeApplicationAdapter:
    application: object
    policy: NativeImageEvidencePolicy

    @property
    def skill_ref(self):
        return self.policy.skill_ref

    @property
    def artifact_root(self):
        return self.policy.artifact_store.root

    @property
    def authority_path(self):
        return self.policy.child_authority.path

    @property
    def skills(self):
        skills = self.application.skills
        if len(skills) != 1 or f"{skills[0].skill_id}@{skills[0].version}" != self.skill_ref:
            raise ValueError("native app must retain its one exact registered descriptor")
        ref = skills[0].qualification_artifact
        qualification = SkillQualification.model_validate_json(
            self.policy.artifact_store.resolve(ref).path.read_bytes()
        )
        if qualification.skill_ref != self.skill_ref:
            raise ValueError("native app qualification differs from exact descriptor")
        self.policy.artifact_store.resolve_digest(qualification.report_sha256)
        return skills

    def invoke(self, parameters, *, run_id):
        self.skills
        if type(parameters) is not self.policy.input_model:
            raise TypeError("adapter requires exact native input model")
        method = (
            self.application.invoke_typed
            if type(self.application) is Image2EnvGenesisCompileApplication
            else self.application.invoke
        )
        return method(parameters, run_id=run_id)

    def invocation(self, run_id):
        return self.application.invocation(run_id)

    def run_state(self, run_id):
        return self.application.run_state(run_id)


def _require_native_image_execution_binding(
    binding: GoldenExecutionBinding,
) -> GoldenExecutionBinding:
    """Recover components only from this module's sealed rich native binding."""

    try:
        catalog = create_golden_execution_catalog(bindings=(binding,))
    except (AttributeError, TypeError, ValueError) as error:
        raise TypeError("native projection requires one sealed native image binding") from error
    policy = catalog.policy_for(binding.skill_ref)
    application = catalog.application_for(binding.skill_ref)
    authority = catalog.child_authority_for(binding.skill_ref)
    if (
        type(policy) is not NativeImageEvidencePolicy
        or type(application) is not _NativeApplicationAdapter
        or application.policy is not policy
        or authority.path.resolve() != policy.child_authority.path.resolve()
        or application.artifact_root != policy.artifact_store.root
        or application.authority_path.resolve() != authority.path.resolve()
        or binding.evidence_hook is not None
    ):
        raise TypeError("native projection requires one sealed native image binding")
    return binding


def create_native_image_execution_bindings(
    *, compile_application, replay_application, observation_application, validation_application
):
    """Bind exact registered apps to their existing CAS and child SQLite stores."""
    applications = (
        compile_application,
        replay_application,
        observation_application,
        validation_application,
    )
    expected = (
        Image2EnvGenesisCompileApplication,
        GenesisSceneApplication,
        GenesisFreshCaptureApplication,
        GenesisSceneValidationApplication,
    )
    if any(type(app) is not cls for app, cls in zip(applications, expected, strict=True)):
        raise TypeError("bindings require four exact native image applications")
    if (
        replay_application._binding.compile_application is not compile_application
        or observation_application.replay_application is not replay_application
        or validation_application.assessment.application is not replay_application
    ):
        raise ValueError("native apps must share the exact trusted replay application")
    stores = (
        compile_application._store,
        replay_application.producer.artifact_store,
        observation_application.store,
        validation_application.store,
    )
    if len({store.root for store in stores}) != 1:
        raise ValueError("native apps must share the same CAS authority")
    authorities = (
        compile_application._run_store,
        replay_application.run_store,
        observation_application.run_store,
        validation_application.run_store,
    )
    skills = (
        "image2env.compile@2.0.0",
        "image2env.replay@2.0.0",
        "genesis.observe@3.0.0",
        "image2env.validate@2.0.0",
    )
    predecessors = dict(zip(skills, authorities, strict=True))
    bindings = []
    for app, store, authority, skill in zip(applications, stores, authorities, skills, strict=True):
        policy = NativeImageEvidencePolicy(store, authority, skill, predecessors)
        bindings.append(
            _create_golden_execution_binding(
                policy=policy,
                application=_NativeApplicationAdapter(app, policy),
                child_authority=authority,
            )
        )
    return tuple(bindings)
