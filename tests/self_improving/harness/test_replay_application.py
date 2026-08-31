from __future__ import annotations

import hashlib
import inspect
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

import self_improving.harness.replay_application as module
from self_improving.harness.handlers.text2env_replay import text2env_replay_descriptor
from self_improving.harness.qualification import QualificationBundleError
from self_improving.harness.registry import RegistryRegistrationError
from self_improving.harness.replay_application import (
    ReplayApplication,
    ReplayApplicationConfigurationError,
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
)


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


def test_application_facade_exposes_real_registry_journal_and_run_store_surfaces(
    tmp_path: Path,
) -> None:
    dependencies = _dependencies()
    loaded = _loaded(dependencies)
    replay_input = Text2EnvReplayInput.model_validate(
        loaded.kernel_evaluation.invocation.effective_parameters
    )
    state = SimpleNamespace(run_id=loaded.kernel_evaluation.invocation.run_id)

    class Registry:
        descriptors = (loaded.descriptor,)
        invocation_args = None

        def list(self):
            return self.descriptors

        def invoke(self, *args):
            self.invocation_args = args
            return state

    class Journal:
        path = tmp_path / "events.sqlite3"
        read_args = None

        def read(self, **kwargs):
            self.read_args = kwargs
            return "page"

    class Runs:
        def read_invocation(self, run_id):
            return ("invocation", run_id)

        def read_run_state(self, run_id):
            return ("state", run_id)

    registry = Registry()
    journal = Journal()
    application = ReplayApplication(
        artifact_store=SimpleNamespace(root=tmp_path / "cas"),  # type: ignore[arg-type]
        event_journal=journal,  # type: ignore[arg-type]
        run_store=Runs(),  # type: ignore[arg-type]
        registry=registry,  # type: ignore[arg-type]
    )

    assert application.skills == (loaded.descriptor,)
    assert application.artifact_root == tmp_path / "cas"
    assert application.journal_path == journal.path.resolve()
    assert application.replay(replay_input) is state
    assert registry.invocation_args == (
        "text2env.replay",
        "1.0.0",
        replay_input.model_dump(mode="json"),
    )
    with pytest.raises(TypeError, match="Text2EnvReplayInput"):
        application.replay({})  # type: ignore[arg-type]
    run_id = loaded.kernel_evaluation.invocation.run_id
    assert application.events(after_event_id=3, run_id=run_id, limit=7) == "page"
    assert journal.read_args == {"after_event_id": 3, "run_id": run_id, "limit": 7}
    assert application.invocation(run_id) == ("invocation", run_id)
    assert application.run_state(run_id) == ("state", run_id)

    registry.descriptors = (SimpleNamespace(),)
    with pytest.raises(RuntimeError, match="another descriptor type"):
        _ = application.skills


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
