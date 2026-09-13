"""Real offline wheel installation; no repository path injection or provider doubles."""

import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from tests.self_improving.harness.test_packaging import (
    AGENTICSIM_SOURCE,
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
