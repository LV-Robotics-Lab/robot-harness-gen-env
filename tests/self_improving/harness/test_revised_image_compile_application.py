"""Durable revised compilation uses the sealed compiler and existing run authority."""

import pytest

from self_improving.harness.revised_image_compile_application import RevisedImageCompileApplication
from tests.self_improving.harness.test_revised_image_compiler import real_revision  # noqa: F401


@pytest.fixture(scope="module")
def actual_requests(real_revision):  # noqa: F811
    import tempfile
    from pathlib import Path

    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileRequestV2,
        PromptRevisionV3,
        prompt_revision_v3_sha256,
    )
    from self_improving.harness.schemas.x2env_compile_evidence import (
        SceneInterpretationProposalV1,
        canonical_evidence_sha256,
    )
    from tests.self_improving.harness.test_native_prompt_revision_contracts import typed_draft

    compiler, ref, _, publish = real_revision
    first = Image2EnvGenesisCompileRequestV2.model_validate_json(
        compiler.artifact_store.resolve(ref).path.read_bytes()
    )
    body = first.model_dump(mode="json")
    revision = body["compile_input"]["attempt"]["revision"]
    text = "Place the mouse at x=-0.10 m, y=0.05 m, yaw=0.30 rad on the table."
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "replacement.txt"
        path.write_text(text)
        prompt = compiler.artifact_store.put_file(
            path, name=path.name, media_type="text/plain", schema_version="harness.prompt_text.v1"
        )
    revision["patch"]["replacement_text"] = text
    revision["revised_prompt"] = prompt.model_dump(mode="json")
    revision["layout_proposal"].update(
        replacement_text=text,
        effective_prompt=prompt.model_dump(mode="json"),
        position_xy_m=(-0.1, 0.05),
        yaw_rad=0.3,
    )
    revision["revision_sha256"] = prompt_revision_v3_sha256(typed_draft(PromptRevisionV3, revision))
    revised = PromptRevisionV3.model_validate(revision)
    revision_ref = publish(revised)
    body["compile_input"]["attempt"].update(
        revision=revised, prompt_revision=revision_ref, effective_prompt=prompt
    )
    body["expected_receipt_head_sha256"] = revision_ref.sha256
    proposal = body["compile_input"]["proposal"]
    proposal["effective_prompt"] = prompt.model_dump(mode="json")
    proposal["proposal_sha256"] = canonical_evidence_sha256(proposal, "proposal_sha256")
    proposal = SceneInterpretationProposalV1.model_validate(proposal)
    body["compile_input"].update(
        proposal=proposal, interpretation_proposal=publish(proposal), seed=23
    )
    second = Image2EnvGenesisCompileRequestV2.model_validate(body)
    return compiler, (first, second)


def test_formal_two_revision_candidates_qualify_register_and_restore(actual_requests):
    import json
    import os
    from pathlib import Path
    from uuid import uuid4

    from self_improving.harness.schemas.common import RunStatus

    root = Path(os.environ["REVISED_IMAGE_APPLICATION_ROOT"])
    compiler, requests = actual_requests
    app = RevisedImageCompileApplication(root / "application", compiler)
    evaluations = tuple(app.evaluate(request) for request in requests)
    assert all(e.state.status is RunStatus.SUCCEEDED for e in evaluations)
    descriptor = app.qualify(tuple(e.state.run_id for e in evaluations))
    assert descriptor.version == "3.0.0"
    admission = app.admit()
    run_id = uuid4()
    state = app.invoke_typed(requests[0], run_id=run_id)
    assert state.status is RunStatus.SUCCEEDED
    assert app.invoke_typed(requests[0], run_id=run_id) == state
    restarted = RevisedImageCompileApplication(
        root / "application", compiler, qualification=descriptor.qualification_artifact
    )
    result, _ = restarted.read(run_id)
    assert restarted.run_state(run_id) == state
    from self_improving.harness.registry import ControlledRunIdentityError

    with pytest.raises(ControlledRunIdentityError):
        restarted.invoke_typed(requests[1], run_id=run_id)
    summary = {
        "qualification": descriptor.qualification_artifact.model_dump(mode="json"),
        "descriptor": descriptor.model_dump(mode="json"),
        "admission": admission.admission_ref.model_dump(mode="json"),
        "candidates": [str(e.state.run_id) for e in evaluations],
        "registered_run": str(run_id),
        "result": result.model_dump(mode="json"),
    }
    (root / "application-result.json").write_text(
        json.dumps(summary, sort_keys=True, separators=(",", ":"))
    )


def test_revised_application_rejects_caller_compiler_before_creating_state(tmp_path):
    state = tmp_path / "state"
    with pytest.raises(TypeError, match="fixed revised compiler"):
        RevisedImageCompileApplication(state, object())
    assert not state.exists()


def test_candidate_requires_exact_typed_revision_before_any_invocation(tmp_path):
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler

    store = LocalArtifactStore(tmp_path / "cas")
    compiler = RevisedImage2EnvGenesisCompiler(store, Image2EnvGenesisCompiler(store, object()))
    app = RevisedImageCompileApplication(tmp_path / "app", compiler)
    with pytest.raises(TypeError, match="exact request"):
        app.evaluate({})
    assert app.skills == ()


def test_new_qualification_contract_cannot_admit_old_compile_report():
    from self_improving.harness.schemas.revised_image_compile_application import (
        RevisedCompileQualificationReportV1,
    )

    with pytest.raises(ValueError):
        RevisedCompileQualificationReportV1.model_validate(
            {"schema_version": "harness.image2env_genesis_skill_qualification_report.v1"}
        )


def unqualified_app(tmp_path):
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler

    store = LocalArtifactStore(tmp_path / "cas")
    compiler = RevisedImage2EnvGenesisCompiler(store, Image2EnvGenesisCompiler(store, object()))
    return RevisedImageCompileApplication(tmp_path / "app", compiler)


def test_unregistered_requests_and_unknown_candidates_cannot_gain_authority(tmp_path):
    from uuid import uuid4

    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileRequestV2,
    )
    from tests.self_improving.harness.test_native_prompt_revision_contracts import compile_request

    app = unqualified_app(tmp_path)
    request = Image2EnvGenesisCompileRequestV2.model_validate(compile_request())
    run_id = uuid4()
    assert app.run_state(run_id) is None
    assert app.invocation(run_id) is None
    with pytest.raises(TypeError):
        app.invoke({}, run_id=run_id)
    with pytest.raises(TypeError):
        app.invoke_typed({}, run_id=run_id)
    with pytest.raises(ValueError, match="registered"):
        app.invoke_typed(request, run_id=run_id)
    with pytest.raises(ValueError, match="registered"):
        app.read(run_id)
    with pytest.raises(ValueError, match="registered"):
        app.admit()
    with pytest.raises(ValueError, match="implementation"):
        app.qualify(())
    with pytest.raises(ValueError, match="terminal"):
        app.qualify((run_id,))


def test_failed_candidate_is_durable_and_cannot_qualify(tmp_path):
    from self_improving.harness.schemas.common import RunStatus
    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileRequestV2,
    )
    from tests.self_improving.harness.test_native_prompt_revision_contracts import compile_request

    app = unqualified_app(tmp_path)
    evaluated = app.evaluate(Image2EnvGenesisCompileRequestV2.model_validate(compile_request()))
    assert evaluated.state.status is RunStatus.FAILED
    restarted = RevisedImageCompileApplication(tmp_path / "app", app.compiler)
    assert restarted.run_state(evaluated.state.run_id) == evaluated.state
    with pytest.raises(ValueError, match="terminal"):
        restarted.qualify((evaluated.state.run_id,))


@pytest.mark.parametrize(
    "field,value",
    (("schema_version", None), ("media_type", "text/plain"), ("uri", "file:///tmp/input")),
)
def test_invocation_envelope_rejects_untyped_or_external_request(field, value):
    from self_improving.harness.schemas.revised_image_compile_application import (
        INVOCATION_ID,
        RevisedCompileInvocationV1,
    )
    from tests.self_improving.harness.test_native_prompt_revision_contracts import ref

    request = ref("harness.image2env_genesis_compile_request.v2")
    request[field] = value
    with pytest.raises(ValueError, match="exact"):
        RevisedCompileInvocationV1(schema_version=INVOCATION_ID, request=request)


def qualification_report_document():
    from uuid import uuid4

    from self_improving.harness.schemas.revised_image_compile_application import REPORT_ID
    from tests.self_improving.harness.test_native_prompt_revision_contracts import ref

    cases = []
    for number in range(2):
        cases.append(
            dict(
                run_id=uuid4(),
                invocation_digest="a" * 64,
                compile_receipt=ref(
                    "harness.image2env_genesis_compile_receipt.v2", f"compile-{number}"
                ),
                prompt_revision=ref("harness.prompt_revision.v3", f"revision-{number}"),
                predecessor_environment=ref("harness.image2env_genesis_compile_receipt.v1"),
                original_source=ref(None),
                asset_qualification=ref("harness.asset_qualification.v1"),
                scene=ref("harness.genesis_scene_spec.v1", f"scene-{number}"),
            )
        )
    return dict(
        schema_version=REPORT_ID,
        skill_ref="image2env.compile@3.0.0",
        status="pass",
        scope="static_scene_candidate",
        cases=sorted(cases, key=lambda c: str(c["run_id"])),
        implementation_sources=(ref(None, "implementation", "text/x-python"),),
        compile_implementation_sha256="b" * 64,
        implementation_sha256="c" * 64,
    )


@pytest.mark.parametrize(
    "variation", ("case_type", "duplicate", "source_type", "same_scene", "same_revision")
)
def test_new_report_rejects_unbound_or_duplicate_evidence(variation):
    from self_improving.harness.schemas.revised_image_compile_application import (
        RevisedCompileQualificationReportV1,
    )

    data = qualification_report_document()
    if variation == "case_type":
        data["cases"][0]["compile_receipt"]["schema_version"] = (
            "harness.image2env_genesis_compile_receipt.v1"
        )
    elif variation == "duplicate":
        data["cases"][1]["run_id"] = data["cases"][0]["run_id"]
    elif variation == "source_type":
        data["implementation_sources"][0]["media_type"] = "text/plain"
    else:
        key = "scene" if variation == "same_scene" else "prompt_revision"
        data["cases"][1][key] = data["cases"][0][key]
    with pytest.raises(ValueError):
        RevisedCompileQualificationReportV1.model_validate(data)


def test_qualification_metadata_cannot_relabel_old_skill(tmp_path):
    import json

    from self_improving.harness.schemas.common import SkillQualification

    app = unqualified_app(tmp_path)
    qualification = SkillQualification(
        skill_ref="image2env.compile@2.0.0",
        status="pass",
        deterministic_case_id="two-native-layout-revisions",
        regression_command=(
            "pytest -q tests/self_improving/harness/test_revised_image_compile_application.py"
        ),
        report_sha256="a" * 64,
    )
    path = tmp_path / "qualification.json"
    path.write_text(
        json.dumps(qualification.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    )
    ref = app.artifact_store.put_file(
        path,
        name=path.name,
        media_type="application/json",
        schema_version="harness.skill_qualification.v1",
    )
    with pytest.raises(ValueError, match="metadata"):
        app.bind(ref)
    assert app.skills == ()


def test_corrupt_sqlite_cannot_be_recreated_as_empty_authority(tmp_path):
    import sqlite3

    app = unqualified_app(tmp_path)
    damaged = tmp_path / "damaged"
    damaged.mkdir()
    (damaged / "runs.sqlite").write_bytes(b"not a database")
    with pytest.raises(sqlite3.DatabaseError):
        RevisedImageCompileApplication(damaged, app.compiler)
    assert (damaged / "runs.sqlite").read_bytes() == b"not a database"


@pytest.fixture(scope="module")
def existing_application():
    import json
    import os
    from pathlib import Path

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler
    from self_improving.harness.schemas.common import ArtifactRef

    root = os.environ.get("REVISED_IMAGE_APPLICATION_ROOT")
    if not root:
        pytest.skip("requires real revised compile qualification and terminal")
    root = Path(root)
    saved = json.loads((root / "resume-config.json").read_bytes())
    cfg = json.loads(Path(saved["input_config"]).read_bytes())
    store = LocalArtifactStore(Path(cfg["cas_root"]))
    asset = GenesisAssetQualificationApplication(
        Path(cfg["asset_state_path"]), store, ArtifactRef.model_validate(cfg["asset_manifest"])
    )
    compiler = RevisedImage2EnvGenesisCompiler(store, Image2EnvGenesisCompiler(store, asset))
    app = RevisedImageCompileApplication(
        root / "application",
        compiler,
        qualification=ArtifactRef.model_validate(saved["qualification"]),
    )
    return app, saved


def test_formal_existing_qualification_conflict_pending_and_second_registered_input(
    existing_application,
):
    import json
    from pathlib import Path
    from uuid import UUID, uuid4

    from self_improving.harness.registry import (
        ControlledRunIdentityError,
        ControlledRunPendingError,
    )
    from self_improving.harness.schemas.common import RunStatus
    from self_improving.harness.schemas.revised_image_compile_application import (
        INVOCATION_ID,
        RevisedCompileInvocationV1,
    )

    app, saved = existing_application
    original = UUID(saved["registered_run"])
    same = RevisedCompileInvocationV1.model_validate(saved["registered_request"])
    other = next(ref for ref in saved["requests"] if ref != saved["registered_request"]["request"])
    changed = RevisedCompileInvocationV1(schema_version=INVOCATION_ID, request=other)
    with pytest.raises(ControlledRunIdentityError):
        app.invoke(changed, run_id=original)
    pending = uuid4()
    intent = app.run_store.read_controlled_intent(original).model_copy(update={"run_id": pending})
    assert app.run_store.reserve_controlled_intent(intent)
    with pytest.raises(ControlledRunPendingError):
        app.invoke(same, run_id=pending)
    assert app.invocation(pending) is None and app.run_state(pending) is None
    second_id = uuid4()
    state = app.invoke(changed, run_id=second_id)
    assert state.status is RunStatus.SUCCEEDED
    assert state.output != saved["registered_output"]
    with pytest.raises(ValueError, match="candidate"):
        app.qualify((second_id,))
    with pytest.raises(ValueError, match="registered"):
        app.evaluate({})
    # Re-qualifying the same completed cases is deterministic, without invoking
    # either compiler candidate again or assigning a new qualification identity.
    descriptor = app.qualify(tuple(UUID(value) for value in saved["candidates"]))
    assert descriptor.qualification_artifact.model_dump(mode="json") == saved["qualification"]
    admission = app.admit()
    Path(saved["root"], "second-registered-result.json").write_text(
        json.dumps(
            {
                "run_id": str(second_id),
                "state": state.model_dump(mode="json"),
                "qualification": saved["qualification"],
                "admission": admission.admission_ref.model_dump(mode="json"),
                "pending_run_id": str(pending),
                "pending_invocation_created": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def test_formal_second_root_reload_and_noncanonical_request_failure(existing_application, tmp_path):
    import json
    from pathlib import Path
    from uuid import UUID, uuid4

    from self_improving.harness.schemas.common import ArtifactRef, RunStatus
    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileRequestV2,
    )
    from self_improving.harness.schemas.revised_image_compile_application import (
        INVOCATION_ID,
        RevisedCompileInvocationV1,
    )
    from self_improving.harness.schemas.typed_qualification_admission import (
        TypedQualificationAdmissionV1,
    )
    from self_improving.harness.typed_qualification_admission import (
        verify_typed_qualification_admission,
    )

    app, saved = existing_application
    result, receipt = app.read(UUID(saved["registered_run"]))
    assert receipt.model_dump(mode="json") == saved["registered_output"]
    second = json.loads(Path(saved["root"], "second-registered-result.json").read_bytes())
    admission_ref = ArtifactRef.model_validate(second["admission"])
    admission = TypedQualificationAdmissionV1.model_validate_json(
        app.artifact_store.resolve(admission_ref).path.read_bytes()
    )
    checked = verify_typed_qualification_admission(
        artifact_store=app.artifact_store, admission=admission
    )
    assert checked.admission_ref == admission_ref
    request_ref = ArtifactRef.model_validate(saved["registered_request"]["request"])
    request = Image2EnvGenesisCompileRequestV2.model_validate_json(
        app.artifact_store.resolve(request_ref).path.read_bytes()
    )
    path = tmp_path / "noncanonical.json"
    path.write_text(json.dumps(request.model_dump(mode="json"), indent=2))
    bad = app.artifact_store.put_file(
        path, name=path.name, media_type="application/json", schema_version=request.schema_version
    )
    envelope = RevisedCompileInvocationV1(schema_version=INVOCATION_ID, request=bad)
    failed_id = uuid4()
    failed = app.invoke(envelope, run_id=failed_id)
    assert failed.status is RunStatus.FAILED
    assert "canonical" in failed.model_dump_json()
    assert app.invoke(envelope, run_id=failed_id) == failed
    document = {
        "registered_run": saved["registered_run"],
        "receipt": result.model_dump(mode="json"),
        "admission": admission_ref.model_dump(mode="json"),
        "failed_run": str(failed_id),
        "failed_state": failed.model_dump(mode="json"),
        "cas_root": str(app.artifact_store.root),
        "genesis_executed": False,
        "scope": "static_scene_candidate",
    }
    output = Path(saved["root"], "relocation-result.json")
    output.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")))


@pytest.mark.parametrize("variation", ("missing_receipt", "wrong_output"))
def test_formal_semantic_sqlite_corruption_fails_after_checksum_consistency(
    existing_application, tmp_path, variation
):
    import hashlib
    import json
    import sqlite3
    from uuid import UUID

    app, saved = existing_application
    copied = tmp_path / "copied"
    copied.mkdir()
    with (
        sqlite3.connect("file:" + str(app.run_store.path) + "?mode=ro", uri=True) as source,
        sqlite3.connect(copied / "runs.sqlite") as target,
    ):
        source.backup(target)
        run_id = saved["candidates"][0]
        raw = target.execute(
            "SELECT payload_json FROM harness_run_states WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        payload = json.loads(raw)
        if variation == "missing_receipt":
            payload["artifacts"] = [
                ref
                for ref in payload["artifacts"]
                if ref["schema_version"] != "harness.image2env_genesis_compile_receipt.v2"
            ]
        else:
            payload["output"]["implementation_sha256"] = "f" * 64
        raw = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        target.execute(
            "UPDATE harness_run_states SET payload_json=?,payload_sha256=? WHERE run_id=?",
            (raw, hashlib.sha256(raw).hexdigest(), run_id),
        )
    contender = RevisedImageCompileApplication(copied, app.compiler)
    expected = "one compile receipt" if variation == "missing_receipt" else "canonical input/output"
    with pytest.raises(ValueError, match=expected):
        contender.qualify((UUID(run_id),))
    assert contender.skills == ()


def test_formal_self_consistent_report_change_cannot_requalify(existing_application, tmp_path):
    import json

    from self_improving.harness.schemas.common import ArtifactRef, SkillQualification
    from self_improving.harness.schemas.revised_image_compile_application import (
        RevisedCompileQualificationReportV1,
    )

    app, saved = existing_application
    qualification = SkillQualification.model_validate_json(
        app.artifact_store.resolve(
            ArtifactRef.model_validate(saved["qualification"])
        ).path.read_bytes()
    )
    report = RevisedCompileQualificationReportV1.model_validate_json(
        app.artifact_store.resolve_digest(qualification.report_sha256).read_bytes()
    )
    changed = report.model_copy(update={"implementation_sha256": "f" * 64})
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps(changed.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    )
    ref = app.artifact_store.put_file(
        path, name=path.name, media_type="application/json", schema_version=changed.schema_version
    )
    qualification = qualification.model_copy(update={"report_sha256": ref.sha256})
    path.write_text(
        json.dumps(qualification.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    )
    ref = app.artifact_store.put_file(
        path,
        name="qualification.json",
        media_type="application/json",
        schema_version="harness.skill_qualification.v1",
    )
    with pytest.raises(ValueError, match="durable revised candidate"):
        app.bind(ref)


def test_formal_missing_cas_member_stops_terminal_read(existing_application, tmp_path):
    import json
    from uuid import UUID

    from self_improving.harness.schemas.common import ArtifactRef

    app, saved = existing_application
    scene = ArtifactRef.model_validate(saved["registered_output"]["scene"])
    body = json.loads(app.artifact_store.resolve(scene).path.read_bytes())
    member = ArtifactRef.model_validate(body["geometry"])
    path = app.artifact_store.resolve(member).path
    backup = tmp_path / "retained-geometry"
    # Rename only this isolated hardlink; never overwrite shared source bytes.
    path.rename(backup)
    try:
        with pytest.raises(ValueError):
            app.read(UUID(saved["registered_run"]))
    finally:
        backup.rename(path)
    assert app.run_state(UUID(saved["registered_run"])).output == saved["registered_output"]


def test_formal_other_skill_journal_cannot_supply_candidates(existing_application, tmp_path):
    import os
    import sqlite3
    from uuid import UUID

    source_path = os.environ.get("REVISED_LEGACY_COMPILE_SQLITE")
    if not source_path:
        pytest.skip("requires isolated original compile@2 SQLite backup")
    app, _ = existing_application
    copied = tmp_path / "foreign-skill"
    copied.mkdir()
    with (
        sqlite3.connect("file:" + source_path + "?mode=ro", uri=True) as source,
        sqlite3.connect(copied / "runs.sqlite") as target,
    ):
        source.backup(target)
    contender = RevisedImageCompileApplication(copied, app.compiler)
    with pytest.raises(ValueError, match="Skill identity"):
        contender.qualify((UUID("a42a3a83-98ea-4c01-a35f-3003a9109c49"),))


def test_formal_changed_installed_source_rejects_existing_candidates(
    existing_application, tmp_path
):
    import json
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    app, saved = existing_application
    stage = tmp_path / "copied-code"
    stage.mkdir()
    repository = Path(__file__).resolve().parents[3]
    shutil.copytree(
        repository / "self_improving",
        stage / "self_improving",
        ignore=shutil.ignore_patterns("__pycache__", "json_schemas", "golden_e2e_progress"),
    )
    module = stage / "self_improving/harness/revised_image_compile_application.py"
    module.write_bytes(module.read_bytes() + b"\n# Changed installed implementation fixture.\n")
    script = stage / "check.py"
    script.write_text("""import json
from pathlib import Path
from uuid import UUID
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.genesis_asset_qualification_application import (
    GenesisAssetQualificationApplication,
)
from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler
from self_improving.harness.revised_image_compile_application import RevisedImageCompileApplication
from self_improving.harness.schemas.common import ArtifactRef
saved=json.loads(Path(__import__('sys').argv[1]).read_bytes())
cfg=json.loads(Path(saved['input_config']).read_bytes())
store=LocalArtifactStore(Path(cfg['cas_root']))
asset=GenesisAssetQualificationApplication(Path(cfg['asset_state_path']),store,ArtifactRef.model_validate(cfg['asset_manifest']))
compiler=RevisedImage2EnvGenesisCompiler(store,Image2EnvGenesisCompiler(store,asset))
app=RevisedImageCompileApplication(Path(saved['root'])/'application',compiler)
try:
    app.qualify(tuple(UUID(v) for v in saved['candidates']))
except ValueError as error:
    assert 'dependencies changed' in str(error),str(error)
    print('installed source drift rejected before compiler execution')
else:
    raise AssertionError('changed installed source accepted existing candidates')
""")
    env = {
        **os.environ,
        "PYTHONPATH": str(stage) + os.pathsep + str(repository),
        "COVERAGE_FILE": str(Path(saved["root"]) / ".coverage.source-drift"),
    }
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--branch",
            "--source=self_improving.harness.revised_image_compile_application",
            str(script),
            str(Path(saved["root"]) / "resume-config.json"),
        ],
        cwd=stage,
        env=env,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert "before compiler execution" in process.stdout
    Path(saved["root"], "source-drift-result.json").write_text(
        json.dumps(
            {
                "stdout": process.stdout,
                "stderr": process.stderr,
                "copied_source": str(module),
                "returncode": process.returncode,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
