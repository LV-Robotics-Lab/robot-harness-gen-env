"""Private per-invocation process recordings and a transparent stdio witness.

No authentication material is read or copied. Raw logs are private mode 0600 and
never printed by this module. The recorder does not certify simulator outcomes.
"""

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .codex_mcp_acceptance import (
    NORMAL_TOOLS,
    PREVIEW_TOOLS,
    REVISED_TOOLS,
    CodexMCPInvocation,
    inspect_codex_transcript,
    prepare_codex_mcp_invocation,
    prepare_limited_preview_codex_invocation,
    preview_observation_consumption_margins,
    verify_transcript_authority,
)


def record_stdio_transport(command, *, source, destination, audit, errors):
    """Record actual subprocess JSON-RPC bytes while forwarding them unchanged."""
    lock = threading.Lock()
    failures = []
    counter = 0
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors
    )

    def pump(reader, writer, direction):
        nonlocal counter
        try:
            for line in iter(reader.readline, b""):
                message = json.loads(line)
                with lock:
                    audit.write(
                        json.dumps(
                            {
                                "sequence": counter,
                                "monotonic_ns": time.monotonic_ns(),
                                "direction": direction,
                                "message": message,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                        + b"\n"
                    )
                    audit.flush()
                    counter += 1
                writer.write(line)
                writer.flush()
        except Exception as error:
            failures.append(type(error).__name__)
            process.terminate()
        finally:
            if direction == "request":
                writer.close()

    inbound = threading.Thread(target=pump, args=(source, process.stdin, "request"), daemon=True)
    outbound = threading.Thread(target=pump, args=(process.stdout, destination, "response"))
    inbound.start()
    outbound.start()
    code = process.wait()
    outbound.join()
    if failures:
        raise ValueError("stdio recording failed: " + ",".join(failures))
    return code


_TOKEN = object()


@dataclass(frozen=True)
class CodexProcessRecording:
    """Created only after a real local subprocess; not a remotely signed attestation."""

    _token: object
    output_root: Path
    elapsed_seconds: float
    exit_code: int
    started_at: str
    ended_at: str
    executable_sha256: str
    version: str
    model: str
    case: str
    transcript_sha256: str
    transport_sha256: str | None

    def __post_init__(self):
        if self._token is not _TOKEN:
            raise TypeError("process recording requires the fixed subprocess runner")


def run_codex_mcp_invocation(
    invocation: CodexMCPInvocation,
    *,
    server_python: Path,
    server_settings: Path,
    server_workdir: Path,
    objective: str,
    output_root: Path,
    approved_codex_sha256: str,
    timeout_seconds: int = 14400,
) -> CodexProcessRecording:
    """Execute once with only temporary command-line MCP overrides, never resume.

    The operator must choose the actual Codex ELF executable, not a shell wrapper
    that might silently substitute providers. Model text cannot create this record.
    This function is deliberately not invoked by fixture-only acceptance tests.
    """
    if (
        type(invocation) is not CodexMCPInvocation
        or type(timeout_seconds) is not int
        or timeout_seconds <= 0
    ):
        raise TypeError("requires exact invocation and positive integer timeout")
    executable = Path(invocation.argv[0])
    prepare = (
        prepare_limited_preview_codex_invocation
        if invocation.case.startswith("preview_")
        else prepare_codex_mcp_invocation
    )
    image_kwargs = (
        {"image_path": invocation.image_path} if invocation.case.startswith("preview_") else {}
    )
    expected = prepare(
        codex_executable=executable,
        server_python=server_python,
        server_settings=server_settings,
        server_workdir=server_workdir,
        model=invocation.model,
        case=invocation.case,
        objective=objective,
        **image_kwargs,
    )
    if expected != invocation:
        raise ValueError("invocation or frozen server settings changed")
    executable_bytes = executable.read_bytes()
    if not executable_bytes.startswith(b"\x7fELF"):
        raise ValueError("operator must select the real Codex ELF, not a wrapper")
    if hashlib.sha256(executable_bytes).hexdigest() != approved_codex_sha256:
        raise ValueError("Codex executable differs from operator-approved identity")
    version = subprocess.check_output([str(executable), "--version"], timeout=30).decode().strip()
    if not version.startswith("codex-cli "):
        raise ValueError("executable does not identify as Codex CLI")
    if not output_root.is_absolute() or ".." in output_root.parts:
        raise ValueError("recording directory must be absolute")
    output_root.mkdir(mode=0o700, parents=False, exist_ok=False)
    argv = list(invocation.argv)
    config_index = next(i for i, item in enumerate(argv) if item.startswith("mcp_servers="))
    args = [
        "-B",
        "-m",
        "self_improving.harness.codex_mcp_process",
        "--relay",
        str(server_python),
        str(server_settings),
        str(server_workdir),
        str(output_root),
        "--server-module",
        invocation.server_module,
    ]
    tools = (
        PREVIEW_TOOLS
        if invocation.case.startswith("preview_")
        else NORMAL_TOOLS
        if invocation.case == "normal"
        else REVISED_TOOLS
    )
    argv[config_index] = (
        "mcp_servers={golden={command="
        + json.dumps(str(server_python))
        + ",args="
        + json.dumps(args)
        + ",cwd="
        + json.dumps(str(server_workdir))
        + ",startup_timeout_sec=1800,tool_timeout_sec=3600,enabled_tools="
        + json.dumps(list(tools))
        + "}}"
    )
    started_at = datetime.now(UTC).isoformat()
    start = time.monotonic()
    with (
        _private_file(output_root / "codex.jsonl") as stdout,
        _private_file(output_root / "codex.stderr") as stderr,
    ):
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                cwd=output_root,
                start_new_session=True,
            )
            process.communicate(invocation.prompt.encode(), timeout=timeout_seconds)
            code = process.returncode
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = 124
    transcript_sha = hashlib.sha256((output_root / "codex.jsonl").read_bytes()).hexdigest()
    transport_path = output_root / "transport.jsonl"
    transport_sha = (
        hashlib.sha256(transport_path.read_bytes()).hexdigest() if transport_path.exists() else None
    )
    record = CodexProcessRecording(
        _TOKEN,
        output_root,
        time.monotonic() - start,
        code,
        started_at,
        datetime.now(UTC).isoformat(),
        hashlib.sha256(executable_bytes).hexdigest(),
        version,
        invocation.model,
        invocation.case,
        transcript_sha,
        transport_sha,
    )
    metadata = {
        name: getattr(record, name)
        for name in (
            "elapsed_seconds",
            "exit_code",
            "started_at",
            "ended_at",
            "executable_sha256",
            "version",
            "model",
            "transcript_sha256",
            "transport_sha256",
        )
    }
    metadata.update(
        {
            "schema_version": "harness.codex_process_recording.v1",
            "authority": "operator_trusted_local_process_recording",
            "server_settings_sha256": invocation.server_settings_sha256,
            "case": invocation.case,
            "image_sha256": invocation.image_sha256,
            "transcript_sha256": hashlib.sha256(
                (output_root / "codex.jsonl").read_bytes()
            ).hexdigest(),
        }
    )
    with _private_file(output_root / "recording.json") as output:
        output.write(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode())
    return record


def verify_codex_process_recording(recording, *, harness, request):
    """Combine a live local recording with existing durable workflow evidence.

    Local operator trust remains explicit. This does not prove model comprehension
    or provide cryptographic protection from a writer of the same host.
    """
    if type(recording) is not CodexProcessRecording or recording._token is not _TOKEN:
        raise TypeError("requires fixed-runner process recording")
    if recording.exit_code != 0 or recording.transport_sha256 is None:
        raise ValueError("Codex process did not complete a recorded MCP transport")
    raw = (recording.output_root / "codex.jsonl").read_bytes()
    wire = (recording.output_root / "transport.jsonl").read_bytes()
    if (
        hashlib.sha256(raw).hexdigest() != recording.transcript_sha256
        or hashlib.sha256(wire).hexdigest() != recording.transport_sha256
    ):
        raise ValueError("recorded transcript bytes changed")
    inspection = inspect_codex_transcript(raw, wire, case=recording.case)
    head = harness.read(request)
    if (
        not datetime.fromisoformat(recording.started_at)
        <= head.started_at
        <= datetime.fromisoformat(recording.ended_at)
    ):
        raise ValueError("historical workflow cannot satisfy fresh Codex acceptance")
    result = verify_transcript_authority(inspection, harness=harness, request=request)
    result.update(
        {
            "authority": "operator_trusted_codex_process_and_workflow_crosscheck",
            "actual_codex_execution_certified": False,
            "local_codex_process_observed": True,
            "wall_elapsed_seconds": recording.elapsed_seconds,
            "model_requested": recording.model,
            "codex_version": recording.version,
            "codex_executable_sha256": recording.executable_sha256,
        }
    )
    return result


def inspect_preview_process_recording(recording):
    """Inspect a recorded preview process without asserting service authority.

    The service's durable workflow/package check remains a separate acceptance
    gate. Neither model prose nor this transport witness grants physical authority.
    """
    if type(recording) is not CodexProcessRecording or recording._token is not _TOKEN:
        raise TypeError("requires fixed-runner process recording")
    if not recording.case.startswith("preview_"):
        raise ValueError("requires preview recording")
    if recording.exit_code != 0 or recording.transport_sha256 is None:
        raise ValueError("Codex process did not complete a recorded MCP transport")
    raw = (recording.output_root / "codex.jsonl").read_bytes()
    wire = (recording.output_root / "transport.jsonl").read_bytes()
    if (
        hashlib.sha256(raw).hexdigest() != recording.transcript_sha256
        or hashlib.sha256(wire).hexdigest() != recording.transport_sha256
    ):
        raise ValueError("recorded transcript bytes changed")
    inspection = inspect_codex_transcript(raw, wire, case=recording.case)
    result = preview_observation_consumption_margins(inspection)
    result.update(
        authority="operator_trusted_local_process_and_preview_transport_consistency_only",
        actual_codex_execution_certified=False,
        local_codex_process_observed=True,
        durable_service_crosscheck_complete=False,
        wall_elapsed_seconds=recording.elapsed_seconds,
        model_requested=recording.model,
        codex_version=recording.version,
        codex_executable_sha256=recording.executable_sha256,
        transcript_sha256=inspection.transcript_sha256,
        transport_sha256=inspection.transport_sha256,
    )
    return result


def _private_file(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--relay", nargs=4, metavar=("PYTHON", "SETTINGS", "WORKDIR", "OUTPUT"), required=True
    )
    parser.add_argument(
        "--server-module",
        default="self_improving.harness.mcp_stdio",
        choices=(
            "self_improving.harness.mcp_stdio",
            "self_improving.harness.limited_image_preview_mcp",
        ),
    )
    args = parser.parse_args()
    python, settings, workdir, output = map(Path, args.relay)
    if any(
        not path.is_absolute() or ".." in path.parts for path in (python, settings, workdir, output)
    ):
        parser.error("relay paths must be absolute")
    with (
        _private_file(output / "transport.jsonl") as audit,
        _private_file(output / "server.stderr") as errors,
    ):
        os.chdir(workdir)
        return record_stdio_transport(
            [
                str(python),
                "-B",
                "-m",
                args.server_module,
                "--settings",
                str(settings),
            ],
            source=sys.stdin.buffer,
            destination=sys.stdout.buffer,
            audit=audit,
            errors=errors,
        )


if __name__ == "__main__":
    raise SystemExit(main())
