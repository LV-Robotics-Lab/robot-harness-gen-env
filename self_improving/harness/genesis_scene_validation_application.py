"""Rigid-scene validate candidates and qualifications in the shared durable kernel."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .event_journal import SQLiteEventJournal
from .genesis_scene_assessment import GenesisSceneAssessment
from .genesis_scene_execution import _canonical, _publish, _put, _read
from .registry import (
    HandlerResult,
    QualificationCandidate,
    SkillRegistry,
    StaticDependencyResolver,
    _EvidenceInvariantRegistration,
)
from .run_store import SQLiteRunStore
from .schemas.common import DependencyRef, SkillDescriptorV2, SkillQualification
from .schemas.genesis_scene_assessment import (
    SCENE_VALIDATE_INPUT_ID,
    SCENE_VALIDATION_ID,
    SCENE_VALIDATION_QUALIFICATION_ID,
    GenesisSceneValidateInputV1,
    GenesisSceneValidationQualificationV1,
    GenesisSceneValidationV1,
)


@dataclass(frozen=True)
class GenesisSceneValidationHandler:
    assessment: GenesisSceneAssessment
    candidate_only: bool = False

    def __call__(self, parameters, context):
        method = (
            self.assessment.validate_candidate if self.candidate_only else self.assessment.validate
        )
        ref = method(parameters.replay_run_id)
        result = _read(self.assessment.artifact_store, ref, GenesisSceneValidationV1)
        return HandlerResult(result, (ref,))


class GenesisSceneValidationApplication:
    """Candidate evidence never substitutes for the registered source replay terminal."""

    def __init__(self, state_root, assessment):
        if type(assessment) is not GenesisSceneAssessment:
            raise TypeError("validation requires the fixed scene assessment")
        self.assessment = assessment
        self.handler = GenesisSceneValidationHandler(assessment)
        self.candidate_handler = GenesisSceneValidationHandler(assessment, candidate_only=True)
        self.store = assessment.artifact_store
        self.run_store = SQLiteRunStore(state_root / "runs.sqlite")
        self.journal = SQLiteEventJournal(state_root / "runs.sqlite")
        self.implementation = tuple(
            (name, hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest())
            for name in (
                "genesis_scene_assessment.py",
                "genesis_scene_validation_application.py",
                "schemas/genesis_scene_assessment.py",
                "registry.py",
                "run_store.py",
                "event_journal.py",
                "artifacts.py",
            )
        )
        self.implementation_sha256 = hashlib.sha256(_canonical(self.implementation)).hexdigest()
        self.resolver = StaticDependencyResolver(
            {
                "image2env.validate@2.0.0": (
                    DependencyRef(
                        name="genesis_scene_assessment",
                        version="1",
                        sha256=self.implementation_sha256,
                    ),
                )
            }
        )
        self.registry = SkillRegistry(
            artifact_resolver=self.store,
            dependency_resolver=self.resolver,
            event_sink=self.journal,
            clock=lambda: datetime.now(timezone.utc),
            run_id_factory=uuid4,
            run_store=self.run_store,
            evidence_invariant_policy=self,
        )
        self.candidate = QualificationCandidate(
            skill_id="image2env.validate",
            version="2.0.0",
            input_schema=SCENE_VALIDATE_INPUT_ID,
            output_schema=SCENE_VALIDATION_ID,
            max_attempts=1,
        )

    @property
    def skills(self):
        return self.registry.list()

    def run_state(self, run_id):
        return self.run_store.read_run_state(run_id)

    def invocation(self, run_id):
        return self.run_store.read_invocation(run_id)

    def evaluate(self, parameters):
        self._assert_implementation()
        if type(parameters) is not GenesisSceneValidateInputV1:
            raise TypeError("validation requires the exact scene input")
        return self.registry.evaluate_candidate(
            self.candidate, self.candidate_handler, parameters.model_dump(mode="json")
        )

    def _validation_terminal(self, run_id, *, candidate):
        self._assert_implementation()
        state, invocation = (
            self.run_store.read_run_state(run_id),
            self.run_store.read_invocation(run_id),
        )
        if (
            state is None
            or invocation is None
            or state.status.value != "succeeded"
            or state.skill_id != "image2env.validate"
            or state.skill_version != "2.0.0"
            or state.invocation_digest != invocation.invocation_digest
            or invocation.dependencies != self.resolver.dependencies["image2env.validate@2.0.0"]
            or not state.events
            or any(
                e.stage.startswith("qualification_candidate.") != candidate for e in state.events
            )
        ):
            raise ValueError("validation qualification requires a trusted successful candidate")
        refs = [ref for ref in state.artifacts if ref.schema_version == SCENE_VALIDATION_ID]
        if len(refs) != 1:
            raise ValueError("candidate must retain exactly one validation")
        result = _read(self.store, refs[0], GenesisSceneValidationV1)
        parameters = GenesisSceneValidateInputV1.model_validate(invocation.effective_parameters)
        if (
            state.output != result.model_dump(mode="json")
            or result.replay_run_id != parameters.replay_run_id
            or result.candidate_only != candidate
        ):
            raise ValueError("candidate validation input or output differs")
        reader = self.assessment.read_candidate_replay if candidate else self.assessment.read_replay
        source_ref, receipt, closure, source_invocation = reader(result.replay_run_id)
        if (
            source_ref != result.execution_receipt
            or receipt.replay != result.replay
            or receipt.report != result.report
            or closure.request.scene != result.scene
            or source_invocation.invocation_digest != result.invocation_digest
        ):
            raise ValueError("candidate validation lineage differs")
        return refs[0], result

    def read(self, run_id):
        """Recheck the registered validation terminal and its complete source lineage."""
        if len(self.skills) != 1:
            raise ValueError("validation read requires a registered Skill")
        return self._validation_terminal(run_id, candidate=False)

    def qualify(self, run_ids):
        if len(run_ids) != 2 or run_ids[0] == run_ids[1]:
            raise ValueError("validation qualification requires two distinct candidates")
        records = tuple(self._validation_terminal(run_id, candidate=True) for run_id in run_ids)
        if records[0][1].replay_run_id == records[1][1].replay_run_id:
            raise ValueError("validation candidates must assess two independent replay candidates")
        report = GenesisSceneValidationQualificationV1(
            schema_version=SCENE_VALIDATION_QUALIFICATION_ID,
            skill_ref="image2env.validate@2.0.0",
            candidate_runs=tuple(run_ids),
            validations=tuple(item[0] for item in records),
            implementation_sha256=self.implementation_sha256,
        )
        report_ref = _put(self.store, report, "scene-validation-qualification-report.json")
        qualification = SkillQualification(
            skill_ref="image2env.validate@2.0.0",
            status="pass",
            deterministic_case_id="qualified-rigid-scene-deep-assessment",
            regression_command=(
                "pytest -q tests/self_improving/harness/test_genesis_scene_assessment.py"
            ),
            report_sha256=report_ref.sha256,
        )
        ref = _publish(
            self.store,
            _canonical(qualification),
            "scene-validation-qualification.json",
            "harness.skill_qualification.v1",
        )
        return self.bind(ref)

    def verify_and_build(self, request, *, artifact_resolver):
        if artifact_resolver is not self.store:
            raise TypeError("validation qualification must use the configured CAS")
        qualification = _read(self.store, request, SkillQualification)
        if qualification.skill_ref != "image2env.validate@2.0.0" or qualification.status != "pass":
            raise ValueError("validation qualification must pass for this exact Skill")
        data = self.store.resolve_digest(qualification.report_sha256).read_bytes()
        report = GenesisSceneValidationQualificationV1.model_validate_json(data)
        if data != _canonical(report) or report.implementation_sha256 != self.implementation_sha256:
            raise ValueError("validation qualification source identity differs")
        for name, sha in self.implementation:
            if hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() != sha:
                raise ValueError("validation implementation bytes changed")
        if report.candidate_runs[0] == report.candidate_runs[1]:
            raise ValueError("validation qualification repeats a candidate")
        records = tuple(
            self._validation_terminal(run_id, candidate=True) for run_id in report.candidate_runs
        )
        if (
            tuple(item[0] for item in records) != report.validations
            or records[0][1].replay_run_id == records[1][1].replay_run_id
        ):
            raise ValueError("validation qualification differs from repeated evidence")
        descriptor = SkillDescriptorV2(
            skill_id="image2env.validate",
            version="2.0.0",
            mcp_tool_name="image2env_validate_v2_0_0",
            input_schema=SCENE_VALIDATE_INPUT_ID,
            output_schema=SCENE_VALIDATION_ID,
            implementation_name="genesis_scene_assessment",
            implementation_version="1",
            implementation_sha256=self.implementation_sha256,
            reproducibility="evidence_invariant_repeatable",
            max_attempts=1,
            qualification_artifact=request,
        )
        return _EvidenceInvariantRegistration(
            descriptor, self.handler, self.resolver, _canonical(qualification), data
        )

    def bind(self, qualification_ref):
        return self.registry.register_evidence_invariant(qualification_ref)

    def invoke(self, parameters, *, run_id):
        self._assert_implementation()
        if type(parameters) is not GenesisSceneValidateInputV1:
            raise TypeError("validation requires the exact scene input")
        state = self.registry.invoke_with_run_id(
            "image2env.validate", "2.0.0", parameters.model_dump(mode="json"), run_id=run_id
        )
        if state.status.value == "succeeded":
            self.read(run_id)
        return state

    def _assert_implementation(self):
        for name, expected in self.implementation:
            if hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() != expected:
                raise ValueError("validation implementation bytes changed")
