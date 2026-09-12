"""Bounded development measurements that do not alter qualified implementations.

The observer is installed only by a development deployment.  It watches Python's
read-only ``open`` audit events for the existing ``LocalArtifactStore`` and records
per-MCP-call deltas.  It neither caches content nor changes a verifier result.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from .artifacts import LocalArtifactStore
from .schemas.common import ArtifactRef

_ATTRIBUTE = "_limited_preview_development_diagnostics_v1"
_TABLE = "development_preview_diagnostic_samples"
_AUDIT_LOCK = threading.Lock()
_AUDIT_INSTALLED = False


@dataclass(frozen=True, slots=True)
class DevelopmentMeasurementBasis:
    scope: str = "one development MCP process and exact tool call"
    hash_scans: str = (
        "Python open audit events at the existing LocalArtifactStore full-content hash loops"
    )
    hash_logical_bytes: str = "regular-file size observed when each full hash scan opened"
    runtime_manifest_deep_reads: str = (
        "canonical manifest payload reads directly inside read_runtime_manifest"
    )
    qualified_skill_bindings: str = (
        "new exact Skill bindings completed by the process-local LimitedPreviewRuntime"
    )
    process_logical_read_bytes: str = (
        "Linux /proc/self/io rchar delta; includes non-CAS reads and excludes child processes"
    )
    process_storage_read_bytes: str = (
        "Linux /proc/self/io read_bytes delta; page-cache hits may be zero and child processes "
        "are excluded"
    )
    unavailable: str = (
        "CAS-only storage reads and deployment-tree scan counts are not attributable at this seam"
    )


@dataclass(frozen=True, slots=True)
class DevelopmentDiagnosticCounters:
    local_artifact_full_hash_scan_count: int
    local_artifact_full_hash_logical_bytes: int
    cas_full_hash_scan_count: int
    cas_full_hash_logical_bytes: int
    runtime_manifest_deep_read_count: int
    qualified_skill_binding_count: int
    process_logical_read_bytes: int | None
    process_storage_read_bytes: int | None
    cas_storage_read_bytes: None = None
    runtime_deployment_tree_scan_count: None = None


@dataclass(frozen=True, slots=True)
class DevelopmentDiagnosticSample:
    workflow_run_id: UUID
    process_scope_id: UUID
    sequence: int
    operation: str
    status: str
    started_at: datetime
    ended_at: datetime
    counters: DevelopmentDiagnosticCounters
    measurement_basis: DevelopmentMeasurementBasis

    def document(self) -> dict[str, object]:
        value = asdict(self)
        value.update(
            kind="limited_preview_development_diagnostic_sample.v1",
            workflow_run_id=str(self.workflow_run_id),
            process_scope_id=str(self.process_scope_id),
            started_at=self.started_at.isoformat(),
            ended_at=self.ended_at.isoformat(),
        )
        return value


@dataclass(frozen=True, slots=True)
class DevelopmentExecutionMetrics:
    sample_count: int
    totals: DevelopmentDiagnosticCounters
    samples: tuple[DevelopmentDiagnosticSample, ...]
    created_at: datetime
    qualification_status: Literal["development_unqualified"] = "development_unqualified"
    measurement_basis: DevelopmentMeasurementBasis = DevelopmentMeasurementBasis()

    def document(self) -> dict[str, object]:
        return {
            "kind": "limited_preview_development_execution_metrics.v1",
            "qualification_status": self.qualification_status,
            "scope": "recorded exact MCP calls only",
            "sample_count": self.sample_count,
            "totals": asdict(self.totals),
            "measurement_basis": asdict(self.measurement_basis),
            "samples": [sample.document() for sample in self.samples],
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class _Token:
    workflow_run_id: UUID
    process_scope_id: UUID
    sequence: int
    operation: str
    started_at: datetime
    counters: DevelopmentDiagnosticCounters
    process_io: tuple[int, int] | None


def _zero() -> DevelopmentDiagnosticCounters:
    return DevelopmentDiagnosticCounters(0, 0, 0, 0, 0, 0, None, None)


def _process_io() -> tuple[int, int] | None:
    try:
        values = {}
        for line in Path("/proc/self/io").read_text(encoding="ascii").splitlines():
            name, raw = line.split(":", 1)
            if name in {"rchar", "read_bytes"}:
                values[name] = int(raw)
        return values["rchar"], values["read_bytes"]
    except (FileNotFoundError, KeyError, OSError, ValueError):
        return None


class DevelopmentDiagnostics:
    """One process-local counter set with explicit operation snapshots and reset."""

    def __init__(self, store: LocalArtifactStore, runtime_manifests: tuple[ArtifactRef, ...]):
        self.store = store
        self.process_scope_id = uuid4()
        self._manifest_paths = frozenset(
            store.root / "sha256" / ref.sha256[:2] / ref.sha256 for ref in runtime_manifests
        )
        self._lock = threading.Lock()
        self._values = [0, 0, 0, 0, 0, 0]
        self._sequence = 0
        self._active: set[int] = set()

    def _count_hash(self, path: Path) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            return
        with self._lock:
            self._values[0] += 1
            self._values[1] += size
            if _under(path, self.store.root / "sha256"):
                self._values[2] += 1
                self._values[3] += size

    def _count_manifest(self) -> None:
        with self._lock:
            self._values[4] += 1

    def note_binding(self) -> None:
        with self._lock:
            self._values[5] += 1

    def snapshot(self) -> DevelopmentDiagnosticCounters:
        with self._lock:
            values = tuple(self._values)
        return DevelopmentDiagnosticCounters(*values, None, None)

    def reset(self) -> None:
        with self._lock:
            if self._active:
                raise ValueError("cannot reset development diagnostics during an active operation")
            self._values[:] = [0] * 6
            self._sequence = 0
            self.process_scope_id = uuid4()

    def begin(self, *, workflow_id: UUID, operation: str) -> _Token:
        if not operation.strip():
            raise ValueError("development diagnostic operation cannot be empty")
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            self._active.add(sequence)
        return _Token(
            workflow_run_id=UUID(str(workflow_id)),
            process_scope_id=self.process_scope_id,
            sequence=sequence,
            operation=operation,
            started_at=datetime.now(UTC),
            counters=self.snapshot(),
            process_io=_process_io(),
        )

    def finish(self, token: _Token, *, status: str) -> DevelopmentDiagnosticSample:
        if token.process_scope_id != self.process_scope_id or not status.strip():
            raise ValueError("development diagnostic token or status differs")
        current = self.snapshot()
        process_io = _process_io()
        with self._lock:
            if token.sequence not in self._active:
                raise ValueError("development diagnostic token is not active")
            self._active.remove(token.sequence)
        before = token.counters
        logical = (
            None
            if token.process_io is None or process_io is None
            else max(0, process_io[0] - token.process_io[0])
        )
        storage = (
            None
            if token.process_io is None or process_io is None
            else max(0, process_io[1] - token.process_io[1])
        )
        counters = DevelopmentDiagnosticCounters(
            local_artifact_full_hash_scan_count=(
                current.local_artifact_full_hash_scan_count
                - before.local_artifact_full_hash_scan_count
            ),
            local_artifact_full_hash_logical_bytes=(
                current.local_artifact_full_hash_logical_bytes
                - before.local_artifact_full_hash_logical_bytes
            ),
            cas_full_hash_scan_count=(
                current.cas_full_hash_scan_count - before.cas_full_hash_scan_count
            ),
            cas_full_hash_logical_bytes=(
                current.cas_full_hash_logical_bytes - before.cas_full_hash_logical_bytes
            ),
            runtime_manifest_deep_read_count=(
                current.runtime_manifest_deep_read_count - before.runtime_manifest_deep_read_count
            ),
            qualified_skill_binding_count=(
                current.qualified_skill_binding_count - before.qualified_skill_binding_count
            ),
            process_logical_read_bytes=logical,
            process_storage_read_bytes=storage,
        )
        return DevelopmentDiagnosticSample(
            workflow_run_id=token.workflow_run_id,
            process_scope_id=token.process_scope_id,
            sequence=token.sequence,
            operation=token.operation,
            status=status,
            started_at=token.started_at,
            ended_at=datetime.now(UTC),
            counters=counters,
            measurement_basis=DevelopmentMeasurementBasis(),
        )


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _store_and_caller():
    frame = sys._getframe(2)
    store = None
    first_harness = None
    frames = []
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        name = frame.f_code.co_name
        if module.startswith("self_improving.harness.") and module != __name__:
            frames.append((module, name, frame))
            if first_harness is None:
                first_harness = (module, name)
        for candidate in (frame.f_locals.get("self"), frame.f_locals.get("store")):
            if type(candidate) is LocalArtifactStore:
                store = candidate
                break
        if store is not None and first_harness is not None:
            break
        frame = frame.f_back
    return store, first_harness, frames


def _audit_open(event: str, arguments: tuple[object, ...]) -> None:
    if event != "open" or not arguments or not isinstance(arguments[0], (str, bytes)):
        return
    store, caller, frames = _store_and_caller()
    tracker = None if store is None else getattr(store, _ATTRIBUTE, None)
    if type(tracker) is not DevelopmentDiagnostics:
        return
    try:
        path = Path(arguments[0]).resolve()
    except (OSError, ValueError):
        return
    if any(
        module == "self_improving.harness.artifacts" and name == "_sha256"
        for module, name, _frame in frames
    ):
        tracker._count_hash(path)
        return
    put = next(
        (
            frame
            for module, name, frame in frames
            if module == "self_improving.harness.artifacts" and name == "put_file"
        ),
        None,
    )
    if put is not None and path == put.f_locals.get("source_path"):
        tracker._count_hash(path)
        return
    if (
        caller == ("self_improving.harness.genesis_runtime_manifest", "read_runtime_manifest")
        and path in tracker._manifest_paths
    ):
        tracker._count_manifest()


def install_development_diagnostics(
    store: LocalArtifactStore, *, runtime_manifests: tuple[ArtifactRef, ...]
) -> DevelopmentDiagnostics:
    """Install one observer on an exact store; no verifier or store method is replaced."""

    if type(store) is not LocalArtifactStore:
        raise TypeError("development diagnostics require the exact local artifact store")
    if any(
        type(ref) is not ArtifactRef
        or ref.schema_version != "harness.genesis_runtime_manifest.v1"
        or ref.uri != f"artifact://sha256/{ref.sha256}"
        for ref in runtime_manifests
    ):
        raise ValueError("development diagnostics require exact runtime manifest references")
    existing = getattr(store, _ATTRIBUTE, None)
    if existing is not None:
        raise ValueError("development diagnostics are already installed on this store")
    tracker = DevelopmentDiagnostics(store, runtime_manifests)
    setattr(store, _ATTRIBUTE, tracker)
    global _AUDIT_INSTALLED
    with _AUDIT_LOCK:
        if not _AUDIT_INSTALLED:
            sys.addaudithook(_audit_open)
            _AUDIT_INSTALLED = True
    return tracker


def note_qualified_skill_binding(store: LocalArtifactStore, skill_ref: str) -> None:
    """Count one completed process-local binding; release stores have no observer."""

    tracker = getattr(store, _ATTRIBUTE, None)
    if type(tracker) is DevelopmentDiagnostics:
        tracker.note_binding()


def begin_development_mcp_measurement(service, workflow_id, operation):
    try:
        mode = vars(service).get("execution_mode", vars(type(service)).get("execution_mode"))
    except TypeError:
        mode = vars(type(service)).get("execution_mode")
    if mode != "development" or workflow_id is None:
        return None
    tracker = getattr(getattr(service, "store", None), _ATTRIBUTE, None)
    if type(tracker) is not DevelopmentDiagnostics:
        raise ValueError("development service lacks its diagnostic observer")
    return tracker.begin(workflow_id=UUID(str(workflow_id)), operation=operation)


def finish_development_mcp_measurement(service, token, status):
    if token is None:
        return None
    tracker = getattr(service.store, _ATTRIBUTE, None)
    if type(tracker) is not DevelopmentDiagnostics:
        raise ValueError("development diagnostic observer changed during an MCP call")
    sample = tracker.finish(token, status=status)
    append_development_diagnostic_sample(service.journal.path, sample)
    return sample


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()


def append_development_diagnostic_sample(
    database: Path, sample: DevelopmentDiagnosticSample
) -> None:
    raw = _canonical(sample.document())
    with sqlite3.connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} "
            "(workflow TEXT NOT NULL, process_scope TEXT NOT NULL, sequence INTEGER NOT NULL, "
            "payload BLOB NOT NULL, digest TEXT NOT NULL, "
            "PRIMARY KEY(workflow,process_scope,sequence))"
        )
        try:
            connection.execute(
                f"INSERT INTO {_TABLE} VALUES (?,?,?,?,?)",
                (
                    str(sample.workflow_run_id),
                    str(sample.process_scope_id),
                    sample.sequence,
                    raw,
                    hashlib.sha256(raw).hexdigest(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError(
                "development diagnostic sample identity is already occupied"
            ) from error


def _sample(value: object) -> DevelopmentDiagnosticSample:
    if not isinstance(value, dict) or value.get("kind") != (
        "limited_preview_development_diagnostic_sample.v1"
    ):
        raise ValueError("development diagnostic sample is corrupt")
    try:
        counters = DevelopmentDiagnosticCounters(**value["counters"])
        basis = DevelopmentMeasurementBasis(**value["measurement_basis"])
        sample = DevelopmentDiagnosticSample(
            workflow_run_id=UUID(value["workflow_run_id"]),
            process_scope_id=UUID(value["process_scope_id"]),
            sequence=value["sequence"],
            operation=value["operation"],
            status=value["status"],
            started_at=datetime.fromisoformat(value["started_at"]),
            ended_at=datetime.fromisoformat(value["ended_at"]),
            counters=counters,
            measurement_basis=basis,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("development diagnostic sample is corrupt") from error
    if (
        sample.sequence < 1
        or not sample.operation.strip()
        or not sample.status.strip()
        or sample.started_at.tzinfo is None
        or sample.ended_at.tzinfo is None
        or sample.started_at.utcoffset() != UTC.utcoffset(sample.started_at)
        or sample.ended_at.utcoffset() != UTC.utcoffset(sample.ended_at)
        or sample.ended_at < sample.started_at
        or sample.measurement_basis != DevelopmentMeasurementBasis()
        or any(
            type(item) is not int or item < 0
            for item in asdict(sample.counters).values()
            if item is not None
        )
        or sample.document() != value
    ):
        raise ValueError("development diagnostic sample is corrupt")
    return sample


def read_development_diagnostic_samples(
    database: Path, workflow_id: UUID
) -> tuple[DevelopmentDiagnosticSample, ...]:
    path = Path(database)
    if not path.is_file():
        return ()
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        if (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)
            ).fetchone()
            is None
        ):
            return ()
        rows = connection.execute(
            f"SELECT workflow,process_scope,sequence,payload,digest FROM {_TABLE} "
            "WHERE workflow=? ORDER BY payload",
            (str(UUID(str(workflow_id))),),
        ).fetchall()
    result = []
    for workflow, scope, sequence, raw, digest in rows:
        try:
            value = json.loads(raw)
            sample = _sample(value)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError, ValueError) as error:
            raise ValueError("development diagnostic sample is corrupt") from error
        if (
            hashlib.sha256(raw).hexdigest() != digest
            or raw != _canonical(value)
            or workflow != str(sample.workflow_run_id)
            or scope != str(sample.process_scope_id)
            or sequence != sample.sequence
        ):
            raise ValueError("development diagnostic sample is corrupt")
        result.append(sample)
    result.sort(key=lambda item: (item.started_at, str(item.process_scope_id), item.sequence))
    return tuple(result)


def _optional_total(samples, name):
    values = [getattr(sample.counters, name) for sample in samples]
    return None if any(value is None for value in values) else sum(values)


def build_development_execution_metrics(
    samples: tuple[DevelopmentDiagnosticSample, ...], *, created_at: datetime | None = None
) -> DevelopmentExecutionMetrics:
    ordered = tuple(
        sorted(
            samples,
            key=lambda item: (item.started_at, str(item.process_scope_id), item.sequence),
        )
    )
    if len({(item.process_scope_id, item.sequence) for item in ordered}) != len(ordered):
        raise ValueError("development diagnostic samples must be unique")
    totals = DevelopmentDiagnosticCounters(
        local_artifact_full_hash_scan_count=sum(
            item.counters.local_artifact_full_hash_scan_count for item in ordered
        ),
        local_artifact_full_hash_logical_bytes=sum(
            item.counters.local_artifact_full_hash_logical_bytes for item in ordered
        ),
        cas_full_hash_scan_count=sum(item.counters.cas_full_hash_scan_count for item in ordered),
        cas_full_hash_logical_bytes=sum(
            item.counters.cas_full_hash_logical_bytes for item in ordered
        ),
        runtime_manifest_deep_read_count=sum(
            item.counters.runtime_manifest_deep_read_count for item in ordered
        ),
        qualified_skill_binding_count=sum(
            item.counters.qualified_skill_binding_count for item in ordered
        ),
        process_logical_read_bytes=_optional_total(ordered, "process_logical_read_bytes"),
        process_storage_read_bytes=_optional_total(ordered, "process_storage_read_bytes"),
    )
    timestamp = created_at or (ordered[-1].ended_at if ordered else datetime.now(UTC))
    return DevelopmentExecutionMetrics(
        sample_count=len(ordered), totals=totals, samples=ordered, created_at=timestamp
    )


def publish_development_execution_metrics(
    store: LocalArtifactStore,
    database: Path,
    workflow_id: UUID,
    *,
    created_at: datetime,
) -> tuple[ArtifactRef, DevelopmentExecutionMetrics]:
    """Seal the validated journal samples into one independently readable CAS log."""

    if type(store) is not LocalArtifactStore:
        raise TypeError("development metrics require the exact workflow CAS")
    metrics = build_development_execution_metrics(
        read_development_diagnostic_samples(database, workflow_id), created_at=created_at
    )
    raw = _canonical(metrics.document())
    with tempfile.TemporaryDirectory(prefix="preview-development-metrics-") as directory:
        path = Path(directory) / "development-execution-metrics.json"
        path.write_bytes(raw)
        ref = store.put_file(
            path,
            name=path.name,
            media_type="application/json",
            schema_version=None,
        )
    if store.resolve(ref).path.read_bytes() != raw:
        raise ValueError("development metrics CAS bytes differ from journal samples")
    return ref, metrics


__all__ = [
    "DevelopmentDiagnosticCounters",
    "DevelopmentDiagnosticSample",
    "DevelopmentDiagnostics",
    "DevelopmentExecutionMetrics",
    "append_development_diagnostic_sample",
    "begin_development_mcp_measurement",
    "build_development_execution_metrics",
    "finish_development_mcp_measurement",
    "install_development_diagnostics",
    "note_qualified_skill_binding",
    "publish_development_execution_metrics",
    "read_development_diagnostic_samples",
]
