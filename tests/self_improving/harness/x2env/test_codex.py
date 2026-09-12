"""Advisory seam using an explicit external process double, never real model evidence."""

import hashlib
import json
import signal
import subprocess
import sys

import pytest

from self_improving.harness.x2env.contracts import X2EnvRequest
from self_improving.harness.x2env.input import ingest
from self_improving.harness.x2env.store import Store


def test_external_cancellation_propagates_after_scoped_cleanup(tmp_path):
    from self_improving.harness.x2env.codex import CodexBackend

    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(text="a table", seed=1, idempotency_key="cancel", output_dir=str(tmp_path)),
        store,
    )
    path = tmp_path / "waiting-model-double"
    path.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(20)\n")
    path.chmod(0o700)
    backend = CodexBackend(path, hashlib.sha256(path.read_bytes()).hexdigest(), "double", store)

    def cancel(signum, frame):
        raise KeyboardInterrupt("explicit test cancellation")

    old = signal.signal(signal.SIGALRM, cancel)
    signal.setitimer(signal.ITIMER_REAL, 0.3)
    try:
        with pytest.raises(KeyboardInterrupt):
            backend.interpret(bundle, output_root=tmp_path / "attempt", timeout=10)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    terminal = json.loads((tmp_path / "attempt" / "process-terminal.json").read_text())
    assert terminal["failure"] == "model_interrupted"
    assert terminal["signals"] == ["SIGINT"]
    assert terminal["reaped"] is True and terminal["returncode"] is not None
    assert (tmp_path / "attempt" / "codex.stderr").is_file()


def executable(tmp_path, response, event="turn.completed", tool=False):
    path = tmp_path / "model-double"
    path.write_text(
        f"#!{sys.executable}\nimport sys,json,pathlib\n"
        "prompt=sys.stdin.read()\n"
        f"pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text({json.dumps(response)!r})\n"
        f"print({json.dumps({'type': event})!r})\n"
        + ('print(\'{"type":"item.completed","item":{"type":"mcp_tool_call"}}\')\n' if tool else "")
    )
    path.chmod(0o700)
    return path


def test_unknown_is_advisory_and_logs_bound_input_and_model(tmp_path):
    from self_improving.harness.x2env.codex import CodexBackend

    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(
            text="粉红色鼠标在打开的柜子上",
            seed=1,
            idempotency_key="model",
            output_dir=str(tmp_path),
        ),
        store,
    )
    response = {
        "scene": None,
        "unknowns": [
            {
                "field": "cabinet.dimensions",
                "reason": "not specified",
                "critical": True,
                "provenance": [
                    {"source": "text", "input_sha256": bundle.text.sha256, "kind": "explicit"}
                ],
            }
        ],
    }
    path = executable(tmp_path, response)
    backend = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    )
    result = backend.interpret(bundle, output_root=tmp_path / "attempt", timeout=10)
    assert result.status == "completed"
    assert result.authority == "advisory_only"
    assert result.proposal.unknowns[0].critical is True
    assert result.proposal.unknowns[0].reason_kind == "unspecified"
    prompt = (tmp_path / "attempt" / "prompt.txt").read_text()
    assert "invent dimensions or silently resolve conflicting media" in prompt
    assert "Unknown yaw is null, not zero" in prompt
    manifest = json.loads(store.read_artifact(result.evidence[-1]))
    assert manifest["model"] == "test-double"
    assert manifest["input_sha256"] == bundle.request_sha256
    assert manifest["external_agent_executed"] is True
    assert "粉红色鼠标在打开的柜子上" in (tmp_path / "attempt" / "prompt.txt").read_text()
    argv = json.loads((tmp_path / "attempt" / "invocation.json").read_text())["argv"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    process = json.loads((tmp_path / "attempt" / "process.json").read_text())
    assert process["start_ticks"] > 0
    assert process["pgid"] == process["pid"]


@pytest.mark.parametrize(
    "fault", ["unbound_provenance", "skill_success", "incomplete", "changed_executable"]
)
def test_untrusted_model_evidence_is_never_promoted(tmp_path, fault):
    from self_improving.harness.x2env.codex import CodexBackend

    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(text="a cabinet", seed=1, idempotency_key="bad", output_dir=str(tmp_path)),
        store,
    )
    response = {
        "scene": None,
        "unknowns": [
            {
                "field": "dimensions",
                "reason": "unknown",
                "critical": True,
                "provenance": [
                    {
                        "source": "text",
                        "kind": "explicit",
                        "input_sha256": "0" * 64
                        if fault == "unbound_provenance"
                        else bundle.text.sha256,
                    }
                ],
            }
        ],
    }
    if fault == "skill_success":
        response["skill_status"] = "succeeded"
    path = executable(
        tmp_path, response, "turn.started" if fault == "incomplete" else "turn.completed"
    )
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    result = CodexBackend(
        path, "0" * 64 if fault == "changed_executable" else sha, "test-double", store
    ).interpret(bundle, output_root=tmp_path / "attempt", timeout=10)
    assert result.status == "failed"
    assert result.proposal is None
    assert result.error_code is not None
    if fault == "skill_success":
        diagnostics = json.loads((tmp_path / "attempt" / "validation-errors.json").read_text())
        assert diagnostics["errors"][0]["loc"] == ["skill_status"]
        assert diagnostics["errors"][0]["type"] == "extra_forbidden"
        assert all("input" not in item and "ctx" not in item for item in diagnostics["errors"])
        assert any(
            store.read_artifact(ref)
            == (tmp_path / "attempt" / "validation-errors.json").read_bytes()
            for ref in result.evidence
        )


def test_video_advisory_uses_verified_original_frames(tmp_path):
    from self_improving.harness.x2env.codex import CodexBackend
    from self_improving.harness.x2env.contracts import InputMedia

    video = tmp_path / "input.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=32x24:rate=4:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        timeout=30,
    )
    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(
            video=InputMedia(path=str(video)),
            seed=1,
            idempotency_key="video",
            output_dir=str(tmp_path),
        ),
        store,
    )
    response = {
        "scene": None,
        "unknowns": [
            {
                "field": "category",
                "reason": "test pattern",
                "critical": True,
                "provenance": [
                    {
                        "source": "video",
                        "kind": "inferred",
                        "input_sha256": bundle.video.source.sha256,
                        "frame_index": 0,
                    }
                ],
            }
        ],
    }
    path = executable(tmp_path, response)
    result = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    ).interpret(bundle, output_root=tmp_path / "attempt", timeout=10)
    assert result.status == "completed"
    invocation = json.loads((tmp_path / "attempt" / "invocation.json").read_text())
    assert [m["frame_index"] for m in invocation["media"]] == [0, 1, 2, 3]
    sequence = json.loads(store.read_artifact(bundle.video.sequence))
    assert [m["rgb_sha256"] for m in invocation["media"]] == [
        f["sha256"] for f in sequence["frames"]
    ]
    assert all((tmp_path / "attempt" / f"video-{i}.png").is_file() for i in range(4))


@pytest.mark.parametrize("tool", [False, True])
def test_cabinet_mouse_attributes_and_relation_survive_typed_proposal(tmp_path, tool):
    from self_improving.harness.x2env.codex import CodexBackend

    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(
            text="A pink mouse on an open cabinet.",
            seed=1,
            idempotency_key="scene",
            output_dir=str(tmp_path),
        ),
        store,
    )
    origin = {"source": "text", "input_sha256": bundle.text.sha256, "kind": "explicit"}
    fields = ["category", "color", "dimensions", "material", "pose", "articulation_state"]
    entities = [
        {
            "id": category,
            "category": category,
            "color": "pink" if category == "mouse" else None,
            "dimensions": None,
            "material": None,
            "pose": {"frame": "world", "position": [0, 0, 0], "yaw_degrees": 0},
            "articulation_state": {"state": "open"} if category == "cabinet" else None,
            "provenance": {field: [origin] for field in fields},
        }
        for category in ["cabinet", "mouse"]
    ]
    response = {
        "scene": {
            "revision": 0,
            "input_sha256": bundle.request_sha256,
            "entities": entities,
            "relations": [
                {"source": "mouse", "target": "cabinet", "relation": "on", "provenance": [origin]}
            ],
        },
        "unknowns": [],
    }
    path = executable(tmp_path, response, tool=tool)
    result = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    ).interpret(bundle, output_root=tmp_path / "attempt", timeout=10)
    if tool:
        assert result.status == "failed"
        assert result.error_code == "advisory_tool_violation"
    else:
        assert result.status == "completed"
        assert [e.category for e in result.proposal.scene.entities] == ["cabinet", "mouse"]
        assert result.proposal.scene.entities[0].articulation_state.state == "open"
        assert result.proposal.scene.entities[1].color == "pink"
        assert result.proposal.scene.relations[0].relation == "on"


def test_external_model_timeout_is_bounded_and_keeps_logs(tmp_path):
    from self_improving.harness.x2env.codex import CodexBackend

    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(text="a mouse", seed=1, idempotency_key="timeout", output_dir=str(tmp_path)),
        store,
    )
    path = tmp_path / "sleeping-model-double"
    path.write_text(
        f"#!{sys.executable}\nimport time\nprint('started',flush=True)\ntime.sleep(20)\n"
    )
    path.chmod(0o700)
    result = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    ).interpret(bundle, output_root=tmp_path / "attempt", timeout=1)
    assert result.status == "failed"
    assert result.error_code == "model_timeout"
    assert result.elapsed_seconds < 5
    assert any(b"started" in store.read_artifact(ref) for ref in result.evidence)
