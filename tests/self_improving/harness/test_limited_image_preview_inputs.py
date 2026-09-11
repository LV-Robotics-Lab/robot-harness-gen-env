"""Input construction uses real CAS/image/geometry; no simulator or model calls."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from self_improving.harness.limited_image_preview_inputs import LimitedImagePreviewInputs
from self_improving.harness.schemas.image2env_genesis_compile import (
    Image2EnvGenesisCompileRequestV1,
)
from self_improving.harness.schemas.x2env_compile_evidence import (
    AssetRepresentationV1,
    SceneInterpretationProposalV1,
    canonical_evidence_sha256,
)
from tests.self_improving.harness.test_gujie_scene_adapter import candidate  # noqa: F401


@pytest.fixture
def inputs(candidate, tmp_path):  # noqa: F811
    store, representation_ref, source_ref, source, publish = candidate
    representation = AssetRepresentationV1.model_validate_json(
        store.resolve(representation_ref).path.read_bytes()
    )
    # Opaque fixture references are not qualification authority: the builder only
    # constructs inputs; the real compiler must still read its trusted application.
    qualification = publish({}, "qualification.json", "harness.asset_qualification.v1")
    runtime = publish({}, "runtime.json", "harness.genesis_runtime_lock.v1")
    catalog = dict(
        schema_version="harness.portable_asset_catalog.v1",
        runtime_lock=runtime.model_dump(mode="json"),
        entries=[
            dict(
                subject=representation.subject.model_dump(mode="json"),
                semantic_name="mouse",
                aliases=[],
                representation=representation_ref.model_dump(mode="json"),
                representation_sha256=representation.representation_sha256,
                qualification=qualification.model_dump(mode="json"),
                available=True,
                qualified_subject=dict(
                    model_subject=representation.subject.model_dump(mode="json"),
                    representation_sha256=representation.representation_sha256,
                    loader_closure_sha256=representation.loader_closure_sha256,
                ),
            )
        ],
    )
    catalog["catalog_sha256"] = canonical_evidence_sha256(catalog, "catalog_sha256")
    catalog_ref = publish(catalog, "catalog.json", "harness.portable_asset_catalog.v1")
    path = tmp_path / "mouse.png"
    Image.new("RGB", (3, 4), "white").save(path)
    raw = store.put_file(path, name="mouse.png", media_type="image/png", schema_version=None)
    builder = LimitedImagePreviewInputs(
        store,
        asset_catalog=catalog_ref,
        source_scene=source_ref,
        qualification_run_id=uuid4(),
        table_center_m=(0.0, 0.0, 0.4),
        table_size_m=(1.0, 0.6, 0.1),
        table_friction=0.5,
    )
    return SimpleNamespace(
        builder=builder,
        store=store,
        raw=raw,
        workflow=uuid4(),
        publish=publish,
        source=source,
        catalog=catalog,
    )


def test_real_ingest_lookup_and_geometry_build_initial_request(inputs):
    case = inputs
    prompt = "Put one mouse on the table. 修复目标留给 Codex：x=0.2。"
    initial = case.builder.start(
        workflow_id=case.workflow, raw_image_ref=case.raw, user_text=prompt
    )
    lookup = case.builder.lookup(workflow_id=case.workflow, input_ref=initial)
    request_ref = case.builder.build_initial(
        workflow_id=case.workflow, input_ref=initial, lookup_ref=lookup, asset_id="mouse"
    )
    request = Image2EnvGenesisCompileRequestV1.model_validate_json(
        case.store.resolve(request_ref).path.read_bytes()
    )
    proposal = SceneInterpretationProposalV1.model_validate_json(
        case.store.resolve(request.compile_input.interpretation_proposal).path.read_bytes()
    )
    assert proposal.objects[0].dimensions_m == pytest.approx((0.10, 0.06, 0.04))
    assert proposal.objects[0].object_id == "mouse_001"
    assert case.store.resolve(proposal.effective_prompt).path.read_bytes() == prompt.encode()
    assert request.compile_input.source_media.source == case.raw
    assert request.compile_input.attempt.kind == "initial"
    assert request_ref == case.builder.build_initial(
        workflow_id=case.workflow, input_ref=initial, lookup_ref=lookup, asset_id="mouse"
    )


@pytest.mark.parametrize(
    "variation", ["query", "workflow", "asset", "lookup", "catalog", "prompt_missing"]
)
def test_input_lineage_rejects_foreign_or_missing_selection(inputs, variation):
    case = inputs
    initial = case.builder.start(
        workflow_id=case.workflow, raw_image_ref=case.raw, user_text="mouse"
    )
    lookup = case.builder.lookup(workflow_id=case.workflow, input_ref=initial)
    workflow, asset = case.workflow, "mouse"
    if variation == "query":
        with pytest.raises(ValueError, match="unsupported"):
            case.builder.lookup(workflow_id=workflow, input_ref=initial, query="cup")
        return
    if variation == "workflow":
        workflow = uuid4()
    if variation == "asset":
        asset = "cup"
    if variation == "lookup":
        lookup = initial
    if variation == "catalog":
        value = json.loads(case.store.resolve(initial).path.read_bytes())
        value["catalog_ref"] = case.raw.model_dump(mode="json")
        initial = case.publish(value, "foreign-input.json")
    if variation == "prompt_missing":
        value = json.loads(case.store.resolve(initial).path.read_bytes())
        from self_improving.harness.schemas.common import ArtifactRef

        case.store.resolve(ArtifactRef.model_validate(value["effective_prompt"])).path.unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        case.builder.build_initial(
            workflow_id=workflow, input_ref=initial, lookup_ref=lookup, asset_id=asset
        )


@pytest.mark.parametrize(
    "variation",
    [
        "blank",
        "ref_type",
        "noncanonical",
        "prompt_type",
        "no_mouse",
        "unverified_pink_mouse",
        "source_mismatch",
        "layout",
    ],
)
def test_input_contract_and_declared_scope_fail_closed(inputs, tmp_path, variation):
    case = inputs
    if variation == "blank":
        with pytest.raises(ValueError, match="nonempty"):
            case.builder.start(workflow_id=case.workflow, raw_image_ref=case.raw, user_text=" ")
        return
    initial = case.builder.start(
        workflow_id=case.workflow, raw_image_ref=case.raw, user_text="mouse"
    )
    if variation == "ref_type":
        initial = case.raw
    if variation == "noncanonical":
        path = tmp_path / "pretty.json"
        path.write_text(
            json.dumps(json.loads(case.store.resolve(initial).path.read_bytes()), indent=2)
        )
        initial = case.store.put_file(
            path, name="pretty.json", media_type="application/json", schema_version=None
        )
    if variation == "prompt_type":
        payload = json.loads(case.store.resolve(initial).path.read_bytes())
        payload["effective_prompt"] = case.raw.model_dump(mode="json")
        initial = case.publish(payload, "wrong-prompt.json")
    if variation in ("no_mouse", "unverified_pink_mouse"):
        case.catalog["entries"][0]["semantic_name"] = (
            "cup" if variation == "no_mouse" else "pink_mouse"
        )
        case.catalog["catalog_sha256"] = canonical_evidence_sha256(case.catalog, "catalog_sha256")
        case.builder.asset_catalog = case.publish(
            case.catalog, "cup-catalog.json", "harness.portable_asset_catalog.v1"
        )
        initial = case.builder.start(
            workflow_id=case.workflow, raw_image_ref=case.raw, user_text="mouse"
        )
    if variation in ("source_mismatch", "layout"):
        if variation == "source_mismatch":
            case.source["metadata"]["category"] = "cup"
        else:
            case.source["relation"]["relation"] = "near"
        case.builder.source_scene = case.publish(case.source, "changed-source.json")
    with pytest.raises(ValueError):
        lookup = case.builder.lookup(workflow_id=case.workflow, input_ref=initial)
        case.builder.build_initial(
            workflow_id=case.workflow, input_ref=initial, lookup_ref=lookup, asset_id="mouse"
        )


def test_real_fixed_mouse_catalog_builds_without_changing_historical_cas(tmp_path):
    import os
    from pathlib import Path

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.schemas.common import ArtifactRef

    config_path = os.environ.get("PREVIEW_REAL_INPUT_CONFIG")
    if not config_path:
        pytest.skip("requires fixed M1 input config; no simulator execution")
    config = json.loads(Path(config_path).read_text())
    original = LocalArtifactStore(Path(config["cas_root"]))
    request = Image2EnvGenesisCompileRequestV1.model_validate_json(
        original.resolve(ArtifactRef.model_validate(config["request_ref"])).path.read_bytes()
    )
    store = LocalArtifactStore(tmp_path / "isolated-cas")

    def copy(ref):
        actual = store.put_file(
            original.resolve(ref).path,
            name=ref.name,
            media_type=ref.media_type,
            schema_version=ref.schema_version,
        )
        assert actual == ref
        return ref

    catalog_ref = copy(request.compile_input.asset_catalog)
    entry = json.loads(original.resolve(catalog_ref).path.read_bytes())["entries"][0]
    representation_ref = copy(ArtifactRef.model_validate(entry["representation"]))
    representation = AssetRepresentationV1.model_validate_json(
        original.resolve(representation_ref).path.read_bytes()
    )
    for member in representation.members:
        copy(member.artifact)
    copy(ArtifactRef.model_validate(entry["qualification"]))
    source = copy(request.source_scene)
    raw = copy(request.compile_input.source_media.source)
    builder = LimitedImagePreviewInputs(
        store,
        asset_catalog=catalog_ref,
        source_scene=source,
        qualification_run_id=request.qualification_run_id,
        table_center_m=request.table_center_m,
        table_size_m=request.table_size_m,
        table_friction=request.table_friction,
    )
    workflow = uuid4()
    initial = builder.start(workflow_id=workflow, raw_image_ref=raw, user_text="One mouse on table")
    lookup = builder.lookup(workflow_id=workflow, input_ref=initial, query="mouse")
    result = builder.build_initial(
        workflow_id=workflow,
        input_ref=initial,
        lookup_ref=lookup,
        asset_id="simfoundry_pink_mouse_7915827ae4309660",
    )
    compiled = Image2EnvGenesisCompileRequestV1.model_validate_json(
        store.resolve(result).path.read_bytes()
    )
    proposal = SceneInterpretationProposalV1.model_validate_json(
        store.resolve(compiled.compile_input.interpretation_proposal).path.read_bytes()
    )
    assert proposal.objects[0].category == "pink_mouse"
    assert proposal.objects[0].object_id == "iter_0"
    assert (
        original.resolve(catalog_ref).path.read_bytes()
        == store.resolve(catalog_ref).path.read_bytes()
    )


def test_public_input_builder_has_explicit_stages():
    assert all(
        callable(getattr(LimitedImagePreviewInputs, name))
        for name in (
            "start",
            "lookup",
            "build_initial",
        )
    )
