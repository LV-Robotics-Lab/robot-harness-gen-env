"""Durable execution intents and commit-last Genesis asset qualification."""

import hashlib
import json
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

from .artifacts import LocalArtifactStore
from .genesis_runtime_execution import (
    GenesisRuntimeExecutionError,
    GenesisRuntimeExecutionProducer,
    GenesisRuntimeExecutionVerifier,
    LiveGenesisRuntimeExecution,
)
from .schemas.common import ArtifactRef
from .schemas.genesis_runtime_execution import GenesisRuntimeExecutionReceiptV1
from .schemas.x2env_compile_evidence import (
    AssetQualificationCheckV1,
    GenesisAssetQualificationV1,
    QualifiedAssetSubjectV1,
    canonical_evidence_sha256,
)


class GenesisAssetQualificationApplicationError(ValueError):
    """Identity, durable state, or evidence cannot support a qualification operation."""


@dataclass(frozen=True)
class GenesisAssetQualificationRun:
    run_id: UUID
    request_ref: ArtifactRef
    status: str
    execution_ref: ArtifactRef | None = None
    qualification_ref: ArtifactRef | None = None
    failure_ref: ArtifactRef | None = None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class GenesisAssetQualificationApplication:
    """Caller UUIDs execute at most once under operator-approved runtime configuration."""

    def __init__(
        self,
        state_path: Path,
        artifact_store: LocalArtifactStore,
        approved_manifest_ref: ArtifactRef,
    ):
        self._state_path = state_path
        self._artifact_store = artifact_store
        self._approved_manifest_ref = approved_manifest_ref
        self._source = Path(__file__).read_bytes()
        self._issuer_sha256 = hashlib.sha256(self._source).hexdigest()
        self._config = _canonical(
            {
                "schema": 1,
                "manifest": approved_manifest_ref.model_dump(mode="json"),
                "issuer_sha256": self.issuer_sha256,
            }
        )
        existing = state_path.exists()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            if not existing:
                connection.executescript(
                    "CREATE TABLE configuration "
                    "(identity TEXT NOT NULL, run_count INTEGER NOT NULL);"
                    "CREATE TABLE runs "
                    "(run_id TEXT PRIMARY KEY, record TEXT NOT NULL, sha256 TEXT NOT NULL);"
                )
                connection.execute("INSERT INTO configuration VALUES (?, 0)", (self._config,))
            self._check(connection)

    @property
    def state_path(self):
        return self._state_path

    @property
    def artifact_store(self):
        return self._artifact_store

    @property
    def approved_manifest_ref(self):
        return self._approved_manifest_ref

    @property
    def issuer_sha256(self):
        return self._issuer_sha256

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.state_path, isolation_level=None, timeout=60)
        try:
            yield connection
        except sqlite3.Error as error:
            raise GenesisAssetQualificationApplicationError(
                "qualification SQLite state is corrupt"
            ) from error
        finally:
            connection.close()

    def _check(self, connection):
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise GenesisAssetQualificationApplicationError(
                "qualification SQLite integrity differs"
            )
        configuration = connection.execute(
            "SELECT identity,run_count FROM configuration"
        ).fetchall()
        count = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        if configuration != [(self._config, count)]:
            raise GenesisAssetQualificationApplicationError(
                "qualification configuration or row count differs"
            )

    def _uuid(self, run_id):
        if type(run_id) is not UUID or run_id.version != 4:
            raise GenesisAssetQualificationApplicationError("run_id must be an exact UUID4")

    def _decode(self, row):
        if row is None:
            return None
        run_id, document, digest = row
        if hashlib.sha256(document.encode()).hexdigest() != digest:
            raise GenesisAssetQualificationApplicationError("qualification record digest differs")
        value = json.loads(document)
        if (
            _canonical(value) != document
            or set(value)
            != {
                "run_id",
                "request_ref",
                "status",
                "execution_ref",
                "qualification_ref",
                "failure_ref",
            }
            or value["run_id"] != run_id
        ):
            raise GenesisAssetQualificationApplicationError("qualification record is noncanonical")
        refs = {
            key: ArtifactRef.model_validate(value[key]) if value[key] is not None else None
            for key in ("request_ref", "execution_ref", "qualification_ref", "failure_ref")
        }
        state = GenesisAssetQualificationRun(UUID(run_id), status=value["status"], **refs)
        expected = {
            "pending": (False, False, False),
            "failed": (False, False, True),
            "executed": (True, False, False),
            "qualified": (True, True, False),
        }
        actual = tuple(
            value is not None
            for value in (state.execution_ref, state.qualification_ref, state.failure_ref)
        )
        if actual != expected.get(state.status) or state.request_ref is None:
            raise GenesisAssetQualificationApplicationError("qualification record state differs")
        return state

    def _store(self, connection, state):
        document = _canonical(
            {
                "run_id": str(state.run_id),
                "status": state.status,
                **{
                    name: getattr(state, name).model_dump(mode="json")
                    if getattr(state, name) is not None
                    else None
                    for name in ("request_ref", "execution_ref", "qualification_ref", "failure_ref")
                },
            }
        )
        connection.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?)",
            (str(state.run_id), document, hashlib.sha256(document.encode()).hexdigest()),
        )

    def read(self, run_id: UUID) -> GenesisAssetQualificationRun | None:
        self._uuid(run_id)
        with self._connect() as connection:
            self._check(connection)
            state = self._decode(
                connection.execute("SELECT * FROM runs WHERE run_id=?", (str(run_id),)).fetchone()
            )
        if state is not None:
            self.artifact_store.resolve(self._issuer_ref())
            if state.execution_ref is not None:
                closure = self._execution_closure(state)
                if state.qualification_ref is not None:
                    actual = self.artifact_store.resolve(state.qualification_ref).path.read_bytes()
                    expected = self._qualification(state, closure)
                    if (
                        state.qualification_ref != self._qualification_ref(expected)
                        or actual != (_canonical(expected.model_dump(mode="json")) + "\n").encode()
                    ):
                        raise GenesisAssetQualificationApplicationError(
                            "qualification terminal differs"
                        )
            else:
                request, _, _ = GenesisRuntimeExecutionVerifier(self.artifact_store).verify_request(
                    state.request_ref
                )
                if request.manifest != self.approved_manifest_ref:
                    raise GenesisAssetQualificationApplicationError(
                        "record runtime is not operator-approved"
                    )
            if state.failure_ref is not None:
                diagnostic = json.loads(
                    self.artifact_store.resolve(state.failure_ref).path.read_bytes()
                )
                if diagnostic.get("status") != "failed" or diagnostic.get(
                    "request"
                ) != state.request_ref.model_dump(mode="json"):
                    raise GenesisAssetQualificationApplicationError("failure diagnostic differs")
                for value in diagnostic.get("failure_artifacts", ()):
                    self.artifact_store.resolve(ArtifactRef.model_validate(value))
        return state

    def reserve(self, request_ref: ArtifactRef, *, run_id: UUID) -> GenesisAssetQualificationRun:
        """Persist intent only; a reserved intent cannot implicitly execute on retry."""
        self._reserve(request_ref, run_id)
        return self.read(run_id)

    def _reserve(self, request_ref, run_id):
        self._uuid(run_id)
        request, _, _ = GenesisRuntimeExecutionVerifier(self.artifact_store).verify_request(
            request_ref
        )
        if request.manifest != self.approved_manifest_ref:
            raise GenesisAssetQualificationApplicationError("runtime is not operator-approved")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check(connection)
            previous = self._decode(
                connection.execute("SELECT * FROM runs WHERE run_id=?", (str(run_id),)).fetchone()
            )
            if previous is not None:
                if previous.request_ref != request_ref:
                    raise GenesisAssetQualificationApplicationError(
                        "UUID is bound to another canonical input"
                    )
                connection.execute("COMMIT")
                return previous, False
            self._publish(
                self._source, "genesis-asset-qualification-issuer.py", "text/x-python", None
            )
            state = GenesisAssetQualificationRun(run_id, request_ref, "pending")
            self._store(connection, state)
            connection.execute("UPDATE configuration SET run_count=run_count+1")
            connection.execute("COMMIT")
            return state, True

    def _publish(self, payload, name, media_type, schema_version):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "record"
            path.write_bytes(payload)
            return self.artifact_store.put_file(
                path, name=name, media_type=media_type, schema_version=schema_version
            )

    def invoke(self, request_ref: ArtifactRef, *, run_id: UUID, roots) -> ArtifactRef:
        state, fresh = self._reserve(request_ref, run_id)
        if not fresh:
            state = self.read(run_id)
            if state.status == "qualified":
                return state.qualification_ref
            if state.status == "executed":
                return self._finish(state)
            raise GenesisAssetQualificationApplicationError(
                f"{state.status} intent cannot execute again"
            )
        state, live = self._execute_reserved(state, roots)
        return self._finish(state, live)

    def execute(
        self, request_ref: ArtifactRef, *, run_id: UUID, roots
    ) -> GenesisAssetQualificationRun:
        """Execute once and persist the execution receipt, without publishing qualification."""
        state, fresh = self._reserve(request_ref, run_id)
        if not fresh:
            state = self.read(run_id)
            if state.status in ("executed", "qualified"):
                return state
            raise GenesisAssetQualificationApplicationError(
                f"{state.status} intent cannot execute again"
            )
        state, _ = self._execute_reserved(state, roots)
        return state

    def _execute_reserved(self, state, roots):
        try:
            live = GenesisRuntimeExecutionProducer(
                self.artifact_store, self.approved_manifest_ref
            ).produce(state.request_ref, roots=roots)
        except GenesisRuntimeExecutionError as error:
            if error.diagnostic_ref is None:
                raise GenesisAssetQualificationApplicationError(
                    "execution failed without durable diagnostic"
                ) from error
            self._transition(
                state, replace(state, status="failed", failure_ref=error.diagnostic_ref)
            )
            raise GenesisAssetQualificationApplicationError(
                "execution failed; terminal diagnostic retained"
            ) from error
        executed = replace(state, status="executed", execution_ref=live.receipt_ref)
        self._transition(state, executed)
        return executed, live

    def _issuer_ref(self):
        return ArtifactRef(
            name="genesis-asset-qualification-issuer.py",
            uri=f"artifact://sha256/{self.issuer_sha256}",
            sha256=self.issuer_sha256,
            bytes=len(self._source),
            media_type="text/x-python",
            schema_version=None,
        )

    def _execution_closure(self, state):
        closure = GenesisRuntimeExecutionVerifier(self.artifact_store).read_receipt(
            state.execution_ref
        )
        if (
            closure.observation.request != state.request_ref
            or closure.request.manifest != self.approved_manifest_ref
        ):
            raise GenesisAssetQualificationApplicationError("execution journal identity differs")
        return closure

    def _qualification(self, state, closure):
        """Pure construction, called only from live execution or trusted journal recovery."""
        receipt = GenesisRuntimeExecutionReceiptV1.model_validate_json(
            self.artifact_store.resolve(state.execution_ref).path.read_bytes()
        )
        observation = closure.observation
        common = (
            self._issuer_ref(),
            state.request_ref,
            state.execution_ref,
            receipt.observation,
            closure.request.manifest,
            closure.request.representation,
            closure.request.runtime_lock,
            observation.probe_input,
            observation.load_observation,
            observation.load_evidence,
        )

        def evidence(refs):
            by_key = {(ref.sha256, ref.bytes, ref.media_type): ref for ref in refs}
            return tuple(by_key[key] for key in sorted(by_key))

        representation = closure.probe.representation
        payload = {
            "schema_version": "harness.asset_qualification.v1",
            "qualification_sha256": "0" * 64,
            "subject": QualifiedAssetSubjectV1(
                model_subject=representation.subject,
                representation_sha256=representation.representation_sha256,
                loader_closure_sha256=representation.loader_closure_sha256,
            ),
            "representation": closure.request.representation,
            "runtime_lock": closure.request.runtime_lock,
            "requested_profile": "genesis.rigid_scene@1",
            "issuer_implementation_sha256": self.issuer_sha256,
            "checks": (
                AssetQualificationCheckV1(
                    code="backend_load", status="pass", evidence=evidence(common)
                ),
                AssetQualificationCheckV1(
                    code="collision_probe",
                    status="pass",
                    evidence=evidence(
                        common
                        + (
                            observation.trace_rows,
                            observation.contact_trace,
                            observation.probe_report,
                        )
                    ),
                ),
            ),
            "status": "pass",
            "backend_load_executed": True,
            "collision_probe_executed": True,
            "qualified": True,
        }
        payload["qualification_sha256"] = canonical_evidence_sha256(payload, "qualification_sha256")
        return GenesisAssetQualificationV1.model_validate(payload)

    def _qualification_ref(self, value):
        payload = (_canonical(value.model_dump(mode="json")) + "\n").encode()
        digest = hashlib.sha256(payload).hexdigest()
        return ArtifactRef(
            name="genesis-asset-qualification.json",
            uri=f"artifact://sha256/{digest}",
            sha256=digest,
            bytes=len(payload),
            media_type="application/json",
            schema_version=value.schema_version,
        )

    def _finish(self, state, live=None):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check(connection)
            current = self._decode(
                connection.execute(
                    "SELECT * FROM runs WHERE run_id=?", (str(state.run_id),)
                ).fetchone()
            )
            if current != state or state.status != "executed":
                raise GenesisAssetQualificationApplicationError(
                    "execution journal changed before issue"
                )
            if live is not None:
                if (
                    type(live) is not LiveGenesisRuntimeExecution
                    or live.receipt_ref != state.execution_ref
                ):
                    raise GenesisAssetQualificationApplicationError(
                        "issuer requires exact live capability"
                    )
                closure = live.closure
            else:
                closure = self._execution_closure(state)
            self.artifact_store.resolve(self._issuer_ref())
            qualification = self._qualification(state, closure)
            reference = self._publish(
                (_canonical(qualification.model_dump(mode="json")) + "\n").encode(),
                "genesis-asset-qualification.json",
                "application/json",
                qualification.schema_version,
            )
            self._store(connection, replace(state, status="qualified", qualification_ref=reference))
            connection.execute("COMMIT")
            return reference

    def _transition(self, before, after):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check(connection)
            current = self._decode(
                connection.execute(
                    "SELECT * FROM runs WHERE run_id=?", (str(before.run_id),)
                ).fetchone()
            )
            if current != before:
                raise GenesisAssetQualificationApplicationError(
                    "qualification state changed concurrently"
                )
            self._store(connection, after)
            connection.execute("COMMIT")
