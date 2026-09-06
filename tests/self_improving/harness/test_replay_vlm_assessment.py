from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from scene_gen.builder import build_scene_package
from scene_gen.catalog import load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.solver import solve_scene
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.package_store import PackageStore
from self_improving.harness.registry import HandlerResult, SkillRegistry, StaticDependencyResolver
from self_improving.harness.replay_vlm_assessment import (
    ReplayVlmAssessmentApplication,
    ReplayVlmProviderRequest,
    ReplayVlmProviderResult,
)
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas import (
    ArtifactRef,
    DependencyRef,
    EnvironmentPackage,
    ReplayVlmAssessmentInput,
    ReplayVlmAssessmentOutput,
    RunStatus,
    RuntimeConfig,
    SkillDescriptor,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _put(
    store: LocalArtifactStore,
    tmp_path: Path,
    *,
    name: str,
    payload: bytes,
    media_type: str,
    schema_version: str | None,
) -> ArtifactRef:
    source = tmp_path / f"{name}.source"
    source.write_bytes(payload)
    return store.put_file(
        source,
        name=name,
        media_type=media_type,
        schema_version=schema_version,
    )


def _put_json(
    store: LocalArtifactStore,
    tmp_path: Path,
    *,
    name: str,
    value: object,
    schema_version: str,
) -> ArtifactRef:
    return _put(
        store,
        tmp_path,
        name=name,
        payload=json.dumps(value, sort_keys=True).encode(),
        media_type="application/json",
        schema_version=schema_version,
    )


class _FakeProvider:
    dependencies = (
        DependencyRef(name="replay_vlm.model", version="qwen-test", sha256="b" * 64),
        DependencyRef(name="replay_vlm.prompt", version="v1", sha256="c" * 64),
        DependencyRef(name="replay_vlm.provider", version="test", sha256="d" * 64),
    )

    def __init__(self) -> None:
        self.requests: list[ReplayVlmProviderRequest] = []

    def assess(self, request: ReplayVlmProviderRequest) -> ReplayVlmProviderResult:
        self.requests.append(request)
        return ReplayVlmProviderResult(
            advisory_status="pass",
            raw_response=b'{"overall":"pass"}',
            parsed_response={"overall": "pass"},
            provider_receipt={
                "provider_id": "tests.fake_visible_provider",
                "provider_revision": "v1",
                "production_eligible": False,
            },
            model_receipt={
                "model_id": "Qwen/Qwen2.5-VL-3B-Instruct",
                "revision": "e" * 40,
                "model_roster_sha256": "f" * 64,
            },
            resource_receipt={"visible_vlm_invocations": 1, "network_calls": 0},
            claims_physical_pass=False,
        )


class _AuthorityViolatingProvider(_FakeProvider):
    def assess(self, request: ReplayVlmProviderRequest) -> ReplayVlmProviderResult:
        result = super().assess(request)
        return ReplayVlmProviderResult(
            advisory_status=result.advisory_status,
            raw_response=result.raw_response,
            parsed_response=result.parsed_response,
            provider_receipt=result.provider_receipt,
            model_receipt=result.model_receipt,
            resource_receipt=result.resource_receipt,
            claims_physical_pass=True,  # type: ignore[arg-type]
        )


def _seed_successful_replay(
    tmp_path: Path,
    *,
    store: LocalArtifactStore,
    journal: SQLiteEventJournal,
    run_store: SQLiteRunStore,
) -> tuple[UUID, Text2EnvReplayInput, Text2EnvReplayOutput]:
    catalog_value = load_catalog(Path("tests/fixtures/asset_catalog.json"))
    catalog = _put_json(
        store,
        tmp_path,
        name="catalog",
        value=catalog_value.canonical_dict(),
        schema_version="robotwin.asset_catalog.v1",
    )
    scene = parse_rule_based("Place a can on top of a plate.", seed=73)
    resolved = solve_scene(scene, catalog_value)
    package_root = tmp_path / "package"
    build_scene_package(scene, resolved, package_root)
    published = PackageStore(store).publish(package_root)
    runtime = _put_json(
        store,
        tmp_path,
        name="runtime_evidence",
        value={
            "resolved_scene_sha256": resolved.digest(),
            "runtime_validation": {"status": "pass"},
        },
        schema_version="robotwin.scene_runtime_evidence.v2",
    )
    images = tuple(
        _put(
            store,
            tmp_path,
            name=name,
            payload=_PNG + name.encode(),
            media_type="image/png",
            schema_version=None,
        )
        for name in ("observer_start", "observer_mid", "observer_end", "preview_head")
    )
    package = EnvironmentPackage(
        package_id=resolved.digest(),
        route_id="text2env",
        producer_skill_ref="text2env.compile@1.0.0",
        seed=73,
        scene_spec_sha256=scene.digest(),
        resolved_scene_sha256=resolved.digest(),
        asset_catalog=catalog,
        package_manifest=published.manifest,
    )
    replay_input = Text2EnvReplayInput(
        environment_package=package,
        runtime_config=RuntimeConfig(),
    )
    replay_output = Text2EnvReplayOutput(
        runtime_evidence=runtime,
        replay_artifacts=images,
    )
    report = _put_json(
        store,
        tmp_path,
        name="replay_qualification_report",
        value={"status": "pass"},
        schema_version="harness.skill_qualification_report.v1",
    )
    qualification = _put_json(
        store,
        tmp_path,
        name="replay_qualification",
        value={
            "skill_ref": "text2env.replay@1.0.0",
            "status": "pass",
            "deterministic_case_id": "unit-replay",
            "regression_command": "pytest -q",
            "report_sha256": report.sha256,
        },
        schema_version="harness.skill_qualification.v1",
    )
    replay_dependency = DependencyRef(name="replay.test_runtime", version="1", sha256="1" * 64)
    ids = iter((UUID("81000000-0000-4000-8000-000000000001"),))
    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=StaticDependencyResolver(
            {"text2env.replay@1.0.0": (replay_dependency,)}
        ),
        event_sink=journal,
        clock=lambda: datetime(2026, 9, 6, tzinfo=timezone.utc),
        run_id_factory=lambda: next(ids),
        run_store=run_store,
    )
    descriptor = SkillDescriptor(
        skill_id="text2env.replay",
        version="1.0.0",
        mcp_tool_name="text2env_replay_v1_0_0",
        input_schema="harness.text2env_replay_input.v1",
        output_schema="harness.text2env_replay_output.v1",
        implementation_name="tests.replay",
        implementation_version="1",
        implementation_sha256="2" * 64,
        deterministic=True,
        max_attempts=1,
        qualification_artifact=qualification,
    )

    def replay_handler(_value: object, _context: object) -> HandlerResult:
        return HandlerResult(output=replay_output, artifacts=(runtime, *images))

    registry.register(descriptor, replay_handler)
    state = registry.invoke("text2env.replay", "1.0.0", replay_input.model_dump(mode="json"))
    assert state.status is RunStatus.SUCCEEDED
    return state.run_id, replay_input, replay_output


def test_replay_vlm_public_contract_binds_one_replay_run_and_advisory_receipt() -> None:
    replay_run_id = UUID("91000000-0000-4000-8000-000000000001")
    value = ReplayVlmAssessmentInput(replay_run_id=str(replay_run_id))
    assessment = ArtifactRef(
        name="replay_vlm_assessment",
        uri=f"artifact://sha256/{'a' * 64}",
        media_type="application/json",
        sha256="a" * 64,
        bytes=123,
        schema_version="harness.replay_vlm_assessment.v1",
    )

    output = ReplayVlmAssessmentOutput(
        replay_run_id=str(replay_run_id),
        assessment=assessment,
        advisory_status="pass",
        claims_physical_pass=False,
    )

    assert value.model_dump(mode="json") == {"replay_run_id": str(replay_run_id)}
    assert output.assessment == assessment
    assert output.claims_physical_pass is False

    with pytest.raises(ValidationError):
        ReplayVlmAssessmentOutput(
            replay_run_id=str(replay_run_id),
            assessment=assessment.model_copy(update={"schema_version": "wrong.v1"}),
            advisory_status="pass",
            claims_physical_pass=False,
        )
    with pytest.raises(ValidationError):
        ReplayVlmAssessmentOutput(
            replay_run_id=str(replay_run_id),
            assessment=assessment,
            advisory_status="pass",
            claims_physical_pass=True,
        )


def test_assessment_reads_verified_replay_images_and_publishes_bound_cas_receipt(
    tmp_path: Path,
) -> None:
    database = tmp_path / "harness.sqlite3"
    store = LocalArtifactStore(tmp_path / "cas")
    journal = SQLiteEventJournal(database)
    run_store = SQLiteRunStore(database)
    replay_run_id, replay_input, replay_output = _seed_successful_replay(
        tmp_path, store=store, journal=journal, run_store=run_store
    )
    provider = _FakeProvider()
    application = ReplayVlmAssessmentApplication(
        artifact_store=store,
        event_journal=journal,
        run_store=run_store,
        provider=provider,
        clock=lambda: datetime(2026, 9, 6, 1, tzinfo=timezone.utc),
        run_id_factory=lambda: UUID("82000000-0000-4000-8000-000000000001"),
    )

    state = application.assess(ReplayVlmAssessmentInput(replay_run_id=str(replay_run_id)))

    assert state.status is RunStatus.SUCCEEDED
    output = ReplayVlmAssessmentOutput.model_validate(state.output)
    assert output.replay_run_id == str(replay_run_id)
    assert output.advisory_status == "pass"
    assert output.claims_physical_pass is False
    receipt_path = store.resolve(output.assessment).path
    receipt = json.loads(receipt_path.read_bytes())
    source_invocation = run_store.read_invocation(replay_run_id)
    assert source_invocation is not None
    assert receipt["source_replay"] == {
        "run_id": str(replay_run_id),
        "invocation_digest": source_invocation.invocation_digest,
        "resolved_scene_sha256": replay_input.environment_package.resolved_scene_sha256,
        "runtime_evidence": replay_output.runtime_evidence.model_dump(mode="json"),
    }
    assert [item["name"] for item in receipt["images"]] == [
        "observer_start",
        "observer_mid",
        "observer_end",
        "preview_head",
    ]
    assert receipt["physical_gate_authority"] == "deterministic_runtime_only"
    assert receipt["claims_physical_pass"] is False
    assert receipt["resource_receipt"]["visible_vlm_invocations"] == 1
    assert receipt["resource_receipt"]["network_calls"] == 0
    assert len(provider.requests) == 1
    assert tuple(item.ref for item in provider.requests[0].images) == replay_output.replay_artifacts
    assert [event.stage for event in state.events] == [
        "qualification_candidate.preflight",
        "qualification_candidate.vlm.replay_evidence.bound",
        "qualification_candidate.vlm.inference.started",
        "qualification_candidate.vlm.assessment.published",
        "qualification_candidate.complete",
    ]
    assert state.events[-2].artifact_refs == tuple(state.artifacts)
    assert application.run_state(state.run_id) == state
    assert application.invocation(state.run_id) is not None


def test_assessment_blocks_before_inference_when_replay_does_not_exist(tmp_path: Path) -> None:
    database = tmp_path / "harness.sqlite3"
    provider = _FakeProvider()
    application = ReplayVlmAssessmentApplication(
        artifact_store=LocalArtifactStore(tmp_path / "cas"),
        event_journal=SQLiteEventJournal(database),
        run_store=SQLiteRunStore(database),
        provider=provider,
        clock=lambda: datetime(2026, 9, 6, 1, tzinfo=timezone.utc),
        run_id_factory=lambda: UUID("83000000-0000-4000-8000-000000000001"),
    )

    state = application.assess(
        ReplayVlmAssessmentInput(replay_run_id="84000000-0000-4000-8000-000000000001")
    )

    assert state.status is RunStatus.BLOCKED
    assert state.blocker is not None
    assert state.blocker.code == "VLM_REPLAY_NOT_FOUND"
    assert provider.requests == []


def test_assessment_blocks_before_inference_when_bound_image_is_missing_from_cas(
    tmp_path: Path,
) -> None:
    database = tmp_path / "harness.sqlite3"
    store = LocalArtifactStore(tmp_path / "cas")
    journal = SQLiteEventJournal(database)
    run_store = SQLiteRunStore(database)
    replay_run_id, _replay_input, replay_output = _seed_successful_replay(
        tmp_path, store=store, journal=journal, run_store=run_store
    )
    store.resolve(replay_output.replay_artifacts[1]).path.unlink()
    provider = _FakeProvider()
    application = ReplayVlmAssessmentApplication(
        artifact_store=store,
        event_journal=journal,
        run_store=run_store,
        provider=provider,
        clock=lambda: datetime(2026, 9, 6, 1, tzinfo=timezone.utc),
        run_id_factory=lambda: UUID("85000000-0000-4000-8000-000000000001"),
    )

    state = application.assess(ReplayVlmAssessmentInput(replay_run_id=str(replay_run_id)))

    assert state.status is RunStatus.BLOCKED
    assert state.blocker is not None
    assert state.blocker.code == "VLM_REPLAY_MEDIA_INVALID"
    assert provider.requests == []


def test_assessment_rejects_provider_claim_of_physical_authority(tmp_path: Path) -> None:
    database = tmp_path / "harness.sqlite3"
    store = LocalArtifactStore(tmp_path / "cas")
    journal = SQLiteEventJournal(database)
    run_store = SQLiteRunStore(database)
    replay_run_id, _replay_input, _replay_output = _seed_successful_replay(
        tmp_path, store=store, journal=journal, run_store=run_store
    )
    provider = _AuthorityViolatingProvider()
    application = ReplayVlmAssessmentApplication(
        artifact_store=store,
        event_journal=journal,
        run_store=run_store,
        provider=provider,
        clock=lambda: datetime(2026, 9, 6, 1, tzinfo=timezone.utc),
        run_id_factory=lambda: UUID("86000000-0000-4000-8000-000000000001"),
    )

    state = application.assess(ReplayVlmAssessmentInput(replay_run_id=str(replay_run_id)))

    assert state.status is RunStatus.BLOCKED
    assert state.blocker is not None
    assert state.blocker.code == "VLM_PHYSICAL_AUTHORITY_VIOLATION"
    assert len(provider.requests) == 1
    assert state.output is None
