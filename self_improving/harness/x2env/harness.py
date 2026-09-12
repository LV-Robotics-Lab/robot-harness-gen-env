"""Canonical user interface; the Harness owns execution and durable lifecycle."""

from pathlib import Path

from .contracts import InputBundle, ToolResult, WorkflowHandle, WorkflowSnapshot, X2EnvRequest
from .input import InputIngestError, ingest
from .store import Store


class Harness:
    def __init__(
        self, state_dir: Path, *, backend_factory=None, resolver_factory=None, compile_policy=None
    ):
        self._store = Store(state_dir)
        self._state_dir = Path(state_dir)
        self._backend = backend_factory(self._store) if backend_factory else None
        self._resolver = resolver_factory(self._store, self._backend) if resolver_factory else None
        self._compile_policy = compile_policy

    def submit(self, request: X2EnvRequest) -> WorkflowHandle:
        snapshot = self._store.submit(request)
        return WorkflowHandle(workflow_id=snapshot.workflow_id)

    def status(self, workflow_id: str) -> WorkflowSnapshot:
        return self._store.status(workflow_id)

    def resume(self, workflow_id: str) -> WorkflowSnapshot:
        existing = self.status(workflow_id)
        if existing.stop_reason == "clarification_required" or existing.compiled_scene is not None:
            return existing
        if existing.scene_ir is not None and (
            (self._resolver is None or existing.asset_resolution is not None)
            and existing.resolved_assets is None
        ):
            return existing
        snapshot = self._store.claim(workflow_id)
        if snapshot.status in {"succeeded", "failed", "cancelled", "blocked"}:
            return snapshot
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
            return self._store.complete_operation(
                snapshot,
                result,
                snapshot.input_bundle,
                status="blocked",
                reason="blocked_external_resource",
                compiled_scene=ref,
                required_resources=("genesis_replay_executor",),
            )
        except (ValueError, OSError, KeyError, TypeError) as error:
            return self._stage_failure(snapshot, "scene_compile_failed", error)

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
