"""Source composition uses real Store/Registry and explicit resolver boundary doubles."""

import json

import pytest

from self_improving.harness.x2env.compile import ResolvedAsset, ResolvedAssetSet
from self_improving.harness.x2env.resolver import ResolutionResult
from tests.self_improving.harness.x2env.test_resolver import inputs


@pytest.mark.parametrize(
    "fault", [None, "scene", "entity", "parent", "proof", "receipt", "duplicate", "accepted"]
)
def test_router_pauses_local_pending_without_skipping_to_web(tmp_path, fault):
    from self_improving.harness.x2env.resolver import LocalAssetResolver
    from self_improving.harness.x2env.source_router import SourceRouter
    from tests.self_improving.harness.x2env.test_resolver import color_resolution_inputs

    store, registry, scene, _, proof, backend = color_resolution_inputs(tmp_path)
    local = LocalAssetResolver(store, registry, backend, lambda v, timeout: proof)
    calls = []

    class Local:
        def resolve(self, ref, **kwargs):
            result = local.resolve(ref, **kwargs)
            if fault is None:
                return result
            candidate = result.pending_color_repairs[0]
            updates = {
                "scene": {"scene_ir": proof.receipt},
                "entity": {"entity_id": "mouse"},
                "parent": {"parent_version": "a" * 64},
                "proof": {"preview_proof": proof.model_copy(update={"version_sha256": "b" * 64})},
                "receipt": {},
                "duplicate": {},
                "accepted": {"entity_id": "accepted"},
            }
            candidate = candidate.model_copy(update=updates[fault])
            candidates = (candidate, candidate) if fault == "duplicate" else (candidate,)
            payload = json.loads(store.read_artifact(result.receipt))
            if fault != "receipt":
                payload["pending_color_repairs"] = [p.model_dump(mode="json") for p in candidates]
            else:
                payload["pending_color_repairs"] = []
            return result.model_copy(
                update={
                    "pending_color_repairs": candidates,
                    "receipt": store.write_artifact(
                        json.dumps(payload).encode(), "application/json"
                    ),
                }
            )

    class Web:
        def resolve(self, ref, **kwargs):
            calls.append(kwargs["entity_ids"])
            raise AssertionError("pending local repair must precede web")

    result = SourceRouter(store, local=Local(), web=Web()).resolve(
        scene,
        allowed_sources=("reconstruction", "web", "local"),
        allow_cousin=False,
        output_root=tmp_path / "router",
    )
    assert not calls and result.status == "blocked"
    if fault:
        assert not result.pending_color_repairs and not result.resolved.assets
    else:
        assert [a.entity_id for a in result.resolved.assets] == ["accepted"]
        assert [p.entity_id for p in result.pending_color_repairs] == ["box"]
        assert result.required_resources == ()
        assert result.next_source == "web"
        receipt = json.loads(store.read_artifact(result.receipt))
        assert receipt["unresolved_entities"] == ["box", "mouse"]
        assert receipt["searched_entities"] == {"local": ["box", "accepted", "mouse"]}
        assert receipt["next_source"] == "web"
        assert len(receipt["sources"]) == 1


def test_router_keeps_partial_success_and_only_asks_next_source_for_missing_entity(tmp_path):
    from self_improving.harness.x2env.source_router import SourceRouter

    store, registry, box, scene, _ = inputs(tmp_path, extra_entity=True)
    report = json.loads(store.read_artifact(box.normalization_report))
    mouse = registry.register(
        "mouse",
        "mouse",
        tmp_path / "normalized",
        "asset.urdf",
        files=tuple(r["path"] for r in report["files"]),
        normalization_report=box.normalization_report,
        license=box.license,
        source=box.source,
        receipt=box.receipt,
    )
    calls = []

    class Resolver:
        def __init__(self, source, entity, version):
            self.source, self.entity, self.version = source, entity, version

        def resolve(self, ref, **kwargs):
            calls.append((self.source, kwargs["entity_ids"]))
            assert ref == scene
            assets = ResolvedAssetSet(
                scene_ir=ref,
                assets=(
                    ResolvedAsset(
                        entity_id=self.entity,
                        version_sha256=self.version.version_sha256,
                        acquisition_source=self.source,
                        selection="exact",
                    ),
                ),
            )
            receipt = store.write_artifact(b"explicit resolver double", "text/plain")
            return ResolutionResult(
                status="blocked" if self.source == "local" else "succeeded",
                resolved=assets,
                receipt=receipt,
            )

    result = SourceRouter(
        store, local=Resolver("local", "box", box), web=Resolver("web", "mouse", mouse)
    ).resolve(
        scene, allowed_sources=("web", "local"), allow_cousin=False, output_root=tmp_path / "router"
    )
    assert result.status == "succeeded"
    assert calls == [("local", ("box", "mouse")), ("web", ("mouse",))]
    assert {a.entity_id for a in result.resolved.assets} == {"box", "mouse"}


def test_local_and_web_filter_keep_original_scene_and_skip_other_entities(tmp_path):
    from self_improving.harness.x2env.resolver import LocalAssetResolver
    from self_improving.harness.x2env.web_resolver import WebAssetResolver
    from tests.self_improving.harness.x2env.test_resolver import VisualDouble, preview_proof
    from tests.self_improving.harness.x2env.test_web_resolver import ProviderDouble

    store, registry, version, scene, image = inputs(tmp_path, extra_entity=True)
    backend = VisualDouble(store)
    local = LocalAssetResolver(
        store, registry, backend, lambda v, timeout: preview_proof(store, v, image)
    ).resolve(
        scene,
        allowed_sources=("local",),
        allow_cousin=False,
        output_root=tmp_path / "local",
        entity_ids=("box",),
    )
    assert local.status == "succeeded" and local.resolved.scene_ir == scene
    assert len(backend.calls) == 1
    provider = ProviderDouble(store, tmp_path)
    web = WebAssetResolver(store, registry, provider, None, None, None).resolve(
        scene,
        allowed_sources=("web",),
        allow_cousin=False,
        output_root=tmp_path / "web",
        entity_ids=("mouse",),
    )
    assert web.status == "blocked" and web.resolved.scene_ir == scene
    assert provider.queries == [("mouse", "web", 8)]
