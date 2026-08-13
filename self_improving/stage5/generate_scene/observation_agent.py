#!/usr/bin/env python3
"""Vision observation agent for RoboTwin scene previews."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generate_scene import moonshot_client, openai_client
from generate_scene.schemas import read_json, write_json

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def _load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def _uses_openai(provider: str) -> bool:
    return provider.lower() in {"openai", "gpt", "gpt5", "gpt-5", "gpt5.5", "gpt-5.5"}


def _image_to_data_url(path: Path, provider: str) -> str:
    if _uses_openai(provider):
        return openai_client.image_to_data_url(path)
    return moonshot_client.image_to_data_url(path)


def _json_chat_for_provider(
    *,
    provider: str,
    system: str,
    user: str | list[dict[str, Any]],
    model: str | None = None,
) -> dict[str, Any]:
    if _uses_openai(provider):
        return openai_client.json_chat(system=system, user=user, model=model or openai_client.model_from_env("vision"))
    return moonshot_client.json_chat(system=system, user=user, model=model or moonshot_client.model_from_env("vision"))


def _image_blocks(smoke_dir: Path, provider: str) -> tuple[list[dict[str, Any]], list[str]]:
    content: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in ["observer_camera.png", "head_camera.png"]:
        path = smoke_dir / name
        if not path.exists():
            missing.append(name)
            continue
        content.append({"type": "text", "text": f"Image: {name}"})
        content.append({"type": "image_url", "image_url": {"url": _image_to_data_url(path, provider)}})
    return content, missing


def observe_scene_with_provider(
    *,
    smoke_dir: Path,
    prompt: str,
    placement: dict[str, Any] | None = None,
    asset_grounding: dict[str, Any] | None = None,
    model_provider: str = "moonshot",
    model: str | None = None,
) -> dict[str, Any]:
    """Use an external vision model to review rendered RoboTwin scene evidence."""

    smoke_dir = smoke_dir.expanduser()
    content, missing = _image_blocks(smoke_dir, model_provider)
    if missing:
        return {
            "schema_version": "robotwin.tabletop_visual_review.v0",
            "prompt": prompt,
            "status": "fail_missing_artifacts",
            "review_mode": model_provider,
            "model_backend": model_provider,
            "missing_required_artifacts": missing,
            "checks": [],
            "issues": [{"severity": "blocker", "message": f"Missing preview image(s): {missing}"}],
            "repair_suggestions": [],
            "generated_at": date.today().isoformat(),
        }

    system = _load_prompt("observation_vlm_agent.md")
    text_prompt = {
        "task": "Review RoboTwin tabletop scene preview images.",
        "language_prompt": prompt,
        "placement": placement or {},
        "asset_grounding": asset_grounding or {},
        "required_output_schema": {
            "schema_version": "robotwin.tabletop_visual_review.v0",
            "prompt": prompt,
            "status": "pass | fail_visual_review | repair_required",
            "review_mode": model_provider,
            "model_backend": model_provider,
            "summary": "short summary",
            "checks": [
                {
                    "name": "object_identity | object_presence | table_contact | penetration | floating | orientation | occlusion | spatial_relation",
                    "status": "pass | fail | warning",
                    "evidence": "what you saw in the image",
                }
            ],
            "issues": [
                {
                    "severity": "blocker | major | minor",
                    "target": "object id or scene",
                    "message": "specific visual problem",
                }
            ],
            "repair_suggestions": [
                {
                    "target": "placement pose, qpos, z_policy, asset defaults, or catalog metadata",
                    "suggestion": "concrete repair action",
                }
            ],
        },
        "direction_rule": "left/right/front/back are judged in robot or dual-arm first-person frame, not image screen coordinates.",
    }
    user_content: list[dict[str, Any]] = [{"type": "text", "text": json.dumps(text_prompt, ensure_ascii=False, indent=2)}, *content]
    review = _json_chat_for_provider(provider=model_provider, system=system, user=user_content, model=model)
    review["schema_version"] = "robotwin.tabletop_visual_review.v0"
    review["prompt"] = prompt
    review["review_mode"] = model_provider
    review["model_backend"] = model_provider
    review.setdefault("generated_at", date.today().isoformat())
    review.setdefault("missing_required_artifacts", [])
    review.setdefault("checks", [])
    review.setdefault("issues", [])
    review.setdefault("repair_suggestions", [])
    if review.get("status") not in {"pass", "fail_visual_review", "repair_required"}:
        has_fail = any(check.get("status") == "fail" for check in review.get("checks", []))
        review["status"] = "fail_visual_review" if has_fail or review.get("issues") else "pass"
    return review


def observe_scene_with_moonshot(
    *,
    smoke_dir: Path,
    prompt: str,
    placement: dict[str, Any] | None = None,
    asset_grounding: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    return observe_scene_with_provider(
        smoke_dir=smoke_dir,
        prompt=prompt,
        placement=placement,
        asset_grounding=asset_grounding,
        model_provider="moonshot",
        model=model,
    )


def observe_scene_with_openai(
    *,
    smoke_dir: Path,
    prompt: str,
    placement: dict[str, Any] | None = None,
    asset_grounding: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    return observe_scene_with_provider(
        smoke_dir=smoke_dir,
        prompt=prompt,
        placement=placement,
        asset_grounding=asset_grounding,
        model_provider="openai",
        model=model,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Review RoboTwin scene preview images with a VLM provider.")
    parser.add_argument("--smoke-dir", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--placement")
    parser.add_argument("--asset-grounding")
    parser.add_argument("--out", required=True)
    parser.add_argument("--model-provider", choices=["moonshot", "openai"], default="moonshot")
    parser.add_argument("--model")
    args = parser.parse_args()

    placement = read_json(Path(args.placement)) if args.placement else None
    asset_grounding = read_json(Path(args.asset_grounding)) if args.asset_grounding else None
    review = observe_scene_with_provider(
        smoke_dir=Path(args.smoke_dir),
        prompt=args.prompt,
        placement=placement,
        asset_grounding=asset_grounding,
        model_provider=args.model_provider,
        model=args.model,
    )
    write_json(Path(args.out), review)
    print(f"{review['status'].upper()} {args.out}")
    return 0 if review["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
