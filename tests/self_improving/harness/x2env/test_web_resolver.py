"""Real CAS/normalization/registry; network, preparation, render and model are unit doubles."""

import hashlib
import json
import time
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


@pytest.mark.parametrize("second_match", [False, True])
def test_web_query_revision_stops_after_one_retry_and_retains_both_receipts(tmp_path, second_match):
    from self_improving.harness.x2env.codex import CodexBackend
    from self_improving.harness.x2env.search_advisory import plan_search
    from self_improving.harness.x2env.web_resolver import WebAssetResolver
    from tests.self_improving.harness.x2env.test_codex import executable

    store, registry, _, scene, image = inputs(tmp_path)
    program = executable(tmp_path, {"query": "alternative name", "reason": "broader search"})
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "double", store
    )
    calls = []

    class EmptyProvider(ProviderDouble):
        def search(self, entity, source, limit, *, query=None):
            calls.append(query)
            ref = store.write_artifact(
                json.dumps(
                    {
                        "entity_id": entity.id,
                        "category": entity.category,
                        "query": query,
                    }
                ).encode(),
                "application/json",
            )
            if second_match and query is not None:
                return super().search(entity, source, limit).model_copy(update={"receipt": ref})
            return ProviderSearchResult(
                status="blocked",
                candidates=(),
                receipt=ref,
                error_code="asset_not_found",
            )

    resolver = WebAssetResolver(
        store,
        registry,
        EmptyProvider(store, tmp_path),
        VisualDouble(store),
        preview_double(store, image),
        prepared(store, scene),
        retry_query_port=lambda entity, failure: plan_search(
            backend,
            scene,
            entity,
            store=store,
            output_root=tmp_path / "query",
            timeout=10,
            previous_failure=failure,
        ),
    )
    result = resolver.resolve(
        scene,
        allowed_sources=("web",),
        allow_cousin=False,
        output_root=tmp_path / "resolve",
    )
    assert result.status == ("succeeded" if second_match else "blocked")
    assert bool(result.resolved.assets) == second_match
    assert calls == [None, "alternative name"]
    receipt = json.loads(store.read_artifact(result.receipt))
    assert len(receipt["attempts"]) == 2 and receipt["query_revisions"] == 1
    assert (tmp_path / "resolve/receipt.json").is_file()
    assert (tmp_path / "resolve/query-revision/receipt.json").is_file()


@pytest.mark.parametrize("error", ["blocked_license", "provider_offline", "resolver_timeout"])
def test_web_external_failure_never_spends_query_revision(tmp_path, error):
    from self_improving.harness.x2env.web_resolver import WebAssetResolver

    store, registry, _, scene, _ = inputs(tmp_path)

    class Unavailable:
        def search(self, *args, **kwargs):
            return ProviderSearchResult(
                status="blocked",
                candidates=(),
                error_code=error,
                receipt=store.write_artifact(b"{}", "application/json"),
            )

    def forbidden(*args):
        raise AssertionError("resource failure must not retry the model")

    result = WebAssetResolver(
        store,
        registry,
        Unavailable(),
        None,
        None,
        None,
        retry_query_port=forbidden,
    ).resolve(scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve")
    assert result.error_code == error and not result.resolved.assets


def test_revised_query_never_fetches_the_same_failed_candidate_twice(tmp_path):
    from self_improving.harness.x2env.codex import CodexBackend
    from self_improving.harness.x2env.search_advisory import plan_search
    from self_improving.harness.x2env.web_resolver import WebAssetResolver
    from tests.self_improving.harness.x2env.test_codex import executable

    store, registry, _, scene, image = inputs(tmp_path)
    program = executable(tmp_path, {"query": "alternative name", "reason": "same object"})
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "double", store
    )
    fetches = []

    class SameCandidate(ProviderDouble):
        def search(self, entity, source, limit, *, query=None):
            result = super().search(entity, source, limit)
            receipt = store.write_artifact(
                json.dumps(
                    {
                        "entity_id": entity.id,
                        "category": entity.category,
                        "query": query,
                    }
                ).encode(),
                "application/json",
            )
            return result.model_copy(update={"receipt": receipt})

        def fetch(self, candidate, output_dir):
            fetches.append(candidate.candidate_id)
            return super().fetch(candidate, output_dir)

    def unsupported(*args):
        raise ValueError("unsupported material fields")

    result = WebAssetResolver(
        store,
        registry,
        SameCandidate(store, tmp_path),
        VisualDouble(store),
        preview_double(store, image),
        unsupported,
        retry_query_port=lambda entity, failure: plan_search(
            backend,
            scene,
            entity,
            store=store,
            output_root=tmp_path / "query",
            timeout=10,
            previous_failure=failure,
        ),
    ).resolve(scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve")
    assert result.status == "blocked" and not result.resolved.assets
    assert fetches == ["fixture"]


@pytest.mark.parametrize(
    "override",
    [
        {"timeout": 0},
        {"timeout": True},
        {"allow_cousin": 1},
        {"allowed_sources": ()},
        {"allowed_sources": ("web", "web")},
        {"allowed_sources": ("unknown",)},
        {"output_root": Path("relative")},
    ],
)
def test_invalid_web_configuration_does_not_call_provider(tmp_path, override):
    from self_improving.harness.x2env.web_resolver import WebAssetResolver

    store, registry, _, scene, _ = inputs(tmp_path)
    provider = ProviderDouble(store, tmp_path)
    args = dict(allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve")
    args.update(override)
    with pytest.raises(ValueError, match="^invalid web resolver configuration$"):
        WebAssetResolver(store, registry, provider, None, None, None).resolve(scene, **args)
    assert provider.queries == []


@pytest.mark.parametrize("fault", ["invalid_scene", "source", "filter"])
def test_unavailable_scene_or_scope_never_searches(tmp_path, fault):
    from self_improving.harness.x2env.web_resolver import WebAssetResolver

    store, registry, _, scene, _ = inputs(tmp_path)
    provider = ProviderDouble(store, tmp_path)
    resolver = WebAssetResolver(store, registry, provider, None, None, None)
    if fault == "filter":
        with pytest.raises(ValueError, match="^invalid resolver entity filter$"):
            resolver.resolve(
                scene,
                allowed_sources=("web",),
                allow_cousin=False,
                output_root=tmp_path / "resolve",
                entity_ids=("not-present",),
            )
    else:
        if fault == "invalid_scene":
            scene = store.write_artifact(b"{}", "application/json")
        result = resolver.resolve(
            scene, allowed_sources=("local",), allow_cousin=False, output_root=tmp_path / "resolve"
        )
        assert result.error_code == (
            "invalid_scene_evidence" if fault == "invalid_scene" else "web_source_not_allowed"
        )
        assert result.status == "blocked" and not result.resolved.assets
    assert not provider.queries


def test_empty_explicit_entity_filter_succeeds_without_search(tmp_path):
    from self_improving.harness.x2env.web_resolver import WebAssetResolver

    store, registry, _, scene, _ = inputs(tmp_path)
    provider = ProviderDouble(store, tmp_path)
    result = WebAssetResolver(store, registry, provider, None, None, None).resolve(
        scene,
        allowed_sources=("web",),
        allow_cousin=False,
        output_root=tmp_path / "resolve",
        entity_ids=(),
    )
    assert result.status == "succeeded" and not result.resolved.assets
    assert not provider.queries


@pytest.mark.parametrize("fault", ["binding", "failure", "failure_default"])
def test_actual_query_receipt_cannot_be_rebound_by_provider(tmp_path, fault):
    from self_improving.harness.x2env.codex import CodexBackend
    from self_improving.harness.x2env.search_advisory import plan_search
    from self_improving.harness.x2env.web_resolver import WebAssetResolver
    from tests.self_improving.harness.x2env.test_codex import executable

    store, registry, _, scene, _ = inputs(tmp_path)
    program = executable(tmp_path, {"query": "rectangular block", "reason": "fixture query"})
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "double", store
    )

    class FailingQueryProvider(ProviderDouble):
        def search(self, entity, source, limit, *, query):
            self.queries.append((entity.category, source, limit))
            receipt = store.write_artifact(
                json.dumps(
                    {
                        "entity_id": entity.id,
                        "category": entity.category,
                        "query": "rebound" if fault == "binding" else query,
                    }
                ).encode(),
                "application/json",
            )
            return ProviderSearchResult(
                status="failed",
                candidates=(),
                receipt=receipt,
                error_code="provider_offline" if fault == "failure" else None,
            )

        def fetch(self, *args):
            raise AssertionError("failed search must never fetch")

    provider = FailingQueryProvider(store, tmp_path)
    result = WebAssetResolver(
        store,
        registry,
        provider,
        backend,
        None,
        None,
        query_port=lambda entity: plan_search(
            backend, scene, entity, store=store, output_root=tmp_path / "query", timeout=10
        ),
    ).resolve(scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve")
    assert result.status == "blocked" and not result.resolved.assets
    assert len(provider.queries) == 1
    assert (
        result.error_code
        == {
            "binding": "invalid_provider_evidence",
            "failure": "provider_offline",
            "failure_default": "web_search_failed",
        }[fault]
    )
    rows = json.loads(store.read_artifact(result.receipt))["candidates"]
    assert "query_advisory" in rows[0]
    if fault == "binding":
        assert rows[1]["reason"] == "provider_query_binding_mismatch"


@pytest.mark.parametrize(
    "phase", ["before_search", "after_search", "after_prepare", "after_preview", "after_model"]
)
def test_shared_deadline_stops_after_real_stage_boundary(tmp_path, monkeypatch, phase):
    from self_improving.harness.x2env.web_resolver import WebAssetResolver

    store, registry, _, scene, image = inputs(tmp_path)
    elapsed = [0.0]
    reads = [0]

    def clock():
        reads[0] += 1
        if phase == "before_search" and reads[0] > 1:
            return 600.0
        return elapsed[0]

    monkeypatch.setattr(time, "monotonic", clock)

    class SlowProvider(ProviderDouble):
        def search(self, *args, **kwargs):
            result = super().search(*args, **kwargs)
            if phase == "after_search":
                elapsed[0] = 599.5
            return result

    def prepare(*args):
        result = prepared(store, scene)(*args)
        if phase == "after_prepare":
            elapsed[0] = 599.5
        return result

    def preview(*args, **kwargs):
        result = preview_double(store, image)(*args, **kwargs)
        if phase == "after_preview":
            elapsed[0] = 599.5
        return result

    class SlowVisual(VisualDouble):
        def assess_asset_candidates(self, *args, **kwargs):
            result = super().assess_asset_candidates(*args, **kwargs)
            if phase == "after_model":
                elapsed[0] = 600.0
            return result

    provider, backend = SlowProvider(store, tmp_path), SlowVisual(store)
    result = WebAssetResolver(store, registry, provider, backend, preview, prepare).resolve(
        scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve"
    )
    assert result.status == "blocked" and result.error_code == "resolver_timeout"
    assert not result.resolved.assets
    assert len(provider.queries) == (0 if phase == "before_search" else 1)
    assert len(backend.calls) == (1 if phase == "after_model" else 0)


@pytest.mark.parametrize("fault", ["proposal_binding", "query_deadline"])
def test_query_proposal_binding_and_elapsed_query_stop_before_search(tmp_path, monkeypatch, fault):
    from self_improving.harness.x2env.codex import CodexBackend
    from self_improving.harness.x2env.contracts import SceneIR
    from self_improving.harness.x2env.search_advisory import plan_search
    from self_improving.harness.x2env.web_resolver import WebAssetResolver
    from tests.self_improving.harness.x2env.test_codex import executable

    store, registry, _, scene, _ = inputs(tmp_path)
    entity = SceneIR.model_validate_json(store.read_artifact(scene)).entities[0]
    program = executable(tmp_path, {"query": "rectangular block", "reason": "fixture query"})
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "double", store
    )
    actual = plan_search(
        backend, scene, entity, store=store, output_root=tmp_path / "query", timeout=10
    )
    elapsed = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])

    def query(entity):
        if fault == "query_deadline":
            elapsed[0] = 599.5
            return actual
        record = json.loads(store.read_artifact(actual.receipt))
        record["proposal"]["query"] = "different query"
        return actual.model_copy(
            update={
                "receipt": store.write_artifact(json.dumps(record).encode(), "application/json")
            }
        )

    provider = ProviderDouble(store, tmp_path)
    result = WebAssetResolver(
        store, registry, provider, backend, None, None, query_port=query
    ).resolve(scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve")
    assert result.status == "blocked" and not provider.queries
    if fault == "query_deadline":
        assert result.error_code == "resolver_timeout"
    else:
        rows = json.loads(store.read_artifact(result.receipt))["candidates"]
        assert result.error_code == "invalid_provider_evidence"
        assert rows[-1]["reason"] == "invalid_search_advisory"


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        ("candidate", "unbound_provider_candidate"),
        ("license", "blocked_license"),
        ("fetch", "web_fetch_failed"),
        ("escape", "unsafe_provider_output"),
        ("duplicate", "missing_physical_metadata"),
    ],
)
def test_provider_faults_preserve_receipts_without_acceptance(tmp_path, fault, error):
    from self_improving.harness.x2env.web_resolver import WebAssetResolver

    store, registry, _, scene, _ = inputs(tmp_path)
    before = registry.find("box")

    class FaultProvider(ProviderDouble):
        fetch_count = 0

        def search(self, entity, source, limit):
            result = super().search(entity, source, limit)
            candidate = result.candidates[0]
            if fault == "candidate":
                candidate = candidate.model_copy(update={"entity_id": "other"})
            elif fault == "license":
                candidate = candidate.model_copy(
                    update={"license": candidate.license.model_copy(update={"evidence": None})}
                )
            return result.model_copy(
                update={"candidates": (candidate,) * (2 if fault == "duplicate" else 1)}
            )

        def fetch(self, candidate, output_dir):
            self.fetch_count += 1
            result = super().fetch(candidate, output_dir)
            if fault == "fetch":
                return result.model_copy(update={"status": "failed", "source_path": None})
            if fault == "escape":
                return result.model_copy(update={"source_path": str(tmp_path / "box.glb")})
            return result

    provider = FaultProvider(store, tmp_path)
    result = WebAssetResolver(store, registry, provider, None, None, None).resolve(
        scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve"
    )
    assert result.error_code == error and not result.resolved.assets
    assert registry.find("box") == before
    assert provider.fetch_count == (0 if fault in {"candidate", "license"} else 1)
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["candidates"][0]["error_code"] == error


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


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        ("no_preview", "missing_preview"),
        ("no_backend", "missing_managed_codex"),
        ("preview_binding", "unbound_preview_evidence"),
        ("verdict_binding", "unbound_visual_verdict"),
    ],
)
def test_preview_or_model_binding_failure_keeps_only_unaccepted_version(tmp_path, fault, error):
    from self_improving.harness.x2env.web_resolver import WebAssetResolver

    store, registry, _, scene, image = inputs(tmp_path)

    def preview(version, **kwargs):
        proof = preview_double(store, image)(version, **kwargs)
        if fault == "preview_binding":
            return proof.model_copy(update={"version_sha256": "0" * 64})
        # Existing CAS members stand for explicitly labelled renderer-boundary evidence.
        record = json.loads(store.read_artifact(proof.receipt))
        record["runtime_scene"] = scene.model_dump(mode="json")
        record["package"] = {"frame": image.model_dump(mode="json")}
        return proof.model_copy(
            update={
                "receipt": store.write_artifact(json.dumps(record).encode(), "application/json")
            }
        )

    class ReboundVisual(VisualDouble):
        def assess_asset_candidates(self, candidates, **kwargs):
            result = super().assess_asset_candidates(candidates, **kwargs)
            if fault == "verdict_binding":
                return result.model_copy(
                    update={
                        "verdicts": (
                            result.verdicts[0].model_copy(update={"candidate_id": "wrong-version"}),
                        )
                    }
                )
            return result

    result = WebAssetResolver(
        store,
        registry,
        ProviderDouble(store, tmp_path),
        None if fault == "no_backend" else ReboundVisual(store),
        None if fault == "no_preview" else preview,
        prepared(store, scene),
    ).resolve(scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve")
    assert result.status == "blocked" and result.error_code == error
    assert not result.resolved.assets
    receipt = json.loads(store.read_artifact(result.receipt))
    version = registry.inspect(receipt["candidates"][0]["version_sha256"])
    assert version.source.kind == "web" and version.physical_evaluated is False


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("complete", "succeeded"),
        ("missing_color_basis", "missing_color_provenance"),
        ("wrong_color", "color_mismatch"),
    ],
)
def test_preparation_result_color_must_bind_declared_intent(tmp_path, fault, expected):
    from self_improving.harness.x2env.asset_preparation import PreparationResult
    from self_improving.harness.x2env.web_resolver import WebAssetResolver

    store, registry, _, scene, image = inputs(tmp_path)
    record = json.loads(store.read_artifact(scene))
    record["entities"][0]["color"] = "red"
    scene = store.write_artifact(json.dumps(record).encode(), "application/json")
    original = prepared(store, scene)

    def prepare(entity, candidate, fetched):
        params = original(entity, candidate, fetched).model_copy(
            update={
                "base_color": (1.0, 0.0, 0.0, 1.0),
                "declared_color": "blue" if fault == "wrong_color" else "red",
                "color_basis": None if fault == "missing_color_basis" else "deployment_supplied",
            }
        )
        binding = json.loads(store.read_artifact(params.evidence))
        binding["parameters"] = params.model_dump(mode="json", exclude={"evidence"})
        params = params.model_copy(
            update={
                "evidence": store.write_artifact(json.dumps(binding).encode(), "application/json")
            }
        )
        return PreparationResult(status="completed", parameters=params, receipt=params.evidence)

    result = WebAssetResolver(
        store,
        registry,
        ProviderDouble(store, tmp_path),
        VisualDouble(store),
        preview_double(store, image),
        prepare,
    ).resolve(scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve")
    assert (result.status if fault == "complete" else result.error_code) == expected
    assert bool(result.resolved.assets) is (fault == "complete")


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
