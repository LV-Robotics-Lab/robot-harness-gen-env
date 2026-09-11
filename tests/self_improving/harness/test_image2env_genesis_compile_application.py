"""Native compile registration derives authority from durable candidate execution."""

import importlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.genesis_asset_qualification_application import (
    GenesisAssetQualificationApplication,
)
from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.image2env_genesis_compile import (
    Image2EnvGenesisCompileRequestV1,
)
from tests.self_improving.harness.test_genesis_runtime_execution import (
    execution_request,  # noqa: F401
)


def test_missing_candidate_cannot_issue_native_skill_qualification(execution_request, tmp_path):  # noqa: F811
    module = importlib.import_module("self_improving.harness.image2env_genesis_compile_application")
    store, _, _, _, request, _ = execution_request
    assets = GenesisAssetQualificationApplication(
        tmp_path / "assets.sqlite", store, request.manifest
    )
    app = module.Image2EnvGenesisCompileApplication(
        tmp_path / "native", Image2EnvGenesisCompiler(store, assets)
    )
    before = set(store.root.rglob("*"))
    with pytest.raises(ValueError, match="candidate"):
        app.qualify((uuid4(),))
    assert app.skills == ()
    assert set(store.root.rglob("*")) == before


def test_native_candidate_without_asset_authority_records_failure(execution_request, tmp_path):  # noqa: F811
    from tests.self_improving.harness.test_x2env_compile_closure import _case

    module = importlib.import_module("self_improving.harness.image2env_genesis_compile_application")
    _, _, _, _, runtime, _ = execution_request
    case = _case(tmp_path / "inputs")
    assets = GenesisAssetQualificationApplication(
        tmp_path / "assets.sqlite", case.store, runtime.manifest
    )
    app = module.Image2EnvGenesisCompileApplication(
        tmp_path / "native", Image2EnvGenesisCompiler(case.store, assets)
    )
    request = Image2EnvGenesisCompileRequestV1(
        schema_version="harness.image2env_genesis_compile_request.v1",
        compile_input=case.compile_input,
        qualification_run_id=uuid4(),
        source_scene=case.compile_input.interpretation_proposal.model_copy(
            update={"schema_version": None}
        ),
        table_center_m=(0.0, 0.0, 0.2),
        table_size_m=(1.0, 0.8, 0.4),
        table_friction=0.5,
    )
    first = app.evaluate(request)
    second = app.evaluate(request)
    assert first.state.status.value == "failed"
    assert first.invocation.invocation_digest == second.invocation.invocation_digest
    assert app.run_state(first.state.run_id) == first.state
    assert app.invocation(first.state.run_id) == first.invocation
    with pytest.raises(ValueError, match="candidate"):
        app.qualify((first.state.run_id,))
    with pytest.raises(TypeError):
        app.evaluate(request.model_dump(mode="json"))
    with pytest.raises(TypeError):
        app.invoke_typed({}, run_id=uuid4())
    with pytest.raises(TypeError):
        module.Image2EnvGenesisCompileApplication(tmp_path / "wrong", object())


@pytest.mark.parametrize(
    "variation", ["case_id", "command", "media_type", "schema", "noncanonical"]
)
def test_qualification_metadata_fails_before_accepting_report(
    execution_request,  # noqa: F811
    tmp_path,
    variation,
):
    module = importlib.import_module("self_improving.harness.image2env_genesis_compile_application")
    store, _, _, _, runtime, _ = execution_request
    path = tmp_path / "report.json"
    path.write_text("{}")
    report = store.put_file(
        path, name="report.json", media_type="application/json", schema_version=None
    )
    data = {
        "skill_ref": "image2env.compile@2.0.0",
        "status": "pass",
        "deterministic_case_id": "native-mouse-three-view-exact-reuse",
        "regression_command": (
            "pytest -q tests/self_improving/harness/"
            + "test_image2env_genesis_compile_application.py"
        ),
        "report_sha256": report.sha256,
    }
    if variation == "case_id":
        data["deterministic_case_id"] = "unrelated-case"
    if variation == "command":
        data["regression_command"] = "true"
    path.write_bytes(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        + (b"\n" if variation == "noncanonical" else b"")
    )
    ref = store.put_file(
        path,
        name="qualification.json",
        media_type="text/plain" if variation == "media_type" else "application/json",
        schema_version="other.v1" if variation == "schema" else "harness.skill_qualification.v1",
    )
    assets = GenesisAssetQualificationApplication(
        tmp_path / "assets.sqlite", store, runtime.manifest
    )
    with pytest.raises(ValueError, match="qualification metadata"):
        module.Image2EnvGenesisCompileApplication(
            tmp_path / "native", Image2EnvGenesisCompiler(store, assets), qualification=ref
        )


@pytest.mark.skipif(
    not os.environ.get("GOLDEN_NATIVE_COMPILE_PREVIOUS_SUMMARY"),
    reason="requires real durable three-image candidates",
)
def test_formal_changed_application_cannot_requalify_old_candidates(tmp_path):
    original = importlib.import_module(
        "self_improving.harness.image2env_genesis_compile_application"
    )
    source_root = Path(original.__file__).parent
    for name in (
        "image2env_genesis_compile_application.py",
        "schemas/image2env_genesis_compile_application.py",
        "registry.py",
        "run_store.py",
        "event_journal.py",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / name, target)
    path = tmp_path / "image2env_genesis_compile_application.py"
    path.write_bytes(
        path.read_bytes() + b"\n# Changed implementation identity for an independent deployment.\n"
    )
    spec = importlib.util.spec_from_file_location(
        "self_improving.harness.changed_native_compile_application", path
    )
    changed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(changed)
    config = json.loads(Path(os.environ["GOLDEN_NATIVE_IMAGE_COMPILE_CONFIG"]).read_text())
    store = LocalArtifactStore(Path(config["cas_root"]))
    assets = GenesisAssetQualificationApplication(
        Path(config["state_path"]),
        store,
        ArtifactRef.model_validate(config["approved_manifest_ref"]),
    )
    summary_path = Path(os.environ["GOLDEN_NATIVE_COMPILE_PREVIOUS_SUMMARY"])
    summary = json.loads(summary_path.read_text())
    app = changed.Image2EnvGenesisCompileApplication(
        summary_path.parent, Image2EnvGenesisCompiler(store, assets)
    )
    with pytest.raises(ValueError, match="implementation"):
        app.qualify(tuple(UUID(value) for value in summary["candidate_run_ids"]))
    # The installed final implementation also refuses the historical candidate dependency identity.
    installed = original.Image2EnvGenesisCompileApplication(
        summary_path.parent, Image2EnvGenesisCompiler(store, assets)
    )
    with pytest.raises(ValueError, match="implementation"):
        installed.qualify(tuple(UUID(value) for value in summary["candidate_run_ids"]))


@pytest.mark.parametrize(
    "variation",
    [
        "valid",
        "case_uuid",
        "case_ref",
        "duplicate_cases",
        "wrong_source",
        "source_not_member",
        "duplicate_source",
    ],
)
def test_native_skill_report_requires_exact_cases_and_implementation_members(variation):
    from self_improving.harness.schemas.image2env_genesis_compile_application import (
        Image2EnvGenesisSkillQualificationReportV1,
    )

    def ref(name, schema, media):
        return ArtifactRef(
            name=name,
            uri="artifact://sha256/" + "a" * 64,
            sha256="a" * 64,
            bytes=1,
            schema_version=schema,
            media_type=media,
        ).model_dump(mode="json")

    source = ref("application.py", None, "text/x-python")
    cases = [
        {
            "run_id": str(value),
            "invocation_digest": "b" * 64,
            "compile_receipt": ref(
                "receipt.json", "harness.image2env_genesis_compile_receipt.v1", "application/json"
            ),
        }
        for value in sorted((uuid4(), uuid4(), uuid4()), key=str)
    ]
    data = {
        "schema_version": "harness.image2env_genesis_skill_qualification_report.v1",
        "skill_ref": "image2env.compile@2.0.0",
        "status": "pass",
        "scope": "static_scene_candidate",
        "cases": cases,
        "application_source": dict(source),
        "implementation_sources": [dict(source)],
        "compile_implementation_sha256": "c" * 64,
        "implementation_sha256": "d" * 64,
    }
    if variation == "case_uuid":
        data["cases"][0]["run_id"] = "00000000-0000-0000-0000-000000000000"
    if variation == "case_ref":
        data["cases"][0]["compile_receipt"]["schema_version"] = "other.v1"
    if variation == "duplicate_cases":
        data["cases"][1] = data["cases"][0]
    if variation == "wrong_source":
        data["application_source"]["media_type"] = "text/plain"
    if variation == "source_not_member":
        data["implementation_sources"][0]["name"] = "different.py"
    if variation == "duplicate_source":
        data["implementation_sources"] *= 2
    if variation == "valid":
        assert (
            Image2EnvGenesisSkillQualificationReportV1.model_validate(data).scope
            == "static_scene_candidate"
        )
    else:
        with pytest.raises(ValueError):
            Image2EnvGenesisSkillQualificationReportV1.model_validate(data)


@pytest.mark.skipif(
    not os.environ.get("GOLDEN_NATIVE_COMPILE_TERMINAL_SUMMARY"),
    reason="requires real production terminal",
)
def test_formal_live_terminal_retry_rejects_missing_nested_asset_without_execution(tmp_path):
    module = importlib.import_module("self_improving.harness.image2env_genesis_compile_application")
    config = json.loads(Path(os.environ["GOLDEN_NATIVE_IMAGE_COMPILE_CONFIG"]).read_text())
    source_store = LocalArtifactStore(Path(config["cas_root"]))
    summary_path = Path(os.environ["GOLDEN_NATIVE_COMPILE_TERMINAL_SUMMARY"])
    summary = json.loads(summary_path.read_text())
    # Isolated directory entries share immutable bytes; unlinking here never alters source CAS.
    shutil.copytree(
        source_store.root / "sha256", tmp_path / "cas" / "sha256", copy_function=os.link
    )
    for source, target in (
        (Path(config["state_path"]), tmp_path / "assets.sqlite"),
        (summary_path.parent / "harness.sqlite3", tmp_path / "native" / "harness.sqlite3"),
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        with (
            closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as reader,
            closing(sqlite3.connect(target)) as writer,
        ):
            reader.backup(writer)
    store = LocalArtifactStore(tmp_path / "cas")
    assets = GenesisAssetQualificationApplication(
        tmp_path / "assets.sqlite",
        store,
        ArtifactRef.model_validate(config["approved_manifest_ref"]),
    )
    qualification = ArtifactRef.model_validate(summary["descriptor"]["qualification_artifact"])
    app = module.Image2EnvGenesisCompileApplication(
        tmp_path / "native", Image2EnvGenesisCompiler(store, assets), qualification=qualification
    )
    run_id = UUID(summary["production_run_ids"][0])
    before = app.run_state(run_id)
    invocation = app.invocation(run_id)
    request = Image2EnvGenesisCompileRequestV1.model_validate(invocation.effective_parameters)
    receipt = before.output
    asset_qualification = json.loads(
        store.resolve(ArtifactRef.model_validate(receipt["qualification"])).path.read_bytes()
    )
    representation = json.loads(
        store.resolve(
            ArtifactRef.model_validate(asset_qualification["representation"])
        ).path.read_bytes()
    )
    member = ArtifactRef.model_validate(representation["members"][-1]["artifact"])
    store.resolve(member).path.unlink()
    with pytest.raises(ValueError):
        app.invoke_typed(request, run_id=run_id)
    assert app.run_state(run_id) == before
    with pytest.raises(ValueError):
        module.Image2EnvGenesisCompileApplication(
            tmp_path / "native",
            Image2EnvGenesisCompiler(store, assets),
            qualification=qualification,
        )
    source_store.resolve(member)


@pytest.mark.skipif(
    not os.environ.get("GOLDEN_NATIVE_COMPILE_APPLICATION_ROOT"),
    reason="requires actual three-image qualified CAS",
)
def test_formal_native_candidates_qualify_register_and_restart():
    module = importlib.import_module("self_improving.harness.image2env_genesis_compile_application")
    base = Path(os.environ["GOLDEN_NATIVE_IMAGE_COMPILE_CONFIG"]).parent
    config = json.loads((base / "config.json").read_text())
    store = LocalArtifactStore(Path(config["cas_root"]))
    assets = GenesisAssetQualificationApplication(
        Path(config["state_path"]),
        store,
        ArtifactRef.model_validate(config["approved_manifest_ref"]),
    )
    compiler = Image2EnvGenesisCompiler(store, assets)
    state_root = Path(os.environ["GOLDEN_NATIVE_COMPILE_APPLICATION_ROOT"])
    app = module.Image2EnvGenesisCompileApplication(state_root, compiler)
    run_ids = []
    requests = []
    for directory in (base, base / "top", base / "side"):
        source = json.loads((directory / "config.json").read_text())
        request = Image2EnvGenesisCompileRequestV1.model_validate_json(
            store.resolve(ArtifactRef.model_validate(source["request_ref"])).path.read_bytes()
        )
        requests.append(request)
        result = app.evaluate(request)
        assert result.state.status.value == "succeeded"
        run_ids.append(result.state.run_id)
    descriptor = app.qualify(tuple(run_ids))
    assert descriptor.version == "2.0.0" and len(app.skills) == 1
    states = []
    for request in requests:
        state = app.invoke_typed(request, run_id=uuid4())
        assert state.status.value == "succeeded"
        states.append(state)
    restarted = module.Image2EnvGenesisCompileApplication(
        state_root, compiler, qualification=descriptor.qualification_artifact
    )
    for request, state in zip(requests, states, strict=True):
        assert restarted.invoke_typed(request, run_id=state.run_id) == state
    with pytest.raises(RuntimeError):
        restarted.invoke_typed(requests[1], run_id=states[0].run_id)
    with pytest.raises(ValueError):
        restarted.evaluate(requests[0])
    (state_root / "qualification-summary.json").write_text(
        json.dumps(
            {
                "descriptor": descriptor.model_dump(mode="json"),
                "candidate_run_ids": [str(value) for value in run_ids],
                "production_run_ids": [str(state.run_id) for state in states],
                "states": [state.model_dump(mode="json") for state in states],
            },
            indent=2,
        )
        + "\n"
    )
