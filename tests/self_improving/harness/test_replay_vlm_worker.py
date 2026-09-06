from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from self_improving.harness.replay_vlm_worker import run_worker
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
            raw_response=b'{"overall":"pass"}',
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
                "schema_version": "harness.replay_vlm_worker_request.v1",
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
    assert response["schema_version"] == "harness.replay_vlm_worker_response.v1"
    assert response["advisory_status"] == "pass"
    assert response["claims_physical_pass"] is False
    assert response["resource_receipt"]["visible_vlm_invocations"] == 1
    assert response["resource_receipt"]["network_calls"] == 0
    assert len(provider.requests) == 1
