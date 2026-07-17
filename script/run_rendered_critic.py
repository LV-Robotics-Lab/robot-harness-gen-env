#!/usr/bin/env python3
"""Run a local VLM critic over real RoboTwin render evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scene_gen.rendered_critic import review_rendered_scene
from scene_gen.schema import ResolvedSceneSpec


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolved-scene", required=True)
    parser.add_argument("--image", action="append", required=True)
    parser.add_argument("--provider", default="qwen_local", choices=["qwen_local"])
    parser.add_argument("--model", default="Qwen/Qwen2.5-VL-3B-Instruct")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    resolved = ResolvedSceneSpec.model_validate_json(
        Path(args.resolved_scene).read_text(encoding="utf-8")
    )
    review = review_rendered_scene(
        resolved=resolved,
        image_paths=[Path(value) for value in args.image],
        provider=args.provider,
        model_name=args.model,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(review, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"{review['status'].upper()} scene={resolved.scene_id} "
        f"checks={len(review.get('checks', []))} provider={review['provider']}"
    )
    return 0 if review["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
