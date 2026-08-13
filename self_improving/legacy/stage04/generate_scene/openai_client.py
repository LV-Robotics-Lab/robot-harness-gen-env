#!/usr/bin/env python3
"""Small OpenAI Responses API client used by scene generation agents."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TEXT_MODEL = "gpt-5.5"
DEFAULT_VISION_MODEL = "gpt-5.5"

# Optional local-only fallback.
# Prefer generate_scene/local_config.py locally. Never commit a real key.
HARDCODED_OPENAI_API_KEY = ""


def _api_key_from_local_config() -> str:
    try:
        from generate_scene.local_config import OPENAI_API_KEY as local_key
    except Exception:
        return ""
    return str(local_key or "")


def _local_config_value(name: str) -> str:
    try:
        from generate_scene import local_config
    except Exception:
        return ""
    return str(getattr(local_config, name, "") or "")


class OpenAIConfigError(RuntimeError):
    """Raised when the OpenAI client is not configured."""


def api_key_from_env() -> str:
    key = os.environ.get("OPENAI_API_KEY") or _api_key_from_local_config() or HARDCODED_OPENAI_API_KEY
    if not key:
        raise OpenAIConfigError("Set OPENAI_API_KEY or create generate_scene/local_config.py with OPENAI_API_KEY.")
    return key


def model_from_env(kind: str) -> str:
    if kind == "vision":
        return os.environ.get("OPENAI_VISION_MODEL") or _local_config_value("OPENAI_VISION_MODEL") or DEFAULT_VISION_MODEL
    return os.environ.get("OPENAI_TEXT_MODEL") or _local_config_value("OPENAI_TEXT_MODEL") or DEFAULT_TEXT_MODEL


def base_url_from_env() -> str:
    return (os.environ.get("OPENAI_BASE_URL") or _local_config_value("OPENAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def image_to_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _to_responses_content(content: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """Convert OpenAI-compatible chat blocks to Responses API content blocks."""

    if isinstance(content, str):
        return content

    converted: list[dict[str, Any]] = []
    for block in content:
        block_type = block.get("type")
        if block_type == "input_text":
            converted.append(block)
        elif block_type == "text":
            converted.append({"type": "input_text", "text": block.get("text", "")})
        elif block_type == "input_image":
            converted.append(block)
        elif block_type == "image_url":
            image_url = block.get("image_url", {})
            if isinstance(image_url, dict):
                url = image_url.get("url", "")
            else:
                url = str(image_url)
            converted.append({"type": "input_image", "image_url": url})
        else:
            converted.append({"type": "input_text", "text": json.dumps(block, ensure_ascii=False)})
    return converted


def _extract_output_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]

    parts: list[str] = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content["text"])
            elif content.get("type") == "refusal" and isinstance(content.get("refusal"), str):
                raise RuntimeError(f"OpenAI model refused the request: {content['refusal']}")
    if parts:
        return "\n".join(parts)
    raise RuntimeError(f"Unexpected OpenAI API response: {data}")


def responses_completion(
    *,
    messages: list[dict[str, Any]],
    model: str | None = None,
    temperature: float | None = 1.0,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
    timeout: int = 120,
) -> str:
    """Call OpenAI's Responses API."""

    payload: dict[str, Any] = {
        "model": model or model_from_env("text"),
        "input": [
            {
                "role": message["role"],
                "content": _to_responses_content(message.get("content", "")),
            }
            for message in messages
        ],
        "store": False,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_output_tokens"] = max_tokens
    if response_format is not None:
        payload["text"] = {"format": response_format}

    url = f"{base_url_from_env()}/responses"
    headers = {
        "Authorization": f"Bearer {api_key_from_env()}",
        "Content-Type": "application/json",
    }

    def _send(data: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(data).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        data = _send(payload)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        if response_format is not None and exc.code in {400, 422}:
            payload.pop("text", None)
            try:
                data = _send(payload)
            except urllib.error.HTTPError as retry_exc:
                retry_body = retry_exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"OpenAI API HTTP {retry_exc.code}: {retry_body}") from retry_exc
        else:
            raise RuntimeError(f"OpenAI API HTTP {exc.code}: {error_body}") from exc

    return _extract_output_text(data)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from strict JSON or a fenced JSON response."""

    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    else:
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first != -1 and last != -1 and last > first:
            cleaned = cleaned[first : last + 1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OpenAI response did not contain valid JSON: {text[:1000]}") from exc
    if not isinstance(data, dict):
        raise ValueError("OpenAI response JSON must be an object.")
    return data


def json_chat(
    *,
    system: str,
    user: str | list[dict[str, Any]],
    model: str | None = None,
    temperature: float = 1.0,
    max_tokens: int | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    raw = responses_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        timeout=timeout,
    )
    result = parse_json_object(raw)
    result.setdefault("_openai_raw_response", raw)
    return result
