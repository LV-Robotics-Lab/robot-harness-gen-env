"""Canonical user interface; the Harness owns execution and durable lifecycle."""

import time
from contextvars import ContextVar
from pathlib import Path

from .contracts import InputBundle, ToolResult, WorkflowHandle, WorkflowSnapshot, X2EnvRequest
from .input import InputIngestError, ingest
from .store import Store

_deadline = ContextVar("x2env_resume_deadline", default=None)


class Harness:
    def __init__(
        self,
        state_dir: Path,
        *,
        backend_factory=None,
        resolver_factory=None,
        compile_policy=None,
        replay_factory=None,
        contextual_resolver_factory=None,
        scene_design_policy=None,
    ):
        from .grounding import SceneDesignPolicy

        self._scene_design_policy = scene_design_policy or SceneDesignPolicy()
        self._store = Store(state_dir)
        self._state_dir = Path(state_dir)
        self._backend = backend_factory(self._store) if backend_factory else None
        self._resolver = resolver_factory(self._store, self._backend) if resolver_factory else None
        self._compile_policy = compile_policy
        self._replay_executor = replay_factory(self._store) if replay_factory else None
        if resolver_factory and contextual_resolver_factory:
            raise ValueError("choose one resolver assembly")
        self._contextual_resolver_factory = contextual_resolver_factory
        from .skill_execution import build_capabilities

        self._capabilities = build_capabilities(
            self._store, compile_policy=compile_policy, replay_executor=self._replay_executor
        )

    def describe_capabilities(self):
        return self._capabilities.describe()

    def _binding_ref(self, name):
        import json
        from dataclasses import asdict

        descriptor = next(d for d in self.describe_capabilities() if d.name == name)
        return self._store.write_artifact(
            json.dumps(asdict(descriptor), sort_keys=True).encode(), "application/json"
        )

    def submit(self, request: X2EnvRequest) -> WorkflowHandle:
        snapshot = self._store.submit(request)
        return WorkflowHandle(workflow_id=snapshot.workflow_id)

    def status(self, workflow_id: str) -> WorkflowSnapshot:
        return self._store.status(workflow_id)

    def resume(self, workflow_id: str, *, timeout: int = 1770) -> WorkflowSnapshot:
        if type(timeout) is not int or not 1 <= timeout <= 1770:
            raise ValueError("resume timeout must be 1..1770 seconds")
        token = _deadline.set(time.monotonic() + timeout)
        try:
            return self._resume(workflow_id)
        except KeyboardInterrupt as error:
            import json

            snapshot = self.status(workflow_id)
            if snapshot.status == "active":
                reason = "timed_out" if str(error) == "command_deadline" else "interrupted"
                receipt = self._store.write_artifact(
                    json.dumps(
                        {
                            "workflow_id": workflow_id,
                            "reason": reason,
                            "partial_artifacts_retained": True,
                        }
                    ).encode(),
                    "application/json",
                )
                running = snapshot.operations and snapshot.operations[-1].status == "running"
                result = (
                    ToolResult(
                        operation_id=snapshot.operations[-1].operation_id,
                        status="cancelled",
                        outputs=(receipt,),
                        error_code=reason,
                    )
                    if running
                    else None
                )
                self._store.complete_operation(
                    snapshot, result, snapshot.input_bundle, status="cancelled", reason=reason
                )
            raise
        finally:
            _deadline.reset(token)

    def _remaining(self):
        remaining = int(_deadline.get() - time.monotonic())
        if remaining < 1:
            raise KeyboardInterrupt("command_deadline")
        return min(600, remaining)

    def _resume(self, workflow_id: str) -> WorkflowSnapshot:
        existing = self.status(workflow_id)
        if existing.status in {"succeeded", "failed", "cancelled"}:
            return existing
        if existing.pending_scene_ir is not None and any(
            op.capability == "codex.ground"
            and op.status in {"blocked", "failed", "cancelled"}
            and (op.result is None or op.result.error_code != "recoverable_dead_owner")
            for op in existing.operations
        ):
            return existing
        if existing.validation is not None and (
            existing.status == "active"
            or existing.stop_reason == "environment_package_materializer"
        ):
            snapshot = self._store.claim(workflow_id)
            if snapshot.status != "active":
                return snapshot
            return self._validate(snapshot)
        if existing.replay_result is not None:
            can_continue = existing.status == "active" or (
                existing.stop_reason == "blocked_external_resource"
                and existing.required_resources == ("fresh_observation",)
            )
            if not can_continue or not callable(
                getattr(self._backend, "assess_and_diagnose", None)
            ):
                return existing
            snapshot = self._store.claim(workflow_id)
            if snapshot.status != "active":
                return snapshot
            if snapshot.diagnosis is not None:
                return self._validate(snapshot)
            if snapshot.observation is not None:
                return self._diagnose(snapshot)
            return self._observe(snapshot)
        if existing.stop_reason == "clarification_required":
            return existing
        if existing.compiled_scene is not None and self._replay_executor is None:
            return existing
        if existing.scene_ir is not None and (
            (
                (self._resolver is None and self._contextual_resolver_factory is None)
                or existing.asset_resolution is not None
            )
            and existing.resolved_assets is None
        ):
            return existing
        snapshot = self._store.claim(workflow_id)
        if snapshot.status in {"succeeded", "failed", "cancelled", "blocked"}:
            return snapshot
        if snapshot.compiled_scene is not None:
            return self._replay(snapshot)
        if snapshot.pending_scene_ir is not None:
            return self._ground(snapshot) if snapshot.resolved_assets else self._resolve(snapshot)
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
            timeout=self._remaining(),
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
        critical = [unknown for unknown in proposal.unknowns if unknown.critical]
        design_pending = False
        if critical and self._scene_design_policy.enabled:
            from .design_plan import classify_design_unknowns

            try:
                classify_design_unknowns(proposal, self._scene_design_policy, self._compile_policy)
                design_pending = True
            except ValueError:
                # The original advisory/critical fields remain in the journal for clarification.
                pass
        if proposal.scene is None or (critical and not design_pending):
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
        result = ToolResult(
            operation_id=operation.operation_id,
            status="succeeded",
            outputs=(*outputs, scene_ref),
        )
        snapshot = self._store.complete_operation(
            snapshot,
            result,
            snapshot.input_bundle,
            status="active" if self._resolver or self._contextual_resolver_factory else "blocked",
            reason=None
            if self._resolver or self._contextual_resolver_factory
            else "blocked_external_resource",
            proposal=proposal_ref,
            scene_ir=None if design_pending else scene_ref,
            pending_scene_ir=scene_ref if design_pending else None,
            required_resources=()
            if self._resolver or self._contextual_resolver_factory
            else ("asset_resolver",),
        )
        return (
            self._resolve(snapshot)
            if self._resolver or self._contextual_resolver_factory
            else snapshot
        )

    def _resolve(self, snapshot):
        snapshot = self._store.begin_operation(snapshot, "asset.resolve")
        operation = snapshot.operations[-1]
        try:
            resolver = (
                self._contextual_resolver_factory(
                    self._store,
                    self._backend,
                    snapshot.request,
                    InputBundle.model_validate_json(
                        self._store.read_artifact(snapshot.input_bundle)
                    ),
                )
                if self._contextual_resolver_factory
                else self._resolver
            )
            resolution = resolver.resolve(
                snapshot.scene_ir or snapshot.pending_scene_ir,
                allowed_sources=snapshot.request.allowed_sources,
                allow_cousin=snapshot.request.constraints.allow_cousin,
                output_root=self._state_dir
                / "attempts"
                / snapshot.workflow_id
                / operation.operation_id,
                timeout=self._remaining(),
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
        if not ready:
            return snapshot
        return self._ground(snapshot) if snapshot.pending_scene_ir else self._compile(snapshot)

    def _ground(self, snapshot):
        from .compile import ResolvedAssetSet

        self._remaining()
        snapshot = self._store.begin_operation(snapshot, "codex.ground")
        operation = snapshot.operations[-1]
        try:
            grounded = self._backend.ground_scene(
                snapshot.input_bundle,
                snapshot.proposal,
                snapshot.resolved_assets,
                self._scene_design_policy,
                structural_policy=self._compile_policy,
                output_root=self._state_dir
                / "attempts"
                / snapshot.workflow_id
                / operation.operation_id,
                timeout=self._remaining(),
            )
            if grounded.status != "completed" or grounded.proposed_scene is None:
                code = grounded.error_code or "grounding_missing_scene"
                return self._store.complete_operation(
                    snapshot,
                    ToolResult(
                        operation_id=operation.operation_id,
                        status="blocked",
                        outputs=(grounded.receipt,),
                        error_code=code,
                    ),
                    snapshot.input_bundle,
                    status="blocked",
                    reason=code,
                )
            scene = self._store.write_artifact(
                grounded.proposed_scene.model_dump_json().encode(), "application/json"
            )
            assets = ResolvedAssetSet.model_validate_json(
                self._store.read_artifact(snapshot.resolved_assets)
            )
            rebound = assets.model_copy(update={"scene_ir": scene})
            assets_ref = self._store.write_artifact(
                rebound.model_dump_json().encode(), "application/json"
            )
            snapshot = self._store.complete_operation(
                snapshot,
                ToolResult(
                    operation_id=operation.operation_id,
                    status="succeeded",
                    outputs=(grounded.receipt, scene, assets_ref),
                ),
                snapshot.input_bundle,
                status="active",
                scene_ir=scene,
                resolved_assets=assets_ref,
                grounding=grounded.receipt,
            )
        except (ValueError, OSError, KeyError, TypeError) as error:
            return self._stage_failure(snapshot, "grounding_failed", error)
        return self._compile(snapshot)

    def _compile(self, snapshot):
        from .compile import ResolvedAssetSet
        from .skill_execution import CompileCall

        if self._compile_policy is None:
            return self._store.complete_operation(
                snapshot,
                None,
                snapshot.input_bundle,
                status="blocked",
                reason="blocked_external_resource",
                required_resources=("compile_policy",),
            )
        self._remaining()
        snapshot = self._store.begin_operation(snapshot, "x2env.compile")
        operation = snapshot.operations[-1]
        try:
            assets = ResolvedAssetSet.model_validate_json(
                self._store.read_artifact(snapshot.resolved_assets)
            )
            compiled = self._capabilities.invoke(
                "x2env.compile",
                "1.0.0",
                CompileCall(
                    scene_ir=snapshot.scene_ir,
                    assets=assets,
                    output_root=str(
                        self._state_dir / "stages" / snapshot.workflow_id / operation.operation_id
                    ),
                    seed=snapshot.request.seed,
                ),
            )
            ref = self._store.write_artifact(
                compiled.model_dump_json().encode(), "application/json"
            )
            result = ToolResult(
                operation_id=operation.operation_id,
                status="succeeded",
                outputs=(compiled.receipt, ref, self._binding_ref("x2env.compile")),
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
        from .skill_execution import ReplayCall

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
            replay = self._capabilities.invoke(
                "x2env.replay",
                "1.0.0",
                ReplayCall(
                    scene=compiled.runtime_scene,
                    package_root=str(
                        self._state_dir
                        / "stages"
                        / snapshot.workflow_id
                        / compile_operation.operation_id
                    ),
                    output_root=str(
                        self._state_dir / "attempts" / snapshot.workflow_id / operation.operation_id
                    ),
                    timeout=self._remaining(),
                ),
            )
            ref = self._store.write_artifact(replay.model_dump_json().encode(), "application/json")
            result = ToolResult(
                operation_id=operation.operation_id,
                status=replay.status,
                outputs=(
                    replay.receipt,
                    ref,
                    self._binding_ref("x2env.replay"),
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
        self._remaining()
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
                timeout=self._remaining(),
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
        from .skill_execution import ValidateCall

        self._remaining()
        snapshot = self._store.begin_operation(snapshot, "x2env.validate")
        try:
            validated = self._capabilities.invoke(
                "x2env.validate",
                "1.0.0",
                ValidateCall(
                    scene_ir=snapshot.scene_ir,
                    observation=snapshot.observation,
                    diagnosis=snapshot.diagnosis,
                ),
            )
            ref, status, code, repairable = (
                validated.report,
                validated.status,
                validated.error_code,
                validated.repairable,
            )
            result = ToolResult(
                operation_id=snapshot.operations[-1].operation_id,
                status=status,
                outputs=(ref, self._binding_ref("x2env.validate")),
                error_code=code,
            )
            snapshot = self._store.complete_operation(
                snapshot,
                result,
                snapshot.input_bundle,
                status="active" if repairable or status == "succeeded" else status,
                reason=None if repairable or status == "succeeded" else code,
                validation=ref,
                required_resources=(code,) if status == "blocked" else (),
            )
        except (ValueError, OSError, KeyError, TypeError) as error:
            return self._stage_failure(snapshot, "scene_validation_failed", error)
        if repairable:
            return self._revise(snapshot)
        return self._materialize(snapshot) if status == "succeeded" else snapshot

    def _materialize(self, snapshot):
        from .completion import materialize_completion
        from .package_loader import verify_package

        self._remaining()
        snapshot = self._store.begin_operation(snapshot, "package.materialize")
        operation = snapshot.operations[-1]
        try:
            completed = materialize_completion(
                snapshot,
                self._store,
                self._state_dir / "stages" / snapshot.workflow_id / operation.operation_id,
            )
            ref = self._store.write_artifact(
                completed.model_dump_json().encode(), "application/json"
            )
            if completed.status != "materialized":
                result = ToolResult(
                    operation_id=operation.operation_id,
                    status="failed",
                    outputs=(ref, completed.receipt),
                    error_code=completed.error_code,
                )
                return self._store.complete_operation(
                    snapshot,
                    result,
                    snapshot.input_bundle,
                    status="failed",
                    reason=completed.error_code,
                )
            verify_package(Path(completed.package_path))
            if (
                Path(completed.package_path) / "manifest.json"
            ).read_bytes() != self._store.read_artifact(completed.manifest):
                raise ValueError("materialized_manifest_changed")
            result = ToolResult(
                operation_id=operation.operation_id,
                status="succeeded",
                outputs=(ref, completed.receipt, completed.manifest),
            )
            return self._store.complete_operation(
                snapshot, result, snapshot.input_bundle, status="succeeded", environment_package=ref
            )
        except (ValueError, OSError, KeyError, TypeError) as error:
            return self._stage_failure(snapshot, "environment_materialization_failed", error)

    def _revise(self, snapshot):
        import hashlib
        import json

        from .assets import AssetRegistry
        from .compile import ResolvedAssetSet
        from .contracts import RepairReservation, SceneIR
        from .diagnosis import DiagnosisResult
        from .observation import ObservationResult
        from .revision import apply_revision

        self._remaining()
        approval = None
        try:
            observed = ObservationResult.model_validate_json(
                self._store.read_artifact(snapshot.observation)
            )
            report = json.loads(self._store.read_artifact(observed.physics_report))
            if report.get("execution_evidence_bound") is not True:
                raise ValueError("unbound_repair_evidence")
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "input": report["input_sha256"],
                        "error_code": report.get("error_code"),
                        "checks": [
                            c for c in report.get("checks", []) if c.get("status") != "passed"
                        ],
                        "images": [f.image.sha256 for f in observed.observation.frames],
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            diagnosis = DiagnosisResult.model_validate_json(
                self._store.read_artifact(snapshot.diagnosis)
            )
            scene = SceneIR.model_validate_json(self._store.read_artifact(snapshot.scene_ir))
            proposal = diagnosis.proposal
            if (
                diagnosis.status != "completed"
                or proposal is None
                or proposal.base_revision != scene.revision
                or observed.observation.scene_ir != snapshot.scene_ir
                or report["input_sha256"] != scene.input_sha256
            ):
                raise ValueError("revision_base_mismatch")
            cost = int(bool(proposal.scene_patches)) + len(proposal.asset_patches)
            if not 1 <= cost <= 2:
                raise ValueError("revision_budget_exhausted")
            approval = self._store.write_artifact(
                json.dumps(
                    {
                        "authority": "harness_controller",
                        "approved": True,
                        "workflow_id": snapshot.workflow_id,
                        "base_revision": snapshot.revision,
                        "input_bundle": snapshot.input_bundle.model_dump(),
                        "scene_ir": snapshot.scene_ir.model_dump(),
                        "diagnosis": snapshot.diagnosis.model_dump(),
                        "cost": cost,
                        "failure_fingerprint": fingerprint,
                    },
                    sort_keys=True,
                ).encode(),
                "application/json",
            )
            reservation = RepairReservation(
                kind="scene_asset"
                if proposal.scene_patches and proposal.asset_patches
                else "scene"
                if proposal.scene_patches
                else "asset",
                cost=cost,
                failure_fingerprint=fingerprint,
                approval=approval,
                base_revision=snapshot.revision,
            )
            snapshot = self._store.begin_operation(
                snapshot, "revise", repair_reservation=reservation
            )
        except (ValueError, OSError, KeyError, TypeError) as error:
            # A rejected request is not an executable repair and cannot consume a prior operation.
            snapshot = self._store.begin_operation(snapshot, "repair.reject")
            code = {
                "repair_budget_exhausted": "revision_budget_exhausted",
                "repair_repeated_failure": "repeated_failure",
            }.get(str(error), "repair_preflight_rejected")
            error_ref = self._store.write_artifact(
                json.dumps(
                    {
                        "error_code": code,
                        "reason": str(error)[:2048],
                        "executable_repair": False,
                        "scene_ir": snapshot.scene_ir.model_dump() if snapshot.scene_ir else None,
                        "diagnosis": snapshot.diagnosis.model_dump()
                        if snapshot.diagnosis
                        else None,
                        "approval": approval.model_dump() if approval else None,
                    },
                    sort_keys=True,
                ).encode(),
                "application/json",
            )
            return self._store.complete_operation(
                snapshot,
                ToolResult(
                    operation_id=snapshot.operations[-1].operation_id,
                    status="failed",
                    outputs=(error_ref,),
                    error_code=code,
                ),
                snapshot.input_bundle,
                status="failed",
                reason=code,
            )
        try:
            self._remaining()
            reservation_ref = self._store.write_artifact(
                json.dumps(
                    {
                        "workflow_id": snapshot.workflow_id,
                        "operation_id": snapshot.operations[-1].operation_id,
                        "reservation": reservation.model_dump(mode="json"),
                        "input_bundle": snapshot.input_bundle.model_dump(),
                        "scene_ir": snapshot.scene_ir.model_dump(),
                        "diagnosis": snapshot.diagnosis.model_dump(),
                    },
                    sort_keys=True,
                ).encode(),
                "application/json",
            )
            revised = apply_revision(
                self._store,
                AssetRegistry(self._store),
                snapshot.scene_ir,
                ResolvedAssetSet.model_validate_json(
                    self._store.read_artifact(snapshot.resolved_assets)
                ),
                snapshot.diagnosis,
                output_root=self._state_dir
                / "stages"
                / snapshot.workflow_id
                / snapshot.operations[-1].operation_id,
                history=snapshot.revisions,
                failure_fingerprint=fingerprint,
                reservation_ref=reservation_ref,
            )
            assets_ref = self._store.write_artifact(
                revised.assets.model_dump_json().encode(), "application/json"
            )
            result = ToolResult(
                operation_id=snapshot.operations[-1].operation_id,
                status="succeeded",
                outputs=(revised.receipt, revised.scene_ir, assets_ref),
            )
            snapshot = self._store.complete_operation(
                snapshot,
                result,
                snapshot.input_bundle,
                status="active",
                scene_ir=revised.scene_ir,
                resolved_assets=assets_ref,
                revision_receipt=revised.receipt,
            )
        except (ValueError, OSError, KeyError, TypeError) as error:
            code = (
                str(error)
                if str(error)
                in {"repeated_failure", "revision_budget_exhausted", "revision_has_no_effect"}
                else "scene_revision_failed"
            )
            return self._stage_failure(snapshot, code, error)
        return self._compile(snapshot)

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

    def package(self, workflow_id: str, *, output: Path | None = None, reuse_existing=False):
        snapshot = self.status(workflow_id)
        if snapshot.status == "succeeded":
            from .delivery import export_completion

            return export_completion(snapshot, self._store, output, reuse_existing=reuse_existing)
        if output is not None and snapshot.status in {"failed", "blocked", "cancelled"}:
            from .failure_bundle import materialize_failure

            return materialize_failure(snapshot, self._store, output, reuse_existing=reuse_existing)
        raise ValueError("no materialized environment package")
