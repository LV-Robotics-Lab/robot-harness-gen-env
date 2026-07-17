"""Rendered-scene VLM critic with strict machine-readable evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .schema import ResolvedSceneSpec

CRITIC_SCHEMA_VERSION = "robotwin.rendered_scene_critic.v1"
REQUIRED_CHECKS = {
    "object_presence",
    "support_relation",
    "penetration_or_floating",
    "articulation_state",
    "overall_prompt_match",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _extract_json(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    else:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("VLM critic response must be a JSON object")
    return value


def build_critic_prompt(resolved: ResolvedSceneSpec) -> str:
    objects = [
        {
            "object_id": item.object_id,
            "category": item.category,
            "color": item.color,
            "asset_id": item.asset_id,
            "asset_provenance": item.asset_provenance,
            "support_relation": item.support_relation.value,
            "support_target": item.support_target,
            "articulation_state": (
                item.articulation_state.model_dump(mode="json") if item.articulation_state else None
            ),
        }
        for item in resolved.objects
    ]
    relations = [item.model_dump(mode="json", exclude_none=True) for item in resolved.relations]
    contract = {
        "task": "Judge the rendered RoboTwin scene against the requested scene. Use only visible evidence. Static geometry and runtime checks are handled separately.",
        "request": resolved.request,
        "objects": objects,
        "relations": relations,
        "required_checks": sorted(REQUIRED_CHECKS),
        "rules": [
            "Report missing or visually wrong objects.",
            "Report visible floating, deep penetration, or unsupported stacking/containment.",
            "Return exactly five check objects, one for every required_checks entry; never omit a check.",
            "Use the exact required check names and use status not_applicable when a check does not apply.",
            "For articulation_state, use not_applicable only when no articulated state was requested.",
            "Do not claim exact metric distances from pixels.",
            "Return strict JSON and no markdown.",
        ],
        "output_schema": {
            "status": "pass or fail",
            "summary": "short evidence-grounded summary",
            "checks": [
                {
                    "name": "one required check name",
                    "status": "pass, fail, warning, or not_applicable",
                    "evidence": "visible evidence",
                }
            ],
            "issues": [
                {"severity": "blocker, major, or minor", "target": "object or scene", "message": "specific issue"}
            ],
        },
    }
    return json.dumps(contract, ensure_ascii=False, indent=2)


def build_repair_prompt(
    resolved: ResolvedSceneSpec,
    *,
    missing_check: str,
) -> str:
    contract = {
        "task": "Judge exactly one omitted rendered-scene check using the rendered views.",
        "request": resolved.request,
        "check_name": missing_check,
        "rules": [
            f"Return exactly one check object named {missing_check} and no other check names.",
            "Judge this check from the rendered views and request without assuming hidden geometry.",
            "Use only pass, fail, warning, or not_applicable as check status.",
            "Return strict JSON and no markdown.",
        ],
        "output_schema": {
            "status": "pass or fail",
            "summary": "short repair summary",
            "checks": [
                {
                    "name": missing_check,
                    "status": "pass, fail, warning, or not_applicable",
                    "evidence": "visible evidence",
                }
            ],
            "issues": [
                {"severity": "blocker, major, or minor", "target": "object or scene", "message": "specific issue"}
            ],
        },
    }
    return json.dumps(contract, ensure_ascii=False, indent=2)


def _normalize_checks(parsed: dict[str, Any]) -> list[dict[str, str]]:
    checks = parsed.get("checks") if isinstance(parsed.get("checks"), list) else []
    normalized: list[dict[str, str]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        name = str(check.get("name") or "").strip()
        status = str(check.get("status") or "").strip().lower()
        if name not in REQUIRED_CHECKS or status not in {"pass", "fail", "warning", "not_applicable"}:
            continue
        normalized.append(
            {"name": name, "status": status, "evidence": str(check.get("evidence") or "").strip()}
        )
    return normalized


def run_qwen_local(
    *,
    image_paths: list[Path],
    prompt: str,
    model_name: str,
) -> str:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import torch
    from qwen_vl_utils import process_vision_info
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        model_name,
        torch_dtype="auto",
        device_map="auto",
        low_cpu_mem_usage=True,
        local_files_only=True,
    )
    processor = AutoProcessor.from_pretrained(
        model_name,
        min_pixels=256 * 28 * 28,
        max_pixels=1024 * 28 * 28,
        local_files_only=True,
    )
    content: list[dict[str, Any]] = []
    for index, path in enumerate(image_paths, start=1):
        content.append({"type": "text", "text": f"Rendered view {index}: {path.name}"})
        content.append({"type": "image", "image": f"file://{path.resolve()}"})
    content.append({"type": "text", "text": prompt})
    messages = [{"role": "user", "content": content}]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
    ).to(model.device)
    with torch.inference_mode():
        generated = model.generate(**inputs, max_new_tokens=768, do_sample=False)
    trimmed = [output[len(source) :] for source, output in zip(inputs.input_ids, generated)]
    return processor.batch_decode(trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]


def review_rendered_scene(
    *,
    resolved: ResolvedSceneSpec,
    image_paths: list[Path],
    provider: str = "qwen_local",
    model_name: str = "Qwen/Qwen2.5-VL-3B-Instruct",
    infer: Callable[..., str] | None = None,
) -> dict[str, Any]:
    existing = [path.expanduser().resolve() for path in image_paths if path.is_file()]
    missing = [str(path) for path in image_paths if not path.is_file()]
    base = {
        "schema_version": CRITIC_SCHEMA_VERSION,
        "scene_id": resolved.scene_id,
        "resolved_scene_sha256": resolved.digest(),
        "provider": provider,
        "model": model_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "images": [
            {"path": str(path), "sha256": _sha256(path)} for path in existing
        ],
        "missing_images": missing,
    }
    if missing or not existing:
        return {
            **base,
            "status": "fail",
            "summary": "Rendered critic could not run because required images are missing.",
            "checks": [],
            "issues": [
                {"severity": "blocker", "target": "render", "message": f"missing images: {missing}"}
            ],
        }
    if provider != "qwen_local" and infer is None:
        raise ValueError(f"unsupported rendered critic provider: {provider}")
    inference = infer or run_qwen_local
    raw = inference(image_paths=existing, prompt=build_critic_prompt(resolved), model_name=model_name)
    parsed = _extract_json(raw)
    normalized_checks = _normalize_checks(parsed)
    present = {item["name"] for item in normalized_checks}
    missing_checks = sorted(REQUIRED_CHECKS - present)
    repair_raw_responses: list[str] = []
    repair_parsed_responses: list[dict[str, Any]] = []
    repair_errors: list[str] = []
    if missing_checks:
        for missing_check in missing_checks:
            repair_raw = inference(
                image_paths=existing,
                prompt=build_repair_prompt(resolved, missing_check=missing_check),
                model_name=model_name,
            )
            repair_raw_responses.append(repair_raw)
            try:
                repair_parsed = _extract_json(repair_raw)
            except (json.JSONDecodeError, ValueError) as error:
                repair_errors.append(f"{missing_check}: {error}")
                continue
            repair_parsed_responses.append(repair_parsed)
            matching = [
                check
                for check in _normalize_checks(repair_parsed)
                if check["name"] == missing_check
            ]
            if len(matching) == 1:
                normalized_checks.append(matching[0])
        present = {item["name"] for item in normalized_checks}
        missing_checks = sorted(REQUIRED_CHECKS - present)
    parsed_responses = [parsed, *repair_parsed_responses]
    issues = [
        issue
        for response in parsed_responses
        for issue in (response.get("issues") if isinstance(response.get("issues"), list) else [])
    ]
    normalized_issues: list[dict[str, str]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity") or "").strip().lower()
        target = str(issue.get("target") or "").strip()
        message = str(issue.get("message") or "").strip()
        if severity not in {"blocker", "major", "minor"} or not target or not message:
            continue
        normalized_issues.append(
            {"severity": severity, "target": target, "message": message}
        )
    model_status = str(parsed.get("status") or "").lower()
    if missing_checks:
        normalized_issues.append(
            {
                "severity": "blocker",
                "target": "critic_contract",
                "message": f"missing required checks: {missing_checks}",
            }
        )
    if repair_errors:
        normalized_issues.append(
            {
                "severity": "blocker",
                "target": "critic_contract",
                "message": f"repair response is not valid JSON: {repair_errors}",
            }
        )
    response_statuses = [
        str(response.get("status") or "").lower() for response in parsed_responses
    ]
    invalid_statuses = [value or "missing" for value in response_statuses if value not in {"pass", "fail"}]
    if invalid_statuses:
        normalized_issues.append(
            {
                "severity": "blocker",
                "target": "critic_contract",
                "message": f"invalid top-level status: {invalid_statuses}",
            }
        )
    model_status = "fail" if "fail" in response_statuses else response_statuses[0]
    duplicate_checks = sorted(
        name
        for name in REQUIRED_CHECKS
        if sum(item["name"] == name for item in normalized_checks) > 1
    )
    if duplicate_checks:
        normalized_issues.append(
            {
                "severity": "blocker",
                "target": "critic_contract",
                "message": f"duplicate required checks: {duplicate_checks}",
            }
        )
    contract_normalizations: list[dict[str, str]] = []
    articulation_requested = any(item.articulation_state is not None for item in resolved.objects)
    articulation_check = next(
        (item for item in normalized_checks if item["name"] == "articulation_state"),
        None,
    )
    if articulation_check is not None:
        if not articulation_requested and articulation_check["status"] != "not_applicable":
            model_status_before_normalization = articulation_check["status"]
            articulation_check["status"] = "not_applicable"
            articulation_check["evidence"] = (
                "No articulated state was requested in ResolvedSceneSpec."
            )
            contract_normalizations.append(
                {
                    "check": "articulation_state",
                    "model_status": model_status_before_normalization,
                    "normalized_status": "not_applicable",
                    "reason": "applicability is determined by ResolvedSceneSpec",
                }
            )
        elif articulation_requested and articulation_check["status"] == "not_applicable":
            normalized_issues.append(
                {
                    "severity": "blocker",
                    "target": "critic_contract",
                    "message": "articulation_state is required but the model returned not_applicable",
                }
            )
    failed = any(item["status"] == "fail" for item in normalized_checks)
    blocking_issue = any(
        item["severity"] in {"blocker", "major"} for item in normalized_issues
    )
    status = (
        "pass"
        if model_status == "pass" and not failed and not missing_checks and not blocking_issue
        else "fail"
    )
    return {
        **base,
        "status": status,
        "summary": str(parsed.get("summary") or "").strip(),
        "checks": normalized_checks,
        "issues": normalized_issues,
        "contract_normalizations": contract_normalizations,
        "missing_required_checks": missing_checks,
        "raw_response": raw,
        "repair_raw_responses": repair_raw_responses,
    }
