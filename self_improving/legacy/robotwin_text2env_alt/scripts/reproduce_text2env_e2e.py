#!/usr/bin/env python3
"""End-to-end reproduction runner for the Text2Env -> RoboTwin2 demo.

The default path is intentionally light: it runs the natural-language agent
flow, validates the resulting Text2Env JSON, and generates a RoboTwin task
scaffold. Deployment, simulator smoke collection, and policy hook checks are
opt-in because they require a configured RoboTwin installation and GPU runtime.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def display_cmd(cmd: list[str] | str) -> str:
    if isinstance(cmd, str):
        return cmd
    return " ".join(shlex.quote(part) for part in cmd)


def run_step(
    name: str,
    cmd: list[str] | str,
    *,
    cwd: Path = ROOT,
    log_path: Path,
    timeout_s: int | None = None,
    shell: bool = False,
) -> dict[str, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "step": name,
        "cwd": str(cwd),
        "command": display_cmd(cmd),
        "timeout_s": timeout_s,
    }
    with log_path.open("w", encoding="utf-8") as log:
        log.write(json.dumps(header, ensure_ascii=False) + "\n\n")
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            shell=shell,
        )
        log.write(proc.stdout)
    result = {
        **header,
        "log": str(log_path),
        "returncode": proc.returncode,
        "status": "pass" if proc.returncode == 0 else "fail",
    }
    if proc.returncode != 0:
        raise RuntimeError(f"{name} failed with exit code {proc.returncode}; see {log_path}")
    return result


def deploy_generated_task(generated_root: Path, robotwin_root: Path, task_name: str) -> dict[str, Any]:
    src_task = generated_root / "envs" / f"{task_name}.py"
    src_instruction = generated_root / "description" / "task_instruction" / f"{task_name}.json"
    dst_task = robotwin_root / "envs" / f"{task_name}.py"
    dst_instruction = robotwin_root / "description" / "task_instruction" / f"{task_name}.json"

    missing = [str(path) for path in (src_task, src_instruction) if not path.exists()]
    if missing:
        raise FileNotFoundError(f"generated files missing: {missing}")
    if not (robotwin_root / "envs").is_dir() or not (robotwin_root / "description" / "task_instruction").is_dir():
        raise FileNotFoundError(f"RoboTwin root does not look valid: {robotwin_root}")

    dst_task.parent.mkdir(parents=True, exist_ok=True)
    dst_instruction.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_task, dst_task)
    shutil.copy2(src_instruction, dst_instruction)
    return {
        "status": "pass",
        "source": [str(src_task), str(src_instruction)],
        "destination": [str(dst_task), str(dst_instruction)],
    }


def expected_smoke_outputs(robotwin_root: Path, task_name: str, task_config: str) -> list[Path]:
    base = robotwin_root / "data" / task_name / task_config
    return [
        base / "data" / "episode0.hdf5",
        base / "video" / "episode0.mp4",
        base / "instructions" / "episode0.json",
        base / "scene_info.json",
        base / "seed.txt",
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Text2Env -> RoboTwin2 end-to-end reproduction")
    parser.add_argument("--instruction", required=True, help="Natural-language tabletop task")
    parser.add_argument("--run-dir", required=True, help="Run directory for prompts, logs, generated files, and summary")
    parser.add_argument("--backend", choices=["openai-compatible", "mock"], default="openai-compatible")
    parser.add_argument("--api-base", default=os.environ.get("TEXT2ENV_LLM_API_BASE", "http://localhost:8000/v1"))
    parser.add_argument("--api-key", default=os.environ.get("TEXT2ENV_LLM_API_KEY", ""))
    parser.add_argument("--model", default=os.environ.get("TEXT2ENV_LLM_MODEL", "Qwen/Qwen2.5-14B-Instruct"))
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--timeout-s", type=int, default=180, help="LLM request timeout")
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--known-assets", help="Optional text/JSON file listing known RoboTwin assets")
    parser.add_argument("--reference-examples", help="Optional file containing reference Text2Env examples")
    parser.add_argument("--robotwin-root", default="", help="RoboTwin root, for deploy/smoke and optional asset checks")
    parser.add_argument("--generated-root", help="Where to write generated RoboTwin task files")
    parser.add_argument("--final-json", help="Where to write final Text2Env JSON")
    parser.add_argument("--deploy", action="store_true", help="Copy generated task files into --robotwin-root")
    parser.add_argument("--run-smoke", action="store_true", help="Run RoboTwin collect_data.sh after deployment")
    parser.add_argument("--task-config", help="RoboTwin task config, defaults to generated manifest preferred_task_config")
    parser.add_argument("--gpu-id", default="0")
    parser.add_argument("--conda-sh", default="~/miniconda3/etc/profile.d/conda.sh")
    parser.add_argument("--conda-env", default="RoboTwin")
    parser.add_argument("--smoke-timeout-s", type=int, default=900)
    parser.add_argument("--run-policy-hook", action="store_true", help="Run scripts/run_policy_hook.sh after smoke")
    parser.add_argument("--policy-episodes", type=int, default=2)
    parser.add_argument("--policy-timeout-s", type=int, default=1800)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_dir = Path(args.run_dir).expanduser().resolve()
    logs_dir = run_dir / "logs"
    final_json = Path(args.final_json).expanduser().resolve() if args.final_json else run_dir / "final_text2env.json"
    generated_root = (
        Path(args.generated_root).expanduser().resolve() if args.generated_root else run_dir / "generated_robotwin_task"
    )
    robotwin_root = Path(args.robotwin_root).expanduser().resolve() if args.robotwin_root else None

    summary: dict[str, Any] = {
        "instruction": args.instruction,
        "run_dir": str(run_dir),
        "final_text2env": str(final_json),
        "generated_root": str(generated_root),
        "steps": [],
        "status": "running",
    }

    try:
        agent_cmd = [
            sys.executable,
            str(SCRIPTS / "run_text2env_agents.py"),
            "--backend",
            args.backend,
            "--api-base",
            args.api_base,
            "--model",
            args.model,
            "--temperature",
            str(args.temperature),
            "--max-tokens",
            str(args.max_tokens),
            "--timeout-s",
            str(args.timeout_s),
            "--retries",
            str(args.retries),
            "--instruction",
            args.instruction,
            "--run-dir",
            str(run_dir / "agent"),
            "--out",
            str(final_json),
        ]
        if args.api_key:
            os.environ["TEXT2ENV_LLM_API_KEY"] = args.api_key
        if args.known_assets:
            agent_cmd.extend(["--known-assets", args.known_assets])
        if args.reference_examples:
            agent_cmd.extend(["--reference-examples", args.reference_examples])
        if robotwin_root:
            agent_cmd.extend(["--robotwin-root", str(robotwin_root)])
        summary["steps"].append(run_step("agent_flow", agent_cmd, log_path=logs_dir / "01_agent_flow.log"))

        check_cmd = [sys.executable, str(SCRIPTS / "generate_text2env.py"), "check", str(final_json)]
        if robotwin_root:
            check_cmd.extend(["--robotwin-root", str(robotwin_root)])
        summary["steps"].append(run_step("schema_check", check_cmd, log_path=logs_dir / "02_schema_check.log"))

        data = load_json(final_json)
        task_name = data["task_name"]
        summary["task_name"] = task_name

        scaffold_cmd = [
            sys.executable,
            str(SCRIPTS / "generate_robotwin_task.py"),
            "generate",
            str(final_json),
            "--out-root",
            str(generated_root),
        ]
        if robotwin_root:
            scaffold_cmd.extend(["--robotwin-root", str(robotwin_root)])
        summary["steps"].append(run_step("generate_robotwin_task", scaffold_cmd, log_path=logs_dir / "03_generate_task.log"))

        manifest = load_json(generated_root / "manifest.json")
        task_config = args.task_config or manifest.get("preferred_task_config", "demo_smoke")
        summary["task_config"] = task_config

        if args.deploy:
            if not robotwin_root:
                raise ValueError("--deploy requires --robotwin-root")
            summary["steps"].append(
                {
                    "step": "deploy",
                    **deploy_generated_task(generated_root, robotwin_root, task_name),
                }
            )

        if args.run_smoke:
            if not robotwin_root:
                raise ValueError("--run-smoke requires --robotwin-root")
            conda_sh = Path(args.conda_sh).expanduser()
            smoke_cmd = (
                f"source {shlex.quote(str(conda_sh))} && "
                f"conda activate {shlex.quote(args.conda_env)} && "
                f"timeout {int(args.smoke_timeout_s)} bash collect_data.sh "
                f"{shlex.quote(task_name)} {shlex.quote(task_config)} {shlex.quote(args.gpu_id)}"
            )
            summary["steps"].append(
                run_step(
                    "simulator_smoke",
                    ["bash", "-lc", smoke_cmd],
                    cwd=robotwin_root,
                    log_path=logs_dir / "04_simulator_smoke.log",
                    timeout_s=args.smoke_timeout_s + 60,
                )
            )
            outputs = expected_smoke_outputs(robotwin_root, task_name, task_config)
            summary["smoke_outputs"] = [{"path": str(path), "exists": path.exists()} for path in outputs]
            missing = [str(path) for path in outputs if not path.exists()]
            if missing:
                raise FileNotFoundError(f"smoke completed but expected outputs are missing: {missing}")

        if args.run_policy_hook:
            if not robotwin_root:
                raise ValueError("--run-policy-hook requires --robotwin-root")
            policy_cmd = [
                "bash",
                str(SCRIPTS / "run_policy_hook.sh"),
                str(robotwin_root),
                task_name,
                "demo_policy_hook",
                args.gpu_id,
                str(args.policy_episodes),
            ]
            summary["steps"].append(
                run_step(
                    "policy_hook",
                    policy_cmd,
                    cwd=ROOT,
                    log_path=logs_dir / "05_policy_hook.log",
                    timeout_s=args.policy_timeout_s,
                )
            )

        summary["status"] = "pass"
        write_json(run_dir / "e2e_summary.json", summary)
        print(f"PASS {run_dir / 'e2e_summary.json'}")
        return 0
    except Exception as exc:
        summary["status"] = "fail"
        summary["error"] = str(exc)
        write_json(run_dir / "e2e_summary.json", summary)
        print(f"FAIL {run_dir / 'e2e_summary.json'}", file=sys.stderr)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
