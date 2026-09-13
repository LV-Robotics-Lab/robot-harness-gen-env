"""Real offline wheel installation; no repository path injection or provider doubles."""

import json
import shutil
import subprocess
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path

import pytest

from tests.self_improving.harness.test_packaging import (
    AGENTICSIM_SOURCE,
    CANONICAL_SCHEMAS,
    FROZEN_ASSERTIONS,
    LEDGER_PACKAGE,
    REPO_ROOT,
    _copy_build_fixture,
    _run,
)


@pytest.fixture(scope="module")
def installed_provider(tmp_path_factory):
    root = tmp_path_factory.mktemp("installed-provider")
    source = _copy_build_fixture(root)
    for package in (Path("self_improving/harness/x2env"), Path("scene_gen"), LEDGER_PACKAGE):
        for path in (REPO_ROOT / package).rglob("*.py"):
            target = source / path.relative_to(REPO_ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    wheelhouse = root / "wheelhouse"
    wheelhouse.mkdir()
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(source),
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheelhouse),
        ],
        cwd=root,
    )
    wheel = next(wheelhouse.glob("*.whl"))
    environment = root / "environment"
    _run([sys.executable, "-m", "venv", "--system-site-packages", str(environment)], cwd=root)
    python = environment / "bin/python"
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            str(wheel),
            "--no-deps",
            "--no-index",
            "--no-compile",
            "--ignore-installed",
        ],
        cwd=root,
    )
    return root, python, wheel


def test_installed_yuxin_provider_contains_original_engine_without_source_paths(installed_provider):
    root, python, wheel = installed_provider
    with zipfile.ZipFile(wheel) as archive:
        for name in (
            "__init__.py",
            "_third_party.py",
            "openxsim/assets.py",
            "openxsim/ir.py",
            "generation/placement_agent.py",
        ):
            member = f"agenticsim/{name}"
            assert member in archive.namelist()
            assert archive.read(member) == (REPO_ROOT / AGENTICSIM_SOURCE / member).read_bytes()
    probe = """
import importlib, pathlib, sysconfig
root = pathlib.Path(sysconfig.get_paths()['purelib']).resolve()
for name in ('agenticsim.openxsim.assets',
             'self_improving.asset_pipeline.active.asset_reuse.lib.a1_providers',
             'self_improving.asset_pipeline.active.asset_reuse.lib.a3_webfetch',
             'self_improving.harness.x2env.adapters.yuxin'):
    module = importlib.import_module(name)
    assert pathlib.Path(module.__file__).resolve().is_relative_to(root), module.__file__
from self_improving.asset_pipeline.active.asset_reuse.lib.a1_providers import load_providers
tiers, config = load_providers({'providers': {}, 'globals': {'license_gate': True}})
assert tiers == [] and config['license_gate'] is True
"""
    _run([str(python), "-I", "-c", probe], cwd=root)


def test_provider_import_never_bootstraps_optional_simulators(installed_provider):
    root, python, _ = installed_provider
    probe = """
import sys
before = tuple(sys.path)
class RuntimeImportGuard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'isaaclab_tasks', 'isaaclab', 'torch', 'sapien', 'metasim'}:
            raise AssertionError('provider import initialized optional runtime: ' + fullname)
sys.meta_path.insert(0, RuntimeImportGuard())
import agenticsim.openxsim.assets
assert tuple(sys.path) == before
assert callable(agenticsim.bootstrap_vendored_isaaclab)
"""
    _run([str(python), "-I", "-c", probe], cwd=root)


def test_wheel_declares_scipy_for_the_canonical_platform(installed_provider):
    _, _, wheel = installed_provider
    with zipfile.ZipFile(wheel) as archive:
        name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        requirements = BytesParser().parsebytes(archive.read(name)).get_all("Requires-Dist")
    assert any(line.startswith("scipy") and 'extra == "platform"' in line for line in requirements)


def test_wheel_pins_hole_preserving_geometry_for_the_platform(installed_provider):
    _, _, wheel = installed_provider
    with zipfile.ZipFile(wheel) as archive:
        name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        requirements = BytesParser().parsebytes(archive.read(name)).get_all("Requires-Dist")
    assert 'shapely==2.1.2; extra == "platform"' in requirements


def test_installed_canonical_schema_snapshots_and_loader_templates_are_original(installed_provider):
    root, python, wheel = installed_provider
    with zipfile.ZipFile(wheel) as archive:
        for path in (REPO_ROOT / CANONICAL_SCHEMAS).iterdir():
            member = path.relative_to(REPO_ROOT).as_posix()
            assert member in archive.namelist()
            assert archive.read(member) == path.read_bytes()
        for name in ("genesis_child.py", "package_loader.py"):
            path = Path("self_improving/harness/x2env") / name
            assert archive.read(path.as_posix()) == (REPO_ROOT / path).read_bytes()
    probe = """
import importlib.resources, json
resource = importlib.resources.files('self_improving.harness.x2env')
schema = json.loads(resource.joinpath('json_schemas/X2EnvRequest.json').read_bytes())
assert schema['title'] == 'X2EnvRequest'
assert resource.joinpath('genesis_child.py').is_file()
assert resource.joinpath('package_loader.py').is_file()
"""
    _run([str(python), "-I", "-B", "-c", probe], cwd=root)


def test_installed_physics_uses_exact_frozen_resource_without_repository_fallback(
    installed_provider,
):
    root, python, wheel = installed_provider
    with zipfile.ZipFile(wheel) as archive:
        assert FROZEN_ASSERTIONS.as_posix() in archive.namelist()
        assert (
            archive.read(FROZEN_ASSERTIONS.as_posix())
            == (REPO_ROOT / FROZEN_ASSERTIONS).read_bytes()
        )
    probe = """
import hashlib, importlib.resources
from self_improving.harness.x2env.assessment import ASSERTIONS_SHA256, evaluate_physics
from self_improving.harness.x2env.genesis_runtime import RuntimeEntity, RuntimeScene
data = importlib.resources.files('self_improving').joinpath(
    'golden_e2e_progress/physics-assertions-v1.json').read_bytes()
assert hashlib.sha256(data).hexdigest() == ASSERTIONS_SHA256
scene = RuntimeScene(seed=0, scene_ir_sha256='a' * 64, entities=(RuntimeEntity(
    id='table', category='table', kind='structural_box', position_m=(0.0, 0.0, 0.0),
    orientation_wxyz=(1.0, 0.0, 0.0, 0.0), size_m=(1.0, 1.0, 0.1), friction=0.5),))
result = evaluate_physics(scene, [], [], {})
assert result['error_code'] == 'unsupported_physical_profile', result
assert result['simulator_execution_proven'] is False
"""
    _run([str(python), "-I", "-B", "-c", probe], cwd=root)


def test_installed_cli_and_three_skills_keep_no_backend_submission_explicitly_blocked(
    installed_provider,
):
    root, python, _ = installed_provider
    cli = [str(python), "-I", "-B", str(python.with_name("x2env"))]
    help_result = _run([*cli, "--help"], cwd=root)
    assert "{check,submit,status,resume,package}" in help_result.stdout
    probe = """
from pathlib import Path
from self_improving.harness.x2env.harness import Harness
harness = Harness(Path.cwd() / 'describe-only-state')
descriptors = harness.describe_capabilities()
assert tuple(d.name for d in descriptors) == ('x2env.compile', 'x2env.replay', 'x2env.validate')
assert all(d.version == '1.0.0' and len(d.schema_sha256) == 64 for d in descriptors)
"""
    _run([str(python), "-I", "-B", "-c", probe], cwd=root)
    deployment = root / "no-backend-deployment.json"
    deployment.write_text(json.dumps({"state_dir": str(root / "cli-state")}))
    with pytest.raises(subprocess.CalledProcessError) as stopped:
        _run(
            [
                *cli,
                "--deployment",
                str(deployment),
                "submit",
                "--text",
                "one box on a table",
                "--seed",
                "23",
                "--source",
                "local",
                "--idempotency-key",
                "no-model-install-check",
                "--output",
                str(root / "cli-delivery"),
            ],
            cwd=root,
        )
    assert stopped.value.returncode == 2, stopped.value.stdout
    body = json.loads(stopped.value.stdout)
    assert body["status"] == "blocked"
    assert body["snapshot"]["required_resources"] == ["managed_codex_backend"]
    assert body["snapshot"]["scene_ir"] is None
    assert body["snapshot"]["compiled_scene"] is None
    assert body["snapshot"]["resolved_assets"] is None


def test_installed_canonical_catalog_runs_original_local_search_without_pythonpath(
    installed_provider,
):
    root, python, _ = installed_provider
    probe = """
import json, pathlib, sys, sysconfig
from types import SimpleNamespace
import trimesh
from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
from self_improving.harness.x2env.local_catalog import RegistryLocalCatalog
from self_improving.harness.x2env.normalization import normalize_mesh
from self_improving.harness.x2env.store import Store
import self_improving.harness.x2env.local_catalog as local_catalog

site = pathlib.Path(sysconfig.get_paths()['purelib']).resolve()
assert pathlib.Path(local_catalog.__file__).resolve().is_relative_to(site)
root = pathlib.Path.cwd() / 'local-search'
root.mkdir()
store = Store(root / 'state')
registry = AssetRegistry(store)
mesh = root / 'unit-box.glb'
mesh.write_bytes(trimesh.creation.box().export(file_type='glb'))
normalized = root / 'normalized'
report = normalize_mesh(mesh, normalized, dimensions_m=(0.1, 0.1, 0.1),
                        up_axis='Z', mass_kg=0.1, friction=0.6)
proof = store.write_artifact(
    b'explicit generated unit fixture, not a production asset', 'text/plain')
version = registry.register('unit-box', 'box', normalized, report['entrypoint'],
    files=tuple(f['path'] for f in report['files']),
    normalization_report=store.write_artifact(json.dumps(report).encode(), 'application/json'),
    license=AssetLicense(spdx='CC0-1.0', source_url='https://example.org/unit', evidence=proof),
    source=AssetSource(kind='web', provider='unit-fixture', source_ref='fixed', evidence=proof),
    receipt=proof)
result = RegistryLocalCatalog(store, registry).search(
    SimpleNamespace(id='item', category='box'),
    output_root=root / 'projection', limit=1, timeout=30)
assert result.status == 'succeeded', result
assert result.versions == (version,) and len(result.candidates) == 1
assert result.candidates[0].provider == 'robotwin_local'
assert result.candidates[0].license.spdx == 'CC0-1.0'
engine = json.loads(store.read_artifact(result.candidates[0].engine_record))
assert engine['metadata']['matched_phrases'] == ['box']
assert registry.inspect(version.version_sha256) == version
for name in ('agenticsim.openxsim.assets',
             'self_improving.asset_pipeline.active.asset_reuse.lib.a1_providers'):
    assert pathlib.Path(sys.modules[name].__file__).resolve().is_relative_to(site)
for name, module in tuple(sys.modules.items()):
    if name.split('.')[0] in {'agenticsim', 'self_improving', 'scene_gen'}:
        path = getattr(module, '__file__', None)
        assert path is None or pathlib.Path(path).resolve().is_relative_to(site), (name, path)
assert not {'torch', 'isaaclab_tasks', 'sapien', 'metasim'} & set(sys.modules)
print(json.dumps({'status': result.status, 'provider': result.candidates[0].provider,
                  'count': len(result.versions), 'physical_evaluated': version.physical_evaluated}))
"""
    result = _run([str(python), "-I", "-c", probe], cwd=root)
    assert json.loads(result.stdout) == {
        "status": "succeeded",
        "provider": "robotwin_local",
        "count": 1,
        "physical_evaluated": False,
    }
