"""Two restricted runtime skills sharing one existing Registry and SQLite journal.

Qualification needs one actual modified replay candidate and a validation candidate
over that saved result. Validation never launches Genesis. No terminal admission,
per-stage database, or alternative workflow state is introduced here.
"""

import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter_ns
from uuid import UUID, uuid4

from .event_journal import SQLiteEventJournal
from .genesis_scene_execution_v3 import (
    GenesisSceneExecutionErrorV3,
    GenesisSceneExecutionProducerV3,
    GenesisSceneExecutionVerifierV3,
)
from .limited_image_preview_execution import validate_preview_receipt
from .qualification import QualificationCheckV1, QualificationReportV1, _source_tree_sha256
from .registry import (
    HandlerResult,
    QualificationCandidate,
    SkillBlocked,
    SkillRegistry,
    StaticDependencyResolver,
    _EvidenceInvariantRegistration,
)
from .revised_image_compile_application import RevisedImageCompileApplication
from .run_store import SQLiteRunStore
from .schemas.common import (
    Blocker,
    DependencyRef,
    RunStatus,
    SkillDescriptorV2,
    SkillQualification,
)
from .schemas.genesis_scene_execution_v3 import (
    GenesisSceneCompileBindingV2,
    GenesisSceneExecutionReceiptV3,
    GenesisSceneExecutionRequestV3,
)
from .schemas.limited_image_preview import LimitedImagePreviewManifestV1
from .schemas.limited_image_preview_execution import (
    LimitedSceneValidateInputV1,
    LimitedSceneValidationV1,
)

REPLAY = "image2env.replay@3.0.0"
VALIDATE = "image2env.validate@3.0.0"
PUBLISH = "image2env.preview_publish@1.0.0"
_MODELS = {
    REPLAY: (GenesisSceneCompileBindingV2, GenesisSceneExecutionReceiptV3),
    VALIDATE: (LimitedSceneValidateInputV1, LimitedSceneValidationV1),
    PUBLISH: (LimitedImagePreviewManifestV1, LimitedImagePreviewManifestV1),
}
_FILES = (
    "limited_image_preview_application.py",
    "limited_image_preview_execution.py",
    "schemas/limited_image_preview_execution.py",
    "genesis_scene_execution_v3.py",
    "genesis_scene_execution_v2.py",
    "genesis_scene_replay_batched.py",
    "genesis_scene_replay.py",
    "registry.py",
    "run_store.py",
    "event_journal.py",
    "limited_image_preview_library.py",
    "schemas/limited_image_preview.py",
)


def _bytes(value):
    return json.dumps(
        value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


@dataclass(frozen=True)
class _Handler:
    application: object
    skill_ref: str

    def __call__(self, value, context):
        app = self.application
        if self.skill_ref == PUBLISH:
            from .limited_image_preview_library import admit_limited_image_preview

            if app.library is None:
                raise ValueError("preview publish requires its configured verified library")
            if value.project_id != app.library.read_index().project_id:
                raise ValueError("preview manifest belongs to another library project")
            ref = app.put(value, "limited-image-preview.json")
            verified = admit_limited_image_preview(
                artifact_store=app.artifact_store,
                compiler=app.compiler_application.compiler,
                manifest=ref,
            )
            publishing_started = perf_counter_ns()
            published = app.library.publish(verified)
            if published.manifest != ref or app.library.get(value.preview_id).manifest_ref != ref:
                raise ValueError("published library entry differs from verified manifest")
            library_publish_ns = perf_counter_ns() - publishing_started
            index = app.library.read_index()
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "library-publication-timing.json"
                path.write_bytes(
                    json.dumps(
                        {
                            "kind": "limited_preview_library_timing.v1",
                            "manifest": ref.model_dump(mode="json"),
                            "preview_id": value.preview_id,
                            "project_id": index.project_id,
                            "index_sha256": index.index_sha256,
                            "index_revision": index.revision,
                            "library_publish_ns": library_publish_ns,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                )
                timing = app.artifact_store.put_file(
                    path, name=path.name, media_type="application/json", schema_version=None
                )
            context.emit("preview.library_published", artifact_refs=(ref, timing))
            return HandlerResult(value, (ref, timing))
        if self.skill_ref == REPLAY:
            request = app.resolve_replay(value)
            try:
                live = app.producer.produce(request, roots=app.roots)
            except GenesisSceneExecutionErrorV3 as error:
                context.emit("preview.replay_failure", artifact_refs=(error.diagnostic_ref,))
                raise
            output = app.read_artifact(live.receipt_ref, GenesisSceneExecutionReceiptV3)
            return HandlerResult(
                output,
                (
                    live.receipt_ref,
                    output.request,
                    output.replay,
                    output.report,
                    output.baseline_preview,
                    output.half_dt_preview,
                    output.retained_index,
                ),
            )
        compiler = app.compiler_application.compiler
        if value.execution_receipt.schema_version.endswith(".v2"):
            compiler = compiler.predecessor_compiler
        result = validate_preview_receipt(app.artifact_store, compiler, value)
        ref = app.put(result, "limited-scene-validation.json")
        refs = (ref, result.execution_receipt, result.scene, result.physical_report)
        context.emit("preview.semantic_validation", artifact_refs=refs)
        if result.status != "passed":
            raise SkillBlocked(
                Blocker(
                    code="PREVIEW_LAYOUT_MISMATCH",
                    message="Actual scene differs from requested XY/yaw",
                    stage="preview.semantic_validation",
                    retryable=False,
                    details={},
                    unknowns=(),
                    artifact_refs=refs,
                )
            )
        return HandlerResult(result, refs)


class LimitedImagePreviewApplication:
    durable_run_state = True

    def __init__(self, state_root, compiler_application, producer, *, roots, library=None):
        if type(compiler_application) is not RevisedImageCompileApplication or type(
            producer
        ) is not (GenesisSceneExecutionProducerV3):
            raise TypeError("shared preview requires exact registered compiler and producer")
        if producer.compiler is not compiler_application.compiler:
            raise ValueError("shared runtime must use the same verified compiler")
        if library is not None:
            from .limited_image_preview_library import LimitedImagePreviewLibrary

            if (
                type(library) is not LimitedImagePreviewLibrary
                or library._artifact_store is not producer.artifact_store
                or library._compiler is not compiler_application.compiler
            ):
                raise TypeError("publication requires this exact CAS and compiler library")
        self.compiler_application = compiler_application
        self.producer = producer
        self.artifact_store = producer.artifact_store
        self.roots = roots
        self.library = library
        self.run_store = SQLiteRunStore(Path(state_root) / "runs.sqlite")
        self._source_identity = self.implementation_sha256
        self._resolver = StaticDependencyResolver(
            {
                skill: (
                    DependencyRef(
                        name="limited_image_preview", version="1", sha256=self._source_identity
                    ),
                )
                for skill in _MODELS
            }
        )
        self._handlers = {skill: _Handler(self, skill) for skill in _MODELS}
        self._registry = SkillRegistry(
            artifact_resolver=self.artifact_store,
            dependency_resolver=self._resolver,
            event_sink=SQLiteEventJournal(Path(state_root) / "runs.sqlite"),
            clock=lambda: datetime.now(timezone.utc),
            run_id_factory=uuid4,
            run_store=self.run_store,
            evidence_invariant_policy=self,
        )
        self._qualifications = {}

    @property
    def implementation_sha256(self):
        root = Path(__file__).parent
        members = [
            (name, hashlib.sha256((root / name).read_bytes()).hexdigest()) for name in _FILES
        ]
        return hashlib.sha256(json.dumps(members, separators=(",", ":")).encode()).hexdigest()

    @property
    def skills(self):
        return self._registry.list()

    def put(self, value, name):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_bytes(_bytes(value))
            return self.artifact_store.put_file(
                path,
                name=name,
                media_type="application/json",
                schema_version=value.model_config.get("json_schema_extra", {}).get(
                    "$id", getattr(value, "schema_version", None)
                ),
            )

    def read_artifact(self, ref, model):
        raw = self.artifact_store.resolve(ref).path.read_bytes()
        result = model.model_validate_json(raw)
        schema = model.model_config.get("json_schema_extra", {}).get(
            "$id", getattr(result, "schema_version", None)
        )
        if raw != _bytes(result) or ref.schema_version != schema:
            raise ValueError("shared preview requires exact canonical typed CAS")
        return result

    def resolve_replay(self, value):
        if type(value) is not GenesisSceneCompileBindingV2:
            raise TypeError("replay needs exact registered compile binding")
        ref, compiled = self.compiler_application.read(value.compile_run_id)
        request = self.read_artifact(value.execution_request, GenesisSceneExecutionRequestV3)
        if (
            ref != value.compile_receipt
            or compiled.request != value.compile_request
            or request.compile_receipt != ref
            or request.scene != compiled.scene
            or request.asset_qualification != compiled.qualification
        ):
            raise ValueError("modified replay binding differs from registered compile")
        return value.execution_request

    def evaluate(self, skill_ref, parameters):
        input_model, output_model = _MODELS[skill_ref]
        if type(parameters) is not input_model:
            raise TypeError("candidate needs exact typed input")
        return self._registry.evaluate_candidate(
            QualificationCandidate(
                skill_id=skill_ref.split("@")[0],
                version=skill_ref.split("@")[1],
                input_schema=input_model.model_config["json_schema_extra"]["$id"],
                output_schema=output_model.model_config["json_schema_extra"]["$id"],
                max_attempts=1,
            ),
            self._handlers[skill_ref],
            parameters.model_dump(mode="json"),
        )

    def _terminal(self, run_id, skill, *, candidate):
        life = self.run_store.read_controlled_lifecycle(run_id)
        if (
            life.invocation is None
            or life.terminal is None
            or life.terminal.status is not RunStatus.SUCCEEDED
        ):
            raise ValueError("qualification requires a completed successful candidate")
        if (
            f"{life.invocation.skill_id}@{life.invocation.skill_version}" != skill
            or life.invocation.dependencies != self._resolver.resolve(skill, None)
            or any(
                event.stage.startswith("qualification_candidate.") != candidate
                for event in life.terminal.events
            )
        ):
            raise ValueError("candidate Skill, implementation or event namespace differs")
        output = _MODELS[skill][1].model_validate(life.terminal.output)
        refs = [
            ref for ref in life.terminal.artifacts if ref.schema_version == output.schema_version
        ]
        if len(refs) != 1 or self.read_artifact(refs[0], type(output)) != output:
            raise ValueError("candidate output differs from durable CAS evidence")
        if skill == REPLAY:
            parameters = GenesisSceneCompileBindingV2.model_validate(
                life.invocation.effective_parameters
            )
            if self.resolve_replay(parameters) != output.request:
                raise ValueError("candidate replay input differs from its output")
            GenesisSceneExecutionVerifierV3(
                self.artifact_store, self.producer.compiler
            ).read_receipt(refs[0])
        elif skill == PUBLISH:
            from .limited_image_preview_library import admit_limited_image_preview

            verified = admit_limited_image_preview(
                artifact_store=self.artifact_store,
                compiler=self.compiler_application.compiler,
                manifest=refs[0],
            )
            if verified.manifest != output:
                raise ValueError("publication differs from its deep CAS evidence")
            if (
                LimitedImagePreviewManifestV1.model_validate(life.invocation.effective_parameters)
                != output
            ):
                raise ValueError("publication input differs from its output")
        else:
            parameters = LimitedSceneValidateInputV1.model_validate(
                life.invocation.effective_parameters
            )
            compiler = self.producer.compiler
            if parameters.execution_receipt.schema_version.endswith(".v2"):
                compiler = compiler.predecessor_compiler
            if validate_preview_receipt(self.artifact_store, compiler, parameters) != output:
                raise ValueError("candidate validation differs from recomputed evidence")
        return life, output

    def _candidate(self, run_id, skill):
        return self._terminal(run_id, skill, candidate=True)

    def _checks(self, replay_candidate, validation_candidate):
        replay_life, _ = self._candidate(replay_candidate, REPLAY)
        _, validation = self._candidate(validation_candidate, VALIDATE)
        refs = [
            r
            for r in replay_life.terminal.artifacts
            if r.schema_version == "harness.genesis_scene_execution_receipt.v3"
        ]
        if len(refs) != 1 or validation.execution_receipt != refs[0]:
            raise ValueError("qualification validation must consume its actual replay candidate")
        return (
            QualificationCheckV1(
                name="fresh_replay_and_saved_validation",
                status="pass",
                evidence={
                    "replay_candidate": str(replay_candidate),
                    "validation_candidate": str(validation_candidate),
                    "receipt": refs[0].model_dump(mode="json"),
                },
            ),
        )

    def qualify(self, replay_candidate, validation_candidate):
        checks = self._checks(replay_candidate, validation_candidate)
        root = Path(__file__).resolve().parents[2]
        for skill in (REPLAY, VALIDATE):
            report = QualificationReportV1(
                schema_version="harness.skill_qualification_report.v1",
                skill_ref=skill,
                status="pass",
                deterministic_case_id="one-fresh-modified-scene",
                regression_command="pytest -q -k limited_image_preview_application",
                implementation_sha256=self.implementation_sha256,
                scene_gen_tree_sha256=_source_tree_sha256(root / "scene_gen", label="scene_gen"),
                ledger_contract_tree_sha256=_source_tree_sha256(
                    root / "self_improving/asset_pipeline/active/1_asset_reuse/lib", label="ledger"
                ),
                checks=checks,
            )
            report_ref = self.put(report, "preview-qualification-report.json")
            qualification = SkillQualification(
                skill_ref=skill,
                status="pass",
                deterministic_case_id=report.deterministic_case_id,
                regression_command=report.regression_command,
                report_sha256=report_ref.sha256,
            )
            ref = self.put(qualification, "preview-qualification.json")
            self.bind(ref)
        return dict(self._qualifications)

    def _publication_checks(self, candidate_run_id):
        _, manifest = self._candidate(candidate_run_id, PUBLISH)
        return (
            QualificationCheckV1(
                name="verified_library_publication",
                status="pass",
                evidence={"candidate": str(candidate_run_id), "preview_id": manifest.preview_id},
            ),
        )

    def qualify_publish(self, candidate_run_id):
        """Qualify publication from one saved verified bootstrap workflow, without Genesis."""
        _, output = self._candidate(candidate_run_id, PUBLISH)
        if self.library is None or self.library.get(output.preview_id).manifest != output:
            raise ValueError("publication candidate was not saved in its qualification library")
        checks = self._publication_checks(candidate_run_id)
        root = Path(__file__).resolve().parents[2]
        report = QualificationReportV1(
            schema_version="harness.skill_qualification_report.v1",
            skill_ref=PUBLISH,
            status="pass",
            deterministic_case_id="verified-preview-publication",
            regression_command="pytest -q -k limited_image_preview_application",
            implementation_sha256=self.implementation_sha256,
            scene_gen_tree_sha256=_source_tree_sha256(root / "scene_gen", label="scene_gen"),
            ledger_contract_tree_sha256=_source_tree_sha256(
                root / "self_improving/asset_pipeline/active/1_asset_reuse/lib", label="ledger"
            ),
            checks=checks,
        )
        report_ref = self.put(report, "preview-publication-qualification-report.json")
        ref = self.put(
            SkillQualification(
                skill_ref=PUBLISH,
                status="pass",
                deterministic_case_id=report.deterministic_case_id,
                regression_command=report.regression_command,
                report_sha256=report_ref.sha256,
            ),
            "preview-publication-qualification.json",
        )
        self.bind(ref)
        return ref

    def verify_and_build(self, ref, *, artifact_resolver):
        if artifact_resolver is not self.artifact_store:
            raise TypeError("qualification must use the shared CAS")
        qualification = self.read_artifact(ref, SkillQualification)
        raw = self.artifact_store.resolve_digest(qualification.report_sha256).read_bytes()
        report = QualificationReportV1.model_validate_json(raw)
        root = Path(__file__).resolve().parents[2]
        if (
            raw != _bytes(report)
            or report.skill_ref != qualification.skill_ref
            or (report.implementation_sha256 != self.implementation_sha256)
            or report.skill_ref not in _MODELS
            or report.deterministic_case_id
            != (
                "verified-preview-publication"
                if report.skill_ref == PUBLISH
                else "one-fresh-modified-scene"
            )
            or qualification.deterministic_case_id != report.deterministic_case_id
            or qualification.regression_command != report.regression_command
            or report.regression_command != "pytest -q -k limited_image_preview_application"
            or report.scene_gen_tree_sha256
            != _source_tree_sha256(root / "scene_gen", label="scene_gen")
            or report.ledger_contract_tree_sha256
            != _source_tree_sha256(
                root / "self_improving/asset_pipeline/active/1_asset_reuse/lib", label="ledger"
            )
            or len(report.checks) != 1
            or report.checks[0].name
            != (
                "verified_library_publication"
                if report.skill_ref == PUBLISH
                else "fresh_replay_and_saved_validation"
            )
        ):
            raise ValueError("shared preview qualification identity changed")
        evidence = report.checks[0].evidence
        expected_checks = (
            self._publication_checks(UUID(evidence["candidate"]))
            if report.skill_ref == PUBLISH
            else self._checks(
                UUID(evidence["replay_candidate"]), UUID(evidence["validation_candidate"])
            )
        )
        if report.checks != expected_checks:
            raise ValueError("qualification candidate checks differ from actual saved evidence")
        input_model, output_model = _MODELS[qualification.skill_ref]
        descriptor = SkillDescriptorV2(
            skill_id=qualification.skill_ref.split("@")[0],
            version=qualification.skill_ref.split("@")[1],
            mcp_tool_name=qualification.skill_ref.split("@")[0].replace(".", "_")
            + "_v"
            + qualification.skill_ref.split("@")[1].replace(".", "_"),
            input_schema=input_model.model_config["json_schema_extra"]["$id"],
            output_schema=output_model.model_config["json_schema_extra"]["$id"],
            implementation_name="limited_image_preview",
            implementation_version="1",
            implementation_sha256=self.implementation_sha256,
            reproducibility="evidence_invariant_repeatable",
            max_attempts=1,
            qualification_artifact=ref,
        )
        return _EvidenceInvariantRegistration(
            descriptor,
            self._handlers[qualification.skill_ref],
            self._resolver,
            _bytes(qualification),
            raw,
        )

    def bind(self, ref):
        descriptor = self._registry.register_evidence_invariant(ref)
        self._qualifications[f"{descriptor.skill_id}@{descriptor.version}"] = ref
        return descriptor

    def invoke(self, skill_ref, parameters, *, run_id):
        if self.implementation_sha256 != self._source_identity:
            raise ValueError("shared preview implementation changed")
        if skill_ref not in self._qualifications:
            raise ValueError("shared preview skill requires registered qualification")
        if type(parameters) is not _MODELS[skill_ref][0]:
            raise TypeError("shared preview requires exact typed input")
        self.bind(self._qualifications[skill_ref])
        state = self._registry.invoke_with_run_id(
            *skill_ref.split("@"), parameters.model_dump(mode="json"), run_id=run_id
        )
        if state.status is RunStatus.SUCCEEDED:
            self._terminal(run_id, skill_ref, candidate=False)
        return state

    def read(self, run_id):
        invocation = self.invocation(run_id)
        skill = f"{invocation.skill_id}@{invocation.skill_version}"
        if skill not in self._qualifications:
            raise ValueError("shared preview skill requires registered qualification")
        self.bind(self._qualifications[skill])
        life, output = self._terminal(run_id, skill, candidate=False)
        refs = [r for r in life.terminal.artifacts if r.schema_version == output.schema_version]
        return refs[0], output

    def invocation(self, run_id):
        return self.run_store.read_invocation(run_id)

    def run_state(self, run_id):
        return self.run_store.read_run_state(run_id)
