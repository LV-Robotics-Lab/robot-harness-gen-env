"""Fresh-process import boundary; never substitute sys.modules or legacy adapters."""

import os
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path


def test_superseded_workflows_are_not_importable_in_fresh_process(tmp_path):
    root = Path(__file__).resolve().parents[4]
    # Resolve the actual dependency locations, including path-only .pth environments.
    # The child keeps -S: no editable finder or other site startup hook is executed.
    dependencies = (
        "pydantic",
        "pydantic_core",
        "annotated_types",
        "typing_extensions",
        "typing_inspection",
        "flask",
        "werkzeug",
        "jinja2",
        "click",
        "itsdangerous",
        "markupsafe",
        "blinker",
        "yaml",
        "PIL",
    )
    dependency_roots = []
    for name in dependencies:
        spec = find_spec(name)
        if spec is None:
            # Transitive requirements vary across supported dependency versions.
            assert name not in {"pydantic", "flask", "yaml", "PIL"}, name
            continue
        assert spec.origin is not None, name
        origin = Path(spec.origin).resolve()
        directory = origin.parent.parent if spec.submodule_search_locations else origin.parent
        if str(directory) not in dependency_roots:
            dependency_roots.append(str(directory))
    program = """
import sys
from importlib.util import find_spec
assert sys.flags.no_site == 1
assert not any(name.startswith('__editable__') for name in sys.modules)
for name in (
    'self_improving.harness.system2.planner',
    'self_improving.harness.system2.dispatcher',
    'self_improving.harness.system2.context',
    'self_improving.harness.system2.domain',
    'self_improving.harness.system2.history',
    'self_improving.harness.system2.qwen_local',
    'self_improving.harness.golden_run',
    'self_improving.system2_compile_turn',
    'self_improving.system2_replay_handoff',
    'self_improving.system2_replay_input_promotion',
    'self_improving.system2_replay_evidence_acquisition',
    'self_improving.system2_replay_snapshot_assessment',
    'self_improving.system2_replay_snapshot_validation',
    'self_improving.harness.qwen_replay_vlm_provider',
    'self_improving.harness.replay_vlm_worker',
    'self_improving.harness.replay_vlm_assessment',
    'demo.harness_replay_assessment',
):
    try:
        spec = find_spec(name)
    except ModuleNotFoundError:
        spec = None
    assert spec is None, name
from self_improving.harness.x2env.cli import main
from self_improving.harness import runtime_assets, runtime_capability, runtime_events
from demo.app import create_app
assert callable(main) and callable(create_app)
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", program],
        cwd=tmp_path,
        # Load real dependencies without unrelated editable-install .pth finders.
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(root), *dependency_roots])},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_canonical_import_and_cli_do_not_initialize_legacy_graph(tmp_path):
    root = Path(__file__).resolve().parents[4]
    program = """
import sys
import self_improving.harness.x2env
from self_improving.harness.x2env.cli import main
for name in (
    'self_improving.harness.application',
    'self_improving.harness.qualification',
    'self_improving.harness.golden_run',
    'self_improving.harness.registry',
    'self_improving.harness.system2.planner',
    'self_improving.harness.qwen_replay_vlm_provider',
):
    assert name not in sys.modules, name
assert callable(main)
assert main(['status', '--workflow-id', 'missing']) == 1
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert '"error_code": "invalid_request"' in result.stdout
