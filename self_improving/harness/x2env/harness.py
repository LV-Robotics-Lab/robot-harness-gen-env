"""Canonical user interface; the Harness owns execution and durable lifecycle."""

from pathlib import Path

from .contracts import InputBundle, ToolResult, WorkflowHandle, WorkflowSnapshot, X2EnvRequest
from .input import InputIngestError, ingest
from .store import Store


class Harness:
    def __init__(
        self,
        state_dir: Path,
        *,
        backend_factory=None,
        resolver_factory=None,
        compile_policy=None,
        replay_factory=None,
    ):
        self._store = Store(state_dir)
        self._state_dir = Path(state_dir)
        self._backend = backend_factory(self._store) if backend_factory else None
        self._resolver = resolver_factory(self._store, self._backend) if resolver_factory else None
        self._compile_policy = compile_policy
        self._replay_executor = replay_factory(self._store) if replay_factory else None

    def submit(self, request: X2EnvRequest) -> WorkflowHandle:
        snapshot = self._store.submit(request)
        return WorkflowHandle(workflow_id=snapshot.workflow_id)

    def status(self, workflow_id: str) -> WorkflowSnapshot:
        return self._store.status(workflow_id)

    def resume(self, workflow_id: str) -> WorkflowSnapshot:
        existing = self.status(workflow_id)
        if existing.stop_reason == "clarification_required" or existing.replay_result is not None:
            return existing
        if existing.compiled_scene is not None and self._replay_executor is None:
            return existing
        if existing.scene_ir is not None and (
            (self._resolver is None or existing.asset_resolution is not None)
            and existing.resolved_assets is None
        ):
            return existing
        snapshot = self._store.claim(workflow_id)
        if snapshot.status in {"succeeded", "failed", "cancelled", "blocked"}:
            return snapshot
        if snapshot.compiled_scene is not None:
            return self._replay(snapshot)
        if snapshot.scene_ir is not None:
            return self._compile(snapshot) if snapshot.resolved_assets else self._resolve(snapshot)
        result = None
        bundle_ref = snapshot.input_bundle
        if bundle_ref is None:
            operation_id = snapshot.operations[-1].operation_id
            try:
                bundle = ingest(snapshot.request, self._store)
                bundle_ref = self._store.write_artifact(
                    bundle.model_dump_json().encode(), "application/json"
                )
                result = ToolResult(
                    operation_id=operation_id, status="succeeded", outputs=(bundle_ref,)
                )
            except InputIngestError as error:
                status = "blocked" if error.code == "blocked_external_resource" else "failed"
                result = ToolResult(
                    operation_id=operation_id,
                    status=status,
                    outputs=error.artifacts,
                    error_code=error.code,
                    message=str(error),
                )
                return self._store.complete_operation(
                    snapshot,
                    result,
                    None,
                    status=status,
                    reason=error.code,
                    required_resources=("ffmpeg", "ffprobe") if status == "blocked" else (),
                )
        snapshot = self._store.complete_operation(snapshot, result, bundle_ref, status="active")
        if self._backend is not None:
            return self._interpret(snapshot)
        return self._store.complete_operation(
            snapshot,
            None,
            bundle_ref,
            status="blocked",
            reason="blocked_external_resource",
            required_resources=("managed_codex_backend",),
        )

    def _interpret(self, snapshot):
        snapshot = self._store.begin_operation(snapshot, "codex.interpret", version="2.0.0")
        operation = snapshot.operations[-1]
        bundle = InputBundle.model_validate_json(self._store.read_artifact(snapshot.input_bundle))
        advisory = self._backend.interpret(
            bundle,
            output_root=self._state_dir
            / "attempts"
            / snapshot.workflow_id
            / operation.operation_id,
            timeout=600,
        )
        proposal_ref = self._store.write_artifact(
            advisory.model_dump_json().encode(), "application/json"
        )
        outputs = (*advisory.evidence, proposal_ref)
        if advisory.status != "completed":
            result = ToolResult(
                operation_id=operation.operation_id,
                status=advisory.status,
                outputs=outputs,
                error_code=advisory.error_code,
            )
            return self._store.complete_operation(
                snapshot,
                result,
                snapshot.input_bundle,
                status=advisory.status,
                reason=advisory.error_code,
                proposal=proposal_ref,
            )
        result = ToolResult(
            operation_id=operation.operation_id, status="succeeded", outputs=outputs
        )
        proposal = advisory.proposal
        if proposal.scene is None or any(unknown.critical for unknown in proposal.unknowns):
            return self._store.complete_operation(
                snapshot,
                result,
                snapshot.input_bundle,
                status="blocked",
                reason="clarification_required",
                proposal=proposal_ref,
            )
        scene_ref = self._store.write_artifact(
            proposal.scene.model_dump_json().encode(), "application/json"
        )
        snapshot = self._store.complete_operation(
            snapshot,
            result,
            snapshot.input_bundle,
            status="active" if self._resolver else "blocked",
            reason=None if self._resolver else "blocked_external_resource",
            proposal=proposal_ref,
            scene_ir=scene_ref,
            required_resources=() if self._resolver else ("asset_resolver",),
        )
        return self._resolve(snapshot) if self._resolver else snapshot

    def _resolve(self, snapshot):
        snapshot = self._store.begin_operation(snapshot, "asset.resolve")
        operation = snapshot.operations[-1]
        try:
            resolution = self._resolver.resolve(
                snapshot.scene_ir,
                allowed_sources=snapshot.request.allowed_sources,
                allow_cousin=snapshot.request.constraints.allow_cousin,
                output_root=self._state_dir
                / "attempts"
                / snapshot.workflow_id
                / operation.operation_id,
                timeout=600,
            )
            resolution_ref = self._store.write_artifact(
                resolution.model_dump_json().encode(), "application/json"
            )
            assets_ref = self._store.write_artifact(
                resolution.resolved.model_dump_json().encode(), "application/json"
            )
            result = ToolResult(
                operation_id=operation.operation_id,
                status=resolution.status,
                outputs=(resolution.receipt, resolution_ref, assets_ref),
                error_code=resolution.error_code,
            )
            ready = resolution.status == "succeeded"
            snapshot = self._store.complete_operation(
                snapshot,
                result,
                snapshot.input_bundle,
                status="active" if ready else resolution.status,
                reason=resolution.error_code,
                required_resources=resolution.required_resources,
                asset_resolution=resolution_ref,
                resolved_assets=assets_ref if ready else None,
            )
        except (ValueError, OSError, KeyError, TypeError) as error:
            return self._stage_failure(snapshot, "asset_resolution_failed", error)
        return self._compile(snapshot) if ready else snapshot

    def _compile(self, snapshot):
        from .assets import AssetRegistry
        from .compile import ResolvedAssetSet, compile_scene

        if self._compile_policy is None:
            return self._store.complete_operation(
                snapshot,
                None,
                snapshot.input_bundle,
                status="blocked",
                reason="blocked_external_resource",
                required_resources=("compile_policy",),
            )
        snapshot = self._store.begin_operation(snapshot, "x2env.compile")
        operation = snapshot.operations[-1]
        try:
            assets = ResolvedAssetSet.model_validate_json(
                self._store.read_artifact(snapshot.resolved_assets)
            )
            compiled = compile_scene(
                snapshot.scene_ir,
                assets,
                registry=AssetRegistry(self._store),
                store=self._store,
                output_root=self._state_dir
                / "stages"
                / snapshot.workflow_id
                / operation.operation_id,
                policy=self._compile_policy,
                seed=snapshot.request.seed,
            )
            ref = self._store.write_artifact(
                compiled.model_dump_json().encode(), "application/json"
            )
            result = ToolResult(
                operation_id=operation.operation_id,
                status="succeeded",
                outputs=(compiled.receipt, ref),
            )
            snapshot = self._store.complete_operation(
                snapshot,
                result,
                snapshot.input_bundle,
                status="active" if self._replay_executor else "blocked",
                reason=None if self._replay_executor else "blocked_external_resource",
                compiled_scene=ref,
                required_resources=() if self._replay_executor else ("genesis_replay_executor",),
            )
        except (ValueError, OSError, KeyError, TypeError) as error:
            return self._stage_failure(snapshot, "scene_compile_failed", error)
        return self._replay(snapshot) if self._replay_executor else snapshot

    def _replay(self, snapshot):
        from .compile import CompiledScene

        compile_operation = next(
            op
            for op in reversed(snapshot.operations)
            if op.capability == "x2env.compile" and op.status == "succeeded"
        )
        snapshot = self._store.begin_operation(snapshot, "x2env.replay")
        operation = snapshot.operations[-1]
        try:
            compiled = CompiledScene.model_validate_json(
                self._store.read_artifact(snapshot.compiled_scene)
            )
            replay = self._replay_executor.replay(
                compiled.runtime_scene,
                package_root=self._state_dir
                / "stages"
                / snapshot.workflow_id
                / compile_operation.operation_id,
                output_root=self._state_dir
                / "attempts"
                / snapshot.workflow_id
                / operation.operation_id,
                timeout=600,
            )
            ref = self._store.write_artifact(replay.model_dump_json().encode(), "application/json")
            result = ToolResult(
                operation_id=operation.operation_id,
                status=replay.status,
                outputs=(
                    replay.receipt,
                    ref,
                    *(f.artifact for p in replay.profiles for f in p.files),
                ),
                error_code=replay.error_code,
            )
            can_observe = replay.status == "succeeded" and callable(
                getattr(self._backend, "assess_and_diagnose", None)
            )
            snapshot = self._store.complete_operation(
                snapshot,
                result,
                snapshot.input_bundle,
                status="active"
                if can_observe
                else "blocked"
                if replay.status == "succeeded"
                else replay.status,
                reason=None
                if can_observe
                else "blocked_external_resource"
                if replay.status == "succeeded"
                else replay.error_code,
                replay_result=ref,
                required_resources=("fresh_observation",)
                if replay.status == "succeeded" and not can_observe
                else (),
            )
        except (ValueError, OSError, KeyError, TypeError) as error:
            return self._stage_failure(snapshot, "scene_replay_failed", error)
        return self._observe(snapshot) if can_observe else snapshot

    def _observe(self, snapshot):
        from .compile import CompiledScene
        from .observation import observe_replay
        from .replay import ReplayResult

        compile_op = next(
            op
            for op in reversed(snapshot.operations)
            if op.capability == "x2env.compile" and op.status == "succeeded"
        )
        snapshot = self._store.begin_operation(snapshot, "observe")
        try:
            compiled = CompiledScene.model_validate_json(
                self._store.read_artifact(snapshot.compiled_scene)
            )
            runtime_ref = self._store.write_artifact(
                compiled.runtime_scene.model_dump_json().encode(), "application/json"
            )
            replay = ReplayResult.model_validate_json(
                self._store.read_artifact(snapshot.replay_result)
            )
            observed = observe_replay(
                self._store,
                snapshot.scene_ir,
                runtime_ref,
                replay,
                package_root=self._state_dir
                / "stages"
                / snapshot.workflow_id
                / compile_op.operation_id,
            )
            ref = self._store.write_artifact(
                observed.model_dump_json().encode(), "application/json"
            )
            result = ToolResult(
                operation_id=snapshot.operations[-1].operation_id,
                status="succeeded",
                outputs=(ref, observed.receipt, observed.physics_report, runtime_ref),
            )
            snapshot = self._store.complete_operation(
                snapshot, result, snapshot.input_bundle, status="active", observation=ref
            )
        except (ValueError, OSError, KeyError, TypeError, IndexError) as error:
            return self._stage_failure(snapshot, "scene_observation_failed", error)
        return self._diagnose(snapshot)

    def _diagnose(self, snapshot):
        from .diagnosis import DiagnosisResult
        from .observation import ObservationResult

        snapshot = self._store.begin_operation(snapshot, "codex.diagnose")
        operation = snapshot.operations[-1]
        try:
            observed = ObservationResult.model_validate_json(
                self._store.read_artifact(snapshot.observation)
            )
            advisory = self._backend.assess_and_diagnose(
                snapshot.scene_ir,
                observed.observation,
                observed.physics_report,
                output_root=self._state_dir
                / "attempts"
                / snapshot.workflow_id
                / operation.operation_id,
                timeout=600,
            )
            advisory = DiagnosisResult.model_validate_json(advisory.model_dump_json())
            ref = self._store.write_artifact(
                advisory.model_dump_json().encode(), "application/json"
            )
            status = "succeeded" if advisory.status == "completed" else advisory.status
            result = ToolResult(
                operation_id=operation.operation_id,
                status=status,
                outputs=(ref, advisory.receipt),
                error_code=advisory.error_code,
            )
            snapshot = self._store.complete_operation(
                snapshot,
                result,
                snapshot.input_bundle,
                status="active" if status == "succeeded" else status,
                reason=advisory.error_code,
                diagnosis=ref,
            )
        except (ValueError, OSError, KeyError, TypeError) as error:
            return self._stage_failure(snapshot, "scene_diagnosis_failed", error)
        return self._validate(snapshot) if status == "succeeded" else snapshot

    def _validate(self, snapshot):
        import json

        from .diagnosis import DiagnosisResult
        from .observation import ObservationResult

        snapshot = self._store.begin_operation(snapshot, "x2env.validate")
        try:
            observed = ObservationResult.model_validate_json(
                self._store.read_artifact(snapshot.observation)
            )
            diagnosis = DiagnosisResult.model_validate_json(
                self._store.read_artifact(snapshot.diagnosis)
            )
            physical = json.loads(self._store.read_artifact(observed.physics_report))
            proposal = diagnosis.proposal
            physical_pass = (
                physical.get("physical_status") == "passed"
                and physical.get("execution_evidence_bound") is True
            )
            visual_pass = proposal is not None and proposal.visual_intent == "passed"
            patches = proposal is not None and (proposal.scene_patches or proposal.asset_patches)
            code = (
                "revision_executor"
                if patches
                else "physical_validation_not_passed"
                if not physical_pass
                else "visual_intent_not_passed"
                if not visual_pass
                else "environment_package_materializer"
            )
            status = "blocked" if patches or (physical_pass and visual_pass) else "failed"
            report = {
                "physical_status": physical.get("physical_status", "not_run"),
                "visual_status": proposal.visual_intent if proposal else "not_run",
                "physics_report": observed.physics_report.model_dump(mode="json"),
                "diagnosis": snapshot.diagnosis.model_dump(mode="json"),
                "scene_ir": snapshot.scene_ir.model_dump(mode="json"),
                "status": status,
                "error_code": code,
                "sim_ready": False,
            }
            ref = self._store.write_artifact(
                json.dumps(report, sort_keys=True).encode(), "application/json"
            )
            result = ToolResult(
                operation_id=snapshot.operations[-1].operation_id,
                status=status,
                outputs=(ref,),
                error_code=code,
            )
            return self._store.complete_operation(
                snapshot,
                result,
                snapshot.input_bundle,
                status=status,
                reason=code,
                validation=ref,
                required_resources=(code,) if status == "blocked" else (),
            )
        except (ValueError, OSError, KeyError, TypeError) as error:
            return self._stage_failure(snapshot, "scene_validation_failed", error)

    def _stage_failure(self, snapshot, code, error):
        import json

        ref = self._store.write_artifact(
            json.dumps(
                {
                    "error_code": code,
                    "reason": str(error)[:2048],
                    "operation_id": snapshot.operations[-1].operation_id,
                }
            ).encode(),
            "application/json",
        )
        result = ToolResult(
            operation_id=snapshot.operations[-1].operation_id,
            status="failed",
            outputs=(ref,),
            error_code=code,
        )
        return self._store.complete_operation(
            snapshot, result, snapshot.input_bundle, status="failed", reason=code
        )

    def package(self, workflow_id: str):
        self.status(workflow_id)
        raise ValueError("no materialized environment package")
