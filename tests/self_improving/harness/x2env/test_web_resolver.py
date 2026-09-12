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
    def render(version):
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

    def incomplete(version):
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
