#!/usr/bin/env python3
"""Small Moonshot/Kimi API client used by scene generation agents."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
DEFAULT_TEXT_MODEL = "kimi-k2.5"
DEFAULT_VISION_MODEL = "kimi-k2.5"

# Optional local-only fallback.
# If you do not want to export MOONSHOT_API_KEY in the shell, paste your key
# between the quotes below for local experiments only. Never commit or push
# this file with a real key; prefer generate_scene/local_config.py locally.
HARDCODED_MOONSHOT_API_KEY = ""


def _api_key_from_local_config() -> str:
    try:
        from generate_scene.local_config import MOONSHOT_API_KEY as local_key
    except Exception:
        return ""
    return str(local_key or "")


def _local_config_value(name: str) -> str:
    try:
        from generate_scene import local_config
    except Exception:
        return ""
    return str(getattr(local_config, name, "") or "")


class MoonshotConfigError(RuntimeError):
    """Raised when the Moonshot client is not configured."""


def api_key_from_env() -> str:
    key = (
        os.environ.get("MOONSHOT_API_KEY")
        or os.environ.get("KIMI_API_KEY")
        or _api_key_from_local_config()
        or HARDCODED_MOONSHOT_API_KEY
    )
    if not key:
        raise MoonshotConfigError(
            "Set MOONSHOT_API_KEY/KIMI_API_KEY or create generate_scene/local_config.py with MOONSHOT_API_KEY."
        )
    return key


def model_from_env(kind: str) -> str:
    if kind == "vision":
        return os.environ.get("MOONSHOT_VISION_MODEL") or _local_config_value("MOONSHOT_VISION_MODEL") or DEFAULT_VISION_MODEL
    return os.environ.get("MOONSHOT_TEXT_MODEL") or _local_config_value("MOONSHOT_TEXT_MODEL") or DEFAULT_TEXT_MODEL


def base_url_from_env() -> str:
    return (os.environ.get("MOONSHOT_BASE_URL") or _local_config_value("MOONSHOT_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def chat_completion(
    *,
    messages: list[dict[str, Any]],
    model: str | None = None,
    temperature: float = 1.0,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
    timeout: int = 240,
) -> str:
    """Call Moonshot's OpenAI-compatible chat completions endpoint."""

    payload: dict[str, Any] = {
        "model": model or model_from_env("text"),
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if response_format is not None:
        payload["response_format"] = response_format

    url = f"{base_url_from_env()}/chat/completions"
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
        try:
            data = _send(payload)
        except (TimeoutError, socket.timeout, urllib.error.URLError):
            data = _send(payload)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 400 and "invalid temperature" in error_body and "only 1 is allowed" in error_body:
            payload["temperature"] = 1
            try:
                data = _send(payload)
            except urllib.error.HTTPError as retry_exc:
                retry_body = retry_exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Moonshot API HTTP {retry_exc.code}: {retry_body}") from retry_exc
        elif response_format is not None and exc.code in {400, 422}:
            payload.pop("response_format", None)
            try:
                data = _send(payload)
            except urllib.error.HTTPError as retry_exc:
                retry_body = retry_exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Moonshot API HTTP {retry_exc.code}: {retry_body}") from retry_exc
        else:
            raise RuntimeError(f"Moonshot API HTTP {exc.code}: {error_body}") from exc

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Moonshot API response: {data}") from exc


def image_to_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


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
        raise ValueError(f"Moonshot response did not contain valid JSON: {text[:1000]}") from exc
    if not isinstance(data, dict):
        raise ValueError("Moonshot response JSON must be an object.")
    return data


def json_chat(
    *,
    system: str,
    user: str | list[dict[str, Any]],
    model: str | None = None,
    temperature: float = 1.0,
    max_tokens: int | None = None,
    timeout: int = 240,
) -> dict[str, Any]:
    content: str | list[dict[str, Any]] = user
    raw = chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        timeout=timeout,
    )
    result = parse_json_object(raw)
    result.setdefault("_moonshot_raw_response", raw)
    return result
