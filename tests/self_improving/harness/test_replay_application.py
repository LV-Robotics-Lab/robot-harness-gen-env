from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
import subprocess
import sys
import threading
from contextlib import ExitStack
from dataclasses import replace
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid1, uuid4

import pytest

import self_improving.harness.replay_application as module
from self_improving.harness.handlers.text2env_replay import text2env_replay_descriptor
from self_improving.harness.package_store import PackageStore
from self_improving.harness.qualification import QualificationBundleError
from self_improving.harness.registry import (
    ControlledRunPendingError,
    RegistryRegistrationError,
)
from self_improving.harness.replay_application import (
    ReplayApplication,
    ReplayApplicationConfigurationError,
    ReplayApplicationInputError,
    ReplayApplicationRunIdentityError,
    ReplayApplicationRunPendingError,
    ReplayApplicationSettings,
    create_replay_application,
)
from self_improving.harness.schemas import (
    ArtifactRef,
    DependencyRef,
    EnvironmentPackage,
    Invocation,
    RuntimeConfig,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)
from tests.self_improving.harness import test_text2env_replay_handler as replay_fixture

ROOT = Path(__file__).resolve().parents[3]
REPLAY_BUNDLE = ROOT / "self_improving/harness/qualified_skills/text2env.replay/1.0.0"


def _ref(name: str, sha256: str, schema_version: str) -> ArtifactRef:
    return ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{sha256}",
        media_type="application/json",
        sha256=sha256,
        bytes=1,
        schema_version=schema_version,
    )


def _dependencies() -> tuple[DependencyRef, ...]:
    return tuple(
        DependencyRef(name=name, version="1", sha256=character * 64)
        for name, character in (
            ("text2env.replay.capability", "1"),
            ("text2env.replay.executor", "2"),
            ("text2env.replay.handler_config", "3"),
            ("text2env.replay.media_verifier", "4"),
            ("text2env.replay.runtime_assets", "5"),
        )
    )


def _loaded(dependencies: tuple[DependencyRef, ...]) -> SimpleNamespace:
    package = EnvironmentPackage(
        package_id="b" * 64,
        route_id="text2env",
        producer_skill_ref="text2env.compile@1.0.0",
        seed=7,
        scene_spec_sha256="a" * 64,
        resolved_scene_sha256="b" * 64,
        asset_catalog=_ref("catalog", "c" * 64, "robotwin.asset_catalog.v1"),
        package_manifest=_ref(
            "manifest",
            "d" * 64,
            "robotwin.generated_scene_package.v1",
        ),
    )
    replay_input = Text2EnvReplayInput(
        environment_package=package,
        runtime_config=RuntimeConfig(
            precheck_steps=0,
            settle_steps=900,
            contact_window_steps=120,
            video_frames=120,
            fps=12,
        ),
    )
    qualification = _ref(
        "qualification",
        "e" * 64,
        "harness.skill_qualification.v1",
    )
    descriptor = text2env_replay_descriptor(
        qualification_artifact=qualification,
        implementation_sha256="f" * 64,
    )
    invocation = Invocation(
        run_id=UUID("12345678-1234-4234-9234-123456789abc"),
        skill_id="text2env.replay",
        skill_version="1.0.0",
        effective_parameters=replay_input.model_dump(mode="json"),
        dependencies=dependencies,
        max_attempts=2,
        invocation_digest="6" * 64,
    )
    return SimpleNamespace(
        descriptor=descriptor,
        exact_dependencies=SimpleNamespace(dependencies=dependencies),
        kernel_evaluation=SimpleNamespace(invocation=invocation),
        executions=SimpleNamespace(
            evidence_closure_manifest=_ref(
                "closure",
                "7" * 64,
                "harness.replay_qualification.evidence_closure.v1",
            )
        ),
    )


def _factory_settings(
    root: Path,
    *,
    evidence_root: Path,
    allowed_asset_root: Path,
) -> ReplayApplicationSettings:
    operator = root / "operator"
    operator.mkdir(exist_ok=True)
    files: dict[str, Path] = {}
    for name in ("interpreter", "runner", "capability", "launcher", "ffmpeg"):
        path = operator / name
        path.write_text(name, encoding="utf-8")
        files[name] = path
    module_root = operator / "module"
    delegated_root = operator / "delegated"
    module_root.mkdir(exist_ok=True)
    delegated_root.mkdir(exist_ok=True)
    return ReplayApplicationSettings(
        state_root=root / "state",
        qualification_bundle_root=REPLAY_BUNDLE,
        evidence_artifact_root=evidence_root,
        implementation_root=module._DISTRIBUTION_ROOT,
        scene_gen_root=module._SCENE_GEN_ROOT,
        ledger_contract_root=module._LEDGER_CONTRACT_ROOT,
        allowed_asset_roots=(allowed_asset_root,),
        interpreter=files["interpreter"],
        runtime_runner=files["runner"],
        runtime_module_root=module_root,
        runtime_capability_path=files["capability"],
        media_launcher=files["launcher"],
        static_ffmpeg=files["ffmpeg"],
        delegated_cgroup_root=delegated_root,
        runtime_timeout_seconds=10.0,
        capability_timeout_seconds=2.0,
    )


def _factory_boundary_patches(
    *,
    store: module.LocalArtifactStore,
    robotwin_root: Path,
    runtime_root: Path,
) -> tuple[ExitStack, list[replay_fixture.RecordingExecutor]]:
    qualification_bytes = (REPLAY_BUNDLE / "qualification.json").read_bytes()
    report_bytes = (REPLAY_BUNDLE / "report.json").read_bytes()
    qualification_ref = store.put_file(
        REPLAY_BUNDLE / "qualification.json",
        name="qualification",
        media_type="application/json",
        schema_version="harness.skill_qualification.v1",
    )
    manifest = json.loads((REPLAY_BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    descriptor = text2env_replay_descriptor(
        qualification_artifact=qualification_ref,
        implementation_sha256=manifest["bundle_sha256"],
    )
    inspected = SimpleNamespace(
        descriptor=descriptor,
        generic=SimpleNamespace(
            qualification_bytes=qualification_bytes,
            report_bytes=report_bytes,
        ),
    )
    executors: list[replay_fixture.RecordingExecutor] = []

    def assemble(*, settings, state_root, artifact_store, allowed_roots):
        del settings
        assert artifact_store.root == store.root
        assert allowed_roots == (robotwin_root.resolve(),)
        executor = replay_fixture.RecordingExecutor(
            runtime_root / f"executor-{len(executors)}",
            replay_fixture._capability_document(),
        )
        executors.append(executor)
        return replay_fixture.build_text2env_replay_wiring(
            artifact_store=artifact_store,
            package_store=PackageStore(artifact_store),
            runtime_executor=executor,
            media_verifier=replay_fixture.RecordingMediaVerifier(),
            work_root=state_root / "replay-handler",
            dependency_work_root=state_root / "replay-dependencies",
            allowed_asset_roots=allowed_roots,
            expected_capability_sha256=executor.capability_sha256,
            checkpoint_steps=120,
        )

    stack = ExitStack()
    stack.enter_context(
        patch.object(module, "verify_replay_qualification_bundle", return_value=inspected)
    )
    stack.enter_context(patch.object(module, "_verify_settings_match_evidence"))
    stack.enter_context(patch.object(module, "_assemble_live_wiring", side_effect=assemble))
    stack.enter_context(patch.object(module, "_verify_live_wiring"))
    stack.enter_context(patch.object(module, "publish_replay_qualification_bundle"))
    return stack, executors


def _controlled_factory_fixture(root: Path):
    source = replay_fixture._fixture(root / "source")
    value = source.value.model_copy(
        update={
            "runtime_config": source.value.runtime_config.model_copy(update={"settle_steps": 120})
        }
    )
    settings = _factory_settings(
        root,
        evidence_root=source.store.root,
        allowed_asset_root=source.robotwin_root,
    )
    stack, executors = _factory_boundary_patches(
        store=source.store,
        robotwin_root=source.robotwin_root,
        runtime_root=root / "runtime-test-adapter",
    )
    return source, value, settings, stack, executors


def _child_recover(root_value: str, run_id_value: str) -> None:
    root = Path(root_value)
    store = module.LocalArtifactStore(root / "source/cas")
    value = Text2EnvReplayInput.model_validate_json(
        (root / "replay-input.json").read_text(encoding="utf-8")
    )
    settings = _factory_settings(
        root,
        evidence_root=store.root,
        allowed_asset_root=root / "source/RoboTwin",
    )
    stack, executors = _factory_boundary_patches(
        store=store,
        robotwin_root=root / "source/RoboTwin",
        runtime_root=root / "child-runtime-test-adapter",
    )
    with stack:
        application = create_replay_application(settings)
        state = application.invoke_typed(value, run_id=UUID(run_id_value))
    print(
        json.dumps(
            {
                "run_id": str(state.run_id),
                "status": state.status.value,
                "executor_calls": len(executors[0].calls),
            },
            sort_keys=True,
        )
    )


def test_public_factory_exposes_no_handler_descriptor_or_qualification_injection() -> None:
    fields = set(ReplayApplicationSettings.__dataclass_fields__)
    assert not ({"handler", "descriptor", "qualification", "registration"} & fields)
    assert tuple(inspect.signature(create_replay_application).parameters) == ("settings",)
    with pytest.raises(TypeError, match="unexpected keyword"):
        create_replay_application(object(), handler=lambda *_args: None)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="ReplayApplicationSettings"):
        create_replay_application(object())  # type: ignore[arg-type]

    assert not hasattr(module, "_issue_verified_registration")
    assert not hasattr(module, "VerifiedReplayQualification")


def test_public_constructor_cannot_issue_factory_authority(
    tmp_path: Path,
) -> None:
    dependencies = _dependencies()
    loaded = _loaded(dependencies)
    replay_input = Text2EnvReplayInput.model_validate(
        loaded.kernel_evaluation.invocation.effective_parameters
    )

    class Registry:
        descriptors = (loaded.descriptor,)

        def list(self):
            return self.descriptors

    class Journal:
        path = tmp_path / "events.sqlite3"

        def read(self, **_kwargs):
            return "page"

    class Runs:
        def read_invocation(self, run_id):
            return ("invocation", run_id)

        def read_run_state(self, run_id):
            return ("state", run_id)

    application = ReplayApplication(
        artifact_store=SimpleNamespace(root=tmp_path / "cas"),  # type: ignore[arg-type]
        event_journal=Journal(),  # type: ignore[arg-type]
        run_store=Runs(),  # type: ignore[arg-type]
        registry=Registry(),  # type: ignore[arg-type]
    )

    with pytest.raises(ReplayApplicationConfigurationError, match="factory-issued"):
        application._assert_golden_execution_ready()
    with pytest.raises(ReplayApplicationConfigurationError, match="production factory"):
        ReplayApplication._from_verified_assembly(
            authority=object(),
            settings=object(),  # type: ignore[arg-type]
            state_root=tmp_path,
            artifact_store=object(),  # type: ignore[arg-type]
            event_journal=object(),  # type: ignore[arg-type]
            run_store=object(),  # type: ignore[arg-type]
            registry=object(),  # type: ignore[arg-type]
            registration_policy=object(),  # type: ignore[arg-type]
        )
    for operation in (
        lambda: application.skills,
        lambda: application.artifact_root,
        lambda: application.journal_path,
        lambda: application.replay(replay_input),
        lambda: application.events(),
        lambda: application.invocation(loaded.kernel_evaluation.invocation.run_id),
        lambda: application.run_state(loaded.kernel_evaluation.invocation.run_id),
    ):
        with pytest.raises(ReplayApplicationConfigurationError, match="factory-issued"):
            operation()


def test_factory_issues_exact_controlled_authority_and_recovers_without_repeat(
    tmp_path: Path,
) -> None:
    source, value, settings, stack, executors = _controlled_factory_fixture(tmp_path)
    run_id = UUID("12345678-1234-4234-9234-123456789abc")

    with stack:
        application = create_replay_application(settings)
        application._assert_golden_execution_ready()
        assert type(application._artifact_store) is module.LocalArtifactStore
        assert type(application._event_journal) is module.SQLiteEventJournal
        assert type(application._run_store) is module.SQLiteRunStore
        assert type(application._registry) is module.SkillRegistry
        assert application._event_journal.path == application._run_store.path
        assert application._registry._artifact_resolver is application._artifact_store
        assert application._registry._event_sink is application._event_journal
        assert application._registry._run_store is application._run_store
        assert application._registry._dependency_resolver is (
            application._registry._registrations[
                ("text2env.replay", "1.0.0")
            ].handler.dependency_resolver
        )

        first = application.invoke_typed(value, run_id=run_id)
        recovered = application.invoke_typed(value, run_id=run_id)
        reopened = create_replay_application(settings)
        recovered_after_reopen = reopened.invoke_typed(value, run_id=run_id)

        assert first == recovered == recovered_after_reopen
        assert first.status.value == "succeeded"
        lifecycle = reopened._run_store.read_controlled_lifecycle(run_id)
        assert lifecycle.intent is not None
        assert lifecycle.invocation is not None
        assert lifecycle.terminal == first
        output = Text2EnvReplayOutput.model_validate(first.output)
        assert application.resolve_artifact(output.runtime_evidence).is_file()
        assert application.journal_path == settings.state_root / "harness.sqlite3"
        assert application.events(run_id=run_id).events
        assert application.invocation(run_id) == lifecycle.invocation
        assert application.run_state(run_id) == first
        assert [len(executor.calls) for executor in executors] == [1, 0]
        assert source.store.root == application.artifact_root


def test_controlled_boundary_rejects_invalid_or_changed_identity_and_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, value, settings, stack, executors = _controlled_factory_fixture(tmp_path)
    run_id = uuid4()

    with stack:
        application = create_replay_application(settings)
        with pytest.raises(ReplayApplicationRunIdentityError, match="UUID4"):
            application.invoke_typed(value, run_id=uuid1())
        with pytest.raises(ReplayApplicationInputError, match="Text2EnvReplayInput"):
            application.invoke_typed({})  # type: ignore[arg-type]

        ordinary = application.invoke_typed(value)
        with pytest.raises(ReplayApplicationRunIdentityError, match="ordinary run"):
            application.invoke_typed(value, run_id=ordinary.run_id)

        first = application.invoke_typed(value, run_id=run_id)
        changed = value.model_copy(
            update={"runtime_config": value.runtime_config.model_copy(update={"fps": 13})}
        )
        with pytest.raises(ReplayApplicationRunIdentityError, match="another intent"):
            application.invoke_typed(changed, run_id=run_id)

        selected = source.selected_files[0]
        original = selected.read_bytes()
        selected.write_bytes(b"changed-after-terminal")
        with pytest.raises(ReplayApplicationRunIdentityError, match="dependencies"):
            application.invoke_typed(value, run_id=run_id)
        selected.write_bytes(original)

        registration = application._registry._registrations[("text2env.replay", "1.0.0")]
        object.__setattr__(registration.handler, "checkpoint_steps", 121)
        with pytest.raises(ReplayApplicationConfigurationError, match="configuration"):
            application.invoke_typed(value, run_id=uuid4())

        object.__setattr__(registration.handler, "checkpoint_steps", 120)
        with monkeypatch.context() as mapping:
            mapping.setattr(
                module.SkillRegistry,
                "invoke_with_run_id",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    ControlledRunPendingError("controlled run is pending")
                ),
            )
            with pytest.raises(ReplayApplicationRunPendingError, match="pending"):
                application.invoke_typed(value, run_id=uuid4())

        assert first.status.value == "succeeded"
        assert len(executors[0].calls) == 2


@pytest.mark.parametrize(
    "drift",
    [
        "state_root",
        "artifact_type",
        "artifact_root",
        "event_journal",
        "run_store",
        "database_path",
        "registry_type",
        "registry_authority",
        "policy",
        "registration",
        "descriptor",
        "handler",
        "resolver",
        "handler_resolver",
        "shared_cas",
        "mutable_roots",
        "dependency_identity",
        "assembly_identity",
        "listed_descriptor",
    ],
)
def test_application_rechecks_every_factory_authority_before_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    _source, _value, settings, stack, _executors = _controlled_factory_fixture(tmp_path)
    with stack:
        application = create_replay_application(settings)
        registration = application._registry._registrations[("text2env.replay", "1.0.0")]
        handler = registration.handler
        resolver = application._registry._dependency_resolver
        assert resolver is not None
        if drift == "state_root":
            application._state_root = tmp_path / "missing-state"
        elif drift == "artifact_type":
            application._artifact_store = object()  # type: ignore[assignment]
        elif drift == "artifact_root":
            application._artifact_store = module.LocalArtifactStore(tmp_path / "foreign-cas")
        elif drift == "event_journal":
            application._event_journal = object()  # type: ignore[assignment]
        elif drift == "run_store":
            application._run_store = object()  # type: ignore[assignment]
        elif drift == "database_path":
            application._event_journal.path = tmp_path / "foreign.sqlite3"
        elif drift == "registry_type":
            application._registry = object()  # type: ignore[assignment]
        elif drift == "registry_authority":
            application._registry._artifact_resolver = object()
        elif drift == "policy":
            object.__setattr__(application._registration_policy, "allowed_roots", (tmp_path,))
        elif drift == "registration":
            application._registry._registrations.clear()
        elif drift == "descriptor":
            object.__setattr__(
                registration,
                "descriptor",
                registration.descriptor.model_copy(update={"max_attempts": 3}),
            )
        elif drift == "handler":
            object.__setattr__(registration, "handler", object())
        elif drift == "resolver":
            application._registry._dependency_resolver = object()
        elif drift == "handler_resolver":
            object.__setattr__(handler, "dependency_resolver", object())
        elif drift == "shared_cas":
            object.__setattr__(resolver, "artifact_store", module.LocalArtifactStore(tmp_path))
        elif drift == "mutable_roots":
            object.__setattr__(handler, "work_root", tmp_path / "other-work")
        elif drift == "dependency_identity":
            object.__setattr__(resolver, "handler_config_sha256", "f" * 64)
        elif drift == "assembly_identity":
            application._assembly_identity = ()
        else:
            monkeypatch.setattr(module.SkillRegistry, "list", lambda _self: (object(),))

        with pytest.raises(
            (ReplayApplicationConfigurationError, RuntimeError),
            match="production assembly|another descriptor",
        ):
            _ = application.skills


def test_typed_boundary_rejects_corrupt_model_or_cas_reference(tmp_path: Path) -> None:
    _source, value, settings, stack, _executors = _controlled_factory_fixture(tmp_path)
    with stack:
        application = create_replay_application(settings)
        malformed = value.model_copy()
        object.__setattr__(malformed, "runtime_config", object())
        with pytest.warns(UserWarning, match="Pydantic serializer warnings"):
            with pytest.raises(ReplayApplicationInputError, match="typed replay input"):
                application.invoke_typed(malformed)

        foreign_ref = value.environment_package.asset_catalog.model_copy(
            update={"uri": f"file://{tmp_path}/foreign.json"}
        )
        foreign_package = value.environment_package.model_copy(
            update={"asset_catalog": foreign_ref}
        )
        with pytest.raises(ReplayApplicationInputError, match="application CAS"):
            application.invoke_typed(
                value.model_copy(update={"environment_package": foreign_package})
            )

        missing_ref = value.environment_package.package_manifest
        application._artifact_store.resolve(missing_ref).path.unlink()
        with pytest.raises(ReplayApplicationInputError, match="unavailable or corrupt"):
            application.invoke_typed(value)


def test_factory_rejects_corrupt_nonempty_state_without_clearing_it(tmp_path: Path) -> None:
    source = replay_fixture._fixture(tmp_path / "source")
    settings = _factory_settings(
        tmp_path,
        evidence_root=source.store.root,
        allowed_asset_root=source.robotwin_root,
    )
    settings.state_root.mkdir()
    marker = settings.state_root / "unowned"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(ReplayApplicationConfigurationError, match="unexpected entry"):
        create_replay_application(settings)

    assert marker.read_text(encoding="utf-8") == "preserve"
    assert tuple(settings.state_root.iterdir()) == (marker,)


@pytest.mark.parametrize("kind", ["symlink", "replay_file", "database_directory"])
def test_factory_rejects_unsafe_owned_state_entry(tmp_path: Path, kind: str) -> None:
    source = replay_fixture._fixture(tmp_path / "source")
    settings = _factory_settings(
        tmp_path,
        evidence_root=source.store.root,
        allowed_asset_root=source.robotwin_root,
    )
    settings.state_root.mkdir()
    if kind == "symlink":
        target = tmp_path / "outside"
        target.mkdir()
        (settings.state_root / "replay-handler").symlink_to(target, target_is_directory=True)
    elif kind == "replay_file":
        (settings.state_root / "replay-handler").write_text("wrong type", encoding="utf-8")
    else:
        (settings.state_root / "harness.sqlite3").mkdir()

    with pytest.raises(ReplayApplicationConfigurationError, match="unsafe|wrong type"):
        create_replay_application(settings)


def test_factory_rejects_non_path_state_root_and_rolls_back_only_new_entries(
    tmp_path: Path,
) -> None:
    source = replay_fixture._fixture(tmp_path / "source")
    settings = _factory_settings(
        tmp_path,
        evidence_root=source.store.root,
        allowed_asset_root=source.robotwin_root,
    )
    with pytest.raises(ReplayApplicationConfigurationError, match="state_root must be a Path"):
        create_replay_application(replace(settings, state_root="not-a-path"))  # type: ignore[arg-type]

    root = tmp_path / "rollback-new"
    root.mkdir()
    preserved = root / "harness.sqlite3"
    preserved.write_text("preserved", encoding="utf-8")
    created = root / "replay-handler"
    created.mkdir()
    (created / "partial").write_text("partial", encoding="utf-8")
    module._rollback_new_state_entries(
        root,
        preexisting_names=frozenset({preserved.name}),
        remove_root=False,
    )
    assert preserved.read_text(encoding="utf-8") == "preserved"
    assert tuple(root.iterdir()) == (preserved,)


def test_state_root_assembly_lock_rejects_invalid_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ReplayApplicationConfigurationError, match="named directory leaf"):
        with module._state_root_assembly_lock(Path("/")):
            pass

    with monkeypatch.context() as changed_parent:
        changed_parent.setattr(module, "_directory", lambda *_args, **_kwargs: tmp_path / "other")
        with pytest.raises(ReplayApplicationConfigurationError, match="parent is not canonical"):
            with module._state_root_assembly_lock(tmp_path / "state"):
                pass

    lock_path = tmp_path / ".state.replay-application.lock"
    lock_path.mkdir()
    with pytest.raises(ReplayApplicationConfigurationError, match="lock is unavailable"):
        with module._state_root_assembly_lock(tmp_path / "state"):
            pass
    lock_path.rmdir()

    lock_path.touch()
    second_link = tmp_path / "second-lock-link"
    module.os.link(lock_path, second_link)
    with pytest.raises(ReplayApplicationConfigurationError, match="not one regular file"):
        with module._state_root_assembly_lock(tmp_path / "state"):
            pass
    second_link.unlink()

    actual_stat = module.os.stat

    def changed_stat(path, *args, **kwargs):
        result = actual_stat(path, *args, **kwargs)
        if Path(path) == lock_path:
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_nlink=result.st_nlink,
                st_dev=result.st_dev,
                st_ino=result.st_ino + 1,
            )
        return result

    with monkeypatch.context() as changed_lock:
        changed_lock.setattr(module.os, "stat", changed_stat)
        with pytest.raises(ReplayApplicationConfigurationError, match="identity changed"):
            with module._state_root_assembly_lock(tmp_path / "state"):
                pass

    with monkeypatch.context() as without_nofollow:
        without_nofollow.delattr(module.os, "O_NOFOLLOW")
        with module._state_root_assembly_lock(tmp_path / "state"):
            pass


def test_controlled_terminal_recovers_in_a_fresh_process(tmp_path: Path) -> None:
    _source, value, settings, stack, executors = _controlled_factory_fixture(tmp_path)
    run_id = uuid4()
    (tmp_path / "replay-input.json").write_text(
        value.model_dump_json(),
        encoding="utf-8",
    )

    with stack:
        first = create_replay_application(settings).invoke_typed(value, run_id=run_id)
    assert first.status.value == "succeeded"
    assert len(executors[0].calls) == 1

    command = (
        "from tests.self_improving.harness.test_replay_application "
        "import _child_recover; "
        f"_child_recover({str(tmp_path)!r}, {str(run_id)!r})"
    )
    completed = subprocess.run(
        [sys.executable, "-c", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "executor_calls": 0,
        "run_id": str(run_id),
        "status": "succeeded",
    }


def test_concurrent_factory_failure_cannot_delete_successful_authority(tmp_path: Path) -> None:
    _source, _value, settings, stack, _executors = _controlled_factory_fixture(tmp_path)
    settings.state_root.mkdir()
    for name in ("replay-handler", "replay-dependencies"):
        (settings.state_root / name).mkdir()
    original_validate = module._validate_reopenable_state_root
    both_snapshotted = threading.Barrier(2)
    successful_factory_done = threading.Event()
    publication_lock = threading.Lock()
    successful_name: list[str] = []
    applications: dict[str, ReplayApplication] = {}
    errors: dict[str, Exception] = {}

    def synchronized_validate(root: Path) -> None:
        try:
            both_snapshotted.wait(timeout=0.25)
        except threading.BrokenBarrierError:
            pass
        original_validate(root)

    def publish(*_args, **_kwargs) -> None:
        with publication_lock:
            if not successful_name:
                successful_name.append(threading.current_thread().name)
                return
        assert successful_factory_done.wait(timeout=5)
        raise QualificationBundleError("deliberate_loser", "second factory publication failed")

    def assemble(name: str) -> None:
        try:
            applications[name] = create_replay_application(settings)
        except Exception as error:
            errors[name] = error
        finally:
            if successful_name and name == successful_name[0]:
                successful_factory_done.set()

    with stack:
        with patch.object(module, "_validate_reopenable_state_root", synchronized_validate):
            with patch.object(module, "publish_replay_qualification_bundle", publish):
                first = threading.Thread(target=assemble, args=("first",), name="first")
                second = threading.Thread(target=assemble, args=("second",), name="second")
                first.start()
                second.start()
                first.join(timeout=10)
                second.join(timeout=10)

    assert not first.is_alive() and not second.is_alive()
    assert tuple(applications) == (successful_name[0],)
    loser_name = next(name for name in ("first", "second") if name != successful_name[0])
    assert type(errors.get(loser_name)) is QualificationBundleError
    assert (settings.state_root / "harness.sqlite3").is_file()
    assert applications[successful_name[0]].skills[0].skill_id == "text2env.replay"


def test_corrupt_reopened_sqlite_is_one_application_configuration_error(tmp_path: Path) -> None:
    _source, _value, settings, stack, _executors = _controlled_factory_fixture(tmp_path)
    settings.state_root.mkdir()
    database = settings.state_root / "harness.sqlite3"
    database.write_bytes(b"not a sqlite database")

    with stack:
        with pytest.raises(ReplayApplicationConfigurationError, match="SQLite authority") as caught:
            create_replay_application(settings)

    assert isinstance(caught.value.__cause__, sqlite3.DatabaseError)
    assert database.read_bytes() == b"not a sqlite database"


def test_application_path_timeout_and_capability_helpers_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regular = tmp_path / "regular"
    regular.write_text("value", encoding="utf-8")
    directory = tmp_path / "directory"
    directory.mkdir()
    linked_file = tmp_path / "linked-file"
    linked_file.symlink_to(regular)
    linked_directory = tmp_path / "linked-directory"
    linked_directory.symlink_to(directory, target_is_directory=True)

    assert module._file(regular, label="file") == regular.resolve()
    assert module._directory(directory, label="directory") == directory.resolve()
    created = tmp_path / "created"
    assert module._directory(created, label="created", create=True) == created.resolve()
    assert module._directories((directory,), label="roots") == (directory.resolve(),)
    assert module._positive_timeout(1) == 1.0
    assert module._positive_timeout(0.5) == 0.5

    for value in ("not-path", tmp_path / "missing", directory, linked_file):
        with pytest.raises(ReplayApplicationConfigurationError):
            module._file(value, label="file")  # type: ignore[arg-type]
    for value in ("not-path", tmp_path / "missing-dir", regular, linked_directory):
        with pytest.raises(ReplayApplicationConfigurationError):
            module._directory(value, label="directory")  # type: ignore[arg-type]
    for values in ((), [directory], (directory, directory)):
        with pytest.raises(ReplayApplicationConfigurationError):
            module._directories(values, label="roots")  # type: ignore[arg-type]
    second = tmp_path / "a-directory"
    second.mkdir()
    with pytest.raises(ReplayApplicationConfigurationError, match="unique and sorted"):
        module._directories((directory, second), label="roots")
    for value in (True, "1", 0, -1, float("inf"), float("nan")):
        with pytest.raises(ReplayApplicationConfigurationError, match="positive and finite"):
            module._positive_timeout(value)  # type: ignore[arg-type]

    capability = tmp_path / "capability.json"
    capability.write_bytes(b"{}\n")
    monkeypatch.setattr(module, "validate_runtime_capability_document", lambda value: value)
    monkeypatch.setattr(module, "canonical_capability_bytes", lambda value: b"{}\n")
    assert module._capability_sha256(capability) == hashlib.sha256(b"{}\n").hexdigest()
    capability.write_bytes(b'{"noncanonical":true}\n')
    with pytest.raises(ReplayApplicationConfigurationError, match="not canonical"):
        module._capability_sha256(capability)
    capability.write_bytes(b"{broken")
    with pytest.raises(ReplayApplicationConfigurationError, match="strict production"):
        module._capability_sha256(capability)

    now = module._utc_now()
    assert now.tzinfo is timezone.utc

    source_settings = SimpleNamespace(
        implementation_root=module._DISTRIBUTION_ROOT,
        scene_gen_root=module._SCENE_GEN_ROOT,
        ledger_contract_root=module._LEDGER_CONTRACT_ROOT,
    )
    assert module._source_roots(source_settings) == (
        module._DISTRIBUTION_ROOT,
        module._SCENE_GEN_ROOT,
        module._LEDGER_CONTRACT_ROOT,
    )
    with pytest.raises(ReplayApplicationConfigurationError, match="actual replay distribution"):
        module._source_roots(
            SimpleNamespace(
                implementation_root=module._DISTRIBUTION_ROOT,
                scene_gen_root=module._SCENE_GEN_ROOT,
                ledger_contract_root=directory,
            )
        )


def test_live_assembly_uses_only_fixed_production_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = {}
    for name in ("interpreter", "runtime-runner", "capability", "launcher", "ffmpeg"):
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        files[name] = path
    directories = {}
    for name in ("state", "module", "delegated", "assets"):
        path = tmp_path / name
        path.mkdir()
        directories[name] = path
    settings = ReplayApplicationSettings(
        state_root=directories["state"],
        qualification_bundle_root=directories["state"],
        evidence_artifact_root=directories["state"],
        implementation_root=module._DISTRIBUTION_ROOT,
        scene_gen_root=module._SCENE_GEN_ROOT,
        ledger_contract_root=module._LEDGER_CONTRACT_ROOT,
        allowed_asset_roots=(directories["assets"],),
        interpreter=files["interpreter"],
        runtime_runner=files["runtime-runner"],
        runtime_module_root=directories["module"],
        runtime_capability_path=files["capability"],
        media_launcher=files["launcher"],
        static_ffmpeg=files["ffmpeg"],
        delegated_cgroup_root=directories["delegated"],
        runtime_timeout_seconds=9.0,
        capability_timeout_seconds=3.0,
    )
    calls: dict[str, object] = {}

    def runtime(**kwargs):
        calls["runtime"] = kwargs
        return "runtime"

    def sandbox(**kwargs):
        calls["sandbox"] = kwargs
        return "sandbox"

    def media(*, sandbox):
        calls["media"] = sandbox
        return "media"

    def package(store):
        calls["package"] = store
        return "package"

    sentinel = SimpleNamespace()

    def build(**kwargs):
        calls["build"] = kwargs
        return sentinel

    monkeypatch.setattr(module, "SubprocessRoboTwinRuntimeExecutor", runtime)
    monkeypatch.setattr(module, "NativeCgroupSandbox", sandbox)
    monkeypatch.setattr(module, "SubprocessReplayMediaVerifier", media)
    monkeypatch.setattr(module, "PackageStore", package)
    monkeypatch.setattr(module, "build_text2env_replay_wiring", build)
    monkeypatch.setattr(module, "_capability_sha256", lambda path: "a" * 64)
    store = SimpleNamespace()

    result = module._assemble_live_wiring(
        settings=settings,
        state_root=directories["state"],
        artifact_store=store,  # type: ignore[arg-type]
        allowed_roots=(directories["assets"],),
    )

    assert result is sentinel
    assert calls["runtime"] == {
        "interpreter": files["interpreter"],
        "runner": files["runtime-runner"],
        "work_root": directories["state"] / "runtime-executor",
        "timeout_seconds": 9.0,
        "capability_timeout_seconds": 3.0,
        "module_root": directories["module"],
    }
    build_call = calls["build"]
    assert isinstance(build_call, dict)
    assert build_call["task_config"] == "demo_clean"
    assert build_call["min_visible_pixels"] == 64
    assert build_call["checkpoint_steps"] == 120
    assert build_call["expected_capability_sha256"] == "a" * 64


@pytest.mark.parametrize("failure", ["nonempty", "registration", "publication"])
def test_application_failure_leaves_no_pass_documents_or_partial_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    bundle = tmp_path / "bundle"
    evidence = tmp_path / "evidence"
    assets = tmp_path / "assets"
    module_root = tmp_path / "module"
    delegated = tmp_path / "delegated"
    for directory in (bundle, evidence, assets, module_root, delegated):
        directory.mkdir()
    settings = ReplayApplicationSettings(
        state_root=tmp_path / "state",
        qualification_bundle_root=bundle,
        evidence_artifact_root=evidence,
        implementation_root=module._DISTRIBUTION_ROOT,
        scene_gen_root=module._SCENE_GEN_ROOT,
        ledger_contract_root=module._LEDGER_CONTRACT_ROOT,
        allowed_asset_roots=(assets,),
        interpreter=tmp_path / "interpreter",
        runtime_runner=tmp_path / "runner",
        runtime_module_root=module_root,
        runtime_capability_path=tmp_path / "capability",
        media_launcher=tmp_path / "launcher",
        static_ffmpeg=tmp_path / "ffmpeg",
        delegated_cgroup_root=delegated,
        runtime_timeout_seconds=1.0,
        capability_timeout_seconds=1.0,
    )
    for path in (
        settings.interpreter,
        settings.runtime_runner,
        settings.runtime_capability_path,
        settings.media_launcher,
        settings.static_ffmpeg,
    ):
        path.write_text(path.name, encoding="utf-8")
    publication_calls = 0
    if failure == "nonempty":
        settings.state_root.mkdir()
        (settings.state_root / "owned").write_text("preserve", encoding="utf-8")

    def register(_self, _request):
        if failure == "registration":
            raise RegistryRegistrationError("deep report rejected")
        return SimpleNamespace()

    def publish(*_args, **_kwargs):
        nonlocal publication_calls
        publication_calls += 1
        raise QualificationBundleError("artifact_publish_failed", "second CAS put failed")

    monkeypatch.setattr(module.SkillRegistry, "register_evidence_invariant", register)
    monkeypatch.setattr(module, "publish_replay_qualification_bundle", publish)

    with pytest.raises((ReplayApplicationConfigurationError, QualificationBundleError)):
        create_replay_application(settings)

    assert publication_calls == (1 if failure == "publication" else 0)
    if failure == "nonempty":
        assert (settings.state_root / "owned").read_text(encoding="utf-8") == "preserve"
    else:
        assert not settings.state_root.exists()
    assert tuple(evidence.rglob("*")) == ()


def test_replay_policy_request_and_cas_identity_are_not_injectable(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    settings = ReplayApplicationSettings(
        state_root=state,
        qualification_bundle_root=tmp_path,
        evidence_artifact_root=evidence,
        implementation_root=module._DISTRIBUTION_ROOT,
        scene_gen_root=module._SCENE_GEN_ROOT,
        ledger_contract_root=module._LEDGER_CONTRACT_ROOT,
        allowed_asset_roots=(tmp_path,),
        interpreter=tmp_path / "interpreter",
        runtime_runner=tmp_path / "runner",
        runtime_module_root=tmp_path,
        runtime_capability_path=tmp_path / "capability",
        media_launcher=tmp_path / "launcher",
        static_ffmpeg=tmp_path / "ffmpeg",
        delegated_cgroup_root=tmp_path,
        runtime_timeout_seconds=1.0,
        capability_timeout_seconds=1.0,
    )
    store = module.LocalArtifactStore(evidence)
    policy = module._ReplayRegistrationPolicy(
        settings=settings,
        state_root=state,
        qualification_root=tmp_path,
        artifact_store=store,
        allowed_roots=(tmp_path,),
        implementation_root=module._DISTRIBUTION_ROOT,
        scene_gen_root=module._SCENE_GEN_ROOT,
        ledger_contract_root=module._LEDGER_CONTRACT_ROOT,
    )

    with pytest.raises(ReplayApplicationConfigurationError, match="exact operator settings"):
        policy.verify_and_build(object(), artifact_resolver=store)
    with pytest.raises(ReplayApplicationConfigurationError, match="share the application"):
        policy.verify_and_build(settings, artifact_resolver=object())


def test_nonempty_state_is_refused_and_rollback_handles_files_and_directories(
    tmp_path: Path,
) -> None:
    root = tmp_path / "rollback"
    (root / "child").mkdir(parents=True)
    (root / "child" / "nested").write_text("value", encoding="utf-8")
    (root / "file").write_text("value", encoding="utf-8")

    module._rollback_state_root(root, remove_root=False)

    assert root.is_dir()
    assert tuple(root.iterdir()) == ()
    (root / "child").mkdir()
    module._rollback_state_root(root, remove_root=True)
    assert not root.exists()
