"""Retired qualified execution graph stays absent; stable lower-level modules stay usable."""

import os
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path


def test_old_qualified_execution_modules_are_absent(tmp_path):
    root = Path(__file__).resolve().parents[3]
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
    modules = (
        "self_improving.harness.application",
        "self_improving.harness.compile_cli",
        "self_improving.harness.replay_application",
        "self_improving.harness.qualify_compile",
        "self_improving.harness.qualify_replay",
        "self_improving.harness.replay_qualification",
        "self_improving.harness.run_receipts",
        "self_improving.harness.registry",
        "self_improving.harness.replay_dependencies",
        "self_improving.qualified_replay_cli",
        "self_improving.harness.handlers.text2env_compile",
        "self_improving.harness.handlers.text2env_compile_dependencies",
        "self_improving.harness.handlers.text2env_replay",
        "self_improving.harness.handlers.text2env_validate",
        "self_improving.harness.qualification",
        "self_improving.harness.schemas.qualification_report",
        "self_improving.harness.schema_catalog",
        "self_improving.harness.run_store",
        "self_improving.harness.golden_store",
        "self_improving.harness.assets",
        "self_improving.harness.package_store",
        "self_improving.harness.runtime_executor",
        "self_improving.harness.media_verifier",
        "self_improving.harness.media_sandbox",
        "self_improving.harness.validate_v2",
        "self_improving.validate_v2_snapshot",
    )
    program = f"""
import sys
from importlib.util import find_spec
assert sys.flags.no_site == 1
assert not any(name.startswith('__editable__') for name in sys.modules)
for name in {modules!r}:
    assert find_spec(name) is None, name
from self_improving.harness import runtime_assets, runtime_capability, runtime_events
from self_improving.harness.schemas import ArtifactRef, SkillDescriptor
from self_improving.harness.x2env.cli import main
from demo.app import create_app
assert callable(main) and callable(create_app)
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", program],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(root), *dependency_roots))},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_old_qualification_and_schema_resources_are_absent():
    root = Path(__file__).resolve().parents[3]
    assert not (root / "script/export_harness_schemas.py").exists()
    for directory in ("json_schemas", "qualified_skills"):
        assert not list((root / "self_improving/harness" / directory).rglob("*.json"))
    assert not (root / "self_improving/harness/native/media_sandbox.c").exists()
    assert list((root / "self_improving/harness/x2env/json_schemas").glob("*.json"))
    assert (root / "self_improving/golden_e2e_progress/physics-assertions-v1.json").is_file()
