"""Harness-owned bounded Codex attempts in the existing Golden database.

Attempt rows are diagnostics, never workflow receipts or qualification. Only the
service can advance business state or publish. Settings/CLI assembly is separate.
"""

import hashlib
import json
import math
import os
import signal
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from .codex_backend import CodexAssignment, CodexBackendResult, process_start_ticks
from .golden_store import SQLiteGoldenRunStore
from .schemas.common import ArtifactRef
from .schemas.limited_image_preview_service import PreviewServiceResult
from .trusted_state import TrustedWorldState

STATUS = "image2env_preview_status_v1"
ACTIONS = {
    "normal": (
        STATUS,
        "image2env_preview_asset_lookup_v1",
        "image2env_compile_v2_0_0",
        "image2env_replay_v2_0_0",
        "genesis_observe_v3_0_0",
        "image2env_validate_v3_0_0",
    ),
    "diagnosis": (
        STATUS,
        "genesis_observe_v3_0_0",
        "harness_advisory_record_v2",
        "harness_prompt_revision_record_v3",
    ),
    "repair": (
        STATUS,
        "image2env_compile_v3_0_0",
        "image2env_replay_v3_0_0",
        "image2env_validate_v3_0_0",
    ),
}


class PreviewService(Protocol):
    store: object
    journal: SQLiteGoldenRunStore

    def start(self, **arguments): ...
    def status(self, *, workflow_id): ...
    def publish(self, *, workflow_id, idempotency_key, timing_samples): ...
    def package_get(self, *, workflow_id): ...


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()


def timing_samples(result):
    """Preserve measured samples; missing or overlapping measurements stay invalid."""

    def ns(value):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid measured seconds")
        return round(value * 1_000_000_000)

    backend = ns(result.elapsed_seconds)
    calls, valid = [], result.decision_evidence is not None
    for call in (result.decision_evidence or {}).get("calls", []):
        transport = ns(call["elapsed_seconds"])
        output = (call.get("result") or {}).get("structuredContent") or {}
        stage = output.get("stage")
        service = ns(output["elapsed_seconds"]) if "elapsed_seconds" in output else None
        if stage in {"compile", "replay", "observe", "validate"} and service is None:
            valid = False
        if service is not None and transport < service:
            valid = False
        calls.append(
            dict(
                tool=call["tool"],
                stage=stage,
                transport_elapsed_ns=transport,
                service_elapsed_ns=service,
            )
        )
    return dict(
        backend_elapsed_ns=backend,
        calls=calls,
        valid=valid and backend >= sum(call["transport_elapsed_ns"] for call in calls),
    )


class LimitedImagePreviewController:
    def __init__(
        self,
        service: PreviewService,
        backend,
        *,
        evidence_root: Path,
        max_attempts=3,
        call_budget=30,
        attempt_seconds=3600,
    ):
        if type(service.journal) is not SQLiteGoldenRunStore or not service.journal.path.is_file():
            raise TypeError("controller requires the existing Golden database")
        if any(
            type(value) is not int or value <= 0
            for value in (max_attempts, call_budget, attempt_seconds)
        ):
            raise ValueError("controller budgets must be positive integers")
        if not evidence_root.is_absolute():
            raise ValueError("controller evidence root must be absolute")
        self.service, self.backend = service, backend
        self.root, self.max_attempts = evidence_root, max_attempts
        self.call_budget, self.attempt_seconds = call_budget, attempt_seconds
        self.root.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(service.journal.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS codex_preview_attempts "
                "(workflow TEXT NOT NULL, number INTEGER NOT NULL, payload BLOB NOT NULL, "
                "digest TEXT NOT NULL, PRIMARY KEY(workflow,number))"
            )

    def attempts(self, workflow_id):
        with sqlite3.connect(self.service.journal.path) as connection:
            rows = connection.execute(
                "SELECT number,payload,digest FROM codex_preview_attempts "
                "WHERE workflow=? ORDER BY number",
                (str(workflow_id),),
            ).fetchall()
        records = []
        for number, raw, digest in rows:
            value = json.loads(raw)
            if (
                raw != _canonical(value)
                or hashlib.sha256(raw).hexdigest() != digest
                or value["workflow_id"] != str(workflow_id)
                or value["number"] != number
            ):
                raise ValueError("attempt diagnostic row integrity differs")
            records.append(value)
        return records

    def _write(self, record, *, previous=None):
        raw = _canonical(record)
        with sqlite3.connect(self.service.journal.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if previous is None:
                connection.execute(
                    "INSERT INTO codex_preview_attempts VALUES (?,?,?,?)",
                    (record["workflow_id"], record["number"], raw, hashlib.sha256(raw).hexdigest()),
                )
            else:
                count = connection.execute(
                    "UPDATE codex_preview_attempts SET payload=?,digest=? "
                    "WHERE workflow=? AND number=? AND digest=?",
                    (
                        raw,
                        hashlib.sha256(raw).hexdigest(),
                        record["workflow_id"],
                        record["number"],
                        hashlib.sha256(_canonical(previous)).hexdigest(),
                    ),
                ).rowcount
                if count != 1:
                    raise ValueError("concurrent attempt transition")

    def _put(self, path):
        return self.service.store.put_file(
            path, name=path.name, media_type="application/octet-stream", schema_version=None
        )

    def _status(self, workflow_id):
        status = PreviewServiceResult.model_validate_json(
            json.dumps(self.service.status(workflow_id=str(workflow_id)))
        )
        if status.snapshot is None or str(status.workflow_id) != str(workflow_id):
            raise ValueError("service returned a foreign workflow status")
        return status

    def start(self, **raw_input):
        started = time.monotonic_ns()
        value = PreviewServiceResult.model_validate_json(
            json.dumps(self.service.start(**raw_input))
        )
        return self.resume(value.workflow_id, input_elapsed_ns=time.monotonic_ns() - started)

    def _assignment(self, status):
        snapshot = status.snapshot
        state = TrustedWorldState.model_validate_json(
            self.service.store.resolve(snapshot.state_ref).path.read_bytes()
        )
        facts = [fact for fact in state.facts if fact.key == "request.preview_input"]
        if len(facts) != 1:
            raise ValueError("workflow lacks its original input binding")
        document = json.loads(
            self.service.store.resolve(ArtifactRef.model_validate(facts[0].value)).path.read_bytes()
        )
        if document["workflow_id"] != str(snapshot.workflow_run_id):
            raise ValueError("foreign original workflow input")
        source = ArtifactRef.model_validate(document["source_media"]["source"])
        prompt = ArtifactRef.model_validate(document["effective_prompt"])
        objective = self.service.store.resolve(prompt).path.read_bytes().decode("utf-8")
        image = self.service.store.resolve(source).path
        phase = "diagnosis" if status.failure_receipts else "normal"
        cursor, seen = snapshot.receipt_head, set()
        while cursor.schema_version != "harness.workflow_start_receipt.v1":
            if cursor.sha256 in seen:
                raise ValueError("workflow history cycle")
            seen.add(cursor.sha256)
            body = json.loads(self.service.store.resolve(cursor).path.read_bytes())
            if body.get("workflow_run_id") != str(snapshot.workflow_run_id):
                raise ValueError("foreign phase history")
            if cursor.schema_version == "harness.prompt_revision.v3":
                phase = "repair"
                break
            cursor = ArtifactRef.model_validate(body["predecessor_receipt"])
        return CodexAssignment(
            snapshot=snapshot,
            image_path=image,
            objective=objective,
            phase=phase,
            allowed_actions=ACTIONS[phase],
            call_budget=self.call_budget,
            deadline=datetime.now(UTC) + timedelta(seconds=self.attempt_seconds),
        )

    def _recover(self, row):
        output = Path(row["output_root"])
        marker = output / "process.json"
        if not marker.exists():
            if process_start_ticks(row["owner_pid"]) == row["owner_start_ticks"]:
                return "running"
            # Crash before publication of process identity: never guess that no
            # detached Codex exists. Scan only exact output cwd, never arguments.
            matches = []
            for path in Path("/proc").glob("[0-9]*/cwd"):
                try:
                    if path.resolve(strict=True) == output:
                        matches.append(path.parent.name)
                except (FileNotFoundError, PermissionError):
                    pass
            if (
                matches
                or (datetime.now(UTC) - datetime.fromisoformat(row["started_at"])).total_seconds()
                < 2
            ):
                return "recovering"
        else:
            process = json.loads(marker.read_bytes())
            if (
                process["output_root"] != str(output)
                or datetime.fromisoformat(process["deadline"])
                != datetime.fromisoformat(row["assignment"]["deadline"])
                or process["executable_sha256"] != row["expected_executable_sha256"]
            ):
                raise ValueError("backend process identity differs from attempt")
            if (
                process["start_ticks"] is not None
                and process_start_ticks(process["pid"]) == process["start_ticks"]
            ):
                if datetime.now(UTC) < datetime.fromisoformat(process["deadline"]):
                    return "running"
                if os.getpgid(process["pid"]) != process["pid"]:
                    raise ValueError("assigned process group differs")
                os.killpg(process["pid"], signal.SIGKILL)
                until = time.monotonic() + 1
                while process_start_ticks(process["pid"]) == process["start_ticks"]:
                    if time.monotonic() >= until:
                        return "recovering"
                    time.sleep(0.01)
                terminal = {**row, "status": "terminal", "backend_status": "timeout"}
                self._write(terminal, previous=row)
                return None
        self._write({**row, "status": "terminal", "backend_status": "interrupted"}, previous=row)
        return None

    def _published(self, status):
        package = PreviewServiceResult.model_validate_json(
            json.dumps(self.service.package_get(workflow_id=str(status.workflow_id)))
        )
        if (
            package.status != "published"
            or package.package is None
            or package.package != status.package
        ):
            raise ValueError("published status and readable package differ")
        self.service.store.resolve(package.package)
        return dict(
            status="published",
            workflow_id=str(status.workflow_id),
            package=package.package.model_dump(mode="json"),
        )

    def resume(self, workflow_id, *, input_elapsed_ns=None):
        status = self._status(workflow_id)
        if status.status == "published":
            return self._published(status)
        rows = self.attempts(workflow_id)
        published = self._try_publish(status, rows)
        if published is not None:
            return published
        if rows and rows[-1]["status"] == "started":
            waiting = self._recover(rows[-1])
            if waiting:
                return dict(status=waiting, workflow_id=str(workflow_id))
            rows = self.attempts(workflow_id)
        if len(rows) >= self.max_attempts:
            return dict(status="attempt_limit", workflow_id=str(workflow_id))
        assignment = self._assignment(status)
        number = len(rows) + 1
        output = self.root / f"{workflow_id}-{number}"
        row = dict(
            workflow_id=str(workflow_id),
            number=number,
            status="started",
            started_at=datetime.now(UTC).isoformat(),
            assignment=assignment.model_dump(mode="json"),
            snapshot_sha256=hashlib.sha256(
                _canonical(assignment.snapshot.model_dump(mode="json"))
            ).hexdigest(),
            output_root=str(output),
            input_elapsed_ns=input_elapsed_ns,
            owner_pid=os.getpid(),
            owner_start_ticks=process_start_ticks(os.getpid()),
            expected_executable_sha256=getattr(self.backend, "executable_sha", None),
        )
        self._write(row)
        backend_start = time.monotonic()
        try:
            result = self.backend.run(assignment, output_root=output)
        except Exception as error:
            result = CodexBackendResult(
                "launch_failure",
                output,
                time.monotonic() - backend_start,
                None,
                None,
                None,
                None,
                str(error),
            )
        samples = timing_samples(result)
        samples["input_elapsed_ns"] = input_elapsed_ns
        evidence = [
            self._put(path).model_dump(mode="json")
            for path in sorted(output.glob("*"))
            if path.is_file()
        ]
        terminal = {
            **row,
            "status": "terminal",
            "backend_status": result.status,
            "elapsed_seconds": result.elapsed_seconds,
            "failure": result.failure,
            "call_count": len(samples["calls"]),
            "timing_samples": samples,
            "evidence": evidence,
        }
        self._write(terminal, previous=row)
        current = self._status(workflow_id)
        published = self._try_publish(current, self.attempts(workflow_id))
        if published is not None:
            return published
        return dict(
            status="waiting" if result.status == "completed" else result.status,
            workflow_id=str(workflow_id),
            attempt=number,
            timing_valid=samples["valid"],
        )

    def _try_publish(self, current, rows):
        operation = current.operation
        if (
            rows
            and rows[0].get("input_elapsed_ns") is not None
            and all(row.get("timing_samples", {}).get("valid") for row in rows)
            and operation is not None
            and operation.skill_ref == "image2env.validate@3.0.0"
            and operation.status.value == "succeeded"
            and current.snapshot.revision == operation.committed_revision
        ):
            published = PreviewServiceResult.model_validate_json(
                json.dumps(
                    self.service.publish(
                        workflow_id=str(current.workflow_id),
                        idempotency_key=f"controller-publish-{current.revision}",
                        timing_samples=[record.get("timing_samples") for record in rows],
                    )
                )
            )
            if published.status == "published":
                return self._published(published)
        return None
