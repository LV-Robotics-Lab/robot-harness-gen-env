"""Real original Yuxin local engine over a clearly labelled Registry projection.

The declared OpenXSim source dependency must be on deployment PYTHONPATH.
The mesh/license below are unit fixtures, not a licensed production asset claim.
"""

import json
from types import SimpleNamespace

from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
from self_improving.harness.x2env.store import Store
from tests.self_improving.harness.x2env.test_asset_registry import prepared


def fixture(tmp_path):
    store = Store(tmp_path / "state")
    registry = AssetRegistry(store)
    root, files, report = prepared(tmp_path, store)
    proof = store.write_artifact(b"unit fixture license and source", "text/plain")
    version = registry.register(
        "unchanged-name",
        "container",
        root,
        "model.urdf",
        files=files,
        normalization_report=report,
        license=AssetLicense(spdx="CC0-1.0", source_url="https://example.org/unit", evidence=proof),
        source=AssetSource(kind="web", provider="fixture", source_ref="fixed", evidence=proof),
        receipt=proof,
    )
    return store, registry, version


def test_original_engine_search_binds_immutable_version_and_origin(tmp_path):
    from self_improving.harness.x2env.local_catalog import RegistryLocalCatalog

    store, registry, version = fixture(tmp_path)
    result = RegistryLocalCatalog(store, registry).search(
        SimpleNamespace(id="item", category="container"), output_root=tmp_path / "projection"
    )
    assert result.status == "succeeded" and result.versions == (version,)
    assert result.candidates[0].provider == "robotwin_local"
    assert result.candidates[0].license.spdx == "CC0-1.0"
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["scope"] == "exact_category_registry_projection"
    assert receipt["mapping"][0]["version_sha256"] == version.version_sha256
    assert registry.inspect(version.version_sha256) == version
    engine = json.loads(store.read_artifact(result.candidates[0].engine_record))
    assert engine["metadata"]["matched_phrases"] == ["container"]
    assert receipt["mapping"][0]["original_asset_id"] == "unchanged-name"


def test_budget_excess_is_failure_not_truncated_search(tmp_path):
    from self_improving.harness.x2env.local_catalog import RegistryLocalCatalog

    store, registry, version = fixture(tmp_path)
    result = RegistryLocalCatalog(store, registry, max_bytes=1).search(
        SimpleNamespace(id="item", category="container"), output_root=tmp_path / "projection"
    )
    assert result.status == "failed" and result.error_code == "local_catalog_byte_limit"
    assert not result.versions and not result.candidates
    assert not (tmp_path / "projection" / "assets").exists()


def test_raw_directory_is_not_an_existing_version(tmp_path):
    from self_improving.harness.x2env.local_catalog import RegistryLocalCatalog

    store = Store(tmp_path / "state")
    registry = AssetRegistry(store)
    (tmp_path / "raw.urdf").write_text('<robot name="raw"/>')
    result = RegistryLocalCatalog(store, registry).search(
        SimpleNamespace(id="raw", category="raw"), output_root=tmp_path / "projection"
    )
    assert result.status == "blocked" and not result.versions
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["raw_directory_import"] == "requires_normalization_and_registration"


def test_version_limit_rejects_whole_projection_even_with_limit_one(tmp_path):
    from self_improving.harness.x2env.local_catalog import RegistryLocalCatalog

    store, registry, first = fixture(tmp_path)
    root, files, report = prepared(tmp_path, store, mass="2")
    registry.register(
        "second",
        "container",
        root,
        "model.urdf",
        files=files,
        normalization_report=report,
        license=first.license,
        source=first.source,
        receipt=first.receipt,
    )
    result = RegistryLocalCatalog(store, registry, max_versions=1).search(
        SimpleNamespace(id="item", category="container"),
        output_root=tmp_path / "projection",
        limit=1,
    )
    assert result.status == "failed" and result.error_code == "local_catalog_version_limit"
    assert not result.versions and not (tmp_path / "projection" / "catalog.json").exists()
