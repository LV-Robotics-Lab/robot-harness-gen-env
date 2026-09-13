"""Color repair advisory seams; all visual/model evidence here is an explicit test double."""

import hashlib
import json

import pytest

from self_improving.harness.x2env.asset_advisory import AssetVisualAssessment, VisualVerdict
from self_improving.harness.x2env.contracts import SceneIR
from tests.self_improving.harness.x2env.test_resolver import inputs, preview_proof


def color_inputs(tmp_path, detail_updates=None):
    store, registry, version, scene_ref, image = inputs(tmp_path)
    scene = json.loads(store.read_artifact(scene_ref))
    scene["entities"][0]["color"] = "pink"
    scene["entities"][0]["material"] = "plastic"
    scene_ref = store.write_artifact(json.dumps(scene).encode(), "application/json")
    entity = SceneIR.model_validate_json(store.read_artifact(scene_ref)).entities[0]
    detail = {
        "candidate_id": version.version_sha256,
        "asked_category": "box",
        "name": "box",
        "verdict": "mismatch",
        "open_answer": "box",
        "name_check": "supports",
        "colors": ["blue"],
        "materials": ["plastic"],
        "attribute_veto": "attribute_mismatch",
        "attribute_check": {"color": "mismatch", "material": "ok"},
        "test_double": True,
        **(detail_updates or {}),
    }
    ref = store.write_artifact(json.dumps(detail).encode(), "application/json")
    verdict = VisualVerdict(
        candidate_id=version.version_sha256, preview=image, verdict="mismatch", detail=ref
    )
    receipt = store.write_artifact(
        json.dumps(
            {
                "status": "completed",
                "error_code": None,
                "verifier": "asset_reuse.lib.a6_verify.verify_candidate",
                "verdicts": [verdict.model_dump(mode="json")],
                "evidence": [ref.model_dump(mode="json")],
                "model": "test-double",
                "physical_evaluated": False,
            }
        ).encode(),
        "application/json",
    )
    advisory = AssetVisualAssessment(
        status="completed", verdicts=(verdict,), receipt=receipt, evidence=(ref,)
    )
    return (
        store,
        registry,
        scene_ref,
        entity,
        version,
        preview_proof(store, version, image),
        advisory,
    )


def test_color_only_veto_returns_bound_advisory_not_resolved_asset(tmp_path):
    from self_improving.harness.x2env.local_color_advisory import classify_color_repair

    store, registry, scene, entity, version, proof, advisory = color_inputs(tmp_path)
    before = registry.inspect(version.version_sha256)
    candidate = classify_color_repair(store, scene, entity, version, proof, advisory)
    assert candidate.parent_version == version.version_sha256
    assert candidate.scene_ir == scene and candidate.entity_id == entity.id
    assert candidate.requested_color == "pink" and candidate.requested_material == "plastic"
    assert candidate.preview_proof == proof and candidate.assessment == advisory
    assert candidate.authority == "advisory_only" and not candidate.physical_evaluated
    assert registry.inspect(version.version_sha256) == before


@pytest.mark.parametrize(
    "detail",
    [
        {"attribute_check": {"color": "unknown", "material": "ok"}},
        {"attribute_check": {"color": "mismatch", "material": "mismatch"}},
        {"attribute_check": {"color": "mismatch", "material": "unknown"}},
        {"attribute_veto": None},
        {"name_veto": True},
        {"second_opinion_veto": True},
        {"verdict": "match"},
        {"open_answer": None},
        {"colors": []},
    ],
)
def test_other_mismatches_cannot_be_relabelled_color_only(tmp_path, detail):
    from self_improving.harness.x2env.local_color_advisory import classify_color_repair

    store, _, scene, entity, version, proof, advisory = color_inputs(tmp_path, detail)
    assert classify_color_repair(store, scene, entity, version, proof, advisory) is None


@pytest.mark.parametrize("fault", ["entity", "version", "proof", "image", "receipt", "detail"])
def test_unbound_evidence_is_rejected_before_color_proposal(tmp_path, fault):
    from self_improving.harness.x2env.local_color_advisory import classify_color_repair

    store, _, scene, entity, version, proof, advisory = color_inputs(tmp_path)
    if fault == "entity":
        entity = entity.model_copy(update={"category": "mouse"})
    if fault == "version":
        version = version.model_copy(update={"category": "mouse"})
    if fault == "proof":
        proof = proof.model_copy(update={"version_sha256": "b" * 64})
    if fault == "image":
        proof = proof.model_copy(update={"image": store.write_artifact(b"not png", "image/png")})
    if fault == "receipt":
        advisory = advisory.model_copy(
            update={"receipt": store.write_artifact(b"{}", "application/json")}
        )
    if fault == "detail":
        detail = store.write_artifact(b"{}", "application/json")
        advisory = advisory.model_copy(
            update={"verdicts": (advisory.verdicts[0].model_copy(update={"detail": detail}),)}
        )
    with pytest.raises((ValueError, OSError)):
        classify_color_repair(store, scene, entity, version, proof, advisory)


def test_managed_color_proposal_binds_original_failure_and_actual_image(tmp_path):
    from self_improving.harness.x2env.codex import CodexBackend
    from self_improving.harness.x2env.local_color_advisory import (
        classify_color_repair,
        propose_color_repair,
    )
    from tests.self_improving.harness.x2env.test_codex import executable

    store, _, scene, entity, version, proof, advisory = color_inputs(tmp_path)
    candidate = classify_color_repair(store, scene, entity, version, proof, advisory)
    program = executable(tmp_path, {"declared_color": "pink", "rgba": [0.9, 0.4, 0.6, 1.0]})
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "test-double", store
    )
    result = propose_color_repair(backend, candidate, output_root=tmp_path / "proposal", timeout=10)
    assert result.status == "completed" and result.proposal.rgba == (0.9, 0.4, 0.6, 1.0)
    assert result.candidate == candidate and result.proposal.declared_color == "pink"
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["executable_sha256"] == backend.executable_sha
    assert receipt["authority"] == "advisory_only" and receipt["model"] == "test-double"
    assert receipt["external_agent_executed"] is True
    assert "attribute_mismatch" in (tmp_path / "proposal/prompt.txt").read_text()
    invocation = json.loads((tmp_path / "proposal/invocation.json").read_text())
    assert invocation["media"][0]["input_sha256"] == proof.image.sha256
    assert (tmp_path / "proposal/candidate.png").read_bytes() == store.read_artifact(proof.image)
    evidence = [store.read_artifact(r) for r in result.evidence]
    assert (tmp_path / "proposal/codex.jsonl").read_bytes() in evidence
    assert (tmp_path / "proposal/process-terminal.json").read_bytes() in evidence


def test_classification_accepts_original_managed_verifier_receipt_shape(tmp_path):
    from self_improving.harness.x2env.asset_advisory import VisualCandidate
    from self_improving.harness.x2env.codex import CodexBackend
    from self_improving.harness.x2env.local_color_advisory import classify_color_repair
    from tests.self_improving.harness.x2env.test_codex import executable

    store, _, scene, entity, version, proof, _ = color_inputs(tmp_path)
    program = executable(
        tmp_path,
        dict(
            object="box",
            match=True,
            colors=["blue"],
            materials=["plastic"],
            confidence=0.9,
            same_kind=None,
            plausible=None,
            suggests=None,
        ),
    )
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "test-double", store
    )
    advisory = backend.assess_asset_candidates(
        (
            VisualCandidate(
                candidate_id=version.version_sha256,
                category="box",
                name="box",
                preview=proof.image,
                want_color="pink",
                want_material="plastic",
            ),
        ),
        output_root=tmp_path / "assessment",
        timeout=10,
    )
    assert classify_color_repair(store, scene, entity, version, proof, advisory) is not None


@pytest.mark.parametrize(
    "fault", ["name", "range", "authority", "tools", "identity", "missing", "candidate"]
)
def test_failed_color_proposal_keeps_evidence_and_never_returns_patch(tmp_path, fault):
    from self_improving.harness.x2env.codex import CodexBackend
    from self_improving.harness.x2env.local_color_advisory import (
        classify_color_repair,
        propose_color_repair,
    )
    from tests.self_improving.harness.x2env.test_codex import executable

    store, registry, scene, entity, version, proof, advisory = color_inputs(tmp_path)
    candidate = classify_color_repair(store, scene, entity, version, proof, advisory)
    answer = {"declared_color": "pink", "rgba": [0.9, 0.4, 0.6, 1.0]}
    if fault == "name":
        answer["declared_color"] = "blue"
    if fault == "range":
        answer["rgba"][0] = 2.0
    if fault == "authority":
        answer["approved"] = True
    program = executable(tmp_path, answer, tool=fault == "tools")
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "test-double", store
    )
    if fault == "identity":
        backend.executable_sha = "b" * 64
    if fault == "missing":
        backend.executable = tmp_path / "not-present"
    if fault == "candidate":
        candidate = candidate.model_copy(update={"requested_color": "blue"})
    before = registry.find("box")
    result = propose_color_repair(backend, candidate, output_root=tmp_path / "proposal", timeout=10)
    assert result.status == ("blocked" if fault == "missing" else "failed")
    assert result.proposal is None and result.error_code
    assert registry.find("box") == before
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["proposal"] is None and receipt["evidence"]
    assert receipt["external_agent_executed"] == (fault not in {"identity", "missing", "candidate"})


@pytest.mark.parametrize("fault", ["no_color", "dimensions", "category"])
def test_unrequested_color_or_other_entity_constraints_do_not_offer_repair(tmp_path, fault):
    from self_improving.harness.x2env.local_color_advisory import classify_color_repair

    store, _, scene, _, version, proof, advisory = color_inputs(tmp_path)
    document = json.loads(store.read_artifact(scene))
    if fault == "no_color":
        document["entities"][0]["color"] = None
    elif fault == "dimensions":
        document["entities"][0]["dimensions"] = [0.2, 0.1, 0.1]
    else:
        document["entities"][0]["category"] = "mouse"
    scene = store.write_artifact(json.dumps(document).encode(), "application/json")
    entity = SceneIR.model_validate_json(store.read_artifact(scene)).entities[0]
    assert classify_color_repair(store, scene, entity, version, proof, advisory) is None


def test_color_advisory_rejects_invalid_deadline_and_unsafe_output(tmp_path):
    from self_improving.harness.x2env.local_color_advisory import (
        classify_color_repair,
        propose_color_repair,
    )

    store, _, scene, entity, version, proof, advisory = color_inputs(tmp_path)
    candidate = classify_color_repair(store, scene, entity, version, proof, advisory)
    with pytest.raises(ValueError, match="timeout"):
        propose_color_repair(None, candidate, output_root=tmp_path / "attempt", timeout=True)
    with pytest.raises(ValueError, match="unsafe"):
        propose_color_repair(None, candidate, output_root="relative")


def test_color_proposal_cancellation_retains_transport_then_propagates(tmp_path):
    import signal
    import sys

    from self_improving.harness.x2env.codex import CodexBackend
    from self_improving.harness.x2env.local_color_advisory import (
        classify_color_repair,
        propose_color_repair,
    )

    store, _, scene, entity, version, proof, advisory = color_inputs(tmp_path)
    candidate = classify_color_repair(store, scene, entity, version, proof, advisory)
    program = tmp_path / "waiting-double"
    program.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(20)\n")
    program.chmod(0o700)
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "test-double", store
    )

    def cancel(signum, frame):
        raise KeyboardInterrupt("test cancellation")

    original = signal.signal(signal.SIGALRM, cancel)
    signal.setitimer(signal.ITIMER_REAL, 0.3)
    try:
        with pytest.raises(KeyboardInterrupt):
            propose_color_repair(backend, candidate, output_root=tmp_path / "proposal", timeout=10)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, original)
    receipt = json.loads((tmp_path / "proposal/result.json").read_text())
    assert receipt["error_code"] == "model_interrupted" and receipt["proposal"] is None
    terminal = json.loads((tmp_path / "proposal/process-terminal.json").read_text())
    assert terminal["reaped"] is True and terminal["returncode"] is not None


@pytest.mark.parametrize("fault", ["failed", "count", "verdict", "detail"])
def test_bound_outer_receipt_does_not_hide_inner_visual_faults(tmp_path, fault):
    from self_improving.harness.x2env.local_color_advisory import classify_color_repair

    store, _, scene, entity, version, proof, advisory = color_inputs(tmp_path)
    if fault == "failed":
        advisory = advisory.model_copy(update={"status": "failed", "error_code": "model_error"})
    elif fault == "count":
        advisory = advisory.model_copy(update={"verdicts": ()})
    elif fault == "verdict":
        advisory = advisory.model_copy(
            update={
                "verdicts": (advisory.verdicts[0].model_copy(update={"candidate_id": "wrong"}),)
            }
        )
    else:
        detail = json.loads(store.read_artifact(advisory.verdicts[0].detail))
        detail["asked_category"] = "mouse"
        ref = store.write_artifact(json.dumps(detail).encode(), "application/json")
        advisory = advisory.model_copy(
            update={
                "verdicts": (advisory.verdicts[0].model_copy(update={"detail": ref}),),
                "evidence": (ref,),
            }
        )
    receipt = json.loads(store.read_artifact(advisory.receipt))
    receipt.update(
        status=advisory.status,
        error_code=advisory.error_code,
        verdicts=[v.model_dump(mode="json") for v in advisory.verdicts],
        evidence=[r.model_dump(mode="json") for r in advisory.evidence],
    )
    advisory = advisory.model_copy(
        update={"receipt": store.write_artifact(json.dumps(receipt).encode(), "application/json")}
    )
    if fault == "failed":
        assert classify_color_repair(store, scene, entity, version, proof, advisory) is None
    else:
        with pytest.raises(ValueError):
            classify_color_repair(store, scene, entity, version, proof, advisory)


def test_registry_version_without_finite_geometry_dimensions_cannot_offer_color_repair(tmp_path):
    from self_improving.harness.x2env.local_color_advisory import classify_color_repair

    store, registry, scene, entity, version, proof, advisory = color_inputs(tmp_path)
    report = json.loads(store.read_artifact(version.normalization_report))
    report["dimensions_m"] = [0, 0.1, 0.1]
    invalid = registry.register(
        version.asset_id,
        version.category,
        tmp_path / "normalized",
        version.entrypoint,
        files=tuple(f.path for f in version.files),
        normalization_report=store.write_artifact(json.dumps(report).encode(), "application/json"),
        license=version.license,
        source=version.source,
        receipt=version.receipt,
    )
    with pytest.raises(ValueError, match="dimensions_invalid"):
        classify_color_repair(store, scene, entity, invalid, proof, advisory)
