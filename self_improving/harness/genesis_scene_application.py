"""Native scene candidate execution in the existing durable Harness kernel."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .event_journal import SQLiteEventJournal
from .genesis_scene_execution import (
    GenesisSceneExecutionError,
    GenesisSceneExecutionProducer,
    GenesisSceneExecutionVerifier,
    _canonical,
    _publish,
    _put,
    _read,
)
from .registry import (
    HandlerResult,
    QualificationCandidate,
    SkillRegistry,
    StaticDependencyResolver,
    _EvidenceInvariantRegistration,
)
from .run_store import SQLiteRunStore
from .schemas.common import ArtifactRef, DependencyRef, SkillDescriptorV2, SkillQualification
from .schemas.genesis_scene_application import (
    SCENE_SKILL_REPORT_ID,
    GenesisSceneSkillQualificationReportV1,
)
from .schemas.genesis_scene_execution import (
    SCENE_EXECUTION_RECEIPT_ID,
    SCENE_EXECUTION_REQUEST_ID,
    GenesisSceneExecutionReceiptV1,
    GenesisSceneExecutionRequestV1,
)


@dataclass(frozen=True)
class GenesisSceneReplayHandler:
    producer: object
    roots: object

    def __call__(self, parameters, context):
        request_ref = _put(self.producer.artifact_store, parameters, "scene-execution-request.json")
        try:
            live = self.producer.produce(request_ref, roots=self.roots)
        except GenesisSceneExecutionError as error:
            context.emit("scene_execution_failure", artifact_refs=(error.diagnostic_ref,))
            raise
        receipt = GenesisSceneExecutionReceiptV1.model_validate_json(
            self.producer.artifact_store.resolve(live.receipt_ref).path.read_bytes()
        )
        return HandlerResult(receipt, (live.receipt_ref,))


class GenesisSceneApplication:
    """Candidate facade; production binding is a separate verified qualification gate."""

    def __init__(self, state_root, producer, *, roots):
        if type(producer) is not GenesisSceneExecutionProducer:
            raise TypeError("scene application requires the fixed execution producer")
        self.producer = producer
        self.run_store = SQLiteRunStore(state_root / "runs.sqlite")
        self.journal = SQLiteEventJournal(state_root / "runs.sqlite")
        self.handler = GenesisSceneReplayHandler(producer, roots)
        self.implementation = _implementation()
        self.implementation_sha256 = hashlib.sha256(
            _canonical([ref.model_dump(mode="json") for ref, _ in self.implementation])
        ).hexdigest()
        self.resolver = StaticDependencyResolver(
            {
                "image2env.replay@2.0.0": (
                    DependencyRef(
                        name="genesis_scene_execution",
                        version="1",
                        sha256=self.implementation_sha256,
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
        self.candidate = QualificationCandidate(
            skill_id="image2env.replay",
            version="2.0.0",
            input_schema=SCENE_EXECUTION_REQUEST_ID,
            output_schema=SCENE_EXECUTION_RECEIPT_ID,
            max_attempts=1,
        )

    @property
    def skills(self):
        return self._registry.list()

    def evaluate(self, parameters):
        if type(parameters) is not GenesisSceneExecutionRequestV1:
            raise TypeError("scene evaluation requires the exact typed execution request")
        return self._registry.evaluate_candidate(
            self.candidate, self.handler, parameters.model_dump(mode="json")
        )

    def run_state(self, run_id):
        return self.run_store.read_run_state(run_id)

    def invocation(self, run_id):
        return self.run_store.read_invocation(run_id)

    def _candidate_closure(self, run_id):
        state = self.run_state(run_id)
        invocation = self.invocation(run_id)
        if (
            state is None
            or invocation is None
            or state.status.value != "succeeded"
            or state.skill_id != "image2env.replay"
            or state.skill_version != "2.0.0"
            or state.invocation_digest != invocation.invocation_digest
            or not all(event.stage.startswith("qualification_candidate.") for event in state.events)
        ):
            raise ValueError("qualification requires a successful candidate in trusted SQLite")
        expected_dependencies = self.resolver.dependencies["image2env.replay@2.0.0"]
        if invocation.dependencies != expected_dependencies:
            raise ValueError("candidate implementation differs from current fixed code")
        refs = [ref for ref in state.artifacts if ref.schema_version == SCENE_EXECUTION_RECEIPT_ID]
        if len(refs) != 1:
            raise ValueError("candidate must retain exactly one execution receipt")
        receipt = _read(self.producer.artifact_store, refs[0], GenesisSceneExecutionReceiptV1)
        if state.output != receipt.model_dump(mode="json"):
            raise ValueError("candidate output differs from its execution receipt")
        closure = GenesisSceneExecutionVerifier(
            self.producer.artifact_store, self.producer.compiler
        ).read_receipt(refs[0])
        if (
            closure.request.model_dump(mode="json") != invocation.effective_parameters
            or closure.request.runtime_manifest != self.producer.approved_manifest
            or closure.report.status != "passed"
            or len(closure.report.checks) != 34
            or len(closure.report.media) != 2
        ):
            raise ValueError("candidate execution request or invariant report differs")
        return refs[0], invocation, closure

    def qualify(self, run_ids):
        """Issue a Skill qualification only from two actual local candidate terminals."""
        if len(run_ids) != 2 or run_ids[0] == run_ids[1]:
            raise ValueError("qualification requires two distinct candidate run IDs")
        records = tuple(self._candidate_closure(run_id) for run_id in run_ids)
        if records[0][2].request != records[1][2].request:
            raise ValueError("qualification candidates must execute the same request")
        for ref, path in self.implementation:
            actual = self.producer.artifact_store.put_file(
                path, name=ref.name, media_type=ref.media_type, schema_version=None
            )
            if actual != ref:
                raise ValueError("scene implementation changed during qualification")
        report = GenesisSceneSkillQualificationReportV1(
            schema_version=SCENE_SKILL_REPORT_ID,
            skill_ref="image2env.replay@2.0.0",
            candidate_runs=tuple(run_ids),
            receipts=tuple(record[0] for record in records),
            invocation_digests=tuple(record[1].invocation_digest for record in records),
            approved_manifest=self.producer.approved_manifest,
            implementation=tuple(ref for ref, _ in self.implementation),
            implementation_sha256=self.implementation_sha256,
            invariant="same_request_dual_profile_34_checks_and_41_exact_decoded_frames_each",
        )
        report_ref = _put(
            self.producer.artifact_store, report, "scene-skill-qualification-report.json"
        )
        qualification = SkillQualification(
            skill_ref="image2env.replay@2.0.0",
            status="pass",
            deterministic_case_id="native-image-finite-table-dual-profile",
            regression_command=(
                "pytest -q tests/self_improving/harness/test_genesis_scene_application.py"
            ),
            report_sha256=report_ref.sha256,
        )
        qualification_ref = _publish(
            self.producer.artifact_store,
            _canonical(qualification),
            "scene-skill-qualification.json",
            "harness.skill_qualification.v1",
        )
        return self._registry.register_evidence_invariant(qualification_ref)

    def verify_and_build(self, request, *, artifact_resolver):
        """Registry policy: recheck local candidate authority and implementation on restart."""
        if (
            type(request) is not ArtifactRef
            or artifact_resolver is not self.producer.artifact_store
        ):
            raise TypeError("scene registration requires the configured CAS qualification")
        qualification = _read(artifact_resolver, request, SkillQualification)
        report_bytes = artifact_resolver.resolve_digest(qualification.report_sha256).read_bytes()
        report = GenesisSceneSkillQualificationReportV1.model_validate_json(report_bytes)
        if (
            report_bytes != _canonical(report)
            or report.implementation_sha256 != self.implementation_sha256
            or report.implementation != tuple(ref for ref, _ in self.implementation)
            or report.approved_manifest != self.producer.approved_manifest
        ):
            raise ValueError("scene qualification implementation or runtime differs")
        for ref, _ in self.implementation:
            artifact_resolver.resolve(ref)
        records = tuple(self._candidate_closure(run_id) for run_id in report.candidate_runs)
        if (
            report.candidate_runs[0] == report.candidate_runs[1]
            or tuple(item[0] for item in records) != report.receipts
            or tuple(item[1].invocation_digest for item in records) != report.invocation_digests
            or records[0][2].request != records[1][2].request
        ):
            raise ValueError("qualification does not match trusted repeated candidate evidence")
        descriptor = SkillDescriptorV2(
            skill_id="image2env.replay",
            version="2.0.0",
            mcp_tool_name="image2env_replay_v2_0_0",
            input_schema=SCENE_EXECUTION_REQUEST_ID,
            output_schema=SCENE_EXECUTION_RECEIPT_ID,
            implementation_name="genesis_scene_execution",
            implementation_version="1",
            implementation_sha256=self.implementation_sha256,
            reproducibility="evidence_invariant_repeatable",
            max_attempts=1,
            qualification_artifact=request,
        )
        return _EvidenceInvariantRegistration(
            descriptor, self.handler, self.resolver, _canonical(qualification), report_bytes
        )

    def bind(self, qualification_ref):
        return self._registry.register_evidence_invariant(qualification_ref)

    def invoke(self, parameters, *, run_id):
        if type(parameters) is not GenesisSceneExecutionRequestV1:
            raise TypeError("scene invocation requires the exact typed execution request")
        result = self._registry.invoke_with_run_id(
            "image2env.replay", "2.0.0", parameters.model_dump(mode="json"), run_id=run_id
        )
        if result.status.value == "succeeded":
            refs = [
                ref for ref in result.artifacts if ref.schema_version == SCENE_EXECUTION_RECEIPT_ID
            ]
            if len(refs) != 1:
                raise ValueError("durable scene terminal lacks its execution receipt")
            GenesisSceneExecutionVerifier(
                self.producer.artifact_store, self.producer.compiler
            ).read_receipt(refs[0])
        return result


def _implementation():
    paths = (
        "genesis_scene_application.py",
        "genesis_scene_execution.py",
        "genesis_scene_replay.py",
        "schemas/genesis_scene_application.py",
        "schemas/genesis_scene_execution.py",
        "schemas/genesis_scene_replay.py",
        "registry.py",
        "run_store.py",
        "event_journal.py",
        "artifacts.py",
        "schemas/base.py",
        "schemas/common.py",
    )
    records = []
    for name in sorted(paths):
        path = Path(__file__).parent / name
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        records.append(
            (
                ArtifactRef(
                    name=name.replace("/", "__"),
                    uri=f"artifact://sha256/{sha}",
                    sha256=sha,
                    bytes=len(data),
                    media_type="text/x-python",
                    schema_version=None,
                ),
                path,
            )
        )
    return tuple(sorted(records, key=lambda item: item[0].name))
