from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest

import self_improving.system2_compile_turn as system2_compile_turn_module
from scene_gen.catalog import AssetCatalog
from self_improving.harness.application import (
    CompileApplicationSettings,
    create_compile_application,
)
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas import (
    RunState,
    RuntimeConfig,
    Text2EnvCompileOutput,
    Text2EnvReplayInput,
)
from self_improving.harness.system2.domain import TrustedWorldState, build_state_delta
from self_improving.harness.system2.history import (
    publish_planner_history_authority,
    verify_planner_history_authority,
)
from self_improving.harness.system2.planner import (
    PlannerProviderIdentity,
    PlannerProviderResponse,
    PlannerUsage,
)
from self_improving.system2_compile_turn import System2CompileTurn
from self_improving.system2_replay_handoff import (
    PreparedSystem2Replay,
    System2ReplayHandoff,
    System2ReplayHandoffError,
)


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


@dataclass
class _CompileDecisionProvider:
    @property
    def identity(self) -> PlannerProviderIdentity:
        return PlannerProviderIdentity(
            provider_id="fixture_local",
            model_id="fixture/compile-planner",
            model_revision="revision-1",
            model_snapshot_sha256="1" * 64,
            implementation_sha256="2" * 64,
            inference={"do_sample": False},
            network_access=False,
        )

    def invoke(
        self,
        prompt: bytes,
        progress: Callable[[str], None],
    ) -> PlannerProviderResponse:
        progress("inference")
        context = json.loads(prompt)["context"]
        compile_input = next(
            fact["value"] for fact in context["facts"] if fact["key"] == "inputs.compile"
        )
        decision = {
            "schema_version": "harness.planner_decision.v1",
            "base_state_sha256": context["world_state_sha256"],
            "context_sha256": context["context_sha256"],
            "action": "invoke_skill",
            "skill_ref": "text2env.compile@1.0.0",
            "parameters": compile_input,
            "observation_keys": [],
            "stop_reason": None,
            "summary": "Compile the exact trusted input.",
        }
        return PlannerProviderResponse(
            raw=_canonical_json(decision),
            usage=PlannerUsage(
                input_tokens=10,
                output_tokens=5,
                wall_time_ms=2,
                peak_vram_bytes=None,
                finish_reason="stop",
            ),
        )


def _compile_turn(
    tmp_path: Path,
    *,
    generate_missing_assets: bool = True,
    input_root: Path | None = None,
):
    inputs = tmp_path if input_root is None else input_root
    catalogs = inputs / "catalogs"
    assets = inputs / "external-assets"
    catalogs.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    catalog_path = catalogs / "empty.json"
    catalog = AssetCatalog(
        robotwin_root=str(inputs / "RoboTwin"),
        objects_root=str(assets / "objects"),
        entries=(),
    )
    catalog_path.write_text(
        json.dumps(
            catalog.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    application = create_compile_application(
        CompileApplicationSettings(
            state_root=tmp_path / "state",
            external_catalog_roots=(catalogs,),
            allowed_asset_roots=(assets,),
            admission_date=date(2026, 9, 2),
            asset_library_root=tmp_path / "asset-library",
        )
    )
    result = System2CompileTurn(
        application=application,
        provider=_CompileDecisionProvider(),
    ).execute(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
        asset_catalog_path=catalog_path,
        generate_missing_assets=generate_missing_assets,
    )
    return LocalArtifactStore(application.artifact_root), result


def _rehash_state_with_changed_package(state: TrustedWorldState) -> TrustedWorldState:
    facts = []
    for fact in state.facts:
        if fact.key == "environment.package":
            changed = dict(fact.value)
            changed["seed"] = 78
            fact = fact.model_copy(update={"value": changed})
        facts.append(fact)
    identity = {
        "domain": "harness.trusted_world_state.identity.v1",
        "schema_version": "harness.trusted_world_state.v1",
        "version": state.version,
        "predecessor_state_sha256": state.predecessor_state_sha256,
        "as_of": state.as_of.isoformat(),
        "facts": [fact.model_dump(mode="json") for fact in facts],
    }
    digest = hashlib.sha256(_canonical_json(identity)).hexdigest()
    return TrustedWorldState.model_validate(
        {
            **state.model_dump(mode="python", exclude={"facts", "state_sha256"}),
            "facts": facts,
            "state_sha256": digest,
        }
    )


def _cas_objects(store: LocalArtifactStore) -> frozenset[str]:
    root = store.root / "sha256"
    return frozenset(path.name for path in root.glob("*/*") if path.is_file())


def _merge_cas(source: LocalArtifactStore, destination: LocalArtifactStore) -> None:
    shutil.copytree(source.root / "sha256", destination.root / "sha256", dirs_exist_ok=True)


def test_replay_handoff_rejects_rehashed_world_package_without_writing_artifacts(
    tmp_path: Path,
) -> None:
    store, compile_result = _compile_turn(tmp_path)
    changed_state = _rehash_state_with_changed_package(compile_result.world_state)
    coordinated_tamper = compile_result.__class__(
        world_state=changed_state,
        history_authority=compile_result.history_authority,
        tool_result=compile_result.tool_result,
    )
    before = _cas_objects(store)

    with pytest.raises(System2ReplayHandoffError) as raised:
        System2ReplayHandoff(
            artifact_store=store,
            scratch_root=tmp_path / "handoff",
        ).prepare(
            compile_result=coordinated_tamper,
            runtime_config=RuntimeConfig(),
        )

    assert raised.value.reason == "compile_history_mismatch"
    assert _cas_objects(store) == before


def test_replay_handoff_projects_verified_compile_evidence_into_canonical_replay_input(
    tmp_path: Path,
) -> None:
    store, compile_result = _compile_turn(tmp_path)
    before = _cas_objects(store)
    runtime_config = RuntimeConfig(
        precheck_steps=2,
        settle_steps=600,
        contact_window_steps=90,
        video_frames=80,
        fps=10,
    )

    prepared = System2ReplayHandoff(
        artifact_store=store,
        scratch_root=tmp_path / "handoff",
    ).prepare(
        compile_result=compile_result,
        runtime_config=runtime_config,
    )

    assert type(prepared) is PreparedSystem2Replay
    assert type(prepared.replay_input) is Text2EnvReplayInput
    assert prepared.replay_input.runtime_config == runtime_config
    package = prepared.replay_input.environment_package
    assert package.seed == 77
    assert package.producer_skill_ref == "text2env.compile@1.0.0"
    assert package.package_id == package.resolved_scene_sha256

    package_ref = prepared.environment_package_ref
    assert package_ref.name == "environment_package"
    assert package_ref.uri == f"artifact://sha256/{package_ref.sha256}"
    assert package_ref.media_type == "application/json"
    assert package_ref.schema_version == "harness.environment_package.v1"
    assert store.resolve(package_ref).path.read_bytes() == _canonical_json(
        package.model_dump(mode="json")
    )

    provenance_ref = prepared.request_provenance_ref
    assert provenance_ref.name == "text2env_request_provenance"
    assert provenance_ref.uri == f"artifact://sha256/{provenance_ref.sha256}"
    assert provenance_ref.media_type == "application/json"
    assert provenance_ref.schema_version == "harness.text2env_request_provenance.v1"
    provenance_bytes = store.resolve(provenance_ref).path.read_bytes()
    provenance = json.loads(provenance_bytes)
    run_state = RunState.model_validate_json(
        store.resolve(compile_result.tool_result.run_state).path.read_bytes()
    )
    assert provenance == {
        "compile_history_authority": compile_result.history_authority.model_dump(mode="json"),
        "compile_invocation_digest": run_state.invocation_digest,
        "compile_run_id": str(run_state.run_id),
        "compile_skill_ref": "text2env.compile@1.0.0",
        "compile_trusted_receipt": compile_result.tool_result.trusted_receipt.model_dump(
            mode="json"
        ),
        "compile_world_state_sha256": compile_result.world_state.state_sha256,
        "environment_package": package_ref.model_dump(mode="json"),
        "request": "Place a purple hexagonal pedestal on the table.",
        "schema_version": "harness.text2env_request_provenance.v1",
        "seed": 77,
    }
    assert provenance_bytes == _canonical_json(provenance)
    assert _cas_objects(store) - before == {package_ref.sha256, provenance_ref.sha256}
    assert not any((tmp_path / "handoff").iterdir())


@pytest.mark.parametrize("blocked", [False, True])
def test_replay_handoff_rejects_compile_outcomes_without_success_history(
    tmp_path: Path,
    blocked: bool,
) -> None:
    store, compile_result = _compile_turn(
        tmp_path,
        generate_missing_assets=not blocked,
    )
    if not blocked:
        compile_result = compile_result.__class__(
            world_state=compile_result.world_state,
            history_authority=None,
            tool_result=compile_result.tool_result,
        )
    before = _cas_objects(store)

    with pytest.raises(System2ReplayHandoffError) as raised:
        System2ReplayHandoff(
            artifact_store=store,
            scratch_root=tmp_path / "handoff",
        ).prepare(
            compile_result=compile_result,
            runtime_config=RuntimeConfig(),
        )

    assert raised.value.reason == "compile_not_succeeded"
    assert _cas_objects(store) == before


@pytest.mark.parametrize(
    ("swap", "expected_reason"),
    [
        ("history", "compile_history_mismatch"),
        ("receipt", "compile_result_mismatch"),
    ],
)
def test_replay_handoff_rejects_evidence_swapped_between_compile_turns(
    tmp_path: Path,
    swap: str,
    expected_reason: str,
) -> None:
    shared_inputs = tmp_path / "shared-inputs"
    store_a, result_a = _compile_turn(tmp_path / "a", input_root=shared_inputs)
    store_b, result_b = _compile_turn(tmp_path / "b", input_root=shared_inputs)
    _merge_cas(store_b, store_a)
    swapped = result_a.__class__(
        world_state=result_a.world_state,
        history_authority=(
            result_b.history_authority if swap == "history" else result_a.history_authority
        ),
        tool_result=(result_b.tool_result if swap == "receipt" else result_a.tool_result),
    )
    before = _cas_objects(store_a)

    with pytest.raises(System2ReplayHandoffError) as raised:
        System2ReplayHandoff(
            artifact_store=store_a,
            scratch_root=tmp_path / "handoff",
        ).prepare(
            compile_result=swapped,
            runtime_config=RuntimeConfig(),
        )

    assert raised.value.reason == expected_reason
    assert _cas_objects(store_a) == before


@pytest.mark.parametrize(
    ("damage", "expected_reason"),
    [
        ("missing_history", "compile_history_mismatch"),
        ("missing_invocation", "compile_receipt_invalid"),
        ("corrupt_run_state", "compile_receipt_invalid"),
        ("corrupt_package_member", "compile_receipt_invalid"),
    ],
)
def test_replay_handoff_fails_closed_when_compile_cas_closure_is_unavailable(
    tmp_path: Path,
    damage: str,
    expected_reason: str,
) -> None:
    store, compile_result = _compile_turn(tmp_path)
    if damage == "missing_history":
        assert compile_result.history_authority is not None
        damaged_path = store.resolve(compile_result.history_authority).path
        damaged_path.unlink()
    elif damage == "missing_invocation":
        assert compile_result.tool_result.invocation is not None
        damaged_path = store.resolve(compile_result.tool_result.invocation).path
        damaged_path.unlink()
    elif damage == "corrupt_run_state":
        damaged_path = store.resolve(compile_result.tool_result.run_state).path
        damaged_path.write_bytes(b"{}\n")
    else:
        output = Text2EnvCompileOutput.model_validate(compile_result.tool_result.typed_output)
        damaged_path = store.resolve(output.environment_package.package_manifest).path
        damaged_path.write_bytes(b"{}\n")
    after_damage = _cas_objects(store)

    with pytest.raises(System2ReplayHandoffError) as raised:
        System2ReplayHandoff(
            artifact_store=store,
            scratch_root=tmp_path / "handoff",
        ).prepare(
            compile_result=compile_result,
            runtime_config=RuntimeConfig(),
        )

    assert raised.value.reason == expected_reason
    assert _cas_objects(store) == after_damage


class _RuntimeConfigSubclass(RuntimeConfig):
    pass


@pytest.mark.parametrize(
    "runtime_config",
    [
        {"settle_steps": 900},
        RuntimeConfig.model_construct(
            precheck_steps=0,
            settle_steps="900",
            contact_window_steps=120,
            video_frames=120,
            fps=12,
        ),
        RuntimeConfig.model_construct(
            precheck_steps=0,
            settle_steps=0,
            contact_window_steps=120,
            video_frames=120,
            fps=12,
        ),
        _RuntimeConfigSubclass(),
    ],
)
def test_replay_handoff_requires_an_exact_strict_runtime_config(
    tmp_path: Path,
    runtime_config: object,
) -> None:
    store, compile_result = _compile_turn(tmp_path)
    before = _cas_objects(store)

    with pytest.raises(System2ReplayHandoffError) as raised:
        System2ReplayHandoff(
            artifact_store=store,
            scratch_root=tmp_path / "handoff",
        ).prepare(
            compile_result=compile_result,
            runtime_config=runtime_config,
        )

    assert raised.value.reason == "runtime_config_invalid"
    assert _cas_objects(store) == before


def test_replay_handoff_returns_no_output_when_artifact_publication_cannot_start(
    tmp_path: Path,
) -> None:
    store, compile_result = _compile_turn(tmp_path)
    blocked_scratch_root = tmp_path / "not-a-directory"
    blocked_scratch_root.write_text("occupied", encoding="utf-8")
    before = _cas_objects(store)

    with pytest.raises(System2ReplayHandoffError) as raised:
        System2ReplayHandoff(
            artifact_store=store,
            scratch_root=blocked_scratch_root,
        ).prepare(
            compile_result=compile_result,
            runtime_config=RuntimeConfig(),
        )

    assert raised.value.reason == "artifact_write_failed"
    assert _cas_objects(store) == before


def test_replay_handoff_is_byte_deterministic_across_cas_clones(
    tmp_path: Path,
) -> None:
    store, compile_result = _compile_turn(tmp_path / "source")
    clone_root = tmp_path / "clone-cas"
    shutil.copytree(store.root, clone_root)
    clone = LocalArtifactStore(clone_root)
    runtime_config = RuntimeConfig(
        precheck_steps=3,
        settle_steps=480,
        contact_window_steps=60,
        video_frames=72,
        fps=9,
    )

    first = System2ReplayHandoff(
        artifact_store=store,
        scratch_root=tmp_path / "source-scratch",
    ).prepare(
        compile_result=compile_result,
        runtime_config=runtime_config,
    )
    second = System2ReplayHandoff(
        artifact_store=clone,
        scratch_root=tmp_path / "clone-scratch",
    ).prepare(
        compile_result=compile_result,
        runtime_config=runtime_config,
    )

    assert first == second
    assert (
        store.resolve(first.environment_package_ref).path.read_bytes()
        == clone.resolve(second.environment_package_ref).path.read_bytes()
    )
    provenance_bytes = store.resolve(first.request_provenance_ref).path.read_bytes()
    assert provenance_bytes == clone.resolve(second.request_provenance_ref).path.read_bytes()
    assert str(tmp_path).encode() not in provenance_bytes


def test_replay_handoff_constructor_requires_local_store_and_path() -> None:
    with pytest.raises(TypeError, match="artifact_store"):
        System2ReplayHandoff(artifact_store=object(), scratch_root=Path("scratch"))

    with pytest.raises(TypeError, match="scratch_root"):
        System2ReplayHandoff(
            artifact_store=LocalArtifactStore(Path("artifacts")),
            scratch_root="scratch",
        )


@pytest.mark.parametrize(
    "malformed",
    ["wrong_type", "invalid_member", "invalid_history"],
)
def test_replay_handoff_rejects_malformed_compile_result_objects(
    tmp_path: Path,
    malformed: str,
) -> None:
    store, compile_result = _compile_turn(tmp_path)
    supplied: object
    if malformed == "wrong_type":
        supplied = object()
    elif malformed == "invalid_member":
        supplied = compile_result.__class__(
            world_state=object(),
            history_authority=compile_result.history_authority,
            tool_result=compile_result.tool_result,
        )
    else:
        supplied = compile_result.__class__(
            world_state=compile_result.world_state,
            history_authority=object(),
            tool_result=compile_result.tool_result,
        )
    before = _cas_objects(store)

    with pytest.raises(System2ReplayHandoffError) as raised:
        System2ReplayHandoff(
            artifact_store=store,
            scratch_root=tmp_path / "handoff",
        ).prepare(
            compile_result=supplied,
            runtime_config=RuntimeConfig(),
        )

    assert raised.value.reason == "compile_result_invalid"
    assert _cas_objects(store) == before


def test_replay_handoff_rejects_in_memory_tool_result_drift(
    tmp_path: Path,
) -> None:
    store, compile_result = _compile_turn(tmp_path)
    changed_tool_result = compile_result.tool_result.model_copy(
        update={"started_at": compile_result.tool_result.started_at - timedelta(seconds=1)}
    )
    drifted = compile_result.__class__(
        world_state=compile_result.world_state,
        history_authority=compile_result.history_authority,
        tool_result=changed_tool_result,
    )
    before = _cas_objects(store)

    with pytest.raises(System2ReplayHandoffError) as raised:
        System2ReplayHandoff(
            artifact_store=store,
            scratch_root=tmp_path / "handoff",
        ).prepare(
            compile_result=drifted,
            runtime_config=RuntimeConfig(),
        )

    assert raised.value.reason == "compile_result_mismatch"
    assert _cas_objects(store) == before


def test_replay_handoff_rejects_a_tool_delta_that_does_not_recreate_history_state(
    tmp_path: Path,
) -> None:
    store, compile_result = _compile_turn(tmp_path)
    original_delta = compile_result.tool_result.state_delta
    assert original_delta is not None
    empty_delta = build_state_delta(
        base_state_sha256=original_delta.base_state_sha256,
        effective_at=original_delta.effective_at,
        receipt=original_delta.receipt,
        mutations=(),
    )
    drifted = compile_result.__class__(
        world_state=compile_result.world_state,
        history_authority=compile_result.history_authority,
        tool_result=compile_result.tool_result.model_copy(update={"state_delta": empty_delta}),
    )
    before = _cas_objects(store)

    with pytest.raises(System2ReplayHandoffError) as raised:
        System2ReplayHandoff(
            artifact_store=store,
            scratch_root=tmp_path / "handoff",
        ).prepare(
            compile_result=drifted,
            runtime_config=RuntimeConfig(),
        )

    assert raised.value.reason == "compile_result_mismatch"
    assert _cas_objects(store) == before


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        del cls, tz
        return datetime(2026, 9, 2, tzinfo=timezone.utc)


def test_replay_handoff_rejects_a_valid_receipt_for_another_transition_from_same_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system2_compile_turn_module, "datetime", _FixedDateTime)
    shared_inputs = tmp_path / "shared-inputs"
    store_a, result_a = _compile_turn(tmp_path / "a", input_root=shared_inputs)
    store_b, result_b = _compile_turn(tmp_path / "b", input_root=shared_inputs)
    _merge_cas(store_b, store_a)
    assert result_a.history_authority is not None
    assert result_b.history_authority is not None
    history_a = verify_planner_history_authority(
        artifact_store=store_a,
        authority_ref=result_a.history_authority,
        current_state=result_a.world_state,
    )
    history_b = verify_planner_history_authority(
        artifact_store=store_a,
        authority_ref=result_b.history_authority,
        current_state=result_b.world_state,
    )
    combined_authority = publish_planner_history_authority(
        artifact_store=store_a,
        scratch_root=tmp_path / "combined-history",
        lineage=history_b.lineage,
        entries=history_a.entries + history_b.entries,
    )
    assert result_a.tool_result.state_delta is not None
    wrong_transition = system2_compile_turn_module.apply_state_delta(
        history_b.lineage[-2],
        result_a.tool_result.state_delta,
        artifact_store=store_a,
    )
    assert wrong_transition != result_b.world_state
    cross_bound = result_b.__class__(
        world_state=result_b.world_state,
        history_authority=combined_authority,
        tool_result=result_a.tool_result,
    )
    before = _cas_objects(store_a)

    with pytest.raises(System2ReplayHandoffError) as raised:
        System2ReplayHandoff(
            artifact_store=store_a,
            scratch_root=tmp_path / "handoff",
        ).prepare(
            compile_result=cross_bound,
            runtime_config=RuntimeConfig(),
        )

    assert raised.value.reason == "compile_result_mismatch"
    assert _cas_objects(store_a) == before


def test_replay_handoff_rejects_cas_bytes_changed_after_post_put_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, compile_result = _compile_turn(tmp_path)
    original_resolve = LocalArtifactStore.resolve
    mutation_count = 0

    def drift_after_resolve(self, ref):
        nonlocal mutation_count
        resolved = original_resolve(self, ref)
        if (
            ref.name == "environment_package"
            and ref.schema_version == "harness.environment_package.v1"
        ):
            resolved.path.write_bytes(b"{}\n")
            mutation_count += 1
        return resolved

    monkeypatch.setattr(LocalArtifactStore, "resolve", drift_after_resolve)

    with pytest.raises(System2ReplayHandoffError) as raised:
        System2ReplayHandoff(
            artifact_store=store,
            scratch_root=tmp_path / "handoff",
        ).prepare(
            compile_result=compile_result,
            runtime_config=RuntimeConfig(),
        )

    assert raised.value.reason == "artifact_write_failed"
    assert mutation_count == 1
