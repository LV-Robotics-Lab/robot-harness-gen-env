from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import pytest

import self_improving.system2_replay_input_promotion as promotion_module
from scene_gen.catalog import AssetCatalog
from self_improving.harness.application import (
    CompileApplicationSettings,
    create_compile_application,
)
from self_improving.harness.artifacts import ArtifactResolutionError, LocalArtifactStore
from self_improving.harness.package_store import PackageStore, PublishedPackage
from self_improving.harness.schemas import RuntimeConfig
from self_improving.harness.system2.planner import (
    PlannerProviderIdentity,
    PlannerProviderResponse,
    PlannerUsage,
)
from self_improving.system2_compile_turn import System2CompileTurn
from self_improving.system2_replay_handoff import PreparedSystem2Replay, System2ReplayHandoff
from self_improving.system2_replay_input_promotion import (
    PromotedSystem2ReplayInput,
    System2ReplayInputPromoter,
    System2ReplayInputPromotionError,
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


def _compile_and_prepare(tmp_path: Path):
    catalogs = tmp_path / "catalogs"
    assets = tmp_path / "external-assets"
    catalogs.mkdir(parents=True)
    assets.mkdir(parents=True)
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
    compile_result = System2CompileTurn(
        application=application,
        provider=_CompileDecisionProvider(),
    ).execute(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
        asset_catalog_path=catalog_path,
        generate_missing_assets=True,
    )
    source_store = LocalArtifactStore(application.artifact_root)
    prepared = System2ReplayHandoff(
        artifact_store=source_store,
        scratch_root=tmp_path / "handoff-scratch",
    ).prepare(
        compile_result=compile_result,
        runtime_config=RuntimeConfig(
            precheck_steps=2,
            settle_steps=600,
            contact_window_steps=90,
            video_frames=80,
            fps=10,
        ),
    )
    return source_store, compile_result, prepared


def _cas_objects(store: LocalArtifactStore) -> frozenset[str]:
    return frozenset(path.name for path in (store.root / "sha256").glob("*/*") if path.is_file())


def test_promoted_replay_package_is_complete_after_source_becomes_unavailable(
    tmp_path: Path,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")

    promoted = System2ReplayInputPromoter(
        source_artifact_store=source_store,
        replay_artifact_store=replay_store,
        scratch_root=tmp_path / "promotion-scratch",
    ).promote(
        compile_result=compile_result,
        prepared=prepared,
    )

    assert type(promoted) is PromotedSystem2ReplayInput
    assert promoted.replay_input == prepared.replay_input
    assert promoted.environment_package_ref == prepared.environment_package_ref
    assert promoted.destination_artifact_root == replay_store.root

    source_store.root.rename(source_store.root.with_name("source-cas-unavailable"))
    package = promoted.replay_input.environment_package
    replay_store.resolve(promoted.environment_package_ref)
    replay_store.resolve(package.asset_catalog)
    materialized = PackageStore(replay_store).materialize(
        package.package_manifest,
        tmp_path / "destination-materialized",
    )
    assert (materialized / "package_manifest.json").is_file()
    with pytest.raises(ArtifactResolutionError) as raised:
        replay_store.resolve(prepared.request_provenance_ref)
    assert raised.value.reason == "not_found"


def test_forged_prepared_record_is_rejected_before_destination_writes(tmp_path: Path) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    forged = PreparedSystem2Replay(
        replay_input=prepared.replay_input,
        environment_package_ref=prepared.environment_package_ref.model_copy(
            update={"name": "forged_environment_package"}
        ),
        request_provenance_ref=prepared.request_provenance_ref,
    )
    before = _cas_objects(replay_store)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        System2ReplayInputPromoter(
            source_artifact_store=source_store,
            replay_artifact_store=replay_store,
            scratch_root=tmp_path / "promotion-scratch",
        ).promote(
            compile_result=compile_result,
            prepared=forged,
        )

    assert raised.value.reason == "prepared_mismatch"
    assert _cas_objects(replay_store) == before


def test_prepared_record_from_another_compile_is_rejected_before_destination_writes(
    tmp_path: Path,
) -> None:
    _, _, prepared_a = _compile_and_prepare(tmp_path / "compile-a")
    source_b, result_b, _ = _compile_and_prepare(tmp_path / "compile-b")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    before = _cas_objects(replay_store)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        System2ReplayInputPromoter(
            source_artifact_store=source_b,
            replay_artifact_store=replay_store,
            scratch_root=tmp_path / "promotion-scratch",
        ).promote(
            compile_result=result_b,
            prepared=prepared_a,
        )

    assert raised.value.reason == "prepared_mismatch"
    assert _cas_objects(replay_store) == before


@pytest.mark.parametrize(
    ("target", "damage"),
    [
        ("member", "missing"),
        ("member", "corrupt"),
        ("catalog", "missing"),
        ("catalog", "corrupt"),
    ],
)
def test_unavailable_source_package_closure_fails_before_destination_writes(
    tmp_path: Path,
    target: str,
    damage: str,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    package = prepared.replay_input.environment_package
    if target == "catalog":
        damaged_path = source_store.resolve(package.asset_catalog).path
    else:
        manifest = json.loads(source_store.resolve(package.package_manifest).path.read_bytes())
        damaged_path = source_store.resolve_digest(manifest["files"][0]["sha256"])
    if damage == "missing":
        damaged_path.unlink()
    else:
        damaged_path.write_bytes(b"corrupt\n")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    before = _cas_objects(replay_store)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        System2ReplayInputPromoter(
            source_artifact_store=source_store,
            replay_artifact_store=replay_store,
            scratch_root=tmp_path / "promotion-scratch",
        ).promote(
            compile_result=compile_result,
            prepared=prepared,
        )

    assert raised.value.reason == "source_authority_invalid"
    assert _cas_objects(replay_store) == before


def test_preexisting_corrupt_destination_object_fails_without_deleting_cas_prefixes(
    tmp_path: Path,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    catalog_ref = prepared.replay_input.environment_package.asset_catalog
    seeded_ref = replay_store.put_file(
        source_store.resolve(catalog_ref).path,
        name=catalog_ref.name,
        media_type=catalog_ref.media_type,
        schema_version=catalog_ref.schema_version,
    )
    assert seeded_ref == catalog_ref
    corrupt_path = replay_store.resolve(catalog_ref).path
    corrupt_path.write_bytes(b"corrupt\n")
    before = _cas_objects(replay_store)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        System2ReplayInputPromoter(
            source_artifact_store=source_store,
            replay_artifact_store=replay_store,
            scratch_root=tmp_path / "promotion-scratch",
        ).promote(
            compile_result=compile_result,
            prepared=prepared,
        )

    assert raised.value.reason == "destination_write_failed"
    assert corrupt_path.read_bytes() == b"corrupt\n"
    assert before <= _cas_objects(replay_store)
    with pytest.raises(ArtifactResolutionError) as missing_environment:
        replay_store.resolve(prepared.environment_package_ref)
    assert missing_environment.value.reason == "not_found"


@pytest.mark.parametrize("layout", ["same", "source_contains", "destination_contains", "alias"])
def test_promoter_rejects_canonical_artifact_root_overlap(
    tmp_path: Path,
    layout: str,
) -> None:
    if layout == "same":
        source_root = destination_root = tmp_path / "cas"
    elif layout == "source_contains":
        source_root = tmp_path / "cas"
        destination_root = source_root / "nested"
    elif layout == "destination_contains":
        destination_root = tmp_path / "cas"
        source_root = destination_root / "nested"
    else:
        source_root = tmp_path / "canonical"
        source_root.mkdir()
        destination_root = tmp_path / "alias"
        destination_root.symlink_to(source_root, target_is_directory=True)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        System2ReplayInputPromoter(
            source_artifact_store=LocalArtifactStore(source_root),
            replay_artifact_store=LocalArtifactStore(destination_root),
            scratch_root=tmp_path / "scratch",
        )

    assert raised.value.reason == "artifact_roots_overlap"


@pytest.mark.parametrize(
    "layout",
    ["symlink_ancestor", "symlink_root", "inside_source", "contains_destination", "file"],
)
def test_promoter_rejects_unsafe_scratch_roots(tmp_path: Path, layout: str) -> None:
    source_root = tmp_path / "source-cas"
    destination_root = tmp_path / "destination-cas"
    if layout == "symlink_ancestor":
        real_parent = tmp_path / "real-parent"
        real_parent.mkdir()
        linked_parent = tmp_path / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        scratch_root = linked_parent / "scratch"
    elif layout == "symlink_root":
        real_scratch = tmp_path / "real-scratch"
        real_scratch.mkdir()
        scratch_root = tmp_path / "scratch-link"
        scratch_root.symlink_to(real_scratch, target_is_directory=True)
    elif layout == "inside_source":
        scratch_root = source_root / "scratch"
    elif layout == "contains_destination":
        scratch_root = tmp_path
    else:
        scratch_root = tmp_path / "scratch-file"
        scratch_root.write_text("occupied", encoding="utf-8")

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        System2ReplayInputPromoter(
            source_artifact_store=LocalArtifactStore(source_root),
            replay_artifact_store=LocalArtifactStore(destination_root),
            scratch_root=scratch_root,
        )

    assert raised.value.reason == "scratch_root_unsafe"


def test_promotion_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    promoter = System2ReplayInputPromoter(
        source_artifact_store=source_store,
        replay_artifact_store=replay_store,
        scratch_root=tmp_path / "promotion-scratch",
    )

    first = promoter.promote(compile_result=compile_result, prepared=prepared)
    objects_after_first = _cas_objects(replay_store)
    second = promoter.promote(compile_result=compile_result, prepared=prepared)

    assert second == first
    assert _cas_objects(replay_store) == objects_after_first
    assert not any((tmp_path / "promotion-scratch").iterdir())


def test_destination_contains_only_the_seven_logical_package_closure_refs(
    tmp_path: Path,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")

    System2ReplayInputPromoter(
        source_artifact_store=source_store,
        replay_artifact_store=replay_store,
        scratch_root=tmp_path / "promotion-scratch",
    ).promote(
        compile_result=compile_result,
        prepared=prepared,
    )

    package = prepared.replay_input.environment_package
    manifest = json.loads(replay_store.resolve(package.package_manifest).path.read_bytes())
    member_digests = [record["sha256"] for record in manifest["files"]]
    logical_refs = [
        prepared.environment_package_ref.sha256,
        package.asset_catalog.sha256,
        package.package_manifest.sha256,
        *member_digests,
    ]
    assert len(logical_refs) == 7
    assert _cas_objects(replay_store) == frozenset(logical_refs)
    assert prepared.request_provenance_ref.sha256 not in _cas_objects(replay_store)


def test_destination_reread_drift_fails_closed_after_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    original_resolve = LocalArtifactStore.resolve
    drifted = False

    def drift_environment_after_verified_resolve(self, ref):
        nonlocal drifted
        resolved = original_resolve(self, ref)
        if (
            self.root == replay_store.root
            and ref == prepared.environment_package_ref
            and not drifted
        ):
            resolved.path.write_bytes(b"{}\n")
            drifted = True
        return resolved

    monkeypatch.setattr(LocalArtifactStore, "resolve", drift_environment_after_verified_resolve)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        System2ReplayInputPromoter(
            source_artifact_store=source_store,
            replay_artifact_store=replay_store,
            scratch_root=tmp_path / "promotion-scratch",
        ).promote(
            compile_result=compile_result,
            prepared=prepared,
        )

    assert raised.value.reason == "destination_verification_failed"
    assert drifted is True


class _LocalArtifactStoreSubclass(LocalArtifactStore):
    pass


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", object()),
        ("source", _LocalArtifactStoreSubclass(Path("source"))),
        ("destination", object()),
        ("destination", _LocalArtifactStoreSubclass(Path("destination"))),
        ("scratch", "scratch"),
    ],
)
def test_promoter_constructor_requires_exact_local_stores_and_path(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    source: object = LocalArtifactStore(tmp_path / "source-cas")
    destination: object = LocalArtifactStore(tmp_path / "destination-cas")
    scratch: object = tmp_path / "scratch"
    if field == "source":
        source = value
    elif field == "destination":
        destination = value
    else:
        scratch = value

    expected_name = "replay_artifact_store" if field == "destination" else field
    with pytest.raises(TypeError, match=expected_name):
        System2ReplayInputPromoter(
            source_artifact_store=source,
            replay_artifact_store=destination,
            scratch_root=scratch,
        )


def test_promoter_rejects_a_non_prepared_object_before_destination_writes(
    tmp_path: Path,
) -> None:
    source_store, compile_result, _ = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        System2ReplayInputPromoter(
            source_artifact_store=source_store,
            replay_artifact_store=replay_store,
            scratch_root=tmp_path / "promotion-scratch",
        ).promote(
            compile_result=compile_result,
            prepared=object(),
        )

    assert raised.value.reason == "prepared_invalid"
    assert _cas_objects(replay_store) == frozenset()


def test_unexpected_staging_failure_has_a_fixed_error_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")

    def fail_to_allocate_staging(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("injected staging failure")

    monkeypatch.setattr(
        promotion_module.tempfile,
        "TemporaryDirectory",
        fail_to_allocate_staging,
    )

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        System2ReplayInputPromoter(
            source_artifact_store=source_store,
            replay_artifact_store=replay_store,
            scratch_root=tmp_path / "promotion-scratch",
        ).promote(
            compile_result=compile_result,
            prepared=prepared,
        )

    assert raised.value.reason == "promotion_failed"
    assert _cas_objects(replay_store) == frozenset()


def test_source_closure_loss_after_authority_verification_still_precedes_destination_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    original_prepare = System2ReplayHandoff.prepare

    def remove_manifest_after_handoff(self, **kwargs):
        verified = original_prepare(self, **kwargs)
        manifest_ref = verified.replay_input.environment_package.package_manifest
        source_store.resolve(manifest_ref).path.unlink()
        return verified

    monkeypatch.setattr(System2ReplayHandoff, "prepare", remove_manifest_after_handoff)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        System2ReplayInputPromoter(
            source_artifact_store=source_store,
            replay_artifact_store=replay_store,
            scratch_root=tmp_path / "promotion-scratch",
        ).promote(
            compile_result=compile_result,
            prepared=prepared,
        )

    assert raised.value.reason == "source_package_invalid"
    assert _cas_objects(replay_store) == frozenset()


def test_source_package_publication_must_reproduce_the_exact_manifest_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    original_publish = PackageStore.publish

    def drift_source_manifest(self, package_root):
        published = original_publish(self, package_root)
        if self.artifact_store.root == source_store.root:
            return PublishedPackage(
                manifest=published.manifest.model_copy(update={"name": "drifted_manifest"}),
                members=published.members,
            )
        return published

    monkeypatch.setattr(PackageStore, "publish", drift_source_manifest)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        System2ReplayInputPromoter(
            source_artifact_store=source_store,
            replay_artifact_store=replay_store,
            scratch_root=tmp_path / "promotion-scratch",
        ).promote(
            compile_result=compile_result,
            prepared=prepared,
        )

    assert raised.value.reason == "source_package_invalid"
    assert _cas_objects(replay_store) == frozenset()


@pytest.mark.parametrize("drift", ["environment", "invalid_catalog", "changed_catalog"])
def test_source_bytes_are_rechecked_after_authority_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    package = prepared.replay_input.environment_package
    original_prepare = System2ReplayHandoff.prepare
    original_resolve = LocalArtifactStore.resolve
    authority_verified = False
    drifted = False

    def mark_authority_verified(self, **kwargs):
        nonlocal authority_verified
        verified = original_prepare(self, **kwargs)
        authority_verified = True
        return verified

    def drift_after_verified_resolve(self, ref):
        nonlocal drifted
        resolved = original_resolve(self, ref)
        target_ref = (
            prepared.environment_package_ref if drift == "environment" else package.asset_catalog
        )
        if (
            authority_verified
            and self.root == source_store.root
            and ref == target_ref
            and not drifted
        ):
            if drift == "environment":
                changed = package.model_copy(update={"seed": 78})
                payload = _canonical_json(changed.model_dump(mode="json"))
            elif drift == "invalid_catalog":
                payload = b"{}\n"
            else:
                catalog = AssetCatalog.model_validate_json(resolved.path.read_bytes())
                changed = catalog.model_copy(
                    update={"robotwin_root": catalog.robotwin_root + "-drift"}
                )
                payload = json.dumps(
                    changed.canonical_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            resolved.path.write_bytes(payload)
            drifted = True
        return resolved

    monkeypatch.setattr(System2ReplayHandoff, "prepare", mark_authority_verified)
    monkeypatch.setattr(LocalArtifactStore, "resolve", drift_after_verified_resolve)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        System2ReplayInputPromoter(
            source_artifact_store=source_store,
            replay_artifact_store=replay_store,
            scratch_root=tmp_path / "promotion-scratch",
        ).promote(
            compile_result=compile_result,
            prepared=prepared,
        )

    assert raised.value.reason == "source_package_invalid"
    assert drifted is True
    assert _cas_objects(replay_store) == frozenset()


@pytest.mark.parametrize("drift", ["package", "catalog", "environment"])
def test_destination_adapter_must_return_the_exact_source_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    package = prepared.replay_input.environment_package
    if drift == "package":
        original_publish = PackageStore.publish

        def drift_destination_package(self, package_root):
            published = original_publish(self, package_root)
            if self.artifact_store.root == replay_store.root:
                return PublishedPackage(
                    manifest=published.manifest.model_copy(update={"name": "drifted_manifest"}),
                    members=published.members,
                )
            return published

        monkeypatch.setattr(PackageStore, "publish", drift_destination_package)
    else:
        original_put_file = LocalArtifactStore.put_file
        target = package.asset_catalog if drift == "catalog" else prepared.environment_package_ref

        def drift_destination_ref(self, source, **kwargs):
            published = original_put_file(self, source, **kwargs)
            if self.root == replay_store.root and published == target:
                return published.model_copy(update={"name": f"drifted_{published.name}"})
            return published

        monkeypatch.setattr(LocalArtifactStore, "put_file", drift_destination_ref)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        System2ReplayInputPromoter(
            source_artifact_store=source_store,
            replay_artifact_store=replay_store,
            scratch_root=tmp_path / "promotion-scratch",
        ).promote(
            compile_result=compile_result,
            prepared=prepared,
        )

    assert raised.value.reason == "destination_ref_mismatch"


def test_valid_but_changed_environment_bytes_fail_the_final_destination_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    original_resolve = LocalArtifactStore.resolve
    changed_package = prepared.replay_input.environment_package.model_copy(update={"seed": 78})
    changed_bytes = _canonical_json(changed_package.model_dump(mode="json"))
    drifted = False

    def drift_environment_after_verified_resolve(self, ref):
        nonlocal drifted
        resolved = original_resolve(self, ref)
        if (
            self.root == replay_store.root
            and ref == prepared.environment_package_ref
            and not drifted
        ):
            resolved.path.write_bytes(changed_bytes)
            drifted = True
        return resolved

    monkeypatch.setattr(LocalArtifactStore, "resolve", drift_environment_after_verified_resolve)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        System2ReplayInputPromoter(
            source_artifact_store=source_store,
            replay_artifact_store=replay_store,
            scratch_root=tmp_path / "promotion-scratch",
        ).promote(
            compile_result=compile_result,
            prepared=prepared,
        )

    assert raised.value.reason == "destination_verification_failed"
    assert drifted is True


def test_scratch_directory_is_rechecked_immediately_before_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    scratch_root = (tmp_path / "promotion-scratch").absolute()
    promoter = System2ReplayInputPromoter(
        source_artifact_store=source_store,
        replay_artifact_store=replay_store,
        scratch_root=scratch_root,
    )
    scratch_root.rmdir()
    original_is_dir = Path.is_dir

    def report_replaced_scratch(self):
        if self == scratch_root:
            return False
        return original_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", report_replaced_scratch)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        promoter.promote(compile_result=compile_result, prepared=prepared)

    assert raised.value.reason == "scratch_root_unsafe"
    assert _cas_objects(replay_store) == frozenset()


def test_artifact_roots_are_rechecked_immediately_before_promotion(tmp_path: Path) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    promoter = System2ReplayInputPromoter(
        source_artifact_store=source_store,
        replay_artifact_store=replay_store,
        scratch_root=tmp_path / "promotion-scratch",
    )
    source_alias = tmp_path / "source-cas-alias"
    source_alias.symlink_to(source_store.root, target_is_directory=True)
    replay_store.root = source_alias
    before = _cas_objects(source_store)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        promoter.promote(compile_result=compile_result, prepared=prepared)

    assert raised.value.reason == "artifact_roots_overlap"
    assert _cas_objects(source_store) == before


def test_destination_path_replaced_by_source_symlink_is_rejected_before_writes(
    tmp_path: Path,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    destination_root = tmp_path / "replay-cas"
    destination_root.mkdir()
    replay_store = LocalArtifactStore(destination_root)
    promoter = System2ReplayInputPromoter(
        source_artifact_store=source_store,
        replay_artifact_store=replay_store,
        scratch_root=tmp_path / "promotion-scratch",
    )
    destination_root.rmdir()
    destination_root.symlink_to(source_store.root, target_is_directory=True)
    before = _cas_objects(source_store)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        promoter.promote(compile_result=compile_result, prepared=prepared)

    assert raised.value.reason == "artifact_roots_overlap"
    assert _cas_objects(source_store) == before


@pytest.mark.parametrize(
    "target",
    ["source", "destination", "invalid_source", "invalid_destination"],
)
def test_artifact_store_root_identity_is_frozen_at_construction(
    tmp_path: Path,
    target: str,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    original_source = LocalArtifactStore(source_store.root)
    original_replay = LocalArtifactStore(replay_store.root)
    promoter = System2ReplayInputPromoter(
        source_artifact_store=source_store,
        replay_artifact_store=replay_store,
        scratch_root=tmp_path / "promotion-scratch",
    )
    if target == "source":
        source_store.root = tmp_path / "different-source"
    elif target == "destination":
        replay_store.root = tmp_path / "different-destination"
    elif target == "invalid_source":
        source_store.root = object()
    else:
        replay_store.root = object()
    source_before = _cas_objects(original_source)
    replay_before = _cas_objects(original_replay)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        promoter.promote(compile_result=compile_result, prepared=prepared)

    assert raised.value.reason == "artifact_roots_changed"
    assert _cas_objects(original_source) == source_before
    assert _cas_objects(original_replay) == replay_before


def test_replay_root_drift_during_source_prepare_fails_before_destination_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    frozen_replay = LocalArtifactStore(replay_store.root)
    promoter = System2ReplayInputPromoter(
        source_artifact_store=source_store,
        replay_artifact_store=replay_store,
        scratch_root=tmp_path / "promotion-scratch",
    )
    original_resolve = LocalArtifactStore.resolve
    frozen_source_root = source_store.root
    drifted = False

    def drift_during_source_prepare(self, ref):
        nonlocal drifted
        resolved = original_resolve(self, ref)
        if (
            self.root == frozen_source_root
            and ref == prepared.environment_package_ref
            and not drifted
        ):
            replay_store.root = source_store.root
            drifted = True
        return resolved

    monkeypatch.setattr(LocalArtifactStore, "resolve", drift_during_source_prepare)
    before = _cas_objects(frozen_replay)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        promoter.promote(compile_result=compile_result, prepared=prepared)

    assert raised.value.reason == "artifact_roots_overlap"
    assert drifted is True
    assert _cas_objects(frozen_replay) == before


def test_replay_root_drift_during_destination_write_never_returns_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    frozen_replay = LocalArtifactStore(replay_store.root)
    promoter = System2ReplayInputPromoter(
        source_artifact_store=source_store,
        replay_artifact_store=replay_store,
        scratch_root=tmp_path / "promotion-scratch",
    )
    original_put_file = LocalArtifactStore.put_file
    frozen_replay_root = replay_store.root
    drifted = False

    def drift_after_first_destination_write(self, source, **kwargs):
        nonlocal drifted
        ref = original_put_file(self, source, **kwargs)
        if self.root == frozen_replay_root and not drifted:
            replay_store.root = source_store.root
            drifted = True
        return ref

    monkeypatch.setattr(LocalArtifactStore, "put_file", drift_after_first_destination_write)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        promoter.promote(compile_result=compile_result, prepared=prepared)

    assert raised.value.reason == "artifact_roots_overlap"
    assert drifted is True
    with pytest.raises(ArtifactResolutionError) as missing_environment:
        frozen_replay.resolve(prepared.environment_package_ref)
    assert missing_environment.value.reason == "not_found"


def test_replay_root_drift_during_destination_verification_never_returns_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    frozen_replay = LocalArtifactStore(replay_store.root)
    promoter = System2ReplayInputPromoter(
        source_artifact_store=source_store,
        replay_artifact_store=replay_store,
        scratch_root=tmp_path / "promotion-scratch",
    )
    original_resolve = LocalArtifactStore.resolve
    frozen_replay_root = replay_store.root
    drifted = False

    def drift_after_first_destination_reread(self, ref):
        nonlocal drifted
        resolved = original_resolve(self, ref)
        if self.root == frozen_replay_root and not drifted:
            replay_store.root = source_store.root
            drifted = True
        return resolved

    monkeypatch.setattr(LocalArtifactStore, "resolve", drift_after_first_destination_reread)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        promoter.promote(compile_result=compile_result, prepared=prepared)

    assert raised.value.reason == "artifact_roots_overlap"
    assert drifted is True
    frozen_replay.resolve(prepared.environment_package_ref)


def test_source_root_drift_during_handoff_cannot_write_into_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_store, compile_result, prepared = _compile_and_prepare(tmp_path / "compile")
    replay_store = LocalArtifactStore(tmp_path / "replay-cas")
    frozen_replay = LocalArtifactStore(replay_store.root)
    promoter = System2ReplayInputPromoter(
        source_artifact_store=source_store,
        replay_artifact_store=replay_store,
        scratch_root=tmp_path / "promotion-scratch",
    )
    frozen_source_root = source_store.root
    original_put_file = LocalArtifactStore.put_file
    drifted = False

    def redirect_caller_source_before_environment_put(self, source, **kwargs):
        nonlocal drifted
        if (
            self.root == frozen_source_root
            and kwargs.get("name") == "environment_package"
            and not drifted
        ):
            source_store.root = replay_store.root
            drifted = True
        return original_put_file(self, source, **kwargs)

    monkeypatch.setattr(
        LocalArtifactStore,
        "put_file",
        redirect_caller_source_before_environment_put,
    )
    before = _cas_objects(frozen_replay)

    with pytest.raises(System2ReplayInputPromotionError) as raised:
        promoter.promote(compile_result=compile_result, prepared=prepared)

    assert raised.value.reason == "artifact_roots_overlap"
    assert drifted is True
    assert _cas_objects(frozen_replay) == before
