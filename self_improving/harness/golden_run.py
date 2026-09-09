"""Deep aggregate entry point for one parent Golden Workflow."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel

from .artifacts import LocalArtifactStore
from .schemas.common import ArtifactRef
from .schemas.workflow import RunSnapshot, RunStartRequest, WorkflowStartReceipt
from .system2.domain import WorldFact, WorldFactEvidence, build_world_state


class GoldenRunHarness:
    """Create and evolve auditable parent workflows through the S1 seam."""

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        workflow_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._artifact_store = artifact_store
        self._clock = clock
        self._workflow_id_factory = workflow_id_factory

    def start(self, request: RunStartRequest) -> RunSnapshot:
        """Build the first trusted state and its actor-neutral receipt head."""

        started_at = self._clock()
        if started_at.utcoffset() != timezone.utc.utcoffset(started_at):
            raise ValueError("clock must return a UTC datetime")
        workflow_run_id = self._workflow_id_factory()
        self._artifact_store.resolve(request.registry_snapshot)

        facts = tuple(
            self._fact_from_user_evidence(artifact)
            for artifact in request.initial_fact_evidence
        )
        state = build_world_state(facts, as_of=started_at)
        state_ref = self._publish_model(
            state,
            name="trusted_world_state.json",
            schema_version="harness.trusted_world_state.v1",
        )
        receipt = WorkflowStartReceipt(
            schema_version="harness.workflow_start_receipt.v1",
            workflow_run_id=workflow_run_id,
            principal_id=request.principal_id,
            workspace=request.workspace,
            requested_profile=request.requested_profile,
            request_sha256=_request_sha256(request),
            state_sha256=state.state_sha256,
            state_ref=state_ref,
            registry_snapshot=request.registry_snapshot,
            started_at=started_at,
        )
        receipt_ref = self._publish_model(
            receipt,
            name="workflow_start_receipt.json",
            schema_version="harness.workflow_start_receipt.v1",
        )
        return RunSnapshot(
            schema_version="harness.run_snapshot.v1",
            workflow_run_id=workflow_run_id,
            principal_id=request.principal_id,
            workspace=request.workspace,
            requested_profile=request.requested_profile,
            revision=0,
            status="active",
            turn_seq=0,
            state_sha256=state.state_sha256,
            state_ref=state_ref,
            registry_snapshot=request.registry_snapshot,
            receipt_head=receipt_ref,
            active_operation=None,
            child_runs=(),
            started_at=started_at,
            updated_at=started_at,
        )

    def _fact_from_user_evidence(self, artifact: ArtifactRef) -> WorldFact:
        payload = self._artifact_store.resolve(artifact).path.read_bytes()
        evidence = WorldFactEvidence.model_validate_json(payload)
        if payload != _canonical_model_bytes(evidence):
            raise ValueError("initial fact evidence must use canonical JSON bytes")
        if evidence.source_kind != "user_input":
            raise ValueError("initial fact evidence must declare user_input provenance")
        if not evidence.key.startswith("request."):
            raise ValueError("initial fact evidence key must use the request namespace")
        return WorldFact(
            key=evidence.key,
            value=evidence.value,
            source_kind="user_input",
            source_artifact=artifact,
            observed_at=evidence.observed_at,
            valid_until=evidence.valid_until,
        )

    def _publish_model(
        self,
        model: BaseModel,
        *,
        name: str,
        schema_version: str,
    ) -> ArtifactRef:
        payload = _canonical_model_bytes(model)
        with tempfile.TemporaryDirectory(prefix="harness-golden-run-") as temporary_root:
            source = Path(temporary_root) / name
            source.write_bytes(payload)
            return self._artifact_store.put_file(
                source,
                name=name,
                media_type="application/json",
                schema_version=schema_version,
            )


def _canonical_model_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _request_sha256(request: RunStartRequest) -> str:
    payload = {
        "domain": "harness.run_start_request.identity.v1",
        "request": request.model_dump(mode="json"),
    }
    return hashlib.sha256(
        (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()


__all__ = ["GoldenRunHarness"]
