"""Deep aggregate entry point for one parent Golden Workflow."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .qualification import QualificationReportV1
from .schema_catalog import schema_model
from .schemas.common import (
    ArtifactRef,
    SkillDescriptor,
    SkillDescriptorV2,
    SkillQualification,
)
from .schemas.registry_snapshot import RegistrySnapshot
from .schemas.workflow import RunSnapshot, RunStartRequest, WorkflowStartReceipt
from .system2.domain import (
    TrustedWorldState,
    WorldFact,
    WorldFactEvidence,
    build_world_state,
)


class GoldenRunConflictError(RuntimeError):
    """A caller reused an immutable workflow identity for different input."""

    code = "HARN_IDEMPOTENCY_CONFLICT"


class GoldenRunCorruptionError(RuntimeError):
    """Persisted workflow authority or its immutable CAS closure is corrupt."""

    code = "HARN_PERSISTENCE_CONFLICT"


class GoldenRunRegistryError(ValueError):
    """A submitted Registry snapshot is malformed, unavailable, or unqualified."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class GoldenRunHarness:
    """Create and evolve auditable parent workflows through the S1 seam."""

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        workflow_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if type(artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be the exact LocalArtifactStore")
        self._artifact_store = artifact_store
        self._clock = clock
        self._workflow_id_factory = workflow_id_factory
        self._start_store = _SQLiteWorkflowStartStore(
            self._artifact_store.root / "golden-workflows.sqlite3"
        )

    def start(self, request: RunStartRequest) -> RunSnapshot:
        """Build the first trusted state and its actor-neutral receipt head."""

        request_sha256 = _request_sha256(request)
        snapshot = self._start_store.get_or_create(
            principal_id=request.principal_id,
            idempotency_key=request.idempotency_key,
            request_sha256=request_sha256,
            factory=lambda: self._create_start(request, request_sha256=request_sha256),
        )
        self._verify_start_snapshot(
            snapshot,
            request=request,
            request_sha256=request_sha256,
        )
        return snapshot

    def _create_start(self, request: RunStartRequest, *, request_sha256: str) -> RunSnapshot:
        started_at = self._clock()
        if started_at.utcoffset() != timezone.utc.utcoffset(started_at):
            raise ValueError("clock must return a UTC datetime")
        workflow_run_id = self._workflow_id_factory()
        self._verify_registry_snapshot(request.registry_snapshot)

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
        request_ref = self._publish_model(
            request,
            name="run_start_request.json",
            schema_version="harness.run_start_request.v1",
        )
        receipt = WorkflowStartReceipt(
            schema_version="harness.workflow_start_receipt.v1",
            workflow_run_id=workflow_run_id,
            principal_id=request.principal_id,
            workspace=request.workspace,
            requested_profile=request.requested_profile,
            request_sha256=request_sha256,
            request_ref=request_ref,
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
            start_request_ref=request_ref,
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

    def _verify_start_snapshot(
        self,
        snapshot: RunSnapshot,
        *,
        request: RunStartRequest,
        request_sha256: str,
    ) -> None:
        if (
            snapshot.principal_id != request.principal_id
            or snapshot.workspace != request.workspace
            or snapshot.requested_profile != request.requested_profile
            or snapshot.registry_snapshot != request.registry_snapshot
            or snapshot.revision != 0
            or snapshot.status.value != "active"
            or snapshot.turn_seq != 0
            or snapshot.active_operation is not None
            or snapshot.child_runs
            or snapshot.updated_at != snapshot.started_at
        ):
            raise GoldenRunCorruptionError(
                "persisted workflow start failed its request receipt binding"
            )
        try:
            self._verify_registry_snapshot(snapshot.registry_snapshot)
            state = self._resolve_canonical_model(
                snapshot.state_ref,
                TrustedWorldState,
            )
            receipt = self._resolve_canonical_model(
                snapshot.receipt_head,
                WorkflowStartReceipt,
            )
            persisted_request = self._resolve_canonical_model(
                snapshot.start_request_ref,
                RunStartRequest,
            )
            for fact in state.facts:
                self._artifact_store.resolve(fact.source_artifact)
        except (
            ArtifactResolutionError,
            GoldenRunRegistryError,
            ValidationError,
            TypeError,
            ValueError,
        ) as error:
            raise GoldenRunCorruptionError(
                f"persisted workflow start failed its CAS closure: {error}"
            ) from error
        if (
            state.state_sha256 != snapshot.state_sha256
            or snapshot.state_ref.sha256
            != hashlib.sha256(_canonical_model_bytes(state)).hexdigest()
            or tuple(fact.source_artifact for fact in state.facts)
            != request.initial_fact_evidence
            or persisted_request != request
            or receipt.workflow_run_id != snapshot.workflow_run_id
            or receipt.principal_id != snapshot.principal_id
            or receipt.workspace != snapshot.workspace
            or receipt.requested_profile != snapshot.requested_profile
            or receipt.request_sha256 != request_sha256
            or receipt.request_ref != snapshot.start_request_ref
            or receipt.state_sha256 != snapshot.state_sha256
            or receipt.state_ref != snapshot.state_ref
            or receipt.registry_snapshot != snapshot.registry_snapshot
            or receipt.started_at != snapshot.started_at
        ):
            raise GoldenRunCorruptionError(
                "persisted workflow start failed its request receipt binding"
            )

    def _verify_registry_snapshot(
        self,
        artifact: ArtifactRef,
    ) -> RegistrySnapshot:
        try:
            snapshot = self._resolve_canonical_model(artifact, RegistrySnapshot)
        except ArtifactResolutionError as error:
            raise GoldenRunRegistryError(
                "HARN_ARTIFACT_UNAVAILABLE",
                f"registry snapshot is unavailable: {error}",
            ) from error
        except (ValidationError, TypeError, ValueError) as error:
            raise GoldenRunRegistryError(
                "HARN_INPUT_SCHEMA_INVALID",
                f"registry snapshot is not strict canonical JSON: {error}",
            ) from error
        for entry in snapshot.qualified_skills:
            try:
                descriptor_model = (
                    SkillDescriptor
                    if entry.descriptor_ref.schema_version == "harness.skill_descriptor.v1"
                    else SkillDescriptorV2
                )
                descriptor = self._resolve_canonical_model(
                    entry.descriptor_ref,
                    descriptor_model,
                )
                qualification = self._resolve_canonical_model(
                    entry.qualification_ref,
                    SkillQualification,
                )
                report = self._resolve_canonical_model(
                    entry.qualification_report_ref,
                    QualificationReportV1,
                )
            except ArtifactResolutionError as error:
                raise GoldenRunRegistryError(
                    "HARN_ARTIFACT_UNAVAILABLE",
                    f"registry qualification artifact is unavailable: {entry.skill_ref}",
                ) from error
            except (ValidationError, TypeError, ValueError) as error:
                raise GoldenRunRegistryError(
                    "HARN_INPUT_SCHEMA_INVALID",
                    f"registry qualification artifact is invalid: {entry.skill_ref}: {error}",
                ) from error
            descriptor_skill_ref = f"{descriptor.skill_id}@{descriptor.version}"
            if (
                descriptor_skill_ref != entry.skill_ref
                or descriptor.mcp_tool_name != entry.mcp_tool_name
                or descriptor.qualification_artifact != entry.qualification_ref
                or qualification.skill_ref != entry.skill_ref
                or qualification.report_sha256 != entry.qualification_report_ref.sha256
                or report.skill_ref != entry.skill_ref
                or report.status != qualification.status
                or report.deterministic_case_id != qualification.deterministic_case_id
                or report.regression_command != qualification.regression_command
                or report.implementation_sha256 != descriptor.implementation_sha256
            ):
                raise GoldenRunRegistryError(
                    "HARN_SKILL_UNQUALIFIED",
                    f"registry snapshot has an unbound qualification closure: {entry.skill_ref}",
                )
            try:
                schema_model(descriptor.input_schema)
                schema_model(descriptor.output_schema)
            except KeyError as error:
                raise GoldenRunRegistryError(
                    "HARN_DEPENDENCY_DRIFT",
                    f"registry descriptor references an unavailable schema: {entry.skill_ref}",
                ) from error
        return snapshot

    def _resolve_canonical_model(
        self,
        artifact: ArtifactRef,
        model: type[BaseModel],
    ) -> BaseModel:
        payload = self._artifact_store.resolve(artifact).path.read_bytes()
        parsed = model.model_validate_json(payload)
        if payload != _canonical_model_bytes(parsed):
            raise ValueError("artifact does not use canonical JSON bytes")
        return parsed

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


class _SQLiteWorkflowStartStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._path)) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS golden_workflow_starts (
                    principal_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    snapshot_json BLOB NOT NULL,
                    PRIMARY KEY (principal_id, idempotency_key)
                )
                """
            )

    def get_or_create(
        self,
        *,
        principal_id: str,
        idempotency_key: str,
        request_sha256: str,
        factory: Callable[[], RunSnapshot],
    ) -> RunSnapshot:
        with closing(sqlite3.connect(self._path)) as connection, connection:
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT request_sha256, snapshot_json
                FROM golden_workflow_starts
                WHERE principal_id = ? AND idempotency_key = ?
                """,
                (principal_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing[0] != request_sha256:
                    raise GoldenRunConflictError(
                        "idempotency key is already bound to a different request"
                    )
                payload = existing[1]
                try:
                    snapshot = RunSnapshot.model_validate_json(payload)
                except (ValidationError, TypeError, ValueError) as error:
                    raise GoldenRunCorruptionError(
                        f"cannot reconstruct persisted workflow start: {error}"
                    ) from error
                if type(payload) is not bytes or payload != _canonical_model_bytes(snapshot):
                    raise GoldenRunCorruptionError(
                        "persisted workflow start does not use canonical snapshot bytes"
                    )
                return snapshot

            snapshot = factory()
            connection.execute(
                """
                INSERT INTO golden_workflow_starts (
                    principal_id, idempotency_key, request_sha256, snapshot_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    principal_id,
                    idempotency_key,
                    request_sha256,
                    _canonical_model_bytes(snapshot),
                ),
            )
            return snapshot


__all__ = [
    "GoldenRunConflictError",
    "GoldenRunCorruptionError",
    "GoldenRunHarness",
    "GoldenRunRegistryError",
]
