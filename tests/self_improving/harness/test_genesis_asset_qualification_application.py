"""Durable asset qualification through public application operations."""

import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from uuid import uuid1, uuid4

import pytest

from self_improving.harness.genesis_asset_qualification_application import (
    GenesisAssetQualificationApplication,
    GenesisAssetQualificationApplicationError,
)
from tests.self_improving.harness.test_genesis_runtime_execution import (
    execution_request,  # noqa: F401
)


def test_reserved_intent_survives_restart_without_executing(execution_request, tmp_path):  # noqa: F811
    store, roots, _, _, request, request_ref = execution_request
    state_path = tmp_path / "qualification.sqlite"
    app = GenesisAssetQualificationApplication(state_path, store, request.manifest)
    run_id = uuid4()
    assert app.read(run_id) is None
    reserved = app.reserve(request_ref, run_id=run_id)
    assert reserved.status == "pending"
    restarted = GenesisAssetQualificationApplication(state_path, store, request.manifest)
    assert restarted.read(run_id) == reserved
    before = set(store.root.rglob("*"))
    with pytest.raises(GenesisAssetQualificationApplicationError, match="pending"):
        restarted.invoke(request_ref, run_id=run_id, roots=roots)
    assert set(store.root.rglob("*")) == before


def test_real_launcher_preflight_failure_is_terminal_and_reloaded(execution_request, tmp_path):  # noqa: F811
    store, roots, _, _, request, request_ref = execution_request
    state_path = tmp_path / "qualification.sqlite"
    app = GenesisAssetQualificationApplication(state_path, store, request.manifest)
    run_id = uuid4()
    with pytest.raises(GenesisAssetQualificationApplicationError, match="failed"):
        app.invoke(request_ref, run_id=run_id, roots=roots)
    failed = app.read(run_id)
    assert failed.status == "failed"
    assert failed.failure_ref is not None
    restarted = GenesisAssetQualificationApplication(state_path, store, request.manifest)
    before = set(store.root.rglob("*"))
    with pytest.raises(GenesisAssetQualificationApplicationError, match="failed"):
        restarted.invoke(request_ref, run_id=run_id, roots=roots)
    assert restarted.read(run_id) == failed
    with pytest.raises(GenesisAssetQualificationApplicationError, match="failed"):
        restarted.execute(request_ref, run_id=run_id, roots=roots)
    assert set(store.root.rglob("*")) == before


def test_row_cannot_be_rebound_to_another_uuid(execution_request, tmp_path):  # noqa: F811
    store, _, _, _, request, request_ref = execution_request
    path = tmp_path / "qualification.sqlite"
    app = GenesisAssetQualificationApplication(path, store, request.manifest)
    first, second = uuid4(), uuid4()
    app.reserve(request_ref, run_id=first)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE runs SET run_id=? WHERE run_id=?", (str(second), str(first)))
    with pytest.raises(GenesisAssetQualificationApplicationError):
        app.read(second)


@pytest.mark.parametrize("change", ["delete", "digest", "schema", "database"])
def test_sqlite_corruption_fails_closed(execution_request, tmp_path, change):  # noqa: F811
    store, _, _, _, request, request_ref = execution_request
    path = tmp_path / "qualification.sqlite"
    app = GenesisAssetQualificationApplication(path, store, request.manifest)
    run_id = uuid4()
    app.reserve(request_ref, run_id=run_id)
    if change == "database":
        path.write_bytes(b"not sqlite")
    else:
        with closing(sqlite3.connect(path)) as connection, connection:
            if change == "delete":
                connection.execute("DELETE FROM runs")
            elif change == "digest":
                connection.execute("UPDATE runs SET record='{}'")
            else:
                connection.execute("DROP TABLE runs")
    with pytest.raises(GenesisAssetQualificationApplicationError):
        app.read(run_id)


def test_uuid_and_input_are_immutable(execution_request, tmp_path):  # noqa: F811
    store, _, _, _, request, request_ref = execution_request
    path = tmp_path / "qualification.sqlite"
    app = GenesisAssetQualificationApplication(path, store, request.manifest)
    with pytest.raises(GenesisAssetQualificationApplicationError, match="UUID4"):
        app.reserve(request_ref, run_id=uuid1())
    run_id = uuid4()
    reserved = app.reserve(request_ref, run_id=run_id)
    assert app.reserve(request_ref, run_id=run_id) == reserved
    with pytest.raises(GenesisAssetQualificationApplicationError, match="another"):
        app.reserve(request_ref.model_copy(update={"name": "another-name.json"}), run_id=run_id)
    with pytest.raises(GenesisAssetQualificationApplicationError, match="configuration"):
        GenesisAssetQualificationApplication(
            path, store, request.manifest.model_copy(update={"bytes": 0})
        )


def test_cas_failure_record_corruption_is_rejected_on_restart(execution_request, tmp_path):  # noqa: F811
    store, roots, _, _, request, request_ref = execution_request
    path = tmp_path / "qualification.sqlite"
    app = GenesisAssetQualificationApplication(path, store, request.manifest)
    run_id = uuid4()
    with pytest.raises(GenesisAssetQualificationApplicationError):
        app.invoke(request_ref, run_id=run_id, roots=roots)
    failure = app.read(run_id).failure_ref
    store.resolve(failure).path.write_bytes(b"changed diagnostic")
    restarted = GenesisAssetQualificationApplication(path, store, request.manifest)
    with pytest.raises(ValueError):
        restarted.read(run_id)


@pytest.mark.parametrize("change", ["status", "missing_artifact"])
def test_well_formed_failure_evidence_must_still_close(execution_request, tmp_path, change):  # noqa: F811
    store, roots, _, _, request, request_ref = execution_request
    path = tmp_path / "state.sqlite"
    app = GenesisAssetQualificationApplication(path, store, request.manifest)
    run_id = uuid4()
    with pytest.raises(GenesisAssetQualificationApplicationError):
        app.invoke(request_ref, run_id=run_id, roots=roots)
    failure = app.read(run_id).failure_ref
    payload = json.loads(store.resolve(failure).path.read_bytes())
    if change == "status":
        payload["status"] = "pass"
    else:
        missing = request_ref.model_copy(
            update={"sha256": "f" * 64, "uri": "artifact://sha256/" + "f" * 64}
        )
        payload["failure_artifacts"] = [missing.model_dump(mode="json")]
    altered = tmp_path / "altered-failure.json"
    altered.write_text(json.dumps(payload))
    reference = store.put_file(
        altered,
        name=failure.name,
        media_type=failure.media_type,
        schema_version=failure.schema_version,
    )
    with closing(sqlite3.connect(path)) as connection, connection:
        row = json.loads(connection.execute("SELECT record FROM runs").fetchone()[0])
        row["failure_ref"] = reference.model_dump(mode="json")
        record = json.dumps(row, sort_keys=True, separators=(",", ":"))
        connection.execute(
            "UPDATE runs SET record=?,sha256=?",
            (record, hashlib.sha256(record.encode()).hexdigest()),
        )
    with pytest.raises(ValueError):
        app.read(run_id)


def test_operator_configuration_is_read_only(execution_request, tmp_path):  # noqa: F811
    store, _, _, _, request, _ = execution_request
    app = GenesisAssetQualificationApplication(tmp_path / "state.sqlite", store, request.manifest)
    with pytest.raises(AttributeError):
        app.approved_manifest_ref = request.manifest.model_copy(update={"bytes": 0})


def test_execute_does_not_consume_reserved_intent(execution_request, tmp_path):  # noqa: F811
    store, roots, _, _, request, request_ref = execution_request
    app = GenesisAssetQualificationApplication(tmp_path / "state.sqlite", store, request.manifest)
    run_id = uuid4()
    pending = app.reserve(request_ref, run_id=run_id)
    with pytest.raises(GenesisAssetQualificationApplicationError, match="pending"):
        app.execute(request_ref, run_id=run_id, roots=roots)
    assert app.read(run_id) == pending


def test_concurrent_callers_share_one_failed_execution(execution_request, tmp_path):  # noqa: F811
    store, roots, _, _, request, request_ref = execution_request
    app = GenesisAssetQualificationApplication(tmp_path / "state.sqlite", store, request.manifest)
    run_id = uuid4()
    barrier = Barrier(2)

    def invoke():
        barrier.wait(timeout=10)
        try:
            app.invoke(request_ref, run_id=run_id, roots=roots)
        except GenesisAssetQualificationApplicationError as error:
            return str(error)
        pytest.fail("fixture runtime must fail actual launcher preflight")

    with ThreadPoolExecutor(max_workers=2) as workers:
        errors = list(workers.map(lambda _: invoke(), range(2)))
    assert errors.count("execution failed; terminal diagnostic retained") == 1
    assert sum("intent cannot execute again" in error for error in errors) == 1
    assert app.read(run_id).status == "failed"


@pytest.mark.parametrize("change", ["status", "missing_input", "noncanonical"])
def test_rehashed_invalid_state_is_still_rejected(execution_request, tmp_path, change):  # noqa: F811
    store, _, _, _, request, request_ref = execution_request
    path = tmp_path / "state.sqlite"
    app = GenesisAssetQualificationApplication(path, store, request.manifest)
    run_id = uuid4()
    app.reserve(request_ref, run_id=run_id)
    with closing(sqlite3.connect(path)) as connection, connection:
        row = json.loads(connection.execute("SELECT record FROM runs").fetchone()[0])
        if change == "status":
            row["status"] = "unknown"
        elif change == "missing_input":
            row["request_ref"] = None
        document = json.dumps(row, sort_keys=True, separators=(",", ":"))
        if change == "noncanonical":
            document += "\n"
        connection.execute(
            "UPDATE runs SET record=?,sha256=?",
            (document, hashlib.sha256(document.encode()).hexdigest()),
        )
    with pytest.raises(GenesisAssetQualificationApplicationError):
        app.read(run_id)


def test_issuer_cas_bytes_are_rechecked(execution_request, tmp_path):  # noqa: F811
    store, _, _, _, request, request_ref = execution_request
    app = GenesisAssetQualificationApplication(tmp_path / "state.sqlite", store, request.manifest)
    run_id = uuid4()
    app.reserve(request_ref, run_id=run_id)
    store.resolve_digest(app.issuer_sha256).write_bytes(b"changed issuer source")
    with pytest.raises(ValueError):
        app.read(run_id)


def test_unapproved_runtime_cannot_reserve(execution_request, tmp_path):  # noqa: F811
    store, _, _, _, request, request_ref = execution_request
    app = GenesisAssetQualificationApplication(
        tmp_path / "state.sqlite", store, request.manifest.model_copy(update={"bytes": 0})
    )
    run_id = uuid4()
    with pytest.raises(GenesisAssetQualificationApplicationError, match="approved"):
        app.reserve(request_ref, run_id=run_id)
    assert app.read(run_id) is None


@pytest.mark.skipif(
    not os.environ.get("GOLDEN_RUNTIME_EXECUTION_CONFIG"),
    reason="requires approved real Genesis execution",
)
@pytest.mark.parametrize("explicit_execution", [False, True], ids=["live", "executed_restart"])
def test_formal_asset_qualification_executes_once_and_reloads_without_live_application(
    explicit_execution,
):
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.schemas.common import ArtifactRef
    from self_improving.harness.schemas.x2env_compile_evidence import GenesisAssetQualificationV1

    config_path = Path(os.environ["GOLDEN_RUNTIME_EXECUTION_CONFIG"])
    config = json.loads(config_path.read_text())
    store = LocalArtifactStore(Path(config["cas_root"]))
    state_path = Path(
        os.environ.get(
            "GOLDEN_QUALIFICATION_STATE_PATH", str(config_path.parent / "qualification.sqlite")
        )
    )
    manifest = ArtifactRef.model_validate(config["approved_manifest_ref"])
    request = ArtifactRef.model_validate(config["request_ref"])
    roots = {key: Path(value) for key, value in config["roots"].items()}
    app = GenesisAssetQualificationApplication(state_path, store, manifest)
    run_id = uuid4()
    (config_path.parent / "qualification-attempt.json").write_text(
        json.dumps(
            {
                "run_id": str(run_id),
                "state_path": str(state_path),
                "issuer_sha256": app.issuer_sha256,
            },
            indent=2,
        )
        + "\n"
    )
    if explicit_execution:
        executed = app.execute(request, run_id=run_id, roots=roots)
        assert executed.status == "executed"
        assert executed.qualification_ref is None
        app = GenesisAssetQualificationApplication(state_path, store, manifest)
        assert app.execute(request, run_id=run_id, roots={}) == executed
        qualification_ref = app.invoke(request, run_id=run_id, roots={})
    else:
        qualification_ref = app.invoke(request, run_id=run_id, roots=roots)
    qualification = GenesisAssetQualificationV1.model_validate_json(
        store.resolve(qualification_ref).path.read_bytes()
    )
    assert tuple(check.code for check in qualification.checks) == (
        "backend_load",
        "collision_probe",
    )
    assert qualification.qualified is True
    restarted = GenesisAssetQualificationApplication(state_path, store, manifest)
    assert restarted.read(run_id).qualification_ref == qualification_ref
    assert restarted.invoke(request, run_id=run_id, roots={}) == qualification_ref
    summary_name = (
        "qualification-summary-executed.json"
        if explicit_execution
        else "qualification-summary.json"
    )
    (config_path.parent / summary_name).write_text(
        json.dumps(
            {
                "run_id": str(run_id),
                "state_path": str(state_path),
                "qualification_ref": qualification_ref.model_dump(mode="json"),
                "issuer_sha256": app.issuer_sha256,
            },
            indent=2,
        )
        + "\n"
    )
