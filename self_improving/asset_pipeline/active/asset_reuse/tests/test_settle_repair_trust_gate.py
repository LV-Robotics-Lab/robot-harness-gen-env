import hashlib
import importlib.util
import json
import os
import runpy
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ledger" / "settle_repair.py"
sys.path.insert(0, str(ROOT))
from lib import ledger, ledger_writes  # noqa: E402

from tests.test_ledger import make_valid  # noqa: E402
from tests.trusted_fixtures import qualified_runtime_capability  # noqa: E402


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fake_attempt(measurement, runtime_root):
    passed = measurement is not None
    orientation = measurement["orientation_wxyz"] if passed else [1.0, 0.0, 0.0, 0.0]
    return {
        "measurement": measurement,
        "result": {
            "schema": "asset_settle_result.v2",
            "finite": passed,
            "late_drift_m": measurement["late_m"] if passed else None,
            "support_z_m": 0.0 if passed else None,
            "tilt_deg": 0.0 if passed else None,
            "rest_orientation_wxyz": orientation,
            "origin_z_m": measurement["origin_z"] if passed else None,
            "derived_z_policy": (
                ledger.z_policy_from_origin(measurement["origin_z"]) if passed else None
            ),
            "details": {},
        },
        "runtime_capability": qualified_runtime_capability(
            runtime_root,
            "asset.settle_repair.v1",
            ledger,
            ledger_writes,
        ),
    }


def _write_file_backed_library_ledger(library, asset="315_shears"):
    asset_dir = library / asset
    asset_dir.mkdir(parents=True)
    document = make_valid()
    document["external_ids"]["env_gen"] = asset
    document["asset_id"] = f"external_{asset}"
    model = document["models"][0]
    for index, representation in enumerate(model["representations"]):
        suffix = "png" if representation["role"] == "snapshot" else "ply"
        path = asset_dir / f"library-representation-{index}.{suffix}"
        path.write_bytes(f"library-private-bytes-{index}".encode())
        digest = _sha256(path)
        representation.update(
            format=suffix,
            uri=str(path),
            sha256=digest,
            files=[{"uri": str(path), "sha256": digest, "bytes": path.stat().st_size}],
        )
    model["verification"][0]["verified_digest"] = ledger.reps_digest(model, "sapien")
    assert ledger.validate_ledger(document, check_files=True) == []
    ledger_path = asset_dir / "ledger.json"
    ledger.write_ledger(ledger_path, document)
    return ledger_path, document


def _load_settle_repair_module():
    spec = importlib.util.spec_from_file_location("settle_repair_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_world_min_z_uses_sapien3_collision_component_not_render_geometry():
    module = _load_settle_repair_module()

    class RenderComponent:
        render_shapes = (object(),)

        def compute_global_aabb_tight(self):
            return [[-1.0, -1.0, -2.0], [1.0, 1.0, 2.0]]

    class CollisionComponent:
        collision_shapes = (object(), object())

        def compute_global_aabb_tight(self):
            return [[-0.1, -0.2, 0.125], [0.1, 0.2, 0.725]]

    class Sapien3Entity:
        def get_components(self):
            return [RenderComponent(), CollisionComponent()]

    assert module._world_min_z(Sapien3Entity()) == pytest.approx(0.125)


def _write_fake_settle_runtime(shadow):
    (shadow / "sapien").mkdir(parents=True)
    (shadow / "sapien" / "__init__.py").write_text("")
    (shadow / "sapien" / "executed_only.py").write_text("LOADED = True\n")
    (shadow / "sapien" / "core.py").write_text(
        """
class Pose:
    def __init__(self, p=None, q=None):
        self.p = p or [0, 0, 0]
        self.q = q or [1, 0, 0, 0]


class Scene:
    def set_timestep(self, *args):
        pass

    def add_ground(self, *args):
        pass

    def step(self):
        pass
"""
    )
    (shadow / "envs").mkdir()
    (shadow / "envs" / "__init__.py").write_text("")
    (shadow / "envs" / "executed_only.py").write_text("LOADED = True\n")
    (shadow / "envs" / "utils.py").write_text(
        """
import os


class _Pose:
    def __init__(self, p, q):
        self.p = p
        self.q = q


class _Entity:
    def __init__(self, mode):
        self.mode = mode
        self.calls = 0

    def get_pose(self):
        self.calls += 1
        if self.mode == "late" and self.calls > 1:
            return _Pose([0.01, 0.0, 0.03], [1.0, 0.0, 0.0, 0.0])
        return _Pose([0.0, 0.0, 0.03], [0.5, 0.5, 0.5, 0.5])

    def get_global_aabb(self):
        return [[-0.01, -0.01, 0.0], [0.01, 0.01, 0.06]]


class _Wrapper:
    def __init__(self, mode):
        self.actor = _Entity(mode)


def create_actor(*args, **kwargs):
    import envs.executed_only
    import sapien.executed_only

    mode = os.environ.get("SETTLE_FAKE_MODE", "pass")
    if mode == "none":
        return None
    if mode == "plain":
        return _Entity(mode)
    return _Wrapper(mode)
"""
    )


def _clear_fake_runtime_modules():
    for module_name in (
        "sapien",
        "sapien.core",
        "sapien.executed_only",
        "envs",
        "envs.utils",
        "envs.executed_only",
    ):
        sys.modules.pop(module_name, None)


def _install_fake_sapien_modules():
    sapien_package = types.ModuleType("sapien")
    sapien_package.__path__ = []
    sapien_core = types.ModuleType("sapien.core")

    class Pose:
        def __init__(self, p=None, q=None):
            self.p = p or [0, 0, 0]
            self.q = q or [1, 0, 0, 0]

    class Scene:
        def set_timestep(self, *args):
            pass

        def add_ground(self, *args):
            pass

        def step(self):
            pass

    sapien_core.Pose = Pose
    sapien_core.Scene = Scene
    sapien_package.core = sapien_core
    sys.modules["sapien"] = sapien_package
    sys.modules["sapien.core"] = sapien_core


def test_settle_source_identity_helpers_reject_unsafe_paths_and_bad_closures(tmp_path):
    module = _load_settle_repair_module()
    error = module.SourceIdentityError

    with pytest.raises(error):
        module._existing_path(None, field="candidate")
    with pytest.raises(error):
        module._existing_path("", field="candidate")
    with pytest.raises(error):
        module._existing_path(tmp_path / "missing", field="candidate")
    assert module._samefile(tmp_path / "missing-left", tmp_path / "missing-right") is False

    visual = tmp_path / "visual.ply"
    visual.write_bytes(b"visual")
    visual_record = {
        "uri": str(visual),
        "sha256": _sha256(visual),
        "bytes": visual.stat().st_size,
    }
    inputs = module._sapien_representation_inputs(
        {
            "representations": [
                "not-a-representation",
                {"backend": "sapien", "role": "snapshot", "files": [visual_record]},
                {"backend": "sapien", "role": "visual", "files": [visual_record]},
            ]
        }
    )
    assert inputs == {"sapien_representation_2_0": visual_record}

    bad_cases = [
        {"representations": [{"backend": "sapien", "role": "visual", "files": None}]},
        {"representations": [{"backend": "sapien", "role": "visual", "files": [None]}]},
        {
            "representations": [
                {
                    "backend": "sapien",
                    "role": "visual",
                    "files": [{"uri": str(tmp_path / "missing"), "sha256": "0" * 64, "bytes": 1}],
                }
            ]
        },
        {"representations": []},
    ]
    for model in bad_cases:
        with pytest.raises(error):
            module._sapien_representation_inputs(model)


def test_try_settle_uses_shadow_runtime_and_restores_cwd(tmp_path, monkeypatch):
    module = _load_settle_repair_module()
    shadow = tmp_path / "shadow"
    _write_fake_settle_runtime(shadow)
    old_path = list(sys.path)
    cwd = Path.cwd()
    _clear_fake_runtime_modules()
    try:
        monkeypatch.setenv("SETTLE_FAKE_MODE", "pass")
        result = module.try_settle(shadow, shadow, "315_shears", 0, [1, 0, 0, 0])
        assert result["measurement"] == {
            "orientation_wxyz": [0.5, 0.5, 0.5, 0.5],
            "origin_z": 0.03,
            "late_m": 0.0,
        }
        assert Path.cwd() == cwd
        loader_names = {entry["name"] for entry in result["runtime_capability"]["loader_modules"]}
        sapien_names = {entry["name"] for entry in result["runtime_capability"]["sapien_modules"]}
        assert "envs.executed_only" in loader_names
        assert "sapien.executed_only" in sapien_names

        monkeypatch.setenv("SETTLE_FAKE_MODE", "plain")
        assert (
            module.try_settle(shadow, shadow, "315_shears", 0, [1, 0, 0, 0])["measurement"][
                "late_m"
            ]
            == 0.0
        )

        monkeypatch.setenv("SETTLE_FAKE_MODE", "none")
        assert (
            module.try_settle(shadow, shadow, "315_shears", 0, [1, 0, 0, 0])["measurement"] is None
        )

        monkeypatch.setenv("SETTLE_FAKE_MODE", "late")
        assert (
            module.try_settle(shadow, shadow, "315_shears", 0, [1, 0, 0, 0])["measurement"] is None
        )
    finally:
        os.chdir(cwd)
        sys.path[:] = old_path
        _clear_fake_runtime_modules()


def test_settle_repair_refuses_shadow_asset_that_is_not_the_library_tree(tmp_path, monkeypatch):
    library = tmp_path / "data" / "asset_library"
    ledger_path, _document = _write_file_backed_library_ledger(library)
    before = ledger_path.read_bytes()

    upstream_asset = tmp_path / "upstream_robotwin" / "assets" / "objects" / "315_shears"
    upstream_asset.mkdir(parents=True)
    shadow_objects = tmp_path / "data" / "robotwin_shadow" / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / "315_shears").symlink_to(upstream_asset, target_is_directory=True)

    module = _load_settle_repair_module()
    module.DEV = tmp_path
    calls = []

    def fake_try_settle(*args):
        calls.append(args)
        return _fake_attempt(
            {
                "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "origin_z": 0.0,
                "late_m": 0.0,
            },
            tmp_path / "runtime",
        )

    monkeypatch.setattr(module, "try_settle", fake_try_settle)
    monkeypatch.setattr(sys, "argv", ["settle_repair.py", "315_shears"])

    module.main()

    assert calls == []
    assert ledger_path.read_bytes() == before
    assert not (ledger_path.parent / "verification_evidence").exists()


def test_settle_repair_accepts_when_shadow_asset_is_the_library_tree(tmp_path, monkeypatch):
    library = tmp_path / "data" / "asset_library"
    ledger_path, _document = _write_file_backed_library_ledger(library)
    asset_dir = ledger_path.parent

    shadow_objects = tmp_path / "data" / "robotwin_shadow" / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / "315_shears").symlink_to(asset_dir, target_is_directory=True)

    module = _load_settle_repair_module()
    module.DEV = tmp_path
    calls = []

    def fake_try_settle(*args):
        calls.append(args)
        return _fake_attempt(
            {
                "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "origin_z": 0.0,
                "late_m": 0.0,
            },
            tmp_path / "runtime",
        )

    monkeypatch.setattr(module, "try_settle", fake_try_settle)
    monkeypatch.setattr(sys, "argv", ["settle_repair.py", "315_shears"])

    module.main()

    assert len(calls) == 1
    written = json.loads(ledger_path.read_text())
    receipt = written["models"][0]["verification"][-1]
    assert receipt["check"] == "settle"
    assert receipt["run_id"] == module.RUN_ID
    assert (
        ledger.latest_trusted_verification(written["models"][0], "sapien", "settle", "315_shears")
        == receipt
    )


def test_settle_repair_rechecks_source_identity_after_replay_before_evidence(tmp_path, monkeypatch):
    library = tmp_path / "data" / "asset_library"
    ledger_path, _document = _write_file_backed_library_ledger(library)
    before = ledger_path.read_bytes()
    asset_dir = ledger_path.parent

    shadow_objects = tmp_path / "data" / "robotwin_shadow" / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / "315_shears").symlink_to(asset_dir, target_is_directory=True)

    module = _load_settle_repair_module()
    module.DEV = tmp_path
    calls = []

    def fake_try_settle(*args):
        calls.append(args)
        (asset_dir / "library-representation-0.ply").write_bytes(b"mutated-after-replay")
        return _fake_attempt(
            {
                "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "origin_z": 0.0,
                "late_m": 0.0,
            },
            tmp_path / "runtime",
        )

    monkeypatch.setattr(module, "try_settle", fake_try_settle)
    monkeypatch.setattr(sys, "argv", ["settle_repair.py", "315_shears"])

    module.main()

    assert len(calls) == 1
    assert ledger_path.read_bytes() == before
    assert not (asset_dir / "verification_evidence").exists()


def test_settle_repair_refuses_valid_source_identity_drift_after_replay(tmp_path, monkeypatch):
    library = tmp_path / "data" / "asset_library"
    ledger_path, _document = _write_file_backed_library_ledger(library)
    asset_dir = ledger_path.parent
    representation_path = asset_dir / "library-representation-0.ply"

    shadow_objects = tmp_path / "data" / "robotwin_shadow" / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / "315_shears").symlink_to(asset_dir, target_is_directory=True)

    module = _load_settle_repair_module()
    module.DEV = tmp_path
    calls = []

    def fake_try_settle(*args):
        calls.append(args)
        representation_path.write_bytes(b"mutated-but-ledger-updated")
        digest = _sha256(representation_path)
        document = json.loads(ledger_path.read_text())
        representation = document["models"][0]["representations"][0]
        representation["sha256"] = digest
        representation["files"][0].update(
            sha256=digest,
            bytes=representation_path.stat().st_size,
        )
        document["models"][0]["verification"][0]["verified_digest"] = ledger.reps_digest(
            document["models"][0], "sapien"
        )
        ledger.write_ledger(ledger_path, document)
        return _fake_attempt(
            {
                "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "origin_z": 0.0,
                "late_m": 0.0,
            },
            tmp_path / "runtime",
        )

    monkeypatch.setattr(module, "try_settle", fake_try_settle)
    monkeypatch.setattr(sys, "argv", ["settle_repair.py", "315_shears"])

    module.main()

    assert len(calls) == 1
    written = json.loads(ledger_path.read_text())
    assert len(written["models"][0]["verification"]) == 1
    assert not (asset_dir / "verification_evidence").exists()


def test_settle_repair_skips_unsafe_or_invalid_target_before_write(tmp_path, monkeypatch):
    module = _load_settle_repair_module()
    module.DEV = tmp_path
    monkeypatch.setattr(sys, "argv", ["settle_repair.py", "../315_shears"])

    module.main()

    assert not (tmp_path / "data" / "asset_library").exists()


def test_settle_repair_skips_existing_file_backed_ledger_violation(tmp_path, monkeypatch):
    library = tmp_path / "data" / "asset_library"
    ledger_path, _document = _write_file_backed_library_ledger(library)
    before = ledger_path.read_bytes()
    (ledger_path.parent / "library-representation-0.ply").write_bytes(b"stale")

    module = _load_settle_repair_module()
    module.DEV = tmp_path
    monkeypatch.setattr(sys, "argv", ["settle_repair.py", "315_shears"])

    module.main()

    assert ledger_path.read_bytes() == before
    assert not (ledger_path.parent / "verification_evidence").exists()


def test_settle_repair_retries_orientations_and_skips_duplicate_target(tmp_path, monkeypatch):
    library = tmp_path / "data" / "asset_library"
    ledger_path, _document = _write_file_backed_library_ledger(library)
    asset_dir = ledger_path.parent

    shadow_objects = tmp_path / "data" / "robotwin_shadow" / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / "315_shears").symlink_to(asset_dir, target_is_directory=True)

    module = _load_settle_repair_module()
    module.DEV = tmp_path
    calls = []

    def fake_try_settle(*args):
        calls.append(args)
        if len(calls) == 1:
            return _fake_attempt(None, tmp_path / "runtime")
        return _fake_attempt(
            {
                "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "origin_z": 0.03,
                "late_m": 0.0,
            },
            tmp_path / "runtime",
        )

    monkeypatch.setattr(module, "try_settle", fake_try_settle)
    monkeypatch.setattr(sys, "argv", ["settle_repair.py", "315_shears", "315_shears"])

    module.main()

    assert len(calls) == 2
    written = json.loads(ledger_path.read_text())
    receipt = written["models"][0]["verification"][-1]
    assert receipt["check"] == "settle"
    assert receipt["verdict"] == "pass"
    assert written["models"][0]["physical"]["conventions"]["z_policy"] == "center_on_table"


def test_settle_repair_writes_fail_receipt_without_stable_pose_mutation(tmp_path, monkeypatch):
    library = tmp_path / "data" / "asset_library"
    ledger_path, document = _write_file_backed_library_ledger(library)
    original_pose = document["models"][0]["physical"]["conventions"]["stable_poses"][0]
    asset_dir = ledger_path.parent

    shadow_objects = tmp_path / "data" / "robotwin_shadow" / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / "315_shears").symlink_to(asset_dir, target_is_directory=True)

    module = _load_settle_repair_module()
    module.DEV = tmp_path
    calls = []

    def fake_try_settle(*args):
        calls.append(args)
        return _fake_attempt(None, tmp_path / "runtime")

    monkeypatch.setattr(module, "try_settle", fake_try_settle)
    monkeypatch.setattr(sys, "argv", ["settle_repair.py", "315_shears"])

    module.main()

    assert len(calls) == 6
    written = json.loads(ledger_path.read_text())
    assert written["models"][0]["verification"][-1]["verdict"] == "fail"
    assert written["models"][0]["physical"]["conventions"]["stable_poses"][0] == original_pose


def test_settle_repair_main_entrypoint_runs_without_writing_for_unsafe_target(monkeypatch, capsys):
    old_path = list(sys.path)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "../315_shears"])
    try:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    finally:
        sys.path[:] = old_path

    captured = capsys.readouterr()
    assert "SKIP write" in captured.out
