import hashlib
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "3_materialize" / "import_materialize.py"


@pytest.mark.parametrize(
    "records",
    [
        [{"asset": "../escape", "model": 0}],
        [{"asset": "315_shears", "model": "0"}],
        [
            {"asset": "315_shears", "model": 0},
            {"asset": "315_shears", "model": 0},
        ],
    ],
)
def test_materialize_rejects_unsafe_or_duplicate_staging_identity_before_file_writes(
    tmp_path, records
):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "staging_manifest.json").write_text(json.dumps(records))
    stub_modules = tmp_path / "stubs"
    stub_modules.mkdir()
    (stub_modules / "sapien.py").write_text("# early trust-gate import stub\n")
    library = tmp_path / "library"
    library.mkdir()
    output = tmp_path / "out"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((str(stub_modules), str(ROOT)))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--staging",
            str(staging),
            "--library-dir",
            str(library),
            "--out",
            str(output),
            "--overrides-fragment",
            str(tmp_path / "fragment.yml"),
            "--identity-basis",
            "manifest_human",
        ],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert not any(library.iterdir())
    assert not output.exists()


def test_materialize_settle_gate_uses_the_complete_runtime_actor_aabb():
    source = SCRIPT.read_text()

    assert "def actor_min_world_z(actor):" in source
    assert "actor.get_global_aabb()" in source
    assert "sample_pts" not in source


def test_worker_mode_records_a_failed_conversion_without_creating_an_authoritative_asset(
    tmp_path, monkeypatch
):
    """The public worker CLI must preserve a failed conversion as run evidence only."""

    staging = tmp_path / "staging"
    staging.mkdir()
    staging_record = {
        "asset": "315_shears",
        "model": 0,
        "usd": "shears.usd",
        "group": "rigid_props",
        "status": "failed",
        "error": "converter rejected source",
    }
    (staging / "staging_manifest.json").write_text(json.dumps([staging_record]))
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    # This test never reaches the simulator or mesh APIs: the stubs prove the
    # conversion-status gate runs before either optional runtime is used.
    (stubs / "sapien.py").write_text("# import-only simulator stub\n")
    (stubs / "trimesh.py").write_text("# import-only mesh stub\n")
    library = tmp_path / "library"
    output = tmp_path / "out"
    fragment = tmp_path / "generated" / "fragment.yml"

    old_path = list(sys.path)
    for name in ("sapien", "trimesh", "gen_fragment"):
        sys.modules.pop(name, None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--staging",
            str(staging),
            "--library-dir",
            str(library),
            "--out",
            str(output),
            "--overrides-fragment",
            str(fragment),
            "--identity-basis",
            "manifest_human",
            "--only-index",
            "0",
        ],
    )
    sys.path.insert(0, str(stubs))
    try:
        with pytest.raises(SystemExit) as stopped:
            runpy.run_path(str(SCRIPT), run_name="__main__")
    finally:
        sys.path[:] = old_path
        for name in ("sapien", "trimesh", "gen_fragment"):
            sys.modules.pop(name, None)

    assert stopped.value.code == 0
    row = json.loads((output / "rows" / "row_0.json").read_text())
    assert row == {
        "asset": "315_shears",
        "model": 0,
        "usd": "shears.usd",
        "category": None,
        "status": "rejected",
        "reasons": ["converter rejected source"],
    }
    assert not (library / "nvidia" / "315_shears" / "ledger.json").exists()


def _write_materialize_runtime_stub(stubs, *, min_z=0.0):
    package = stubs / "sapien"
    package.mkdir()
    (package / "_native.so").write_bytes(b"test-native-runtime")
    source = """
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


class _Actor:
    def __init__(self):
        self._pose = Pose()
    def set_pose(self, pose):
        self._pose = pose
    def get_pose(self):
        return self._pose
    def get_global_aabb(self):
        return np.array([[-0.05, -0.05, MIN_Z], [0.05, 0.05, 0.2]])


class _Builder:
    def add_multiple_convex_collisions_from_file(self, **_kwargs):
        pass
    def add_visual_from_file(self, **_kwargs):
        pass
    def build(self, **_kwargs):
        return _Actor()


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
    def create_actor_builder(self):
        return _Builder()
    def step(self):
        pass
    def add_camera(self, *_args):
        return _Camera()
    def update_render(self):
        pass
"""
    (package / "__init__.py").write_text(source.replace("MIN_Z", repr(min_z)))


def test_worker_cli_publishes_only_a_snapshot_bound_settle_receipt_for_a_passing_model(
    tmp_path, monkeypatch
):
    """Successful materialization is admitted only with its immutable replay evidence."""

    trimesh = pytest.importorskip("trimesh")
    staging = tmp_path / "staging"
    staging.mkdir()
    source_glb = staging / "converted.glb"
    trimesh.creation.box(extents=[0.1, 0.2, 0.1]).export(source_glb)
    source_usd = staging / "source.usd"
    source_usd.write_text("#usda 1.0\n")
    source_digest = hashlib.sha256(source_usd.read_bytes()).hexdigest()
    record = {
        "asset": "315_shears",
        "model": 0,
        "usd": "source.usd",
        "usd_local": str(source_usd),
        "usd_sha256": source_digest,
        "glb": str(source_glb),
        "group": "rigid_props",
        "status": "converted",
        "category": "tool",
        "aliases": ["shears"],
        "size_policy": "none",
    }
    (staging / "staging_manifest.json").write_text(json.dumps([record]))
    reference_catalog = tmp_path / "reference_catalog.json"
    reference_catalog.write_text(json.dumps({"entries": []}))
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _write_materialize_runtime_stub(stubs)
    library = tmp_path / "library"
    library.mkdir()
    output = tmp_path / "out"

    old_path = list(sys.path)
    for name in ("sapien", "sapien._native", "gen_fragment"):
        sys.modules.pop(name, None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--staging",
            str(staging),
            "--library-dir",
            str(library),
            "--out",
            str(output),
            "--overrides-fragment",
            str(tmp_path / "fragment.yml"),
            "--identity-basis",
            "manifest_human",
            "--reference-catalog",
            str(reference_catalog),
            "--only-index",
            "0",
        ],
    )
    sys.path.insert(0, str(stubs))
    try:
        with pytest.raises(SystemExit) as stopped:
            runpy.run_path(str(SCRIPT), run_name="__main__")
    finally:
        sys.path[:] = old_path
        for name in ("sapien", "sapien._native", "gen_fragment"):
            sys.modules.pop(name, None)

    assert stopped.value.code == 0
    from lib import ledger

    document = json.loads((library / "nvidia" / "315_shears" / "ledger.json").read_text())
    model = document["models"][0]
    receipt = ledger.latest_trusted_verification(model, "sapien", "settle", "315_shears")
    assert receipt is not None
    assert receipt["verdict"] == "pass"
    assert ledger.validate_ledger(document, check_files=True) == []


def test_worker_cli_rejects_a_model_whose_complete_runtime_aabb_penetrates_ground(
    tmp_path, monkeypatch
):
    """The materialize CLI must reject a loadable mesh when the actor AABB penetrates ground."""

    trimesh = pytest.importorskip("trimesh")
    staging = tmp_path / "staging"
    staging.mkdir()
    source_glb = staging / "converted.glb"
    trimesh.creation.box(extents=[0.1, 0.2, 0.1]).export(source_glb)
    source_usd = staging / "source.usd"
    source_usd.write_text("#usda 1.0\n")
    record = {
        "asset": "315_shears",
        "model": 0,
        "usd": "source.usd",
        "usd_local": str(source_usd),
        "usd_sha256": hashlib.sha256(source_usd.read_bytes()).hexdigest(),
        "glb": str(source_glb),
        "group": "rigid_props",
        "status": "converted",
        "category": "tool",
        "aliases": ["shears"],
        "size_policy": "none",
    }
    (staging / "staging_manifest.json").write_text(json.dumps([record]))
    reference_catalog = tmp_path / "reference_catalog.json"
    reference_catalog.write_text(json.dumps({"entries": []}))
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _write_materialize_runtime_stub(stubs, min_z=-0.01)
    library = tmp_path / "library"
    library.mkdir()
    output = tmp_path / "out"

    old_path = list(sys.path)
    for name in ("sapien", "sapien._native", "gen_fragment"):
        sys.modules.pop(name, None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--staging",
            str(staging),
            "--library-dir",
            str(library),
            "--out",
            str(output),
            "--overrides-fragment",
            str(tmp_path / "fragment.yml"),
            "--identity-basis",
            "manifest_human",
            "--reference-catalog",
            str(reference_catalog),
            "--only-index",
            "0",
        ],
    )
    sys.path.insert(0, str(stubs))
    try:
        with pytest.raises(SystemExit) as stopped:
            runpy.run_path(str(SCRIPT), run_name="__main__")
    finally:
        sys.path[:] = old_path
        for name in ("sapien", "sapien._native", "gen_fragment"):
            sys.modules.pop(name, None)

    assert stopped.value.code == 0
    row = json.loads((output / "rows" / "row_0.json").read_text())
    assert row["status"] == "rejected"
    assert row["trusted_snapshot_check"]["no_penetration"] is False
    cleanup_candidate = row["cleanup_candidate"]
    assert cleanup_candidate["schema"] == "asset_materialize_cleanup_candidate.v1"
    assert cleanup_candidate["model_id"] == 0
    assert cleanup_candidate["ledger_before_model_sha256"] is None
    assert cleanup_candidate["ledger_write_committed"] is False
    assert len(cleanup_candidate["model_sha256"]) == 64
    assert {record["path"] for record in cleanup_candidate["files"]} == {
        "collision/base0.glb",
        "model_data0.json",
        "visual/base0.glb",
    }
    for record in cleanup_candidate["files"]:
        assert (
            hashlib.sha256(
                (library / "nvidia" / "315_shears" / record["path"]).read_bytes()
            ).hexdigest()
            == record["sha256"]
        )
    assert not (library / "nvidia" / "315_shears" / "ledger.json").exists()


@pytest.mark.parametrize(
    "worker_writes_model_file",
    [False, True],
    ids=["no-worker-output", "partial-worker-output"],
)
def test_driver_records_missing_worker_result_as_rejected_and_quarantines_the_asset(
    tmp_path, monkeypatch, worker_writes_model_file
):
    """A crashed worker can never leave an unreviewed asset visible to catalog consumers."""

    staging = tmp_path / "staging"
    staging.mkdir()
    record = {
        "asset": "315_shears",
        "model": 0,
        "usd": "source.usd",
        "group": "rigid_props",
        "status": "converted",
    }
    (staging / "staging_manifest.json").write_text(json.dumps([record]))
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "sapien.py").write_text("# import-only simulator stub\n")
    (stubs / "trimesh.py").write_text("# import-only mesh stub\n")
    library = tmp_path / "library"
    library.mkdir()
    output = tmp_path / "out"
    fragment = tmp_path / "fragment.yml"

    def crashed_worker(*_args, **_kwargs):
        if worker_writes_model_file:
            leftover = library / "nvidia" / "315_shears" / "visual" / "base0.glb"
            leftover.parent.mkdir(parents=True, exist_ok=True)
            leftover.write_bytes(b"partial-worker-output")
        return SimpleNamespace(stdout="", stderr="worker terminated")

    old_path = list(sys.path)
    for name in ("sapien", "trimesh", "gen_fragment"):
        sys.modules.pop(name, None)
    monkeypatch.setattr(subprocess, "run", crashed_worker)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--staging",
            str(staging),
            "--library-dir",
            str(library),
            "--out",
            str(output),
            "--overrides-fragment",
            str(fragment),
            "--identity-basis",
            "manifest_human",
        ],
    )
    sys.path.insert(0, str(stubs))
    try:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    finally:
        sys.path[:] = old_path
        for name in ("sapien", "trimesh", "gen_fragment"):
            sys.modules.pop(name, None)

    matrix = json.loads((output / "import_matrix.json").read_text())
    assert matrix == [
        {
            "asset": "315_shears",
            "model": 0,
            "usd": "source.usd",
            "category": None,
            "status": "rejected",
            "reasons": ["native crash or timeout during processing"],
        }
    ]
    assert not (library / "nvidia" / "315_shears").exists()
    assert fragment.exists()


def test_driver_does_not_quarantine_a_concurrent_winner_for_the_same_model(tmp_path, monkeypatch):
    """A rejected worker may clean only the exact candidate bytes it produced."""

    trimesh = pytest.importorskip("trimesh")
    losing_staging = tmp_path / "losing-staging"
    losing_staging.mkdir()
    losing_source = losing_staging / "losing.usd"
    losing_source.write_text("#usda 1.0\n# losing candidate\n")
    losing_glb = losing_staging / "losing.glb"
    trimesh.creation.box(extents=[0.1, 0.1, 0.1]).export(losing_glb)
    losing_record = {
        "asset": "315_shears",
        "model": 0,
        "usd": losing_source.name,
        "usd_local": str(losing_source),
        "usd_sha256": hashlib.sha256(losing_source.read_bytes()).hexdigest(),
        "glb": str(losing_glb),
        "group": "rigid_props",
        "status": "converted",
        "category": "tool",
        "aliases": ["shears"],
        "size_policy": "none",
    }
    (losing_staging / "staging_manifest.json").write_text(json.dumps([losing_record]))

    winner_staging = tmp_path / "winner-staging"
    winner_staging.mkdir()
    winner_source = winner_staging / "winner.usd"
    winner_source.write_text("#usda 1.0\n# concurrent winner\n")
    winner_glb = winner_staging / "winner.glb"
    trimesh.creation.icosphere(subdivisions=1, radius=0.06).export(winner_glb)
    winner_record = {
        **losing_record,
        "usd": winner_source.name,
        "usd_local": str(winner_source),
        "usd_sha256": hashlib.sha256(winner_source.read_bytes()).hexdigest(),
        "glb": str(winner_glb),
    }
    (winner_staging / "staging_manifest.json").write_text(json.dumps([winner_record]))

    reference_catalog = tmp_path / "reference_catalog.json"
    reference_catalog.write_text(json.dumps({"entries": []}))
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _write_materialize_runtime_stub(stubs)
    library = tmp_path / "library"
    library.mkdir()
    output = tmp_path / "losing-out"
    winner_output = tmp_path / "winner-out"
    fragment = tmp_path / "fragment.yml"
    asset_dir = library / "nvidia" / "315_shears"
    ledger_path = asset_dir / "ledger.json"
    real_subprocess_run = subprocess.run
    winner_state = {}

    def raced_worker(*_args, **_kwargs):
        # The losing worker has already exported its model outputs and captured
        # their content identity before attempting the ledger CAS.
        losing_files = {
            "visual/base0.glb": b"losing-visual-bytes",
            "collision/base0.glb": b"losing-collision-bytes",
            "model_data0.json": b'{"owner":"losing-worker"}\n',
        }
        for relative, payload in losing_files.items():
            target = asset_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        cleanup_files = [
            {
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for relative, payload in sorted(losing_files.items())
        ]

        # Before the losing worker performs its expect-absent CAS, another
        # legal worker publishes the same model_id through the real worker CLI.
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join((str(stubs), str(ROOT)))
        winner = real_subprocess_run(
            [
                sys.executable,
                str(SCRIPT),
                "--staging",
                str(winner_staging),
                "--library-dir",
                str(library),
                "--out",
                str(winner_output),
                "--overrides-fragment",
                str(tmp_path / "winner-fragment.yml"),
                "--identity-basis",
                "manifest_human",
                "--reference-catalog",
                str(reference_catalog),
                "--only-index",
                "0",
            ],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        assert winner.returncode == 0, winner.stdout + winner.stderr
        winner_row = json.loads((winner_output / "rows" / "row_0.json").read_text())
        assert winner_row["status"] == "accepted"
        winner_state["ledger"] = ledger_path.read_bytes()
        winner_state["visual"] = (asset_dir / "visual" / "base0.glb").read_bytes()

        # The losing worker's stale expect-absent CAS now fails. Its public row
        # carries the exact candidate/previous-ledger ownership facts needed by
        # driver cleanup; neither fact describes the winner now on disk.
        row = {
            "asset": "315_shears",
            "model": 0,
            "usd": losing_source.name,
            "category": "tool",
            "status": "rejected",
            "reasons": ["ConcurrentLedgerUpdateError: cleanup expected no ledger but one exists"],
            "cleanup_candidate": {
                "schema": "asset_materialize_cleanup_candidate.v1",
                "model_id": 0,
                "model_sha256": hashlib.sha256(b"losing-model-entry").hexdigest(),
                "ledger_before_model_sha256": None,
                "ledger_write_committed": False,
                "files": cleanup_files,
            },
        }
        rows = output / "rows"
        rows.mkdir(parents=True, exist_ok=True)
        (rows / "row_0.json").write_text(json.dumps(row))
        return SimpleNamespace(stdout="REJECTED 315_shears m0\n", stderr="")

    old_path = list(sys.path)
    for name in ("sapien", "sapien._native", "trimesh", "gen_fragment"):
        sys.modules.pop(name, None)
    monkeypatch.setattr(subprocess, "run", raced_worker)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--staging",
            str(losing_staging),
            "--library-dir",
            str(library),
            "--out",
            str(output),
            "--overrides-fragment",
            str(fragment),
            "--identity-basis",
            "manifest_human",
            "--reference-catalog",
            str(reference_catalog),
        ],
    )
    sys.path.insert(0, str(stubs))
    try:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    finally:
        sys.path[:] = old_path
        for name in ("sapien", "sapien._native", "trimesh", "gen_fragment"):
            sys.modules.pop(name, None)

    from lib import ledger

    assert ledger_path.read_bytes() == winner_state["ledger"]
    assert (asset_dir / "visual" / "base0.glb").read_bytes() == winner_state["visual"]
    assert ledger.validate_ledger(json.loads(ledger_path.read_text()), check_files=True) == []
    matrix = json.loads((output / "import_matrix.json").read_text())
    assert matrix[0]["status"] == "rejected"
