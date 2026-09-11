"""Harness-owned preview input and workflow access, backed by the Golden journal.

The user enters through start. Internal agents receive the resulting workflow ID;
they do not own a second state machine or choose a foreign input binding.
"""

import base64
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image

from .artifacts import LocalArtifactStore
from .golden_execution import GoldenExecutionCatalog
from .golden_run import GoldenRunHarness, GoldenRunNotFoundError
from .golden_store import SQLiteGoldenRunStore
from .limited_image_preview_input_evidence import read_limited_preview_input
from .limited_image_preview_inputs import LimitedImagePreviewInputs
from .schemas.common import ArtifactRef, RunState
from .schemas.genesis_fresh_capture import GenesisFreshCaptureInputV1, GenesisFreshCaptureReceiptV1
from .schemas.genesis_scene_application_v2 import GenesisSceneReplayInputV2
from .schemas.genesis_scene_execution_v2 import GenesisSceneExecutionRequestV2
from .schemas.genesis_scene_execution_v3 import (
    GenesisSceneCompileBindingV2,
    GenesisSceneExecutionRequestV3,
)
from .schemas.image2env_genesis_compile import Image2EnvGenesisCompileRequestV1
from .schemas.limited_image_preview_execution import LimitedSceneValidateInputV1, PreviewLayout
from .schemas.limited_image_preview_service import (
    PreviewAsset,
    PreviewResourceLink,
    PreviewServiceResult,
)
from .schemas.native_prompt_revision import (
    Image2EnvCompileV3Input,
    Image2EnvGenesisCompileRequestV2,
    PromptRevisionCompileAttemptV2,
    PromptRevisionV3,
)
from .schemas.resource_access import resource_access_request_sha256
from .schemas.revised_image_compile_application import RevisedCompileInvocationV1
from .schemas.workflow import RunCommandRequest, RunReadRequest, RunStartRequest
from .schemas.workflow_operation import ToolResultV2
from .schemas.x2env_compile_evidence import SceneInterpretationProposalV1, canonical_evidence_sha256
from .trusted_state import TrustedWorldState, WorldFactEvidence


class LimitedImagePreviewService:
    """One principal's server-owned facade over existing workflow state and CAS."""

    def __init__(
        self,
        *,
        artifact_store,
        workflow_store,
        input_builder,
        registry_snapshot,
        principal_id,
        execution_catalog=None,
        scene_manifest=None,
    ):
        if (
            type(artifact_store) is not LocalArtifactStore
            or type(workflow_store) is not SQLiteGoldenRunStore
            or type(input_builder) is not LimitedImagePreviewInputs
            or input_builder.store is not artifact_store
            or execution_catalog is not None
            and type(execution_catalog) is not GoldenExecutionCatalog
        ):
            raise TypeError(
                "preview service requires exact shared Golden, CAS and input authorities"
            )
        self.store = artifact_store
        self.journal = workflow_store
        self.inputs = input_builder
        self.registry_snapshot = registry_snapshot
        self.principal_id = principal_id
        self.execution_catalog = execution_catalog
        self.scene_manifest = scene_manifest
        self.harness = self._harness()

    def _harness(self, workflow_id=None):
        values = dict(
            artifact_store=self.store,
            workflow_store=self.journal,
            execution_catalog=self.execution_catalog,
        )
        if workflow_id is not None:
            values["workflow_id_factory"] = lambda: workflow_id
        return GoldenRunHarness(**values)

    def _put(self, value, name, schema):
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        if schema == "harness.world_fact_evidence.v1":
            raw += b"\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_bytes(raw)
            return self.store.put_file(
                path, name=name, media_type="application/json", schema_version=schema
            )

    def _request(self, workflow_id):
        return RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=self.principal_id,
            workflow_run_id=UUID(str(workflow_id)),
        )

    def _input(self, snapshot):
        state = TrustedWorldState.model_validate_json(
            self.store.resolve(snapshot.state_ref).path.read_bytes()
        )
        facts = [fact for fact in state.facts if fact.key == "request.preview_input"]
        if len(facts) != 1:
            raise ValueError("workflow must retain its one original preview input")
        ref = ArtifactRef.model_validate(facts[0].value)
        value, _ = read_limited_preview_input(self.store, ref, snapshot.workflow_run_id)
        return ref, value

    def _link(self, workflow_id, ref):
        return PreviewResourceLink(
            uri=f"harness://workflows/{workflow_id}/artifacts/{ref.sha256}",
            name=ref.name,
            mime_type=ref.media_type,
        )

    def _projection(self, snapshot, stage, **values):
        _, document = self._input(snapshot)
        prompt = ArtifactRef.model_validate(document["effective_prompt"])
        values.setdefault("failure_receipts", self._failures(snapshot))
        values.setdefault("resource_links", (self._link(snapshot.workflow_run_id, prompt),))
        return PreviewServiceResult(
            workflow_id=snapshot.workflow_run_id,
            revision=snapshot.revision,
            stage=stage,
            status="active",
            snapshot=snapshot,
            source_prompt=prompt,
            **values,
        ).model_dump(mode="json")

    def start(self, *, user_text, idempotency_key, raw_image_ref=None, raw_image_path=None):
        if (raw_image_ref is None) == (raw_image_path is None):
            raise ValueError("start requires exactly one raw image source")
        if raw_image_path is not None:
            path = Path(raw_image_path)
            if path.is_symlink() or not path.is_file():
                raise ValueError("raw image must be an existing regular file")
            with Image.open(path) as image:
                media_type = Image.MIME.get(image.format)
            if media_type is None or not media_type.startswith("image/"):
                raise ValueError("raw file has no supported image media type")
            raw_image_ref = self.store.put_file(
                path,
                name=path.name,
                media_type=media_type,
                schema_version=None,
            )
        raw_image_ref = ArtifactRef.model_validate(raw_image_ref)
        identity = json.dumps([self.principal_id, idempotency_key], separators=(",", ":")).encode()
        workflow_id = UUID(
            bytes=hashlib.sha256(b"limited-preview-start\0" + identity).digest()[:16], version=4
        )
        ref = self.inputs.start(
            workflow_id=workflow_id, raw_image_ref=raw_image_ref, user_text=user_text
        )
        try:
            snapshot = self.harness.read(self._request(workflow_id))
        except GoldenRunNotFoundError:
            snapshot = None
        if snapshot is not None:
            original, _ = self._input(snapshot)
            if original != ref:
                raise ValueError("start idempotency key is already bound to different input")
            return self._projection(snapshot, "start")
        document, _ = read_limited_preview_input(self.store, ref, workflow_id)
        values = {
            "request.preview_input": ref.model_dump(mode="json"),
            "request.image_source": document["source_media"],
            "request.objective": user_text,
        }
        now = datetime.now(timezone.utc)
        facts = tuple(
            self._put(
                WorldFactEvidence(
                    source_kind="user_input",
                    key=key,
                    value=value,
                    observed_at=now,
                    valid_until=None,
                ).model_dump(mode="json"),
                "preview-input-fact.json",
                "harness.world_fact_evidence.v1",
            )
            for key, value in values.items()
        )
        request = RunStartRequest(
            schema_version="harness.run_start_request.v1",
            principal_id=self.principal_id,
            idempotency_key=idempotency_key,
            workspace="production",
            requested_profile="genesis.rigid_scene@1",
            registry_snapshot=self.registry_snapshot,
            initial_fact_evidence=tuple(
                sorted(facts, key=lambda r: (r.sha256, r.bytes, r.media_type, r.schema_version))
            ),
        )
        snapshot = self._harness(workflow_id).start(request)
        return self._projection(snapshot, "start")

    def asset_lookup(self, *, workflow_id, query="mouse"):
        snapshot = self.harness.read(self._request(workflow_id))
        ref, document = self._input(snapshot)
        catalog = ArtifactRef.model_validate(document["catalog_ref"])
        delivery = self.harness.read_workflow_artifact_audited(
            self._request(workflow_id),
            sha256=catalog.sha256,
            resource_uri=self._link(snapshot.workflow_run_id, catalog).uri,
        )
        lookup = self.inputs.lookup(
            workflow_id=snapshot.workflow_run_id, input_ref=ref, query=query
        )
        selected = json.loads(self.store.resolve(lookup).path.read_bytes())
        return self._projection(
            snapshot,
            "asset_lookup",
            asset=PreviewAsset(
                asset_id=selected["asset_id"],
                label="mouse",
                representation=selected["representation"],
                qualification=selected["qualification"],
            ),
            artifacts=(catalog, delivery.access_receipt),
            resource_links=(self._link(snapshot.workflow_run_id, catalog),),
        )

    def status(self, *, workflow_id):
        snapshot = self.harness.read(self._request(workflow_id))
        entries = self.journal.read_operations(
            principal_id=self.principal_id, workflow_run_id=snapshot.workflow_run_id
        )
        if entries:
            return self._stage(entries[-1].operation, "status")
        return self._projection(snapshot, "status")

    def _receipts(self, snapshot):
        refs = {}
        ref = snapshot.receipt_head
        seen = set()
        while ref.schema_version != "harness.workflow_start_receipt.v1":
            if ref.sha256 in seen:
                raise ValueError("workflow receipt chain contains a cycle")
            seen.add(ref.sha256)
            value = json.loads(self.store.resolve(ref).path.read_bytes())
            if ref.schema_version == "harness.workflow_operation_receipt.v2":
                refs[value["operation_id"]] = ref
            ref = ArtifactRef.model_validate(value["predecessor_receipt"])
        return refs

    def _failures(self, snapshot):
        receipts = self._receipts(snapshot)
        return tuple(
            sorted(
                (
                    receipts[str(item.operation.operation_id)]
                    for item in self.journal.read_operations(
                        principal_id=self.principal_id, workflow_run_id=snapshot.workflow_run_id
                    )
                    if item.operation.status.value in {"failed", "blocked"}
                ),
                key=lambda ref: ref.sha256,
            )
        )

    def _read_model(self, ref, model):
        return model.model_validate_json(self.store.resolve(ref).path.read_bytes())

    def _existing(self, workflow_id, skill, key):
        stored = self.journal.read_operation(
            principal_id=self.principal_id,
            workflow_run_id=UUID(str(workflow_id)),
            skill_ref=skill,
            idempotency_key=key,
        )
        if stored is None:
            return None
        return stored.operation, self._read_model(stored.operation.command, RunCommandRequest)

    def _stage(self, operation, stage):
        snapshot = self.harness.read(self._request(operation.workflow_run_id))
        result = (
            None
            if operation.tool_result is None
            else self._read_model(operation.tool_result, ToolResultV2)
        )
        refs = (
            ()
            if result is None
            else (operation.tool_result, result.trusted_receipt, *result.diagnostics)
        )
        status = operation.status.value
        values = dict(
            operation=operation,
            artifacts=refs,
            resource_links=tuple(self._link(snapshot.workflow_run_id, r) for r in refs),
            elapsed_seconds=0.0
            if operation.ended_at is None
            else (operation.ended_at - operation.started_at).total_seconds(),
        )
        if status in {"failed", "blocked"}:
            values["failure_code"] = (
                result.blocker.code if result and result.blocker else "PREVIEW_STAGE_FAILED"
            )
            values["failure_message"] = (
                str(result.blocker.details.get("error", result.blocker.message))
                if result and result.blocker
                else "Preview stage did not complete"
            )
            status = "failed"
        if (
            operation.skill_ref == "genesis.observe@3.0.0"
            and result is not None
            and status == "succeeded"
        ):
            observed = GenesisFreshCaptureReceiptV1.model_validate(result.typed_output)
            receipt = next(
                r
                for r in self._read_model(result.run_state, RunState).artifacts
                if r.schema_version == observed.schema_version
            )
            margin = (observed.expires_at - datetime.now(timezone.utc)).total_seconds()
            values.update(
                observation=dict(
                    receipt=receipt,
                    image=observed.image,
                    captured_at=observed.captured_at,
                    expires_at=observed.expires_at,
                    authority=observed.authority,
                ),
                ttl_remaining_seconds=margin,
                resource_links=(
                    self._link(snapshot.workflow_run_id, receipt),
                    self._link(snapshot.workflow_run_id, observed.image),
                ),
            )
            if margin < 0:
                status = "waiting_input"
                values.update(
                    failure_code="PREVIEW_OBSERVATION_EXPIRED",
                    failure_message="Observation expired before delivery",
                )
        _, document = self._input(snapshot)
        context = [
            ArtifactRef.model_validate(document["effective_prompt"]),
            *self._failures(snapshot),
        ]
        for entry in self.journal.read_operations(
            principal_id=self.principal_id, workflow_run_id=snapshot.workflow_run_id
        ):
            prior = entry.operation
            if prior.tool_result is None:
                continue
            prior_result = self._read_model(prior.tool_result, ToolResultV2)
            context.extend(prior_result.diagnostics)
            if prior.skill_ref == "image2env.replay@2.0.0" and prior.status.value == "succeeded":
                terminal = self._read_model(prior_result.run_state, RunState)
                context.extend(
                    r
                    for r in terminal.artifacts
                    if r.schema_version == "harness.genesis_scene_execution_receipt.v2"
                )
        links = {link.uri: link for link in values["resource_links"]}
        for ref in context:
            link = self._link(snapshot.workflow_run_id, ref)
            links[link.uri] = link
        values["resource_links"] = tuple(links.values())
        return PreviewServiceResult(
            workflow_id=snapshot.workflow_run_id,
            revision=snapshot.revision,
            snapshot=snapshot,
            stage=stage,
            status=status,
            source_prompt=document["effective_prompt"],
            failure_receipts=self._failures(snapshot),
            **values,
        ).model_dump(mode="json")

    def _submit(self, snapshot, skill, parameters, key, stage):
        request = RunCommandRequest(
            schema_version="harness.run_command_request.v1",
            command_id=uuid4(),
            principal_id=self.principal_id,
            workflow_run_id=snapshot.workflow_run_id,
            idempotency_key=key,
            expected_workflow_revision=snapshot.revision,
            expected_state_sha256=snapshot.state_sha256,
            expected_receipt_head_sha256=snapshot.receipt_head.sha256,
            skill_ref=skill,
            parameters=parameters.model_dump(mode="json"),
        )
        return self._stage(self.harness.submit(request), stage)

    def _parent(self, snapshot):
        operations = self.journal.read_operations(
            principal_id=self.principal_id,
            workflow_run_id=snapshot.workflow_run_id,
        )
        if not operations:
            raise ValueError("stage requires a committed predecessor")
        operation = operations[-1].operation
        if (
            operation.committed_revision != snapshot.revision
            or operation.status.value != "succeeded"
        ):
            raise ValueError("stage requires the current successful predecessor")
        return operation, self._read_model(operation.tool_result, ToolResultV2)

    def compile(
        self,
        *,
        workflow_id,
        asset_id,
        idempotency_key,
        expected_skill_ref,
        position_x_m=None,
        position_y_m=None,
        yaw_rad=None,
    ):
        if expected_skill_ref == "image2env.compile@3.0.0":
            return self._compile_revised(
                workflow_id, asset_id, idempotency_key, position_x_m, position_y_m, yaw_rad
            )
        if expected_skill_ref != "image2env.compile@2.0.0":
            raise ValueError("compile requires its exact limited Skill identity")
        if any(v is not None for v in (position_x_m, position_y_m, yaw_rad)):
            raise ValueError("initial compile preserves source layout and accepts no explicit pose")
        snapshot = self.harness.read(self._request(workflow_id))
        input_ref, document = self._input(snapshot)
        previous = self._existing(workflow_id, expected_skill_ref, idempotency_key)
        if previous is not None:
            lookup = self.inputs.lookup(workflow_id=snapshot.workflow_run_id, input_ref=input_ref)
            if json.loads(self.store.resolve(lookup).path.read_bytes())["asset_id"] != asset_id:
                raise ValueError("retry asset differs from original selection")
            return self._stage(self.harness.submit(previous[1]), "compile")
        if snapshot.revision != 0:
            raise ValueError("initial compile is only available at the initial workflow head")
        catalog = ArtifactRef.model_validate(document["catalog_ref"])
        snapshot_bytes = (
            json.dumps(
                snapshot.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
            + b"\n"
        )
        access_id = resource_access_request_sha256(
            principal_id=self.principal_id,
            workflow_run_id=snapshot.workflow_run_id,
            resource_uri=self._link(snapshot.workflow_run_id, catalog).uri,
            resource=catalog,
            observed_snapshot_sha256=hashlib.sha256(snapshot_bytes).hexdigest(),
        )
        if (
            self.journal.read_resource_access(
                principal_id=self.principal_id,
                workflow_run_id=snapshot.workflow_run_id,
                access_identity_sha256=access_id,
            )
            is None
        ):
            raise ValueError("compile requires this workflow's audited lookup first")
        lookup = self.inputs.lookup(workflow_id=snapshot.workflow_run_id, input_ref=input_ref)
        ref = self.inputs.build_initial(
            workflow_id=snapshot.workflow_run_id,
            input_ref=input_ref,
            lookup_ref=lookup,
            asset_id=asset_id,
        )
        return self._submit(
            snapshot,
            expected_skill_ref,
            self._read_model(ref, Image2EnvGenesisCompileRequestV1),
            idempotency_key,
            "compile",
        )

    def _compile_revised(self, workflow_id, asset_id, key, x, y, yaw):
        if any(v is None for v in (x, y, yaw)):
            raise ValueError("revised compile requires explicit XY and yaw")
        layout = PreviewLayout(x_m=x, y_m=y, yaw_rad=yaw)
        snapshot = self.harness.read(self._request(workflow_id))
        input_ref, _ = self._input(snapshot)
        lookup = self.inputs.lookup(workflow_id=snapshot.workflow_run_id, input_ref=input_ref)
        if json.loads(self.store.resolve(lookup).path.read_bytes())["asset_id"] != asset_id:
            raise ValueError("revised compile cannot replace the selected qualified asset")
        previous = self._existing(workflow_id, "image2env.compile@3.0.0", key)
        if previous is not None:
            envelope = RevisedCompileInvocationV1.model_validate(previous[1].parameters)
            revised = self._read_model(envelope.request, Image2EnvGenesisCompileRequestV2)
            target = revised.compile_input.attempt.revision.layout_proposal
            if target.position_xy_m != (layout.x_m, layout.y_m) or target.yaw_rad != layout.yaw_rad:
                raise ValueError("revised compile retry changed committed layout")
            return self._stage(self.harness.submit(previous[1]), "compile")
        if snapshot.receipt_head.schema_version != "harness.prompt_revision.v3":
            raise ValueError("revised compile requires the current committed PromptRevisionV3")
        revision = self._read_model(snapshot.receipt_head, PromptRevisionV3)
        if (
            revision.layout_proposal.position_xy_m != (layout.x_m, layout.y_m)
            or revision.layout_proposal.yaw_rad != layout.yaw_rad
        ):
            raise ValueError("compile arguments must consume committed revision layout exactly")
        initial = [
            entry.operation
            for entry in self.journal.read_operations(
                principal_id=self.principal_id,
                workflow_run_id=snapshot.workflow_run_id,
            )
            if entry.operation.skill_ref == "image2env.compile@2.0.0"
            and entry.operation.status.value == "succeeded"
        ]
        if len(initial) != 1:
            raise ValueError("revised compile requires this workflow's unique original compile")
        original_command = self._read_model(initial[0].command, RunCommandRequest)
        original = Image2EnvGenesisCompileRequestV1.model_validate(original_command.parameters)
        proposal = self._read_model(
            original.compile_input.interpretation_proposal, SceneInterpretationProposalV1
        ).model_dump(mode="json")
        proposal["effective_prompt"] = revision.revised_prompt.model_dump(mode="json")
        proposal["proposal_sha256"] = canonical_evidence_sha256(proposal, "proposal_sha256")
        proposal = SceneInterpretationProposalV1.model_validate(proposal)
        proposal_ref = self._put(
            proposal.model_dump(mode="json"),
            "revised-preview-proposal.json",
            proposal.schema_version,
        )
        attempt = PromptRevisionCompileAttemptV2(
            schema_version="harness.prompt_revision_compile_attempt.v2",
            kind="prompt_revision",
            prompt_revision=snapshot.receipt_head,
            revision=revision,
            effective_prompt=revision.revised_prompt,
            failure_receipts=revision.failure_receipts,
            predecessor_environment=revision.predecessor_environment,
        )
        request = Image2EnvGenesisCompileRequestV2(
            schema_version="harness.image2env_genesis_compile_request.v2",
            workflow_run_id=snapshot.workflow_run_id,
            principal_id=self.principal_id,
            expected_workflow_revision=snapshot.revision,
            expected_state_sha256=snapshot.state_sha256,
            expected_receipt_head_sha256=snapshot.receipt_head.sha256,
            qualification_run_id=original.qualification_run_id,
            compile_input=Image2EnvCompileV3Input(
                schema_version="harness.image2env_compile_input.v3",
                attempt=attempt,
                source_media=original.compile_input.source_media,
                interpretation_proposal=proposal_ref,
                proposal=proposal,
                asset_catalog=original.compile_input.asset_catalog,
                seed=original.compile_input.seed,
                requested_profile="genesis.rigid_scene@1",
                acquisition_policy="exact_only",
                model_execution_allowed=False,
            ),
        )
        envelope = RevisedCompileInvocationV1(
            schema_version="harness.revised_compile_invocation.v1",
            request=self._put(
                request.model_dump(mode="json"),
                "revised-preview-request.json",
                request.schema_version,
            ),
        )
        return self._submit(snapshot, "image2env.compile@3.0.0", envelope, key, "compile")

    def replay(self, *, workflow_id, idempotency_key, expected_skill_ref):
        if expected_skill_ref not in {"image2env.replay@2.0.0", "image2env.replay@3.0.0"}:
            raise ValueError("preview requires exact replay identity")
        previous = self._existing(workflow_id, expected_skill_ref, idempotency_key)
        if previous is not None:
            return self._stage(self.harness.submit(previous[1]), "replay")
        snapshot = self.harness.read(self._request(workflow_id))
        parent, result = self._parent(snapshot)
        version = expected_skill_ref.split("@")[1]
        if parent.skill_ref != "image2env.compile@" + version:
            raise ValueError("replay tool differs from current compiled layout version")
        if self.scene_manifest is None:
            raise ValueError("preview has no approved runtime manifest")
        output = result.typed_output
        terminal = self._read_model(result.run_state, RunState)
        receipt = next(
            r for r in terminal.artifacts if r.schema_version == output["schema_version"]
        )
        model = (
            GenesisSceneExecutionRequestV2 if version == "2.0.0" else GenesisSceneExecutionRequestV3
        )
        request = model(
            schema_version="harness.genesis_scene_execution_request."
            + ("v2" if version == "2.0.0" else "v3"),
            compile_receipt=receipt,
            scene=output["scene"],
            runtime_manifest=self.scene_manifest,
            asset_qualification=output["qualification"],
        )
        ref = self._put(
            request.model_dump(mode="json"), "preview-replay-request.json", request.schema_version
        )
        if version == "2.0.0":
            parameters = GenesisSceneReplayInputV2(
                schema_version="harness.genesis_scene_replay_input.v2",
                compile_run_id=parent.child_run_id,
                execution_request=ref,
            )
        else:
            parameters = GenesisSceneCompileBindingV2(
                schema_version="harness.genesis_scene_compile_binding.v2",
                compile_skill_ref="image2env.compile@3.0.0",
                compile_run_id=parent.child_run_id,
                compile_request=output["request"],
                compile_receipt=receipt,
                execution_request=ref,
            )
        return self._submit(snapshot, expected_skill_ref, parameters, idempotency_key, "replay")

    def observe(self, *, workflow_id, idempotency_key):
        skill = "genesis.observe@3.0.0"
        previous = self._existing(workflow_id, skill, idempotency_key)
        if previous is not None:
            return self._stage(self.harness.submit(previous[1]), "observe")
        snapshot = self.harness.read(self._request(workflow_id))
        parent, _ = self._parent(snapshot)
        if parent.skill_ref != "image2env.replay@2.0.0":
            raise ValueError("diagnosis observation requires the current initial replay")
        return self._submit(
            snapshot,
            skill,
            GenesisFreshCaptureInputV1(
                schema_version="harness.genesis_fresh_capture_input.v1",
                replay_run_id=parent.child_run_id,
                ttl_seconds=300,
            ),
            idempotency_key,
            "observe",
        )

    def validate(
        self,
        *,
        workflow_id,
        idempotency_key,
        expected_position_x_m=None,
        expected_position_y_m=None,
        expected_yaw_rad=None,
    ):
        coordinates = (expected_position_x_m, expected_position_y_m, expected_yaw_rad)
        if any(v is None for v in coordinates) and any(v is not None for v in coordinates):
            raise ValueError("expected layout requires all three explicit coordinates")
        expected = (
            None
            if coordinates[0] is None
            else PreviewLayout(x_m=coordinates[0], y_m=coordinates[1], yaw_rad=coordinates[2])
        )
        skill = "image2env.validate@3.0.0"
        previous = self._existing(workflow_id, skill, idempotency_key)
        if previous is not None:
            parameters = LimitedSceneValidateInputV1.model_validate(previous[1].parameters)
            if parameters.expected_layout != expected:
                raise ValueError("validation retry changed expected layout")
            return self._stage(self.harness.submit(previous[1]), "validate")
        snapshot = self.harness.read(self._request(workflow_id))
        parent, result = self._parent(snapshot)
        if parent.skill_ref == "genesis.observe@3.0.0":
            receipt = GenesisFreshCaptureReceiptV1.model_validate(
                result.typed_output
            ).replay_receipt
        elif parent.skill_ref == "image2env.replay@3.0.0":
            terminal = self._read_model(result.run_state, RunState)
            receipt = next(
                r
                for r in terminal.artifacts
                if r.schema_version == "harness.genesis_scene_execution_receipt.v3"
            )
        else:
            raise ValueError("validation requires current observed initial or modified replay")
        return self._submit(
            snapshot,
            skill,
            LimitedSceneValidateInputV1(
                schema_version="harness.limited_scene_validate_input.v1",
                execution_receipt=receipt,
                expected_layout=expected,
            ),
            idempotency_key,
            "validate",
        )

    def read_resource(self, workflow_id, uri):
        access_prefix = f"harness://workflows/{UUID(str(workflow_id))}/resource-access/"
        if uri.startswith(access_prefix) and len(uri.removeprefix(access_prefix)) == 64:
            _, payload = self.harness.read_resource_access_receipt(
                self._request(workflow_id),
                sha256=uri.removeprefix(access_prefix),
            )
            return {
                "contents": [
                    {"uri": uri, "mimeType": "application/json", "text": payload.decode("utf-8")}
                ]
            }
        prefix = f"harness://workflows/{UUID(str(workflow_id))}/artifacts/"
        if not uri.startswith(prefix) or len(uri.removeprefix(prefix)) != 64:
            raise ValueError("resource must belong to this exact workflow")
        access = self.harness.read_workflow_artifact_audited(
            self._request(workflow_id),
            sha256=uri.removeprefix(prefix),
            resource_uri=uri,
        )
        item = {"uri": uri, "mimeType": access.resource.media_type}
        try:
            item["text"] = access.payload.decode("utf-8")
        except UnicodeDecodeError:
            item["blob"] = base64.b64encode(access.payload).decode("ascii")
        receipt_uri = (
            f"harness://workflows/{workflow_id}/resource-access/{access.access_receipt.sha256}"
        )
        return {
            "contents": [
                item,
                {
                    "uri": receipt_uri,
                    "mimeType": "application/json",
                    "text": access.access_receipt_payload.decode("utf-8"),
                },
            ]
        }

    def submit_control(self, wire):
        from .limited_image_preview_mcp import MCPPromptRevisionRecordRequestV3
        from .mcp_adapter import MCPToolCall
        from .mcp_control_v2 import MCPAdvisoryRecordRequestV2, create_native_harness_mcp_adapter
        from .schemas.native_prompt_revision import (
            PromptRevisionCommandV3,
            TabletopLayoutRevisionV1,
        )
        from .schemas.resource_access import ResourceAccessReceipt

        if type(wire) is MCPAdvisoryRecordRequestV2:
            adapter = create_native_harness_mcp_adapter(
                harness=self.harness,
                principal_id=self.principal_id,
                registry_snapshot=self.registry_snapshot,
            )
            result = adapter.call_tool(
                MCPToolCall(
                    name="harness_advisory_record_v2",
                    arguments=wire.model_dump(mode="json"),
                )
            )
            if result.is_error:
                raise ValueError(str(result.structured_content))
            return dict(result.structured_content)
        if type(wire) is not MCPPromptRevisionRecordRequestV3:
            raise TypeError("preview control requires exact advisory V2 or prompt revision V3 wire")
        snapshot = self.harness.read(self._request(wire.workflow_run_id))
        if (snapshot.revision, snapshot.state_sha256, snapshot.receipt_head.sha256) != (
            wire.expected_workflow_revision,
            wire.expected_state_sha256,
            wire.expected_receipt_head_sha256,
        ):
            raise ValueError("revision wire differs from current Golden head")
        deliveries = {}
        prefix = f"harness://workflows/{snapshot.workflow_run_id}/resource-access/"
        for uri in wire.resource_access_receipt_uris:
            if not uri.startswith(prefix):
                raise ValueError("control access receipt belongs to another workflow")
            _, payload = self.harness.read_resource_access_receipt(
                self._request(snapshot.workflow_run_id),
                sha256=uri.removeprefix(prefix),
            )
            delivery = ResourceAccessReceipt.model_validate_json(payload)
            if delivery.resource_uri in deliveries or delivery.observed_snapshot != snapshot:
                raise ValueError("revision sources require unique reads at the current head")
            deliveries[delivery.resource_uri] = delivery.resource
        used = set()

        def resolve(uri):
            expected = f"harness://workflows/{snapshot.workflow_run_id}/artifacts/"
            if not uri.startswith(expected) or uri not in deliveries:
                raise ValueError("revision resource was not read in this workflow")
            ref, _ = self.harness.read_workflow_artifact(
                self._request(snapshot.workflow_run_id),
                sha256=uri.removeprefix(expected),
            )
            if ref != deliveries[uri]:
                raise ValueError("revision resource differs from delivered bytes")
            used.add(uri)
            return ref

        original = [
            entry.operation
            for entry in self.journal.read_operations(
                principal_id=self.principal_id,
                workflow_run_id=snapshot.workflow_run_id,
            )
            if entry.operation.skill_ref == "image2env.compile@2.0.0"
            and entry.operation.status.value == "succeeded"
        ]
        if len(original) != 1:
            raise ValueError("revision requires this workflow's unique original scene")
        request = Image2EnvGenesisCompileRequestV1.model_validate(
            self._read_model(original[0].command, RunCommandRequest).parameters
        )
        proposal = self._read_model(
            request.compile_input.interpretation_proposal, SceneInterpretationProposalV1
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "revised-prompt.txt"
            path.write_bytes(wire.replacement_text.encode("utf-8"))
            prompt = self.store.put_file(
                path,
                name=path.name,
                media_type="text/plain",
                schema_version="harness.prompt_text.v1",
            )
        source = resolve(wire.source_prompt_uri)
        command = PromptRevisionCommandV3(
            schema_version="harness.prompt_revision_command.v3",
            command_id=wire.command_id,
            principal_id=self.principal_id,
            workflow_run_id=snapshot.workflow_run_id,
            idempotency_key=wire.idempotency_key,
            expected_workflow_revision=snapshot.revision,
            expected_state_sha256=snapshot.state_sha256,
            expected_receipt_head_sha256=snapshot.receipt_head.sha256,
            source_prompt=source,
            advisory_receipt=resolve(wire.advisory_receipt_uri),
            failure_receipts=tuple(resolve(uri) for uri in wire.failure_receipt_uris),
            predecessor_environment=resolve(wire.predecessor_environment_uri),
            patch=dict(
                kind="full_replace",
                base_text_sha256=source.sha256,
                replacement_text=wire.replacement_text,
                reason=wire.reason,
            ),
            fallback=dict(kind="none"),
            next_compile_skill_ref="image2env.compile@3.0.0",
            external_agent_executed=False,
            layout_proposal=TabletopLayoutRevisionV1(
                schema_version="harness.tabletop_layout_revision.v1",
                workflow_run_id=snapshot.workflow_run_id,
                principal_id=self.principal_id,
                base_revision=snapshot.revision,
                state_sha256=snapshot.state_sha256,
                receipt_head_sha256=snapshot.receipt_head.sha256,
                original_source=request.source_scene,
                source_object_id=proposal.objects[0].object_id,
                replacement_text=wire.replacement_text,
                effective_prompt=prompt,
                position_xy_m=wire.position_xy_m,
                yaw_rad=wire.yaw_rad,
                table_center_m=request.table_center_m
                if wire.table_center_m is None
                else wire.table_center_m,
                table_size_m=request.table_size_m
                if wire.table_size_m is None
                else wire.table_size_m,
                relation="on_table",
                physical_properties="preserve_qualified_asset",
            ),
        )
        if used != set(deliveries):
            raise ValueError("control resource reads must exactly match consumed sources")
        return self.harness.submit_control(command).model_dump(mode="json")
