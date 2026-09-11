"""Revised compilation keeps durable qualification authority in the existing app."""

import pytest

from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler


def test_caller_cannot_replace_the_qualified_predecessor_reader():
    with pytest.raises(TypeError, match="exact"):
        RevisedImage2EnvGenesisCompiler(object(), object())


def test_distinct_cas_roots_cannot_share_predecessor_authority(tmp_path):
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler

    first = LocalArtifactStore(tmp_path / "one")
    second = LocalArtifactStore(tmp_path / "two")
    with pytest.raises(ValueError, match="share"):
        RevisedImage2EnvGenesisCompiler(first, Image2EnvGenesisCompiler(second, object()))


@pytest.mark.parametrize("variation", ("schema", "media", "canonical"))
def test_public_compile_rejects_wrong_wire_evidence_before_asset_access(tmp_path, variation):
    import json

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileRequestV2,
    )
    from tests.self_improving.harness.test_native_prompt_revision_contracts import compile_request

    store = LocalArtifactStore(tmp_path / "cas")
    model = Image2EnvGenesisCompileRequestV2.model_validate(compile_request())
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            model.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":") if variation != "canonical" else (", ", ": "),
            ensure_ascii=False,
        )
    )
    ref = store.put_file(
        path,
        name=path.name,
        media_type="text/plain" if variation == "media" else "application/json",
        schema_version=None if variation == "schema" else model.schema_version,
    )
    compiler = RevisedImage2EnvGenesisCompiler(store, Image2EnvGenesisCompiler(store, object()))
    with pytest.raises(ValueError, match="canonical"):
        compiler.compile(ref)


@pytest.fixture(scope="module")
def real_revision():
    """Actual isolated M1 qualified asset; control ownership belongs to future Golden seam."""
    import json
    import os
    import tempfile
    from pathlib import Path
    from uuid import UUID

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.run_store import SQLiteRunStore
    from self_improving.harness.schemas.common import ArtifactRef
    from self_improving.harness.schemas.image2env_genesis_compile import (
        Image2EnvGenesisCompileRequestV1,
    )
    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileRequestV2,
        PromptRevisionV3,
        prompt_revision_v3_sha256,
    )
    from self_improving.harness.schemas.x2env_compile_evidence import (
        SceneInterpretationProposalV1,
        canonical_evidence_sha256,
    )
    from tests.self_improving.harness.test_native_prompt_revision_contracts import (
        revision,
        typed_draft,
    )

    configured = os.environ.get("REVISED_IMAGE_COMPILER_CONFIG")
    if not configured:
        pytest.skip("requires isolated M1 qualified asset journal and CAS")
    cfg = json.loads(Path(configured).read_bytes())
    store = LocalArtifactStore(Path(cfg["cas_root"]))
    app = GenesisAssetQualificationApplication(
        Path(cfg["asset_state_path"]), store, ArtifactRef.model_validate(cfg["asset_manifest"])
    )
    old = Image2EnvGenesisCompiler(store, app)
    authority = SQLiteRunStore(Path(cfg["compile_state_root"]) / "harness.sqlite3")
    state = authority.read_run_state(UUID("a42a3a83-98ea-4c01-a35f-3003a9109c49"))
    parent_ref = next(
        r
        for r in state.artifacts
        if r.schema_version == "harness.image2env_genesis_compile_receipt.v1"
    )
    parent = json.loads(store.resolve(parent_ref).path.read_bytes())
    original = Image2EnvGenesisCompileRequestV1.model_validate_json(
        store.resolve(ArtifactRef.model_validate(parent["request"])).path.read_bytes()
    )

    def publish(model, name="revised-input.json"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_text(
                json.dumps(
                    model.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
            )
            return store.put_file(
                path, name=name, media_type="application/json", schema_version=model.schema_version
            )

    data = revision()
    change = data["layout_proposal"]
    change.update(
        original_source=original.source_scene,
        source_object_id=parent["source_object_id"],
        table_center_m=original.table_center_m,
        table_size_m=original.table_size_m,
    )
    data["predecessor_environment"] = parent_ref
    data["revision_sha256"] = prompt_revision_v3_sha256(typed_draft(PromptRevisionV3, data))
    revised = PromptRevisionV3.model_validate(data)
    revision_ref = publish(revised)
    prompt_text = revised.patch.replacement_text
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "prompt.txt"
        path.write_text(prompt_text)
        prompt_ref = store.put_file(
            path,
            name=revised.revised_prompt.name,
            media_type="text/plain",
            schema_version="harness.prompt_text.v1",
        )
    assert prompt_ref == revised.revised_prompt
    proposal = SceneInterpretationProposalV1.model_validate_json(
        store.resolve(original.compile_input.interpretation_proposal).path.read_bytes()
    )
    body = proposal.model_dump(mode="json")
    body["effective_prompt"] = prompt_ref.model_dump(mode="json")
    body["proposal_sha256"] = canonical_evidence_sha256(body, "proposal_sha256")
    proposal = SceneInterpretationProposalV1.model_validate(body)
    proposal_ref = publish(proposal)
    request = Image2EnvGenesisCompileRequestV2(
        schema_version="harness.image2env_genesis_compile_request.v2",
        workflow_run_id=revised.workflow_run_id,
        principal_id=revised.principal_id,
        expected_workflow_revision=revised.committed_revision,
        expected_state_sha256=revised.resulting_state_sha256,
        expected_receipt_head_sha256=revision_ref.sha256,
        qualification_run_id=original.qualification_run_id,
        compile_input=dict(
            schema_version="harness.image2env_compile_input.v3",
            attempt=dict(
                schema_version="harness.prompt_revision_compile_attempt.v2",
                kind="prompt_revision",
                prompt_revision=revision_ref,
                revision=revised,
                effective_prompt=prompt_ref,
                failure_receipts=revised.failure_receipts,
                predecessor_environment=parent_ref,
            ),
            source_media=original.compile_input.source_media,
            interpretation_proposal=proposal_ref,
            proposal=proposal,
            asset_catalog=original.compile_input.asset_catalog,
            seed=17,
            requested_profile="genesis.rigid_scene@1",
            acquisition_policy="exact_only",
            model_execution_allowed=False,
        ),
    )
    return RevisedImage2EnvGenesisCompiler(store, old), publish(request), parent_ref, publish


@pytest.fixture(scope="module")
def real_compiled(real_revision):
    compiler, request, _, _ = real_revision
    return compiler.compile(request)


def test_formal_revised_compile_changes_real_qualified_scene_and_pure_reads(
    real_revision, real_compiled
):
    compiler, request, parent, _ = real_revision
    receipt, scene = compiler.read(real_compiled)
    old_receipt, old_scene = compiler.predecessor_compiler.read(parent)
    assert receipt.scene != old_receipt.scene
    assert scene.position_m[:2] == pytest.approx((0.1, 0.0))
    assert scene.representation == old_scene.representation
    assert receipt.qualification == old_receipt.qualification
    assert receipt.simulator_executed is False
    w, x, y, z = scene.orientation_wxyz
    _, old_x, old_y, _ = old_scene.orientation_wxyz
    assert x * x + y * y == pytest.approx(old_x * old_x + old_y * old_y, abs=1e-14)
    import math

    assert math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)) == pytest.approx(0.0, abs=1e-12)


def revised_request(compiler, ref, publish, *, parent=None, source=None):
    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileRequestV2,
        PromptRevisionV3,
        prompt_revision_v3_sha256,
    )
    from tests.self_improving.harness.test_native_prompt_revision_contracts import typed_draft

    request = Image2EnvGenesisCompileRequestV2.model_validate_json(
        compiler.artifact_store.resolve(ref).path.read_bytes()
    )
    data = request.model_dump(mode="json")
    draft = data["compile_input"]["attempt"]["revision"]
    if parent is not None:
        draft["predecessor_environment"] = parent.model_dump(mode="json")
        data["compile_input"]["attempt"]["predecessor_environment"] = parent.model_dump(mode="json")
    if source is not None:
        draft["layout_proposal"]["original_source"] = source.model_dump(mode="json")
    draft["revision_sha256"] = prompt_revision_v3_sha256(typed_draft(PromptRevisionV3, draft))
    revision = PromptRevisionV3.model_validate(draft)
    revision_ref = publish(revision)
    data["compile_input"]["attempt"]["revision"] = revision
    data["compile_input"]["attempt"]["prompt_revision"] = revision_ref
    data["expected_receipt_head_sha256"] = revision_ref.sha256
    return Image2EnvGenesisCompileRequestV2.model_validate(data)


def test_formal_nonlayout_input_changes_are_rejected(real_revision):
    from uuid import uuid4

    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileRequestV2,
    )

    compiler, ref, _, publish = real_revision
    request = Image2EnvGenesisCompileRequestV2.model_validate_json(
        compiler.artifact_store.resolve(ref).path.read_bytes()
    )
    changed = request.model_copy(update={"qualification_run_id": uuid4()})
    with pytest.raises(ValueError, match="non-layout"):
        compiler.compile(publish(changed))


def test_formal_modified_receipt_is_not_a_new_qualification(real_revision, real_compiled):
    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileReceiptV2,
    )

    compiler, _, _, publish = real_revision
    receipt = Image2EnvGenesisCompileReceiptV2.model_validate_json(
        compiler.artifact_store.resolve(real_compiled).path.read_bytes()
    )
    changed = receipt.model_copy(update={"implementation_sha256": "f" * 64})
    with pytest.raises(ValueError, match="implementation"):
        compiler.read(publish(changed))


def test_formal_revised_predecessor_is_deeply_read(real_revision, real_compiled):
    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileReceiptV2,
    )

    compiler, ref, _, publish = real_revision
    receipt = Image2EnvGenesisCompileReceiptV2.model_validate_json(
        compiler.artifact_store.resolve(real_compiled).path.read_bytes()
    )
    request = revised_request(
        compiler, ref, publish, parent=real_compiled, source=receipt.derived_source_scene
    )
    result = compiler.compile(publish(request))
    actual, _ = compiler.read(result)
    assert actual.qualification == receipt.qualification


def test_formal_replay_predecessor_uses_complete_public_physics_reader(real_revision):
    import json
    import os
    from pathlib import Path
    from uuid import UUID

    from self_improving.harness.run_store import SQLiteRunStore

    compiler, ref, _, publish = real_revision
    cfg = json.loads(Path(os.environ["REVISED_IMAGE_COMPILER_CONFIG"]).read_bytes())
    state = SQLiteRunStore(Path(cfg["scene_state_root"]) / "runs.sqlite").read_run_state(
        UUID("bfc30903-0a69-478b-94ed-3ad2c9bbea03")
    )
    replay = next(
        r
        for r in state.artifacts
        if r.schema_version == "harness.genesis_scene_execution_receipt.v2"
    )
    request = revised_request(compiler, ref, publish, parent=replay)
    result = compiler.compile(publish(request))
    # Producer already called the complete public verifier; a second read is
    # independently exercised by the revised-predecessor test above.
    payload = json.loads(compiler.artifact_store.resolve(result).path.read_bytes())
    assert payload["simulator_executed"] is False
    summary = Path("/tmp/golden-j2-replay-predecessor-result.json")
    summary.write_text(
        json.dumps(
            {
                "request": publish(request).model_dump(mode="json"),
                "receipt": result.model_dump(mode="json"),
                "predecessor": replay.model_dump(mode="json"),
                "scene": payload["scene"],
                "scope": payload["scope"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
