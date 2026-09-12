"""Canonical user interface; the Harness owns execution and durable lifecycle."""

from pathlib import Path

from .contracts import InputBundle, ToolResult, WorkflowHandle, WorkflowSnapshot, X2EnvRequest
from .input import InputIngestError, ingest
from .store import Store


class Harness:
    def __init__(self, state_dir: Path, *, backend_factory=None):
        self._store = Store(state_dir)
        self._state_dir = Path(state_dir)
        self._backend = backend_factory(self._store) if backend_factory else None

    def submit(self, request: X2EnvRequest) -> WorkflowHandle:
        snapshot = self._store.submit(request)
        return WorkflowHandle(workflow_id=snapshot.workflow_id)

    def status(self, workflow_id: str) -> WorkflowSnapshot:
        return self._store.status(workflow_id)

    def resume(self, workflow_id: str) -> WorkflowSnapshot:
        existing = self.status(workflow_id)
        if existing.stop_reason == "clarification_required" or existing.scene_ir is not None:
            return existing
        snapshot = self._store.claim(workflow_id)
        if snapshot.status in {"succeeded", "failed", "cancelled"}:
            return snapshot
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
        return self._store.complete_operation(
            snapshot,
            result,
            snapshot.input_bundle,
            status="blocked",
            reason="blocked_external_resource",
            proposal=proposal_ref,
            scene_ir=scene_ref,
            required_resources=("asset_resolver",),
        )

    def package(self, workflow_id: str):
        self.status(workflow_id)
        raise ValueError("no materialized environment package")
