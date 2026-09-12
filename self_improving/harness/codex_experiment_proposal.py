"""Structured advisory turns of the existing managed Codex backend.

No tool execution, state update, or physical authority is inferred from model text.
The caller journals the result and applies the relevant typed proposal contract.
"""

import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path


def run_proposal(backend, *, prompt, output_model, images, output_root, timeout):
    started = time.monotonic()
    if timeout <= 0 or timeout > 600:
        raise ValueError("proposal deadline must be within 1..600 seconds")
    if hashlib.sha256(backend.executable.read_bytes()).hexdigest() != backend.executable_sha:
        raise ValueError("Codex executable changed")
    root = Path(output_root)
    if not root.is_absolute():
        raise ValueError("proposal output root must be absolute")
    root.mkdir(parents=True, exist_ok=False)
    schema = output_model.model_json_schema()
    schema_path = root / "proposal.schema.json"
    schema_path.write_text(json.dumps(schema, sort_keys=True))
    (root / "prompt.txt").write_text(prompt)
    media = []
    for path in images:
        path = Path(path)
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise ValueError("proposal image must be a regular absolute file")
        media.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (root / "inputs.json").write_text(
        json.dumps(
            {"images": media, "model": backend.model, "executable_sha256": backend.executable_sha}
        )
    )
    argv = [
        str(backend.executable),
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--model",
        backend.model,
        "--output-schema",
        str(schema_path),
        "-c",
        "features.shell_tool=false",
        "-c",
        "features.multi_agent=false",
        "-c",
        "mcp_servers={}",
        "--output-last-message",
        str(root / "proposal.json"),
    ]
    for image in media:
        argv.extend(["-i", image["path"]])
    argv.append("-")
    status, proposal, failure = "failed", None, None
    with (root / "codex.jsonl").open("wb") as stdout, (root / "codex.stderr").open("wb") as stderr:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
            cwd=root,
            start_new_session=True,
        )
        from .codex_backend import process_start_ticks

        (root / "process.json").write_text(
            json.dumps({"pid": process.pid, "start_ticks": process_start_ticks(process.pid)})
        )
        try:
            process.communicate(
                (
                    "You are the Harness internal advisory backend. Do not use tools. "
                    "Return only the requested structured proposal. " + prompt
                ).encode(),
                timeout=timeout,
            )
            code = process.returncode
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            os.killpg(process.pid, signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            code = process.returncode
            failure = "timeout" if isinstance(error, subprocess.TimeoutExpired) else "interrupted"
    raw = (root / "codex.jsonl").read_bytes()
    if code == 0 and failure is None:
        try:
            events = [json.loads(line) for line in raw.splitlines() if line.strip()]
            if not any(row.get("type") == "turn.completed" for row in events):
                raise ValueError("missing completed model turn")
            if any(
                row.get("item", {}).get("type") in {"command_execution", "mcp_tool_call"}
                for row in events
            ):
                raise ValueError("advisory attempted an unassigned tool")
            proposal = output_model.model_validate_json(
                (root / "proposal.json").read_bytes()
            ).model_dump(mode="json")
            status = "completed"
        except (ValueError, OSError) as error:
            failure = str(error)
    result = dict(
        status=status,
        proposal=proposal,
        failure=failure or (None if status == "completed" else "model_exit_failure"),
        exit_code=code,
        elapsed_seconds=time.monotonic() - started,
        external_agent_executed=True,
        authority="advisory_only",
        transcript_sha256=hashlib.sha256(raw).hexdigest(),
        output_root=str(root),
    )
    (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result
