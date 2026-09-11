"""Harness-owned Codex process backend; business authority stays in the service.

An assignment resumes an existing workflow, never a Codex session. The relay
rejects foreign workflows and unassigned actions before forwarding to MCP.
Evidence here proves local process/transport consistency, not physical success.
"""

import argparse
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .codex_mcp_acceptance import PREVIEW_TOOLS
from .codex_mcp_process import record_stdio_transport
from .schemas.workflow import RunSnapshot

STATUS = "image2env_preview_status_v1"
START = "image2env_preview_start_v1"


class CodexAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    snapshot: RunSnapshot
    image_path: Path
    objective: str = Field(min_length=1)
    phase: Literal["normal", "repair", "diagnosis"]
    allowed_actions: tuple[str, ...]
    call_budget: int = Field(strict=True, ge=1, le=100)
    deadline: datetime

    @model_validator(mode="after")
    def bounded_assignment(self):
        if (
            STATUS not in self.allowed_actions
            or START in self.allowed_actions
            or len(set(self.allowed_actions)) != len(self.allowed_actions)
            or not set(self.allowed_actions) <= set(PREVIEW_TOOLS)
        ):
            raise ValueError("assignment requires exact actions including status, never start")
        if not self.image_path.is_absolute() or ".." in self.image_path.parts:
            raise ValueError("assignment image must be an absolute path")
        if self.deadline.tzinfo is None or self.deadline.utcoffset().total_seconds() != 0:
            raise ValueError("assignment deadline must be UTC")
        return self


class AssignedMCPInput:
    """Transparent read boundary that applies the assignment before dispatch."""

    def __init__(self, source, assignment: CodexAssignment):
        self.source, self.assignment, self.calls = source, assignment, 0

    def readline(self):
        line = self.source.readline()
        if not line:
            return line
        if datetime.now(UTC) >= self.assignment.deadline:
            raise ValueError("assignment deadline exceeded")
        message = json.loads(line)
        method = message.get("method")
        if message.get("jsonrpc") != "2.0":
            raise ValueError("invalid assigned JSON-RPC")
        params = message.get("params", {})
        workflow = str(self.assignment.snapshot.workflow_run_id)
        if method == "tools/call":
            name, arguments = params.get("name"), params.get("arguments", {})
            if name not in self.assignment.allowed_actions or (self.calls == 0 and name != STATUS):
                raise ValueError("unassigned action or missing initial status")
            identities = [
                arguments[key] for key in ("workflow_id", "workflow_run_id") if key in arguments
            ]
            if not identities or any(value != workflow for value in identities):
                raise ValueError("foreign workflow action")
            self.calls += 1
            if self.calls > self.assignment.call_budget:
                raise ValueError("assignment call budget exceeded")
        elif method == "resources/read":
            uri = params.get("uri", "")
            prefix = f"harness://workflows/{workflow}"
            if uri != prefix and not uri.startswith(prefix + "/"):
                raise ValueError("foreign workflow resource")
        elif method not in {
            "initialize",
            "ping",
            "tools/list",
            "resources/list",
            "resources/templates/list",
            "notifications/initialized",
        }:
            raise ValueError("unsupported assigned protocol method")
        return line


def inspect_assigned_transcript(transcript: bytes, wire: bytes, assignment: CodexAssignment):
    """Match assistant calls to recorded wire, excluding model prose/session IDs."""
    events = [json.loads(line) for line in transcript.splitlines() if line.strip()]
    if (
        not events
        or events[0].get("type") != "thread.started"
        or events[-1].get("type") != "turn.completed"
    ):
        raise ValueError("incomplete Codex turn")
    pending, calls = {}, []
    for event in events[1:-1]:
        kind, item = event.get("type"), event.get("item", {})
        if kind == "turn.started":
            continue
        if kind not in {"item.started", "item.updated", "item.completed"}:
            raise ValueError("unexpected Codex event")
        if item.get("type") in {"agent_message", "reasoning", "todo_list"}:
            continue
        if item.get("type") != "mcp_tool_call" or item.get("server") != "golden":
            raise ValueError("only assigned MCP may drive workflow")
        identity = item.get("id")
        key = (item.get("tool"), item.get("arguments"))
        if kind == "item.started":
            if identity in pending or any(row.get("id") == identity for row in calls):
                raise ValueError("duplicate assistant call")
            pending[identity] = key
        elif pending.get(identity) != key:
            raise ValueError("assistant call drift")
        elif kind == "item.completed":
            if item.get("status") not in {"completed", "failed"}:
                raise ValueError("unfinished assistant call")
            calls.append(item)
            del pending[identity]
    if pending or not calls:
        raise ValueError("missing completed assigned calls")
    frames = [json.loads(line) for line in wire.splitlines() if line.strip()]
    requests, actual, reads = {}, [], []
    # Replay the same guard with the original deadline relaxed only for historical
    # inspection. Actual dispatch already applied the real deadline.
    import io

    guard = AssignedMCPInput(
        io.BytesIO(), assignment.model_copy(update={"deadline": datetime.max.replace(tzinfo=UTC)})
    )
    for frame in frames:
        message = frame["message"]
        if frame["direction"] == "request":
            guard.source = io.BytesIO(json.dumps(message).encode() + b"\n")
            guard.readline()
            if "id" in message:
                if message["id"] in requests:
                    raise ValueError("duplicate wire request")
                requests[message["id"]] = (message, frame["monotonic_ns"])
        elif frame["direction"] == "response":
            request, started = requests.pop(message.get("id"))
            elapsed = (frame["monotonic_ns"] - started) / 1e9
            if elapsed < 0:
                raise ValueError("wire timing drift")
            if request["method"] == "tools/call":
                actual.append((request["params"], message.get("result"), elapsed))
            elif request["method"] == "resources/read":
                reads.append({"uri": request["params"]["uri"], "result": message.get("result")})
        else:
            raise ValueError("unexpected wire direction")
    if requests or len(actual) != len(calls):
        raise ValueError("incomplete wire calls")
    first = actual[0][1]
    if (first or {}).get("structuredContent", {}).get("snapshot") != assignment.snapshot.model_dump(
        mode="json"
    ):
        raise ValueError("initial status differs from assigned snapshot")
    decisions = []
    for item, (params, result, elapsed) in zip(calls, actual, strict=True):
        observed = item.get("result") or {}
        if (
            item["tool"] != params["name"]
            or item["arguments"] != params.get("arguments", {})
            or result is None
            or observed.get("structured_content") != result.get("structuredContent")
            or observed.get("content") != result.get("content")
        ):
            raise ValueError("assistant/wire output drift")
        decisions.append(
            {
                "tool": item["tool"],
                "arguments": params["arguments"],
                "result": result,
                "elapsed_seconds": elapsed,
            }
        )
    return {
        "workflow_id": str(assignment.snapshot.workflow_run_id),
        "calls": decisions,
        "resource_reads": reads,
        "business_state_authority": False,
    }


@dataclass(frozen=True)
class CodexBackendResult:
    status: str
    evidence_root: Path
    elapsed_seconds: float
    exit_code: int | None
    transcript_sha256: str | None
    transport_sha256: str | None
    decision_evidence: dict | None
    failure: str | None


class CodexBackend:
    """Internal model backend; the caller owns assignment and durable recovery."""

    def __init__(
        self,
        *,
        executable: Path,
        approved_executable_sha256: str,
        server_python: Path,
        server_settings: Path,
        server_workdir: Path,
        model: str,
    ):
        for path in (executable, server_python, server_settings, server_workdir):
            if not path.is_absolute() or ".." in path.parts:
                raise ValueError("backend configuration paths must be absolute")
        raw = executable.read_bytes()
        if (
            not raw.startswith(b"\x7fELF")
            or hashlib.sha256(raw).hexdigest() != approved_executable_sha256
        ):
            raise ValueError("backend requires operator-approved Codex ELF")
        self.executable, self.executable_sha = executable, approved_executable_sha256
        self.python, self.settings, self.workdir, self.model = (
            server_python,
            server_settings,
            server_workdir,
            model,
        )

    def run(self, assignment: CodexAssignment, *, output_root: Path) -> CodexBackendResult:
        started = time.monotonic()
        remaining = (assignment.deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return CodexBackendResult(
                "timeout", output_root, 0.0, None, None, None, None, "assignment expired"
            )
        if hashlib.sha256(self.executable.read_bytes()).hexdigest() != self.executable_sha:
            raise ValueError("Codex executable changed")
        if not output_root.is_absolute() or ".." in output_root.parts:
            raise ValueError("evidence directory must be absolute")
        with Image.open(assignment.image_path) as image:
            image.verify()
        output_root.mkdir(mode=0o700, parents=False, exist_ok=False)

        def write(name, data):
            with os.fdopen(
                os.open(output_root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
            ) as stream:
                stream.write(data)

        write("assignment.json", assignment.model_dump_json().encode())
        write(
            "inputs.json",
            json.dumps(
                {
                    "image_sha256": hashlib.sha256(assignment.image_path.read_bytes()).hexdigest(),
                    "settings_sha256": hashlib.sha256(self.settings.read_bytes()).hexdigest(),
                }
            ).encode(),
        )
        relay = [
            "-B",
            "-m",
            __name__,
            "--relay",
            str(output_root / "assignment.json"),
            str(self.python),
            str(self.settings),
            str(self.workdir),
            str(output_root),
        ]
        override = (
            "mcp_servers={golden={command="
            + json.dumps(str(self.python))
            + ",args="
            + json.dumps(relay)
            + ",cwd="
            + json.dumps(str(self.workdir))
            + ",startup_timeout_sec=1800,tool_timeout_sec="
            + str(max(1, math.ceil(remaining)))
            + ",enabled_tools="
            + json.dumps(list(assignment.allowed_actions))
            + "}}"
        )
        prompt = (
            "You are the Harness internal Codex backend. "
            "Continue only the assigned existing workflow. "
            "First call image2env_preview_status_v1 and compare its snapshot to this assignment. "
            "If the snapshot differs, stop and return the mismatch. Never call start. "
            "Use only allowed exact MCP actions and workflow resources; "
            "no shell, Python, file edits, other clients or session resume. "
            "Read evidence before diagnosis/revision; respect TTL. "
            "Do not invent placements: derive explicit arguments from the original objective. "
            "Do not claim business success from prose. Assignment: " + assignment.model_dump_json()
        )
        argv = [
            str(self.executable),
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--model",
            self.model,
            "-c",
            "features.shell_tool=false",
            "-c",
            "features.multi_agent=false",
            "-c",
            override,
            "-i",
            str(assignment.image_path),
            "-",
        ]
        for name in ("codex.jsonl", "codex.stderr"):
            write(name, b"")
        with (
            (output_root / "codex.jsonl").open("wb") as out,
            (output_root / "codex.stderr").open("wb") as err,
        ):
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=err,
                cwd=output_root,
                start_new_session=True,
            )
            try:
                process.communicate(prompt.encode(), timeout=remaining)
                code = process.returncode
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                code = 124
        raw = (output_root / "codex.jsonl").read_bytes()
        wire = (
            (output_root / "transport.jsonl").read_bytes()
            if (output_root / "transport.jsonl").exists()
            else b""
        )
        decision, failure = None, None
        status = "timeout" if code == 124 else "exit_failure" if code else "completed"
        if status == "completed":
            try:
                decision = inspect_assigned_transcript(raw, wire, assignment)
            except (ValueError, KeyError, TypeError) as error:
                status, failure = "invalid_transcript", str(error)
        else:
            failure = status
        return CodexBackendResult(
            status,
            output_root,
            time.monotonic() - started,
            code,
            hashlib.sha256(raw).hexdigest(),
            hashlib.sha256(wire).hexdigest(),
            decision,
            failure,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--relay", nargs=5, required=True)
    args = parser.parse_args()
    assignment_path, python, settings, workdir, output = map(Path, args.relay)
    assignment = CodexAssignment.model_validate_json(assignment_path.read_bytes())
    os.chdir(workdir)
    with (
        (output / "transport.jsonl").open("xb") as audit,
        (output / "server.stderr").open("xb") as errors,
    ):
        os.chmod(output / "transport.jsonl", 0o600)
        os.chmod(output / "server.stderr", 0o600)
        return record_stdio_transport(
            [
                str(python),
                "-B",
                "-m",
                "self_improving.harness.limited_image_preview_mcp",
                "--settings",
                str(settings),
            ],
            source=AssignedMCPInput(sys.stdin.buffer, assignment),
            destination=sys.stdout.buffer,
            audit=audit,
            errors=errors,
        )


if __name__ == "__main__":
    raise SystemExit(main())
