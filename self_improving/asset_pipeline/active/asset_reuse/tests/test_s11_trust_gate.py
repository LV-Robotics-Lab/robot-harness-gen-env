import hashlib
import json
import os
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "4_validate" / "s11_runtime_load_sweep.py"
sys.path.insert(0, str(ROOT))
from lib import ledger, ledger_writes  # noqa: E402

from tests.test_ledger import make_valid  # noqa: E402
from tests.trusted_fixtures import qualified_runtime_capability  # noqa: E402


@pytest.mark.parametrize(
    "entries",
    [
        [{"asset_id": "../315_shears", "models": [{"model_id": 0}]}],
        [{"asset_id": "315_shears", "models": [{"model_id": True}]}],
        [
            {
                "asset_id": "315_shears",
                "models": [{"model_id": 0}, {"model_id": 0}],
            }
        ],
    ],
)
def test_s11_rejects_unsafe_or_duplicate_identity_before_importing_simulator(
    tmp_path, entries, monkeypatch, capsys
):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"entries": entries}))
    output = tmp_path / "report.json"

    code = _run_script_in_process(
        monkeypatch,
        [
            "--shadow",
            str(tmp_path / "missing-shadow"),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(tmp_path / "library"),
        ],
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "FAIL s11" in captured.out
    assert not output.exists()


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_file_backed_library_ledger(
    library, asset="315_shears", *, trusted_settle=False, urdf=False
):
    asset_dir = library / asset
    asset_dir.mkdir(parents=True)
    document = make_valid(kind="articulated" if urdf else "rigid")
    document["external_ids"]["env_gen"] = asset
    document["asset_id"] = f"external_{asset}"
    model = document["models"][0]
    if urdf:
        model["articulation"] = {"joint_names": ["joint_0"]}
        conventions = model["physical"]["conventions"]
        conventions["is_static"] = True
        conventions["stable_poses"][0].update(
            pose_id="fixed_root_identity",
            orientation_wxyz=[1.0, 0.0, 0.0, 0.0],
        )
        model["representations"][0].update(
            format="ply",
            backend="sapien",
            role="visual_and_collision",
            collision_meta={"mode": "explicit_mesh"},
        )
    else:
        model["representations"][1].update(
            format="ply",
            backend="sapien",
            role="collision",
            collision_meta={"mode": "explicit_mesh"},
        )
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
    if trusted_settle:
        representation_inputs = {
            f"representation_{index}_{file_index}": dict(member)
            for index, representation in enumerate(model["representations"])
            if representation["backend"] == "sapien" and representation["role"] != "snapshot"
            for file_index, member in enumerate(representation["files"])
        }
        issuer = "asset.s13b_joint_sweep.v1" if urdf else "asset.settle_repair.v1"
        thresholds = (
            {"min_screenshot_std": 1.0, "allow_free_joints": False}
            if urdf
            else {
                "max_late_drift_m": 0.002,
                "min_support_z_m": -0.005,
                "max_tilt_deg": 181.0,
            }
        )
        default_pose = model["physical"]["conventions"]["stable_poses"][0]
        result = (
            {
                "schema": "asset_joint_sweep_result.v2",
                "loaded": True,
                "dof": 1,
                "expected_dof": 1,
                "limits_match": True,
                "settle_finite": True,
                "converged": True,
                "free_joint_count": 0,
                "sweep_finite": True,
                "screenshot_std": 2.0,
                "fix_root_link": True,
                "root_pose_id": default_pose["pose_id"],
                "root_orientation_wxyz": list(default_pose["orientation_wxyz"]),
                "z_policy": model["physical"]["conventions"]["z_policy"],
                "details": {},
            }
            if urdf
            else {
                "schema": "asset_settle_result.v2",
                "finite": True,
                "late_drift_m": 0.0,
                "support_z_m": 0.0,
                "tilt_deg": 0.0,
                "rest_orientation_wxyz": list(default_pose["orientation_wxyz"]),
                "origin_z_m": 0.0,
                "derived_z_policy": "origin_on_table",
                "details": {},
            }
        )
        payload = ledger_writes.issue_qualified_verification(
            issuer=issuer,
            asset_key=asset,
            model_id=model["model_id"],
            run_id="trusted-settle-fixture",
            timestamp="2026-08-31T12:00:00",
            reps_digest=ledger.reps_digest(model, "sapien"),
            inputs={
                **representation_inputs,
                "task": {"asset_key": asset, "model_id": model["model_id"]},
            },
            thresholds=thresholds,
            result=result,
            model_entry=model,
            execution_snapshot=ledger_writes.publish_execution_snapshot(
                asset_dir / "verification_inputs",
                source_asset_dir=asset_dir,
                asset_key=asset,
                model_entry=model,
            ),
            runtime_capability=qualified_runtime_capability(
                asset_dir / "runtime",
                issuer,
                ledger,
                ledger_writes,
            ),
        )
        record = ledger_writes.publish_verification_evidence(
            asset_dir / "verification_evidence", payload
        )
        model["verification"] = [ledger_writes.receipt_from_evidence(payload, record)]
    assert ledger.validate_ledger(document, check_files=True) == []
    ledger_path = asset_dir / "ledger.json"
    ledger.write_ledger(ledger_path, document)
    return ledger_path, document


def _catalog_placement(document, model_id=0):
    model = next(item for item in document["models"] if item["model_id"] == model_id)
    pose = next(
        item
        for item in model["physical"]["conventions"]["stable_poses"]
        if item.get("is_default") is True
    )
    return {
        "stable_pose_id": pose["pose_id"],
        "stable_orientation_wxyz": list(pose["orientation_wxyz"]),
        "z_policy": model["physical"]["conventions"]["z_policy"],
    }


def _write_runtime_loader_stubs(
    shadow, *, mutate_path=None, actor_none=False, returned_orientation=None
):
    (shadow / "envs").mkdir(parents=True)
    (shadow / "envs" / "__init__.py").write_text("")
    (shadow / "envs" / "executed_only.py").write_text("LOADED = True\n")
    mutation = (
        "    __import__('pathlib').Path("
        f"{str(mutate_path)!r}"
        ").write_bytes(b'mutated-after-replay')\n"
        if mutate_path is not None
        else ""
    )
    returned_q = returned_orientation
    (shadow / "envs" / "utils.py").write_text(
        f"""
class _Pose:
    def __init__(self, p, q):
        self.p = list(p)
        self.q = list(q)


class _Actor:
    def __init__(self, pose):
        returned_q = {returned_q!r}
        self._pose = _Pose(pose.p, returned_q if returned_q is not None else pose.q)

    def get_pose(self):
        return self._pose


def create_actor(*args, **kwargs):
    import envs.executed_only
    import sapien.executed_only

{mutation}\
    if {actor_none!r}:
        return None
    marker = __import__("os").environ.get("S11_CWD_MARKER")
    if marker:
        __import__("pathlib").Path(marker).write_text(str(__import__("pathlib").Path.cwd()))
    pose_marker = __import__("os").environ.get("S11_POSE_MARKER")
    if pose_marker:
        __import__("pathlib").Path(pose_marker).write_text(
            __import__("json").dumps(list(args[1].q))
        )
    return _Actor(args[1])


def create_sapien_urdf_obj(*args, **kwargs):
    import envs.executed_only
    import sapien.executed_only

    return _Actor(args[1])
"""
    )
    (shadow / "sapien").mkdir()
    (shadow / "sapien" / "executed_only.py").write_text("LOADED = True\n")
    (shadow / "sapien" / "_native.so").write_bytes(b"fake-native-runtime")
    (shadow / "sapien" / "__init__.py").write_text(
        """
import pathlib
import sys
import types

_native = types.ModuleType("sapien._native")
_native.__file__ = str(pathlib.Path(__file__).with_name("_native.so"))
sys.modules["sapien._native"] = _native


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


def _run_script_in_process(monkeypatch, argv):
    cwd = Path.cwd()
    old_path = list(sys.path)
    for module_name in (
        "sapien",
        "sapien._native",
        "sapien.executed_only",
        "envs",
        "envs.utils",
        "envs.executed_only",
    ):
        sys.modules.pop(module_name, None)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *argv])
    try:
        with pytest.raises(SystemExit) as stopped:
            runpy.run_path(str(SCRIPT), run_name="__main__")
    finally:
        os.chdir(cwd)
        sys.path[:] = old_path
        for module_name in (
            "sapien",
            "sapien._native",
            "sapien.executed_only",
            "envs",
            "envs.utils",
            "envs.executed_only",
        ):
            sys.modules.pop(module_name, None)
    return stopped.value.code


def _load_s11_namespace(monkeypatch, tmp_path):
    catalog = tmp_path / "empty_catalog.json"
    catalog.write_text(json.dumps({"entries": []}))
    output = tmp_path / "empty_report.json"
    cwd = Path.cwd()
    old_path = list(sys.path)
    for module_name in ("sapien", "envs", "envs.utils"):
        sys.modules.pop(module_name, None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--shadow",
            str(tmp_path / "shadow"),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(tmp_path / "library"),
        ],
    )
    monkeypatch.setattr(sys, "exit", lambda code=0: None)
    try:
        return runpy.run_path(str(SCRIPT), run_name="s11_under_test")
    finally:
        os.chdir(cwd)
        sys.path[:] = old_path
        for module_name in ("sapien", "envs", "envs.utils"):
            sys.modules.pop(module_name, None)


def test_s11_source_identity_helpers_reject_unsafe_paths(monkeypatch, tmp_path):
    namespace = _load_s11_namespace(monkeypatch, tmp_path)
    error = namespace["SourceIdentityError"]

    with pytest.raises(error):
        namespace["_existing_path"](None, field="candidate")
    with pytest.raises(error):
        namespace["_existing_path"]("", field="candidate")
    with pytest.raises(error):
        namespace["_existing_path"](tmp_path / "missing", field="candidate")
    assert namespace["_samefile"](tmp_path / "missing-left", tmp_path / "missing-right") is False

    stale = {"uri": str(tmp_path / "missing"), "sha256": "0" * 64, "bytes": 1}
    with pytest.raises(error):
        namespace["_record_path"](stale, field="record")

    current = tmp_path / "current.ply"
    current.write_bytes(b"current")
    current_record = {
        "uri": str(current),
        "sha256": _sha256(current),
        "bytes": current.stat().st_size,
    }

    def explode(_uri):
        raise ValueError("unsafe uri")

    with monkeypatch.context() as scoped:
        scoped.setattr(namespace["ledger"], "artifact_file_record_is_current", lambda record: True)
        scoped.setattr(namespace["ledger"], "resolve_uri", explode)
        with pytest.raises(error):
            namespace["_record_path"](current_record, field="record")


def test_s11_source_identity_helpers_require_file_closure_and_role_match(monkeypatch, tmp_path):
    namespace = _load_s11_namespace(monkeypatch, tmp_path)
    error = namespace["SourceIdentityError"]
    visual = tmp_path / "visual.ply"
    extra = tmp_path / "extra.ply"
    visual.write_bytes(b"visual")
    extra.write_bytes(b"extra")
    visual_record = {
        "uri": str(visual),
        "sha256": _sha256(visual),
        "bytes": visual.stat().st_size,
    }
    extra_record = {
        "uri": str(extra),
        "sha256": _sha256(extra),
        "bytes": extra.stat().st_size,
    }

    inputs, primary_paths = namespace["_sapien_representation_identity"](
        {
            "representations": [
                "not-a-representation",
                {
                    "backend": "sapien",
                    "role": "snapshot",
                    "files": [extra_record],
                    "uri": str(extra),
                    "sha256": _sha256(extra),
                },
                {
                    "backend": "sapien",
                    "role": "visual",
                    "files": [extra_record, visual_record],
                    "uri": str(visual),
                    "sha256": _sha256(visual),
                },
            ]
        }
    )
    assert sorted(inputs) == [
        "sapien_representation_2_0",
        "sapien_representation_2_1",
    ]
    assert primary_paths["visual"][0].samefile(visual)
    namespace["_require_catalog_field_matches_roles"](
        {"visual_path": str(visual)}, "visual_path", ("visual",), primary_paths
    )
    with pytest.raises(error):
        namespace["_require_catalog_field_matches_roles"](
            {"visual_path": str(visual)}, "visual_path", ("collision",), primary_paths
        )

    bad_cases = [
        {"representations": [{"backend": "sapien", "role": "visual", "files": None}]},
        {"representations": [{"backend": "sapien", "role": "visual", "files": [None]}]},
        {
            "representations": [
                {
                    "backend": "sapien",
                    "role": "visual",
                    "files": [extra_record],
                    "uri": str(visual),
                    "sha256": _sha256(visual),
                }
            ]
        },
        {"representations": []},
    ]
    for model in bad_cases:
        with pytest.raises(error):
            namespace["_sapien_representation_identity"](model)


def test_s11_metadata_helper_requires_authoritative_model_data(monkeypatch, tmp_path):
    namespace = _load_s11_namespace(monkeypatch, tmp_path)
    error = namespace["SourceIdentityError"]
    asset_dir = tmp_path / "asset"
    asset_dir.mkdir()
    loaded = SimpleNamespace(asset_dir=asset_dir)
    assert namespace["_require_catalog_metadata_matches_ledger"]({}, loaded, 0) is None

    wrong = tmp_path / "wrong.json"
    wrong.write_text("{}")
    with pytest.raises(error):
        namespace["_require_catalog_metadata_matches_ledger"](
            {"metadata_path": str(wrong)}, loaded, 0
        )


@pytest.mark.parametrize(
    ("kind", "is_static", "catalog_model"),
    [
        ("rigid", False, {"urdf_path": "model.urdf"}),
        ("articulated", False, {"urdf_path": "model.urdf"}),
        ("articulated", True, {}),
        ("rigid", True, {}),
    ],
)
def test_s11_loader_mode_must_match_the_authoritative_physical_mode(
    monkeypatch, tmp_path, kind, is_static, catalog_model
):
    namespace = _load_s11_namespace(monkeypatch, tmp_path)
    model = {"physical": {"conventions": {"is_static": is_static}}}
    loaded = SimpleNamespace(document={"kind": kind})

    with pytest.raises(namespace["SourceIdentityError"]):
        namespace["_require_loader_semantics"](
            loaded,
            catalog_model,
            model,
            {"z_policy": "origin_on_table"},
        )


def test_s11_loader_mode_rejects_an_unimplemented_center_origin_policy(monkeypatch, tmp_path):
    namespace = _load_s11_namespace(monkeypatch, tmp_path)

    with pytest.raises(namespace["SourceIdentityError"], match="origin_on_table"):
        namespace["_require_loader_semantics"](
            SimpleNamespace(document={"kind": "rigid"}),
            {},
            {"physical": {"conventions": {"is_static": False}}},
            {"z_policy": "center_on_table"},
        )


def test_s11_refuses_upstream_fallback_before_evidence_or_receipt(tmp_path, monkeypatch, capsys):
    library = tmp_path / "library"
    ledger_path, _document = _write_file_backed_library_ledger(library)
    before = ledger_path.read_bytes()

    shadow = tmp_path / "shadow"
    _write_runtime_loader_stubs(shadow)
    upstream_asset = tmp_path / "robotwin" / "assets" / "objects" / "315_shears"
    (upstream_asset / "visual").mkdir(parents=True)
    (upstream_asset / "collision").mkdir()
    (upstream_asset / "visual" / "base0.glb").write_bytes(b"upstream visual")
    (upstream_asset / "collision" / "base0.glb").write_bytes(b"upstream collision")
    (upstream_asset / "model_data0.json").write_text("{}")
    (shadow / "assets" / "objects").mkdir(parents=True)
    (shadow / "assets" / "objects" / "315_shears").symlink_to(
        upstream_asset, target_is_directory=True
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": "315_shears",
                        "asset_path": str(upstream_asset),
                        "models": [
                            {
                                "model_id": 0,
                                "usable": True,
                                "visual_path": str(upstream_asset / "visual" / "base0.glb"),
                                "collision_path": str(upstream_asset / "collision" / "base0.glb"),
                                "metadata_path": str(upstream_asset / "model_data0.json"),
                            }
                        ],
                    }
                ]
            }
        )
    )
    output = tmp_path / "runtime_load_report.json"

    code = _run_script_in_process(
        monkeypatch,
        [
            "--shadow",
            str(shadow),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(library),
        ],
    )

    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert "source identity" in captured.out
    assert ledger_path.read_bytes() == before
    assert not (ledger_path.parent / "verification_evidence").exists()


def test_s11_accepts_when_catalog_shadow_and_library_source_identity_match(
    tmp_path, monkeypatch, capsys
):
    library = tmp_path / "library"
    ledger_path, document = _write_file_backed_library_ledger(library, trusted_settle=True)
    asset = document["external_ids"]["env_gen"]
    asset_dir = ledger_path.parent
    (asset_dir / "model_data0.json").write_text("{}")

    shadow = tmp_path / "shadow"
    _write_runtime_loader_stubs(shadow)
    shadow_objects = shadow / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / asset).symlink_to(asset_dir, target_is_directory=True)
    catalog_model = {
        "model_id": 0,
        "usable": True,
        **_catalog_placement(document),
        "visual_path": str(shadow_objects / asset / "library-representation-0.ply"),
        "collision_path": str(shadow_objects / asset / "library-representation-1.ply"),
        "metadata_path": str(shadow_objects / asset / "model_data0.json"),
    }
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": asset,
                        "asset_path": str(shadow_objects / asset),
                        "models": [catalog_model],
                    }
                ]
            }
        )
    )
    output = tmp_path / "runtime_load_report.json"
    cwd_marker = tmp_path / "loader-cwd.txt"
    pose_marker = tmp_path / "loader-pose.json"
    monkeypatch.setenv("S11_CWD_MARKER", str(cwd_marker))
    monkeypatch.setenv("S11_POSE_MARKER", str(pose_marker))

    code = _run_script_in_process(
        monkeypatch,
        [
            "--shadow",
            str(shadow),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(library),
        ],
    )

    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    written = json.loads(ledger_path.read_text())
    receipt = written["models"][0]["verification"][-1]
    assert receipt["check"] == "runtime_load"
    assert (
        ledger.latest_trusted_verification(written["models"][0], "sapien", "runtime_load", asset)
        == receipt
    )
    snapshot_roots = list((asset_dir / "verification_inputs").glob("*.execution"))
    assert snapshot_roots
    assert any(Path(cwd_marker.read_text()).samefile(root) for root in snapshot_roots)
    assert (
        json.loads(pose_marker.read_text())
        == _catalog_placement(document)["stable_orientation_wxyz"]
    )
    evidence = json.loads(Path(ledger.resolve_uri(receipt["evidence"]["uri"])).read_text())
    loader_names = {entry["name"] for entry in evidence["capability"]["runtime"]["loader_modules"]}
    sapien_names = {entry["name"] for entry in evidence["capability"]["runtime"]["sapien_modules"]}
    assert "envs.executed_only" in loader_names
    assert "sapien.executed_only" in sapien_names


def test_s11_rejects_loader_that_returns_a_different_spawn_orientation(
    tmp_path, monkeypatch, capsys
):
    library = tmp_path / "library"
    ledger_path, document = _write_file_backed_library_ledger(library, trusted_settle=True)
    before = ledger_path.read_bytes()
    asset = document["external_ids"]["env_gen"]
    asset_dir = ledger_path.parent
    (asset_dir / "model_data0.json").write_text("{}")

    shadow = tmp_path / "shadow"
    _write_runtime_loader_stubs(shadow, returned_orientation=[1.0, 0.0, 0.0, 0.0])
    shadow_objects = shadow / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / asset).symlink_to(asset_dir, target_is_directory=True)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": asset,
                        "asset_path": str(shadow_objects / asset),
                        "models": [
                            {
                                "model_id": 0,
                                "usable": True,
                                **_catalog_placement(document),
                                "visual_path": str(
                                    shadow_objects / asset / "library-representation-0.ply"
                                ),
                                "collision_path": str(
                                    shadow_objects / asset / "library-representation-1.ply"
                                ),
                                "metadata_path": str(shadow_objects / asset / "model_data0.json"),
                            }
                        ],
                    }
                ]
            }
        )
    )
    output = tmp_path / "runtime_load_report.json"

    code = _run_script_in_process(
        monkeypatch,
        [
            "--shadow",
            str(shadow),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(library),
        ],
    )

    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert "spawn orientation" in captured.out
    written = json.loads(ledger_path.read_text())
    assert written != json.loads(before)
    receipt = written["models"][0]["verification"][-1]
    assert receipt["check"] == "runtime_load"
    assert receipt["verdict"] == "fail"
    evidence = json.loads(Path(ledger.resolve_uri(receipt["evidence"]["uri"])).read_text())
    assert evidence["result"]["loaded"] is False


def test_s11_refuses_catalog_stable_pose_or_z_policy_that_differs_from_ledger(
    tmp_path, monkeypatch, capsys
):
    library = tmp_path / "library"
    ledger_path, document = _write_file_backed_library_ledger(library, trusted_settle=True)
    before = ledger_path.read_bytes()
    asset = document["external_ids"]["env_gen"]
    asset_dir = ledger_path.parent
    (asset_dir / "model_data0.json").write_text("{}")
    shadow = tmp_path / "shadow"
    _write_runtime_loader_stubs(shadow)
    shadow_objects = shadow / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / asset).symlink_to(asset_dir, target_is_directory=True)
    catalog_model = {
        "model_id": 0,
        "usable": True,
        **_catalog_placement(document),
        "visual_path": str(shadow_objects / asset / "library-representation-0.ply"),
        "collision_path": str(shadow_objects / asset / "library-representation-1.ply"),
        "metadata_path": str(shadow_objects / asset / "model_data0.json"),
    }
    catalog_model["stable_orientation_wxyz"] = [1.0, 0.0, 0.0, 0.0]
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": asset,
                        "asset_path": str(shadow_objects / asset),
                        "models": [catalog_model],
                    }
                ]
            }
        )
    )
    output = tmp_path / "runtime_load_report.json"

    code = _run_script_in_process(
        monkeypatch,
        [
            "--shadow",
            str(shadow),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(library),
        ],
    )

    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert "stable placement" in captured.out
    assert ledger_path.read_bytes() == before


def test_s11_refuses_catalog_model_paths_that_drift_from_ledger_roles(
    tmp_path, monkeypatch, capsys
):
    library = tmp_path / "library"
    ledger_path, document = _write_file_backed_library_ledger(library, trusted_settle=True)
    before = ledger_path.read_bytes()
    asset = document["external_ids"]["env_gen"]
    asset_dir = ledger_path.parent
    (asset_dir / "model_data0.json").write_text("{}")

    shadow = tmp_path / "shadow"
    _write_runtime_loader_stubs(shadow)
    shadow_objects = shadow / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / asset).symlink_to(asset_dir, target_is_directory=True)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": asset,
                        "asset_path": str(shadow_objects / asset),
                        "models": [
                            {
                                "model_id": 0,
                                "usable": True,
                                "visual_path": str(
                                    shadow_objects / asset / "library-representation-1.ply"
                                ),
                                "collision_path": str(
                                    shadow_objects / asset / "library-representation-0.ply"
                                ),
                                "metadata_path": str(shadow_objects / asset / "model_data0.json"),
                            }
                        ],
                    }
                ]
            }
        )
    )
    output = tmp_path / "runtime_load_report.json"

    code = _run_script_in_process(
        monkeypatch,
        [
            "--shadow",
            str(shadow),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(library),
        ],
    )

    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert "source identity" in captured.out
    assert ledger_path.read_bytes() == before


def test_s11_refuses_unqualified_library_model_even_when_paths_match(tmp_path, monkeypatch, capsys):
    library = tmp_path / "library"
    ledger_path, document = _write_file_backed_library_ledger(library, trusted_settle=False)
    before = ledger_path.read_bytes()
    asset = document["external_ids"]["env_gen"]
    asset_dir = ledger_path.parent
    (asset_dir / "model_data0.json").write_text("{}")

    shadow = tmp_path / "shadow"
    _write_runtime_loader_stubs(shadow)
    shadow_objects = shadow / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / asset).symlink_to(asset_dir, target_is_directory=True)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": asset,
                        "asset_path": str(shadow_objects / asset),
                        "models": [
                            {
                                "model_id": 0,
                                "usable": True,
                                "visual_path": str(
                                    shadow_objects / asset / "library-representation-0.ply"
                                ),
                                "collision_path": str(
                                    shadow_objects / asset / "library-representation-1.ply"
                                ),
                                "metadata_path": str(shadow_objects / asset / "model_data0.json"),
                            }
                        ],
                    }
                ]
            }
        )
    )
    output = tmp_path / "runtime_load_report.json"

    code = _run_script_in_process(
        monkeypatch,
        [
            "--shadow",
            str(shadow),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(library),
        ],
    )

    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert "trusted settle/pass" in captured.out
    assert ledger_path.read_bytes() == before


def test_s11_refuses_catalog_model_id_absent_from_library(tmp_path, monkeypatch, capsys):
    library = tmp_path / "library"
    ledger_path, document = _write_file_backed_library_ledger(library, trusted_settle=True)
    before = ledger_path.read_bytes()
    asset = document["external_ids"]["env_gen"]
    asset_dir = ledger_path.parent

    shadow = tmp_path / "shadow"
    _write_runtime_loader_stubs(shadow)
    shadow_objects = shadow / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / asset).symlink_to(asset_dir, target_is_directory=True)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": asset,
                        "asset_path": str(shadow_objects / asset),
                        "models": [{"model_id": 1, "usable": True}],
                    }
                ]
            }
        )
    )
    output = tmp_path / "runtime_load_report.json"

    code = _run_script_in_process(
        monkeypatch,
        [
            "--shadow",
            str(shadow),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(library),
        ],
    )

    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert "no model_id=1" in captured.out
    assert ledger_path.read_bytes() == before


def test_s11_skips_unusable_catalog_model_without_importing_loader(tmp_path, monkeypatch, capsys):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": "315_shears",
                        "asset_path": str(tmp_path / "unused"),
                        "models": [{"model_id": 0, "usable": False}],
                    }
                ]
            }
        )
    )
    output = tmp_path / "runtime_load_report.json"

    code = _run_script_in_process(
        monkeypatch,
        [
            "--shadow",
            str(tmp_path / "shadow-without-sapien"),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(tmp_path / "library"),
        ],
    )

    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    assert json.loads(output.read_text()) == []


def test_s11_accepts_urdf_catalog_when_source_identity_matches(tmp_path, monkeypatch, capsys):
    library = tmp_path / "library"
    ledger_path, document = _write_file_backed_library_ledger(
        library, trusted_settle=True, urdf=True
    )
    asset = document["external_ids"]["env_gen"]
    asset_dir = ledger_path.parent

    shadow = tmp_path / "shadow"
    _write_runtime_loader_stubs(shadow)
    shadow_objects = shadow / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / asset).symlink_to(asset_dir, target_is_directory=True)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": asset,
                        "asset_path": str(shadow_objects / asset),
                        "models": [
                            {
                                "model_id": 0,
                                "usable": True,
                                "urdf_path": str(
                                    shadow_objects / asset / "library-representation-0.ply"
                                ),
                            }
                        ],
                    }
                ]
            }
        )
    )
    output = tmp_path / "runtime_load_report.json"

    code = _run_script_in_process(
        monkeypatch,
        [
            "--shadow",
            str(shadow),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(library),
        ],
    )

    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    written = json.loads(ledger_path.read_text())
    assert written["models"][0]["verification"][-1]["check"] == "runtime_load"


def test_s11_records_runtime_load_failure_when_loader_returns_none(tmp_path, monkeypatch, capsys):
    library = tmp_path / "library"
    ledger_path, document = _write_file_backed_library_ledger(library, trusted_settle=True)
    asset = document["external_ids"]["env_gen"]
    asset_dir = ledger_path.parent

    shadow = tmp_path / "shadow"
    _write_runtime_loader_stubs(shadow, actor_none=True)
    shadow_objects = shadow / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / asset).symlink_to(asset_dir, target_is_directory=True)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": asset,
                        "asset_path": str(shadow_objects / asset),
                        "models": [
                            {
                                "model_id": 0,
                                "usable": True,
                                "visual_path": str(
                                    shadow_objects / asset / "library-representation-0.ply"
                                ),
                                "collision_path": str(
                                    shadow_objects / asset / "library-representation-1.ply"
                                ),
                            }
                        ],
                    }
                ]
            }
        )
    )
    output = tmp_path / "runtime_load_report.json"

    code = _run_script_in_process(
        monkeypatch,
        [
            "--shadow",
            str(shadow),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(library),
        ],
    )

    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    written = json.loads(ledger_path.read_text())
    receipt = written["models"][0]["verification"][-1]
    assert receipt["check"] == "runtime_load"
    assert receipt["verdict"] == "fail"


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: ledger_writes.LedgerWriteError(
            [SimpleNamespace(path="models.0.verification", code="bad_fact", message="bad")]
        ),
        lambda: ledger.VerificationConflictError("conflicting verification fact"),
    ],
)
def test_s11_reports_writeback_failures_after_evidence_preparation(
    tmp_path, monkeypatch, capsys, error_factory
):
    library = tmp_path / "library"
    ledger_path, document = _write_file_backed_library_ledger(library, trusted_settle=True)
    asset = document["external_ids"]["env_gen"]
    asset_dir = ledger_path.parent

    shadow = tmp_path / "shadow"
    _write_runtime_loader_stubs(shadow)
    shadow_objects = shadow / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / asset).symlink_to(asset_dir, target_is_directory=True)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": asset,
                        "asset_path": str(shadow_objects / asset),
                        "models": [
                            {
                                "model_id": 0,
                                "usable": True,
                                "visual_path": str(
                                    shadow_objects / asset / "library-representation-0.ply"
                                ),
                                "collision_path": str(
                                    shadow_objects / asset / "library-representation-1.ply"
                                ),
                            }
                        ],
                    }
                ]
            }
        )
    )
    output = tmp_path / "runtime_load_report.json"

    def fail_append(*args, **kwargs):
        raise error_factory()

    monkeypatch.setattr(ledger_writes, "append_validated_verification", fail_append)
    code = _run_script_in_process(
        monkeypatch,
        [
            "--shadow",
            str(shadow),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(library),
        ],
    )

    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert "FAIL backfill" in captured.out
    assert json.loads(output.read_text())[0]["writeback_error"]
    written = json.loads(ledger_path.read_text())
    assert written["models"][0]["verification"][-1]["check"] == "settle"


def test_s11_rechecks_source_identity_after_replay_before_publishing_evidence(
    tmp_path, monkeypatch, capsys
):
    library = tmp_path / "library"
    ledger_path, document = _write_file_backed_library_ledger(library, trusted_settle=True)
    before = ledger_path.read_bytes()
    asset = document["external_ids"]["env_gen"]
    asset_dir = ledger_path.parent
    (asset_dir / "model_data0.json").write_text("{}")
    evidence_before = {path.name for path in (asset_dir / "verification_evidence").iterdir()}

    shadow = tmp_path / "shadow"
    _write_runtime_loader_stubs(shadow, mutate_path=asset_dir / "library-representation-0.ply")
    shadow_objects = shadow / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / asset).symlink_to(asset_dir, target_is_directory=True)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": asset,
                        "asset_path": str(shadow_objects / asset),
                        "models": [
                            {
                                "model_id": 0,
                                "usable": True,
                                "visual_path": str(
                                    shadow_objects / asset / "library-representation-0.ply"
                                ),
                                "collision_path": str(
                                    shadow_objects / asset / "library-representation-1.ply"
                                ),
                                "metadata_path": str(shadow_objects / asset / "model_data0.json"),
                            }
                        ],
                    }
                ]
            }
        )
    )
    output = tmp_path / "runtime_load_report.json"

    code = _run_script_in_process(
        monkeypatch,
        [
            "--shadow",
            str(shadow),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(library),
        ],
    )

    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert "source identity" in captured.out
    assert ledger_path.read_bytes() == before
    assert {
        path.name for path in (asset_dir / "verification_evidence").iterdir()
    } == evidence_before


def test_s11_rechecks_catalog_metadata_digest_after_replay_before_writeback(
    tmp_path, monkeypatch, capsys
):
    library = tmp_path / "library"
    ledger_path, document = _write_file_backed_library_ledger(library, trusted_settle=True)
    before = ledger_path.read_bytes()
    asset = document["external_ids"]["env_gen"]
    asset_dir = ledger_path.parent
    metadata = asset_dir / "model_data0.json"
    metadata.write_text("{}")
    evidence_before = {path.name for path in (asset_dir / "verification_evidence").iterdir()}

    shadow = tmp_path / "shadow"
    _write_runtime_loader_stubs(shadow, mutate_path=metadata)
    shadow_objects = shadow / "assets" / "objects"
    shadow_objects.mkdir(parents=True)
    (shadow_objects / asset).symlink_to(asset_dir, target_is_directory=True)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": asset,
                        "asset_path": str(shadow_objects / asset),
                        "models": [
                            {
                                "model_id": 0,
                                "usable": True,
                                "visual_path": str(
                                    shadow_objects / asset / "library-representation-0.ply"
                                ),
                                "collision_path": str(
                                    shadow_objects / asset / "library-representation-1.ply"
                                ),
                                "metadata_path": str(shadow_objects / asset / "model_data0.json"),
                            }
                        ],
                    }
                ]
            }
        )
    )
    output = tmp_path / "runtime_load_report.json"

    code = _run_script_in_process(
        monkeypatch,
        [
            "--shadow",
            str(shadow),
            "--catalog",
            str(catalog),
            "--out",
            str(output),
            "--library-dir",
            str(library),
        ],
    )

    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert "source identity mismatch after replay" in captured.out
    assert ledger_path.read_bytes() == before
    assert {
        path.name for path in (asset_dir / "verification_evidence").iterdir()
    } == evidence_before
