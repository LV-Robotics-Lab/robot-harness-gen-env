"""Durable exact native image compilation through the existing Harness kernel."""

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .event_journal import SQLiteEventJournal
from .image2env_genesis_compile import Image2EnvGenesisCompiler
from .registry import HandlerResult, QualificationCandidate, SkillRegistry, StaticDependencyResolver
from .run_store import SQLiteRunStore
from .schemas.common import (
    ArtifactRef,
    DependencyRef,
    RunStatus,
    SkillDescriptor,
    SkillQualification,
)
from .schemas.image2env_genesis_compile import (
    Image2EnvGenesisCompileReceiptV1,
    Image2EnvGenesisCompileRequestV1,
)
from .schemas.image2env_genesis_compile_application import (
    Image2EnvGenesisSkillQualificationReportV1,
    NativeCompileQualificationCaseV1,
)


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def _sources():
    paths = (
        "image2env_genesis_compile_application.py",
        "schemas/image2env_genesis_compile_application.py",
        "registry.py",
        "run_store.py",
        "event_journal.py",
    )
    result = []
    for name in paths:
        path = Path(__file__).parent / name
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        result.append(
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
    return tuple(sorted(result, key=lambda item: item[0].name))


def _dependencies():
    return (
        DependencyRef(
            name="native_compile_application",
            version="2",
            sha256=hashlib.sha256(
                _canonical([ref.model_dump(mode="json") for ref, _ in _sources()])
            ).hexdigest(),
        ),
    )


class Image2EnvGenesisCompileApplication:
    """Candidate execution and registration share the existing durable run authority."""

    durable_run_state = True

    def __init__(self, state_root: Path, compiler: Image2EnvGenesisCompiler, *, qualification=None):
        if type(compiler) is not Image2EnvGenesisCompiler:
            raise TypeError("native application requires the fixed compiler")
        self._compiler = compiler
        self._run_store = SQLiteRunStore(state_root / "harness.sqlite3")
        self._store = compiler.artifact_store
        self._qualified_compile_identity = None
        self._dependency_identity = _dependencies()
        self._registry = SkillRegistry(
            artifact_resolver=self._store,
            dependency_resolver=StaticDependencyResolver(
                {"image2env.compile@2.0.0": self._dependency_identity}
            ),
            event_sink=SQLiteEventJournal(state_root / "harness.sqlite3"),
            clock=lambda: datetime.now(timezone.utc),
            run_id_factory=uuid4,
            run_store=self._run_store,
        )
        if qualification is not None:
            self._bind(qualification)

    @property
    def skills(self):
        return self._registry.list()

    def _put(self, value, name, schema):
        with tempfile.TemporaryDirectory(prefix="native-skill-") as directory:
            path = Path(directory) / name
            path.write_bytes(_canonical(value))
            return self._store.put_file(
                path, name=name, media_type="application/json", schema_version=schema
            )

    def _handler(self, value, context):
        request = self._put(
            value.model_dump(mode="json"), "native-image-compile-request.json", value.schema_version
        )
        ref = self._compiler.compile(request)
        receipt = Image2EnvGenesisCompileReceiptV1.model_validate_json(
            self._store.resolve(ref).path.read_bytes()
        )
        if (
            self._qualified_compile_identity is not None
            and receipt.implementation_sha256 != self._qualified_compile_identity
        ):
            raise ValueError("registered compile implementation differs from qualification")
        context.emit("native_compile.candidate", artifact_refs=(request, ref, receipt.scene))
        return HandlerResult(
            receipt, (request, ref, receipt.scene, receipt.qualification, *receipt.implementation)
        )

    def evaluate(self, parameters):
        if self.skills:
            raise ValueError("qualified application cannot evaluate candidates")
        if type(parameters) is not Image2EnvGenesisCompileRequestV1:
            raise TypeError("native compile requires exact request")
        candidate = QualificationCandidate(
            skill_id="image2env.compile",
            version="2.0.0",
            input_schema="harness.image2env_genesis_compile_request.v1",
            output_schema="harness.image2env_genesis_compile_receipt.v1",
            max_attempts=1,
        )
        return self._registry.evaluate_candidate(
            candidate, self._handler, parameters.model_dump(mode="json")
        )

    def _report(self, candidate_run_ids):
        cases = []
        identities = set()
        seeds = set()
        sources = set()
        for run_id in sorted(candidate_run_ids, key=str):
            state = self._run_store.read_run_state(run_id)
            invocation = self._run_store.read_invocation(run_id)
            if (
                state is None
                or invocation is None
                or state.status is not RunStatus.SUCCEEDED
                or state.skill_id != "image2env.compile"
                or state.skill_version != "2.0.0"
                or not all(
                    event.stage.startswith("qualification_candidate.") for event in state.events
                )
            ):
                raise ValueError("qualification candidate is not a successful durable native run")
            if invocation.dependencies != _dependencies():
                raise ValueError(
                    "candidate implementation dependencies differ from current application"
                )
            receipt = Image2EnvGenesisCompileReceiptV1.model_validate(state.output)
            refs = [ref for ref in state.artifacts if ref.schema_version == receipt.schema_version]
            if len(refs) != 1:
                raise ValueError("candidate must retain exactly one compile receipt")
            verified, _ = self._compiler.read(refs[0])
            if verified != receipt or state.invocation_digest != invocation.invocation_digest:
                raise ValueError("candidate receipt or invocation differs")
            request = Image2EnvGenesisCompileRequestV1.model_validate_json(
                self._store.resolve(receipt.request).path.read_bytes()
            )
            if invocation.effective_parameters != request.model_dump(mode="json"):
                raise ValueError("candidate receipt request differs from durable invocation")
            for ref in state.artifacts:
                self._store.resolve(ref)
            identities.add(receipt.implementation_sha256)
            sources.add(request.compile_input.source_media.source.sha256)
            seeds.add(request.compile_input.seed)
            cases.append(
                NativeCompileQualificationCaseV1(
                    run_id=run_id,
                    invocation_digest=invocation.invocation_digest,
                    compile_receipt=refs[0],
                )
            )
        if len(identities) != 1 or len(sources) < 3 or len(seeds) < 3:
            raise ValueError(
                "qualification requires three distinct images and seeds on one implementation"
            )
        implementation_sources = tuple(ref for ref, _ in _sources())
        compile_identity = next(iter(identities))
        digest = hashlib.sha256(
            _canonical(
                {
                    "sources": [ref.model_dump(mode="json") for ref in implementation_sources],
                    "compile": compile_identity,
                }
            )
        ).hexdigest()
        return Image2EnvGenesisSkillQualificationReportV1(
            schema_version="harness.image2env_genesis_skill_qualification_report.v1",
            skill_ref="image2env.compile@2.0.0",
            status="pass",
            scope="static_scene_candidate",
            cases=tuple(cases),
            application_source=next(
                ref
                for ref in implementation_sources
                if ref.name == "image2env_genesis_compile_application.py"
            ),
            implementation_sources=implementation_sources,
            compile_implementation_sha256=compile_identity,
            implementation_sha256=digest,
        )

    def qualify(self, candidate_run_ids):
        report = self._report(candidate_run_ids)
        for ref, path in _sources():
            if (
                self._store.put_file(
                    path, name=ref.name, media_type=ref.media_type, schema_version=None
                )
                != ref
            ):
                raise ValueError("application implementation changed")
        report_ref = self._put(
            report.model_dump(mode="json"),
            "native-compile-qualification-report.json",
            report.schema_version,
        )
        qualification = SkillQualification(
            skill_ref="image2env.compile@2.0.0",
            status="pass",
            deterministic_case_id="native-mouse-three-view-exact-reuse",
            regression_command=(
                "pytest -q tests/self_improving/harness/"
                "test_image2env_genesis_compile_application.py"
            ),
            report_sha256=report_ref.sha256,
        )
        ref = self._put(
            qualification.model_dump(mode="json"),
            "native-compile-qualification.json",
            "harness.skill_qualification.v1",
        )
        return self._bind(ref)

    def _bind(self, ref):
        raw = self._store.resolve(ref).path.read_bytes()
        qualification = SkillQualification.model_validate_json(raw)
        if (
            ref.schema_version != "harness.skill_qualification.v1"
            or ref.media_type != "application/json"
            or ref.uri != f"artifact://sha256/{ref.sha256}"
            or raw != _canonical(qualification.model_dump(mode="json"))
            or qualification.skill_ref != "image2env.compile@2.0.0"
            or qualification.deterministic_case_id != "native-mouse-three-view-exact-reuse"
            or qualification.regression_command
            != (
                "pytest -q tests/self_improving/harness/"
                "test_image2env_genesis_compile_application.py"
            )
        ):
            raise ValueError("native skill qualification metadata differs")
        report_bytes = self._store.resolve_digest(qualification.report_sha256).read_bytes()
        report = Image2EnvGenesisSkillQualificationReportV1.model_validate_json(report_bytes)
        expected = self._report(tuple(case.run_id for case in report.cases))
        if report_bytes != _canonical(expected.model_dump(mode="json")):
            raise ValueError("qualification does not match durable candidate evidence")
        for source in report.implementation_sources:
            self._store.resolve(source)
        descriptor = SkillDescriptor(
            skill_id="image2env.compile",
            version="2.0.0",
            mcp_tool_name="image2env_compile_v2_0_0",
            input_schema="harness.image2env_genesis_compile_request.v1",
            output_schema="harness.image2env_genesis_compile_receipt.v1",
            implementation_name="native.image2env_genesis_compile",
            implementation_version="2",
            implementation_sha256=report.implementation_sha256,
            deterministic=True,
            max_attempts=1,
            qualification_artifact=ref,
        )
        if self.skills != (descriptor,):
            self._registry.register(descriptor, self._handler)
        self._qualified_compile_identity = report.compile_implementation_sha256
        return descriptor

    def invoke_typed(self, parameters, *, run_id):
        if type(parameters) is not Image2EnvGenesisCompileRequestV1:
            raise TypeError("native compile requires exact request")
        if self._dependency_identity != _dependencies():
            raise ValueError("native application implementation changed after registration")
        prior = self._run_store.read_run_state(run_id)
        state = self._registry.invoke_with_run_id(
            "image2env.compile", "2.0.0", parameters.model_dump(mode="json"), run_id=run_id
        )
        if prior is not None and state.status is RunStatus.SUCCEEDED:
            output = Image2EnvGenesisCompileReceiptV1.model_validate(state.output)
            refs = tuple(
                ref for ref in state.artifacts if ref.schema_version == output.schema_version
            )
            if len(refs) != 1:
                raise ValueError("terminal native compile must retain one exact receipt")
            receipt, _ = self._compiler.read(refs[0])
            request = Image2EnvGenesisCompileRequestV1.model_validate_json(
                self._store.resolve(receipt.request).path.read_bytes()
            )
            if receipt != output or request != parameters:
                raise ValueError("terminal native compile output differs from invocation")
        return state

    def run_state(self, run_id):
        return self._run_store.read_run_state(run_id)

    def invocation(self, run_id):
        return self._run_store.read_invocation(run_id)
