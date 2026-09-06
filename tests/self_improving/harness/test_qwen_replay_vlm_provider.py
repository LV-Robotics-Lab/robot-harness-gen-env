from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.qwen_replay_vlm_provider import (
    QwenReplayVlmProviderSettings,
    SubprocessQwenReplayVlmProvider,
)
from self_improving.harness.replay_vlm_assessment import (
    REPLAY_VLM_PROMPT,
    REPLAY_VLM_PROMPT_VERSION,
    ReplayVlmProviderRequest,
)


class _FakeProcessRunner:
    def __init__(self) -> None:
        self.request: dict[str, object] | None = None
        self.command: tuple[str, ...] | None = None
        self.environment: dict[str, str] | None = None

    def run(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> SimpleNamespace:
        del cwd, timeout_seconds
        self.command = command
        self.environment = environment
        request_path = Path(command[command.index("--request") + 1])
        response_path = Path(command[command.index("--response") + 1])
        self.request = json.loads(request_path.read_bytes())
        response_path.write_text(
            json.dumps(
                {
                    "schema_version": "harness.replay_vlm_worker_response.v1",
                    "advisory_status": "pass",
                    "raw_response_base64": base64.b64encode(b'{"overall":"pass"}').decode(),
                    "parsed_response": {"overall": "pass"},
                    "provider_receipt": {
                        "provider_id": "vlm_fallback.local_qwen_visible",
                        "production_eligible": False,
                    },
                    "model_receipt": {
                        "model_id": "Qwen/Qwen2.5-VL-3B-Instruct",
                        "revision": "6" * 40,
                        "role": "primary_local_vlm",
                        "snapshot_manifest_sha256": "7" * 64,
                        "model_content_manifest_sha256": hashlib.sha256(
                            b'{"model":"bound"}'
                        ).hexdigest(),
                        "model_roster_sha256": "8" * 64,
                        "max_new_tokens": 768,
                    },
                    "resource_receipt": {
                        "visible_vlm_invocations": 1,
                        "network_calls": 0,
                    },
                    "claims_physical_pass": False,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")


def test_subprocess_provider_runs_fixed_offline_worker_and_returns_typed_receipt(
    tmp_path: Path,
) -> None:
    interpreter = tmp_path / "python"
    interpreter.write_bytes(b"python-runtime")
    snapshot = tmp_path / "models--Qwen--Qwen2.5-VL-3B-Instruct" / "snapshots" / ("6" * 40)
    snapshot.mkdir(parents=True)
    manifest = tmp_path / "model_content_3b.json"
    manifest.write_bytes(b'{"model":"bound"}')
    settings = QwenReplayVlmProviderSettings(
        interpreter=interpreter,
        module_root=Path.cwd(),
        work_root=tmp_path / "work",
        model_id="Qwen/Qwen2.5-VL-3B-Instruct",
        revision="6" * 40,
        local_snapshot_path=snapshot,
        snapshot_manifest_sha256="7" * 64,
        model_content_manifest_path=manifest,
        model_content_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        model_roster_sha256="8" * 64,
        device_index=0,
        timeout_seconds=900,
    )
    store = LocalArtifactStore(tmp_path / "cas")
    image_refs = []
    for name in ("observer_start", "observer_mid", "observer_end", "preview_head"):
        source = tmp_path / f"{name}.png"
        source.write_bytes(b"image-" + name.encode())
        image_refs.append(
            store.put_file(
                source,
                name=name,
                media_type="image/png",
                schema_version=None,
            )
        )
    images = tuple(store.resolve(ref) for ref in image_refs)
    request = ReplayVlmProviderRequest(
        assessment_run_id=UUID("91000000-0000-4000-8000-000000000001"),
        replay_run_id=UUID("92000000-0000-4000-8000-000000000001"),
        replay_invocation_digest="a" * 64,
        task_context="Place a can on top of a plate.",
        resolved_scene_sha256="b" * 64,
        prompt=REPLAY_VLM_PROMPT,
        prompt_version=REPLAY_VLM_PROMPT_VERSION,
        prompt_sha256=hashlib.sha256(REPLAY_VLM_PROMPT.encode()).hexdigest(),
        images=images,
    )
    runner = _FakeProcessRunner()
    provider = SubprocessQwenReplayVlmProvider(settings, process_runner=runner)

    result = provider.assess(request)

    assert result.advisory_status == "pass"
    assert result.raw_response == b'{"overall":"pass"}'
    assert result.claims_physical_pass is False
    assert runner.command is not None
    assert runner.command[:3] == (
        str(interpreter.resolve()),
        "-m",
        "self_improving.harness.replay_vlm_worker",
    )
    assert runner.environment is not None
    assert runner.environment["HF_HUB_OFFLINE"] == "1"
    assert runner.environment["TRANSFORMERS_OFFLINE"] == "1"
    assert runner.request is not None
    assert runner.request["network_allowed"] is False
    assert runner.request["prompt_sha256"] == request.prompt_sha256
    assert [item["sha256"] for item in runner.request["images"]] == [
        ref.sha256 for ref in image_refs
    ]
    assert {item.name for item in provider.dependencies} == {
        "replay_vlm.model",
        "replay_vlm.prompt",
        "replay_vlm.provider",
        "replay_vlm.worker_runtime",
    }


def test_subprocess_provider_rejects_worker_model_identity_drift(tmp_path: Path) -> None:
    class DriftingRunner(_FakeProcessRunner):
        def run(self, command, **kwargs):
            completed = super().run(command, **kwargs)
            response_path = Path(command[command.index("--response") + 1])
            response = json.loads(response_path.read_bytes())
            response["model_receipt"]["revision"] = "f" * 40
            response_path.write_text(json.dumps(response), encoding="utf-8")
            return completed

    interpreter = tmp_path / "python"
    interpreter.write_bytes(b"python-runtime")
    revision = "6" * 40
    snapshot = tmp_path / "models--Qwen--Qwen2.5-VL-3B-Instruct" / "snapshots" / revision
    snapshot.mkdir(parents=True)
    manifest = tmp_path / "model_content_3b.json"
    manifest.write_bytes(b'{"model":"bound"}')
    provider = SubprocessQwenReplayVlmProvider(
        QwenReplayVlmProviderSettings(
            interpreter=interpreter,
            module_root=Path.cwd(),
            work_root=tmp_path / "work",
            model_id="Qwen/Qwen2.5-VL-3B-Instruct",
            revision=revision,
            local_snapshot_path=snapshot,
            snapshot_manifest_sha256="7" * 64,
            model_content_manifest_path=manifest,
            model_content_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
            model_roster_sha256="8" * 64,
        ),
        process_runner=DriftingRunner(),
    )
    store = LocalArtifactStore(tmp_path / "cas")
    images = []
    for name in ("observer_start", "observer_mid", "observer_end", "preview_head"):
        source = tmp_path / f"{name}.png"
        source.write_bytes(name.encode())
        ref = store.put_file(source, name=name, media_type="image/png", schema_version=None)
        images.append(store.resolve(ref))
    request = ReplayVlmProviderRequest(
        assessment_run_id=UUID("93000000-0000-4000-8000-000000000001"),
        replay_run_id=UUID("94000000-0000-4000-8000-000000000001"),
        replay_invocation_digest="a" * 64,
        task_context="Place a can on top of a plate.",
        resolved_scene_sha256="b" * 64,
        prompt=REPLAY_VLM_PROMPT,
        prompt_version=REPLAY_VLM_PROMPT_VERSION,
        prompt_sha256=hashlib.sha256(REPLAY_VLM_PROMPT.encode()).hexdigest(),
        images=tuple(images),
    )

    with pytest.raises(RuntimeError, match="model receipt"):
        provider.assess(request)
