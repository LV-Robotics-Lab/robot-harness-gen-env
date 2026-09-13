import hashlib
import json
import runpy
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "asset_reuse/scripts/ledger"))
sys.path.insert(0, str(REPO / "asset_reuse"))
import gen_fragment  # noqa: E402
from lib import ledger, ledger_writes  # noqa: E402
from tests.test_ledger import make_valid  # noqa: E402
from tests.trusted_fixtures import qualified_runtime_capability  # noqa: E402


def _write(lib, asset, led, *, trusted_model_ids=None):
    led["external_ids"]["env_gen"] = asset
    led["asset_id"] = f"external_{asset}"
    asset_dir = lib / asset
    files_dir = asset_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    for model in led["models"]:
        for index, representation in enumerate(model["representations"]):
            suffix = "png" if representation.get("role") == "snapshot" else "ply"
            path = files_dir / f"m{model['model_id']}_{index}.{suffix}"
            path.write_bytes(f"{asset}:{model['model_id']}:{index}".encode())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            representation.update(
                format=suffix,
                uri=str(path),
                sha256=digest,
                files=[{"uri": str(path), "sha256": digest, "bytes": path.stat().st_size}],
            )
    if trusted_model_ids is None:
        trusted_model_ids = {m["model_id"] for m in led["models"]}
    for m in led["models"]:  # digest 补真值 + immutable evidence
        for index, verification in enumerate(m["verification"]):
            digest = ledger.reps_digest(m, verification["backend"])
            verification["verified_digest"] = digest
            if m["model_id"] not in trusted_model_ids:
                continue
            common = {
                "asset_key": asset,
                "model_id": m["model_id"],
                "run_id": verification["run_id"],
                "timestamp": verification["timestamp"],
                "reps_digest": digest,
                "inputs": {
                    "fixture": ledger_writes.provenance_file_record(__file__),
                    "task": {"asset_key": asset, "model_id": m["model_id"]},
                },
                "model_entry": m,
                "execution_snapshot": ledger_writes.publish_execution_snapshot(
                    asset_dir / "verification_inputs",
                    source_asset_dir=asset_dir,
                    asset_key=asset,
                    model_entry=m,
                ),
            }
            if verification["check"] == "settle":
                passed = verification["verdict"] == "pass"
                payload = ledger_writes.issue_qualified_verification(
                    issuer="asset.settle_repair.v1",
                    thresholds={
                        "max_late_drift_m": 0.002,
                        "min_support_z_m": -0.005,
                        "max_tilt_deg": 181.0,
                    },
                    result={
                        "schema": "asset_settle_result.v2",
                        "finite": passed,
                        "late_drift_m": 0.0,
                        "support_z_m": 0.0,
                        "tilt_deg": 0.0,
                        "rest_orientation_wxyz": list(
                            next(
                                pose
                                for pose in m["physical"]["conventions"]["stable_poses"]
                                if pose.get("is_default") is True
                            )["orientation_wxyz"]
                        ),
                        "origin_z_m": 0.0 if passed else None,
                        "derived_z_policy": "origin_on_table" if passed else None,
                        "details": {},
                    },
                    runtime_capability=qualified_runtime_capability(
                        asset_dir / "runtime",
                        "asset.settle_repair.v1",
                        ledger,
                        ledger_writes,
                    ),
                    **common,
                )
            elif verification["check"] == "joint_sweep":
                passed = verification["verdict"] == "pass"
                conventions = m["physical"]["conventions"]
                default_pose = next(
                    pose for pose in conventions["stable_poses"] if pose.get("is_default") is True
                )
                payload = ledger_writes.issue_qualified_verification(
                    issuer="asset.s13b_joint_sweep.v1",
                    thresholds={"min_screenshot_std": 1.0, "allow_free_joints": False},
                    result={
                        "schema": "asset_joint_sweep_result.v2",
                        "loaded": passed,
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
                        "z_policy": conventions["z_policy"],
                        "details": {},
                    },
                    runtime_capability=qualified_runtime_capability(
                        asset_dir / "runtime",
                        "asset.s13b_joint_sweep.v1",
                        ledger,
                        ledger_writes,
                    ),
                    **common,
                )
            else:
                payload = ledger_writes.build_verification_evidence(
                    backend=verification["backend"],
                    check=verification["check"],
                    verdict=verification["verdict"],
                    script_path=__file__,
                    thresholds={},
                    result={"fixture_verdict": verification["verdict"]},
                    **{
                        key: value
                        for key, value in common.items()
                        if key not in {"model_entry", "execution_snapshot"}
                    },
                )
            record = ledger_writes.publish_verification_evidence(
                asset_dir / "verification_evidence", payload
            )
            m["verification"][index] = ledger_writes.receipt_from_evidence(payload, record)
    p = asset_dir / "ledger.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(led))
    return led


def test_legacy_digest_bound_pass_is_explicitly_not_qualified(tmp_path):
    _write(tmp_path, "315_shears", make_valid(), trusted_model_ids=set())

    fragment, _ = gen_fragment.generate(tmp_path)

    assert "315_shears" not in fragment


def test_generation_qc_evidence_is_operator_metadata_not_physical_qualification(tmp_path):
    document = make_valid()
    document["models"][0]["verification"][0]["check"] = "generation_qc"
    _write(tmp_path, "315_shears", document)

    fragment, _ = gen_fragment.generate(tmp_path)

    assert "315_shears" not in fragment
    assert gen_fragment.qualified_model_ids(tmp_path) == {}


def test_qualified_model_projection_is_exact_and_supports_grouped_library(tmp_path):
    led = make_valid()
    second = json.loads(json.dumps(led["models"][0]))
    second["model_id"] = 1
    led["models"].append(second)
    provider_root = tmp_path / "nvidia"
    _write(provider_root, "315_shears", led, trusted_model_ids={0})

    qualified = gen_fragment.qualified_model_ids(tmp_path)

    assert qualified == {"315_shears": frozenset({0})}


def test_execution_projection_binds_authoritative_tree_digest_and_role_closure(tmp_path):
    led = make_valid()
    representation = led["models"][0]["representations"][0]
    representation["role"] = "visual_and_collision"
    representation["collision_meta"] = {"mode": "explicit_mesh"}
    asset_dir = tmp_path / "github" / "315_shears"
    written = _write(tmp_path / "github", "315_shears", led)
    model = written["models"][0]

    projection = gen_fragment.qualified_execution_projection(tmp_path)

    assert set(projection) == {"315_shears"}
    asset = projection["315_shears"]
    assert Path(asset["asset_dir"]).samefile(asset_dir)
    projected = asset["models"][0]
    assert projected["reps_digest"] == ledger.reps_digest(model, "sapien")
    assert projected["physical_facts_digest"] == ledger.settle_physical_facts_digest(model)
    assert set(projected["roles"]) == {"visual_and_collision"}
    (role,) = projected["roles"]["visual_and_collision"]
    assert role["primary"] == model["representations"][0]["files"][0]
    assert role["closure"] == model["representations"][0]["files"]


@pytest.mark.parametrize("attack", ["wrong_asset_tree", "wrong_primary", "stale_primary"])
def test_catalog_filter_rejects_library_path_or_hash_not_in_projection(tmp_path, attack):
    library = tmp_path / "library"
    led = make_valid()
    representation = led["models"][0]["representations"][0]
    representation["role"] = "visual_and_collision"
    representation["collision_meta"] = {"mode": "explicit_mesh"}
    written = _write(library / "github", "315_shears", led)
    asset_dir = library / "github" / "315_shears"
    primary = Path(written["models"][0]["representations"][0]["uri"])
    pose = written["models"][0]["physical"]["conventions"]["stable_poses"][0]
    projection = gen_fragment.qualified_execution_projection(library)
    wrong_tree = library / "github" / "316_other"
    wrong_tree.mkdir()
    wrong_primary = asset_dir / "files" / "wrong.ply"
    wrong_primary.write_bytes(primary.read_bytes())
    catalog_asset = wrong_tree if attack == "wrong_asset_tree" else asset_dir
    catalog_primary = wrong_primary if attack == "wrong_primary" else primary
    if attack == "stale_primary":
        primary.write_bytes(b"changed-after-projection")
    catalog = {
        "entries": [
            {
                "asset_id": "315_shears",
                "asset_path": str(catalog_asset),
                "models": [
                    {
                        "model_id": 0,
                        "model_path": str(asset_dir),
                        "usable": True,
                        "missing": [],
                        "visual_path": str(catalog_primary),
                        "collision_path": str(catalog_primary),
                        "stable_pose_id": pose["pose_id"],
                        "stable_orientation_wxyz": pose["orientation_wxyz"],
                        "z_policy": "origin_on_table",
                    }
                ],
                "available": True,
                "availability_reasons": [],
            }
        ]
    }

    assert gen_fragment.filter_catalog_execution_view(catalog, library, projection) == {
        "entries": []
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_path", "wrong"),
        ("stable_pose_id", "forged"),
        ("stable_orientation_wxyz", [1.0, 0.0, 0.0, 0.0]),
        ("z_policy", "center_on_table"),
    ],
)
def test_catalog_filter_rejects_unbound_model_or_placement_field(tmp_path, field, value):
    library = tmp_path / "library"
    led = make_valid()
    led["models"][0]["representations"][0].update(
        role="visual_and_collision",
        collision_meta={"mode": "explicit_mesh"},
    )
    written = _write(library / "github", "315_shears", led)
    asset_dir = library / "github" / "315_shears"
    primary = written["models"][0]["representations"][0]["uri"]
    pose = written["models"][0]["physical"]["conventions"]["stable_poses"][0]
    model = {
        "model_id": 0,
        "model_path": str(asset_dir),
        "visual_path": primary,
        "collision_path": primary,
        "stable_pose_id": pose["pose_id"],
        "stable_orientation_wxyz": pose["orientation_wxyz"],
        "z_policy": "origin_on_table",
        "usable": True,
        "missing": [],
    }
    model[field] = str(tmp_path / "wrong") if field == "model_path" else value
    catalog = {
        "entries": [
            {
                "asset_id": "315_shears",
                "asset_path": str(asset_dir),
                "models": [model],
                "available": True,
                "availability_reasons": [],
            }
        ]
    }

    projection = gen_fragment.qualified_execution_projection(library)
    assert gen_fragment.filter_catalog_execution_view(catalog, library, projection) == {
        "entries": []
    }


def test_catalog_filter_keeps_exact_library_models_and_preserves_upstream_fallback(tmp_path):
    library = tmp_path / "library"
    led = make_valid()
    led["models"][0]["representations"][0].update(
        role="visual_and_collision",
        collision_meta={"mode": "explicit_mesh"},
    )
    second = json.loads(json.dumps(led["models"][0]))
    second["model_id"] = 1
    led["models"].append(second)
    written = _write(library / "nvidia", "315_shears", led, trusted_model_ids={1})
    grouped_asset = library / "nvidia" / "315_shears"
    projection = gen_fragment.qualified_execution_projection(library)
    primary = written["models"][1]["representations"][0]["uri"]
    pose = written["models"][1]["physical"]["conventions"]["stable_poses"][0]
    upstream_asset = tmp_path / "robotwin" / "assets" / "objects" / "316_native"
    upstream_asset.mkdir(parents=True)
    catalog = {
        "entries": [
            {
                "asset_id": "315_shears",
                "asset_path": str(grouped_asset),
                "models": [
                    {"model_id": 0, "usable": True, "missing": []},
                    {
                        "model_id": 1,
                        "model_path": str(grouped_asset),
                        "visual_path": primary,
                        "collision_path": primary,
                        "stable_pose_id": pose["pose_id"],
                        "stable_orientation_wxyz": pose["orientation_wxyz"],
                        "z_policy": "origin_on_table",
                        "usable": False,
                        "missing": ["collision"],
                    },
                ],
                "available": True,
                "availability_reasons": [],
            },
            {
                "asset_id": "316_native",
                "asset_path": str(upstream_asset),
                "models": [{"model_id": 0, "usable": True, "missing": []}],
                "available": True,
                "availability_reasons": [],
            },
        ]
    }
    filtered = gen_fragment.filter_catalog_execution_view(catalog, library, projection)

    assert [entry["asset_id"] for entry in filtered["entries"]] == [
        "315_shears",
        "316_native",
    ]
    library_entry = filtered["entries"][0]
    assert [model["model_id"] for model in library_entry["models"]] == [1]
    assert library_entry["available"] is False
    assert library_entry["availability_reasons"] == ["collision"]
    assert filtered["entries"][1] == catalog["entries"][1]


def test_catalog_filter_removes_library_asset_with_no_qualified_models(tmp_path):
    library_asset = tmp_path / "library" / "github" / "315_shears"
    library_asset.mkdir(parents=True)
    catalog = {
        "entries": [
            {
                "asset_id": "315_shears",
                "asset_path": str(library_asset),
                "models": [{"model_id": 0, "usable": True, "missing": []}],
                "available": True,
                "availability_reasons": [],
            }
        ]
    }
    assert gen_fragment.filter_catalog_execution_view(catalog, tmp_path / "library", {}) == {
        "entries": []
    }


def test_projection_helpers_cover_static_appearance_semantics_and_pose_failures():
    led = make_valid()
    model = led["models"][0]
    model["physical"]["conventions"]["stable_poses"].insert(
        0,
        {
            "pose_id": "not-default",
            "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
            "is_default": False,
        },
    )
    model["physical"]["conventions"]["is_static"] = True
    model["appearance"] = {"colors_measured": ["red"]}
    projected_model = gen_fragment._project_model(model)
    assert projected_model["is_static"] is True
    assert projected_model["colors"] == ["red"]

    led["semantics"]["colors"] = ["blue"]
    led["semantics"]["materials"] = ["metal"]
    projected_asset = gen_fragment._project_asset(led, {"0": projected_model}, ["red"])
    assert projected_asset["colors"] == ["blue"]
    assert projected_asset["materials"] == ["metal"]
    led["semantics"]["colors"] = []
    assert gen_fragment._project_asset(led, {"0": projected_model}, ["red"])["colors"] == ["red"]

    assert gen_fragment._agreed_measured_colors(led, {"0"}) == ["red"]
    disagreeing = json.loads(json.dumps(model))
    disagreeing["model_id"] = 1
    disagreeing["appearance"]["colors_measured"] = ["green"]
    led["models"].append(disagreeing)
    assert gen_fragment._agreed_measured_colors(led, {"0", "1"}) is None

    no_default = json.loads(json.dumps(model))
    for pose in no_default["physical"]["conventions"]["stable_poses"]:
        pose["is_default"] = False
    with pytest.raises(ValueError, match="no is_default"):
        gen_fragment._default_pose(no_default)


def test_catalog_filter_leaves_malformed_entry_for_strict_catalog_parser(tmp_path):
    malformed = {"entries": [{"asset_id": "broken"}]}
    assert gen_fragment.filter_catalog_execution_view(malformed, tmp_path, {}) == malformed


def test_declared_license_does_not_increment_unknown_count(tmp_path):
    led = make_valid()
    led["models"][0]["source"]["license"]["status"] = "declared"
    _write(tmp_path, "315_shears", led)

    fragment, stats = gen_fragment.generate(tmp_path)

    assert "315_shears" in fragment
    assert stats == {"unknown_license_models": 0}


def test_yaml_writer_covers_optional_fields_and_scalar_shapes(tmp_path):
    fragment = {
        "315_shears": {
            "category": "shears",
            "aliases": ["shears"],
            "colors": ["red"],
            "materials": ["metal"],
            "models": {
                "0": {
                    "stable_pose_id": "upright",
                    "stable_orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                    "z_policy": "origin_on_table",
                    "footprint_shape": "box",
                    "is_static": True,
                    "colors": ["red"],
                }
            },
        }
    }
    output = tmp_path / "fragment.yml"

    gen_fragment.write_yaml(fragment, output)

    assert yaml.safe_load(output.read_text()) == {"315_shears": fragment["315_shears"]}
    assert gen_fragment._fmt_scalar(True) == "true"
    assert gen_fragment._fmt_scalar(False) == "false"


def test_cli_license_gate_and_module_entrypoint(tmp_path, capsys, monkeypatch):
    _write(tmp_path, "315_shears", make_valid())
    output = tmp_path / "gated.yml"
    assert (
        gen_fragment.main(["--library-dir", str(tmp_path), "--out", str(output), "--license-gate"])
        == 0
    )
    assert yaml.safe_load(output.read_text()) is None
    assert "excluded" in capsys.readouterr().err

    empty_library = tmp_path / "empty"
    empty_library.mkdir()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(Path(gen_fragment.__file__)),
            "--library-dir",
            str(empty_library),
            "--out",
            str(tmp_path / "empty.yml"),
        ],
    )
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(gen_fragment.__file__, run_name="__main__")
    assert stopped.value.code == 0


def test_projection_default_pose(tmp_path):
    _write(tmp_path, "315_shears", make_valid())
    frag, stats = gen_fragment.generate(tmp_path)
    m = frag["315_shears"]["models"]["0"]
    assert m["stable_pose_id"] == "upright"  # 列表→标量投影
    assert m["stable_orientation_wxyz"] == ledger.X90_WXYZ
    assert "is_static" not in m  # False 不输出
    assert stats["unknown_license_models"] == 1  # 警告计数


def test_latest_fail_excluded(tmp_path):
    led = make_valid()
    led["models"][0]["verification"].append(
        {
            "backend": "sapien",
            "check": "settle",
            "verdict": "fail",
            "run_id": "r2",
            "timestamp": "2026-08-08T12:00:00",
            "verified_digest": "补真值占位",
            "report_path": "r.json",
        }
    )
    _write(tmp_path, "315_shears", led)
    frag, _ = gen_fragment.generate(tmp_path)
    assert "315_shears" not in frag  # latest=fail → 出视图（禁 any(pass)）


def test_stale_digest_excluded(tmp_path):
    led = make_valid()
    _write(tmp_path, "315_shears", led)
    p = tmp_path / "315_shears/ledger.json"
    led2 = json.loads(p.read_text())
    led2["models"][0]["verification"][0]["verified_digest"] = "e" * 64
    p.write_text(json.dumps(led2))
    frag, _ = gen_fragment.generate(tmp_path)
    assert "315_shears" not in frag  # digest 失效=未验证


def test_file_tampering_is_excluded_even_when_receipt_digest_is_unchanged(tmp_path):
    led = _write(tmp_path, "315_shears", make_valid())
    representation = led["models"][0]["representations"][0]
    Path(representation["uri"]).write_bytes(b"tampered-after-verification")

    frag, _ = gen_fragment.generate(tmp_path)

    assert "315_shears" not in frag


def test_license_gate(tmp_path):
    _write(tmp_path, "315_shears", make_valid())
    frag_off, _ = gen_fragment.generate(tmp_path)
    frag_on, _ = gen_fragment.generate(tmp_path, license_gate=True)
    assert "315_shears" in frag_off and "315_shears" not in frag_on


def test_cli_warns_unknown(tmp_path, capsys):
    _write(tmp_path, "315_shears", make_valid())
    gen_fragment.main(["--library-dir", str(tmp_path), "--out", str(tmp_path / "f.yml")])
    assert "unknown license" in capsys.readouterr().err.lower()  # 无论开关必打警告


def _articulated_ledger(verification):
    led = make_valid(kind="articulated")
    led["models"][0]["articulation"] = {"joint_names": ["hinge"]}
    conventions = led["models"][0]["physical"]["conventions"]
    conventions["is_static"] = True
    conventions["stable_poses"][0].update(
        pose_id="fixed_root_identity",
        orientation_wxyz=list(ledger.IDENTITY_WXYZ),
    )
    led["models"][0]["verification"] = verification
    return led


def test_articulated_joint_sweep_pass_included(tmp_path):
    led = _articulated_ledger(
        [
            {
                "backend": "sapien",
                "check": "joint_sweep",
                "verdict": "pass",
                "run_id": "attempt1",
                "timestamp": "2026-08-09T22:02:13",
                "verified_digest": "补真值占位",
                "report_path": "r.json",
            }
        ]
    )
    _write(tmp_path, "314_cabinet", led)
    frag, _ = gen_fragment.generate(tmp_path)
    assert "314_cabinet" in frag  # kind=articulated + joint_sweep pass → 入视图


def test_articulated_settle_only_excluded(tmp_path):
    led = _articulated_ledger(
        [
            {
                "backend": "sapien",
                "check": "settle",
                "verdict": "pass",
                "run_id": "attempt1",
                "timestamp": "2026-08-08T10:00:00",
                "verified_digest": "补真值占位",
                "report_path": "r.json",
            }
        ]
    )
    _write(tmp_path, "314_cabinet", led)
    frag, _ = gen_fragment.generate(tmp_path)
    assert "314_cabinet" not in frag  # 关节体不认 settle -- 防倒灌
