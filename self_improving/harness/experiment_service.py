"""Experimental facade over the existing Golden workflow authority."""

import hashlib
import json
import traceback
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from .artifacts import LocalArtifactStore
from .development_execution import (
    DevelopmentSkillDescriptor,
    capture_development_authority,
    create_development_execution_binding,
)
from .event_journal import SQLiteEventJournal
from .experiment_inputs import ExperimentInputs
from .experiment_skill_policy import ExperimentSkillEvidencePolicy
from .golden_execution import create_golden_execution_catalog
from .golden_run import GoldenRunHarness, GoldenRunNotFoundError
from .golden_store import SQLiteGoldenRunStore
from .registry import HandlerResult, SkillRegistry
from .run_store import SQLiteRunStore
from .schemas.common import ArtifactRef
from .schemas.experiment import ExperimentSkillInput, ExperimentSkillOutput
from .schemas.workflow import RunCommandRequest, RunReadRequest, RunStartRequest
from .trusted_state import TrustedWorldState, WorldFactEvidence

STAGES = (
    "interpret",
    "acquire",
    "compile",
    "replay",
    "observe",
    "visual",
    "validate",
    "revise_scene",
    "revise_asset",
)


class _Dependencies:
    def resolve(self, skill, parameters):
        return ()


class _Handler:
    def __init__(self, service, stage):
        self.service, self.stage = service, stage

    def __call__(self, parameters, context):
        from .experiment_stage_handlers import execute_stage

        root = self.service.root / "operations" / str(context.run_id)
        root.mkdir(parents=True, exist_ok=True)
        try:
            if self.stage in {"interpret", "visual", "revise_scene"}:
                from .experiment_model_stages import execute_model_stage

                execute = execute_model_stage
            else:
                execute = execute_stage
            body = execute(
                self.stage,
                parameters,
                store=self.service.store,
                root=root / "stage",
                settings=self.service.settings,
                backend=self.service.backend,
            )
            if body.get("status") == "not_run":
                body = {
                    **body,
                    "visual_status": "not_run",
                    "status": "failed",
                    "error_code": "visual_not_decidable",
                }
        except Exception as error:
            (root / "error.log").write_text(traceback.format_exc())
            body = {
                "status": "failed",
                "error_code": type(error).__name__,
                "reason": str(error),
                "partial_root": str(root),
            }
        receipt = self.service.inputs.publish(body, name=self.stage + ".json")
        return HandlerResult(
            output=ExperimentSkillOutput(stage=self.stage, status=body["status"], receipt=receipt),
            artifacts=(receipt,),
        )


class ExperimentService:
    """Development-only service; CLI/controller remain the only user entry."""

    experiment = True

    def __init__(self, deployment, *, state_root, backend=None):
        self.deployment = Path(deployment).absolute()
        self.settings = json.loads(self.deployment.read_text())
        self.root = Path(state_root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self.principal_id = "harness/experiment"
        self.store = LocalArtifactStore(self.root / "cas")
        self.journal = SQLiteGoldenRunStore(self.root / "golden.sqlite")
        self.inputs = ExperimentInputs(self.store)
        self.backend = backend
        module_root = Path(__file__).absolute().parent
        source_files = tuple(
            sorted(
                set(module_root.glob("experiment*.py"))
                | {
                    module_root / name
                    for name in (
                        "development_execution.py",
                        "registry.py",
                        "golden_run.py",
                        "codex_backend.py",
                        "codex_experiment_proposal.py",
                        "portable_genesis_package.py",
                        "_portable_genesis_child.py",
                        "genesis_scene_cpu_backend.py",
                        "_genesis_scene_child.py",
                        "_genesis_cpu_child.py",
                        "schemas/experiment.py",
                    )
                }
            )
        )
        self.authority = capture_development_authority(
            artifact_store=self.store,
            source_files=source_files,
            configuration=self.deployment,
        )
        child = SQLiteRunStore(self.root / "children.sqlite")
        registry = SkillRegistry(
            artifact_resolver=self.store,
            dependency_resolver=_Dependencies(),
            event_sink=SQLiteEventJournal(child.path),
            clock=lambda: datetime.now(UTC),
            run_id_factory=uuid4,
            run_store=child,
        )
        descriptors, bindings = [], []
        for stage in STAGES:
            descriptor = DevelopmentSkillDescriptor(
                skill_id="experiment." + stage,
                version="1.0.0",
                mcp_tool_name="experiment_" + stage + "_v1_0_0",
                input_schema="harness.experiment_skill_input.v1",
                output_schema="harness.experiment_skill_output.v1",
                implementation_sha256=self.authority.implementation_sha256,
            )
            policy = ExperimentSkillEvidencePolicy(
                "experiment." + stage + "@1.0.0",
                stage,
                self.store,
                self.authority.implementation_sha256,
            )
            descriptors.append(descriptor)
            bindings.append(
                create_development_execution_binding(
                    descriptor=descriptor,
                    handler=_Handler(self, stage),
                    policy=policy,
                    registry=registry,
                    run_store=child,
                    authority=self.authority,
                )
            )
        self.registry_snapshot = self.authority.registry(tuple(descriptors))
        self.catalog = create_golden_execution_catalog(bindings=tuple(bindings))
        self.harness = GoldenRunHarness(
            artifact_store=self.store,
            workflow_store=self.journal,
            execution_catalog=self.catalog,
            development_authority=self.authority,
        )

    def execute_stage(self, workflow_id, stage, idempotency_key, proposal=None):
        if stage not in STAGES:
            raise ValueError("unsupported experiment stage")
        current = self.status(workflow_id=workflow_id)
        for stored in self.journal.read_operations(
            principal_id=self.principal_id, workflow_run_id=UUID(str(workflow_id))
        ):
            command = json.loads(self.store.resolve(stored.operation.command).path.read_bytes())
            if command["idempotency_key"] == idempotency_key:
                if command["skill_ref"] != "experiment." + stage + "@1.0.0" or command[
                    "parameters"
                ]["proposal"] != (proposal or {}):
                    raise ValueError("idempotency key binds a different stage/proposal")
                return self.harness.submit(RunCommandRequest.model_validate(command))
        if stage in {"revise_scene", "revise_asset"}:
            history = current["history"]
            if sum(item["stage"] == stage for item in history) >= 2:
                raise ValueError("repair budget exhausted")
            if not history or history[-1]["status"] != "failed":
                raise ValueError("repair requires latest failed operation")
            failure = history[-1]
            if stage == "revise_scene":
                if (proposal or {}).get("failure_receipt") != failure.get("receipt"):
                    raise ValueError("repair must cite latest failed receipt")
            else:
                body = failure.get("body", {})
                if body.get("repair_kind") != "asset" or not body.get("model_proposal_receipt"):
                    raise ValueError("asset repair requires recorded model advice")
                self.store.resolve(ArtifactRef.model_validate(body["model_proposal_receipt"]))
                if not proposal or proposal != body.get("asset_patch"):
                    raise ValueError("asset patch differs from model advice")
        facts, snapshot = current["facts"], current["snapshot"]
        parameters = ExperimentSkillInput(
            input_ref=ArtifactRef.model_validate(facts["request.experiment_input"]),
            dependencies={
                key: ArtifactRef.model_validate(value)
                for key, value in facts.items()
                if key != "request.experiment_input"
            },
            proposal=proposal or {},
        )
        return self.harness.submit(
            RunCommandRequest(
                schema_version="harness.run_command_request.v1",
                command_id=uuid4(),
                principal_id=self.principal_id,
                workflow_run_id=UUID(str(workflow_id)),
                idempotency_key=idempotency_key,
                skill_ref="experiment." + stage + "@1.0.0",
                expected_workflow_revision=snapshot["revision"],
                expected_state_sha256=snapshot["state_sha256"],
                expected_receipt_head_sha256=snapshot["receipt_head"]["sha256"],
                parameters=parameters.model_dump(mode="json"),
            )
        )

    def _request(self, workflow_id):
        return RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=self.principal_id,
            workflow_run_id=UUID(str(workflow_id)),
        )

    def _state(self, snapshot):
        return TrustedWorldState.model_validate_json(
            self.store.resolve(snapshot.state_ref).path.read_bytes()
        )

    def status(self, *, workflow_id):
        snapshot = self.harness.read(self._request(workflow_id))
        values = {fact.key: fact.value for fact in self._state(snapshot).facts}
        bundle = json.loads(
            self.store.resolve(
                ArtifactRef.model_validate(values["request.experiment_input"])
            ).path.read_bytes()
        )
        history = []
        for stored in self.journal.read_operations(
            principal_id=self.principal_id, workflow_run_id=snapshot.workflow_run_id
        ):
            operation = stored.operation
            command = json.loads(self.store.resolve(operation.command).path.read_bytes())
            item = {
                "stage": operation.skill_ref.split("@")[0].removeprefix("experiment."),
                "status": operation.status.value,
                "operation": operation.model_dump(mode="json"),
                "idempotency_key": command["idempotency_key"],
            }
            if operation.tool_result is not None:
                result = json.loads(self.store.resolve(operation.tool_result).path.read_bytes())
                output = result.get("typed_output")
                if output:
                    receipt = ArtifactRef.model_validate(output["receipt"])
                    item.update(
                        status=output["status"],
                        receipt=receipt.model_dump(mode="json"),
                        body=json.loads(self.store.resolve(receipt).path.read_bytes()),
                    )
            history.append(item)
        return dict(
            workflow_id=str(snapshot.workflow_run_id),
            status=snapshot.status.value,
            route=bundle["route"],
            snapshot=snapshot.model_dump(mode="json"),
            facts=values,
            history=history,
            qualification_status="development_unqualified",
        )

    def start(self, *, user_text=None, idempotency_key, raw_image_path=None, images=(), video=None):
        if raw_image_path is not None:
            images = (*images, raw_image_path)
        bundle = self.inputs.ingest(text=user_text, images=images, video=video)
        input_ref = self.inputs.save(bundle)
        identity = hashlib.sha256((self.principal_id + "\0" + idempotency_key).encode()).digest()
        workflow_id = UUID(bytes=identity[:16], version=4)
        try:
            existing = self.status(workflow_id=workflow_id)
        except GoldenRunNotFoundError:
            existing = None
        if existing is not None:
            if existing["facts"]["request.experiment_input"] != input_ref.model_dump(mode="json"):
                raise ValueError("idempotency key binds different input")
            return existing
        evidence = WorldFactEvidence(
            source_kind="user_input",
            key="request.experiment_input",
            value=input_ref.model_dump(mode="json"),
            observed_at=datetime.now(UTC),
            valid_until=None,
        )
        ref = self.inputs.publish(
            evidence.model_dump(mode="json"),
            name="input-fact.json",
            schema="harness.world_fact_evidence.v1",
        )
        # WorldFactEvidence canonical encoding includes a terminal LF.
        data = self.store.resolve(ref).path.read_bytes() + b"\n"
        ref = self.inputs.publish(
            data, name="input-fact.json", schema="harness.world_fact_evidence.v1"
        )
        harness = GoldenRunHarness(
            artifact_store=self.store,
            workflow_store=self.journal,
            development_authority=self.authority,
            workflow_id_factory=lambda: workflow_id,
        )
        harness.start(
            RunStartRequest(
                schema_version="harness.run_start_request.v1",
                principal_id=self.principal_id,
                idempotency_key=idempotency_key,
                workspace="ephemeral",
                requested_profile="genesis.rigid_scene@1",
                registry_snapshot=self.registry_snapshot,
                initial_fact_evidence=(ref,),
            )
        )
        return self.status(workflow_id=workflow_id)
