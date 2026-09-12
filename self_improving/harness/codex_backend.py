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


class CodexTerminalDecision(BaseModel):
    """A model diagnostic, never evidence that a business operation succeeded."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    workflow_id: str
    decision: Literal["phase_complete", "unsupported"]
    reason: str = Field(min_length=1, max_length=2000)


def validate_terminal_decision(value, workflow_id):
    decision = CodexTerminalDecision.model_validate(value)
    if decision.workflow_id != str(workflow_id) or not decision.reason.strip():
        raise ValueError("terminal decision differs from assigned workflow or lacks reason")
    return decision.model_dump(mode="json")


def process_start_ticks(pid):
    """Linux process identity, including zombie detection, without reading arguments."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    except FileNotFoundError:
        return None
    return None if fields[0] == "Z" else fields[19]


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
    pending, calls, final_item = {}, [], None
    for event in events[1:-1]:
        kind, item = event.get("type"), event.get("item", {})
        if kind == "turn.started":
            continue
        if kind not in {"item.started", "item.updated", "item.completed"}:
            raise ValueError("unexpected Codex event")
        if kind == "item.completed":
            final_item = item
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
    evidence, actual = _inspect_assigned_transport(wire, assignment)
    if len(actual) != len(calls):
        raise ValueError("assistant calls differ from complete wire calls")
    for item, (method, params, result, _elapsed) in zip(calls, actual, strict=True):
        observed = item.get("result") or {}
        if method == "resources/read":
            contents = observed.get("content", [])
            if (
                item["tool"] != "read_mcp_resource"
                or item["arguments"] != {"server": "golden", "uri": params["uri"]}
                or observed.get("structured_content") is not None
                or len(contents) != 1
                or contents[0].get("type") != "text"
                or result is None
            ):
                raise ValueError("assistant resource dispatch differs from wire read")
            envelope = json.loads(contents[0]["text"])
            if envelope != {"server": "golden", "uri": params["uri"], **result}:
                raise ValueError("assistant resource delivery differs from wire result")
            continue
        if (
            item["tool"] != params["name"]
            or item["arguments"] != params.get("arguments", {})
            or result is None
            or observed.get("structured_content") != result.get("structuredContent")
            or observed.get("content") != result.get("content")
        ):
            raise ValueError("assistant/wire output drift")
    if final_item is None or final_item.get("type") != "agent_message":
        raise ValueError("missing strict terminal decision")
    terminal = validate_terminal_decision(
        json.loads(final_item.get("text", "")), assignment.snapshot.workflow_run_id
    )
    if terminal["decision"] == "unsupported" and any(
        call["tool"] not in {STATUS, "image2env_preview_asset_lookup_v1"}
        for call in evidence["calls"]
    ):
        raise ValueError("unsupported decision followed business execution")
    return {**evidence, "terminal_decision": terminal}


def inspect_assigned_transport(wire: bytes, assignment: CodexAssignment):
    """Recover complete assigned calls from the Harness-recorded JSON-RPC transport."""
    evidence, _actual = _inspect_assigned_transport(wire, assignment)
    return evidence


def _inspect_assigned_transport(wire: bytes, assignment: CodexAssignment):
    import io

    if not wire.strip():
        raise ValueError("missing assigned transport")
    try:
        frames = [json.loads(line) for line in wire.splitlines() if line.strip()]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("invalid assigned transport JSON") from error
    requests, actual, decisions, reads = {}, [], [], []
    guard = AssignedMCPInput(
        io.BytesIO(), assignment.model_copy(update={"deadline": datetime.max.replace(tzinfo=UTC)})
    )
    previous_time = None
    for expected_sequence, frame in enumerate(frames):
        if not isinstance(frame, dict) or set(frame) != {
            "sequence",
            "monotonic_ns",
            "direction",
            "message",
        }:
            raise ValueError("assigned transport frame shape differs")
        moment = frame["monotonic_ns"]
        if (
            type(frame["sequence"]) is not int
            or frame["sequence"] != expected_sequence
            or type(moment) is not int
            or moment < 0
            or (previous_time is not None and moment < previous_time)
        ):
            raise ValueError("assigned transport sequence or timing differs")
        previous_time = moment
        message = frame["message"]
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ValueError("invalid assigned JSON-RPC frame")
        direction = frame["direction"]
        if direction == "request":
            if not isinstance(message.get("params", {}), dict):
                raise ValueError("assigned request params must be an object")
            guard.source = io.BytesIO(
                json.dumps(message, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            )
            try:
                guard.readline()
            except (AttributeError, KeyError, TypeError) as error:
                raise ValueError("invalid assigned request") from error
            if "id" in message:
                identity = message["id"]
                if type(identity) not in (int, str) or identity in requests:
                    raise ValueError("duplicate or invalid wire request")
                requests[identity] = (message, moment)
        elif direction == "response":
            identity = message.get("id")
            if type(identity) not in (int, str) or identity not in requests:
                raise ValueError("wire response lacks one pending request")
            if ("result" in message) == ("error" in message) or "error" in message:
                raise ValueError("wire response does not contain one attributable result")
            request, started = requests.pop(identity)
            elapsed_ns = moment - started
            if elapsed_ns <= 0:
                raise ValueError("wire timing drift")
            method, params, result = request["method"], request.get("params", {}), message["result"]
            if method in {"tools/call", "resources/read"}:
                if not isinstance(result, dict):
                    raise ValueError("assigned call result must be an object")
                actual.append((method, params, result, elapsed_ns / 1e9))
            if method == "tools/call":
                decisions.append(
                    {
                        "tool": params["name"],
                        "arguments": params.get("arguments", {}),
                        "result": result,
                        "elapsed_seconds": elapsed_ns / 1e9,
                        "transport_started_monotonic_ns": started,
                        "transport_ended_monotonic_ns": moment,
                    }
                )
            elif method == "resources/read":
                reads.append({"uri": params["uri"], "result": result})
        else:
            raise ValueError("unexpected wire direction")
    if requests:
        raise ValueError("incomplete wire calls")
    if not decisions or decisions[0]["tool"] != STATUS:
        raise ValueError("missing initial status call")
    first = decisions[0]["result"]
    if first.get("structuredContent", {}).get("snapshot") != assignment.snapshot.model_dump(
        mode="json"
    ):
        raise ValueError("initial status differs from assigned snapshot")
    return (
        {
            "workflow_id": str(assignment.snapshot.workflow_run_id),
            "calls": decisions,
            "resource_reads": reads,
            "terminal_decision": None,
            "attribution_complete": True,
            "business_state_authority": False,
        },
        actual,
    )


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
        schema = CodexTerminalDecision.model_json_schema()
        schema["properties"]["workflow_id"]["const"] = str(assignment.snapshot.workflow_run_id)
        write("terminal-decision.schema.json", json.dumps(schema, sort_keys=True).encode())
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
            "Use status to skip already successful steps and reuse their displayed idempotency. "
            "Phase describes persisted progress, not the meaning of the user's objective. "
            "For preservation/no explicit target: lookup, compile2, replay2, observe3 once, "
            "then validate3 with no target coordinates. "
            "For explicit XY/yaw: lookup, compile2, replay2, validate3 with that target first, "
            "preserve the actual mismatch, then observe3 once and stop at the phase boundary. "
            "During diagnosis reuse the status observation if it is still fresh; "
            "only capture again after expiry. Diagnosis is a two-head sequence. "
            "At the pre-advisory current head, first call image2env_preview_status_v1, then read "
            "the current effective prompt, preserved validation failure, fresh observation, "
            "diagnostics, and initial replay evidence before calling harness_advisory_record_v2. "
            "The advisory commit advances the Golden head. "
            "After harness_advisory_record_v2 commits, call image2env_preview_status_v1 again and "
            "use its post-advisory current head. "
            "At that post-advisory head, re-read every artifact "
            "harness_prompt_revision_record_v3 will reference: the current source prompt, new "
            "advisory receipt, preserved failure receipts, and predecessor environment. "
            "Use only the new post-advisory resource-access receipts from those reads. "
            "Pre-advisory resource-access receipts are invalid for "
            "harness_prompt_revision_record_v3; never reuse them or rely on session memory. "
            "Only after those reads call harness_prompt_revision_record_v3. "
            "Revised progress is compile3, replay3, validate3 against that same explicit target. "
            "For revised compile, take the exact asset_id from the current status asset field; "
            "never guess or reconstruct an asset identifier. "
            "Only take actions allowed by this assignment; stop at a phase boundary. "
            "The Harness handles publication after your turn; do not publish or fetch a package. "
            'End with exactly one JSON object: {"workflow_id":"the assigned UUID",'
            '"decision":"phase_complete" or "unsupported","reason":"concise reason"}. '
            "Use unsupported only for an out-of-scope request before any business operation; "
            "status and explicit asset lookup are allowed before that decision. "
            "This final decision is diagnostic, not a declaration of business success. "
            "Do not invent placements: derive explicit arguments from the original objective. "
            "Do not claim business success from prose. Assignment: " + assignment.model_dump_json()
        )
        argv = [
            str(self.executable),
            "exec",
            "--json",
            "--ephemeral",
            "--output-schema",
            str(output_root / "terminal-decision.schema.json"),
            "--ignore-user-config",
            "--ignore-rules",
            "--approve-for-me",
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
            write(
                "process.json",
                json.dumps(
                    {
                        "pid": process.pid,
                        "start_ticks": process_start_ticks(process.pid),
                        "deadline": assignment.deadline.isoformat(),
                        "executable_sha256": self.executable_sha,
                        "output_root": str(output_root),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode(),
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
        if status != "completed":
            try:
                decision = inspect_assigned_transport(wire, assignment)
            except (ValueError, KeyError, TypeError):
                decision = None
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
