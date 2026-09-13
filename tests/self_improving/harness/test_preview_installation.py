"""Installed preview provenance with an explicit no-GPU external runner double."""

import subprocess

from tests.self_improving.harness.test_packaging import _run
from tests.self_improving.harness.test_provider_installation import (
    installed_provider as installed_provider,
)


def test_installed_preview_records_distribution_without_denying_site_packages(installed_provider):
    root, python, _ = installed_provider
    probe = r"""
import hashlib, json, pathlib, sysconfig
import trimesh
from PIL import Image
from self_improving.harness.x2env import asset_preview
from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
from self_improving.harness.x2env.normalization import normalize_mesh
from self_improving.harness.x2env.store import Store
installed = pathlib.Path(sysconfig.get_paths()['purelib']).resolve()
assert pathlib.Path(asset_preview.__file__).resolve().is_relative_to(installed)
root = pathlib.Path.cwd()
store = Store(root / 'preview-state')
source = root / 'preview-source.glb'
source.write_bytes(trimesh.creation.box().export(file_type='glb'))
normalized = root / 'preview-normalized'
report = normalize_mesh(source, normalized, dimensions_m=(0.1, 0.1, 0.1),
                        up_axis='Z', mass_kg=0.1, friction=0.6)
evidence = store.write_artifact(b'fixture-only', 'text/plain')
version = AssetRegistry(store).register(
    'box', 'box', normalized, report['entrypoint'],
    files=tuple(f['path'] for f in report['files']),
    normalization_report=store.write_artifact(json.dumps(report).encode(), 'application/json'),
    license=AssetLicense(spdx='CC0-1.0', source_url='https://example.org/fixture',
                         evidence=evidence),
    source=AssetSource(kind='web', provider='fixture', source_ref='fixed', evidence=evidence),
    receipt=evidence)
denied = (str(root / 'explicit-denied'),)
def runner(scene, **kwargs):
    assert kwargs['denied_roots'] == denied
    out = kwargs['output_dir']
    out.mkdir()
    Image.new('RGB', (4, 4), 'red').save(out / 'double.png')
    return {'status': 'passed', 'simulator_executed': True,
            'media': {'frames': [{'path': 'double.png',
                      'png_sha256': hashlib.sha256(
                          (out / 'double.png').read_bytes()).hexdigest()}]}}
renderer = asset_preview.AssetPreviewRenderer(store, {}, root / 'preview', 1,
                                             runner=runner, denied_roots=denied)
proof = renderer.render(version)
assert proof.status == 'passed', proof
receipt = json.loads(store.read_artifact(proof.receipt))
identity = receipt['identity']
assert identity['kind'] == 'installed_distribution'
assert identity['distribution_name'] == 'robot-harness-gen-env'
assert len(identity['record_sha256']) == len(identity['metadata_sha256']) == 64
assert identity['authority'] == 'development_provenance_only'
assert 'head' not in identity
assert receipt['qualified'] is False and receipt['physical_profile'] == 'not_run'
assert {m['path'] for m in identity['members']} >= {'package_loader.py', 'genesis_child.py'}
from importlib import metadata
from self_improving.harness.x2env.source_identity import GitSourcePolicy, InstalledSourcePolicy
marker = root / '.git'
assert not marker.exists()
try:
    marker.write_text('gitdir: nonexistent\n')
    explicit = asset_preview.AssetPreviewRenderer(
        store, {}, root / 'explicit-installed', 1, runner=runner, denied_roots=denied,
        source_identity_policy=InstalledSourcePolicy()).render(version)
    assert explicit.status == 'passed', explicit
    assert json.loads(store.read_artifact(explicit.receipt))['identity'] == identity
finally:
    marker.unlink()
def forbidden(*args, **kwargs):
    raise AssertionError('unverified installed source reached runtime')
explicit_git = asset_preview.AssetPreviewRenderer(
    store, {}, root / 'explicit-git', 1, runner=forbidden,
    source_identity_policy=GitSourcePolicy(root=str(root))).render(version)
assert explicit_git.status == 'failed' and explicit_git.error_code == 'source_git_root_mismatch'
dist = metadata.distribution('robot-harness-gen-env')
record = next(pathlib.Path(dist.locate_file(f)) for f in dist.files
              if str(f).endswith('.dist-info/RECORD'))
targets = (
    (pathlib.Path(asset_preview.__file__).with_name('genesis_child.py'),
     b'\n# tampered child\n', None, 'source_record_member_mismatch'),
    (record.with_name('METADATA'), b'\nTampered: true\n', None, 'source_record_member_mismatch'),
    (record, b'\n', InstalledSourcePolicy(expected_record_sha256=identity['record_sha256']),
     'source_record_pin_mismatch'),
    (record, b'\n', None, 'source_record_invalid'),
)
for target, suffix, policy, error in targets:
    original = target.read_bytes()
    try:
        target.write_bytes(original + suffix)
        proof = asset_preview.AssetPreviewRenderer(
            store, {}, root / 'tampered-preview', 1, runner=forbidden,
            source_identity_policy=policy).render(version)
        assert proof.status == 'failed' and proof.error_code == error, proof
        assert proof.image is None
        assert json.loads(store.read_artifact(proof.receipt))['outputs'] == {}
    finally:
        target.write_bytes(original)
"""
    try:
        _run([str(python), "-I", "-B", "-c", probe], cwd=root)
    except subprocess.CalledProcessError as exc:
        raise AssertionError(exc.stderr) from exc
