"""Single SQLite authority for workflow snapshots and idempotency."""

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .contracts import ArtifactRef, OperationRecord, ToolResult, WorkflowSnapshot, X2EnvRequest


def process_identity(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return f"{pid}:{stat[19]}:{boot}"
    except FileNotFoundError:
        return None


class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.cas = root / "cas"
        self.database = root / "harness.sqlite"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS workflows (
                workflow_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                request_sha256 TEXT NOT NULL, snapshot TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS asset_versions (
                version_sha256 TEXT PRIMARY KEY, asset_id TEXT NOT NULL,
                category TEXT NOT NULL, record TEXT NOT NULL)""")

    def register_asset(
        self, version_sha256: str, asset_id: str, category: str, record_json: str
    ) -> None:
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT asset_id, category, record FROM asset_versions WHERE version_sha256=?",
                (version_sha256,),
            ).fetchone()
            if row:
                if row != (asset_id, category, record_json):
                    raise ValueError("immutable asset version conflict")
                return
            db.execute(
                "INSERT INTO asset_versions VALUES (?, ?, ?, ?)",
                (version_sha256, asset_id, category, record_json),
            )

    def asset_version(self, version_sha256: str) -> str:
        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute(
                "SELECT record FROM asset_versions WHERE version_sha256=?", (version_sha256,)
            ).fetchone()
        if row is None:
            raise KeyError(version_sha256)
        return row[0]

    def asset_versions(self, category: str) -> tuple[str, ...]:
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute(
                "SELECT record FROM asset_versions WHERE category=? ORDER BY version_sha256",
                (category,),
            ).fetchall()
        return tuple(row[0] for row in rows)

    def submit(self, request: X2EnvRequest) -> WorkflowSnapshot:
        digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT request_sha256, snapshot FROM workflows WHERE idempotency_key=?",
                (request.idempotency_key,),
            ).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError("idempotency key already belongs to a different request")
                return WorkflowSnapshot.model_validate_json(row[1])
            snapshot = WorkflowSnapshot(
                workflow_id=str(uuid4()), status="active", revision=0, request=request
            )
            db.execute(
                "INSERT INTO workflows VALUES (?, ?, ?, ?)",
                (snapshot.workflow_id, request.idempotency_key, digest, snapshot.model_dump_json()),
            )
            return snapshot

    def status(self, workflow_id: str) -> WorkflowSnapshot:
        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute(
                "SELECT snapshot FROM workflows WHERE workflow_id=?", (workflow_id,)
            ).fetchone()
        if row is None:
            raise KeyError(workflow_id)
        return WorkflowSnapshot.model_validate_json(row[0])

    def write_artifact(self, data: bytes, media_type: str) -> ArtifactRef:
        ref = ArtifactRef(
            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), media_type=media_type
        )
        directory = self.cas / ref.sha256[:2]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / ref.sha256
        with tempfile.NamedTemporaryFile(dir=directory) as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
            try:
                os.link(temporary.name, target)
            except FileExistsError:
                self.read_artifact(ref)
        return ref

    def read_artifact(self, ref: ArtifactRef) -> bytes:
        data = (self.cas / ref.sha256[:2] / ref.sha256).read_bytes()
        if len(data) != ref.size_bytes or hashlib.sha256(data).hexdigest() != ref.sha256:
            raise ValueError("artifact integrity mismatch")
        return data

    def claim(self, workflow_id: str) -> WorkflowSnapshot:
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT snapshot FROM workflows WHERE workflow_id=?", (workflow_id,)
            ).fetchone()
            if row is None:
                raise KeyError(workflow_id)
            snapshot = WorkflowSnapshot.model_validate_json(row[0])
            if snapshot.status in {"succeeded", "failed", "cancelled"}:
                return snapshot
            if (
                snapshot.owner
                and process_identity(int(snapshot.owner.split(":")[0])) == snapshot.owner
            ):
                raise ValueError("workflow owner is still active")
            operations = list(snapshot.operations)
            if operations and operations[-1].status == "running":
                old = operations[-1]
                if old.capability.startswith("codex."):
                    blocker = self._orphan_blocker(snapshot, old)
                    if blocker is not None:
                        reason, resource = blocker
                        blocked = snapshot.model_copy(
                            update={
                                "status": "blocked",
                                "owner": None,
                                "stop_reason": reason,
                                "required_resources": (resource,),
                            }
                        )
                        db.execute(
                            "UPDATE workflows SET snapshot=? WHERE workflow_id=?",
                            (blocked.model_dump_json(), workflow_id),
                        )
                        return blocked
                result = ToolResult(
                    operation_id=old.operation_id,
                    status="failed",
                    error_code="recoverable_dead_owner",
                )
                operations[-1] = old.model_copy(
                    update={
                        "status": "failed",
                        "result": result,
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            if snapshot.input_bundle is None:
                operations.append(
                    OperationRecord(
                        operation_id=str(uuid4()),
                        capability="ingest",
                        version="1.0.0",
                        status="running",
                        started_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
            snapshot = snapshot.model_copy(
                update={
                    "owner": process_identity(os.getpid()),
                    "status": "active",
                    "stop_reason": None,
                    "operations": tuple(operations),
                }
            )
            db.execute(
                "UPDATE workflows SET snapshot=? WHERE workflow_id=?",
                (snapshot.model_dump_json(), workflow_id),
            )
            return snapshot

    def _orphan_blocker(self, snapshot, operation):
        attempt = self.database.parent / "attempts" / snapshot.workflow_id / operation.operation_id
        record = attempt / "process.json"
        try:
            if any(p.is_symlink() for p in (record, *record.parents)):
                return "orphaned_backend_identity_unverified", str(record)
            metadata = json.loads(record.read_bytes())
            pid = metadata["pid"]
            if type(pid) is not int or pid <= 0:
                raise ValueError("invalid recorded PID")
            live = process_identity(pid)
            if live is None or live.split(":")[1] != str(metadata["start_ticks"]):
                return None
            if metadata.get("attempt_root", str(attempt)) != str(attempt):
                return "orphaned_backend_identity_unverified", str(record)
            command = (Path(f"/proc/{pid}") / "cmdline").read_bytes().split(b"\0")
            matches = any(
                arg.decode(errors="replace") == str(attempt)
                or arg.decode(errors="replace").startswith(str(attempt) + "/")
                for arg in command
            )
            if not matches:
                return "orphaned_backend_identity_unverified", str(record)
            return "orphaned_backend_still_running", str(record)
        except (OSError, ValueError, KeyError, TypeError):
            return "orphaned_backend_identity_unverified", str(record)

    def begin_operation(
        self, snapshot: WorkflowSnapshot, capability: str, *, version: str = "1.0.0"
    ) -> WorkflowSnapshot:
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT snapshot FROM workflows WHERE workflow_id=?", (snapshot.workflow_id,)
            ).fetchone()
            if row is None:
                raise KeyError(snapshot.workflow_id)
            current = WorkflowSnapshot.model_validate_json(row[0])
            self._require_owned_head(current, snapshot)
            if any(op.status == "running" for op in current.operations):
                raise ValueError("workflow already has a running operation")
            operation = OperationRecord(
                operation_id=str(uuid4()),
                capability=capability,
                version=version,
                status="running",
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            updated = current.model_copy(update={"operations": (*current.operations, operation)})
            db.execute(
                "UPDATE workflows SET snapshot=? WHERE workflow_id=?",
                (updated.model_dump_json(), snapshot.workflow_id),
            )
            return updated

    @staticmethod
    def _require_owned_head(current, supplied):
        owner = process_identity(os.getpid())
        if not owner or current.owner != owner or current.status != "active":
            raise ValueError("workflow owner must be the live current process")
        if current != supplied:
            raise ValueError("workflow owner, revision or operation head changed")

    def complete_operation(
        self,
        snapshot: WorkflowSnapshot,
        result: ToolResult | None,
        bundle: ArtifactRef | None,
        *,
        status: str,
        reason: str | None = None,
        required_resources: tuple[str, ...] = (),
        proposal: ArtifactRef | None = None,
        scene_ir: ArtifactRef | None = None,
        asset_resolution: ArtifactRef | None = None,
        resolved_assets: ArtifactRef | None = None,
        compiled_scene: ArtifactRef | None = None,
        replay_result: ArtifactRef | None = None,
        observation: ArtifactRef | None = None,
        diagnosis: ArtifactRef | None = None,
        validation: ArtifactRef | None = None,
    ) -> WorkflowSnapshot:
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT snapshot FROM workflows WHERE workflow_id=?", (snapshot.workflow_id,)
            ).fetchone()
            if row is None:
                raise KeyError(snapshot.workflow_id)
            current = WorkflowSnapshot.model_validate_json(row[0])
            self._require_owned_head(current, snapshot)
            operations = list(current.operations)
            if result is not None:
                result = ToolResult.model_validate_json(result.model_dump_json())
                if (
                    not operations
                    or operations[-1].status != "running"
                    or operations[-1].operation_id != result.operation_id
                ):
                    raise ValueError("result must complete the current running operation")
                for ref in result.outputs:
                    self.read_artifact(ref)
                operation = operations[-1]
                operations[-1] = operation.model_copy(
                    update={
                        "status": result.status,
                        "result": result,
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            elif any(op.status == "running" for op in operations):
                raise ValueError("running operation requires its own result")
            for ref in (
                bundle,
                proposal,
                scene_ir,
                asset_resolution,
                resolved_assets,
                compiled_scene,
                replay_result,
                observation,
                diagnosis,
                validation,
            ):
                if ref is not None:
                    self.read_artifact(ref)
            updated = current.model_copy(
                update={
                    "status": status,
                    "stop_reason": reason,
                    "owner": current.owner if status == "active" else None,
                    "revision": current.revision + int(result is not None),
                    "input_bundle": bundle,
                    "required_resources": required_resources,
                    "proposal": proposal or current.proposal,
                    "scene_ir": scene_ir or current.scene_ir,
                    "asset_resolution": asset_resolution or current.asset_resolution,
                    "resolved_assets": resolved_assets or current.resolved_assets,
                    "compiled_scene": compiled_scene or current.compiled_scene,
                    "replay_result": replay_result or current.replay_result,
                    "observation": observation or current.observation,
                    "diagnosis": diagnosis or current.diagnosis,
                    "validation": validation or current.validation,
                    "operations": tuple(operations),
                }
            )
            updated = WorkflowSnapshot.model_validate_json(updated.model_dump_json())
            db.execute(
                "UPDATE workflows SET snapshot=? WHERE workflow_id=?",
                (updated.model_dump_json(), snapshot.workflow_id),
            )
            return updated
