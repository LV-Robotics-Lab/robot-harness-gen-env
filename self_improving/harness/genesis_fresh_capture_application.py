"""Durable genesis.observe@3 over actual independent fixed camera executions."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .event_journal import SQLiteEventJournal
from .genesis_fresh_capture import GenesisFreshCaptureError, GenesisFreshCaptureProducer
from .genesis_scene_execution_v2 import _canonical, _publish, _put, _read
from .registry import (
    HandlerResult,
    QualificationCandidate,
    SkillRegistry,
    StaticDependencyResolver,
    _EvidenceInvariantRegistration,
)
from .run_store import SQLiteRunStore
from .schemas.common import ArtifactRef, DependencyRef, SkillDescriptorV2, SkillQualification
from .schemas.genesis_fresh_capture import (
    FRESH_CAPTURE_INPUT_ID,
    FRESH_CAPTURE_QUALIFICATION_ID,
    FRESH_CAPTURE_RECEIPT_ID,
    GenesisFreshCaptureInputV1,
    GenesisFreshCaptureQualificationV1,
    GenesisFreshCaptureReceiptV1,
)


@dataclass(frozen=True)
class GenesisFreshCaptureHandler:
    producer: GenesisFreshCaptureProducer
    roots: object

    def __call__(self, parameters, context):
        try:
            live = self.producer.produce(parameters, roots=self.roots)
        except GenesisFreshCaptureError as error:
            context.emit("fresh_capture_failure", artifact_refs=(error.diagnostic_ref,))
            raise
        result = _read(self.producer.artifact_store, live.receipt_ref, GenesisFreshCaptureReceiptV1)
        return HandlerResult(result, (live.receipt_ref,))


class GenesisFreshCaptureApplication:
    def __init__(self, state_root, producer, *, roots):
        if type(producer) is not GenesisFreshCaptureProducer:
            raise TypeError("observation v3 requires the fixed fresh capture producer")
        self.producer = producer
        self.replay_application = producer.replay_application
        self.store = producer.artifact_store
        self.implementation = _implementation()
        self.implementation_sha256 = hashlib.sha256(
            _canonical([ref.model_dump(mode="json") for ref, _ in self.implementation])
        ).hexdigest()
        self.replay_qualification = self._replay_qualification()
        self.handler = GenesisFreshCaptureHandler(producer, roots)
        self.run_store = SQLiteRunStore(state_root / "runs.sqlite")
        self.journal = SQLiteEventJournal(state_root / "runs.sqlite")
        self.resolver = StaticDependencyResolver(
            {
                "genesis.observe@3.0.0": (
                    DependencyRef(
                        name="fresh_capture", version="1", sha256=self.implementation_sha256
                    ),
                    DependencyRef(
                        name="fresh_capture_runtime",
                        version="1",
                        sha256=producer.approved_manifest.sha256,
                    ),
                    DependencyRef(
                        name="registered_replay",
                        version="2",
                        sha256=self.replay_qualification.sha256,
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
            skill_id="genesis.observe",
            version="3.0.0",
            input_schema=FRESH_CAPTURE_INPUT_ID,
            output_schema=FRESH_CAPTURE_RECEIPT_ID,
            max_attempts=1,
        )

    def _replay_qualification(self):
        skills = self.replay_application.skills
        if len(skills) != 1 or (skills[0].skill_id, skills[0].version) != (
            "image2env.replay",
            "2.0.0",
        ):
            raise ValueError("fresh observation requires registered replay qualification")
        return skills[0].qualification_artifact

    def _assert_current(self):
        if (
            tuple(ref for ref, _ in _implementation())
            != tuple(ref for ref, _ in self.implementation)
            or self._replay_qualification() != self.replay_qualification
        ):
            raise ValueError("fresh observation implementation or replay qualification changed")

    @property
    def skills(self):
        return self.registry.list()

    def run_state(self, run_id):
        return self.run_store.read_run_state(run_id)

    def invocation(self, run_id):
        return self.run_store.read_invocation(run_id)

    def evaluate(self, parameters):
        self._assert_current()
        if type(parameters) is not GenesisFreshCaptureInputV1:
            raise TypeError("fresh observation requires exact v3 input")
        return self.registry.evaluate_candidate(
            self.candidate, self.handler, parameters.model_dump(mode="json")
        )

    def _terminal(self, run_id, *, candidate):
        self._assert_current()
        state, invocation = self.run_state(run_id), self.invocation(run_id)
        if (
            state is None
            or invocation is None
            or state.status.value != "succeeded"
            or (state.skill_id, state.skill_version) != ("genesis.observe", "3.0.0")
            or state.invocation_digest != invocation.invocation_digest
            or invocation.dependencies != self.resolver.dependencies["genesis.observe@3.0.0"]
            or not state.events
            or any(
                event.stage.startswith("qualification_candidate.") != candidate
                for event in state.events
            )
        ):
            raise ValueError("fresh observation requires a trusted successful terminal")
        refs = [ref for ref in state.artifacts if ref.schema_version == FRESH_CAPTURE_RECEIPT_ID]
        if len(refs) != 1:
            raise ValueError("fresh terminal must retain one receipt")
        result = self.producer.read_receipt(refs[0]).receipt
        parameters = GenesisFreshCaptureInputV1.model_validate(invocation.effective_parameters)
        if (
            result.model_dump(mode="json") != state.output
            or result.replay_run_id != parameters.replay_run_id
            or result.ttl_seconds != parameters.ttl_seconds
        ):
            raise ValueError("fresh terminal input/output differs from verified capture")
        return refs[0], result

    def read(self, run_id):
        if len(self.skills) != 1:
            raise ValueError("fresh observation read requires registered Skill")
        return self._terminal(run_id, candidate=False)

    def _report(self, run_ids):
        if len(run_ids) != 2 or run_ids[0] == run_ids[1]:
            raise ValueError("fresh qualification requires two distinct camera executions")
        records = tuple(self._terminal(run_id, candidate=True) for run_id in run_ids)
        if records[0][1].capture_result == records[1][1].capture_result:
            raise ValueError("fresh qualification cannot reuse a camera execution")
        return GenesisFreshCaptureQualificationV1(
            schema_version=FRESH_CAPTURE_QUALIFICATION_ID,
            skill_ref="genesis.observe@3.0.0",
            candidate_runs=run_ids,
            receipts=tuple(item[0] for item in records),
            capture_manifest=self.producer.approved_manifest,
            replay_qualification=self.replay_qualification,
            implementation=tuple(ref for ref, _ in self.implementation),
            implementation_sha256=self.implementation_sha256,
        )

    def qualify(self, run_ids):
        report = self._report(tuple(run_ids))
        for ref, path in self.implementation:
            stored = self.store.put_file(
                path, name=ref.name, media_type=ref.media_type, schema_version=None
            )
            if stored != ref:
                raise ValueError("fresh implementation changed during qualification")
        report_ref = _put(self.store, report, "fresh-capture-qualification-report.json")
        qualification = SkillQualification(
            skill_ref="genesis.observe@3.0.0",
            status="pass",
            deterministic_case_id="two-fixed-restored-state-camera-executions",
            regression_command=(
                "pytest -q tests/self_improving/harness/test_genesis_fresh_capture.py"
            ),
            report_sha256=report_ref.sha256,
        )
        return self.bind(
            _publish(
                self.store,
                _canonical(qualification),
                "fresh-capture-qualification.json",
                "harness.skill_qualification.v1",
            )
        )

    def verify_and_build(self, request, *, artifact_resolver):
        if type(request) is not ArtifactRef or artifact_resolver is not self.store:
            raise TypeError("fresh qualification requires exact configured CAS reference")
        qualification = _read(self.store, request, SkillQualification)
        if qualification.skill_ref != "genesis.observe@3.0.0" or qualification.status != "pass":
            raise ValueError("fresh qualification must pass for exact v3 Skill")
        raw = self.store.resolve_digest(qualification.report_sha256).read_bytes()
        report = GenesisFreshCaptureQualificationV1.model_validate_json(raw)
        if raw != _canonical(report) or report != self._report(report.candidate_runs):
            raise ValueError("fresh qualification differs from trusted capture candidates")
        for ref in report.implementation:
            self.store.resolve(ref)
        descriptor = SkillDescriptorV2(
            skill_id="genesis.observe",
            version="3.0.0",
            mcp_tool_name="genesis_observe_v3_0_0",
            input_schema=FRESH_CAPTURE_INPUT_ID,
            output_schema=FRESH_CAPTURE_RECEIPT_ID,
            implementation_name="genesis_fresh_capture",
            implementation_version="1",
            implementation_sha256=self.implementation_sha256,
            reproducibility="evidence_invariant_repeatable",
            max_attempts=1,
            qualification_artifact=request,
        )
        return _EvidenceInvariantRegistration(
            descriptor, self.handler, self.resolver, _canonical(qualification), raw
        )

    def bind(self, qualification_ref):
        return self.registry.register_evidence_invariant(qualification_ref)

    def invoke(self, parameters, *, run_id):
        self._assert_current()
        if type(parameters) is not GenesisFreshCaptureInputV1:
            raise TypeError("fresh observation requires exact v3 input")
        existing = self.run_state(run_id)
        result = self.registry.invoke_with_run_id(
            "genesis.observe", "3.0.0", parameters.model_dump(mode="json"), run_id=run_id
        )
        if existing is not None and result.status.value == "succeeded":
            self.read(run_id)
        return result


def _implementation():
    names = (
        "genesis_fresh_capture.py",
        "genesis_fresh_capture_application.py",
        "schemas/genesis_fresh_capture.py",
        "genesis_scene_assessment.py",
        "genesis_scene_execution_v2.py",
        "genesis_runtime_manifest.py",
        "genesis_runtime_launcher.py",
        "genesis_scene_launcher.py",
        "genesis_scene_fresh_capture_launcher.py",
        "gujie_scene_adapter.py",
        "registry.py",
        "run_store.py",
        "event_journal.py",
        "artifacts.py",
    )
    result = []
    for name in names:
        path = Path(__file__).parent / name
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        result.append(
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
    return tuple(sorted(result, key=lambda item: item[0].name))
