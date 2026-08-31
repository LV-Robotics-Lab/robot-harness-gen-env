from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.events import RecordingEventSink
from self_improving.harness.handlers.text2env_replay import (
    build_text2env_replay_wiring,
    text2env_replay_descriptor,
)
from self_improving.harness.package_store import PackageStore
from self_improving.harness.registry import DependencyResolutionError, SkillRegistry
from self_improving.harness.replay_dependencies import (
    REPLAY_DEPENDENCY_NAMES,
    Text2EnvReplayDependencyResolver,
    canonical_identity_sha256,
    replay_dependency_set,
    require_replay_dependencies,
)
from self_improving.harness.runtime_assets import RuntimeAssetStore
from self_improving.harness.schemas import DependencyRef, RunStatus
from tests.self_improving.harness.test_text2env_replay_handler import (
    RecordingExecutor,
    RecordingMediaVerifier,
    _capability_document,
    _fixture,
    _handler_configuration,
)


def _resolver(tmp_path: Path):
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    verifier = RecordingMediaVerifier()
    config_sha256 = _handler_configuration(
        fixture,
        executor,
        verifier,
        checkpoint_steps=4,
    )
    resolver = Text2EnvReplayDependencyResolver(
        artifact_store=fixture.store,
        package_store=PackageStore(fixture.store),
        allowed_asset_roots=(fixture.robotwin_root,),
        runtime_executor_identity=executor.identity,
        media_verifier_identity=verifier.identity,
        handler_config_sha256=config_sha256,
        expected_capability_sha256=executor.capability_sha256,
        work_root=tmp_path / "dependency-work",
    )
    return fixture, executor, verifier, resolver


def _put_json(
    store: LocalArtifactStore,
    path: Path,
    *,
    name: str,
    schema_version: str,
    payload: dict[str, object],
):
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def test_resolver_configuration_rejects_mixed_stores_and_invalid_identities(
    tmp_path: Path,
) -> None:
    _fixture_value, _executor, _verifier, resolver = _resolver(tmp_path)

    with pytest.raises(TypeError, match="artifact_store"):
        replace(resolver, artifact_store=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="package_store"):
        replace(resolver, package_store=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="share one CAS"):
        replace(resolver, artifact_store=LocalArtifactStore(tmp_path / "other-cas"))
    with pytest.raises(ValueError, match="handler_config_sha256"):
        replace(resolver, handler_config_sha256="bad")
    with pytest.raises(ValueError, match="expected_capability_sha256"):
        replace(resolver, expected_capability_sha256="bad")
    with pytest.raises(ValueError, match="runtime executor identity"):
        replace(resolver, runtime_executor_identity=object())
    with pytest.raises(ValueError, match="media verifier identity"):
        replace(resolver, media_verifier_identity=object())
    with pytest.raises(TypeError, match="work_root"):
        replace(resolver, work_root="bad")  # type: ignore[arg-type]


def test_resolver_configuration_rejects_unsafe_work_roots(tmp_path: Path) -> None:
    _fixture_value, _executor, _verifier, resolver = _resolver(tmp_path)
    regular_file = tmp_path / "work-file"
    regular_file.write_bytes(b"not a directory")
    with pytest.raises(ValueError, match="real directory"):
        replace(resolver, work_root=regular_file)

    target = tmp_path / "real-work"
    target.mkdir()
    linked = tmp_path / "linked-work"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        replace(resolver, work_root=linked)

    child = target / "child"
    child.mkdir()
    with pytest.raises(ValueError, match="traverses a symlink"):
        replace(resolver, work_root=linked / "child")


@pytest.mark.parametrize(
    ("identity", "message"),
    [
        (object(), "unavailable"),
        (SimpleNamespace(canonical_bytes="{}", sha256="a" * 64), "must be bytes"),
        (SimpleNamespace(canonical_bytes=b"{}", sha256="a" * 64), "inconsistent"),
        (
            SimpleNamespace(
                canonical_bytes=b"\xff",
                sha256=hashlib.sha256(b"\xff").hexdigest(),
            ),
            "invalid JSON",
        ),
        (
            SimpleNamespace(
                canonical_bytes=b'{"a":1,"a":2}',
                sha256=hashlib.sha256(b'{"a":1,"a":2}').hexdigest(),
            ),
            "invalid JSON",
        ),
        (
            SimpleNamespace(
                canonical_bytes=b'{"a":NaN}',
                sha256=hashlib.sha256(b'{"a":NaN}').hexdigest(),
            ),
            "invalid JSON",
        ),
        (
            SimpleNamespace(
                canonical_bytes=b"[]",
                sha256=hashlib.sha256(b"[]").hexdigest(),
            ),
            "one JSON object",
        ),
    ],
)
def test_canonical_identity_contract_is_fail_closed(identity: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        canonical_identity_sha256(identity, label="fixture")


def test_parameter_aware_resolver_returns_exact_sorted_dependency_set(tmp_path: Path) -> None:
    fixture, executor, verifier, resolver = _resolver(tmp_path)

    dependencies = resolver.resolve("text2env.replay@1.0.0", fixture.value)

    assert tuple(item.name for item in dependencies) == tuple(sorted(REPLAY_DEPENDENCY_NAMES))
    assert len(dependencies) == 5
    assert next(item for item in dependencies if item.name.endswith("executor")).sha256 == (
        executor.identity.sha256
    )
    assert (
        next(item for item in dependencies if item.name.endswith("media_verifier")).sha256
        == verifier.identity.sha256
    )
    assert next(item for item in dependencies if item.name.endswith("capability")).sha256 == (
        executor.capability_sha256
    )
    assert not tuple((tmp_path / "dependency-work").iterdir())


def test_production_wiring_carries_one_dependency_set_through_registry_context(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    verifier = RecordingMediaVerifier()
    wiring = build_text2env_replay_wiring(
        artifact_store=fixture.store,
        package_store=PackageStore(fixture.store),
        runtime_executor=executor,
        media_verifier=verifier,
        work_root=tmp_path / "replay-work",
        dependency_work_root=tmp_path / "dependency-work",
        allowed_asset_roots=(fixture.robotwin_root,),
        expected_capability_sha256=executor.capability_sha256,
        checkpoint_steps=4,
    )
    report = _put_json(
        fixture.store,
        tmp_path / "qualification-report.json",
        name="replay_qualification_report",
        schema_version="harness.skill_qualification_report.v1",
        payload={"case": "registry-chain", "status": "pass"},
    )
    qualification = _put_json(
        fixture.store,
        tmp_path / "qualification.json",
        name="replay_qualification",
        schema_version="harness.skill_qualification.v1",
        payload={
            "skill_ref": "text2env.replay@1.0.0",
            "status": "pass",
            "deterministic_case_id": "registry-chain",
            "regression_command": (
                "pytest -q tests/self_improving/harness/test_replay_dependencies.py"
            ),
            "report_sha256": report.sha256,
        },
    )
    descriptor = text2env_replay_descriptor(
        qualification_artifact=qualification,
        implementation_sha256="d" * 64,
    )
    registry = SkillRegistry(
        artifact_resolver=fixture.store,
        dependency_resolver=wiring.dependency_resolver,
        event_sink=RecordingEventSink(),
        clock=lambda: datetime(2026, 8, 31, tzinfo=timezone.utc),
        run_id_factory=lambda: UUID("12345678-1234-4234-9234-123456789abc"),
    )
    registry.register(descriptor, wiring.handler)

    state = registry.invoke(
        "text2env.replay",
        "1.0.0",
        fixture.value.model_dump(mode="json"),
    )

    assert state.status is RunStatus.SUCCEEDED
    invocation = registry.invocation(state.run_id)
    assert invocation is not None
    assert tuple(item.name for item in invocation.dependencies) == tuple(
        sorted(REPLAY_DEPENDENCY_NAMES)
    )
    receipt_ref = next(
        artifact for artifact in state.artifacts if artifact.name == "replay_execution_receipt"
    )
    receipt = json.loads(fixture.store.resolve(receipt_ref).path.read_text(encoding="utf-8"))
    assert receipt["invocation_dependencies"] == [
        item.model_dump(mode="json") for item in invocation.dependencies
    ]
    assert wiring.handler.dependency_resolver is wiring.dependency_resolver


def test_resolver_snapshot_dependency_equals_independent_runtime_asset_snapshot(
    tmp_path: Path,
) -> None:
    fixture, _executor, _verifier, resolver = _resolver(tmp_path)
    dependencies = resolver.resolve("text2env.replay@1.0.0", fixture.value)
    snapshot_dependency = next(
        item for item in dependencies if item.name.endswith("runtime_assets")
    )

    package_root = PackageStore(fixture.store).materialize(
        fixture.value.environment_package.package_manifest,
        tmp_path / "independent-package",
    )
    from scene_gen.catalog import load_catalog
    from scene_gen.schema import ResolvedSceneSpec

    resolved = ResolvedSceneSpec.model_validate_json(
        (package_root / "resolved_scene.json").read_text(encoding="utf-8")
    )
    catalog = load_catalog(
        fixture.store.resolve(fixture.value.environment_package.asset_catalog).path
    )
    snapshot = RuntimeAssetStore(fixture.store).snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(fixture.robotwin_root,),
    )

    assert snapshot_dependency.sha256 == snapshot.manifest.sha256


def test_resolver_has_no_snapshot_side_channel_between_inputs(tmp_path: Path) -> None:
    fixture, _executor, _verifier, resolver = _resolver(tmp_path)
    first = resolver.resolve("text2env.replay@1.0.0", fixture.value)
    added = fixture.robotwin_root / "assets/objects/071_can/late-bound.bin"
    added.write_bytes(b"second snapshot")
    second = resolver.resolve("text2env.replay@1.0.0", fixture.value)

    first_snapshot = next(item for item in first if item.name.endswith("runtime_assets"))
    second_snapshot = next(item for item in second if item.name.endswith("runtime_assets"))
    assert first_snapshot.sha256 != second_snapshot.sha256
    assert resolver.resolve("text2env.replay@1.0.0", fixture.value) == second


def test_resolver_maps_unavailable_package_and_catalog_to_dependency_errors(
    tmp_path: Path,
) -> None:
    fixture, _executor, _verifier, resolver = _resolver(tmp_path)
    missing_manifest = fixture.value.environment_package.package_manifest.model_copy(
        update={"sha256": "f" * 64, "uri": "artifact://sha256/" + "f" * 64}
    )
    missing_package = fixture.value.environment_package.model_copy(
        update={"package_manifest": missing_manifest}
    )
    with pytest.raises(DependencyResolutionError, match="manifest_unavailable"):
        resolver.resolve(
            "text2env.replay@1.0.0",
            fixture.value.model_copy(update={"environment_package": missing_package}),
        )
    assert not tuple(resolver.work_root.iterdir())

    invalid_catalog_path = tmp_path / "invalid-catalog.json"
    invalid_catalog_path.write_text("{}", encoding="utf-8")
    invalid_catalog = fixture.store.put_file(
        invalid_catalog_path,
        name="invalid_asset_catalog",
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )
    package = fixture.value.environment_package.model_copy(
        update={"asset_catalog": invalid_catalog}
    )
    with pytest.raises(DependencyResolutionError, match="catalog dependency is unavailable"):
        resolver.resolve(
            "text2env.replay@1.0.0",
            fixture.value.model_copy(update={"environment_package": package}),
        )
    assert not tuple(resolver.work_root.iterdir())


def test_resolver_rejects_package_catalog_binding_disagreement(tmp_path: Path) -> None:
    fixture, _executor, _verifier, resolver = _resolver(tmp_path)
    package = fixture.value.environment_package.model_copy(update={"scene_spec_sha256": "f" * 64})

    with pytest.raises(DependencyResolutionError, match="package/catalog binding failed"):
        resolver.resolve(
            "text2env.replay@1.0.0",
            fixture.value.model_copy(update={"environment_package": package}),
        )

    assert not tuple(resolver.work_root.iterdir())


@pytest.mark.parametrize(
    "payload",
    [
        b"[]",
        b'{"schema_version":"a","schema_version":"b"}',
        b'{"schema_version":NaN}',
        b"{",
    ],
    ids=["not-object", "duplicate-key", "non-standard-number", "malformed"],
)
def test_resolver_reparses_materialized_package_strictly(
    tmp_path: Path,
    payload: bytes,
) -> None:
    fixture, _executor, _verifier, resolver = _resolver(tmp_path)

    class CorruptingPackageStore(PackageStore):
        def materialize(self, manifest, destination):
            root = super().materialize(manifest, destination)
            (root / "package_manifest.json").write_bytes(payload)
            return root

    resolver = replace(resolver, package_store=CorruptingPackageStore(fixture.store))

    with pytest.raises(DependencyResolutionError, match="package content is invalid"):
        resolver.resolve("text2env.replay@1.0.0", fixture.value)

    assert not tuple(resolver.work_root.iterdir())


def test_resolver_maps_runtime_asset_snapshot_failure_and_cleans_scratch(tmp_path: Path) -> None:
    fixture, _executor, _verifier, resolver = _resolver(tmp_path)
    selected = fixture.selected_files[0]
    target = selected.with_name(selected.name + ".real")
    selected.rename(target)
    selected.symlink_to(target)

    with pytest.raises(DependencyResolutionError, match="runtime asset dependency"):
        resolver.resolve("text2env.replay@1.0.0", fixture.value)

    assert not tuple(resolver.work_root.iterdir())


@pytest.mark.parametrize(
    ("skill_ref", "valid_input"),
    [
        ("text2env.compile@1.0.0", True),
        ("text2env.replay@1.0.0", False),
    ],
)
def test_resolver_rejects_wrong_skill_or_parameter_type(
    tmp_path: Path,
    skill_ref: str,
    valid_input: bool,
) -> None:
    fixture, _executor, _verifier, resolver = _resolver(tmp_path)
    effective = fixture.value if valid_input else None

    with pytest.raises(DependencyResolutionError):
        resolver.resolve(skill_ref, effective)  # type: ignore[arg-type]


def test_dependency_set_comparison_rejects_missing_extra_duplicate_and_mismatch(
    tmp_path: Path,
) -> None:
    fixture, executor, verifier, resolver = _resolver(tmp_path)
    expected = resolver.resolve("text2env.replay@1.0.0", fixture.value)

    assert require_replay_dependencies(expected, expected) == expected
    variants = (
        expected[:-1],
        (*expected, DependencyRef(name="extra", version="1", sha256="a" * 64)),
        (*expected, expected[0]),
        (expected[0].model_copy(update={"sha256": "f" * 64}), *expected[1:]),
    )
    for actual in variants:
        with pytest.raises(DependencyResolutionError):
            require_replay_dependencies(actual, expected)

    with pytest.raises(DependencyResolutionError, match="expected.*invalid"):
        require_replay_dependencies(expected, (*expected, expected[0]))

    rebuilt = replay_dependency_set(
        runtime_executor_identity=executor.identity,
        handler_config_sha256=next(
            item for item in expected if item.name.endswith("handler_config")
        ).sha256,
        expected_capability_sha256=executor.capability_sha256,
        runtime_asset_snapshot_sha256=next(
            item for item in expected if item.name.endswith("runtime_assets")
        ).sha256,
        media_verifier_identity=verifier.identity,
    )
    assert rebuilt == expected


@pytest.mark.parametrize(
    "field_name",
    [
        "handler_config_sha256",
        "expected_capability_sha256",
        "runtime_asset_snapshot_sha256",
    ],
)
def test_dependency_set_rejects_invalid_digest_inputs(
    tmp_path: Path,
    field_name: str,
) -> None:
    _fixture_value, executor, verifier, _resolver_value = _resolver(tmp_path)
    values = {
        "runtime_executor_identity": executor.identity,
        "handler_config_sha256": "a" * 64,
        "expected_capability_sha256": "b" * 64,
        "runtime_asset_snapshot_sha256": "c" * 64,
        "media_verifier_identity": verifier.identity,
    }
    values[field_name] = "bad"

    with pytest.raises(ValueError, match=field_name):
        replay_dependency_set(**values)


def test_dependency_comparison_rejects_invalid_container_and_record_types(tmp_path: Path) -> None:
    fixture, _executor, _verifier, resolver = _resolver(tmp_path)
    expected = resolver.resolve("text2env.replay@1.0.0", fixture.value)

    with pytest.raises(DependencyResolutionError, match="must be tuples"):
        require_replay_dependencies(list(expected), expected)  # type: ignore[arg-type]
    with pytest.raises(DependencyResolutionError, match="must be tuples"):
        require_replay_dependencies(expected, list(expected))  # type: ignore[arg-type]
    with pytest.raises(DependencyResolutionError, match="records are invalid"):
        require_replay_dependencies((*expected[:-1], object()), expected)  # type: ignore[arg-type]
    with pytest.raises(DependencyResolutionError, match="records are invalid"):
        require_replay_dependencies(expected, (*expected[:-1], object()))  # type: ignore[arg-type]
    with pytest.raises(DependencyResolutionError, match="expected.*invalid"):
        require_replay_dependencies(expected, expected[:-1])
