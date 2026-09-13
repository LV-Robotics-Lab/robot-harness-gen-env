"""Public CI plan/runner contracts; small external processes, never a synthetic suite pass."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from script.x2env_test_groups import test_plan as build_plan


def test_six_groups_cover_each_active_test_file_once():
    root = Path(__file__).resolve().parents[2]
    groups = build_plan(root)
    assert set(groups) == {str(n) for n in range(1, 7)}
    actual = [
        path for commands in groups.values() for command in commands for path in command.tests
    ]
    expected = set(root.joinpath("tests").rglob("test_*.py"))
    for directory in (
        "stage5/tests",
        "alchedata/tests",
        "sim_adapters/agenticsim_runtime/tests",
        "asset_pipeline/active/asset_reuse/tests",
        "asset_pipeline/active/web/tests",
        "asset_pipeline/active/shared/openxsim/tests",
    ):
        expected.update(root.joinpath("self_improving", directory).rglob("test_*.py"))
    assert set(actual) == expected
    assert len(actual) == len(set(actual))


def test_bounded_process_preserves_failure_and_timeout_logs(tmp_path):
    from script.x2env_test_groups import run_bounded

    failed = run_bounded(
        [sys.executable, "-c", "print('retained'); raise SystemExit(7)"],
        output=tmp_path / "failed",
        seconds=2,
        cleanup_seconds=1,
    )
    assert failed["exit_code"] == 7 and failed["status"] == "failed"
    assert "retained" in (tmp_path / "failed/log.txt").read_text()
    timed = run_bounded(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        output=tmp_path / "timed",
        seconds=0.1,
        cleanup_seconds=0.1,
    )
    assert timed["status"] == "timed_out"
    assert (tmp_path / "timed/result.json").is_file()


def test_merge_fails_closed_without_all_six_real_group_results(tmp_path):
    from script.x2env_test_groups import merge_groups

    with pytest.raises(ValueError, match="missing_group"):
        merge_groups(Path(__file__).resolve().parents[2], tmp_path)


def test_public_merge_reports_partial_real_measurement_without_percentage_block(tmp_path):
    """Real coverage measurement; synthetic shard receipts are a gate fixture, not CI success."""
    import shutil

    from script.x2env_test_groups import merge_groups, source_identity

    root = Path(__file__).resolve().parents[2]
    driver = tmp_path / "measurement.py"
    driver.write_text("import self_improving.harness.x2env.contracts\n")
    data = tmp_path / ".measured"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--branch",
            f"--data-file={data}",
            f"--source={root / 'self_improving/harness/x2env'}",
            str(driver),
        ],
        cwd=root,
        check=True,
        env={**__import__("os").environ, "PYTHONPATH": str(root)},
        timeout=30,
    )
    identity = source_identity(root)
    for group, commands in build_plan(root).items():
        directory = tmp_path / group
        directory.mkdir()
        (directory / "result.json").write_text(
            json.dumps(
                {"status": "passed", "exit_code": 0, "source": identity, "fixture_only": True}
            )
        )
        for index in range(len(commands)):
            shutil.copyfile(data, directory / f".coverage.{index}")
            (directory / f"junit-{index}.xml").write_text(
                '<testsuite name="synthetic gate fixture"/>'
            )
    gate = merge_groups(root, tmp_path)
    assert gate["status"] == "passed"
    assert gate["coverage_policy"] == "report_only"
    assert gate["core_gaps"]
    assert gate["totals"]["missing_lines"] > 0
    assert gate["require_statement_percent"] is None
    assert gate["require_branch_percent"] is None
    assert gate["reported_exclusions"]  # Existing coverage defaults include Protocol bodies.


def test_report_only_coverage_does_not_allow_failed_test_group(tmp_path):
    from script.x2env_test_groups import merge_groups

    directory = tmp_path / "1"
    directory.mkdir()
    (directory / "result.json").write_text(json.dumps({"status": "failed", "exit_code": 1}))
    with pytest.raises(ValueError, match="unsuccessful_group:1"):
        merge_groups(Path(__file__).resolve().parents[2], tmp_path)


def test_merge_rejects_missing_measurements_before_combining(tmp_path):
    from script.x2env_test_groups import merge_groups

    # Deliberately untrusted fixture, not a claim that a real group executed.
    directory = tmp_path / "1"
    directory.mkdir()
    (directory / "result.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "exit_code": 0,
                "source": {},
            }
        )
    )
    with pytest.raises(ValueError, match="missing_group_artifacts:1:0"):
        merge_groups(Path(__file__).resolve().parents[2], tmp_path)


def test_merge_rejects_cross_source_group_receipts(tmp_path):
    from script.x2env_test_groups import merge_groups

    root = Path(__file__).resolve().parents[2]
    # Invalid placeholders may only reach a rejection gate, never coverage combination/pass.
    for group, commands in build_plan(root).items():
        directory = tmp_path / group
        directory.mkdir()
        (directory / "result.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "exit_code": 0,
                    "source": {"head": "other"},
                }
            )
        )
        for index in range(len(commands)):
            (directory / f".coverage.{index}").write_bytes(b"invalid fixture")
            (directory / f"junit-{index}.xml").write_text("<invalid/>")
    with pytest.raises(ValueError, match="group_source_identity_mismatch"):
        merge_groups(root, tmp_path)


def test_ci_keeps_six_groups_and_independent_root():
    import yaml

    root = Path(__file__).resolve().parents[2]
    workflow = yaml.load((root / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    assert workflow["jobs"]["test"]["strategy"]["matrix"]["group"] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "root",
    ]
    assert workflow["jobs"]["coverage"]["needs"] == "test"
    for job in workflow["jobs"].values():
        upload = next(
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact@")
        )
        patterns = upload["with"]["path"].splitlines()
        assert all(pattern != "${{ env.CI_EVIDENCE }}" for pattern in patterns)
        assert all(
            pattern.rsplit("/", 1)[-1]
            in {
                "pending-gates.json",
                "reader-docs.json",
                "ruff.json",
                "log.txt",
                "result.json",
                "junit-*.xml",
                ".coverage.*",
                "coverage.json",
                "coverage.xml",
                "coverage-gate.json",
                ".coverage.combined",
            }
            for pattern in patterns
        )


@pytest.mark.parametrize(
    "relative",
    [
        "scene_gen/new.py",
        "demo/new.py",
        "self_improving/asset_pipeline/active/asset_reuse/lib/new.py",
        "self_improving/asset_pipeline/active/shared/openxsim/source/agenticsim/new.py",
        "repo-docs/new.md",
        "docs/contracts/new.md",
        "docs/history/golden-e2e/new.json",
        "self_improving/golden_e2e_progress/new.md",
    ],
)
def test_source_identity_binds_untracked_active_source_bytes(tmp_path, relative):
    from script.x2env_test_groups import source_identity

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    source = tmp_path / relative
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n")
    before = source_identity(tmp_path)
    source.write_text("value = 2\n")
    after = source_identity(tmp_path)
    assert before["head"] == after["head"] and before["dirty"] == after["dirty"]
    assert before["source_sha256"] != after["source_sha256"]


def test_public_merge_command_is_bounded_and_retains_missing_group_failure(tmp_path):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            str(root / "script/x2env_test_groups.py"),
            "merge",
            "--output",
            str(tmp_path),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    receipt = json.loads((tmp_path / "merge/result.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["seconds"] == 1770 and receipt["cleanup_seconds"] == 30
    assert "missing_group:1" in (tmp_path / "merge/log.txt").read_text()


def test_docs_group_stops_on_real_missing_reader_documents_before_tests(tmp_path):
    import shutil

    from script.x2env_test_groups import ACTIVE, CANONICAL, GROUPS, worker

    # Only a rejection fixture: these placeholders must never become a successful suite receipt.
    for names in GROUPS.values():
        for name in names.split():
            path = tmp_path / CANONICAL / f"test_{name}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
    for directory, _, _ in ACTIVE:
        path = tmp_path / directory / "test_placeholder.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    script = tmp_path / "script/check_reader_docs.py"
    script.parent.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[2] / "script/check_reader_docs.py", script)
    output = tmp_path / "result"
    output.mkdir()
    assert worker(tmp_path, "5", output) != 0
    report = json.loads((output / "reader-docs.json").read_text())
    assert report["local_links"] == report["archive_bytes"] == "failed"
    assert not list(output.glob("junit-*.xml"))


def test_retained_group_stops_on_real_active_lint_error_before_inventory(tmp_path):
    from script.x2env_test_groups import ACTIVE, CANONICAL, GROUPS, LINT_ROOTS, worker

    # Rejection-only workspace; no synthetic passing test or inventory result is supplied.
    for names in GROUPS.values():
        for name in names.split():
            path = tmp_path / CANONICAL / f"test_{name}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
    for directory, _, _ in ACTIVE:
        path = tmp_path / directory / "test_placeholder.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    for name in LINT_ROOTS:
        path = tmp_path / name
        if path.suffix == ".py":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        else:
            path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["F"]\n')
    (tmp_path / "scene_gen/bad.py").write_text("undefined_probe_symbol\n")
    output = tmp_path / "result"
    output.mkdir()
    assert worker(tmp_path, "6", output) != 0
    diagnostics = json.loads((output / "ruff.json").read_text())
    assert any(item["code"] == "F821" for item in diagnostics)
    assert not list(output.glob("junit-*.xml"))
