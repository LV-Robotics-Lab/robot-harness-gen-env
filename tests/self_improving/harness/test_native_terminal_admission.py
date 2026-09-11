"""Durable native terminal admission observes actual public deep reads."""

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from tests.self_improving.harness.test_golden_image2env_execution import restored_apps  # noqa: F401


def _blob(store, tmp_path, name, payload):
    path = tmp_path / name
    path.write_bytes(payload)
    return store.put_file(
        path, name=name, media_type="application/octet-stream", schema_version=None
    )


def test_observes_exact_store_resolve_and_digest_without_changing_prior_profile(tmp_path):
    import sys

    from self_improving.harness.native_terminal_admission import observe_native_reads

    store = LocalArtifactStore(tmp_path / "cas")
    other = LocalArtifactStore(tmp_path / "foreign-cas")
    first = _blob(store, tmp_path, "first", b"first")
    second = _blob(store, tmp_path, "second", b"second")
    foreign = _blob(other, tmp_path, "foreign", b"foreign")
    events = []

    def previous(frame, event, result):
        events.append(event)

    original = sys.getprofile()
    try:
        sys.setprofile(previous)

        def read():
            store.resolve(second)
            store.resolve(first)
            store.resolve(first)
            store.resolve_digest(second.sha256)
            other.resolve(foreign)

        result = observe_native_reads(artifact_store=store, read=read)
        assert sys.getprofile() is previous
        assert result.live_authority is False
        assert set(result.closure.artifacts) == {first, second}
        assert {m.sha256 for m in result.closure.digest_members} == {first.sha256, second.sha256}
        assert events
    finally:
        sys.setprofile(original)


@pytest.mark.parametrize(
    "mode", ["reader", "digest", "resolve", "replace_profile", "file", "wrong_store"]
)
def test_read_observation_failures_restore_profile_without_admission(tmp_path, mode):
    import sys

    from self_improving.harness.native_terminal_admission import observe_native_reads

    store = LocalArtifactStore(tmp_path / "cas")
    ref = _blob(store, tmp_path, "member", b"member")
    previous = sys.getprofile()

    def read():
        if mode == "reader":
            raise RuntimeError("deep read failed")
        if mode == "digest":
            store.resolve_digest("f" * 64)
        elif mode == "resolve":
            store.resolve(ref.model_copy(update={"bytes": 999}))
        elif mode == "replace_profile":
            sys.setprofile(None)
        elif mode == "file":
            store.resolve(ref.model_copy(update={"uri": (tmp_path / "member").as_uri()}))

    with pytest.raises((ValueError, RuntimeError, TypeError)):
        observe_native_reads(artifact_store=object() if mode == "wrong_store" else store, read=read)
    assert sys.getprofile() is previous


@pytest.mark.parametrize("phase", ["installation", "restoration"])
def test_incompatible_prior_profiler_fails_closed(tmp_path, phase):
    import sys

    from self_improving.harness.native_terminal_admission import observe_native_reads

    store = LocalArtifactStore(tmp_path / "cas")
    ref = _blob(store, tmp_path, "member", b"member")
    original = sys.getprofile()
    armed = {"value": False}

    def prior(frame, event, result):
        if armed["value"] and event == "c_return" and result is sys.setprofile:
            armed["value"] = False
            sys.setprofile(None)

    try:
        sys.setprofile(prior)
        armed["value"] = phase == "installation"

        def read():
            store.resolve(ref)
            if phase == "restoration":
                armed["value"] = True

        with pytest.raises(ValueError, match="installed|restored"):
            observe_native_reads(artifact_store=store, read=read)
    finally:
        sys.setprofile(original)


def test_no_public_document_constructor_can_issue_live_terminal_admission():
    from self_improving.harness.native_terminal_admission import (
        VerifiedLiveNativeTerminal,
        VerifiedNativeTerminalReload,
    )

    for cls in (VerifiedLiveNativeTerminal, VerifiedNativeTerminalReload):
        with pytest.raises(TypeError):
            cls(None, None, token=object())


def test_same_root_different_store_instance_is_rejected_before_deep_read(recorded, tmp_path):
    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.image2env_genesis_compile_application import (
        Image2EnvGenesisCompileApplication,
    )
    from self_improving.harness.native_terminal_admission import admit_native_terminal

    c = recorded
    assets = GenesisAssetQualificationApplication(
        tmp_path / "assets.sqlite", c.f.store, c.q.admission_ref
    )
    app = Image2EnvGenesisCompileApplication(
        tmp_path / "unqualified-app", Image2EnvGenesisCompiler(c.f.store, assets)
    )
    with pytest.raises(ValueError, match="exact CAS store instance"):
        admit_native_terminal(
            application=app,
            artifact_store=LocalArtifactStore(c.f.store.root),
            run_id=c.run_id,
            qualification_admission=c.q,
            child_authority=c.f.authority,
        )
    with pytest.raises(ValueError, match="current registered descriptor"):
        admit_native_terminal(
            application=app,
            artifact_store=c.f.store,
            run_id=c.run_id,
            qualification_admission=c.q,
            child_authority=c.f.authority,
        )


@pytest.fixture
def recorded(tmp_path):
    """Synthetic recorded evidence exercises byte/SQLite reload, never a live producer."""
    import hashlib
    import json
    from types import SimpleNamespace
    from uuid import uuid4

    from self_improving.harness.event_journal import SQLiteEventJournal
    from self_improving.harness.events import RunRecorder
    from self_improving.harness.run_store import ControlledRunIntent
    from self_improving.harness.schemas.common import RunStatus
    from self_improving.harness.schemas.native_terminal_admission import (
        NativeReadClosureV1,
        NativeTerminalAdmissionV1,
    )
    from self_improving.harness.typed_qualification_admission import (
        freeze_typed_qualification_admission,
    )
    from tests.self_improving.harness.test_typed_qualification_admission import (
        RUN_IDS,
        STAMP,
        _fixture,
    )

    f = _fixture(tmp_path / "qualified", "compile")
    q = freeze_typed_qualification_admission(
        artifact_store=f.store,
        child_authority=f.authority,
        descriptor_ref=f.descriptor,
        qualification_ref=f.qualification,
        qualification_report_ref=f.report,
        implementation_members=f.implementation,
    )

    def canonical(v):
        return json.dumps(
            v.model_dump(mode="json") if hasattr(v, "model_dump") else v,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()

    def put(name, v, schema=None):
        path = tmp_path / name
        path.write_bytes(canonical(v))
        return f.store.put_file(
            path,
            name=name,
            media_type="application/json",
            schema_version=schema or v.schema_version,
        )

    run_id = uuid4()
    invocation = f.authority.read_invocation(RUN_IDS[0]).model_copy(update={"run_id": run_id})
    params_sha = hashlib.sha256(canonical(invocation.effective_parameters)).hexdigest()
    intent = ControlledRunIntent(
        run_id=run_id,
        skill_id="image2env.compile",
        skill_version="2.0.0",
        input_schema="harness.image2env_genesis_compile_request.v1",
        phase="validated_input",
        raw_parameters_sha256=params_sha,
        effective_parameters_sha256=params_sha,
        dependencies=invocation.dependencies,
        descriptor_sha256=f.descriptor.sha256,
        implementation_sha256=q.admission.implementation_sha256,
        qualification_sha256=f.qualification.sha256,
        max_attempts=1,
    )
    f.authority.reserve_controlled_intent(intent)
    f.authority.put_invocation(invocation)
    candidate = f.authority.read_run_state(RUN_IDS[0])
    native_ref = next(
        ref
        for ref in candidate.artifacts
        if ref.schema_version == "harness.image2env_genesis_compile_receipt.v1"
    )
    recorder = RunRecorder(
        run_id=run_id,
        skill_id="image2env.compile",
        skill_version="2.0.0",
        clock=lambda: STAMP,
        sink=SQLiteEventJournal(f.authority.path),
    )
    recorder.start(stage="execute", attempt=1)
    recorder.finish(status=RunStatus.SUCCEEDED, stage="complete", artifact_refs=(native_ref,))
    state = recorder.build_state(
        invocation_digest=invocation.invocation_digest,
        max_attempts=1,
        artifacts=(native_ref,),
        output=candidate.output,
        blocker=None,
    )
    f.authority.put_run_state(state)
    implementation = _blob(
        f.store, tmp_path, "reader.py", b"synthetic reload fixture only"
    ).model_copy(update={"media_type": "text/x-python"})
    refs = tuple(sorted((native_ref, q.admission_ref, implementation), key=canonical))
    index = NativeReadClosureV1(
        schema_version="harness.native_read_closure.v1",
        artifacts=refs,
        digest_members=(),
        scope="actual_public_deep_read_accesses",
    )
    index_ref = put("index.json", index)
    admission = NativeTerminalAdmissionV1(
        schema_version="harness.native_terminal_admission.v1",
        skill_ref="image2env.compile@2.0.0",
        run_id=run_id,
        invocation=invocation,
        run_state=state,
        controlled_intent_sha256=hashlib.sha256(canonical(intent)).hexdigest(),
        native_receipt=native_ref,
        qualification_admission=q.admission_ref,
        application_implementation_sha256=q.admission.implementation_sha256,
        admission_implementation=implementation,
        read_closure=index_ref,
        predecessor_terminal_admission=None,
        authority="observed_successful_public_deep_read",
        simulator_rerun=False,
    )
    admission_ref = put("admission.json", admission)
    return SimpleNamespace(**locals())


def test_recorded_admission_reloads_without_live_authority_in_second_cas(recorded, tmp_path):
    import shutil

    from self_improving.harness.native_terminal_admission import reload_native_terminal
    from self_improving.harness.typed_qualification_admission import (
        verify_typed_qualification_admission,
    )

    c = recorded
    shutil.copytree(c.f.store.root, tmp_path / "relocated")
    store = LocalArtifactStore(tmp_path / "relocated")
    q = verify_typed_qualification_admission(artifact_store=store, admission=c.q.admission)
    result = reload_native_terminal(
        artifact_store=store,
        admission_ref=c.admission_ref,
        child_authority=c.f.authority,
        expected_skill_ref=c.admission.skill_ref,
        expected_run_id=c.run_id,
        qualification_admission=q,
    )
    assert result.admission == c.admission and result.live_authority is False
    with pytest.raises(AttributeError):
        result.admission = None


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "extra",
        "ref_drift",
        "wrong_run",
        "cross_skill",
        "foreign_sqlite",
        "intent_drift",
        "wrong_schema",
        "file_locator",
        "noncanonical",
    ],
)
def test_recorded_admission_rejects_changed_closure_and_subject(recorded, tmp_path, mode):
    from uuid import uuid4

    from self_improving.harness.native_terminal_admission import reload_native_terminal
    from self_improving.harness.run_store import SQLiteRunStore

    c = recorded
    kwargs = dict(
        artifact_store=c.f.store,
        admission_ref=c.admission_ref,
        child_authority=c.f.authority,
        expected_skill_ref=c.admission.skill_ref,
        expected_run_id=c.run_id,
        qualification_admission=c.q,
    )
    if mode == "missing":
        c.f.store.resolve(c.implementation).path.unlink()
    elif mode in ("extra", "ref_drift"):
        c.f.store.resolve(c.index_ref).path.write_bytes(b"changed index")
    elif mode == "wrong_run":
        kwargs["expected_run_id"] = uuid4()
    elif mode == "cross_skill":
        kwargs["expected_skill_ref"] = "genesis.observe@3.0.0"
    elif mode == "foreign_sqlite":
        kwargs["child_authority"] = SQLiteRunStore(tmp_path / "empty.sqlite")
    elif mode == "intent_drift":
        kwargs["admission_ref"] = c.put(
            "wrong-intent.json",
            c.admission.model_copy(update={"controlled_intent_sha256": "f" * 64}),
        )
    elif mode == "wrong_schema":
        kwargs["admission_ref"] = c.admission_ref.model_copy(update={"schema_version": None})
    elif mode == "file_locator":
        kwargs["admission_ref"] = c.admission_ref.model_copy(
            update={"uri": c.f.store.resolve(c.admission_ref).path.as_uri()}
        )
    else:
        path = tmp_path / "noncanonical.json"
        path.write_bytes(b" " + c.canonical(c.admission))
        kwargs["admission_ref"] = c.f.store.put_file(
            path,
            name=path.name,
            media_type="application/json",
            schema_version=c.admission.schema_version,
        )
    with pytest.raises(ValueError):
        reload_native_terminal(**kwargs)


@pytest.mark.parametrize(
    "mode",
    [
        "artifact_duplicate",
        "artifact_order",
        "digest_duplicate",
        "noncas",
        "subject",
        "predecessor",
        "native_type",
        "implementation",
    ],
)
def test_typed_admission_and_index_close_ambiguous_members(recorded, mode):
    from self_improving.harness.schemas.native_terminal_admission import (
        NativeReadClosureV1,
        NativeTerminalAdmissionV1,
    )

    c = recorded
    if mode.startswith("artifact") or mode in ("digest_duplicate", "noncas"):
        value = c.index.model_dump(mode="json")
        if mode == "artifact_duplicate":
            value["artifacts"].append(value["artifacts"][0])
        elif mode == "artifact_order":
            value["artifacts"].reverse()
        elif mode == "digest_duplicate":
            value["digest_members"] = [{"sha256": "f" * 64, "bytes": 1}] * 2
        else:
            value["artifacts"][0]["uri"] = "file:///untrusted"
        model = NativeReadClosureV1
    else:
        value = c.admission.model_dump(mode="json")
        if mode == "subject":
            value["invocation"]["invocation_digest"] = "f" * 64
        elif mode == "predecessor":
            value["predecessor_terminal_admission"] = c.admission_ref.model_dump(mode="json")
        elif mode == "native_type":
            value["native_receipt"]["schema_version"] = "wrong"
        else:
            value["admission_implementation"]["media_type"] = "text/plain"
        model = NativeTerminalAdmissionV1
    with pytest.raises(ValueError):
        model.model_validate(value)


@pytest.mark.parametrize("mode", ["wrong_boundary", "missing_roots", "digest_size"])
def test_reload_cannot_hide_missing_root_or_digest_size(recorded, mode):
    from self_improving.harness.native_terminal_admission import reload_native_terminal

    c = recorded
    ref = c.admission_ref
    store = c.f.store
    if mode == "wrong_boundary":
        store = object()
    else:
        value = c.index.model_dump(mode="json")
        if mode == "missing_roots":
            value["artifacts"] = [c.implementation.model_dump(mode="json")]
        else:
            value["digest_members"] = [
                {"sha256": c.implementation.sha256, "bytes": c.implementation.bytes + 1}
            ]
        index = type(c.index).model_validate(value)
        index_ref = c.put("changed-index.json", index)
        ref = c.put(
            "changed-admission.json", c.admission.model_copy(update={"read_closure": index_ref})
        )
    with pytest.raises((TypeError, ValueError)):
        reload_native_terminal(
            artifact_store=store,
            admission_ref=ref,
            child_authority=c.f.authority,
            expected_skill_ref=c.admission.skill_ref,
            expected_run_id=c.run_id,
            qualification_admission=c.q,
        )


def test_formal_four_m1_native_terminals_deep_admit_once(restored_apps):  # noqa: F811
    """One read per exact M1 registered terminal; never calls Genesis for a missing run."""
    import json
    import os
    import time
    from pathlib import Path
    from uuid import UUID

    from self_improving.harness.native_terminal_admission import (
        admit_native_terminal,
        reload_native_terminal,
    )
    from self_improving.harness.schemas.common import ArtifactRef, SkillQualification
    from self_improving.harness.schemas.typed_qualification_admission import (
        QualificationImplementationMemberV1,
        TypedQualificationAdmissionV1,
    )
    from self_improving.harness.typed_qualification_admission import (
        freeze_typed_qualification_admission,
        verify_typed_qualification_admission,
    )

    bindings, store, root, _ = restored_apps
    output = Path(os.environ["NATIVE_TERMINAL_ADMISSION_SUMMARY"])

    def publish(name, value, schema, media="application/json"):
        payload = (
            value
            if isinstance(value, bytes)
            else json.dumps(
                value.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        )
        path = root / name
        path.write_bytes(payload)
        return store.put_file(path, name=name, media_type=media, schema_version=schema)

    runs = (
        "a42a3a83-98ea-4c01-a35f-3003a9109c49",
        "bfc30903-0a69-478b-94ed-3ad2c9bbea03",
        "90af3aa7-6d77-4c5e-87d8-185b3bea36ea",
        "510c3c7d-c020-4623-b2bc-67e0dd045212",
    )
    rows = []
    prior = None
    for binding, run in zip(bindings, runs, strict=True):
        app = binding.application.application
        resume = os.environ.get("NATIVE_TERMINAL_ADMISSION_RESUME")
        if resume and binding.skill_ref == "image2env.compile@2.0.0":
            original = json.loads(Path(resume).read_bytes())["rows"][0]
            started = time.monotonic()
            q = verify_typed_qualification_admission(
                artifact_store=store,
                admission=TypedQualificationAdmissionV1.model_validate_json(
                    store.resolve(
                        ArtifactRef.model_validate(original["qualification"])
                    ).path.read_bytes()
                ),
            )
            recovered = reload_native_terminal(
                artifact_store=store,
                admission_ref=ArtifactRef.model_validate(original["admission"]),
                child_authority=binding.child_authority,
                expected_skill_ref=binding.skill_ref,
                expected_run_id=UUID(run),
                qualification_admission=q,
            )
            assert recovered.live_authority is False
            rows.append(
                dict(
                    original,
                    resumed_via="strict_cas_and_child_sqlite_reload",
                    reload_seconds=time.monotonic() - started,
                    reload_cas_root=str(store.root),
                    reload_child_sqlite=str(binding.child_authority.path),
                )
            )
            output.write_text(
                json.dumps(dict(rows=rows, complete=False), sort_keys=True, separators=(",", ":"))
            )
            continue
        descriptor = app.skills[0]
        descriptor_ref = publish(
            "descriptor.json", descriptor, descriptor.model_config["json_schema_extra"]["$id"]
        )
        qref = descriptor.qualification_artifact
        qualification = SkillQualification.model_validate_json(
            store.resolve(qref).path.read_bytes()
        )
        raw = store.resolve_digest(qualification.report_sha256).read_bytes()
        report = json.loads(raw)
        report_ref = ArtifactRef(
            name="native-report.json",
            uri="artifact://sha256/" + qualification.report_sha256,
            sha256=qualification.report_sha256,
            bytes=len(raw),
            media_type="application/json",
            schema_version=report["schema_version"],
        )
        if binding.skill_ref == "image2env.validate@2.0.0":
            import self_improving.harness.genesis_scene_validation_application as module

            names = (
                "genesis_scene_assessment.py",
                "genesis_scene_validation_application.py",
                "schemas/genesis_scene_assessment.py",
                "registry.py",
                "run_store.py",
                "event_journal.py",
                "artifacts.py",
            )
            implementation = tuple(
                QualificationImplementationMemberV1(
                    logical_path=name,
                    artifact=publish(
                        name.replace("/", "__"),
                        (Path(module.__file__).parent / name).read_bytes(),
                        None,
                        "text/x-python",
                    ),
                )
                for name in names
            )
        else:
            source_refs = report.get("implementation_sources", report.get("implementation"))
            implementation = tuple(
                QualificationImplementationMemberV1(
                    # Artifact names are already portable logical identities;
                    # RECORD's encoded parent segments must not become paths.
                    logical_path=ref["name"],
                    artifact=ArtifactRef.model_validate(ref),
                )
                for ref in source_refs
            )
        q = freeze_typed_qualification_admission(
            artifact_store=store,
            child_authority=binding.child_authority,
            descriptor_ref=descriptor_ref,
            qualification_ref=qref,
            qualification_report_ref=report_ref,
            implementation_members=implementation,
        )
        started = time.monotonic()
        capability = admit_native_terminal(
            application=app,
            artifact_store=store,
            run_id=UUID(run),
            child_authority=binding.child_authority,
            qualification_admission=q,
            predecessor_terminal_admission=prior
            if binding.skill_ref == "genesis.observe@3.0.0"
            else None,
        )
        elapsed = time.monotonic() - started
        index = json.loads(store.resolve(capability.admission.read_closure).path.read_bytes())
        rows.append(
            dict(
                skill=binding.skill_ref,
                run_id=run,
                seconds=elapsed,
                admission=capability.admission_ref.model_dump(mode="json"),
                qualification=q.admission_ref.model_dump(mode="json"),
                artifact_members=len(index["artifacts"]),
                digest_members=len(index["digest_members"]),
                child_sqlite=str(binding.child_authority.path),
                cas_root=str(store.root),
            )
        )
        output.write_text(
            json.dumps(dict(rows=rows, complete=False), sort_keys=True, separators=(",", ":"))
        )
        if binding.skill_ref == "image2env.replay@2.0.0":
            prior = capability
    output.write_text(
        json.dumps(dict(rows=rows, complete=True), sort_keys=True, separators=(",", ":"))
    )
    assert len(rows) == 4


def test_terminal_admission_rejects_arbitrary_application():
    from self_improving.harness.native_terminal_admission import admit_native_terminal

    with pytest.raises(TypeError, match="exact"):
        admit_native_terminal(
            application=object(),
            artifact_store=object(),
            run_id=None,
            qualification_admission=None,
            child_authority=object(),
        )


def test_formal_relocated_replay_and_fresh_admission_reload(tmp_path):
    """Real historical captures reload without applications or Genesis/source roots."""
    import json
    import os
    import shutil
    import sqlite3
    from contextlib import closing
    from pathlib import Path
    from uuid import UUID

    from self_improving.harness.native_terminal_admission import reload_native_terminal
    from self_improving.harness.run_store import SQLiteRunStore
    from self_improving.harness.schemas.common import ArtifactRef
    from self_improving.harness.schemas.typed_qualification_admission import (
        TypedQualificationAdmissionV1,
    )
    from self_improving.harness.typed_qualification_admission import (
        verify_typed_qualification_admission,
    )

    configured = os.environ.get("NATIVE_TERMINAL_RELOAD_SUMMARY")
    if not configured:
        pytest.skip("requires completed real terminal admission summary")
    summary = json.loads(Path(configured).read_bytes())
    assert summary["complete"]
    replay_row, fresh_row = summary["rows"][1:3]
    shutil.copytree(replay_row["cas_root"], tmp_path / "cas", copy_function=os.link)
    store = LocalArtifactStore(tmp_path / "cas")
    prior = None
    for row in (replay_row, fresh_row):
        target = tmp_path / (row["run_id"] + ".sqlite")
        with (
            closing(sqlite3.connect(row["child_sqlite"])) as source,
            closing(sqlite3.connect(target)) as dest,
        ):
            source.backup(dest)
        child = SQLiteRunStore(target)
        qualification = verify_typed_qualification_admission(
            artifact_store=store,
            admission=TypedQualificationAdmissionV1.model_validate_json(
                store.resolve(ArtifactRef.model_validate(row["qualification"])).path.read_bytes()
            ),
        )
        arguments = dict(
            artifact_store=store,
            admission_ref=ArtifactRef.model_validate(row["admission"]),
            child_authority=child,
            expected_skill_ref=row["skill"],
            expected_run_id=UUID(row["run_id"]),
            qualification_admission=qualification,
        )
        if prior is not None:
            with pytest.raises(TypeError, match="sealed replay predecessor"):
                reload_native_terminal(**arguments)
            raw = json.loads(store.resolve(arguments["admission_ref"]).path.read_bytes())
            raw["predecessor_terminal_admission"]["name"] = "wrong-replay-admission.json"
            path = tmp_path / "wrong-admission.json"
            path.write_text(json.dumps(raw, sort_keys=True, separators=(",", ":")))
            changed = store.put_file(
                path,
                name=path.name,
                media_type="application/json",
                schema_version=raw["schema_version"],
            )
            with pytest.raises(ValueError, match="different replay predecessor"):
                reload_native_terminal(
                    **dict(arguments, admission_ref=changed),
                    predecessor_terminal_admission=prior,
                )
        prior = reload_native_terminal(**arguments, predecessor_terminal_admission=prior)
        assert prior.live_authority is False
