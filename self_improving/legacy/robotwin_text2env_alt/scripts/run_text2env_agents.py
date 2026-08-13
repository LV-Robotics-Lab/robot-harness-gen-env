#!/usr/bin/env python3
"""Run the SceneSmith-lite Designer/Critic/Orchestrator loop with an LLM.

The default backend is any OpenAI-compatible chat-completions endpoint, such as
vLLM, Ollama, LM Studio, or a hosted compatible gateway. No third-party Python
package is required.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / "prompts"
SCHEMA_DOC = ROOT / "schemas" / "text2env_schema_v0.md"
API_NOTES = ROOT / "robotwin2_task_api_notes.md"
EXAMPLES_DIR = ROOT / "examples" / "tabletop_tasks"
MOCK_EXAMPLES = (
    "move_object_between_zones.json",
    "move_red_block_to_blue_zone.json",
    "put_cup_in_drawer.json",
)

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def render_template(template: str, values: dict[str, str]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace("{{" + key + "}}", value)
    return result


def extract_json(text: str) -> Any:
    """Parse a JSON object even if the model wrapped it in a markdown fence."""
    text = text.strip()
    match = JSON_FENCE_RE.search(text)
    if match:
        text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start_candidates = [idx for idx in (text.find("{"), text.find("[")) if idx != -1]
        if not start_candidates:
            raise
        start = min(start_candidates)
        end = max(text.rfind("}"), text.rfind("]"))
        if end <= start:
            raise
        return json.loads(text[start : end + 1])


def compact_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def default_reference_examples() -> str:
    refs = []
    for name in MOCK_EXAMPLES:
        path = EXAMPLES_DIR / name
        if path.exists():
            refs.append(f"## {name}\n{read_text(path)}")
    return "\n\n".join(refs)


def select_mock_example(instruction: str) -> str:
    """Choose the closest canned Text2Env example for plumbing tests."""
    text = instruction.lower()
    keyword_routes = (
        (("red", "block", "blue", "zone"), "move_red_block_to_blue_zone.json"),
        (("green", "block", "left", "right", "zone"), "move_object_between_zones.json"),
        (("cup", "drawer"), "put_cup_in_drawer.json"),
    )
    for keywords, example_name in keyword_routes:
        if all(keyword in text for keyword in keywords):
            return example_name
    return "move_object_between_zones.json"


def request_chat(
    *,
    api_base: str,
    api_key: str,
    model: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    timeout_s: int,
    retries: int,
) -> str:
    url = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"chat completion failed after {retries + 1} attempts: {last_error}")


def mock_response(stage: str, instruction: str, draft: Any | None = None) -> Any:
    """A plumbing-only backend for testing the script without an LLM server."""
    if stage == "designer":
        data = load_json(EXAMPLES_DIR / select_mock_example(instruction))
        data["language_instruction"] = instruction
        return data
    if stage == "critic":
        return {
            "verdict": "accept",
            "summary": "Mock critic accepted the template-based Text2Env draft.",
            "issues": [],
            "patch_plan": [],
            "ready_for_orchestrator": True,
        }
    if stage == "orchestrator":
        return {
            "decision": "accept",
            "reason": "Mock orchestrator accepted the draft.",
            "final_text2env": draft,
            "remaining_blockers": [],
            "next_step": "run scripts/generate_text2env.py check <final_json>",
        }
    raise ValueError(stage)


def call_stage(args: argparse.Namespace, prompt: str) -> Any:
    raw = request_chat(
        api_base=args.api_base,
        api_key=args.api_key or os.environ.get("OPENAI_API_KEY", ""),
        model=args.model,
        prompt=prompt,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout_s=args.timeout_s,
        retries=args.retries,
    )
    return extract_json(raw)


def run_local_validator(final_json: Path, robotwin_root: str | None) -> int:
    from generate_text2env import validate_text2env

    data = load_json(final_json)
    issues = validate_text2env(data, Path(robotwin_root) if robotwin_root else None)
    write_json(
        final_json.with_suffix(".validation.json"),
        {
            "valid": not any(item["severity"] == "error" for item in issues),
            "issues": issues,
        },
    )
    return 1 if any(item["severity"] == "error" for item in issues) else 0


def run_flow(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    reference_examples = read_text(Path(args.reference_examples)) if args.reference_examples else default_reference_examples()
    known_assets = read_text(Path(args.known_assets)) if args.known_assets else "No explicit asset inventory provided."

    designer_prompt = render_template(
        read_text(PROMPTS_DIR / "text2env_designer.md"),
        {
            "USER_TASK": args.instruction,
            "KNOWN_ASSETS": known_assets,
            "REFERENCE_EXAMPLES": reference_examples,
        },
    )
    designer_prompt += "\n\n## Schema Notes\n\n" + read_text(SCHEMA_DOC)

    if args.backend == "mock":
        draft = mock_response("designer", args.instruction)
    else:
        draft = call_stage(args, designer_prompt)
    write_json(run_dir / "01_designer_prompt.json", {"prompt": designer_prompt})
    write_json(run_dir / "02_designer_draft.json", draft)

    critic_prompt = render_template(
        read_text(PROMPTS_DIR / "text2env_critic.md"),
        {
            "USER_TASK": args.instruction,
            "TEXT2ENV_DRAFT": compact_json(draft),
            "SCHEMA_DOC": read_text(SCHEMA_DOC),
            "TASK_API_NOTES": read_text(API_NOTES) if API_NOTES.exists() else "",
            "ASSET_CHECKS": known_assets,
        },
    )
    if args.backend == "mock":
        critic = mock_response("critic", args.instruction, draft=draft)
    else:
        critic = call_stage(args, critic_prompt)
    write_json(run_dir / "03_critic_prompt.json", {"prompt": critic_prompt})
    write_json(run_dir / "04_critic_report.json", critic)

    orchestrator_prompt = render_template(
        read_text(PROMPTS_DIR / "text2env_orchestrator.md"),
        {
            "USER_TASK": args.instruction,
            "TEXT2ENV_DRAFT": compact_json(draft),
            "CRITIC_REPORT": compact_json(critic),
            "SCHEMA_DOC": read_text(SCHEMA_DOC),
        },
    )
    if args.backend == "mock":
        orchestrator = mock_response("orchestrator", args.instruction, draft=draft)
    else:
        orchestrator = call_stage(args, orchestrator_prompt)
    write_json(run_dir / "05_orchestrator_prompt.json", {"prompt": orchestrator_prompt})
    write_json(run_dir / "06_orchestrator_result.json", orchestrator)

    final_text2env = orchestrator.get("final_text2env") if isinstance(orchestrator, dict) else None
    if not isinstance(final_text2env, dict):
        write_json(run_dir / "07_no_final_text2env.json", {"orchestrator_result": orchestrator})
        print(f"NO_FINAL_TEXT2ENV {run_dir / '06_orchestrator_result.json'}")
        return 1

    out_path = Path(args.out) if args.out else run_dir / "final_text2env.json"
    write_json(out_path, final_text2env)
    validation_code = run_local_validator(out_path, args.robotwin_root)

    print(f"WROTE {out_path}")
    print(f"WROTE {out_path.with_suffix('.validation.json')}")
    return validation_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Text2Env Designer/Critic/Orchestrator agents")
    parser.add_argument("--instruction", required=True, help="Natural-language tabletop task")
    parser.add_argument("--run-dir", required=True, help="Directory for prompts, intermediate outputs, and logs")
    parser.add_argument("--out", help="Final Text2Env JSON path")
    parser.add_argument("--backend", choices=["openai-compatible", "mock"], default="openai-compatible")
    parser.add_argument("--api-base", default=os.environ.get("TEXT2ENV_LLM_API_BASE", "http://localhost:8000/v1"))
    parser.add_argument("--api-key", default=os.environ.get("TEXT2ENV_LLM_API_KEY", ""))
    parser.add_argument("--model", default=os.environ.get("TEXT2ENV_LLM_MODEL", "Qwen/Qwen2.5-14B-Instruct"))
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--timeout-s", type=int, default=180)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--known-assets", help="Optional text/JSON file listing known RoboTwin assets")
    parser.add_argument("--reference-examples", help="Optional file containing reference Text2Env examples")
    parser.add_argument("--robotwin-root", help="Optional RoboTwin root for asset-aware validation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_flow(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
