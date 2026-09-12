"""Real CAS/normalization/registry; network, preparation, render and model are unit doubles."""

import hashlib
import json
from pathlib import Path

import pytest

from self_improving.harness.x2env.adapters.yuxin import (
    LicenseEvidence,
    ProviderCandidate,
    ProviderFetchResult,
    ProviderSearchResult,
)
from tests.self_improving.harness.x2env.test_resolver import VisualDouble, inputs


class ProviderDouble:
    def __init__(self, store, root):
        self.store, self.root, self.queries = store, root, []

    def search(self, entity, source, limit):
        self.queries.append((entity.category, source, limit))
        ref = self.store.write_artifact(b"explicit fixture metadata", "text/plain")
        self.candidate = ProviderCandidate(
            source="web",
            entity_id=entity.id,
            candidate_id="fixture",
            category=entity.category,
            provider="network_double",
            format="glb",
            download_url="https://example.org/box.glb",
            source_page="https://example.org/box",
            score=1.0,
            license=LicenseEvidence(
                status="declared",
                spdx="CC-BY-4.0",
                attribution="Fixture Author",
                evidence=ref,
                source_url="https://example.org/license",
            ),
            engine_record=ref,
        )
        return ProviderSearchResult(status="succeeded", candidates=(self.candidate,), receipt=ref)

    def fetch(self, candidate, output_dir):
        output_dir.mkdir()
        path = output_dir / "box.glb"
        path.write_bytes((self.root / "box.glb").read_bytes())
        ref = self.store.write_artifact(
            json.dumps({"raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}).encode(),
            "application/json",
        )
        return ProviderFetchResult(status="succeeded", source_path=str(path), receipt=ref)


def test_managed_query_is_used_once_without_mutating_scene_or_entity(tmp_path):
    from self_improving.harness.x2env.codex import CodexBackend
    from self_improving.harness.x2env.search_advisory import plan_search
    from self_improving.harness.x2env.web_resolver import WebAssetResolver
    from tests.self_improving.harness.x2env.test_codex import executable

    store, registry, _, scene, _ = inputs(tmp_path)
    original = store.read_artifact(scene)
    path = executable(
        tmp_path, {"query": "rectangular block", "reason": "broad search description"}
    )
    backend = CodexBackend(path, hashlib.sha256(path.read_bytes()).hexdigest(), "double", store)
    plans = []

    def query_port(entity):
        result = plan_search(
            backend, scene, entity, store=store, output_root=tmp_path / "query", timeout=10
        )
        plans.append(result)
        return result

    class QueryProvider(ProviderDouble):
        def search(self, entity, source, limit, *, query):
            assert entity.category == "box" and query == "rectangular block"
            result = super().search(entity, source, limit)
            ref = store.write_artifact(
                json.dumps(
                    {"entity_id": entity.id, "category": entity.category, "query": query}
                ).encode(),
                "application/json",
            )
            return result.model_copy(update={"receipt": ref})

    provider = QueryProvider(store, tmp_path)
    result = WebAssetResolver(
        store, registry, provider, backend, None, lambda *args: None, query_port=query_port
    ).resolve(scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve")
    assert result.error_code == "missing_physical_metadata"
    assert len(plans) == len(provider.queries) == 1
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["candidates"][0]["query_advisory"] == plans[0].model_dump(mode="json")
    assert store.read_artifact(scene) == original


@pytest.mark.parametrize("fault", ["missing_backend", "changed_query", "changed_category"])
def test_failed_or_rebound_query_never_falls_back_to_original_category(tmp_path, fault):
    from self_improving.harness.x2env.search_advisory import plan_search
    from self_improving.harness.x2env.web_resolver import WebAssetResolver

    store, registry, _, scene, _ = inputs(tmp_path)
    provider = ProviderDouble(store, tmp_path)
    calls = []

    def query(entity):
        result = plan_search(
            None, scene, entity, store=store, output_root=tmp_path / "query", timeout=10
        )
        calls.append(result)
        if fault == "changed_query":
            result = result.model_copy(update={"query": "another object"})
        if fault == "changed_category":
            result = result.model_copy(update={"original_category": "another"})
        return result

    result = WebAssetResolver(
        store, registry, provider, None, None, None, query_port=query
    ).resolve(scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve")
    assert result.status == "blocked" and not result.resolved.assets
    assert len(calls) == 1 and provider.queries == []
    evidence = json.loads(store.read_artifact(result.receipt))
    assert "query_advisory" in evidence["candidates"][0]
    if fault == "missing_backend":
        assert result.error_code == "blocked_external_resource"


def test_failed_managed_preparation_preserves_actual_receipt_and_reason(tmp_path):
    from self_improving.harness.x2env.asset_preparation import PreparationResult
    from self_improving.harness.x2env.web_resolver import WebAssetResolver

    store, registry, _, scene, _ = inputs(tmp_path)
    evidence = store.write_artifact(b"model timeout boundary double", "text/plain")
    failure = PreparationResult(
        status="failed", parameters=None, receipt=evidence, error_code="model_timeout"
    )
    result = WebAssetResolver(
        store, registry, ProviderDouble(store, tmp_path), None, None, lambda *args: failure
    ).resolve(scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve")
    assert result.status == "blocked" and result.error_code == "model_timeout"
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["candidates"][0]["preparation_result"] == failure.model_dump(mode="json")


def test_missing_physical_metadata_blocks_without_registering_or_resolving(tmp_path):
    from self_improving.harness.x2env.web_resolver import WebAssetResolver

    store, registry, _, scene, _ = inputs(tmp_path)
    provider = ProviderDouble(store, tmp_path)
    before = registry.find("box")
    result = WebAssetResolver(store, registry, provider, None, None, lambda *args: None).resolve(
        scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve"
    )
    assert result.status == "blocked" and result.error_code == "missing_physical_metadata"
    assert not result.resolved.assets and registry.find("box") == before
    assert provider.queries == [("box", "web", 8)]


def prepared(store, scene):
    def prepare(entity, candidate, fetched):
        from self_improving.harness.x2env.web_resolver import NormalizationParameters

        values = dict(
            dimensions_m=(0.1, 0.1, 0.1),
            up_axis="Z",
            mass_kg=0.1,
            friction=0.6,
            base_color=None,
            declared_color=None,
            dimensions_basis="deployment_supplied",
            up_axis_basis="deployment_supplied",
            mass_basis="deployment_supplied",
            friction_basis="deployment_supplied",
            color_basis=None,
            advisory_receipt=None,
        )
        evidence = store.write_artifact(
            json.dumps(
                {
                    "scene_ir": scene.model_dump(mode="json"),
                    "entity_id": entity.id,
                    "candidate_id": candidate.candidate_id,
                    "fetch_receipt": fetched.receipt.model_dump(mode="json"),
                    "source_sha256": hashlib.sha256(
                        Path(fetched.source_path).read_bytes()
                    ).hexdigest(),
                    "parameters": values,
                }
            ).encode(),
            "application/json",
        )
        return NormalizationParameters(**values, evidence=evidence)

    return prepare


def preview_double(store, image):
    def render(version, **kwargs):
        from self_improving.harness.x2env.asset_preview import AssetPreviewProof

        ref = store.write_artifact(
            json.dumps(
                {
                    "scope": "asset_preview_scope",
                    "status": "passed",
                    "version_sha256": version.version_sha256,
                    "outputs": {"frame.png": image.model_dump(mode="json")},
                }
            ).encode(),
            "application/json",
        )
        return AssetPreviewProof(
            status="passed", version_sha256=version.version_sha256, image=image, receipt=ref
        )

    return render


def test_explicit_preparation_real_normalization_registry_and_visual_binding(tmp_path):
    from self_improving.harness.x2env.web_resolver import WebAssetResolver

    store, registry, _, scene, image = inputs(tmp_path)
    provider = ProviderDouble(store, tmp_path)
    result = WebAssetResolver(
        store,
        registry,
        provider,
        VisualDouble(store),
        preview_double(store, image),
        prepared(store, scene),
    ).resolve(scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve")
    assert result.status == "succeeded"
    asset = result.resolved.assets[0]
    version = registry.inspect(asset.version_sha256)
    assert asset.acquisition_source == "web" and asset.selection == "exact"
    assert version.source.kind == "web" and version.license.attribution == "Fixture Author"
    assert version.physical_evaluated is False
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["candidates"][0]["preview"]["image"] == image.model_dump(mode="json")


def test_missing_preview_output_closure_cannot_resolve(tmp_path):
    from self_improving.harness.x2env.web_resolver import WebAssetResolver

    store, registry, _, scene, image = inputs(tmp_path)

    def incomplete(version, **kwargs):
        proof = preview_double(store, image)(version)
        document = json.loads(store.read_artifact(proof.receipt))
        document["outputs"]["stdout.log"] = dict(
            sha256="0" * 64, size_bytes=5, media_type="text/plain"
        )
        return proof.model_copy(
            update={
                "receipt": store.write_artifact(json.dumps(document).encode(), "application/json")
            }
        )

    backend = VisualDouble(store)
    result = WebAssetResolver(
        store,
        registry,
        ProviderDouble(store, tmp_path),
        backend,
        incomplete,
        prepared(store, scene),
    ).resolve(scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve")
    assert result.status == "blocked" and not result.resolved.assets
    assert not backend.calls


@pytest.mark.parametrize("fault", ["dimensions", "preparation", "visual", "partial"])
def test_candidates_rejected_without_retry_and_partial_assets_retained(tmp_path, fault):
    from self_improving.harness.x2env.web_resolver import WebAssetResolver

    store, registry, _, scene, image = inputs(
        tmp_path,
        extra_entity=fault == "partial",
        dimensions=[0.2, 0.1, 0.1] if fault == "dimensions" else None,
    )
    original = prepared(store, scene)

    def prepare(entity, candidate, fetched):
        if fault == "partial" and entity.id == "mouse":
            return None
        value = original(entity, candidate, fetched)
        return value.model_copy(update={"mass_kg": 0.2}) if fault == "preparation" else value

    backend = VisualDouble(store, verdict="mismatch" if fault == "visual" else "match")
    provider = ProviderDouble(store, tmp_path)
    result = WebAssetResolver(
        store, registry, provider, backend, preview_double(store, image), prepare
    ).resolve(
        scene,
        allowed_sources=("web", "reconstruction"),
        allow_cousin=True,
        output_root=tmp_path / "resolve",
    )
    assert result.status == "blocked" and result.next_source == "reconstruction"
    assert len(result.resolved.assets) == (1 if fault == "partial" else 0)
    assert len(backend.calls) == (1 if fault in {"visual", "partial"} else 0)
    assert len(provider.queries) == (2 if fault == "partial" else 1)
