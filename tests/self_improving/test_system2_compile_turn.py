from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import pytest

from scene_gen.catalog import AssetCatalog
from self_improving.harness.application import (
    CompileApplicationSettings,
    create_compile_application,
)
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas import RunStatus, Text2EnvCompileOutput
from self_improving.harness.system2.history import verify_planner_history_authority
from self_improving.harness.system2.planner import (
    PlannerProviderIdentity,
    PlannerProviderResponse,
    PlannerUsage,
)
from self_improving.system2_compile_turn import (
    System2CompileTurn,
    System2CompileTurnError,
)


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


@dataclass
class _CompileDecisionProvider:
    calls: int = 0
    seed_override: int | None = None
    action: str = "invoke_skill"
    fail: bool = False

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
        self.calls += 1
        progress("inference")
        if self.fail:
            raise RuntimeError("fixture provider failed")
        prompt_document = json.loads(prompt)
        context = prompt_document["context"]
        compile_input = next(
            fact["value"] for fact in context["facts"] if fact["key"] == "inputs.compile"
        )
        if self.seed_override is not None:
            compile_input["seed"] = self.seed_override
        decision: dict[str, object] = {
            "schema_version": "harness.planner_decision.v1",
            "base_state_sha256": context["world_state_sha256"],
            "context_sha256": context["context_sha256"],
            "action": self.action,
            "skill_ref": ("text2env.compile@1.0.0" if self.action == "invoke_skill" else None),
            "parameters": compile_input if self.action == "invoke_skill" else None,
            "observation_keys": [],
            "stop_reason": "No compile is needed." if self.action == "stop" else None,
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


def _application_and_catalog(tmp_path: Path):
    catalogs = tmp_path / "catalogs"
    assets = tmp_path / "external-assets"
    catalogs.mkdir()
    assets.mkdir()
    catalog_path = catalogs / "empty.json"
    catalog = AssetCatalog(
        robotwin_root=str(tmp_path / "RoboTwin"),
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
    return application, catalog_path


def test_compile_turn_commits_one_planned_compile_into_verified_world_history(
    tmp_path: Path,
) -> None:
    application, catalog_path = _application_and_catalog(tmp_path)
    provider = _CompileDecisionProvider()
    turn = System2CompileTurn(application=application, provider=provider)

    result = turn.execute(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
        asset_catalog_path=catalog_path,
        generate_missing_assets=True,
    )

    assert provider.calls == 1
    assert result.tool_result.status is RunStatus.SUCCEEDED
    assert result.world_state.version == 2
    assert result.world_state.predecessor_state_sha256 is not None
    facts = {fact.key: fact.value for fact in result.world_state.facts}
    assert facts["task.objective"] == "Place a purple hexagonal pedestal on the table."
    assert facts["inputs.compile"]["seed"] == 77
    output = Text2EnvCompileOutput.model_validate(result.tool_result.typed_output)
    assert facts["assets.catalog"] == output.environment_package.asset_catalog.model_dump(
        mode="json"
    )
    assert facts["environment.package"] == output.environment_package.model_dump(mode="json")

    assert result.history_authority is not None
    verified_history = verify_planner_history_authority(
        artifact_store=LocalArtifactStore(application.artifact_root),
        authority_ref=result.history_authority,
        current_state=result.world_state,
    )
    assert verified_history.lineage[-1] == result.world_state
    assert len(verified_history.entries) == 1
    assert verified_history.entries[0].receipt == result.tool_result.trusted_receipt


def test_compile_turn_preserves_planner_receipt_when_model_changes_trusted_input(
    tmp_path: Path,
) -> None:
    application, catalog_path = _application_and_catalog(tmp_path)
    provider = _CompileDecisionProvider(seed_override=78)
    turn = System2CompileTurn(application=application, provider=provider)

    with pytest.raises(System2CompileTurnError) as raised:
        turn.execute(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=catalog_path,
            generate_missing_assets=True,
        )

    assert raised.value.reason == "planner_input_mismatch"
    assert raised.value.planner_receipt.schema_version == ("harness.planner_execution_receipt.v1")
    LocalArtifactStore(application.artifact_root).resolve(raised.value.planner_receipt)
    assert provider.calls == 1


def test_compile_turn_records_a_blocked_skill_without_mutating_world_state(
    tmp_path: Path,
) -> None:
    application, catalog_path = _application_and_catalog(tmp_path)
    turn = System2CompileTurn(
        application=application,
        provider=_CompileDecisionProvider(),
    )

    result = turn.execute(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
        asset_catalog_path=catalog_path,
        generate_missing_assets=False,
    )

    assert result.tool_result.status is RunStatus.BLOCKED
    assert result.tool_result.state_delta is None
    assert result.world_state.version == 1
    assert {fact.key for fact in result.world_state.facts} == {
        "inputs.compile",
        "task.objective",
    }
    assert result.history_authority is None
    LocalArtifactStore(application.artifact_root).resolve(result.tool_result.trusted_receipt)


def test_compile_turn_returns_receipted_error_when_planner_stops(
    tmp_path: Path,
) -> None:
    application, catalog_path = _application_and_catalog(tmp_path)
    turn = System2CompileTurn(
        application=application,
        provider=_CompileDecisionProvider(action="stop"),
    )

    with pytest.raises(System2CompileTurnError) as raised:
        turn.execute(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=catalog_path,
            generate_missing_assets=True,
        )

    assert raised.value.reason == "planner_action_not_compile"
    LocalArtifactStore(application.artifact_root).resolve(raised.value.planner_receipt)


def test_compile_turn_hides_planner_failure_taxonomy_but_preserves_its_receipt(
    tmp_path: Path,
) -> None:
    application, catalog_path = _application_and_catalog(tmp_path)
    turn = System2CompileTurn(
        application=application,
        provider=_CompileDecisionProvider(fail=True),
    )

    with pytest.raises(System2CompileTurnError) as raised:
        turn.execute(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=catalog_path,
            generate_missing_assets=True,
        )

    assert raised.value.reason == "planning_failed"
    assert raised.value.planner_receipt is not None
    assert raised.value.tool_result is None
    LocalArtifactStore(application.artifact_root).resolve(raised.value.planner_receipt)


def test_compile_turn_hides_input_setup_failure_taxonomy_without_claiming_a_receipt(
    tmp_path: Path,
) -> None:
    application, catalog_path = _application_and_catalog(tmp_path)
    provider = _CompileDecisionProvider()
    turn = System2CompileTurn(application=application, provider=provider)

    with pytest.raises(System2CompileTurnError) as raised:
        turn.execute(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=catalog_path.with_name("missing.json"),
            generate_missing_assets=True,
        )

    assert raised.value.reason == "input_setup_failed"
    assert raised.value.planner_receipt is None
    assert raised.value.tool_result is None
    assert provider.calls == 0
