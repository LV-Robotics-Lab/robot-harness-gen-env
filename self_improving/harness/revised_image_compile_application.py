"""Durable compile@3 uses the exact revised compiler and existing Harness journal."""

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .event_journal import SQLiteEventJournal
from .registry import HandlerResult, QualificationCandidate, SkillRegistry, StaticDependencyResolver
from .revised_image_compiler import RevisedImage2EnvGenesisCompiler
from .run_store import SQLiteRunStore
from .schemas.common import (
    ArtifactRef,
    DependencyRef,
    RunStatus,
    SkillDescriptor,
    SkillQualification,
)
from .schemas.native_prompt_revision import (
    Image2EnvGenesisCompileReceiptV2,
    Image2EnvGenesisCompileRequestV2,
)
from .schemas.revised_image_compile_application import (
    INVOCATION_ID,
    REPORT_ID,
    RevisedCompileInvocationV1,
    RevisedCompileQualificationCaseV1,
    RevisedCompileQualificationReportV1,
)

SKILL = "image2env.compile@3.0.0"


def _canonical(value):
    return json.dumps(
        value.model_dump(mode="json") if hasattr(value, "model_dump") else value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _sources():
    result = []
    for name in (
        "revised_image_compile_application.py",
        "schemas/revised_image_compile_application.py",
        "registry.py",
        "run_store.py",
        "event_journal.py",
        "typed_qualification_admission.py",
    ):
        path = Path(__file__).parent / name
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        result.append(
            (
                ArtifactRef(
                    name=name.replace("/", "__"),
                    uri="artifact://sha256/" + digest,
                    sha256=digest,
                    bytes=len(raw),
                    media_type="text/x-python",
                    schema_version=None,
                ),
                path,
            )
        )
    return tuple(sorted(result, key=lambda item: item[0].name))


def _dependencies():
    return (
        DependencyRef(
            name="revised_compile_application",
            version="3",
            sha256=hashlib.sha256(
                _canonical([ref.model_dump(mode="json") for ref, _ in _sources()])
            ).hexdigest(),
        ),
    )


class RevisedImageCompileApplication:
    durable_run_state = True

    def __init__(self, state_root, compiler, *, qualification=None):
        if type(compiler) is not RevisedImage2EnvGenesisCompiler:
            raise TypeError("application requires the fixed revised compiler")
        self.compiler = compiler
        self.artifact_store = compiler.artifact_store
        self.run_store = SQLiteRunStore(state_root / "runs.sqlite")
        self._dependency_identity = _dependencies()
        self._registry = SkillRegistry(
            artifact_resolver=self.artifact_store,
            dependency_resolver=StaticDependencyResolver({SKILL: self._dependency_identity}),
            event_sink=SQLiteEventJournal(state_root / "runs.sqlite"),
            clock=lambda: datetime.now(timezone.utc),
            run_id_factory=uuid4,
            run_store=self.run_store,
        )
        self._qualification = None
        self._report_bytes = None
        if qualification is not None:
            self.bind(qualification)

    @property
    def skills(self):
        return self._registry.list()

    def _put(self, model, name):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_bytes(_canonical(model))
            return self.artifact_store.put_file(
                path,
                name=name,
                media_type="application/json",
                schema_version=model.model_config["json_schema_extra"]["$id"],
            )

    def _handler(self, value, context):
        request = value.request
        from .revised_tabletop_scene import read_revision_artifact

        raw = read_revision_artifact(self.artifact_store, request)
        typed_request = Image2EnvGenesisCompileRequestV2.model_validate_json(raw)
        if raw != _canonical(typed_request):
            raise ValueError("invocation request bytes are not canonical")
        receipt_ref = self.compiler.compile(request)
        receipt = Image2EnvGenesisCompileReceiptV2.model_validate_json(
            self.artifact_store.resolve(receipt_ref).path.read_bytes()
        )
        context.emit("revised_compile.output", artifact_refs=(request, receipt_ref, receipt.scene))
        return HandlerResult(
            receipt,
            (request, receipt_ref, receipt.scene, receipt.qualification, *receipt.implementation),
        )

    def evaluate(self, parameters):
        if self.skills:
            raise ValueError("registered application cannot evaluate candidates")
        if type(parameters) is not Image2EnvGenesisCompileRequestV2:
            raise TypeError("revised compile requires exact request")
        candidate = QualificationCandidate(
            skill_id="image2env.compile",
            version="3.0.0",
            input_schema=INVOCATION_ID,
            output_schema="harness.image2env_genesis_compile_receipt.v2",
            max_attempts=1,
        )
        envelope = RevisedCompileInvocationV1(
            schema_version=INVOCATION_ID,
            request=self._put(parameters, "revised-compile-request.json"),
        )
        return self._registry.evaluate_candidate(
            candidate, self._handler, envelope.model_dump(mode="json")
        )

    def _terminal(self, run_id, *, candidate):
        lifecycle = self.run_store.read_controlled_lifecycle(run_id)
        state, invocation = lifecycle.terminal, lifecycle.invocation
        if state is None or invocation is None or state.status is not RunStatus.SUCCEEDED:
            raise ValueError("revised compile has no successful durable terminal")
        if (state.skill_id, state.skill_version) != ("image2env.compile", "3.0.0") or (
            invocation.skill_id,
            invocation.skill_version,
        ) != (state.skill_id, state.skill_version):
            raise ValueError("revised terminal Skill identity differs")
        if (
            invocation.dependencies != self._dependency_identity
            or self._dependency_identity != _dependencies()
        ):
            raise ValueError("revised application implementation dependencies changed")
        if (
            state.invocation_digest != invocation.invocation_digest
            or all(event.stage.startswith("qualification_candidate.") for event in state.events)
            != candidate
        ):
            raise ValueError("revised terminal invocation or candidate status differs")
        output = Image2EnvGenesisCompileReceiptV2.model_validate(state.output)
        refs = tuple(ref for ref in state.artifacts if ref.schema_version == output.schema_version)
        if len(refs) != 1:
            raise ValueError("revised terminal must retain exactly one compile receipt")
        actual, scene = self.compiler.read(refs[0])
        request = Image2EnvGenesisCompileRequestV2.model_validate_json(
            self.artifact_store.resolve(actual.request).path.read_bytes()
        )
        envelope = RevisedCompileInvocationV1.model_validate(invocation.effective_parameters)
        if actual != output or envelope.request != actual.request:
            raise ValueError("revised terminal differs from canonical input/output")
        for ref in state.artifacts:
            self.artifact_store.resolve(ref)
        return refs[0], actual, scene, request, invocation

    def _report(self, run_ids):
        cases = []
        identities = set()
        implementations = set()
        for run_id in sorted(run_ids, key=str):
            ref, receipt, _, request, invocation = self._terminal(run_id, candidate=True)
            identities.add(receipt.implementation_sha256)
            implementations.add(receipt.implementation)
            cases.append(
                RevisedCompileQualificationCaseV1(
                    run_id=run_id,
                    invocation_digest=invocation.invocation_digest,
                    compile_receipt=ref,
                    prompt_revision=request.compile_input.attempt.prompt_revision,
                    predecessor_environment=request.compile_input.attempt.predecessor_environment,
                    original_source=receipt.original_source,
                    asset_qualification=receipt.qualification,
                    scene=receipt.scene,
                )
            )
        if len(identities) != 1 or len(implementations) != 1:
            raise ValueError("qualification requires one exact revised implementation")
        compile_identity = next(iter(identities))
        sources = tuple(
            sorted(
                (*next(iter(implementations)), *(ref for ref, _ in _sources())),
                key=lambda ref: ref.name,
            )
        )
        return RevisedCompileQualificationReportV1(
            schema_version=REPORT_ID,
            skill_ref=SKILL,
            status="pass",
            scope="static_scene_candidate",
            cases=tuple(cases),
            implementation_sources=sources,
            compile_implementation_sha256=compile_identity,
            implementation_sha256=hashlib.sha256(
                _canonical(
                    {
                        "sources": [ref.model_dump(mode="json") for ref in sources],
                        "compile": compile_identity,
                    }
                )
            ).hexdigest(),
        )

    def qualify(self, run_ids):
        report = self._report(run_ids)
        for ref, path in _sources():
            actual = self.artifact_store.put_file(
                path, name=ref.name, media_type=ref.media_type, schema_version=None
            )
            if actual != ref:
                raise ValueError("application source changed while qualifying")
        report_ref = self._put(report, "revised-compile-qualification-report.json")
        qualification = SkillQualification(
            skill_ref=SKILL,
            status="pass",
            deterministic_case_id="two-native-layout-revisions",
            regression_command=(
                "pytest -q tests/self_improving/harness/test_revised_image_compile_application.py"
            ),
            report_sha256=report_ref.sha256,
        )
        ref = self._put(qualification, "revised-compile-qualification.json")
        return self.bind(ref)

    def bind(self, ref):
        from .revised_tabletop_scene import read_revision_artifact

        raw = read_revision_artifact(self.artifact_store, ref)
        qualification = SkillQualification.model_validate_json(raw)
        if (
            ref.schema_version != "harness.skill_qualification.v1"
            or ref.media_type != "application/json"
            or raw != _canonical(qualification)
            or qualification.skill_ref != SKILL
            or qualification.deterministic_case_id != "two-native-layout-revisions"
            or qualification.regression_command
            != "pytest -q tests/self_improving/harness/test_revised_image_compile_application.py"
        ):
            raise ValueError("revised qualification metadata differs")
        report_bytes = self.artifact_store.resolve_digest(qualification.report_sha256).read_bytes()
        report = RevisedCompileQualificationReportV1.model_validate_json(report_bytes)
        expected = self._report(tuple(case.run_id for case in report.cases))
        if report_bytes != _canonical(expected):
            raise ValueError("qualification differs from durable revised candidate evidence")
        for source in report.implementation_sources:
            read_revision_artifact(self.artifact_store, source)
        descriptor = SkillDescriptor(
            skill_id="image2env.compile",
            version="3.0.0",
            mcp_tool_name="image2env_compile_v3_0_0",
            input_schema=INVOCATION_ID,
            output_schema="harness.image2env_genesis_compile_receipt.v2",
            implementation_name="native.revised_image_compile",
            implementation_version="3",
            implementation_sha256=report.implementation_sha256,
            deterministic=True,
            max_attempts=1,
            qualification_artifact=ref,
        )
        if self.skills != (descriptor,):
            self._registry.register(descriptor, self._handler)
        self._qualification = ref
        self._report_bytes = report_bytes
        return descriptor

    def invoke_typed(self, parameters, *, run_id):
        if type(parameters) is not Image2EnvGenesisCompileRequestV2:
            raise TypeError("revised compile requires exact request")
        envelope = RevisedCompileInvocationV1(
            schema_version=INVOCATION_ID,
            request=self._put(parameters, "revised-compile-request.json"),
        )
        return self.invoke(envelope, run_id=run_id)

    def invoke(self, envelope, *, run_id):
        if type(envelope) is not RevisedCompileInvocationV1:
            raise TypeError("revised compile requires exact invocation envelope")
        if self._qualification is None:
            raise ValueError("revised compile requires registered qualification")
        self.bind(self._qualification)
        state = self._registry.invoke_with_run_id(
            "image2env.compile", "3.0.0", envelope.model_dump(mode="json"), run_id=run_id
        )
        if state.status is RunStatus.SUCCEEDED:
            self._terminal(run_id, candidate=False)
        return state

    def read(self, run_id):
        if self._qualification is None:
            raise ValueError("revised compile requires registered qualification")
        self.bind(self._qualification)
        ref, receipt, _, _, _ = self._terminal(run_id, candidate=False)
        return ref, receipt

    def run_state(self, run_id):
        return self.run_store.read_run_state(run_id)

    def invocation(self, run_id):
        return self.run_store.read_invocation(run_id)

    def admit(self):
        """Freeze a separately typed admission from the exact registered evidence."""
        from .schemas.typed_qualification_admission import QualificationImplementationMemberV1
        from .typed_qualification_admission import freeze_typed_qualification_admission

        if self._qualification is None:
            raise ValueError("revised compile requires registered qualification")
        descriptor = self.bind(self._qualification)
        report = RevisedCompileQualificationReportV1.model_validate_json(self._report_bytes)
        descriptor_ref = self._put(descriptor, "revised-compile-descriptor.json")
        digest = hashlib.sha256(self._report_bytes).hexdigest()
        report_ref = ArtifactRef(
            name="revised-compile-qualification-report.json",
            uri="artifact://sha256/" + digest,
            sha256=digest,
            bytes=len(self._report_bytes),
            media_type="application/json",
            schema_version=REPORT_ID,
        )
        return freeze_typed_qualification_admission(
            artifact_store=self.artifact_store,
            child_authority=self.run_store,
            descriptor_ref=descriptor_ref,
            qualification_ref=self._qualification,
            qualification_report_ref=report_ref,
            implementation_members=tuple(
                QualificationImplementationMemberV1(
                    logical_path=ref.name.replace("__", "/"), artifact=ref
                )
                for ref in report.implementation_sources
            ),
        )
