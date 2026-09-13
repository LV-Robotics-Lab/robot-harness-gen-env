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


def test_router_secret_reaches_only_child_environment(tmp_path):
    from self_improving.harness.x2env.codex import CodexBackend
    from self_improving.harness.x2env.deployment import PrivateModelRouter

    key = tmp_path / "credential"
    key.write_text("test-only-not-a-real-secret")
    key.chmod(0o600)
    router = PrivateModelRouter(api_key_file=str(key))
    process = tmp_path / "route-double"
    process.write_text(
        f"#!{sys.executable}\nimport os,sys\n"
        "assert os.environ['X2ENV_ROUTER_API_KEY']=='test-only-not-a-real-secret'\n"
        "assert 'test-only-not-a-real-secret' not in str(sys.argv)\n"
        'sys.stdin.read()\nprint(\'{"type":"turn.completed"}\')\n'
    )
    process.chmod(0o700)
    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(text="a table", seed=1, idempotency_key="route", output_dir=str(tmp_path)),
        store,
    )
    result = CodexBackend(
        process,
        hashlib.sha256(process.read_bytes()).hexdigest(),
        "openai/gpt-5.6-terra",
        store,
        router=router,
    ).interpret(bundle, output_root=tmp_path / "attempt", timeout=5)
    assert result.status != "completed"  # Transport double deliberately supplies no proposal.
    terminal = json.loads((tmp_path / "attempt/process-terminal.json").read_text())
    assert terminal["returncode"] == 0
    invocation = (tmp_path / "attempt/invocation.json").read_text()
    assert "test-only-not-a-real-secret" not in invocation
    assert 'model_provider=\\"x2env_router\\"' in invocation
    prompt = (tmp_path / "attempt/prompt.txt").read_text()
    assert "Exact output JSON Schema" in prompt
    assert '"x2env.scene_intent_proposal.v2"' in prompt
    key.chmod(0o644)
    with pytest.raises(ValueError, match="unsafe_model_credential"):
        router.child_environment()


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


@pytest.mark.parametrize("repair_ok", [True, False])
def test_syntax_only_regeneration_is_once_and_preserves_original_bytes(tmp_path, repair_ok):
    from self_improving.harness.x2env.artifacts import artifact_closure
    from self_improving.harness.x2env.codex import CodexBackend

    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(text="a table", seed=1, idempotency_key="format", output_dir=str(tmp_path)),
        store,
    )
    valid = json.dumps(
        {
            "scene": None,
            "unknowns": [
                {
                    "field": "scene",
                    "reason": "Ambiguous requested relation",
                    "critical": True,
                    "provenance": [
                        {"source": "text", "input_sha256": bundle.text.sha256, "kind": "explicit"}
                    ],
                }
            ],
        }
    )
    broken = valid[:-1]
    path = tmp_path / "format-model-double"
    path.write_text(
        f"#!{sys.executable}\nimport sys,pathlib\n"
        "prompt=sys.stdin.read()\n"
        "out=pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])\n"
        f"raw={valid!r} if 'format-retry-1' in str(out) and {repair_ok!r} else {broken!r}\n"
        "out.write_text(raw)\n"
        'print(\'{"type":"turn.completed"}\')\n'
    )
    path.chmod(0o700)
    attempt = tmp_path / "attempt"
    result = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "double", store
    ).interpret(bundle, output_root=attempt, timeout=10)
    assert result.status == ("completed" if repair_ok else "failed")
    assert (attempt / "proposal.json").read_text() == broken
    assert (attempt / "format-retry-1/proposal.json").is_file()
    assert not (attempt / "format-retry-2").exists()
    assert any(
        store.read_artifact(ref) == broken.encode() and ref.media_type == "text/plain"
        for ref in result.evidence
    )
    artifact_closure(store, result.evidence)
    prompt = (attempt / "format-retry-1/prompt.txt").read_text()
    assert "JSON syntax" in prompt and bundle.request_sha256 in prompt and "a table" in prompt
    receipt = json.loads((attempt / "format-regeneration.json").read_bytes())
    assert receipt["attempt_limit"] == 1 and receipt["shared_deadline"] is True
    if repair_ok:
        assert result.proposal.unknowns[0].critical is True


def test_schema_failure_does_not_get_a_syntax_regeneration(tmp_path):
    from self_improving.harness.x2env.codex import CodexBackend

    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(text="a table", seed=1, idempotency_key="schema", output_dir=str(tmp_path)),
        store,
    )
    path = executable(tmp_path, {"scene": None, "unknowns": []})
    result = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "double", store
    ).interpret(bundle, output_root=tmp_path / "attempt", timeout=10)
    assert result.status == "failed" and result.error_code == "invalid_model_evidence"
    assert not (tmp_path / "attempt/format-retry-1").exists()


@pytest.mark.parametrize("category", ["garbage_can", "computer_mouse", "unlisted_object"])
def test_local_naming_snapshot_is_evidence_not_category_rewriting(tmp_path, category):
    from self_improving.harness.x2env.codex import CodexBackend

    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(
            text="a garbage can", seed=1, idempotency_key="vocabulary", output_dir=str(tmp_path)
        ),
        store,
    )
    provenance = [{"source": "text", "input_sha256": bundle.text.sha256, "kind": "explicit"}]
    response = {
        "scene": {
            "input_sha256": bundle.request_sha256,
            "revision": 0,
            "entities": [
                {
                    "id": "object",
                    "category": category,
                    "role": "foreground",
                    "color": None,
                    "material": None,
                    "dimensions": None,
                    "pose": {"frame": "world", "position": [None, None, None], "yaw_degrees": None},
                    "articulation_state": None,
                    "provenance": {
                        key: provenance
                        for key in (
                            "category",
                            "color",
                            "material",
                            "dimensions",
                            "pose",
                            "articulation_state",
                        )
                    },
                }
            ],
            "relations": [],
        },
        "unknowns": [
            {
                "field": "garbage_can.identity",
                "reason": "new category absent from catalog",
                "critical": True,
                "provenance": [
                    {"source": "text", "input_sha256": bundle.text.sha256, "kind": "explicit"}
                ],
            }
        ],
    }
    path = executable(tmp_path, response)
    result = CodexBackend(
        path,
        hashlib.sha256(path.read_bytes()).hexdigest(),
        "double",
        store,
        local_category_vocabulary=("can", "mouse"),
    ).interpret(bundle, output_root=tmp_path / "attempt", timeout=10)
    assert result.status == "completed"
    assert result.proposal.scene.entities[0].category == category
    assert result.proposal.unknowns[0].field == "garbage_can.identity"
    raw = (tmp_path / "attempt" / "local-category-context.json").read_bytes()
    context = json.loads(raw)
    assert context["categories"] == ["can", "mouse"]
    assert context["scope"] == "naming_context_only"
    assert context["snapshot_basis"] == "backend_construction_not_live_catalog"
    assert context["status"] == "available"
    assert raw in [store.read_artifact(ref) for ref in result.evidence]
    prompt = (tmp_path / "attempt" / "prompt.txt").read_text()
    assert '"categories": ["can", "mouse"]' in prompt
    assert "only when it denotes the same semantic concept" in prompt
    assert "New categories remain unrestricted" in prompt


@pytest.mark.parametrize(
    "vocabulary,error",
    [
        (("mouse", "can"), None),
        (("mouse", "mouse"), None),
        (("",), None),
        (("a" * 129,), None),
        (("mouse\n",), None),
        (("/private/catalog",), None),
        (("private\\catalog",), None),
        ((1,), None),
        (("mouse",), "catalog_category_limit"),
        ((), "arbitrary_error"),
        (tuple(f"{i:03}" + "界" * 125 for i in range(128)), None),
    ],
)
def test_invalid_category_context_is_rejected_before_transport(tmp_path, vocabulary, error):
    from self_improving.harness.x2env.codex import CodexBackend

    with pytest.raises(ValueError, match="invalid local category context"):
        CodexBackend(
            tmp_path / "never-executed",
            "a" * 64,
            "double",
            Store(tmp_path),
            local_category_vocabulary=vocabulary,
            local_category_context_error=error,
        )


@pytest.mark.parametrize("exit_code", [2, 17])
def test_maximum_config_or_model_rejection_preserves_logs_without_retry(tmp_path, exit_code):
    from self_improving.harness.x2env.codex import CodexBackend

    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(text="a table", seed=1, idempotency_key="reject", output_dir=str(tmp_path)),
        store,
    )
    path = tmp_path / "rejecting-process-double"
    path.write_text(
        f"#!{sys.executable}\nimport json,pathlib,sys\n"
        "with pathlib.Path('calls.jsonl').open('a') as out:\n"
        " out.write(json.dumps(sys.argv)+'\\n')\n"
        "print('explicit external config/model rejection',file=sys.stderr)\n"
        f"sys.exit({exit_code})\n"
    )
    path.chmod(0o700)
    result = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    ).interpret(bundle, output_root=tmp_path / "attempt", timeout=10)
    assert result.status == "failed" and result.error_code == "model_exit_failure"
    calls = (tmp_path / "attempt" / "calls.jsonl").read_text().splitlines()
    assert len(calls) == 1
    argv = json.loads(calls[0])
    assert [arg for arg in argv if arg.startswith("model_reasoning_effort=")] == [
        'model_reasoning_effort="max"'
    ]
    terminal = json.loads((tmp_path / "attempt" / "process-terminal.json").read_text())
    assert terminal["returncode"] == exit_code and terminal["reaped"] is True
    assert b"explicit external config/model rejection\n" in [
        store.read_artifact(ref) for ref in result.evidence
    ]


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
    assert (
        "Perform intent extraction only; later Harness stages handle grounding, asset "
        "resolution and simulation, so record unresolved values and their truthful unknowns "
        "rather than planning or solving those stages here."
    ) in prompt
    assert (
        "Return compact JSON and keep each provenance note concise, avoiding repetition of "
        "values already present in typed fields while retaining every required provenance "
        "record and any explanation needed for ambiguity, conflict or an explicit override."
    ) in prompt
    manifest = json.loads(store.read_artifact(result.evidence[-1]))
    assert manifest["model"] == "test-double"
    assert manifest["requested_reasoning_effort"] == "max"
    assert manifest["server_effective_effort_verified"] is False
    assert manifest["input_sha256"] == bundle.request_sha256
    assert manifest["external_agent_executed"] is True
    assert "粉红色鼠标在打开的柜子上" in (tmp_path / "attempt" / "prompt.txt").read_text()
    invocation = json.loads((tmp_path / "attempt" / "invocation.json").read_text())
    argv = invocation["argv"]
    assert 'model_reasoning_effort="max"' in argv
    assert argv[argv.index('model_reasoning_effort="max"') - 1] == "-c"
    assert invocation["requested_reasoning_effort"] == "max"
    assert invocation["server_effective_effort_verified"] is False
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    output_schema = json.loads((tmp_path / "attempt" / "proposal.schema.json").read_bytes())
    assert (
        output_schema["$defs"]["SceneIR"]["properties"]["input_sha256"]["const"]
        == bundle.request_sha256
    )
    assert output_schema["$defs"]["FieldProvenance"]["properties"]["input_sha256"]["enum"] == [
        bundle.text.sha256
    ]
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
    schema = json.loads((tmp_path / "attempt" / "proposal.schema.json").read_bytes())
    assert schema["$defs"]["FieldProvenance"]["properties"]["input_sha256"]["enum"] == [
        bundle.video.source.sha256
    ]
    invocation = json.loads((tmp_path / "attempt" / "invocation.json").read_text())
    assert [m["frame_index"] for m in invocation["media"]] == [0, 1, 2, 3]
    sequence = json.loads(store.read_artifact(bundle.video.sequence))
    assert [m["rgb_sha256"] for m in invocation["media"]] == [
        f["sha256"] for f in sequence["frames"]
    ]
    assert all((tmp_path / "attempt" / f"video-{i}.png").is_file() for i in range(4))


@pytest.mark.parametrize("source", ["text", "image", "wrong_pair"])
def test_bound_hash_enum_does_not_replace_source_index_verification(tmp_path, source):
    from PIL import Image

    from self_improving.harness.x2env.codex import CodexBackend
    from self_improving.harness.x2env.contracts import InputMedia

    image = tmp_path / "image.png"
    Image.new("RGB", (4, 3), "red").save(image)
    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(
            text="an object",
            images=(InputMedia(path=str(image)),),
            seed=1,
            idempotency_key="pair",
            output_dir=str(tmp_path),
        ),
        store,
    )
    use_image = source != "text"
    response = {
        "scene": None,
        "unknowns": [
            {
                "field": "dimensions",
                "reason": "unknown",
                "critical": True,
                "provenance": [
                    {
                        "source": "image" if use_image else "text",
                        "kind": "inferred",
                        "input_sha256": bundle.text.sha256
                        if source != "image"
                        else bundle.images[0].source.sha256,
                        "media_index": 0 if use_image else None,
                    }
                ],
            }
        ],
    }
    path = executable(tmp_path, response)
    result = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "double", store
    ).interpret(bundle, output_root=tmp_path / "attempt", timeout=10)
    assert result.status == ("failed" if source == "wrong_pair" else "completed")
    if source == "wrong_pair":
        assert result.error_code == "proposal_input_mismatch"


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


@pytest.mark.parametrize("hang", [False, True])
def test_revoked_auth_is_structured_blocker_without_model_retry(tmp_path, hang):
    from self_improving.harness.x2env.codex import CodexBackend

    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(text="a table", seed=1, idempotency_key="revoked", output_dir=str(tmp_path)),
        store,
    )
    path = tmp_path / "revoked-auth-process-double"
    path.write_text(
        f"#!{sys.executable}\nimport sys,time\n"
        "print('ERROR failed to refresh available models: "
        "401 Unauthorized; auth error code: token_revoked', file=sys.stderr,flush=True)\n"
        + ("time.sleep(20)\n" if hang else "sys.exit(1)\n")
    )
    path.chmod(0o700)
    result = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    ).interpret(bundle, output_root=tmp_path / "attempt", timeout=1)
    assert result.status == "blocked"
    assert result.error_code == "model_authentication_required"
    terminal = json.loads((tmp_path / "attempt/process-terminal.json").read_bytes())
    assert terminal["reaped"] is True
    assert terminal["failure"] == ("model_timeout" if hang else None)
    diagnostic = json.loads((tmp_path / "attempt/transport-diagnostic.json").read_bytes())
    assert diagnostic["required_resources"] == ["managed_codex_authentication"]
    assert diagnostic["retry_performed"] is False


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
