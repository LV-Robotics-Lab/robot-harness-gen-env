"""Registered compile to fixed scene replay through the existing durable kernel."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .event_journal import SQLiteEventJournal
from .genesis_scene_compile_binding import GenesisSceneCompileBinding
from .genesis_scene_execution_v2 import (
    GenesisSceneExecutionError,
    GenesisSceneExecutionProducerV2,
    GenesisSceneExecutionVerifierV2,
    _canonical,
    _publish,
    _put,
    _read,
)
from .genesis_scene_replay_batched import numpy_runtime_sources
from .registry import (
    HandlerResult,
    QualificationCandidate,
    SkillRegistry,
    StaticDependencyResolver,
    _EvidenceInvariantRegistration,
)
from .run_store import SQLiteRunStore
from .schemas.common import ArtifactRef, DependencyRef, SkillDescriptorV2, SkillQualification
from .schemas.genesis_scene_application_v2 import (
    SCENE_REPLAY_INPUT_V2_ID,
    SCENE_SKILL_REPORT_V2_ID,
    GenesisSceneReplayInputV2,
    GenesisSceneSkillCaseV2,
    GenesisSceneSkillQualificationReportV2,
)
from .schemas.genesis_scene_execution_v2 import (
    SCENE_EXECUTION_RECEIPT_V2_ID,
    GenesisSceneExecutionReceiptV2,
)


@dataclass(frozen=True)
class GenesisSceneReplayHandlerV2:
    producer: GenesisSceneExecutionProducerV2
    roots: object
    binding: GenesisSceneCompileBinding

    def __call__(self, parameters, context):
        request_ref = self.binding.resolve_input(parameters)
        try:
            live = self.producer.produce(request_ref, roots=self.roots)
        except GenesisSceneExecutionError as error:
            context.emit("scene_execution_failure", artifact_refs=(error.diagnostic_ref,))
            raise
        receipt = _read(
            self.producer.artifact_store, live.receipt_ref, GenesisSceneExecutionReceiptV2
        )
        return HandlerResult(receipt, (live.receipt_ref,))


class GenesisSceneApplication:
    """Final replay Skill; every input names a trusted registered compile invocation."""

    def __init__(self, state_root, producer, *, roots, compile_application):
        if type(producer) is not GenesisSceneExecutionProducerV2:
            raise TypeError("scene application requires the fixed v2 execution producer")
        self.producer = producer
        self._binding = GenesisSceneCompileBinding(
            producer.artifact_store, compile_application, producer.approved_manifest
        )
        self._compile_qualification = self._binding.qualification_ref
        self.numeric_identity, self.implementation = _implementation()
        self.implementation_sha256 = hashlib.sha256(
            _canonical(
                {
                    "numeric_identity": self.numeric_identity,
                    "members": [ref.model_dump(mode="json") for ref, _ in self.implementation],
                }
            )
        ).hexdigest()
        self.run_store = SQLiteRunStore(state_root / "runs.sqlite")
        self.journal = SQLiteEventJournal(state_root / "runs.sqlite")
        self.handler = GenesisSceneReplayHandlerV2(producer, roots, self._binding)
        self.resolver = StaticDependencyResolver(
            {
                "image2env.replay@2.0.0": (
                    DependencyRef(
                        name="genesis_scene_execution",
                        version="2",
                        sha256=self.implementation_sha256,
                    ),
                    DependencyRef(
                        name="registered_image_compile",
                        version="2.0.0",
                        sha256=self._compile_qualification.sha256,
                    ),
                )
            }
        )
        self._registry = SkillRegistry(
            artifact_resolver=producer.artifact_store,
            dependency_resolver=self.resolver,
            event_sink=self.journal,
            clock=lambda: datetime.now(timezone.utc),
            run_id_factory=uuid4,
            run_store=self.run_store,
            evidence_invariant_policy=self,
        )
        self._candidate = QualificationCandidate(
            skill_id="image2env.replay",
            version="2.0.0",
            input_schema=SCENE_REPLAY_INPUT_V2_ID,
            output_schema=SCENE_EXECUTION_RECEIPT_V2_ID,
            max_attempts=1,
        )

    @property
    def skills(self):
        return self._registry.list()

    def _assert_implementation(self):
        metadata, members = _implementation()
        if (
            metadata != self.numeric_identity
            or tuple(ref for ref, _ in members) != tuple(ref for ref, _ in self.implementation)
            or self._binding.qualification_ref != self._compile_qualification
        ):
            raise ValueError("scene host implementation or compile qualification changed")

    def resolve_input(self, parameters):
        """Pure known-terminal recovery, shared with downstream registered assessments."""
        self._assert_implementation()
        return self._binding.resolve_input(parameters)

    def evaluate(self, parameters):
        self._assert_implementation()
        if type(parameters) is not GenesisSceneReplayInputV2:
            raise TypeError("scene evaluation requires the exact v2 registered-compile input")
        return self._registry.evaluate_candidate(
            self._candidate, self.handler, parameters.model_dump(mode="json")
        )

    def run_state(self, run_id):
        return self.run_store.read_run_state(run_id)

    def invocation(self, run_id):
        return self.run_store.read_invocation(run_id)

    def _candidate_case(self, run_id):
        state, invocation = self.run_state(run_id), self.invocation(run_id)
        if (
            state is None
            or invocation is None
            or state.status.value != "succeeded"
            or (state.skill_id, state.skill_version) != ("image2env.replay", "2.0.0")
            or state.invocation_digest != invocation.invocation_digest
            or not all(event.stage.startswith("qualification_candidate.") for event in state.events)
        ):
            raise ValueError("scene qualification requires successful trusted candidate terminals")
        if invocation.dependencies != self.resolver.dependencies["image2env.replay@2.0.0"]:
            raise ValueError("candidate implementation or registered compile dependency changed")
        parameters = GenesisSceneReplayInputV2.model_validate(invocation.effective_parameters)
        request_ref = self.resolve_input(parameters)
        refs = tuple(
            ref for ref in state.artifacts if ref.schema_version == SCENE_EXECUTION_RECEIPT_V2_ID
        )
        if len(refs) != 1:
            raise ValueError("scene candidate must retain exactly one v2 execution receipt")
        receipt = _read(self.producer.artifact_store, refs[0], GenesisSceneExecutionReceiptV2)
        if receipt.model_dump(mode="json") != state.output or receipt.request != request_ref:
            raise ValueError("scene candidate output does not bind its registered compile input")
        closure = GenesisSceneExecutionVerifierV2(
            self.producer.artifact_store, self.producer.compiler
        ).read_receipt(refs[0])
        if (
            closure.report.status != "passed"
            or len(closure.report.checks) != 34
            or len(closure.report.media) != 2
        ):
            raise ValueError("scene candidate did not satisfy all physical and media invariants")
        return GenesisSceneSkillCaseV2(
            candidate_run_id=run_id,
            compile_run_id=parameters.compile_run_id,
            invocation_digest=invocation.invocation_digest,
            execution_request=request_ref,
            execution_receipt=refs[0],
        )

    def _report(self, run_ids):
        self._assert_implementation()
        if len(run_ids) != 2 or run_ids[0] == run_ids[1]:
            raise ValueError("scene qualification requires two distinct candidate runs")
        cases = tuple(self._candidate_case(run_id) for run_id in run_ids)
        if cases[0].compile_run_id == cases[1].compile_run_id:
            raise ValueError("scene qualification requires two distinct registered compile runs")
        return GenesisSceneSkillQualificationReportV2(
            schema_version=SCENE_SKILL_REPORT_V2_ID,
            skill_ref="image2env.replay@2.0.0",
            cases=cases,
            approved_manifest=self.producer.approved_manifest,
            compile_qualification=self._compile_qualification,
            implementation=tuple(ref for ref, _ in self.implementation),
            implementation_sha256=self.implementation_sha256,
            **self.numeric_identity,
            invariant="registered_compile_dual_profile_34_checks_and_41_exact_decoded_frames_each",
        )

    def qualify(self, run_ids):
        report = self._report(run_ids)
        for ref, path in self.implementation:
            actual = self.producer.artifact_store.put_file(
                path, name=ref.name, media_type=ref.media_type, schema_version=None
            )
            if actual != ref:
                raise ValueError("host implementation changed while publishing qualification")
        report_ref = _put(
            self.producer.artifact_store, report, "scene-skill-qualification-v2-report.json"
        )
        qualification = SkillQualification(
            skill_ref="image2env.replay@2.0.0",
            status="pass",
            deterministic_case_id="registered-image-finite-table-two-inputs",
            regression_command=(
                "pytest -q tests/self_improving/harness/test_genesis_scene_application_v2.py"
            ),
            report_sha256=report_ref.sha256,
        )
        ref = _publish(
            self.producer.artifact_store,
            _canonical(qualification),
            "scene-skill-qualification-v2.json",
            "harness.skill_qualification.v1",
        )
        return self.bind(ref)

    def verify_and_build(self, request, *, artifact_resolver):
        if (
            type(request) is not ArtifactRef
            or artifact_resolver is not self.producer.artifact_store
        ):
            raise TypeError("scene registration requires the configured CAS qualification")
        qualification = _read(artifact_resolver, request, SkillQualification)
        data = artifact_resolver.resolve_digest(qualification.report_sha256).read_bytes()
        report = GenesisSceneSkillQualificationReportV2.model_validate_json(data)
        expected = self._report(tuple(case.candidate_run_id for case in report.cases))
        if data != _canonical(report) or report != expected:
            raise ValueError(
                "scene qualification differs from trusted registered-compile candidate evidence"
            )
        for ref in report.implementation:
            artifact_resolver.resolve(ref)
        descriptor = SkillDescriptorV2(
            skill_id="image2env.replay",
            version="2.0.0",
            mcp_tool_name="image2env_replay_v2_0_0",
            input_schema=SCENE_REPLAY_INPUT_V2_ID,
            output_schema=SCENE_EXECUTION_RECEIPT_V2_ID,
            implementation_name="genesis_scene_execution",
            implementation_version="2",
            implementation_sha256=self.implementation_sha256,
            reproducibility="evidence_invariant_repeatable",
            max_attempts=1,
            qualification_artifact=request,
        )
        return _EvidenceInvariantRegistration(
            descriptor, self.handler, self.resolver, _canonical(qualification), data
        )

    def bind(self, qualification_ref):
        return self._registry.register_evidence_invariant(qualification_ref)

    def invoke(self, parameters, *, run_id):
        self._assert_implementation()
        if type(parameters) is not GenesisSceneReplayInputV2:
            raise TypeError("scene invocation requires the exact v2 registered-compile input")
        self.resolve_input(parameters)
        result = self._registry.invoke_with_run_id(
            "image2env.replay", "2.0.0", parameters.model_dump(mode="json"), run_id=run_id
        )
        if result.status.value == "succeeded":
            refs = tuple(
                ref
                for ref in result.artifacts
                if ref.schema_version == SCENE_EXECUTION_RECEIPT_V2_ID
            )
            if len(refs) != 1:
                raise ValueError("durable scene terminal lacks its execution receipt")
            receipt = _read(self.producer.artifact_store, refs[0], GenesisSceneExecutionReceiptV2)
            if (
                result.output != receipt.model_dump(mode="json")
                or receipt.request != parameters.execution_request
            ):
                raise ValueError("durable scene terminal differs from its input/receipt")
            GenesisSceneExecutionVerifierV2(
                self.producer.artifact_store, self.producer.compiler
            ).read_receipt(refs[0])
        return result


def _implementation():
    paths = (
        "genesis_scene_application_v2.py",
        "genesis_scene_compile_binding.py",
        "genesis_scene_execution_v2.py",
        "genesis_scene_replay_batched.py",
        "schemas/genesis_scene_application_v2.py",
        "schemas/genesis_scene_execution_v2.py",
        "schemas/genesis_scene_execution.py",
        "schemas/genesis_scene_replay.py",
        "registry.py",
        "run_store.py",
        "event_journal.py",
        "artifacts.py",
        "schemas/base.py",
        "schemas/common.py",
    )
    numeric_identity, sources = numpy_runtime_sources()
    records = list(sources)
    for name in paths:
        path = Path(__file__).parent / name
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        records.append(
            (
                ArtifactRef(
                    name=name.replace("/", "__"),
                    uri="artifact://sha256/" + sha,
                    sha256=sha,
                    bytes=len(data),
                    media_type="text/x-python",
                    schema_version=None,
                ),
                path,
            )
        )
    return numeric_identity, tuple(sorted(records, key=lambda item: item[0].name))
