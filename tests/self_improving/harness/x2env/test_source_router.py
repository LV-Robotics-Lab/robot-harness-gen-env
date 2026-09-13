"""Source composition uses real Store/Registry and explicit resolver boundary doubles."""

import json

import pytest

from self_improving.harness.x2env.compile import ResolvedAsset, ResolvedAssetSet
from self_improving.harness.x2env.resolver import ResolutionResult
from tests.self_improving.harness.x2env.test_resolver import inputs


def continuation_inputs(tmp_path):
    """Real committed journal and color execution; renderer/model are explicit doubles."""
    import hashlib
    import sys

    from self_improving.harness.x2env.codex import CodexBackend
    from self_improving.harness.x2env.contracts import RepairReservation, ToolResult, X2EnvRequest
    from self_improving.harness.x2env.input import ingest
    from self_improving.harness.x2env.local_color_advisory import propose_color_repair
    from self_improving.harness.x2env.local_color_execution import (
        color_failure_fingerprint,
        execute_color_repair,
    )
    from self_improving.harness.x2env.resolver import LocalAssetResolver
    from self_improving.harness.x2env.source_router import SourceRouter
    from tests.self_improving.harness.x2env.test_resolver import (
        color_resolution_inputs,
        preview_proof,
    )

    store, registry, scene, _, proof, visual = color_resolution_inputs(tmp_path)

    def put(value):
        return store.write_artifact(value.model_dump_json().encode(), "application/json")

    request = X2EnvRequest(
        text="pink box and mouse",
        seed=0,
        idempotency_key="continuation",
        allowed_sources=("local", "web"),
        output_dir=str(tmp_path / "out"),
    )
    bundle = ingest(request, store)
    payload = json.loads(store.read_artifact(scene))
    payload["input_sha256"] = bundle.request_sha256
    scene = store.write_artifact(json.dumps(payload).encode(), "application/json")
    snapshot = store.claim(store.submit(request).workflow_id)
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id,
            status="succeeded",
            outputs=(put(bundle), scene),
        ),
        put(bundle),
        status="active",
        pending_scene_ir=scene,
    )
    snapshot = store.begin_operation(snapshot, "asset.resolve")
    original = SourceRouter(
        store, local=LocalAssetResolver(store, registry, visual, lambda v, timeout: proof)
    ).resolve(
        scene,
        allowed_sources=request.allowed_sources,
        allow_cousin=False,
        output_root=tmp_path / "initial",
    )
    original_ref = put(original)
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id,
            status="blocked",
            error_code=original.error_code,
            outputs=(original.receipt, original_ref),
        ),
        put(bundle),
        status="active",
        asset_resolution=original_ref,
    )
    candidate = original.pending_color_repairs[0]
    program = tmp_path / "color-model-double"
    program.write_text(
        f"#!{sys.executable}\n"
        + """import sys,json,pathlib
sys.stdin.read()
schema=json.loads(pathlib.Path(sys.argv[sys.argv.index('--output-schema')+1]).read_text())
answer=({'declared_color':'pink','rgba':[0.9,0.4,0.6,1.0]}
if 'declared_color' in schema['properties'] else
{'object':'box','match':True,'colors':['pink'],'materials':['plastic'],'confidence':0.99,
'same_kind':True,'plausible':True,'suggests':None})
pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps(answer))
print(json.dumps({'type':'turn.completed'}))
"""
    )
    program.chmod(0o700)
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "test-double", store
    )
    proposal = propose_color_repair(backend, candidate, output_root=tmp_path / "proposal")
    snapshot = store.begin_operation(snapshot, "codex.asset_color")
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id,
            status="succeeded",
            outputs=(put(proposal),),
        ),
        put(bundle),
        status="active",
    )
    approval = store.write_artifact(
        json.dumps(
            {
                "authority": "harness_controller",
                "approved": True,
                "parent_version": candidate.parent_version,
                "patch": {"base_color": [0.9, 0.4, 0.6, 1.0], "color_mode": "uniform_replace"},
                "workflow_id": snapshot.workflow_id,
                "base_revision": snapshot.revision,
                "input_bundle": put(bundle).model_dump(),
                "scene_ir": scene.model_dump(),
                "candidate": put(candidate).model_dump(),
                "proposal": put(proposal).model_dump(),
                "cost": 1,
                "failure_fingerprint": color_failure_fingerprint(candidate),
            }
        ).encode(),
        "application/json",
    )
    reservation = RepairReservation(
        kind="asset",
        cost=1,
        approval=approval,
        base_revision=snapshot.revision,
        failure_fingerprint=color_failure_fingerprint(candidate),
    )
    snapshot = store.begin_operation(snapshot, "asset.revise", repair_reservation=reservation)
    envelope = store.write_artifact(
        json.dumps(
            {
                "workflow_id": snapshot.workflow_id,
                "operation_id": snapshot.operations[-1].operation_id,
                "reservation": reservation.model_dump(mode="json"),
                "input_bundle": put(bundle).model_dump(),
                "scene_ir": scene.model_dump(),
                "candidate": put(candidate).model_dump(),
                "proposal": put(proposal).model_dump(),
            }
        ).encode(),
        "application/json",
    )
    execution = execute_color_repair(
        put(candidate),
        put(proposal),
        envelope,
        store=store,
        registry=registry,
        backend=backend,
        preview=lambda v, timeout: preview_proof(store, v, proof.image),
        output_root=tmp_path / "execution",
    )
    assert execution.status == "succeeded", execution
    return store, registry, snapshot, original_ref, execution, put(execution)


def start_continuation(store, snapshot, execution, execution_ref):
    from self_improving.harness.x2env.contracts import ToolResult

    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id,
            status="succeeded",
            outputs=(execution_ref, execution.receipt),
        ),
        snapshot.input_bundle,
        status="active",
    )
    return store.begin_operation(snapshot, "asset.resolve")


@pytest.mark.parametrize("web_match", [False, True])
def test_resume_uses_committed_child_and_partial_only_searching_web_missing_once(
    tmp_path, web_match
):
    from self_improving.harness.x2env.source_router import SourceRouter

    store, registry, snapshot, original_ref, execution, execution_ref = continuation_inputs(
        tmp_path
    )
    snapshot = start_continuation(store, snapshot, execution, execution_ref)
    calls = []
    report = json.loads(store.read_artifact(execution.child.normalization_report))
    mouse = registry.register(
        "mouse",
        "mouse",
        tmp_path / "execution/child",
        "asset.urdf",
        files=tuple(f["path"] for f in report["files"]),
        normalization_report=execution.child.normalization_report,
        license=execution.child.license,
        source=execution.child.source,
        receipt=execution.child.receipt,
    )

    class Source:
        def __init__(self, source):
            self.source = source

        def resolve(self, scene, **kwargs):
            calls.append((self.source, kwargs["entity_ids"]))
            assert self.source == "web" and kwargs["entity_ids"] == ("mouse",)
            receipt = store.write_artifact(b"explicit empty web search double", "text/plain")
            if web_match:
                return ResolutionResult(
                    status="succeeded",
                    resolved=ResolvedAssetSet(
                        scene_ir=scene,
                        assets=(
                            ResolvedAsset(
                                entity_id="mouse",
                                version_sha256=mouse.version_sha256,
                                acquisition_source="web",
                                selection="exact",
                            ),
                        ),
                    ),
                    receipt=receipt,
                )
            return ResolutionResult(
                status="blocked",
                resolved=ResolvedAssetSet(scene_ir=scene, assets=()),
                receipt=receipt,
                error_code="web_assets_unresolved",
            )

    result = SourceRouter(
        store, local=Source("local"), web=Source("web")
    ).resume_after_local_repairs(
        original_ref,
        (execution_ref,),
        workflow_id=snapshot.workflow_id,
        allowed_sources=("local", "web"),
        allow_cousin=False,
        output_root=tmp_path / "continued",
    )
    assert result.status == ("succeeded" if web_match else "blocked")
    assert not result.pending_color_repairs
    assert [(a.entity_id, a.version_sha256) for a in result.resolved.assets[:2]] == [
        ("box", execution.child.version_sha256),
        ("accepted", registry.inspect(execution.child.parent_version).version_sha256),
    ]
    assert len(result.resolved.assets) == (3 if web_match else 2)
    assert calls == [("web", ("mouse",))]
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["original_resolution"] == original_ref.model_dump()
    assert receipt["repair_results"] == [execution_ref.model_dump()]
    assert receipt["searched_entities"] == {"local": ["box", "accepted", "mouse"], "web": ["mouse"]}


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate",
        "uncommitted",
        "wrong_operation",
        "wrong_scene",
        "wrong_sources",
        "replayed",
    ],
)
def test_resume_rejects_unbound_or_incomplete_children_before_any_provider(tmp_path, fault):
    from self_improving.harness.x2env.contracts import ToolResult
    from self_improving.harness.x2env.source_router import SourceRouter

    store, _, snapshot, original_ref, execution, execution_ref = continuation_inputs(tmp_path)
    if fault != "wrong_operation":
        snapshot = start_continuation(store, snapshot, execution, execution_ref)
    if fault == "replayed":
        snapshot = store.complete_operation(
            snapshot,
            ToolResult(
                operation_id=snapshot.operations[-1].operation_id,
                status="failed",
                error_code="already_attempted",
            ),
            snapshot.input_bundle,
            status="active",
        )
        snapshot = store.begin_operation(snapshot, "asset.resolve")
    refs = (execution_ref,)
    if fault == "missing":
        refs = ()
    if fault == "duplicate":
        refs *= 2
    if fault == "uncommitted":
        refs = (
            store.write_artifact(execution.model_dump_json(indent=2).encode(), "application/json"),
        )
    if fault == "wrong_scene":
        original = ResolutionResult.model_validate_json(store.read_artifact(original_ref))
        original_ref = store.write_artifact(
            original.model_dump_json(indent=2).encode(), "application/json"
        )

    class Forbidden:
        def resolve(self, *args, **kwargs):
            pytest.fail("preflight cannot query any provider")

    router = SourceRouter(store, local=Forbidden(), web=Forbidden())
    args = dict(
        workflow_id=snapshot.workflow_id,
        allowed_sources=("local",) if fault == "wrong_sources" else ("local", "web"),
        allow_cousin=False,
        output_root=tmp_path / "refused",
    )
    if fault in {"wrong_operation", "wrong_scene", "wrong_sources", "replayed"}:
        with pytest.raises(ValueError, match="color_continuation_"):
            router.resume_after_local_repairs(original_ref, refs, **args)
    else:
        result = router.resume_after_local_repairs(original_ref, refs, **args)
        assert result.status == ("blocked" if fault == "missing" else "failed")
        assert [a.entity_id for a in result.resolved.assets] == ["accepted"]
        assert [p.entity_id for p in result.pending_color_repairs] == ["box"]
        assert result.next_source == "web"


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
