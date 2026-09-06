from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from self_improving.harness.replay_vlm_worker import parse_advisory_response, run_worker
from self_improving.studies.vlm_fallback_prompt_optimization.runner import (
    ProviderOutcome,
    ResourceUsage,
)


class _FakeVisibleProvider:
    identity = SimpleNamespace(
        provider_id="tests.fake_qwen",
        revision="v1",
        implementation_sha256="9" * 64,
        kind="local_qwen",
        production_eligible=False,
    )

    def __init__(self) -> None:
        self.requests = []

    def invoke(self, request, progress):
        self.requests.append(request)
        progress("generated")
        assert [path.name for path in request.image_paths] == [
            "observer_start.png",
            "observer_mid.png",
            "observer_end.png",
            "preview_head.png",
        ]
        assert all(path.is_file() for path in request.image_paths)
        parsed = {
            "checks": {
                "object_presence": "pass",
                "penetration_or_floating": "pass",
                "overall_prompt_match": "pass",
            },
            "overall": "pass",
            "explanation": "The can and plate are visible.",
        }
        return ProviderOutcome(
            decision="visible_review_complete",
            result=parsed,
            raw_response=json.dumps(parsed).encode(),
            parsed_response=parsed,
            abstained=False,
            resource=ResourceUsage(
                gpu_time_ms=123,
                input_tokens=42,
                output_tokens=17,
                network_calls=0,
                visible_vlm_invocations=1,
            ),
            claims_physical_pass=False,
        )


class _FallbackVisibleProvider(_FakeVisibleProvider):
    def invoke(self, request, progress):
        self.requests.append(request)
        progress("generated")
        if request.attempt == 1:
            first = {
                "checks": {
                    "object_presence": {"can": "pass"},
                    "penetration_or_floating": {"can": "abstain"},
                },
                "overall": "review_required",
                "explanation": "Objects are visible; physics is uncertain.",
            }
            return ProviderOutcome(
                decision="visible_review_complete",
                result={},
                raw_response=f"```json\n{json.dumps(first)}\n```".encode(),
                parsed_response=first,
                abstained=False,
                resource=ResourceUsage(
                    gpu_time_ms=100,
                    network_calls=0,
                    visible_vlm_invocations=1,
                ),
                claims_physical_pass=False,
            )
        parsed = {
            "checks": {
                "object_presence": "pass",
                "penetration_or_floating": "abstain",
                "overall_prompt_match": "abstain",
            },
            "overall": "review_required",
            "explanation": "Objects are visible; physics is uncertain.",
        }
        return ProviderOutcome(
            decision="visible_review_complete",
            result=parsed,
            raw_response=json.dumps(parsed).encode(),
            parsed_response=parsed,
            abstained=True,
            resource=ResourceUsage(
                gpu_time_ms=200,
                network_calls=0,
                visible_vlm_invocations=1,
            ),
            claims_physical_pass=False,
        )


class _AlwaysInvalidVisibleProvider(_FallbackVisibleProvider):
    def invoke(self, request, progress):
        if request.attempt == 1:
            return super().invoke(request, progress)
        self.requests.append(request)
        return ProviderOutcome(
            decision="visible_review_complete",
            result={},
            raw_response=b"still not valid JSON",
            parsed_response=None,
            abstained=False,
            resource=ResourceUsage(network_calls=0, visible_vlm_invocations=1),
            claims_physical_pass=False,
        )


class _SemanticDriftVisibleProvider(_FallbackVisibleProvider):
    def invoke(self, request, progress):
        if request.attempt == 1:
            return super().invoke(request, progress)
        self.requests.append(request)
        changed = {
            "checks": {
                "object_presence": "pass",
                "penetration_or_floating": "pass",
                "overall_prompt_match": "pass",
            },
            "overall": "pass",
            "explanation": "Everything passes.",
        }
        return ProviderOutcome(
            decision="visible_review_complete",
            result=changed,
            raw_response=json.dumps(changed).encode(),
            parsed_response=changed,
            abstained=False,
            resource=ResourceUsage(network_calls=0, visible_vlm_invocations=1),
            claims_physical_pass=False,
        )


def test_advisory_parser_fails_closed_on_nested_check_values() -> None:
    raw = json.dumps(
        {
            "checks": {
                "object_presence": {"can": "pass"},
                "penetration_or_floating": "abstain",
                "overall_prompt_match": "abstain",
            },
            "overall": "review_required",
            "explanation": "Nested value is invalid for the strict output schema.",
        }
    ).encode()

    assert parse_advisory_response(raw) == ("format_invalid", None)


def test_worker_binds_images_invokes_once_and_writes_advisory_response(tmp_path: Path) -> None:
    revision = "6" * 40
    snapshot = tmp_path / "models--Qwen--Qwen2.5-VL-3B-Instruct" / "snapshots" / revision
    snapshot.mkdir(parents=True)
    manifest = tmp_path / "model_content.json"
    manifest.write_bytes(b"{}")
    images = []
    for name in ("observer_start", "observer_mid", "observer_end", "preview_head"):
        path = tmp_path / name
        payload = f"image:{name}".encode()
        path.write_bytes(payload)
        images.append(
            {
                "name": name,
                "path": str(path),
                "media_type": "image/png",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": "harness.replay_vlm_worker_request.v2",
                "assessment_run_id": "91000000-0000-4000-8000-000000000001",
                "replay_run_id": "92000000-0000-4000-8000-000000000001",
                "replay_invocation_digest": "a" * 64,
                "task_context": "Place a can on top of a plate.",
                "resolved_scene_sha256": "b" * 64,
                "prompt": "visible prompt",
                "prompt_version": "visible-v1",
                "prompt_sha256": hashlib.sha256(b"visible prompt").hexdigest(),
                "network_allowed": False,
                "model": {
                    "model_id": "Qwen/Qwen2.5-VL-3B-Instruct",
                    "revision": revision,
                    "local_snapshot_path": str(snapshot),
                    "snapshot_manifest_sha256": "7" * 64,
                    "model_content_manifest_path": str(manifest),
                    "model_content_manifest_sha256": hashlib.sha256(b"{}").hexdigest(),
                    "model_roster_sha256": "8" * 64,
                    "device_index": 0,
                    "max_new_tokens": 768,
                },
                "images": images,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    provider = _FakeVisibleProvider()

    run_worker(request_path, response_path, provider_factory=lambda _model: provider)

    response = json.loads(response_path.read_bytes())
    assert response["schema_version"] == "harness.replay_vlm_worker_response.v2"
    assert response["attempts"][0]["advisory_status"] == "pass"
    assert response["format_repair_prompt_base64"] is None
    assert response["claims_physical_pass"] is False
    assert response["resource_receipt"]["visible_vlm_invocations"] == 1
    assert response["resource_receipt"]["network_calls"] == 0
    assert len(provider.requests) == 1


def test_worker_repairs_format_once_and_binds_second_prompt_to_first_raw(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    _write_request(tmp_path, request_path)
    provider = _FallbackVisibleProvider()

    run_worker(request_path, response_path, provider_factory=lambda _model: provider)

    response = json.loads(response_path.read_bytes())
    assert len(provider.requests) == 2
    assert [item.attempt for item in provider.requests] == [1, 2]
    first = {
        "checks": {
            "object_presence": {"can": "pass"},
            "penetration_or_floating": {"can": "abstain"},
        },
        "overall": "review_required",
        "explanation": "Objects are visible; physics is uncertain.",
    }
    first_raw = f"```json\n{json.dumps(first)}\n```".encode()
    first_sha256 = hashlib.sha256(first_raw).hexdigest()
    assert provider.requests[1].repair_index == 1
    assert provider.requests[1].repair_of == first_sha256
    assert first_sha256 in provider.requests[1].prompt
    assert [item["advisory_status"] for item in response["attempts"]] == [
        "format_invalid",
        "abstain",
    ]
    assert response["attempts"][1]["repair_of_sha256"] == first_sha256
    repair_prompt = provider.requests[1].prompt.encode()
    assert response["attempts"][1]["prompt_sha256"] == hashlib.sha256(
        repair_prompt
    ).hexdigest()
    assert response["resource_receipt"]["visible_vlm_invocations"] == 2
    assert response["resource_receipt"]["gpu_time_ms"] == 300
    assert response["attempts"][1]["format_preservation_receipt"]["preserved"] is True


def test_worker_stops_after_one_failed_format_repair(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    _write_request(tmp_path, request_path)
    provider = _AlwaysInvalidVisibleProvider()

    run_worker(request_path, response_path, provider_factory=lambda _model: provider)

    response = json.loads(response_path.read_bytes())
    assert len(provider.requests) == 2
    assert len(response["attempts"]) == 2
    assert response["attempts"][-1]["advisory_status"] == "format_invalid"
    assert response["resource_receipt"]["visible_vlm_invocations"] == 2


def test_worker_rejects_format_repair_that_changes_visible_judgment(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    _write_request(tmp_path, request_path)
    provider = _SemanticDriftVisibleProvider()

    run_worker(request_path, response_path, provider_factory=lambda _model: provider)

    response = json.loads(response_path.read_bytes())
    assert len(provider.requests) == 2
    assert response["attempts"][-1]["advisory_status"] == "format_invalid"
    assert response["attempts"][-1]["parsed_response"] is None
    preservation = response["attempts"][-1]["format_preservation_receipt"]
    assert preservation["preserved"] is False
    assert set(preservation["mismatched_fields"]) == {
        "checks.overall_prompt_match",
        "checks.penetration_or_floating",
        "explanation",
        "overall",
    }
    assert response["resource_receipt"]["visible_vlm_invocations"] == 2


def _write_request(tmp_path: Path, request_path: Path) -> None:
    revision = "6" * 40
    snapshot = tmp_path / "models--Qwen--Qwen2.5-VL-3B-Instruct" / "snapshots" / revision
    snapshot.mkdir(parents=True)
    manifest = tmp_path / "model_content.json"
    manifest.write_bytes(b"{}")
    images = []
    for name in ("observer_start", "observer_mid", "observer_end", "preview_head"):
        path = tmp_path / name
        payload = f"image:{name}".encode()
        path.write_bytes(payload)
        images.append(
            {
                "name": name,
                "path": str(path),
                "media_type": "image/png",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
    request_path.write_text(
        json.dumps(
            {
                "schema_version": "harness.replay_vlm_worker_request.v2",
                "assessment_run_id": "91000000-0000-4000-8000-000000000001",
                "replay_run_id": "92000000-0000-4000-8000-000000000001",
                "replay_invocation_digest": "a" * 64,
                "task_context": "Place a can on top of a plate.",
                "resolved_scene_sha256": "b" * 64,
                "prompt": "visible prompt",
                "prompt_version": "visible-v1",
                "prompt_sha256": hashlib.sha256(b"visible prompt").hexdigest(),
                "network_allowed": False,
                "model": {
                    "model_id": "Qwen/Qwen2.5-VL-3B-Instruct",
                    "revision": revision,
                    "local_snapshot_path": str(snapshot),
                    "snapshot_manifest_sha256": "7" * 64,
                    "model_content_manifest_path": str(manifest),
                    "model_content_manifest_sha256": hashlib.sha256(b"{}").hexdigest(),
                    "model_roster_sha256": "8" * 64,
                    "device_index": 0,
                    "max_new_tokens": 768,
                },
                "images": images,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
