import hashlib
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "4_validate" / "s13b_validate_articulated.py"
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger  # noqa: E402


def test_validation_report_precedes_receipt_but_is_not_its_mutable_trust_input():
    source = SCRIPT.read_text()
    report_publish = source.index('ledger._atomic_write_json(out / "cabinet314_validation.json"')
    evidence_issue = source.index("ledger_writes.issue_qualified_verification")
    ledger_publish = source.index("ledger_writes.write_validated")

    assert report_publish < evidence_issue < ledger_publish
    assert '"validation_report": ledger_writes.provenance_file_record' not in source


def test_urdf_runtime_uses_prehashed_snapshot_and_postchecks_original_closure():
    source = SCRIPT.read_text()
    closure = source.index('"files": ledger_writes.representation_files(inst / "mobility.urdf")')
    snapshot = source.index("execution_snapshot = ledger_writes.publish_execution_snapshot")
    fixed_load = source.index("art = loader.load(str(execution_urdf))")
    postcheck = source.index("ledger.artifact_file_record_is_current(record)")
    evidence_issue = source.index("ledger_writes.issue_qualified_verification")

    assert closure < snapshot < fixed_load < postcheck < evidence_issue
    assert 'loader.load(str(inst / "mobility.urdf"))' not in source


def test_articulated_validation_requires_finite_root_and_every_link_pose():
    source = SCRIPT.read_text()

    assert "def articulation_poses_are_finite(articulation):" in source
    assert '"root_pose_finite"' in source
    assert '"link_poses_finite"' in source
    assert "articulation_poses_are_finite(art)" in source


def test_non_numeric_instance_dir_fails_fast(tmp_path):
    # T6 fix round 1 (I-2): a non-numeric --instance-dir leaf must not
    # silently default model_id to 0 -- upsert_model's re-import semantics
    # (same model_id = wholesale replace) would let that clobber an
    # existing model 0. The check runs before any SAPIEN/scene work, so
    # this only needs a real `sapien` import (fast, no GPU/display touched)
    # plus a minimal export_report.json -- no real URDF/USD data required.
    inst = tmp_path / "314_cabinet" / "notanumber"
    inst.mkdir(parents=True)
    (inst / "export_report.json").write_text(
        json.dumps({"joints_movable": 1, "bbox_m": [0.6, 0.4, 0.8]})
    )
    lib_dir = tmp_path / "asset_library"
    out = tmp_path / "out"

    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--instance-dir",
            str(inst),
            "--source-usd",
            str(tmp_path / "does_not_need_to_exist.usd"),
            "--out",
            str(out),
            "--library-dir",
            str(lib_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL s13b" in r.stdout
    assert "not numeric" in r.stdout
    assert not (lib_dir / "314_cabinet" / "ledger.json").exists()


def test_noncanonical_numeric_instance_dir_fails_fast(tmp_path):
    inst = tmp_path / "314_cabinet" / "00"
    inst.mkdir(parents=True)
    (inst / "export_report.json").write_text(
        json.dumps({"joints_movable": 1, "bbox_m": [0.6, 0.4, 0.8]})
    )
    lib_dir = tmp_path / "asset_library"
    out = tmp_path / "out"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--instance-dir",
            str(inst),
            "--source-usd",
            str(tmp_path / "unused.usd"),
            "--out",
            str(out),
            "--library-dir",
            str(lib_dir),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "FAIL s13b" in result.stdout
    assert not lib_dir.exists()
    assert not out.exists()


def test_source_usd_must_match_the_export_report_before_runtime_execution(tmp_path):
    inst = tmp_path / "314_cabinet" / "0"
    inst.mkdir(parents=True)
    (inst / "mobility.urdf").write_text("<robot name='fixture'/>")
    exported_source = tmp_path / "exported.usd"
    exported_source.write_bytes(b"exported source")
    supplied_source = tmp_path / "unrelated.usd"
    # Content identity alone is insufficient provenance: the export report
    # names the source file that s13a actually consumed.  A byte-for-byte copy
    # at another path must not be registered as that input.
    supplied_source.write_bytes(exported_source.read_bytes())
    (inst / "export_report.json").write_text(
        json.dumps(
            {
                "joints_movable": 0,
                "bbox_m": [0.1, 0.1, 0.1],
                "source_usd": str(exported_source),
                "source_usd_sha256": hashlib.sha256(exported_source.read_bytes()).hexdigest(),
                "links": [],
                "movable": [],
            }
        )
    )
    library = tmp_path / "library"
    out = tmp_path / "out"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--instance-dir",
            str(inst),
            "--source-usd",
            str(supplied_source),
            "--out",
            str(out),
            "--library-dir",
            str(library),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "source USD does not match export report" in result.stdout
    assert not library.exists()
    assert not out.exists()


# Minimal URDF (primitive box geometry, no mesh files) so SAPIEN can load and
# step it without any real cabinet asset data -- one revolute joint (dof=1)
# matching a hand-written export_report.json below.
_MINI_URDF = """<?xml version="1.0"?>
<robot name="mini_cabinet">
  <link name="base">
    <visual><geometry><box size="0.3 0.3 0.5"/></geometry></visual>
    <collision><geometry><box size="0.3 0.3 0.5"/></geometry></collision>
    <inertial><mass value="1.0"/>
      <inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/>
    </inertial>
  </link>
  <link name="door">
    <visual><geometry><box size="0.02 0.2 0.3"/></geometry></visual>
    <collision><geometry><box size="0.02 0.2 0.3"/></geometry></collision>
    <inertial><mass value="0.2"/>
      <inertia ixx="0.001" ixy="0" ixz="0" iyy="0.001" iyz="0" izz="0.001"/>
    </inertial>
  </link>
  <joint name="door_hinge" type="revolute">
    <parent link="base"/>
    <child link="door"/>
    <origin xyz="0.2 0 0" rpy="0 0 0"/>
    <axis xyz="0 0 1"/>
    <limit lower="0" upper="1.57" effort="10" velocity="1"/>
  </joint>
</robot>
"""


def _run_valid_fixture(tmp_path):
    asset = "314_cabinet"
    inst = tmp_path / asset / "0"
    inst.mkdir(parents=True)
    (inst / "mobility.urdf").write_text(_MINI_URDF)
    source_payload = b"#usda 1.0\n"
    (inst / "export_report.json").write_text(
        json.dumps(
            {
                "joints_movable": 1,
                "bbox_m": [0.3, 0.3, 0.5],
                "source_usd": "dummy_src.usd",
                "source_usd_sha256": hashlib.sha256(source_payload).hexdigest(),
                "links": ["base", "door"],
                "movable": [
                    {
                        "name": "door_hinge",
                        "type": "revolute",
                        "lower": 0.0,
                        "upper": 1.57,
                    }
                ],
            }
        )
    )
    source_usd = tmp_path / "dummy_src.usd"
    source_usd.write_bytes(source_payload)
    lib_dir = tmp_path / "asset_library"
    lib_dir.mkdir()
    out = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--instance-dir",
            str(inst),
            "--source-usd",
            str(source_usd),
            "--out",
            str(out),
            "--library-dir",
            str(lib_dir),
        ],
        capture_output=True,
        text=True,
    )
    return result, lib_dir / asset / "ledger.json"


def test_successful_validation_writes_complete_file_backed_v3_ledger(tmp_path):
    pytest.importorskip("sapien", reason="articulated runtime validation requires SAPIEN")

    result, ledger_path = _run_valid_fixture(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads(ledger_path.read_text())
    model = document["models"][0]
    assert document["external_ids"]["env_gen"] == "314_cabinet"
    assert all("size_bytes" not in representation for representation in model["representations"])
    assert all(representation["files"] for representation in model["representations"])
    assert all(
        representation["collision_meta"]["mode"] == "unknown"
        for representation in model["representations"]
    )
    assert model["physical"]["conventions"]["stable_poses"][0]["measured_against"] == {
        "backend": "sapien",
        "run_id": "out",
    }
    receipt = model["verification"][0]
    assert "report_path" not in receipt
    assert (
        ledger.latest_trusted_verification(model, "sapien", "joint_sweep", "314_cabinet") == receipt
    )
    assert ledger.validate_ledger(document, check_files=True) == []


def _write_articulated_runtime_stub(stubs):
    package = stubs / "sapien"
    package.mkdir()
    (package / "_native.so").write_bytes(b"test-native-runtime")
    (package / "__init__.py").write_text(
        """
import pathlib
import sys
import types
import numpy as np

_native = types.ModuleType("sapien._native")
_native.__file__ = str(pathlib.Path(__file__).with_name("_native.so"))
sys.modules["sapien._native"] = _native


class Pose:
    def __init__(self, p=None, q=None):
        self.p = [0.0, 0.0, 0.0] if p is None else list(p)
        self.q = [1.0, 0.0, 0.0, 0.0] if q is None else list(q)


class _Joint:
    type = "revolute"
    def get_limits(self):
        return np.array([[0.0, 1.57]])


class _Link:
    def get_pose(self):
        return Pose()


class _Articulation:
    dof = 1
    def __init__(self):
        self._qpos = np.array([0.0])
    def get_active_joints(self):
        return [_Joint()]
    def get_qpos(self):
        return self._qpos.copy()
    def set_qpos(self, value):
        self._qpos = np.asarray(value, dtype=float)
    def get_pose(self):
        return Pose()
    def get_links(self):
        return [_Link(), _Link()]


class _Loader:
    fix_root_link = False
    def load(self, _path):
        return _Articulation()


class _CameraEntity:
    def set_pose(self, _pose):
        pass


class _Camera:
    entity = _CameraEntity()
    def take_picture(self):
        pass
    def get_picture(self, _name):
        image = np.zeros((8, 8, 4), dtype=float)
        image[:, :4, :3] = 1.0
        return image


class Scene:
    def set_timestep(self, _value):
        pass
    def add_ground(self, _value):
        pass
    def set_ambient_light(self, _value):
        pass
    def add_directional_light(self, _direction, _color):
        pass
    def create_urdf_loader(self):
        return _Loader()
    def step(self):
        pass
    def add_camera(self, *_args):
        return _Camera()
    def update_render(self):
        pass
"""
    )


def test_cli_accepts_finite_articulation_and_publishes_a_trusted_joint_sweep_receipt(
    tmp_path, monkeypatch
):
    """A finite, stable articulated runtime publishes an evidence-bound receipt."""

    asset = "314_cabinet"
    inst = tmp_path / asset / "7"
    inst.mkdir(parents=True)
    (inst / "mobility.urdf").write_text(_MINI_URDF)
    source_payload = b"#usda 1.0\n"
    (inst / "export_report.json").write_text(
        json.dumps(
            {
                "joints_movable": 1,
                "bbox_m": [0.3, 0.3, 0.5],
                "source_usd": "source.usd",
                "source_usd_sha256": hashlib.sha256(source_payload).hexdigest(),
                "links": ["base", "door"],
                "movable": [
                    {"name": "door_hinge", "type": "revolute", "lower": 0.0, "upper": 1.57}
                ],
            }
        )
    )
    source_usd = tmp_path / "source.usd"
    source_usd.write_bytes(source_payload)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _write_articulated_runtime_stub(stubs)
    out = tmp_path / "out"
    library = tmp_path / "library"
    library.mkdir()

    old_path = list(sys.path)
    cwd = Path.cwd()
    for name in ("sapien", "sapien._native"):
        sys.modules.pop(name, None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--instance-dir",
            str(inst),
            "--source-usd",
            str(source_usd),
            "--out",
            str(out),
            "--library-dir",
            str(library),
        ],
    )
    sys.path.insert(0, str(stubs))
    try:
        with pytest.raises(SystemExit) as stopped:
            runpy.run_path(str(SCRIPT), run_name="__main__")
    finally:
        os.chdir(cwd)
        sys.path[:] = old_path
        for name in ("sapien", "sapien._native"):
            sys.modules.pop(name, None)

    assert stopped.value.code == 0
    document = json.loads((library / asset / "ledger.json").read_text())
    model = document["models"][0]
    receipt = ledger.latest_trusted_verification(model, "sapien", "joint_sweep", asset)
    assert receipt is not None
    assert receipt["verdict"] == "pass"
    conventions = model["physical"]["conventions"]
    pose = conventions["stable_poses"][0]
    assert conventions["is_static"] is True
    assert pose["pose_id"] == "fixed_root_identity"
    evidence = json.loads(Path(ledger.resolve_uri(receipt["evidence"]["uri"])).read_text())
    assert evidence["physical_facts_digest"] == ledger.settle_physical_facts_digest(model)
    assert evidence["result"]["fix_root_link"] is True
    assert evidence["result"]["root_pose_id"] == pose["pose_id"]
    assert evidence["result"]["root_orientation_wxyz"] == pose["orientation_wxyz"]
    assert evidence["result"]["z_policy"] == conventions["z_policy"]
    assert (inst / "model_data7.json").is_file()
    assert not (inst / "model_data0.json").exists()
    assert ledger.validate_ledger(document, check_files=True) == []


def test_violations_block_ledger_write_but_snapshot_stays(tmp_path):
    pytest.importorskip("sapien", reason="articulated runtime validation requires SAPIEN")
    # H3: s13b's gate must align with import_materialize's own gate -- a
    # ledger that fails validate_ledger must NOT become authoritative
    # (formerly: WARN and write anyway). Trigger a genuine violation without
    # touching any script internals: pre-seed the asset's ledger.json with
    # an already-broken sibling model (model_id=0, missing nearly every
    # REQUIRED_MODEL field) before this run registers a second, well-formed
    # model (model_id=1) -- validate_ledger runs over the WHOLE merged
    # ledger (see s13b's `ledger.validate_ledger(led, check_files=False)`),
    # so model 0's pre-existing corruption is what fails the gate here, not
    # anything about this run's own physics.
    asset = "314_cabinet"
    inst = tmp_path / asset / "1"
    inst.mkdir(parents=True)
    (inst / "mobility.urdf").write_text(_MINI_URDF)
    source_payload = b"#usda 1.0\n"
    (inst / "export_report.json").write_text(
        json.dumps(
            {
                "joints_movable": 1,
                "bbox_m": [0.3, 0.3, 0.5],
                "source_usd": "dummy_src.usd",
                "source_usd_sha256": hashlib.sha256(source_payload).hexdigest(),
                "links": ["base", "door"],
                "movable": [
                    {
                        "name": "door_hinge",
                        "type": "revolute",
                        "lower": 0.0,
                        "upper": 1.57,
                    }
                ],
            }
        )
    )
    source_usd = tmp_path / "dummy_src.usd"
    source_usd.write_bytes(source_payload)

    lib_dir = tmp_path / "asset_library"
    asset_dir = lib_dir / asset
    asset_dir.mkdir(parents=True)
    lp = asset_dir / "ledger.json"
    # v2-shaped, and matching what s13b itself would write for this asset --
    # the asset-level fields have to agree or upsert_model raises on drift
    # before the run ever reaches the schema gate this test is about.
    seed_ledger = {
        "schema_version": "asset_ledger.v3",
        "asset_id": f"external_{asset}",
        "external_ids": {"env_gen": asset},
        "category": "cabinet",
        "kind": "articulated",
        "profile": "cross_backend",
        "semantics": {
            "aliases": ["cabinet"],
            "colors": [],
            "materials": [],
            "identity": {"basis": "unknown", "evidence": None, "verified": False},
        },
        "models": [{"model_id": 0}],  # deliberately broken sibling
    }
    lp.write_text(json.dumps(seed_ledger, indent=2) + "\n")
    before = lp.read_text()

    out = tmp_path / "out"
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--instance-dir",
            str(inst),
            "--source-usd",
            str(source_usd),
            "--out",
            str(out),
            "--library-dir",
            str(lib_dir),
        ],
        capture_output=True,
        text=True,
    )
    # I-3 (review round 1): a ledger violation must fail the run loudly --
    # not just skip the write silently. FAIL headline on stdout (this
    # script's own style), returncode != 0, detail trace on stderr.
    assert r.returncode != 0, r.stdout + r.stderr
    assert "FAIL s13b: schema violations" in r.stdout
    assert "NOT writing authoritative ledger" in r.stderr

    # authoritative ledger untouched -- still just the seeded broken model 0,
    # model 1 never got persisted.
    assert lp.read_text() == before
    on_disk = json.loads(lp.read_text())
    assert [m["model_id"] for m in on_disk["models"]] == [0]

    # run snapshot (pool-layer record, not the authoritative ledger) is
    # still written regardless of the violation.
    bundle = json.loads((out / "cabinet314_bundle.json").read_text())
    assert bundle["asset_id"] == f"external_{asset}_m1"
    assert (out / "cabinet314_validation.json").exists()
