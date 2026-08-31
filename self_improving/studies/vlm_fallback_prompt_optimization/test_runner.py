from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from scene_gen.rendered_critic import build_critic_prompt
from scene_gen.schema import ResolvedSceneSpec
from self_improving.studies.vlm_fallback_prompt_optimization import protocol, runner

STUDY_ROOT = Path(__file__).resolve().parent
REPO_ROOT = STUDY_ROOT.parents[2]
A0_RESOLVED_PATH = (
    REPO_ROOT
    / "self_improving/asset_pipeline/active/work/probe_20260818_dustbin_render/out/scenes"
    / "place_a_dustbin_on_the_table_f70e98a43e/resolved_scene.json"
)
A0_RESOLVED = ResolvedSceneSpec.model_validate_json(A0_RESOLVED_PATH.read_text(encoding="utf-8"))
A0_PROMPT = build_critic_prompt(A0_RESOLVED)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_source_manifest(path: Path) -> str:
    entries = [
        {
            "path": "self_improving/studies/vlm_fallback_prompt_optimization/runner.py",
            "sha256": _sha256(STUDY_ROOT / "runner.py"),
        },
        {
            "path": "self_improving/studies/vlm_fallback_prompt_optimization/protocol.py",
            "sha256": _sha256(STUDY_ROOT / "protocol.py"),
        },
        {
            "path": "scene_gen/rendered_critic.py",
            "sha256": _sha256(REPO_ROOT / "scene_gen/rendered_critic.py"),
        },
        {
            "path": "scene_gen/schema.py",
            "sha256": _sha256(REPO_ROOT / "scene_gen/schema.py"),
        },
    ]
    value = {
        "schema_version": "vlm_fallback.runner_source_manifest.v1",
        "study_id": protocol.STUDY_ID,
        "entries": entries,
    }
    path.write_bytes(protocol.canonical_json_bytes(value) + b"\n")
    return _sha256(path)


def _config(tmp_path: Path) -> runner.RunnerConfig:
    tmp_path.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "run_log.jsonl"
    log_path.write_bytes((STUDY_ROOT / "run_log.jsonl").read_bytes())
    source_manifest = tmp_path / "runner_source_manifest.json"
    source_manifest_sha256 = _write_source_manifest(source_manifest)
    return runner.RunnerConfig(
        spec_path=STUDY_ROOT / "experiment_spec.json",
        repo_root=REPO_ROOT,
        log_path=log_path,
        anchor_path=tmp_path / "run_state.json",
        pending_path=tmp_path / "run_pending.json",
        lock_path=tmp_path / "run.lock",
        source_manifest_path=source_manifest,
        expected_source_manifest_sha256=source_manifest_sha256,
        # The shared worktree intentionally lacks unrelated frozen evidence
        # files. Individual execution tests still rebind their selected case.
        verify_artifacts=False,
        allow_test_providers=True,
        execution_mode="test",
        routing_provider_mode="in_process_test",
    )


def _a0_invocation(**changes) -> runner.VisibleInvocation:
    invocation = runner.VisibleInvocation(
        case_id="asset_probe_dustbin_legacy_900",
        arm="A0_current_critic_3b",
        prompt=A0_PROMPT,
        prompt_template_version="scene_gen.rendered_critic.build_critic_prompt.v1",
        processor_config={},
        seed=20260831,
        attempt=1,
        resolved_scene_sha256=A0_RESOLVED.digest(),
        resolved_scene_path=A0_RESOLVED_PATH,
    )
    return replace(invocation, **changes)


def _rehash_event(event: dict, *, previous: str | None = None) -> dict:
    value = json.loads(json.dumps(event))
    if previous is not None:
        value["previous_event_sha256"] = previous
    value.pop("event_sha256", None)
    value["event_sha256"] = protocol.canonical_sha256(value)
    return value


def _write_event_lines(config: runner.RunnerConfig, events: list[dict]) -> None:
    prefix = (STUDY_ROOT / "run_log.jsonl").read_bytes()
    config.log_path.write_bytes(
        prefix + b"".join(protocol.canonical_json_bytes(event) + b"\n" for event in events)
    )


def _pending_fixture(tmp_path: Path) -> tuple[runner.RunnerConfig, bytes, dict, dict, dict]:
    config = _config(tmp_path)
    session = runner.ExperimentRunner.open(config, visible_provider=_VisibleProvider())
    invocation = _a0_invocation(seed=13)
    event = dict(session.execute(invocation).event)
    completed_payload = config.log_path.read_bytes()
    prefix = b"".join(completed_payload.splitlines(keepends=True)[:-1])
    old_anchor, _ = session._journal._observed_anchor(prefix)
    new_anchor = json.loads(config.anchor_path.read_text(encoding="utf-8"))
    pending = {
        "schema_version": "vlm_fallback.pending_append.v1",
        "old_log_sha256": hashlib.sha256(prefix).hexdigest(),
        "old_log_size_bytes": len(prefix),
        "event": event,
    }
    config.log_path.write_bytes(prefix)
    config.anchor_path.write_bytes(protocol.canonical_json_bytes(old_anchor) + b"\n")
    config.pending_path.write_bytes(protocol.canonical_json_bytes(pending) + b"\n")
    return config, prefix, old_anchor, new_anchor, pending


def _config_with_spec(tmp_path: Path, mutate) -> runner.RunnerConfig:
    config = _config(tmp_path)
    spec = json.loads((STUDY_ROOT / "experiment_spec.json").read_text(encoding="utf-8"))
    mutate(spec)
    spec_path = tmp_path / "custom_experiment_spec.json"
    spec_path.write_bytes(protocol.canonical_json_bytes(spec) + b"\n")
    return replace(config, spec_path=spec_path)


def _config_with_test_gold_commitment(tmp_path: Path) -> runner.RunnerConfig:
    return _config_with_spec(
        tmp_path,
        lambda spec: spec["experiments"][0]["gold_annotation"].update(
            sealed_test_annotation_manifest_sha256="a" * 64
        ),
    )


def _proven_prompt_freeze(
    tmp_path: Path,
    session: runner.ExperimentRunner,
    *,
    prompt: str,
    prompt_template_version: str,
    processor_config: dict,
    attempt: int = 1,
) -> runner.VisiblePromptFreeze:
    experiment = session.spec["experiments"][0]
    event_ids: list[str] = []
    case_ids: list[str] = []
    for index, sample in enumerate(experiment["samples"], start=1):
        if sample["split"] != "dev":
            continue
        receipt = session.execute(
            runner.VisibleInvocation(
                case_id=sample["case_id"],
                arm="A1_typed_abstaining_critic_3b",
                prompt=prompt,
                prompt_template_version=prompt_template_version,
                processor_config=processor_config,
                seed=1000 + index,
                attempt=attempt,
            )
        )
        event_ids.append(str(receipt.event["event_id"]))
        case_ids.append(str(sample["case_id"]))
    gold_seal = {
        "schema_version": runner.VISIBLE_GOLD_SEAL_SCHEMA,
        "study_id": session.identity.study_id,
        "spec_sha256": session.identity.spec_sha256,
        "state": "sealed",
        "test_gold_opened": False,
        "annotation_manifest_sha256": "a" * 64,
    }
    gold_seal_path = tmp_path / f"gold_seal_{attempt}.json"
    gold_seal_path.write_bytes(protocol.canonical_json_bytes(gold_seal) + b"\n")
    gold_seal_sha = _sha256(gold_seal_path)
    selection = {
        "schema_version": runner.VISIBLE_SELECTION_SCHEMA,
        "study_id": session.identity.study_id,
        "spec_sha256": session.identity.spec_sha256,
        "selected_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_template_version": prompt_template_version,
        "processor_config_sha256": protocol.canonical_sha256(processor_config),
        "selection_order": list(
            next(
                arm["selection_order"]
                for arm in experiment["arms"]
                if arm["arm"] == "A1_typed_abstaining_critic_3b"
            )
        ),
        "dev_event_ids": event_ids,
        "dev_case_ids": case_ids,
        "gold_seal_sha256": gold_seal_sha,
        "test_gold_opened": False,
    }
    selection_path = tmp_path / f"selection_{attempt}.json"
    selection_path.write_bytes(protocol.canonical_json_bytes(selection) + b"\n")
    return runner.VisiblePromptFreeze(
        prompt=prompt,
        prompt_template_version=prompt_template_version,
        processor_config=processor_config,
        selection_evidence_sha256=_sha256(selection_path),
        selection_evidence_path=selection_path,
        gold_seal_path=gold_seal_path,
        gold_seal_sha256=gold_seal_sha,
    )


class _VisibleProvider:
    identity = runner.ProviderIdentity(
        provider_id="test-visible-provider",
        revision="test-revision-1",
        implementation_sha256="1" * 64,
        kind="test",
        production_eligible=False,
    )

    def __init__(self) -> None:
        self.requests: list[runner.VisibleProviderRequest] = []

    def invoke(self, request, progress):
        self.requests.append(request)
        progress("images_encoded")
        if request.arm == "A0_current_critic_3b":
            result = {
                "schema_version": "robotwin.rendered_scene_critic.v1",
                "status": "pass",
                "checks": [
                    {"name": name, "status": "pass", "evidence": "visible"}
                    for name in (
                        "object_presence",
                        "support_relation",
                        "penetration_or_floating",
                        "articulation_state",
                        "overall_prompt_match",
                    )
                ],
            }
        else:
            result = {
                "schema_version": "vlm_fallback.visible_review.v1",
                "checks": {
                    name: "pass"
                    for name in (
                        "object_presence",
                        "object_identity",
                        "object_count",
                        "orientation",
                        "table_contact",
                        "visible_penetration_or_floating",
                        "spatial_relation",
                        "overall_prompt_match",
                    )
                },
                "overall": "pass",
                "corrections": [],
            }
        return runner.ProviderOutcome(
            decision="visible_review_complete",
            result=result,
            raw_response=protocol.canonical_json_bytes(result),
            parsed_response=result,
            abstained=False,
            resource=runner.ResourceUsage(input_tokens=27, output_tokens=4, peak_vram_mib=32),
        )


class _RoutingProvider:
    identity = runner.ProviderIdentity(
        provider_id="test-typed-router",
        revision="router-test-1",
        implementation_sha256="2" * 64,
        kind="test",
        production_eligible=False,
    )

    def __init__(self) -> None:
        self.requests: list[runner.RoutingProviderRequest] = []

    def invoke(self, request, progress):
        self.requests.append(request)
        progress("route_selected")
        intent_sha = hashlib.sha256(request.original_prompt.encode("utf-8")).hexdigest()
        result = {
            "route": "abstain_unsupported_task_api",
            "revised_prompt": None,
            "intent_sha256_before": intent_sha,
            "intent_sha256_after": intent_sha,
        }
        return runner.ProviderOutcome(
            decision="typed_route_complete",
            result=result,
            raw_response=protocol.canonical_json_bytes(result),
            parsed_response=result,
            abstained=True,
            resource=runner.ResourceUsage(input_tokens=18, output_tokens=3),
        )


def test_open_validates_frozen_inputs_and_anchors_the_existing_log(tmp_path: Path) -> None:
    config = _config(tmp_path)
    progress: list[runner.ProgressUpdate] = []

    session = runner.ExperimentRunner.open(config, progress=progress.append)

    assert session.identity.study_id == protocol.STUDY_ID
    assert session.identity.verified_artifact_count == 0
    assert session.identity.model_identity_matches == 2
    assert progress[-1].stage == "runner.validated"
    anchor = json.loads(config.anchor_path.read_text(encoding="utf-8"))
    assert anchor["line_count"] == 6
    assert anchor["log_sha256"] == _sha256(config.log_path)
    assert anchor["genesis_blob_sha256"] == runner.FROZEN_LOG_PREFIX_SHA256
    assert anchor["genesis_line_count"] == runner.FROZEN_LOG_PREFIX_LINES
    assert anchor["execution_mode"] == "test"
    assert anchor["verification_profile_sha256"] == session.identity.verification_profile_sha256


def test_production_mode_cannot_disable_integrity_verification(tmp_path: Path) -> None:
    base = _config(tmp_path)
    attacks = (
        replace(
            base,
            execution_mode="production",
            verify_artifacts=False,
            verify_models=True,
            allow_test_providers=False,
            routing_provider_mode="sandboxed_subprocess",
        ),
        replace(
            base,
            execution_mode="production",
            verify_artifacts=True,
            verify_models=False,
            allow_test_providers=False,
            routing_provider_mode="sandboxed_subprocess",
        ),
        replace(
            base,
            execution_mode="production",
            verify_artifacts=True,
            verify_models=True,
            allow_test_providers=True,
            routing_provider_mode="sandboxed_subprocess",
        ),
    )

    for config in attacks:
        with pytest.raises(runner.RunnerIntegrityError, match="production mode requires"):
            runner.ExperimentRunner.open(config)


def test_isolated_routing_profile_rejects_an_in_process_provider(tmp_path: Path) -> None:
    provider = _RoutingProvider()
    config = replace(_config(tmp_path), routing_provider_mode="sandboxed_subprocess")
    session = runner.ExperimentRunner.open(config, routing_provider=provider)

    with pytest.raises(runner.RunnerIntegrityError, match="SandboxedRoutingProvider"):
        session.execute(_routing_invocation())
    assert provider.requests == []


def test_sandboxed_provider_profile_cannot_mount_repository_gold(tmp_path: Path) -> None:
    identity = runner.ProviderIdentity(
        provider_id="isolated-router",
        revision="v1",
        implementation_sha256="a" * 64,
        kind="sandboxed_subprocess",
        production_eligible=True,
    )
    in_repo_executable = runner.SandboxedRoutingProvider(
        identity=identity,
        executable=STUDY_ROOT / "runner.py",
    )
    with pytest.raises(runner.RunnerIntegrityError, match="cannot expose the repository"):
        in_repo_executable.isolation_profile(REPO_ROOT)

    repo_as_input = runner.SandboxedRoutingProvider(
        identity=identity,
        executable=Path("/usr/bin/python3"),
        readable_paths=(REPO_ROOT,),
    )
    with pytest.raises(runner.RunnerIntegrityError, match="cannot expose the repository"):
        repo_as_input.isolation_profile(REPO_ROOT)


def test_sandboxed_provider_kills_output_flood_before_child_completion(tmp_path: Path) -> None:
    completed_marker = tmp_path / "provider-completed"
    launcher = tmp_path / "fake-bwrap"
    launcher.write_text(
        f"#!/bin/sh\nhead -c 2097152 /dev/zero\nprintf done > {completed_marker}\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    provider = runner.SandboxedRoutingProvider(
        identity=runner.ProviderIdentity(
            provider_id="bounded-router",
            revision="v1",
            implementation_sha256="b" * 64,
            kind="sandboxed_subprocess",
            production_eligible=True,
        ),
        executable=Path("/usr/bin/true"),
        bubblewrap_path=launcher,
        timeout_seconds=10,
        output_limit_bytes=1024,
    )
    request = runner.RoutingProviderRequest(
        invocation_id="c" * 64,
        case_id="case",
        arm="B2_typed_route_and_prompt_repair",
        original_prompt="put the can on the plate",
        routing_instruction="choose one typed route",
        prompt_template_version="typed-route-v1",
        failure_code="missing_task_api",
        untyped_failure_summary=None,
        typed_failure={"code": "missing_task_api"},
        trusted_state={},
        asset_availability={},
        visible_report=None,
        seed=1,
        attempt=1,
        allowed_routes=("abstain_unsupported_task_api",),
        gold_route=None,
        resource_reservation=runner.ResourceUsage(),
    )

    with pytest.raises(runner.ProviderContractError, match="output exceeds"):
        provider.invoke(request, lambda stage: None)

    assert not completed_marker.exists()


def test_checked_in_runner_source_manifest_is_canonical_and_complete(tmp_path: Path) -> None:
    manifest_path = STUDY_ROOT / "runner_source_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_path.read_bytes() == protocol.canonical_json_bytes(manifest) + b"\n"
    config = replace(
        _config(tmp_path),
        source_manifest_path=manifest_path,
        expected_source_manifest_sha256=_sha256(manifest_path),
    )

    session = runner.ExperimentRunner.open(config)

    assert session.identity.source_manifest_sha256 == _sha256(manifest_path)


def test_visible_call_writes_hash_bound_receipt_and_exact_resume_is_idempotent(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    provider = _VisibleProvider()
    progress: list[runner.ProgressUpdate] = []
    session = runner.ExperimentRunner.open(
        config,
        visible_provider=provider,
        progress=progress.append,
    )
    invocation = runner.VisibleInvocation(
        case_id="asset_probe_dustbin_legacy_900",
        arm="A1_typed_abstaining_critic_3b",
        prompt="Inspect only visible semantics and abstain when uncertain.",
        prompt_template_version="typed-visible-v1",
        processor_config={"min_pixels": 200704, "max_pixels": 802816},
        seed=20260831,
        attempt=1,
    )

    first = session.execute(invocation)
    resumed = session.execute(invocation)

    assert len(provider.requests) == 1
    assert len(provider.requests[0].image_paths) == 6
    assert provider.requests[0].model.revision == "66285546d2b821cf421d4f5eb2576359d3770cd3"
    assert first.resumed is False
    assert resumed.resumed is True
    assert resumed.event == first.event
    assert (
        first.event["model_receipt"]["prompt_sha256"]
        == hashlib.sha256(invocation.prompt.encode("utf-8")).hexdigest()
    )
    assert first.event["model_receipt"]["model_revision"] == provider.requests[0].model.revision
    assert first.event["model_receipt"]["input_tokens"] == 27
    assert first.event["outputs"]["abstain"] is False
    assert first.event["gate_results"]["render_used_as_physics_evidence"] is False
    reservation = next(
        event for event in session._journal.events if event["decision"] == "provider_call_reserved"
    )
    assert reservation["previous_event_sha256"] == runner.FROZEN_LOG_PREFIX_SHA256
    assert first.event["previous_event_sha256"] == reservation["event_sha256"]
    assert progress[-1].stage == "invocation.resumed"
    assert [item.stage for item in progress].count("provider.images_encoded") == 1
    anchor = json.loads(config.anchor_path.read_text(encoding="utf-8"))
    assert anchor["line_count"] == 8
    assert anchor["last_event_sha256"] == first.event["event_sha256"]


def test_stale_runner_refreshes_before_provider_boundary_and_does_not_duplicate(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    first_provider = _VisibleProvider()
    stale_provider = _VisibleProvider()
    first_session = runner.ExperimentRunner.open(config, visible_provider=first_provider)
    stale_session = runner.ExperimentRunner.open(config, visible_provider=stale_provider)
    invocation = _a0_invocation()

    committed = first_session.execute(invocation)
    resumed = stale_session.execute(invocation)

    assert committed.resumed is False
    assert resumed.resumed is True
    assert resumed.event == committed.event
    assert len(first_provider.requests) == 1
    assert stale_provider.requests == []
    assert json.loads(config.anchor_path.read_text(encoding="utf-8"))["line_count"] == 8


def test_typed_route_call_hides_gold_and_binds_route_result(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provider = _RoutingProvider()
    session = runner.ExperimentRunner.open(config, routing_provider=provider)
    invocation = runner.RoutingInvocation(
        case_id="selection2env_drawer_mug_task_api_blocker",
        arm="B2_typed_route_and_prompt_repair",
        original_prompt="Open the drawer and place the mug inside.",
        routing_instruction="Choose one typed route without changing intent.",
        prompt_template_version="typed-route-v1",
        typed_failure={"code": "missing_task_api", "recoverable": False},
        trusted_state={"task_api_available": False},
        asset_availability={"mug": "available", "drawer": "available"},
        visible_report=None,
        seed=20260831,
        attempt=1,
    )

    receipt = session.execute(invocation)

    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.gold_route is None
    assert "gold_route" not in request.typed_failure
    assert receipt.event["outputs"]["result"]["route"] == "abstain_unsupported_task_api"
    assert receipt.event["outputs"]["abstain"] is True
    assert receipt.event["model_receipt"]["model_id"] == "test-typed-router"
    assert receipt.event["model_receipt"]["input_image_sha256"] is None
    assert receipt.event["gate_results"]["gold_route_exposed"] is False
    assert receipt.event["input_bindings"]["typed_failure_sha256"] == protocol.canonical_sha256(
        invocation.typed_failure
    )


def test_routing_provider_preserves_an_explicit_empty_visible_report(tmp_path: Path) -> None:
    provider = _RoutingProvider()
    session = runner.ExperimentRunner.open(_config(tmp_path), routing_provider=provider)

    session.execute(_routing_invocation(visible_report={}))

    assert provider.requests[0].visible_report == {}


def test_routing_arm_context_projection_prevents_treatment_leakage(tmp_path: Path) -> None:
    deployable_providers: list[_RoutingProvider] = []
    oracle_providers: list[_RoutingProvider] = []

    def deployable_factory(arm: str) -> _RoutingProvider:
        provider = _RoutingProvider()
        provider.identity = replace(provider.identity, provider_id=f"deployable-{arm}")
        deployable_providers.append(provider)
        return provider

    def oracle_factory(arm: str) -> _RoutingProvider:
        provider = _RoutingProvider()
        provider.identity = replace(provider.identity, provider_id=f"oracle-{arm}")
        oracle_providers.append(provider)
        return provider

    session = runner.ExperimentRunner.open(
        _config(tmp_path),
        routing_provider_factory=deployable_factory,
        oracle_routing_provider_factory=oracle_factory,
        oracle_capability=runner.OracleRoutingCapability(
            study_id=protocol.STUDY_ID,
            spec_sha256=runner.FROZEN_SPEC_SHA256,
            purpose="descriptive_ceiling_only",
        ),
    )
    hidden_typed = {"code": "missing_task_api", "typed_secret": "do-not-leak"}
    shared = _routing_invocation(
        typed_failure=hidden_typed,
        trusted_state={"trusted_secret": True},
        asset_availability={"asset_secret": "available"},
        visible_report={"visible_secret": "failed"},
        untyped_failure_summary="The previous attempt did not complete.",
    )

    session.execute(replace(shared, arm="B0_retry_unchanged"))
    session.execute(replace(shared, arm="B1_generic_prompt_repair"))
    session.execute(replace(shared, arm="B2_typed_route_and_prompt_repair"))
    session.execute(replace(shared, arm="B3_oracle_route_ceiling"))

    b0, b1, b2 = [provider.requests[0] for provider in deployable_providers]
    b3 = oracle_providers[0].requests[0]
    assert b0.failure_code == "missing_task_api"
    assert b0.untyped_failure_summary is None
    assert b0.typed_failure is None
    assert b0.trusted_state == {}
    assert b0.asset_availability == {}
    assert b0.visible_report is None
    assert b0.gold_route is None
    assert b1.failure_code is None
    assert b1.untyped_failure_summary == "The previous attempt did not complete."
    assert b1.typed_failure is None
    assert b1.trusted_state == {}
    assert b1.asset_availability == {}
    assert b1.visible_report is None
    assert b1.gold_route is None
    assert b2.failure_code is None
    assert b2.untyped_failure_summary is None
    assert b2.typed_failure == hidden_typed
    assert b2.trusted_state == {"trusted_secret": True}
    assert b2.asset_availability == {"asset_secret": "available"}
    assert b2.visible_report == {"visible_secret": "failed"}
    assert b2.gold_route is None
    assert b3.typed_failure == hidden_typed
    assert b3.gold_route == "abstain_unsupported_task_api"


def test_provider_request_mutation_cannot_change_bound_visible_inputs(tmp_path: Path) -> None:
    class MutatingProvider(_VisibleProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            request.processor_config["nested"]["limit"] = 99
            return super().invoke(request, progress)

    provider = MutatingProvider()
    processor_config = {"nested": {"limit": 1}}
    session = runner.ExperimentRunner.open(_config(tmp_path), visible_provider=provider)
    invocation = _a0_invocation(processor_config=processor_config, seed=3)

    receipt = session.execute(invocation)

    assert processor_config == {"nested": {"limit": 1}}
    assert receipt.event["model_receipt"]["processor_config_sha256"] == (
        protocol.canonical_sha256(processor_config)
    )


def test_provider_request_mutation_cannot_change_bound_routing_inputs(tmp_path: Path) -> None:
    class MutatingRouter(_RoutingProvider):
        def invoke(self, request, progress):
            request.typed_failure["nested"]["code"] = "mutated"
            return super().invoke(request, progress)

    provider = MutatingRouter()
    typed_failure = {"nested": {"code": "frozen"}}
    session = runner.ExperimentRunner.open(_config(tmp_path), routing_provider=provider)
    invocation = _routing_invocation(typed_failure=typed_failure)

    receipt = session.execute(invocation)

    assert typed_failure == {"nested": {"code": "frozen"}}
    assert receipt.event["input_bindings"]["typed_failure_sha256"] == (
        protocol.canonical_sha256(typed_failure)
    )


def test_provider_failure_is_terminal_receipted_sanitized_and_resumable(tmp_path: Path) -> None:
    class FailingProvider(_VisibleProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def invoke(self, request, progress):
            self.calls += 1
            progress("model_load_failed")
            raise RuntimeError("secret-token=must-not-enter-log")

    config = _config(tmp_path)
    provider = FailingProvider()
    session = runner.ExperimentRunner.open(config, visible_provider=provider)
    invocation = _a0_invocation(seed=7)

    failed = session.execute(invocation)
    resumed = session.execute(invocation)

    assert provider.calls == 1
    assert failed.event["decision"] == "provider_call_failed"
    assert failed.event["outputs"]["abstain"] is True
    assert failed.event["error"]["code"] == "provider_exception"
    assert failed.event["error"]["exception_type"] == "RuntimeError"
    assert "message_sha256" in failed.event["error"]
    assert "secret-token" not in config.log_path.read_text(encoding="utf-8")
    assert resumed.resumed is True
    assert resumed.event == failed.event


def test_open_rejects_replacement_spec_and_source_drift(tmp_path: Path) -> None:
    spec_config = _config(tmp_path / "spec")
    drifted_spec = tmp_path / "spec-drift.json"
    drifted_spec.write_bytes((STUDY_ROOT / "experiment_spec.json").read_bytes() + b" ")
    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(replace(spec_config, spec_path=drifted_spec))

    source_config = _config(tmp_path / "source")
    source_manifest = json.loads(source_config.source_manifest_path.read_text(encoding="utf-8"))
    source_manifest["entries"][0]["sha256"] = "0" * 64
    source_config.source_manifest_path.write_bytes(
        protocol.canonical_json_bytes(source_manifest) + b"\n"
    )
    with pytest.raises(runner.RunnerIntegrityError, match="source digest mismatch"):
        runner.ExperimentRunner.open(
            replace(
                source_config,
                expected_source_manifest_sha256=_sha256(source_config.source_manifest_path),
            )
        )


@pytest.mark.parametrize(
    ("payload", "match"),
    [(b"{\n", "not valid JSON"), (b"[]\n", "JSON object")],
)
def test_open_wraps_unreadable_and_malformed_spec_as_integrity_errors(
    tmp_path: Path,
    payload: bytes,
    match: str,
) -> None:
    missing_config = _config(tmp_path / "missing")
    missing_spec = tmp_path / "missing" / "absent.json"
    with pytest.raises(runner.RunnerIntegrityError, match="cannot be read"):
        runner.ExperimentRunner.open(replace(missing_config, spec_path=missing_spec))

    malformed_config = _config(tmp_path / "malformed")
    malformed_spec = tmp_path / "malformed" / "spec.json"
    malformed_spec.write_bytes(payload)
    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(replace(malformed_config, spec_path=malformed_spec))


def test_log_truncation_tamper_and_anchor_deletion_fail_closed(tmp_path: Path) -> None:
    invocation = runner.VisibleInvocation(
        case_id="asset_probe_dustbin_legacy_900",
        arm="A1_typed_abstaining_critic_3b",
        prompt="Visible prompt",
        prompt_template_version="typed-v1",
        processor_config={},
        seed=1,
        attempt=1,
    )

    truncated = _config(tmp_path / "truncated")
    runner.ExperimentRunner.open(truncated, visible_provider=_VisibleProvider()).execute(invocation)
    truncated.log_path.write_bytes(truncated.log_path.read_bytes()[:-1])
    with pytest.raises(runner.RunnerIntegrityError, match="truncated"):
        runner.ExperimentRunner.open(truncated)

    tampered = _config(tmp_path / "tampered")
    runner.ExperimentRunner.open(tampered, visible_provider=_VisibleProvider()).execute(invocation)
    payload = tampered.log_path.read_bytes()
    tampered.log_path.write_bytes(
        payload.replace(b"visible_review_complete", b"visible_review_tampered")
    )
    with pytest.raises(runner.RunnerIntegrityError, match="digest mismatch|anchor mismatch"):
        runner.ExperimentRunner.open(tampered)

    anchor_deleted = _config(tmp_path / "anchor-deleted")
    runner.ExperimentRunner.open(
        anchor_deleted,
        visible_provider=_VisibleProvider(),
    ).execute(invocation)
    anchor_deleted.anchor_path.unlink()
    with pytest.raises(runner.RunnerIntegrityError, match="anchor is missing"):
        runner.ExperimentRunner.open(anchor_deleted)


def test_pending_receipt_is_recovered_without_reinvoking_provider(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first_provider = _VisibleProvider()
    first_session = runner.ExperimentRunner.open(config, visible_provider=first_provider)
    invocation = runner.VisibleInvocation(
        case_id="asset_probe_dustbin_legacy_900",
        arm="A1_typed_abstaining_critic_3b",
        prompt="Recover this exact receipt.",
        prompt_template_version="typed-v1",
        processor_config={},
        seed=2,
        attempt=1,
    )
    completed = first_session.execute(invocation)
    completed_payload = config.log_path.read_bytes()
    prefix = b"".join(completed_payload.splitlines(keepends=True)[:-1])
    old_anchor, _ = first_session._journal._observed_anchor(prefix)
    pending = {
        "schema_version": "vlm_fallback.pending_append.v1",
        "old_log_sha256": hashlib.sha256(prefix).hexdigest(),
        "old_log_size_bytes": len(prefix),
        "event": completed.event,
    }
    config.log_path.write_bytes(prefix)
    config.anchor_path.write_bytes(protocol.canonical_json_bytes(old_anchor) + b"\n")
    config.pending_path.write_bytes(protocol.canonical_json_bytes(pending) + b"\n")
    resumed_provider = _VisibleProvider()

    resumed_session = runner.ExperimentRunner.open(config, visible_provider=resumed_provider)
    resumed = resumed_session.execute(invocation)

    assert resumed.resumed is True
    assert resumed.event == completed.event
    assert resumed_provider.requests == []
    assert not config.pending_path.exists()
    assert json.loads(config.anchor_path.read_text(encoding="utf-8"))["line_count"] == 8


def test_budget_violation_is_receipted_and_stops_future_execution(tmp_path: Path) -> None:
    class OverBudgetProvider(_VisibleProvider):
        def invoke(self, request, progress):
            outcome = super().invoke(request, progress)
            return replace(
                outcome,
                decision="oversized_generation",
                raw_response="too many tokens",
                resource=runner.ResourceUsage(output_tokens=769),
            )

    config = _config(tmp_path)
    provider = OverBudgetProvider()
    session = runner.ExperimentRunner.open(config, visible_provider=provider)
    invocation = runner.VisibleInvocation(
        case_id="asset_probe_dustbin_legacy_900",
        arm="A1_typed_abstaining_critic_3b",
        prompt="Budget-bound prompt.",
        prompt_template_version="typed-v1",
        processor_config={},
        seed=3,
        attempt=1,
    )

    with pytest.raises(runner.StudyStoppedError, match="max_new_tokens"):
        session.execute(invocation)

    assert len(provider.requests) == 1
    events = [json.loads(line) for line in config.log_path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["decision"] == "provider_call_completed"
    assert events[-1]["gate_results"]["resource_budget"] == "fail"
    assert events[-1]["gate_results"]["study_stopped"] is True
    assert json.loads(config.anchor_path.read_text(encoding="utf-8"))["stopped"] is True
    with pytest.raises(runner.StudyStoppedError, match="max_new_tokens"):
        session.execute(replace(invocation, attempt=2))
    assert len(provider.requests) == 1


def test_fourth_train_prompt_revision_stops_before_provider_call(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provider = _VisibleProvider()
    session = runner.ExperimentRunner.open(config, visible_provider=provider)
    base = runner.VisibleInvocation(
        case_id="asset_probe_dustbin_legacy_900",
        arm="A1_typed_abstaining_critic_3b",
        prompt="revision-1",
        prompt_template_version="typed-v1",
        processor_config={},
        seed=4,
        attempt=1,
    )
    for attempt in range(1, 4):
        session.execute(replace(base, prompt=f"revision-{attempt}", attempt=attempt))

    with pytest.raises(runner.StudyStoppedError, match="max_train_prompt_revisions"):
        session.execute(replace(base, prompt="revision-4", attempt=4))

    assert len(provider.requests) == 3
    assert json.loads(config.anchor_path.read_text(encoding="utf-8"))["stopped"] is True


def test_routing_provider_failure_is_terminal_and_cannot_be_overwritten(tmp_path: Path) -> None:
    class FailingRouter(_RoutingProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def invoke(self, request, progress):
            self.calls += 1
            raise TimeoutError("private failure detail")

    config = _config(tmp_path)
    providers: list[FailingRouter] = []

    def provider_factory(arm: str) -> FailingRouter:
        provider = FailingRouter()
        providers.append(provider)
        return provider

    session = runner.ExperimentRunner.open(
        config,
        routing_provider_factory=provider_factory,
    )
    invocation = runner.RoutingInvocation(
        case_id="selection2env_drawer_mug_task_api_blocker",
        arm="B2_typed_route_and_prompt_repair",
        original_prompt="Open the drawer and place the mug inside.",
        routing_instruction="Route typed failure.",
        prompt_template_version="typed-route-v1",
        typed_failure={"code": "missing_task_api"},
        trusted_state={},
        asset_availability={},
        visible_report=None,
        seed=5,
        attempt=1,
    )

    failed = session.execute(invocation)
    resumed = session.execute(invocation)

    assert sum(provider.calls for provider in providers) == 1
    assert failed.event["decision"] == "provider_call_failed"
    assert failed.event["error"]["exception_type"] == "TimeoutError"
    assert resumed.resumed is True
    with pytest.raises(runner.DuplicateInvocationError):
        session.execute(replace(invocation, routing_instruction="changed under same logical key"))
    assert sum(provider.calls for provider in providers) == 1

    explicit_retry = session.execute(replace(invocation, attempt=2))

    assert explicit_retry.event["decision"] == "provider_call_failed"
    assert explicit_retry.invocation_id != failed.invocation_id
    assert sum(provider.calls for provider in providers) == 2


def test_routing_resource_violation_stops_after_preserving_call_receipt(tmp_path: Path) -> None:
    class RewriteOverflowRouter(_RoutingProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            intent_sha = hashlib.sha256(request.original_prompt.encode("utf-8")).hexdigest()
            result = {
                "route": "abstain_unsupported_task_api",
                "revised_prompt": None,
                "intent_sha256_before": intent_sha,
                "intent_sha256_after": intent_sha,
            }
            return runner.ProviderOutcome(
                decision="route_with_too_many_rewrites",
                result=result,
                raw_response="route",
                parsed_response=result,
                abstained=False,
                resource=runner.ResourceUsage(prompt_rewrites=2),
            )

    config = _config(tmp_path)
    provider = RewriteOverflowRouter()
    session = runner.ExperimentRunner.open(
        config,
        routing_provider=provider,
    )
    invocation = runner.RoutingInvocation(
        case_id="selection2env_drawer_mug_task_api_blocker",
        arm="B2_typed_route_and_prompt_repair",
        original_prompt="Open the drawer and place the mug inside.",
        routing_instruction="Route typed failure.",
        prompt_template_version="typed-route-v1",
        typed_failure={"code": "visible_semantic_mismatch"},
        trusted_state={},
        asset_availability={},
        visible_report={"status": "fail"},
        seed=6,
        attempt=1,
    )

    with pytest.raises(runner.StudyStoppedError, match="max_prompt_rewrites_per_case_arm"):
        session.execute(invocation)

    events = [json.loads(line) for line in config.log_path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["outputs"]["result"]["route"] == "abstain_unsupported_task_api"
    assert events[-1]["gate_results"]["resource_budget"] == "fail"
    assert events[-1]["gate_results"]["study_stopped"] is True


def test_analysis_entrypoints_delegate_to_the_frozen_protocol() -> None:
    visible_rows = [
        {
            "case_id": "case",
            "gold": {"presence": "fail"},
            "prediction": {"presence": "fail"},
        }
    ]
    routing_rows = [
        {
            "case_id": "case",
            "gold_route": "accept_existing_compile",
            "predicted_route": "accept_existing_compile",
            "recoverable": True,
            "robust_completion": True,
            "unsafe_publication": False,
        }
    ]
    paired_rows = [{"group_id": "g", "baseline": 0.0, "candidate": 1.0}]

    assert runner.summarize_visible(visible_rows) == protocol.visible_metrics(visible_rows)
    assert runner.summarize_routing(routing_rows) == protocol.routing_metrics(routing_rows)
    assert runner.summarize_paired_delta(
        paired_rows,
        resamples=7,
        seed=11,
    ) == protocol.paired_cluster_bootstrap_delta(paired_rows, resamples=7, seed=11)
    assert runner.summarize_route_comparison([False, True], [True, True]) == protocol.exact_mcnemar(
        [False, True], [True, True]
    )


def test_execute_plan_reports_real_completion_and_resumes_checkpoints(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provider = _VisibleProvider()
    progress: list[runner.ProgressUpdate] = []
    session = runner.ExperimentRunner.open(
        config,
        visible_provider=provider,
        progress=progress.append,
    )
    first = _a0_invocation(seed=7)
    second = replace(first, attempt=2)

    receipts = session.execute_plan([first, second])
    resumed = session.execute_plan([first, second])

    assert len(receipts) == 2
    assert [item.resumed for item in resumed] == [True, True]
    assert len(provider.requests) == 2
    completed = [item for item in progress if item.stage == "plan.completed"]
    assert [(item.completed, item.total) for item in completed] == [(2, 2), (2, 2)]


def test_progress_consumer_failure_cannot_turn_provider_work_into_failure(tmp_path: Path) -> None:
    def broken_progress(update) -> None:
        raise RuntimeError(f"frontend unavailable at {update.stage}")

    config = _config(tmp_path)
    provider = _VisibleProvider()
    session = runner.ExperimentRunner.open(
        config,
        visible_provider=provider,
        progress=broken_progress,
    )
    invocation = _a0_invocation(seed=8)

    receipt = session.execute(invocation)

    assert receipt.event["decision"] == "provider_call_completed"
    assert len(provider.requests) == 1
    assert len(session.progress_error_digests) >= 3


def test_format_repair_is_single_hash_bound_child_of_base_invocation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    source_response = '{"visible_facts":"same","schema":"invalid"}'

    class RepairProvider(_VisibleProvider):
        def invoke(self, request, progress):
            if request.repair_index == 0:
                self.requests.append(request)
                result = {"visible_facts": "same", "schema": "invalid"}
                return runner.ProviderOutcome(
                    decision="schema_invalid",
                    result=result,
                    raw_response=source_response,
                    parsed_response=result,
                    abstained=False,
                    resource=runner.ResourceUsage(),
                )
            return super().invoke(request, progress)

    provider = RepairProvider()
    session = runner.ExperimentRunner.open(config, visible_provider=provider)
    base = runner.VisibleInvocation(
        case_id="asset_probe_dustbin_legacy_900",
        arm="A1_typed_abstaining_critic_3b",
        prompt="base response schema",
        prompt_template_version="typed-v1",
        processor_config={},
        seed=9,
        attempt=1,
    )
    parent = session.execute(base)
    assert parent.event["gate_results"]["format_repair_eligible"] is True
    repair = replace(
        base,
        prompt=runner.build_format_only_repair_prompt(base.prompt, source_response),
        prompt_template_version=runner.FORMAT_ONLY_REPAIR_TEMPLATE_VERSION,
        repair_index=1,
        repair_of=parent.invocation_id,
        repair_source_prompt=base.prompt,
        repair_source_response=source_response,
    )

    child = session.execute(repair)

    assert child.event["outputs"]["repair"] == {
        "is_format_only_repair": True,
        "repair_of": parent.invocation_id,
    }
    assert child.event["input_bindings"]["repair_index"] == 1
    assert (
        child.event["input_bindings"]["repair_source_prompt_sha256"]
        == hashlib.sha256(base.prompt.encode("utf-8")).hexdigest()
    )
    assert (
        child.event["input_bindings"]["repair_source_response_sha256"]
        == hashlib.sha256(source_response.encode("utf-8")).hexdigest()
    )
    assert child.event["gate_results"]["format_repair_eligible"] is False
    with pytest.raises(runner.DuplicateInvocationError):
        session.execute(
            replace(
                repair,
                prompt=runner.build_format_only_repair_prompt(base.prompt, source_response + "x"),
                repair_source_response=source_response + "x",
            )
        )
    assert len(provider.requests) == 2

    unbound_config = _config(tmp_path / "unbound")
    unbound_provider = _VisibleProvider()
    unbound_session = runner.ExperimentRunner.open(
        unbound_config,
        visible_provider=unbound_provider,
    )
    with pytest.raises(runner.StudyStoppedError, match="not bound"):
        unbound_session.execute(replace(repair, repair_of="f" * 64))
    assert unbound_provider.requests == []

    tampered_config = _config(tmp_path / "tampered")
    tampered_provider = RepairProvider()
    tampered_session = runner.ExperimentRunner.open(
        tampered_config,
        visible_provider=tampered_provider,
    )
    tampered_parent = tampered_session.execute(base)
    with pytest.raises(runner.StudyStoppedError, match="exact eligible parent bytes"):
        tampered_session.execute(
            replace(
                repair,
                repair_of=tampered_parent.invocation_id,
                repair_source_response=source_response + "x",
            )
        )
    assert len(tampered_provider.requests) == 1


def test_invalid_provider_output_is_a_terminal_failure_with_partial_receipt(tmp_path: Path) -> None:
    class InvalidRouter(_RoutingProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            return runner.ProviderOutcome(
                decision="invalid-route",
                result={
                    "route": "invented_unregistered_route",
                    "revised_prompt": None,
                    "intent_sha256_before": "5" * 64,
                    "intent_sha256_after": "5" * 64,
                },
                raw_response="unregistered route raw response",
                parsed_response={"route": "invented_unregistered_route"},
                abstained=False,
                resource=runner.ResourceUsage(input_tokens=12, output_tokens=2),
            )

    config = _config(tmp_path)
    provider = InvalidRouter()
    session = runner.ExperimentRunner.open(config, routing_provider=provider)
    invocation = runner.RoutingInvocation(
        case_id="selection2env_drawer_mug_task_api_blocker",
        arm="B2_typed_route_and_prompt_repair",
        original_prompt="Open the drawer and place the mug inside.",
        routing_instruction="Use only registered routes.",
        prompt_template_version="typed-route-v1",
        typed_failure={"code": "missing_task_api"},
        trusted_state={},
        asset_availability={},
        visible_report=None,
        seed=10,
        attempt=1,
    )

    failed = session.execute(invocation)
    resumed = session.execute(invocation)

    assert failed.event["decision"] == "provider_call_failed"
    assert failed.event["error"]["code"] == "provider_contract_error"
    assert (
        failed.event["model_receipt"]["raw_response_sha256"]
        == hashlib.sha256(b"unregistered route raw response").hexdigest()
    )
    assert failed.event["resource_receipt"]["input_tokens"] == 12
    assert resumed.resumed is True
    assert len(provider.requests) == 1

    class InvalidVisible(_VisibleProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            return runner.ProviderOutcome(
                decision="bad raw response",
                result={"status": "fail"},
                raw_response=object(),  # type: ignore[arg-type]
                parsed_response=None,
                abstained=True,
                resource=runner.ResourceUsage(),
            )

    visible_config = _config(tmp_path / "visible")
    visible_provider = InvalidVisible()
    visible_session = runner.ExperimentRunner.open(
        visible_config,
        visible_provider=visible_provider,
    )
    visible_invocation = _a0_invocation(seed=10)
    visible_failed = visible_session.execute(visible_invocation)
    assert visible_failed.event["decision"] == "provider_call_failed"
    assert visible_failed.event["error"]["code"] == "provider_contract_error"
    assert visible_failed.event["model_receipt"]["raw_response_sha256"] is None


def test_public_open_rejects_alternate_frozen_spec_path(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec_copy = tmp_path / "experiment_spec.json"
    spec_copy.write_bytes((STUDY_ROOT / "experiment_spec.json").read_bytes())

    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(replace(config, spec_path=spec_copy))


def test_sealed_visible_test_fails_closed_when_deleted_evidence_is_selected(tmp_path: Path) -> None:
    invocation = runner.VisibleInvocation(
        case_id="sceneagent_official_open_laptop",
        arm="A1_typed_abstaining_critic_3b",
        prompt="sealed typed prompt",
        prompt_template_version="typed-visible-v1",
        processor_config={"max_new_tokens": 768},
        seed=20260831,
        attempt=1,
    )
    blocked_config = _config(tmp_path / "blocked")
    missing_evidence_root = tmp_path / "source-only-repo"
    source_manifest = json.loads(blocked_config.source_manifest_path.read_text(encoding="utf-8"))
    for entry in source_manifest["entries"]:
        relative = Path(entry["path"])
        target = missing_evidence_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_ROOT / relative).read_bytes())
    blocked_config = replace(blocked_config, repo_root=missing_evidence_root)
    blocked_provider = _VisibleProvider()
    blocked = runner.ExperimentRunner.open(
        blocked_config,
        visible_provider=blocked_provider,
    )
    with pytest.raises(ValueError, match="missing artifact"):
        blocked.execute(invocation)
    assert blocked_provider.requests == []


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value.update(schema_version="wrong"), "source manifest must use"),
        (lambda value: value.update(study_id="wrong"), "study_id mismatch"),
        (lambda value: value.update(entries=[]), "entries must be non-empty"),
        (lambda value: value.update(entries={}), "entries must be non-empty"),
        (lambda value: value["entries"].__setitem__(0, "bad"), "entry must be an object"),
        (lambda value: value["entries"][0].update(path=7), "path/sha256 must be strings"),
        (lambda value: value["entries"][0].update(path="../escape.py"), "unsafe or duplicate"),
        (lambda value: value["entries"].append(dict(value["entries"][0])), "unsafe or duplicate"),
        (lambda value: value["entries"][0].update(path="missing-source.py"), "missing or escapes"),
        (lambda value: value["entries"].pop(), "missing required entries"),
    ],
)
def test_source_manifest_structure_attacks_fail_closed(
    tmp_path: Path,
    mutation,
    match: str,
) -> None:
    config = _config(tmp_path)
    manifest = json.loads(config.source_manifest_path.read_text(encoding="utf-8"))
    mutation(manifest)
    config.source_manifest_path.write_bytes(protocol.canonical_json_bytes(manifest) + b"\n")
    config = replace(
        config,
        expected_source_manifest_sha256=_sha256(config.source_manifest_path),
    )

    with pytest.raises(runner.RunnerIntegrityError, match=match):
        runner.ExperimentRunner.open(config)


def test_source_manifest_digest_json_and_optional_model_verification_paths(tmp_path: Path) -> None:
    missing_config = _config(tmp_path / "missing")
    missing_config.source_manifest_path.unlink()
    with pytest.raises(runner.RunnerIntegrityError, match="cannot be read"):
        runner.ExperimentRunner.open(missing_config)

    digest_config = _config(tmp_path / "digest")
    with pytest.raises(runner.RunnerIntegrityError, match="manifest digest mismatch"):
        runner.ExperimentRunner.open(
            replace(digest_config, expected_source_manifest_sha256="0" * 64)
        )

    json_config = _config(tmp_path / "json")
    json_config.source_manifest_path.write_bytes(b"{\n")
    with pytest.raises(runner.RunnerIntegrityError, match="not valid JSON"):
        runner.ExperimentRunner.open(
            replace(
                json_config,
                expected_source_manifest_sha256=_sha256(json_config.source_manifest_path),
            )
        )

    noncanonical_config = _config(tmp_path / "noncanonical")
    manifest = json.loads(noncanonical_config.source_manifest_path.read_text(encoding="utf-8"))
    noncanonical_config.source_manifest_path.write_text(
        json.dumps(manifest) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(runner.RunnerIntegrityError, match="canonical JSON"):
        runner.ExperimentRunner.open(
            replace(
                noncanonical_config,
                expected_source_manifest_sha256=_sha256(noncanonical_config.source_manifest_path),
            )
        )

    no_model_config = _config(tmp_path / "no-model")
    session = runner.ExperimentRunner.open(replace(no_model_config, verify_models=False))
    assert session.identity.model_identity_matches == 0


@pytest.mark.parametrize(
    ("kind", "match"),
    [
        ("missing", "cannot be read"),
        ("digest", "digest mismatch"),
        ("json", "not valid JSON"),
        ("object", "must be a JSON object"),
        ("canonical", "must be canonical JSON"),
    ],
)
def test_bound_json_evidence_loader_fails_closed(
    tmp_path: Path,
    kind: str,
    match: str,
) -> None:
    path = tmp_path / "evidence.json"
    if kind == "json":
        path.write_bytes(b"{\n")
    elif kind == "object":
        path.write_bytes(b"[]\n")
    elif kind == "canonical":
        path.write_text('{"value": 1}\n', encoding="utf-8")
    elif kind != "missing":
        path.write_bytes(b'{"value":1}\n')
    expected_sha = "0" * 64 if kind in {"missing", "digest"} else _sha256(path)

    with pytest.raises(runner.RunnerIntegrityError, match=match):
        runner._load_canonical_bound_json(
            path,
            expected_sha256=expected_sha,
            label="test evidence",
        )


@pytest.mark.parametrize(
    ("attack", "match"),
    [
        ("short_prefix", "truncated before"),
        ("prefix_digest", "prefix digest mismatch"),
        ("invalid_json", "not valid JSON"),
        ("non_object", "must be an object"),
        ("non_canonical", "not canonical JSON"),
        ("chain", "chain mismatch"),
        ("event_digest", "event digest mismatch"),
        ("event_id", "invalid event_id"),
        ("logical_type", "invalid invocation logical_key"),
        ("duplicate_event_id", "duplicate or invalid event_id"),
        ("duplicate_logical", "duplicate or invalid invocation logical_key"),
    ],
)
def test_journal_structure_attacks_fail_closed(tmp_path: Path, attack: str, match: str) -> None:
    config = _config(tmp_path)
    provider = _VisibleProvider()
    session = runner.ExperimentRunner.open(config, visible_provider=provider)
    invocation = _a0_invocation(seed=12)
    event = dict(session.execute(invocation).event)
    reservation = dict(session._journal.events[-2])
    prefix = (STUDY_ROOT / "run_log.jsonl").read_bytes()
    if attack == "short_prefix":
        config.log_path.write_bytes(prefix.splitlines(keepends=True)[0])
    elif attack == "prefix_digest":
        config.log_path.write_bytes(b"X" + prefix[1:])
    elif attack == "invalid_json":
        config.log_path.write_bytes(prefix + b"{\n")
    elif attack == "non_object":
        config.log_path.write_bytes(prefix + b"[]\n")
    elif attack == "non_canonical":
        config.log_path.write_bytes(
            prefix
            + protocol.canonical_json_bytes(reservation)
            + b"\n"
            + json.dumps(event).encode("utf-8")
            + b"\n"
        )
    elif attack == "chain":
        event["previous_event_sha256"] = "0" * 64
        _write_event_lines(config, [reservation, _rehash_event(event)])
    elif attack == "event_digest":
        event["event_sha256"] = "0" * 64
        _write_event_lines(config, [reservation, event])
    elif attack == "event_id":
        event["event_id"] = ""
        _write_event_lines(config, [reservation, _rehash_event(event)])
    elif attack == "logical_type":
        event["input_bindings"]["logical_key"] = 7
        _write_event_lines(config, [reservation, _rehash_event(event)])
    else:
        first = _rehash_event(event)
        second = json.loads(json.dumps(first))
        second["previous_event_sha256"] = first["event_sha256"]
        if attack == "duplicate_event_id":
            second["input_bindings"].pop("logical_key")
        else:
            second["event_id"] = "different-event-id"
        second = _rehash_event(second)
        _write_event_lines(config, [reservation, first, second])

    with pytest.raises(runner.RunnerIntegrityError, match=match):
        runner.ExperimentRunner.open(config)


def test_anchor_json_shape_and_field_tamper_fail_closed(tmp_path: Path) -> None:
    invalid = _config(tmp_path / "invalid")
    runner.ExperimentRunner.open(invalid)
    invalid.anchor_path.write_bytes(b"{\n")
    with pytest.raises(runner.RunnerIntegrityError, match="anchor is not valid JSON"):
        runner.ExperimentRunner.open(invalid)

    non_object = _config(tmp_path / "non-object")
    runner.ExperimentRunner.open(non_object)
    non_object.anchor_path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(runner.RunnerIntegrityError, match="anchor must be a JSON object"):
        runner.ExperimentRunner.open(non_object)

    mismatch = _config(tmp_path / "mismatch")
    runner.ExperimentRunner.open(mismatch)
    anchor = json.loads(mismatch.anchor_path.read_text(encoding="utf-8"))
    anchor["study_id"] = "tampered"
    mismatch.anchor_path.write_bytes(protocol.canonical_json_bytes(anchor) + b"\n")
    with pytest.raises(runner.RunnerIntegrityError, match="anchor mismatch for study_id"):
        runner.ExperimentRunner.open(mismatch)

    genesis_mismatch = _config(tmp_path / "genesis-mismatch")
    runner.ExperimentRunner.open(genesis_mismatch)
    anchor = json.loads(genesis_mismatch.anchor_path.read_text(encoding="utf-8"))
    anchor["genesis_blob_sha256"] = "0" * 64
    genesis_mismatch.anchor_path.write_bytes(protocol.canonical_json_bytes(anchor) + b"\n")
    with pytest.raises(
        runner.RunnerIntegrityError,
        match="anchor mismatch for genesis_blob_sha256",
    ):
        runner.ExperimentRunner.open(genesis_mismatch)


@pytest.mark.parametrize(
    ("attack", "match"),
    [
        ("no_anchor", "without a trusted run anchor"),
        ("invalid_json", "not valid JSON"),
        ("schema", "schema is invalid"),
        ("anchor_nonobject", "anchor must be a JSON object"),
        ("old_binding", "old log binding is invalid"),
        ("event_nonobject", "event must be an object"),
        ("event_digest", "event digest mismatch"),
        ("old_log", "old log binding mismatch"),
        ("chain", "does not extend"),
        ("old_anchor", "old run anchor mismatch"),
        ("suffix", "does not match the run log suffix"),
        ("neither_anchor", "matches neither transaction state"),
        ("log_read", "cannot be read during pending recovery"),
    ],
)
def test_pending_receipt_attacks_fail_closed(
    tmp_path: Path,
    attack: str,
    match: str,
) -> None:
    config, prefix, _old_anchor, _new_anchor, pending = _pending_fixture(tmp_path)
    if attack == "no_anchor":
        config.anchor_path.unlink()
    elif attack == "invalid_json":
        config.pending_path.write_bytes(b"{\n")
    elif attack == "schema":
        pending["schema_version"] = "wrong"
    elif attack == "anchor_nonobject":
        config.anchor_path.write_text("[]\n", encoding="utf-8")
    elif attack == "old_binding":
        pending["old_log_size_bytes"] = -1
    elif attack == "event_nonobject":
        pending["event"] = []
    elif attack == "event_digest":
        pending["event"]["event_sha256"] = "0" * 64
    elif attack == "old_log":
        pending["old_log_sha256"] = "0" * 64
    elif attack == "chain":
        pending["event"]["previous_event_sha256"] = "0" * 64
        pending["event"] = _rehash_event(pending["event"])
    elif attack == "old_anchor":
        anchor = json.loads(config.anchor_path.read_text(encoding="utf-8"))
        anchor["study_id"] = "tampered"
        config.anchor_path.write_bytes(protocol.canonical_json_bytes(anchor) + b"\n")
    elif attack == "suffix":
        config.log_path.write_bytes(prefix + b"unexpected\n")
    elif attack == "neither_anchor":
        encoded = protocol.canonical_json_bytes(pending["event"]) + b"\n"
        config.log_path.write_bytes(prefix + encoded)
        anchor = json.loads(config.anchor_path.read_text(encoding="utf-8"))
        anchor["study_id"] = "tampered"
        config.anchor_path.write_bytes(protocol.canonical_json_bytes(anchor) + b"\n")
    elif attack == "log_read":
        config.log_path.unlink()
        config.log_path.mkdir()
    if attack not in {
        "invalid_json",
        "anchor_nonobject",
        "old_anchor",
        "neither_anchor",
        "log_read",
        "no_anchor",
    }:
        config.pending_path.write_bytes(protocol.canonical_json_bytes(pending) + b"\n")

    with pytest.raises(runner.RunnerIntegrityError, match=match):
        runner.ExperimentRunner.open(config)


def test_pending_recovery_accepts_log_appended_with_old_or_new_anchor(tmp_path: Path) -> None:
    for name, use_new_anchor in (("old", False), ("new", True)):
        config, prefix, old_anchor, new_anchor, pending = _pending_fixture(tmp_path / name)
        encoded = protocol.canonical_json_bytes(pending["event"]) + b"\n"
        config.log_path.write_bytes(prefix + encoded)
        anchor = new_anchor if use_new_anchor else old_anchor
        config.anchor_path.write_bytes(protocol.canonical_json_bytes(anchor) + b"\n")

        runner.ExperimentRunner.open(config)

        assert json.loads(config.anchor_path.read_text(encoding="utf-8"))["line_count"] == 8
        assert not config.pending_path.exists()


def test_unreadable_run_log_fails_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.log_path.unlink()
    config.log_path.mkdir()
    with pytest.raises(runner.RunnerIntegrityError, match="run log cannot be read"):
        runner.ExperimentRunner.open(config)


@pytest.mark.parametrize(
    ("identity", "match"),
    [
        (
            runner.ProviderIdentity("", "revision", "1" * 64, "test", False),
            "identity strings",
        ),
        (
            runner.ProviderIdentity("provider", "revision", "z" * 64, "test", False),
            "implementation_sha256",
        ),
        (
            runner.ProviderIdentity("provider", "revision", "1" * 63, "test", False),
            "implementation_sha256",
        ),
        (
            runner.ProviderIdentity("provider", "revision", "A" * 64, "test", False),
            "implementation_sha256",
        ),
    ],
)
def test_provider_identity_validation_fails_before_call(
    tmp_path: Path,
    identity: runner.ProviderIdentity,
    match: str,
) -> None:
    class Provider(_VisibleProvider):
        def __init__(self) -> None:
            super().__init__()
            self.identity = identity

    provider = Provider()
    session = runner.ExperimentRunner.open(
        _config(tmp_path),
        visible_provider=provider,
    )
    invocation = _a0_invocation(seed=14)
    with pytest.raises(runner.RunnerIntegrityError, match=match):
        session.execute(invocation)
    assert provider.requests == []


@pytest.mark.parametrize(
    "outcome",
    [
        None,
        runner.ProviderOutcome(
            "",
            {},
            "raw",
            None,
            True,
            runner.ResourceUsage(),
        ),
        runner.ProviderOutcome(
            "decision",
            [],  # type: ignore[arg-type]
            "raw",
            None,
            True,
            runner.ResourceUsage(),
        ),
        runner.ProviderOutcome(
            "decision",
            {},
            "raw",
            [],  # type: ignore[arg-type]
            True,
            runner.ResourceUsage(),
        ),
        runner.ProviderOutcome(
            "decision",
            {},
            "raw",
            None,
            1,  # type: ignore[arg-type]
            runner.ResourceUsage(),
        ),
        runner.ProviderOutcome(
            "decision",
            {},
            "raw",
            None,
            True,
            {},  # type: ignore[arg-type]
        ),
        runner.ProviderOutcome(
            "decision",
            {},
            "raw",
            None,
            True,
            runner.ResourceUsage(input_tokens=True),
        ),
        runner.ProviderOutcome(
            "decision",
            {},
            "raw",
            None,
            True,
            runner.ResourceUsage(input_tokens=-1),
        ),
        runner.ProviderOutcome(
            "decision",
            {"bad": object()},
            "raw",
            None,
            True,
            runner.ResourceUsage(),
        ),
        runner.ProviderOutcome(
            "decision",
            {},
            "raw",
            {"bad": object()},
            True,
            runner.ResourceUsage(),
        ),
    ],
    ids=[
        "wrong-type",
        "empty-decision",
        "result-shape",
        "parsed-shape",
        "flag-shape",
        "resource-shape",
        "boolean-counter",
        "negative-counter",
        "result-json",
        "parsed-json",
    ],
)
def test_visible_provider_contract_failures_are_receipted(tmp_path: Path, outcome) -> None:
    class Provider(_VisibleProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            return outcome

    provider = Provider()
    session = runner.ExperimentRunner.open(_config(tmp_path), visible_provider=provider)
    invocation = _a0_invocation(seed=15)

    receipt = session.execute(invocation)

    assert receipt.event["decision"] == "provider_call_failed"
    assert receipt.event["error"]["code"] == "provider_contract_error"


def test_visible_invocation_and_plan_input_validation(tmp_path: Path) -> None:
    provider = _VisibleProvider()
    session = runner.ExperimentRunner.open(_config(tmp_path), visible_provider=provider)
    base = _a0_invocation(seed=16)
    invalid = [
        (replace(base, attempt=0), "attempt"),
        (replace(base, repair_index=2), "repair_index"),
        (replace(base, repair_of="f" * 64), "cannot declare repair_of"),
        (replace(base, repair_index=1), "must bind repair_of"),
        (replace(base, prompt=""), "must be non-empty"),
        (replace(base, prompt_template_version=""), "must be non-empty"),
        (replace(base, processor_config=[]), "must be a mapping"),  # type: ignore[arg-type]
        (replace(base, resolved_scene_sha256="z" * 64), "lowercase hex"),
        (replace(base, resolved_scene_sha256="f" * 63), "lowercase hex"),
        (replace(base, resolved_scene_sha256="A" * 64), "lowercase hex"),
    ]
    for invocation, match in invalid:
        with pytest.raises(ValueError, match=match):
            session.execute(invocation)
    with pytest.raises(TypeError, match="VisibleInvocation or RoutingInvocation"):
        session.execute(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one"):
        session.execute_plan([])
    assert provider.requests == []


def test_prompt_freeze_input_validation_and_replacement_spec_rejection(tmp_path: Path) -> None:
    config = _config(tmp_path)
    session = runner.ExperimentRunner.open(config)
    valid = runner.VisiblePromptFreeze("prompt", "typed-v1", {}, "7" * 64)
    invalid = [
        (replace(valid, prompt=""), "must be non-empty"),
        (replace(valid, prompt_template_version=""), "must be non-empty"),
        (replace(valid, processor_config=[]), "must be a mapping"),  # type: ignore[arg-type]
        (replace(valid, selection_evidence_sha256="z" * 64), "lowercase hex"),
        (replace(valid, selection_evidence_sha256="7" * 63), "lowercase hex"),
        (replace(valid, selection_evidence_sha256="A" * 64), "lowercase hex"),
    ]
    for freeze, match in invalid:
        with pytest.raises(ValueError, match=match):
            session.freeze_visible_prompt(freeze)

    drift_config = _config(tmp_path / "replacement")
    spec_copy = tmp_path / "replacement" / "spec.json"
    spec_copy.write_bytes((STUDY_ROOT / "experiment_spec.json").read_bytes())
    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(replace(drift_config, spec_path=spec_copy))


def _routing_invocation(**changes) -> runner.RoutingInvocation:
    base = runner.RoutingInvocation(
        case_id="selection2env_drawer_mug_task_api_blocker",
        arm="B2_typed_route_and_prompt_repair",
        original_prompt="Open the drawer and place the mug inside.",
        routing_instruction="Choose a frozen typed route.",
        prompt_template_version="typed-route-v1",
        typed_failure={"code": "missing_task_api"},
        trusted_state={},
        asset_availability={},
        visible_report=None,
        seed=17,
        attempt=1,
    )
    return replace(base, **changes)


def test_routing_invocation_validation_and_gold_isolation(tmp_path: Path) -> None:
    provider = _RoutingProvider()
    session = runner.ExperimentRunner.open(_config(tmp_path), routing_provider=provider)
    invalid = [
        (_routing_invocation(attempt=0), "attempt"),
        (_routing_invocation(original_prompt=""), "prompts must be non-empty"),
        (_routing_invocation(routing_instruction=""), "prompts must be non-empty"),
        (_routing_invocation(prompt_template_version=""), "template_version"),
        (
            _routing_invocation(untyped_failure_summary=7),  # type: ignore[arg-type]
            "must be text or null",
        ),
        (_routing_invocation(arm="unknown"), "unknown routing arm"),
        (_routing_invocation(case_id="unknown"), "unknown routing case"),
        (_routing_invocation(original_prompt="changed"), "exactly match"),
        (_routing_invocation(attempt=3), "exceeds frozen maximum"),
        (
            _routing_invocation(arm="B0_retry_unchanged", typed_failure={}),
            "failure code",
        ),
        (
            _routing_invocation(arm="B1_generic_prompt_repair"),
            "untyped failure summary",
        ),
        (
            _routing_invocation(typed_failure=[]),  # type: ignore[arg-type]
            "must be mappings",
        ),
        (
            _routing_invocation(visible_report=[]),  # type: ignore[arg-type]
            "must be mappings",
        ),
        (
            _routing_invocation(typed_failure={"nested": [{"gold_route": "secret"}]}),
            "cannot contain gold",
        ),
    ]
    for invocation, match in invalid:
        with pytest.raises(ValueError, match=match):
            session.execute(invocation)
    assert provider.requests == []


def test_routing_provider_configuration_and_oracle_seam(tmp_path: Path) -> None:
    def factory(arm: str) -> _RoutingProvider:
        return _RoutingProvider()

    with pytest.raises(ValueError, match="not both"):
        runner.ExperimentRunner.open(
            _config(tmp_path / "ambiguous"),
            routing_provider=_RoutingProvider(),
            routing_provider_factory=factory,
        )
    with pytest.raises(ValueError, match="must be distinct"):
        runner.ExperimentRunner.open(
            _config(tmp_path / "shared-factory"),
            routing_provider_factory=factory,
            oracle_routing_provider_factory=factory,
        )

    no_provider = runner.ExperimentRunner.open(_config(tmp_path / "none"))
    with pytest.raises(runner.RunnerIntegrityError, match="no typed routing provider"):
        no_provider.execute(_routing_invocation())

    production_config = replace(_config(tmp_path / "production"), allow_test_providers=False)
    forbidden = runner.ExperimentRunner.open(
        production_config,
        routing_provider=_RoutingProvider(),
    )
    with pytest.raises(runner.RunnerIntegrityError, match="non-production provider"):
        forbidden.execute(_routing_invocation())

    oracle_provider = _RoutingProvider()
    missing_capability = runner.ExperimentRunner.open(
        _config(tmp_path / "oracle"),
        routing_provider=oracle_provider,
    )
    with pytest.raises(runner.RunnerIntegrityError, match="exact descriptive-ceiling"):
        missing_capability.execute(_routing_invocation(arm="B3_oracle_route_ceiling"))

    missing_oracle_factory = runner.ExperimentRunner.open(
        _config(tmp_path / "missing-oracle-factory"),
        oracle_capability=runner.OracleRoutingCapability(
            study_id=protocol.STUDY_ID,
            spec_sha256=runner.FROZEN_SPEC_SHA256,
            purpose="descriptive_ceiling_only",
        ),
    )
    with pytest.raises(runner.RunnerIntegrityError, match="separate provider factory"):
        missing_oracle_factory.execute(_routing_invocation(arm="B3_oracle_route_ceiling"))

    oracle = runner.ExperimentRunner.open(
        _config(tmp_path / "oracle-capable"),
        oracle_routing_provider_factory=lambda arm: oracle_provider,
        oracle_capability=runner.OracleRoutingCapability(
            study_id=protocol.STUDY_ID,
            spec_sha256=runner.FROZEN_SPEC_SHA256,
            purpose="descriptive_ceiling_only",
        ),
    )
    oracle_receipt = oracle.execute(_routing_invocation(arm="B3_oracle_route_ceiling"))
    assert oracle_provider.requests[0].gold_route == "abstain_unsupported_task_api"
    assert oracle_receipt.event["gate_results"]["gold_route_exposed"] is True

    repeat_providers: list[_RoutingProvider] = []

    def repeat_factory(arm: str) -> _RoutingProvider:
        provider = _RoutingProvider()
        repeat_providers.append(provider)
        return provider

    repeat = runner.ExperimentRunner.open(
        _config(tmp_path / "repeat"),
        routing_provider_factory=repeat_factory,
    )
    repeat.execute(_routing_invocation(attempt=1))
    repeat.execute(_routing_invocation(attempt=2))
    assert sum(len(provider.requests) for provider in repeat_providers) == 2

    reused_provider = _RoutingProvider()
    reused = runner.ExperimentRunner.open(
        _config(tmp_path / "reused"),
        routing_provider_factory=lambda arm: reused_provider,
    )
    reused.execute(_routing_invocation(attempt=1))
    with pytest.raises(runner.RunnerIntegrityError, match="reused a stateful provider"):
        reused.execute(_routing_invocation(attempt=2))


@pytest.mark.parametrize(
    ("arm", "result", "parsed", "expected_error"),
    [
        (
            "B2_typed_route_and_prompt_repair",
            {
                "route": "accept_existing_compile",
                "revised_prompt": 7,
                "intent_sha256_before": "8" * 64,
                "intent_sha256_after": "8" * 64,
            },
            {},
            "revised_prompt must be a string or null",
        ),
        (
            "B0_retry_unchanged",
            {
                "route": "accept_existing_compile",
                "revised_prompt": "changed",
                "intent_sha256_before": "8" * 64,
                "intent_sha256_after": "8" * 64,
            },
            {},
            "B0 retry must keep the original prompt byte-identical",
        ),
        (
            "B2_typed_route_and_prompt_repair",
            {
                "route": "accept_existing_compile",
                "revised_prompt": "changed",
                "intent_sha256_before": "8" * 64,
                "intent_sha256_after": "8" * 64,
            },
            {},
            "selected route is not allowed to rewrite the prompt",
        ),
        (
            "B2_typed_route_and_prompt_repair",
            {
                "route": "accept_existing_compile",
                "revised_prompt": None,
                "intent_sha256_before": "short",
                "intent_sha256_after": "8" * 64,
            },
            {},
            "intent_sha256_before must be lowercase hex",
        ),
        (
            "B2_typed_route_and_prompt_repair",
            {
                "route": "accept_existing_compile",
                "revised_prompt": None,
                "intent_sha256_before": "z" * 64,
                "intent_sha256_after": "8" * 64,
            },
            {},
            "intent_sha256_before must be lowercase hex",
        ),
        (
            "B2_typed_route_and_prompt_repair",
            {
                "route": "accept_existing_compile",
                "revised_prompt": None,
                "intent_sha256_before": "A" * 64,
                "intent_sha256_after": "8" * 64,
            },
            {},
            "intent_sha256_before must be lowercase hex",
        ),
        (
            "B2_typed_route_and_prompt_repair",
            {
                "route": "accept_existing_compile",
                "revised_prompt": None,
                "intent_sha256_before": "8" * 64,
                "intent_sha256_after": "8" * 64,
            },
            {"bad": object()},
            "provider outcome must be canonical-JSON serializable",
        ),
        (
            "B2_typed_route_and_prompt_repair",
            {"bad": object()},
            {},
            "provider outcome must be canonical-JSON serializable",
        ),
    ],
)
def test_routing_provider_contract_attack_receipts(
    tmp_path: Path,
    arm: str,
    result: dict,
    parsed: dict,
    expected_error: str,
) -> None:
    class Provider(_RoutingProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            return runner.ProviderOutcome(
                decision="contract-attack",
                result=result,
                raw_response=b"raw bytes",
                parsed_response=result if parsed == {} else parsed,
                abstained=False,
                resource=runner.ResourceUsage(
                    prompt_rewrites=int(result.get("revised_prompt") == "changed")
                ),
            )

    provider = Provider()
    session = runner.ExperimentRunner.open(_config(tmp_path), routing_provider=provider)
    receipt = session.execute(
        _routing_invocation(
            arm=arm,
            resource_reservation=runner.ResourceUsage(
                prompt_rewrites=int(result.get("revised_prompt") == "changed")
            ),
        )
    )
    assert receipt.event["decision"] == "provider_call_failed"
    assert receipt.event["error"]["code"] == "provider_contract_error"
    assert (
        receipt.event["error"]["message_sha256"]
        == hashlib.sha256(expected_error.encode("utf-8")).hexdigest()
    )
    assert receipt.event["error"].get("message") is None


def test_routing_unsafe_intent_is_recorded_without_becoming_physical_pass(tmp_path: Path) -> None:
    class UnsafeRouter(_RoutingProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            verifier = runner._ProductionIntentPreservationVerifier()
            verification = verifier.verify(request.original_prompt, "changed but unsafe")
            result = {
                "route": "repair_visible_prompt",
                "revised_prompt": "changed but unsafe",
                "intent_sha256_before": verification.before_sha256,
                "intent_sha256_after": verification.after_sha256,
            }
            return runner.ProviderOutcome(
                decision="unsafe-intent-change",
                result=result,
                raw_response=b"unsafe",
                parsed_response=result,
                abstained=False,
                resource=runner.ResourceUsage(prompt_rewrites=1),
            )

    session = runner.ExperimentRunner.open(
        _config(tmp_path),
        routing_provider=UnsafeRouter(),
    )
    receipt = session.execute(
        _routing_invocation(resource_reservation=runner.ResourceUsage(prompt_rewrites=1))
    )
    assert receipt.event["decision"] == "provider_call_failed"
    assert receipt.event["gate_results"]["intent_preserved"] is False
    assert receipt.event["gate_results"]["unsafe_publication"] is False
    assert receipt.event["outputs"]["abstain"] is True
    assert receipt.event["gate_results"]["render_used_as_physics_evidence"] is False


def test_all_visible_resource_stop_rules_are_reported_together(tmp_path: Path) -> None:
    class ViolatingProvider(_VisibleProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            return runner.ProviderOutcome(
                decision="resource-violation",
                result={"status": "fail"},
                raw_response="fail",
                parsed_response=None,
                abstained=True,
                resource=runner.ResourceUsage(
                    gpu_time_ms=3 * 60 * 60 * 1000 + 1,
                    peak_vram_mib=30001,
                    network_calls=1,
                    remote_paid_calls=1,
                ),
                claims_physical_pass=True,
            )

    config = _config(tmp_path)
    provider = ViolatingProvider()
    session = runner.ExperimentRunner.open(config, visible_provider=provider)
    invocation = _a0_invocation(seed=18)
    with pytest.raises(runner.StudyStoppedError) as stopped:
        session.execute(invocation)
    for marker in (
        "max_peak_vram_mib",
        "max_gpu_hours",
        "network access",
        "remote_paid_calls",
        "physical pass",
    ):
        assert marker in stopped.value.reason
    call_event = json.loads(config.log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert call_event["expensive_execution_started"] is True


def test_all_routing_resource_stop_rules_are_reported_together(tmp_path: Path) -> None:
    class ViolatingRouter(_RoutingProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            replays = 73
            return runner.ProviderOutcome(
                decision="resource-violation",
                result={
                    "route": "accept_existing_compile",
                    "revised_prompt": None,
                    "intent_sha256_before": "8" * 64,
                    "intent_sha256_after": "8" * 64,
                },
                raw_response="route",
                parsed_response=None,
                abstained=False,
                resource=runner.ResourceUsage(
                    gpu_time_ms=12 * 60 * 60 * 1000 + 1,
                    network_calls=1,
                    remote_paid_calls=1,
                    compile_attempts=217,
                    fresh_physical_replays=replays,
                    runtime_steps=replays * 900 + 1,
                    contact_window_steps=replays * 120 + 1,
                    visible_vlm_invocations=73,
                ),
                claims_physical_pass=True,
            )

    session = runner.ExperimentRunner.open(
        _config(tmp_path),
        routing_provider=ViolatingRouter(),
    )
    with pytest.raises(runner.StudyStoppedError) as stopped:
        session.execute(_routing_invocation())
    for marker in (
        "max_runtime_steps_per_replay",
        "contact_window_steps",
        "max_total_compile_attempts",
        "max_fresh_physical_replays",
        "max_vlm_invocations_for_visible_failures",
        "max_gpu_hours",
        "network access",
        "remote_paid_calls",
        "physical pass",
    ):
        assert marker in stopped.value.reason


def test_visible_failure_receipt_enforces_observed_resource_stop_rule(tmp_path: Path) -> None:
    class FailingProvider(_VisibleProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            return runner.ProviderOutcome(
                decision="invalid-contract",
                result={"status": "failed"},
                raw_response="failure",
                parsed_response={"not_json": object()},
                abstained=True,
                resource=runner.ResourceUsage(network_calls=1),
            )

    config = _config(tmp_path)
    session = runner.ExperimentRunner.open(config, visible_provider=FailingProvider())
    invocation = _a0_invocation(seed=20)

    with pytest.raises(runner.StudyStoppedError, match="network access"):
        session.execute(invocation)

    events = [json.loads(line) for line in config.log_path.read_text().splitlines()]
    assert events[-1]["decision"] == "provider_call_failed"
    assert events[-1]["gate_results"]["resource_budget"] == "fail"
    assert events[-1]["gate_results"]["study_stopped"] is True


def test_routing_failure_receipt_enforces_observed_resource_stop_rule(tmp_path: Path) -> None:
    class FailingRouter(_RoutingProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            return runner.ProviderOutcome(
                decision="invalid-contract",
                result={"route": "unknown"},
                raw_response="failure",
                parsed_response=None,
                abstained=True,
                resource=runner.ResourceUsage(remote_paid_calls=1),
            )

    config = _config(tmp_path)
    session = runner.ExperimentRunner.open(config, routing_provider=FailingRouter())

    with pytest.raises(runner.StudyStoppedError, match="remote_paid_calls"):
        session.execute(_routing_invocation())

    events = [json.loads(line) for line in config.log_path.read_text().splitlines()]
    assert events[-1]["decision"] == "provider_call_failed"
    assert events[-1]["gate_results"]["resource_budget"] == "fail"
    assert events[-1]["gate_results"]["study_stopped"] is True


def test_visible_context_and_provider_configuration_fail_closed(tmp_path: Path) -> None:
    provider = _VisibleProvider()
    session = runner.ExperimentRunner.open(_config(tmp_path / "lookup"), visible_provider=provider)
    base = _a0_invocation(seed=19)
    with pytest.raises(ValueError, match="unknown visible arm"):
        session.execute(replace(base, arm="unknown"))
    with pytest.raises(ValueError, match="unknown visible case"):
        session.execute(replace(base, case_id="unknown"))

    no_provider = runner.ExperimentRunner.open(_config(tmp_path / "none"))
    with pytest.raises(runner.RunnerIntegrityError, match="no visible critic provider"):
        no_provider.execute(base)

    production = runner.ExperimentRunner.open(
        replace(_config(tmp_path / "production"), allow_test_providers=False),
        visible_provider=_VisibleProvider(),
    )
    with pytest.raises(runner.RunnerIntegrityError, match="non-production provider"):
        production.execute(base)

    resolved = runner.ExperimentRunner.open(
        _config(tmp_path / "resolved"),
        visible_provider=_VisibleProvider(),
    )
    with pytest.raises(runner.RunnerIntegrityError, match="resolved scene digest mismatch"):
        resolved.execute(replace(base, resolved_scene_sha256="a" * 64))
    receipt = resolved.execute(base)
    assert receipt.event["model_receipt"]["resolved_scene_sha256"] == A0_RESOLVED.digest()


def test_custom_spec_visible_fail_closed_guards(tmp_path: Path) -> None:
    def missing_model(spec: dict) -> None:
        spec["experiments"][0]["arms"][0]["model_role"] = "missing_role"

    model_config = _config_with_spec(tmp_path / "model", missing_model)
    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(model_config, visible_provider=_VisibleProvider())

    def no_images(spec: dict) -> None:
        sample = spec["experiments"][0]["samples"][0]
        sample["artifacts"] = [sample["artifacts"][-1]]
        sample["bundle_sha256"] = protocol.canonical_sha256(
            protocol.build_bundle_manifest(REPO_ROOT, sample["artifacts"])
        )

    no_image_config = _config_with_spec(tmp_path / "no-image", no_images)
    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(no_image_config, visible_provider=_VisibleProvider())

    image_budget_config = _config_with_spec(
        tmp_path / "image-budget",
        lambda spec: spec["experiments"][0]["budget"].update(max_input_images_per_case=0),
    )
    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(image_budget_config, visible_provider=_VisibleProvider())

    base_budget_config = _config_with_spec(
        tmp_path / "base-budget",
        lambda spec: spec["experiments"][0]["budget"].update(max_base_vlm_invocations=0),
    )
    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(base_budget_config, visible_provider=_VisibleProvider())


def test_visible_ceiling_and_frozen_prompt_mismatch_stop(tmp_path: Path) -> None:
    base = runner.VisibleInvocation(
        case_id="asset_probe_dustbin_legacy_900",
        arm="A2_typed_abstaining_critic_7b",
        prompt="ceiling",
        prompt_template_version="typed-v1",
        processor_config={},
        seed=21,
        attempt=1,
    )
    ceiling = runner.ExperimentRunner.open(
        _config(tmp_path / "ceiling"),
        visible_provider=_VisibleProvider(),
    )
    with pytest.raises(runner.StudyStoppedError, match="restricted to the sealed test"):
        ceiling.execute(base)


def test_prompt_freeze_cannot_bypass_the_pending_annotation_manifest(tmp_path: Path) -> None:
    session = runner.ExperimentRunner.open(_config(tmp_path))
    freeze = runner.VisiblePromptFreeze(
        prompt="selected",
        prompt_template_version="typed-v1",
        processor_config={},
        selection_evidence_sha256="7" * 64,
    )

    with pytest.raises(runner.RunnerIntegrityError, match="selection evidence and gold seal"):
        session.freeze_visible_prompt(freeze)


def test_prompt_freeze_commits_only_with_a_sealed_runner_owned_evidence_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the public freeze seam with a sealed test fixture, never a replacement spec."""

    frozen_spec = json.loads((STUDY_ROOT / "experiment_spec.json").read_text(encoding="utf-8"))
    experiment_value = frozen_spec["experiments"][0]
    prompt = "Inspect only visible semantics and abstain when uncertain."
    template = "typed-visible-v1"
    processor_config = {"max_new_tokens": 768}
    dev_cases = [
        sample["case_id"] for sample in experiment_value["samples"] if sample["split"] == "dev"
    ]
    all_pass = {name: "pass" for name in experiment_value["visible_checks"]}
    dev_gold = {
        "schema_version": runner.VISIBLE_DEV_GOLD_SCHEMA,
        "study_id": protocol.STUDY_ID,
        "spec_sha256": runner.FROZEN_SPEC_SHA256,
        **runner._annotation_contract_digests(frozen_spec),
        "rows": [{"case_id": case_id, "gold": all_pass} for case_id in dev_cases],
    }
    dev_gold_path = tmp_path / "sealed_dev_gold.json"
    dev_gold_path.write_bytes(protocol.canonical_json_bytes(dev_gold) + b"\n")
    dev_gold_sha = _sha256(dev_gold_path)
    annotation = {
        "schema_version": "vlm_fallback.sealed_test_annotation_manifest.v2",
        "study_id": protocol.STUDY_ID,
        "spec_sha256": runner.FROZEN_SPEC_SHA256,
        "state": "sealed",
        "case_ids": [
            sample["case_id"]
            for sample in frozen_spec["experiments"][0]["samples"]
            if sample["split"] == "test"
        ],
        **runner._annotation_contract_digests(frozen_spec),
        "dev_gold_manifest_sha256": dev_gold_sha,
        "test_annotation_payload_sha256": "b" * 64,
    }
    annotation_path = tmp_path / "sealed_annotation_manifest.json"
    annotation_path.write_bytes(protocol.canonical_json_bytes(annotation) + b"\n")
    annotation_sha = _sha256(annotation_path)
    monkeypatch.setattr(runner, "SEALED_TEST_ANNOTATION_MANIFEST_PATH", annotation_path)
    monkeypatch.setattr(runner, "SEALED_TEST_ANNOTATION_MANIFEST_SHA256", annotation_sha)

    session = runner.ExperimentRunner.open(_config(tmp_path), visible_provider=_VisibleProvider())
    base = session.execute(_a0_invocation(seed=901))
    experiment = session.spec["experiments"][0]
    typed_result = {
        "schema_version": "vlm_fallback.visible_review.v1",
        "checks": all_pass,
        "overall": "pass",
        "corrections": [],
    }
    train_case = next(
        sample["case_id"] for sample in experiment["samples"] if sample["split"] == "train"
    )
    train_receipt = json.loads(json.dumps(base.event))
    train_receipt["event_id"] = "sealed-train-receipt"
    train_receipt["phase"] = "execution.train"
    train_receipt["case_id"] = train_case
    train_receipt["arm"] = "A1_typed_abstaining_critic_3b"
    train_receipt["attempt"] = 1
    train_receipt["input_bindings"].update(
        repair_index=0,
        logical_key=protocol.canonical_sha256(
            {"kind": "sealed-train-fixture", "case_id": train_case}
        ),
        prompt_size_bytes=len(prompt.encode("utf-8")),
    )
    train_receipt["model_receipt"].update(
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        prompt_template_version=template,
        processor_config_sha256=protocol.canonical_sha256(processor_config),
    )
    train_receipt["outputs"]["result"] = typed_result
    train_receipt["gate_results"]["resource_budget"] = "pass"
    train_receipt.pop("previous_event_sha256", None)
    train_receipt.pop("event_sha256", None)
    session._journal.append(train_receipt)
    dev_event_ids: list[str] = []
    for index, case_id in enumerate(dev_cases):
        receipt = json.loads(json.dumps(base.event))
        receipt["event_id"] = f"sealed-dev-receipt-{index}"
        receipt["phase"] = "execution.dev"
        receipt["case_id"] = case_id
        receipt["arm"] = "A1_typed_abstaining_critic_3b"
        receipt["attempt"] = 1
        receipt["input_bindings"]["repair_index"] = 0
        receipt["input_bindings"]["logical_key"] = protocol.canonical_sha256(
            {"kind": "sealed-dev-fixture", "case_id": case_id}
        )
        receipt["input_bindings"]["prompt_size_bytes"] = len(prompt.encode("utf-8"))
        receipt["model_receipt"].update(
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            prompt_template_version=template,
            processor_config_sha256=protocol.canonical_sha256(processor_config),
        )
        receipt["gate_results"]["resource_budget"] = "pass"
        receipt["outputs"]["result"] = typed_result
        receipt.pop("previous_event_sha256", None)
        receipt.pop("event_sha256", None)
        session._journal.append(receipt)
        dev_event_ids.append(receipt["event_id"])

    gold_seal = {
        "schema_version": runner.VISIBLE_GOLD_SEAL_SCHEMA,
        "study_id": session.identity.study_id,
        "spec_sha256": session.identity.spec_sha256,
        "state": "sealed",
        "test_gold_opened": False,
        "annotation_manifest_sha256": annotation_sha,
    }
    gold_seal_path = tmp_path / "gold_seal.json"
    gold_seal_path.write_bytes(protocol.canonical_json_bytes(gold_seal) + b"\n")
    gold_seal_sha = _sha256(gold_seal_path)
    selection = {
        "schema_version": runner.VISIBLE_SELECTION_SCHEMA,
        "study_id": session.identity.study_id,
        "spec_sha256": session.identity.spec_sha256,
        "selected_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_template_version": template,
        "processor_config_sha256": protocol.canonical_sha256(processor_config),
        "selection_order": list(experiment["arms"][1]["selection_order"]),
        "candidate_evaluations": [
            {
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "prompt_template_version": template,
                "processor_config_sha256": protocol.canonical_sha256(processor_config),
                "train_event_ids": [train_receipt["event_id"]],
                "dev_event_ids": dev_event_ids,
                "dev_rows": [
                    {"case_id": case_id, "gold": all_pass, "prediction": all_pass}
                    for case_id in dev_cases
                ],
                "dev_metrics": protocol.visible_metrics(
                    [
                        {"case_id": case_id, "gold": all_pass, "prediction": all_pass}
                        for case_id in dev_cases
                    ]
                ),
                "dev_wall_time_ms": sum(
                    int(
                        session._journal.events[-len(dev_cases) + index]["resource_receipt"][
                            "wall_time_ms"
                        ]
                    )
                    for index in range(len(dev_cases))
                ),
                "prompt_size_bytes": len(prompt.encode("utf-8")),
            }
        ],
        "train_revision_set_sha256": protocol.canonical_sha256(
            [hashlib.sha256(prompt.encode("utf-8")).hexdigest()]
        ),
        "dev_gold_manifest_sha256": dev_gold_sha,
        "gold_seal_sha256": gold_seal_sha,
        "test_gold_opened": False,
    }
    selection_path = tmp_path / "selection.json"
    selection_path.write_bytes(protocol.canonical_json_bytes(selection) + b"\n")
    freeze = runner.VisiblePromptFreeze(
        prompt=prompt,
        prompt_template_version=template,
        processor_config=processor_config,
        selection_evidence_sha256=_sha256(selection_path),
        selection_evidence_path=selection_path,
        gold_seal_path=gold_seal_path,
        gold_seal_sha256=gold_seal_sha,
        dev_gold_manifest_path=dev_gold_path,
    )

    attacks = (
        (
            lambda value: value["candidate_evaluations"][0]["dev_metrics"].update(coverage=0.0),
            "dev metrics mismatch",
        ),
        (
            lambda value: value["candidate_evaluations"][0].update(train_event_ids=[]),
            "train revisions are incomplete",
        ),
        (
            lambda value: value["candidate_evaluations"][0]["dev_rows"][0].update(
                gold={name: "fail" for name in experiment["visible_checks"]}
            ),
            "dev rows are not receipt-bound",
        ),
    )
    for index, (mutate, match) in enumerate(attacks):
        attacked = json.loads(json.dumps(selection))
        mutate(attacked)
        attacked_path = tmp_path / f"selection_attack_{index}.json"
        attacked_path.write_bytes(protocol.canonical_json_bytes(attacked) + b"\n")
        with pytest.raises(runner.RunnerIntegrityError, match=match):
            session.freeze_visible_prompt(
                replace(
                    freeze,
                    selection_evidence_path=attacked_path,
                    selection_evidence_sha256=_sha256(attacked_path),
                )
            )

    committed = session.freeze_visible_prompt(freeze)
    resumed = session.freeze_visible_prompt(freeze)

    assert committed.event["decision"] == "selected_prompt_frozen"
    assert resumed.resumed is True


def test_selected_case_bundle_drift_is_rejected_even_when_inventory_scan_is_disabled(
    tmp_path: Path,
) -> None:
    visible_config = _config_with_spec(
        tmp_path / "visible",
        lambda spec: spec["experiments"][0]["samples"][0].update(bundle_sha256="0" * 64),
    )
    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(
            replace(visible_config, verify_artifacts=False),
            visible_provider=_VisibleProvider(),
        )

    routing_config = _config_with_spec(
        tmp_path / "routing",
        lambda spec: spec["experiments"][1]["samples"][-3].update(bundle_sha256="0" * 64),
    )
    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(
            replace(routing_config, verify_artifacts=False),
            routing_provider=_RoutingProvider(),
        )


def test_routing_rejects_an_alternate_frozen_spec_path(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec_copy = tmp_path / "spec.json"
    spec_copy.write_bytes((STUDY_ROOT / "experiment_spec.json").read_bytes())
    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(
            replace(config, spec_path=spec_copy), routing_provider=_RoutingProvider()
        )


def test_execution_uses_an_immutable_frozen_spec_snapshot(tmp_path: Path) -> None:
    class OverBudgetProvider(_VisibleProvider):
        def invoke(self, request, progress):
            outcome = super().invoke(request, progress)
            return replace(
                outcome,
                decision="oversized",
                raw_response="oversized",
                resource=runner.ResourceUsage(output_tokens=769),
            )

    config = _config(tmp_path)
    provider = OverBudgetProvider()
    session = runner.ExperimentRunner.open(config, visible_provider=provider)

    with pytest.raises(TypeError):
        session.spec["models"][0]["generation"]["max_new_tokens"] = 1000
    with pytest.raises(TypeError):
        session.spec["experiments"][0]["samples"][0]["artifacts"] = []

    invocation = _a0_invocation(seed=41)
    with pytest.raises(runner.StudyStoppedError, match="max_new_tokens"):
        session.execute(invocation)

    assert provider.requests[0].model.revision == ("66285546d2b821cf421d4f5eb2576359d3770cd3")


def test_interrupted_provider_reservation_recovers_without_reinvocation(
    tmp_path: Path,
) -> None:
    class ExitingProvider(_VisibleProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            raise SystemExit(77)

    config = _config(tmp_path)
    exiting = ExitingProvider()
    invocation = _a0_invocation(seed=43)
    first = runner.ExperimentRunner.open(config, visible_provider=exiting)

    with pytest.raises(SystemExit, match="77"):
        first.execute(invocation)

    replacement = _VisibleProvider()
    recovered = runner.ExperimentRunner.open(config, visible_provider=replacement)
    event = recovered._journal.events[-1]

    assert event["decision"] == "provider_call_interrupted_unknown"
    assert event["outputs"]["abstain"] is True
    assert event["error"]["code"] == "interrupted_unknown"
    assert event["resource_receipt"]["usage_known"] is False
    assert event["gate_results"]["study_stopped"] is True
    assert recovered._journal.anchor["stopped"] is True
    with pytest.raises(runner.StudyStoppedError, match="unknown resource usage"):
        recovered.execute(invocation)
    assert replacement.requests == []


def test_interrupted_unknown_budget_replacement_spec_is_rejected_at_open(tmp_path: Path) -> None:
    config = _config_with_spec(
        tmp_path,
        lambda spec: spec["experiments"][0]["budget"].update(max_base_vlm_invocations=1),
    )

    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(config, visible_provider=_VisibleProvider())


def test_a0_rejects_an_arbitrary_prompt_not_derived_from_the_bound_resolved_scene(
    tmp_path: Path,
) -> None:
    provider = _VisibleProvider()
    session = runner.ExperimentRunner.open(
        _config(tmp_path),
        visible_provider=provider,
    )
    invocation = runner.VisibleInvocation(
        case_id="asset_probe_dustbin_legacy_900",
        arm="A0_current_critic_3b",
        prompt="Caller-controlled baseline prompt.",
        prompt_template_version="scene_gen.rendered_critic.build_critic_prompt.v1",
        processor_config={},
        seed=45,
        attempt=1,
        resolved_scene_sha256=A0_RESOLVED.digest(),
        resolved_scene_path=A0_RESOLVED_PATH,
    )

    with pytest.raises(runner.RunnerIntegrityError, match="exactly match the frozen"):
        session.execute(invocation)

    assert provider.requests == []


def test_a0_source_binding_fails_closed_on_missing_invalid_or_unbound_inputs(
    tmp_path: Path,
) -> None:
    session = runner.ExperimentRunner.open(_config(tmp_path))
    base = _a0_invocation(seed=46)
    experiment, _arm, sample, _model, artifacts = session._visible_context(base)
    assert experiment["experiment_id"] == "A_visible_semantic_correction"

    invalid = [
        (replace(base, resolved_scene_path=None), "requires a source-bound"),
        (
            replace(base, resolved_scene_path=REPO_ROOT / "definitely-missing-resolved.json"),
            "cannot be read",
        ),
        (replace(base, resolved_scene_path=REPO_ROOT / "README.md"), "is invalid"),
        (
            replace(base, prompt_template_version="caller-controlled"),
            "template version is not frozen",
        ),
    ]
    outside = tmp_path / "outside.json"
    outside.write_bytes(A0_RESOLVED_PATH.read_bytes())
    invalid.append((replace(base, resolved_scene_path=outside), "must be a repository file"))
    for invocation, match in invalid:
        with pytest.raises(runner.RunnerIntegrityError, match=match):
            session._validate_a0_prompt_binding(
                invocation,
                sample=sample,
                artifacts=artifacts,
            )

    with pytest.raises(runner.RunnerIntegrityError, match="not bound to the frozen sample"):
        session._validate_a0_prompt_binding(
            base,
            sample={**dict(sample), "task_context": "Different task."},
            artifacts=artifacts,
        )
    with pytest.raises(runner.RunnerIntegrityError, match="not bound to the frozen sample"):
        session._validate_a0_prompt_binding(
            base,
            sample=sample,
            artifacts=[{"path": "experiment_spec.json"}],
        )
    non_object_json = tmp_path / "non-object.json"
    non_object_json.write_text("[]\n", encoding="utf-8")
    session._validate_a0_prompt_binding(
        base,
        sample=sample,
        artifacts=[
            *artifacts,
            {"path": "experiment_spec.json"},
            {"path": str(non_object_json)},
            {"path": "does-not-exist.json"},
        ],
    )


def test_budget_violation_terminal_receipt_atomically_stops_after_crash(
    tmp_path: Path,
) -> None:
    class OverBudgetProvider(_VisibleProvider):
        def invoke(self, request, progress):
            outcome = super().invoke(request, progress)
            return replace(
                outcome,
                decision="oversized",
                raw_response="oversized",
                resource=runner.ResourceUsage(output_tokens=769),
            )

    config = _config(tmp_path)
    provider = OverBudgetProvider()
    session = runner.ExperimentRunner.open(config, visible_provider=provider)
    invocation = _a0_invocation(seed=47)

    def crash_before_separate_stop_event(**kwargs):
        raise RuntimeError("simulated crash after terminal receipt")

    session._record_stop = crash_before_separate_stop_event  # type: ignore[method-assign]
    with pytest.raises(runner.StudyStoppedError, match="max_new_tokens"):
        session.execute(invocation)

    replacement = _VisibleProvider()
    recovered = runner.ExperimentRunner.open(config, visible_provider=replacement)
    assert recovered._journal.anchor["stopped"] is True
    assert "max_new_tokens" in recovered._journal.anchor["stop_reason"]
    with pytest.raises(runner.StudyStoppedError, match="max_new_tokens"):
        recovered.execute(replace(invocation, attempt=2))
    assert replacement.requests == []


def test_oracle_uses_a_separate_capability_factory_and_blinded_provider_ids(
    tmp_path: Path,
) -> None:
    deployable_instances: list[_RoutingProvider] = []
    oracle_instances: list[_RoutingProvider] = []

    def deployable_factory(arm: str):
        provider = _RoutingProvider()
        provider.identity = replace(provider.identity, provider_id=f"deployable-{arm}")
        deployable_instances.append(provider)
        return provider

    def oracle_factory(arm: str):
        provider = _RoutingProvider()
        provider.identity = replace(provider.identity, provider_id=f"oracle-{arm}")
        oracle_instances.append(provider)
        return provider

    config = _config(tmp_path)
    capability = runner.OracleRoutingCapability(
        study_id=protocol.STUDY_ID,
        spec_sha256=runner.FROZEN_SPEC_SHA256,
        purpose="descriptive_ceiling_only",
    )
    session = runner.ExperimentRunner.open(
        config,
        routing_provider_factory=deployable_factory,
        oracle_routing_provider_factory=oracle_factory,
        oracle_capability=capability,
    )

    oracle_receipt = session.execute(_routing_invocation(arm="B3_oracle_route_ceiling", attempt=1))
    deployable_receipt = session.execute(
        _routing_invocation(arm="B2_typed_route_and_prompt_repair", attempt=1)
    )

    assert len(oracle_instances) == 1
    assert len(deployable_instances) == 1
    assert oracle_instances[0] is not deployable_instances[0]
    assert oracle_instances[0].requests[0].gold_route == "abstain_unsupported_task_api"
    deployable_request = deployable_instances[0].requests[0]
    assert deployable_request.gold_route is None
    assert deployable_request.case_id.startswith("blind-case-")
    assert "drawer" not in deployable_request.case_id
    assert deployable_request.invocation_id.startswith("blind-call-")
    assert deployable_request.invocation_id != deployable_receipt.invocation_id
    assert oracle_receipt.event["gate_results"]["gold_route_exposed"] is True
    assert deployable_receipt.event["gate_results"]["gold_route_exposed"] is False


def test_prompt_freeze_fails_closed_without_selection_evidence_and_gold_seal(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    session = runner.ExperimentRunner.open(config)
    freeze = runner.VisiblePromptFreeze(
        prompt="unproven candidate",
        prompt_template_version="typed-v1",
        processor_config={},
        selection_evidence_sha256="7" * 64,
    )

    with pytest.raises(runner.RunnerIntegrityError, match="selection evidence and gold seal"):
        session.freeze_visible_prompt(freeze)

    assert all(
        event.get("decision") != "selected_prompt_frozen" for event in session._journal.events
    )


def test_prompt_freeze_rejects_a_gold_seal_not_committed_by_the_frozen_spec(
    tmp_path: Path,
) -> None:
    config = _config_with_test_gold_commitment(tmp_path)

    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(config, visible_provider=_VisibleProvider())


def test_prompt_freeze_rejects_unresolved_dev_selection_claims(tmp_path: Path) -> None:
    config = _config_with_test_gold_commitment(tmp_path)
    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(config)


def test_prompt_freeze_rejects_gold_and_dev_evidence_contract_attacks(
    tmp_path: Path,
) -> None:
    config = _config_with_test_gold_commitment(tmp_path)
    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(config, visible_provider=_VisibleProvider())


@pytest.mark.parametrize("commitment", ["z" * 64, "A" * 64])
def test_prompt_freeze_rejects_invalid_frozen_gold_commitments(
    tmp_path: Path,
    commitment: str,
) -> None:
    config = _config_with_spec(
        tmp_path,
        lambda spec: spec["experiments"][0]["gold_annotation"].update(
            sealed_test_annotation_manifest_sha256=commitment
        ),
    )
    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(config)


def test_typed_visible_arm_rejects_incomplete_output_and_stops_physical_claim(
    tmp_path: Path,
) -> None:
    class IncompleteVisibleProvider(_VisibleProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            result = {"schema_version": "vlm_fallback.visible_review.v1"}
            return runner.ProviderOutcome(
                decision="incomplete",
                result=result,
                raw_response=protocol.canonical_json_bytes(result),
                parsed_response=result,
                abstained=False,
                resource=runner.ResourceUsage(),
            )

    incomplete_config = _config(tmp_path / "incomplete")
    incomplete = IncompleteVisibleProvider()
    incomplete_session = runner.ExperimentRunner.open(
        incomplete_config,
        visible_provider=incomplete,
    )
    invocation = runner.VisibleInvocation(
        case_id="asset_probe_dustbin_legacy_900",
        arm="A1_typed_abstaining_critic_3b",
        prompt="Return the exact typed visible-review schema.",
        prompt_template_version="typed-v1",
        processor_config={},
        seed=53,
        attempt=1,
    )

    incomplete_receipt = incomplete_session.execute(invocation)
    assert incomplete_receipt.event["decision"] == "provider_call_failed"
    assert incomplete_receipt.event["error"]["code"] == "provider_contract_error"

    class HiddenPhysicalClaim(_VisibleProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            return runner.ProviderOutcome(
                decision="typed-visible",
                result={"physical_pass": True},
                raw_response='{"physical_pass":true}',
                parsed_response={"physical_pass": True},
                abstained=False,
                resource=runner.ResourceUsage(),
                claims_physical_pass=False,
            )

    physical_config = _config(tmp_path / "physical")
    physical = HiddenPhysicalClaim()
    physical_session = runner.ExperimentRunner.open(
        physical_config,
        visible_provider=physical,
    )
    with pytest.raises(runner.StudyStoppedError, match="physical pass"):
        physical_session.execute(invocation)
    assert physical_session._journal.anchor["stopped"] is True


def test_arm_specific_visible_output_contract_rejects_every_incomplete_shape() -> None:
    visible_checks = (
        "object_presence",
        "object_identity",
        "object_count",
        "orientation",
        "table_contact",
        "visible_penetration_or_floating",
        "spatial_relation",
        "overall_prompt_match",
    )

    def outcome(result, *, abstained=False, parsed=True):
        return runner.ProviderOutcome(
            decision="test",
            result=result,
            raw_response=b"{}",
            parsed_response=result if parsed else None,
            abstained=abstained,
            resource=runner.ResourceUsage(),
        )

    a0 = {
        "schema_version": "robotwin.rendered_scene_critic.v1",
        "status": "pass",
        "checks": [
            {"name": name, "status": "pass", "evidence": "visible"}
            for name in (
                "object_presence",
                "support_relation",
                "penetration_or_floating",
                "articulation_state",
                "overall_prompt_match",
            )
        ],
    }
    typed = {
        "schema_version": "vlm_fallback.visible_review.v1",
        "checks": {name: "pass" for name in visible_checks},
        "overall": "pass",
        "corrections": [],
    }
    invalid = [
        ("A0_current_critic_3b", outcome({**a0, "status": "warning"}), "status"),
        ("A0_current_critic_3b", outcome({**a0, "checks": []}), "every frozen"),
        (
            "A0_current_critic_3b",
            outcome({**a0, "checks": [*a0["checks"], a0["checks"][0]]}),
            "exactly once",
        ),
        (
            "A0_current_critic_3b",
            outcome(
                {
                    **a0,
                    "checks": [
                        {**a0["checks"][0], "status": "invented"},
                        *a0["checks"][1:],
                    ],
                }
            ),
            "check schema",
        ),
        ("A0_current_critic_3b", outcome(a0, abstained=True), "cannot abstain"),
        ("A0_current_critic_3b", outcome(a0, parsed=False), "parsed response"),
        (
            "A0_current_critic_3b",
            outcome({**a0, "status": "fail"}),
            "overall status is inconsistent",
        ),
        (
            "A1_typed_abstaining_critic_3b",
            outcome({**typed, "checks": []}),
            "every frozen check",
        ),
        (
            "A1_typed_abstaining_critic_3b",
            outcome({**typed, "checks": {**typed["checks"], "orientation": "unknown"}}),
            "check status",
        ),
        (
            "A1_typed_abstaining_critic_3b",
            outcome({**typed, "overall": "unknown"}),
            "overall status",
        ),
        (
            "A1_typed_abstaining_critic_3b",
            outcome({**typed, "corrections": [{}]}),
            "corrections",
        ),
        (
            "A1_typed_abstaining_critic_3b",
            outcome(typed, abstained=True),
            "abstain flag",
        ),
        (
            "A1_typed_abstaining_critic_3b",
            outcome(typed, parsed=False),
            "parsed response",
        ),
        (
            "A1_typed_abstaining_critic_3b",
            outcome({**typed, "checks": {**typed["checks"], "orientation": "fail"}}),
            "overall status is inconsistent",
        ),
    ]
    for arm, candidate, match in invalid:
        with pytest.raises(runner.ProviderContractError, match=match):
            runner.ExperimentRunner._validate_visible_arm_outcome(
                candidate,
                arm=arm,
                visible_checks=visible_checks,
            )

    with pytest.raises(runner.ProviderPhysicalClaimError, match="physical pass"):
        runner.ExperimentRunner._validate_visible_arm_outcome(
            outcome({"nested": {"physical_pass": {"claimed": True}}}),
            arm="A1_typed_abstaining_critic_3b",
            visible_checks=visible_checks,
        )


def test_prompt_rewrite_requires_runner_owned_intent_verification(tmp_path: Path) -> None:
    class DishonestRouter(_RoutingProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            result = {
                "route": "repair_visible_prompt",
                "revised_prompt": "Drop the drawer constraint and put the mug anywhere.",
                "intent_sha256_before": "8" * 64,
                "intent_sha256_after": "8" * 64,
            }
            return runner.ProviderOutcome(
                decision="rewrite",
                result=result,
                raw_response="{}",
                parsed_response=result,
                abstained=False,
                resource=runner.ResourceUsage(prompt_rewrites=1),
            )

    config = _config(tmp_path)
    provider = DishonestRouter()
    session = runner.ExperimentRunner.open(config, routing_provider=provider)
    receipt = session.execute(
        _routing_invocation(
            typed_failure={"code": "visible_semantic_mismatch"},
            resource_reservation=runner.ResourceUsage(prompt_rewrites=1),
        )
    )

    assert receipt.event["decision"] == "provider_call_failed"
    assert receipt.event["error"]["code"] == "provider_contract_error"
    assert receipt.event["outputs"]["abstain"] is True
    assert receipt.event["gate_results"]["intent_preserved"] is False


def test_prompt_rewrite_rejects_relation_role_reversal_with_the_same_tokens(
    tmp_path: Path,
) -> None:
    original = "Place a red can to the left of a plastic basket near the center."
    revised = "Place a plastic basket to the left of a red can near the center."

    class ReversingRouter(_RoutingProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            result = {
                "route": "repair_visible_prompt",
                "revised_prompt": revised,
                "intent_sha256_before": runner.canonical_intent_sha256(original),
                "intent_sha256_after": runner.canonical_intent_sha256(revised),
            }
            return runner.ProviderOutcome(
                decision="rewrite",
                result=result,
                raw_response=protocol.canonical_json_bytes(result),
                parsed_response=result,
                abstained=False,
                resource=runner.ResourceUsage(prompt_rewrites=1),
            )

    session = runner.ExperimentRunner.open(_config(tmp_path), routing_provider=ReversingRouter())
    receipt = session.execute(
        _routing_invocation(
            case_id="prompt_matrix:red_can_left_of_basket:seed_7",
            original_prompt=original,
            resource_reservation=runner.ResourceUsage(prompt_rewrites=1),
        )
    )

    assert receipt.event["decision"] == "provider_call_failed"
    assert receipt.event["error"]["code"] == "provider_contract_error"
    assert receipt.event["gate_results"]["intent_preserved"] is False


def test_runner_owned_intent_verifier_and_routing_schema_fail_closed(tmp_path: Path) -> None:
    original = "Open the drawer and place the mug inside."

    def outcome(result):
        return runner.ProviderOutcome(
            decision="route",
            result=result,
            raw_response=b"{}",
            parsed_response=result,
            abstained=False,
            resource=runner.ResourceUsage(),
        )

    no_verifier = runner.ExperimentRunner.open(_config(tmp_path / "none"))
    base_result = {
        "route": "repair_visible_prompt",
        "revised_prompt": "Reworded prompt.",
        "intent_sha256_before": "4" * 64,
        "intent_sha256_after": "4" * 64,
    }
    with pytest.raises(runner.ProviderContractError, match="do not match"):
        no_verifier._verify_routing_intent(
            outcome=outcome(base_result),
            original_prompt=original,
            revised_prompt="Reworded prompt.",
        )
    with pytest.raises(runner.ProviderContractError, match="exact frozen schema"):
        no_verifier._verify_routing_intent(
            outcome=outcome({"route": "accept_existing_compile"}),
            original_prompt=original,
            revised_prompt=None,
        )
    physical = {
        **base_result,
        "runtime_pass": {"claimed": True},
    }
    with pytest.raises(runner.ProviderContractError, match="exact frozen schema"):
        no_verifier._verify_routing_intent(
            outcome=outcome(physical),
            original_prompt=original,
            revised_prompt="Reworded prompt.",
        )
    nested_physical = {
        **base_result,
        "route": {"runtime_pass": True},
    }
    with pytest.raises(runner.ProviderPhysicalClaimError, match="physical pass"):
        no_verifier._verify_routing_intent(
            outcome=outcome(nested_physical),
            original_prompt=original,
            revised_prompt="Reworded prompt.",
        )


def test_runner_rejects_caller_rehashed_replacement_spec(tmp_path: Path) -> None:
    """The public open seam must not accept a caller-selected spec digest."""

    config = _config_with_spec(
        tmp_path,
        lambda spec: spec["experiments"][0]["budget"].update(max_new_tokens=999),
    )

    with pytest.raises(runner.RunnerIntegrityError, match="checked-in frozen spec"):
        runner.ExperimentRunner.open(config)


def test_checked_in_pending_annotation_manifest_is_hash_bound_and_blocks_test_unlock(
    tmp_path: Path,
) -> None:
    manifest = json.loads(runner.SEALED_TEST_ANNOTATION_MANIFEST_PATH.read_text())
    assert _sha256(runner.SEALED_TEST_ANNOTATION_MANIFEST_PATH) == (
        runner.SEALED_TEST_ANNOTATION_MANIFEST_SHA256
    )
    assert manifest["state"] == "pending_blinded_annotation"
    experiment = json.loads((STUDY_ROOT / "experiment_spec.json").read_text())["experiments"][0]
    gold = experiment["gold_annotation"]
    assert manifest["label_contract_sha256"] == protocol.canonical_sha256(
        {
            "visible_checks": experiment["visible_checks"],
            "statuses": gold["statuses"],
            "insufficient_view_maps_to": gold["insufficient_view_maps_to"],
        }
    )
    assert manifest["rater_contract_sha256"] == protocol.canonical_sha256(
        {"raters": gold["raters"], "blinded_to": gold["blinded_to"]}
    )
    assert manifest["adjudication_contract_sha256"] == protocol.canonical_sha256(
        {
            "adjudication": gold["adjudication"],
            "agreement_report": gold["agreement_report"],
        }
    )
    assert manifest["dev_gold_manifest_sha256"] is None
    assert manifest["test_annotation_payload_sha256"] is None
    session = runner.ExperimentRunner.open(_config(tmp_path))
    seal = {
        "schema_version": runner.VISIBLE_GOLD_SEAL_SCHEMA,
        "study_id": session.identity.study_id,
        "spec_sha256": session.identity.spec_sha256,
        "state": "sealed",
        "test_gold_opened": False,
        "annotation_manifest_sha256": runner.SEALED_TEST_ANNOTATION_MANIFEST_SHA256,
    }
    seal_path = tmp_path / "seal.json"
    seal_path.write_bytes(protocol.canonical_json_bytes(seal) + b"\n")
    freeze = runner.VisiblePromptFreeze(
        prompt="candidate",
        prompt_template_version="typed-v1",
        processor_config={},
        selection_evidence_sha256="0" * 64,
        selection_evidence_path=tmp_path / "missing-selection.json",
        gold_seal_path=seal_path,
        gold_seal_sha256=_sha256(seal_path),
    )
    with pytest.raises(runner.RunnerIntegrityError, match="selection evidence"):
        session.freeze_visible_prompt(freeze)


def test_open_does_not_allow_an_injected_intent_verifier(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="intent_verifier"):
        runner.ExperimentRunner.open(  # type: ignore[call-arg]
            _config(tmp_path), intent_verifier=object()
        )


def test_b2_and_b3_reject_shared_provider_authority(tmp_path: Path) -> None:
    def deployable_factory(_arm: str) -> _RoutingProvider:
        return _RoutingProvider()

    def oracle_factory(_arm: str) -> _RoutingProvider:
        return _RoutingProvider()

    session = runner.ExperimentRunner.open(
        _config(tmp_path),
        routing_provider_factory=deployable_factory,
        oracle_routing_provider_factory=oracle_factory,
        oracle_capability=runner.OracleRoutingCapability(
            study_id=protocol.STUDY_ID,
            spec_sha256=runner.FROZEN_SPEC_SHA256,
            purpose="descriptive_ceiling_only",
        ),
    )
    session.execute(_routing_invocation(arm="B2_typed_route_and_prompt_repair"))
    with pytest.raises(runner.RunnerIntegrityError, match="authority must be disjoint"):
        session.execute(_routing_invocation(arm="B3_oracle_route_ceiling"))


def test_b2_and_b3_authority_separation_survives_runner_reopen(tmp_path: Path) -> None:
    config = _config(tmp_path)
    deployable = _RoutingProvider()
    first = runner.ExperimentRunner.open(
        config,
        routing_provider_factory=lambda _arm: deployable,
    )
    first.execute(_routing_invocation(arm="B2_typed_route_and_prompt_repair"))

    oracle = _RoutingProvider()
    reopened = runner.ExperimentRunner.open(
        config,
        oracle_routing_provider_factory=lambda _arm: oracle,
        oracle_capability=runner.OracleRoutingCapability(
            study_id=protocol.STUDY_ID,
            spec_sha256=runner.FROZEN_SPEC_SHA256,
            purpose="descriptive_ceiling_only",
        ),
    )
    with pytest.raises(runner.RunnerIntegrityError, match="authority must be disjoint"):
        reopened.execute(_routing_invocation(arm="B3_oracle_route_ceiling"))
    assert oracle.requests == []


def test_provider_identity_is_snapshotted_before_and_verified_after_call(
    tmp_path: Path,
) -> None:
    class IdentitySwitchingRouter(_RoutingProvider):
        def invoke(self, request, progress):
            original = self.identity
            self.identity = replace(
                original,
                revision="switched-after-reservation",
                implementation_sha256="9" * 64,
            )
            return super().invoke(request, progress)

    provider = IdentitySwitchingRouter()
    original = provider.identity
    session = runner.ExperimentRunner.open(_config(tmp_path), routing_provider=provider)

    receipt = session.execute(_routing_invocation())

    assert receipt.event["decision"] == "provider_call_failed"
    assert receipt.event["error"]["code"] == "provider_contract_error"
    assert receipt.event["model_receipt"]["provider_revision"] == original.revision
    assert receipt.event["model_receipt"]["provider_implementation_sha256"] == (
        original.implementation_sha256
    )


def test_blinded_routing_arms_reject_opaque_annotation_lookup_material(tmp_path: Path) -> None:
    session = runner.ExperimentRunner.open(_config(tmp_path), routing_provider=_RoutingProvider())
    with pytest.raises(ValueError, match="cannot contain gold"):
        session.execute(
            _routing_invocation(
                typed_failure={"code": "missing_task_api", "annotation_manifest": "opaque"}
            )
        )


def test_blinded_routing_rejects_gold_hints_in_free_text_and_nested_values(
    tmp_path: Path,
) -> None:
    provider = _RoutingProvider()
    session = runner.ExperimentRunner.open(_config(tmp_path), routing_provider=provider)
    attacks = (
        _routing_invocation(routing_instruction="The gold route is reuse_asset."),
        _routing_invocation(
            typed_failure={
                "code": "missing_task_api",
                "detail": ["oracle answer: abstain_unsupported_task_api"],
            }
        ),
        _routing_invocation(
            arm="B1_generic_prompt_repair",
            untyped_failure_summary="Expected route: generate_asset_on_catalog_miss",
        ),
    )

    for invocation in attacks:
        with pytest.raises(ValueError, match="blinded gold material"):
            session.execute(invocation)
    assert provider.requests == []


def test_prompt_rewrite_budget_accumulates_across_attempts_for_case_and_arm(
    tmp_path: Path,
) -> None:
    class RewritingRouter(_RoutingProvider):
        def invoke(self, request, progress):
            self.requests.append(request)
            revised = "Place the mug inside the drawer and open."
            result = {
                "route": "repair_visible_prompt",
                "revised_prompt": revised,
                "intent_sha256_before": runner.canonical_intent_sha256(request.original_prompt),
                "intent_sha256_after": runner.canonical_intent_sha256(revised),
            }
            return runner.ProviderOutcome(
                decision="rewrite",
                result=result,
                raw_response=protocol.canonical_json_bytes(result),
                parsed_response=result,
                abstained=False,
                resource=runner.ResourceUsage(prompt_rewrites=1),
            )

    provider = RewritingRouter()
    session = runner.ExperimentRunner.open(_config(tmp_path), routing_provider=provider)
    first = _routing_invocation(
        attempt=1,
        resource_reservation=runner.ResourceUsage(prompt_rewrites=1),
    )
    session.execute(first)
    with pytest.raises(runner.StudyStoppedError, match="max_prompt_rewrites_per_case_arm"):
        session.execute(replace(first, attempt=2))
    assert len(provider.requests) == 1
    event = session._journal.events[-1]
    assert event["gate_results"]["study_stopped"] is True
    assert event["decision"] == "protocol_stopped"
    assert event["expensive_execution_started"] is False


def test_global_routing_budget_is_reserved_before_the_provider_boundary(
    tmp_path: Path,
) -> None:
    class CompileProvider(_RoutingProvider):
        def invoke(self, request, progress):
            outcome = super().invoke(request, progress)
            return replace(
                outcome,
                resource=runner.ResourceUsage(
                    compile_attempts=request.resource_reservation.compile_attempts
                ),
            )

    provider = CompileProvider()
    session = runner.ExperimentRunner.open(_config(tmp_path), routing_provider=provider)
    first = _routing_invocation(
        attempt=1,
        resource_reservation=runner.ResourceUsage(compile_attempts=216),
    )
    session.execute(first)

    with pytest.raises(runner.StudyStoppedError, match="max_total_compile_attempts"):
        session.execute(
            replace(
                first,
                attempt=2,
                resource_reservation=runner.ResourceUsage(compile_attempts=1),
            )
        )
    assert len(provider.requests) == 1
    assert session._journal.events[-1]["expensive_execution_started"] is False


def test_natural_language_physics_claim_stops_in_the_receipt_transaction(tmp_path: Path) -> None:
    class PhysicalProseProvider(_VisibleProvider):
        def invoke(self, request, progress):
            outcome = super().invoke(request, progress)
            result = dict(outcome.result)
            result["checks"] = [
                {**item, "evidence": "Physics passed in the simulation."}
                if item["name"] == "object_presence"
                else item
                for item in result["checks"]
            ]
            return replace(
                outcome,
                result=result,
                raw_response=protocol.canonical_json_bytes(result),
                parsed_response=result,
            )

    session = runner.ExperimentRunner.open(
        _config(tmp_path), visible_provider=PhysicalProseProvider()
    )
    with pytest.raises(runner.StudyStoppedError, match="physical pass"):
        session.execute(_a0_invocation(seed=987))
    event = session._journal.events[-1]
    assert event["decision"] == "provider_call_failed"
    assert event["gate_results"]["study_stopped"] is True
