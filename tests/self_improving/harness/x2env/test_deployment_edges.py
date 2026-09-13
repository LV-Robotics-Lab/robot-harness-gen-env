"""Public deployment workflows; explicit model-process and runtime-launch boundary doubles."""

import hashlib
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

from self_improving.harness.x2env.artifacts import artifact_closure
from self_improving.harness.x2env.contracts import X2EnvRequest
from self_improving.harness.x2env.deployment import build_harness, load_deployment
from self_improving.harness.x2env.input import ingest
from tests.self_improving.harness.x2env.test_deployment import foreground_deployment
from tests.self_improving.harness.x2env.test_local_color_advisory import color_inputs


def color_deployment(tmp_path, *, bad_pin=False):
    store, _, scene_ref, _, parent, _, _ = color_inputs(tmp_path)
    request = X2EnvRequest(
        text="one pink plastic box",
        seed=19,
        allowed_sources=("local",),
        idempotency_key="deployed-color",
        output_dir=str(tmp_path / "output"),
    )
    bundle = ingest(request, store)
    scene = json.loads(store.read_artifact(scene_ref))
    scene["input_sha256"] = bundle.request_sha256
    for entity in scene["entities"]:
        entity["dimensions"] = [0.1, 0.1, 0.1]
        entity["provenance"] = {
            field: [{"source": "text", "input_sha256": bundle.text.sha256, "kind": "explicit"}]
            for field in entity["provenance"]
        }
    program = tmp_path / "deployment-model-double"
    program.write_text(
        f"#!{sys.executable}\nimport sys,json,pathlib\nfrom PIL import Image\n"
        "sys.stdin.read()\n"
        "schema=json.loads(pathlib.Path(sys.argv[sys.argv.index('--output-schema')+1]).read_text())\n"
        "if 'scene' in schema['properties']:\n"
        f" answer={repr({'scene': scene, 'unknowns': []})}\n"
        "elif 'declared_color' in schema['properties']:\n"
        " answer={'declared_color':'pink','rgba':[0.9,0.4,0.6,1.0]}\n"
        "else:\n"
        " image=Image.open(sys.argv[sys.argv.index('-i')+1]).convert('RGB')\n"
        " color='blue' if image.getpixel((0,0))[2]>200 else 'pink'\n"
        " answer={'object':'box','match':True,'colors':[color],'materials':['plastic'],"
        "'confidence':0.99,'same_kind':True,'plausible':True,'suggests':None}\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps(answer))\n"
        "print(json.dumps({'type':'turn.completed'}))\n"
    )
    program.chmod(0o700)
    roots = {
        name: str(tmp_path / name)
        for name in ("interpreter", "stdlib", "distributions", "native", "genesis")
    }
    policy = {"kind": "git", "root": str(Path(__file__).resolve().parents[4])}
    if bad_pin:
        policy["expected_commit"] = "0" * 40
    config = tmp_path / "deployment.json"
    config.write_text(
        json.dumps(
            {
                "state_dir": str(store.database.parent),
                "codex": {
                    "executable": str(program),
                    "sha256": hashlib.sha256(program.read_bytes()).hexdigest(),
                },
                "genesis": {
                    "runtime_roots": roots,
                    "denied_roots": [str(tmp_path / "denied")],
                    "source_identity": policy,
                },
            }
        )
    )
    return load_deployment(config), request, store, parent


def test_deployed_color_workflow_forwards_isolation_to_both_real_previews(tmp_path, monkeypatch):
    from self_improving.harness.x2env import package_loader

    config, request, store, parent = color_deployment(tmp_path)
    previews = []

    def launch(payload, **kwargs):
        assert kwargs["denied_roots"] == config.genesis.denied_roots
        assert kwargs["runtime_roots"] == config.genesis.runtime_roots.model_dump()
        output = kwargs["output"]
        output.mkdir()
        if payload["entities"][0]["id"] != "candidate":
            return {
                "status": "failed",
                "simulator_executed": False,
                "error_code": "explicit_final_runtime_double_stop",
                "wall_seconds": 0.0,
            }
        version = payload["entities"][0]["version_sha256"]
        previews.append(version)
        Image.new(
            "RGB", (4, 4), "blue" if version == parent.version_sha256 else (230, 102, 153)
        ).save(output / "preview.png")
        return {
            "status": "passed",
            "simulator_executed": True,
            "wall_seconds": 0.0,
            "media": {
                "frames": [
                    {
                        "path": "preview.png",
                        "png_sha256": hashlib.sha256(
                            (output / "preview.png").read_bytes()
                        ).hexdigest(),
                    }
                ]
            },
        }

    monkeypatch.setattr(package_loader, "launch_child", launch)
    harness = build_harness(config)
    snapshot = harness.resume(harness.submit(request).workflow_id)
    assert len(previews) == 2, snapshot
    assert previews[0] == parent.version_sha256 and previews[1] != previews[0]
    assert snapshot.resolved_assets is not None
    assert any(
        op.capability == "asset.revise" and op.status == "succeeded" for op in snapshot.operations
    )
    roots = tuple(ref for op in snapshot.operations if op.result for ref in op.result.outputs)
    receipts = [
        json.loads(store.read_artifact(ref))
        for ref in artifact_closure(store, roots)
        if ref.media_type == "application/json"
    ]
    preview_receipts = [
        value
        for value in receipts
        if isinstance(value, dict)
        and value.get("scope") == "asset_preview_scope"
        and "identity" in value
    ]
    assert {receipt["version_sha256"] for receipt in preview_receipts} == set(previews)
    assert all(receipt["identity"]["kind"] == "git_checkout" for receipt in preview_receipts)
    assert all(receipt["qualified"] is False for receipt in preview_receipts)


def test_deployed_source_pin_failure_stops_local_preview_before_runtime(tmp_path, monkeypatch):
    from self_improving.harness.x2env import package_loader

    config, request, store, _ = color_deployment(tmp_path, bad_pin=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("source identity failure reached external runtime")

    monkeypatch.setattr(package_loader, "launch_child", forbidden)
    harness = build_harness(config)
    snapshot = harness.resume(harness.submit(request).workflow_id)
    assert snapshot.resolved_assets is None and snapshot.asset_resolution is not None
    records = artifact_closure(store, (snapshot.asset_resolution,))
    assert any(b"source_git_commit_mismatch" in store.read_artifact(ref) for ref in records)
    assert not any(op.capability == "asset.revise" for op in snapshot.operations)


@pytest.mark.parametrize("fault", ["state_root", "state_symlink", "codex_relative", "denied_root"])
def test_public_deployment_rejects_unscoped_or_symbolic_authority_paths(tmp_path, fault):
    data = {"state_dir": str(tmp_path / "state")}
    if fault == "state_root":
        data["state_dir"] = "/"
    elif fault == "state_symlink":
        target = tmp_path / "real-state"
        target.mkdir()
        link = tmp_path / "symbolic-state"
        link.symlink_to(target, target_is_directory=True)
        data["state_dir"] = str(link)
    elif fault == "codex_relative":
        data["codex"] = {"executable": "relative-model", "sha256": "a" * 64}
    else:
        data["genesis"] = {
            "runtime_roots": {
                name: str(tmp_path / name)
                for name in ("interpreter", "stdlib", "distributions", "native", "genesis")
            },
            "denied_roots": ["/"],
        }
    config = tmp_path / "invalid.json"
    config.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_deployment(config)


def test_public_deployment_rejects_oversized_configuration_before_json_parsing(tmp_path):
    path = tmp_path / "oversized.json"
    path.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="deployment file exceeds limit"):
        load_deployment(path)


@pytest.mark.parametrize("fault", ["symbolic_provider", "oversized_provider", "license_pin"])
def test_selected_web_configuration_failure_is_retained_in_workflow_artifacts(tmp_path, fault):
    provider = tmp_path / "provider.json"
    provider.write_text('{"providers": {}}')
    if fault == "symbolic_provider":
        original = tmp_path / "provider-original.json"
        provider.rename(original)
        provider.symlink_to(original)
        expected = "symbolic deployment config"
    elif fault == "oversized_provider":
        provider.write_bytes(b" " * (2 * 1024 * 1024 + 1))
        expected = "oversized pinned config"
    else:
        expected = "deployment_pin_mismatch"
    web = {
        "provider_config": {
            "path": str(provider),
            "sha256": hashlib.sha256(provider.read_bytes()).hexdigest(),
        }
    }
    if fault == "license_pin":
        license_file = tmp_path / "licenses.json"
        license_file.write_text("{}")
        web["license_records"] = {"path": str(license_file), "sha256": "0" * 64}
    config, request = foreground_deployment(tmp_path, "web", {"web": web})
    harness = build_harness(config)
    snapshot = harness.resume(harness.submit(request).workflow_id)
    from self_improving.harness.x2env.store import Store

    store = Store(tmp_path / "state")
    assert snapshot.status == "blocked" and snapshot.compiled_scene is None
    assert snapshot.asset_resolution is not None
    records = artifact_closure(store, (snapshot.asset_resolution,))
    assert any(expected.encode() in store.read_artifact(ref) for ref in records)
