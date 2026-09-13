"""Six fixed canonical CI groups; no business workflow or qualification authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

CANONICAL = "tests/self_improving/harness/x2env"
GROUPS = {
    "1": "acceptance_contract capabilities contracts input schema_export "
    "structured_schema stage_contracts",
    "2": "codex design_plan design_workflow diagnosis grounding "
    "harness_completion harness_lifecycle "
    "harness_repair_budget local_color_harness operation_recovery pipeline repair_reservations "
    "revision revision_reservation revision_state skill_execution store_contract_edges",
    "3": "artifact_closure artifact_store asset_advisory asset_index_store asset_preparation "
    "asset_preview asset_registry asset_revision local_catalog local_color_advisory "
    "local_color_execution normalization reconstruction_adapter reconstruction_planning "
    "reconstruction_resolver resolver search_advisory source_router web_resolver yuxin_adapter "
    "yuxin_package_migration",
    "4": "assessment compile genesis_runtime observation replay",
    "5": "cli completion completion_local_color delivery deployment deployment_edges "
    "failure_bundle import_boundary "
    "package publisher source_identity",
}
# External native/provider execution only; orchestration and normalization stay core.
EXTERNAL_ADAPTERS = (
    "adapters/reconstruction.py",
    "adapters/yuxin.py",
    "genesis_child.py",
)
# Only project source/config/committed test resources, never external runtime or asset trees.
SOURCE_ROOTS = (
    "scene_gen",
    "demo",
    "tests",
    "script",
    ".github",
    "README.md",
    "AGENTS.md",
    "repo-docs",
    "docs/contracts",
    "docs/history/golden-e2e",
    "self_improving/golden_e2e_progress",
    "pyproject.toml",
    ":(glob)self_improving/*.py",
    "self_improving/harness",
    "self_improving/stage5",
    "self_improving/alchedata",
    "self_improving/sim_adapters/agenticsim_runtime",
    "self_improving/asset_pipeline/active/asset_reuse",
    "self_improving/asset_pipeline/active/web",
    "self_improving/asset_pipeline/active/shared/openxsim",
)
PENDING_GATES = ("full_active_ruff_scope_and_legacy_debt",)
ACTIVE = (
    ("self_improving/stage5/tests", ".", (".", "self_improving/stage5")),
    (
        "self_improving/alchedata/tests",
        ".",
        ("self_improving/alchedata", "self_improving/alchedata/scripts"),
    ),
    (
        "self_improving/sim_adapters/agenticsim_runtime/tests",
        ".",
        ("self_improving/sim_adapters/agenticsim_runtime",),
    ),
    (
        "self_improving/asset_pipeline/active/asset_reuse/tests",
        "self_improving/asset_pipeline/active/asset_reuse",
        (
            "self_improving/asset_pipeline/active/asset_reuse",
            "self_improving/asset_pipeline/active/asset_reuse/scripts",
            "self_improving/asset_pipeline/active/shared/openxsim/source/agenticsim",
            ".",
        ),
    ),
    ("self_improving/asset_pipeline/active/web/tests", ".", (".",)),
    (
        "self_improving/asset_pipeline/active/shared/openxsim/tests",
        "self_improving/asset_pipeline/active/shared/openxsim",
        ("self_improving/asset_pipeline/active/shared/openxsim/source/agenticsim",),
    ),
)


@dataclass(frozen=True)
class TestCommand:
    cwd: Path
    tests: tuple[Path, ...]
    pythonpath: tuple[Path, ...]


def test_plan(root: Path) -> dict[str, tuple[TestCommand, ...]]:
    root = root.resolve()
    canonical = root / CANONICAL
    assigned = [
        canonical / f"test_{name}.py" for names in GROUPS.values() for name in names.split()
    ]
    if len(set(assigned)) != len(assigned) or set(assigned) != set(canonical.glob("test_*.py")):
        raise ValueError("canonical_test_classification_drift")
    provider = root / "self_improving/asset_pipeline/active/shared/openxsim/source/agenticsim"
    result = {
        group: (
            TestCommand(
                root,
                tuple(canonical / f"test_{name}.py" for name in names.split()),
                (root, provider),
            ),
        )
        for group, names in GROUPS.items()
    }
    retained = tuple(
        sorted(p for p in (root / "tests").rglob("test_*.py") if not p.is_relative_to(canonical))
    )
    commands = [TestCommand(root, retained, (root, provider))]
    for directory, cwd, paths in ACTIVE:
        files = tuple(sorted((root / directory).rglob("test_*.py")))
        if not files:
            raise ValueError(f"active_test_directory_missing:{directory}")
        commands.append(TestCommand(root / cwd, files, tuple(root / p for p in paths)))
    result["6"] = tuple(commands)
    return result


def source_identity(root: Path) -> dict:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root)

    files = (
        git(
            "ls-files",
            "-co",
            "--exclude-standard",
            "--",
            *SOURCE_ROOTS,
        )
        .decode()
        .splitlines()
    )
    members = []
    total_bytes = 0
    for name in sorted(set(files)):
        path = root / name
        if path.suffix in {".py", ".sh", ".toml", ".yaml", ".yml", ".json", ".md", ".c"}:
            if not path.is_file():
                continue
            if path.is_symlink():
                raise ValueError(f"source_identity_symlink:{name}")
            size = path.stat().st_size
            total_bytes += size
            if size > 32 * 1024**2 or total_bytes > 256 * 1024**2:
                raise ValueError("source_identity_byte_budget")
            members.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
    return {
        "head": git("rev-parse", "HEAD").decode().strip(),
        "dirty": bool(git("status", "--porcelain")),
        "diff_sha256": hashlib.sha256(git("diff", "HEAD", "--binary")).hexdigest(),
        "source_sha256": hashlib.sha256(json.dumps(members).encode()).hexdigest(),
    }


def run_bounded(
    command: list[str],
    *,
    output: Path,
    seconds=1770,
    cleanup_seconds=30,
    cwd: Path | None = None,
    identity: dict | None = None,
) -> dict:
    """GNU timeout owns the foreground process group, including descendant test tools."""
    output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    with (output / "log.txt").open("wb") as log:
        result = subprocess.run(
            [
                "timeout",
                "--signal=INT",
                f"--kill-after={cleanup_seconds}s",
                f"{seconds}s",
                *command,
            ],
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    receipt = {
        "status": (
            "passed"
            if result.returncode == 0
            else "timed_out"
            if result.returncode in (124, 137)
            else "failed"
        ),
        "exit_code": result.returncode,
        "wall_seconds": time.monotonic() - start,
        "command": command,
        "source": identity,
        "seconds": seconds,
        "cleanup_seconds": cleanup_seconds,
    }
    (output / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def worker(root: Path, group: str, output: Path) -> int:
    plan = test_plan(root)
    if group == "1":
        schema = subprocess.run(
            [sys.executable, str(root / "script/export_x2env_schemas.py"), "--check"],
            cwd=root,
        )
        if schema.returncode:
            return schema.returncode
    if group == "6":
        inventory = subprocess.run([sys.executable, "-m", "self_improving", "--json"], cwd=root)
        if inventory.returncode:
            return inventory.returncode
    if group == "5":
        docs = subprocess.run(
            [
                sys.executable,
                str(root / "script/check_reader_docs.py"),
                "--root",
                str(root),
                "--check-remote",
                "--output",
                str(output / "reader-docs.json"),
            ],
            cwd=root,
        )
        if docs.returncode:
            return docs.returncode
    if group == "root":
        commands = (TestCommand(root, (), plan["1"][0].pythonpath),)
    else:
        commands = plan[group]
    for index, command in enumerate(commands):
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(map(str, command.pythonpath)),
            "COVERAGE_FILE": str(output / f".coverage.{index}"),
        }
        args = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            *map(str, command.tests),
            f"--basetemp={output / f'tmp-{index}'}",
            f"--junitxml={output / f'junit-{index}.xml'}",
            f"--cov={root / 'self_improving/harness/x2env'}",
            "--cov-branch",
            "--cov-report=",
            "--cov-fail-under=0",
        ]
        result = subprocess.run(args, cwd=command.cwd, env=env)
        if result.returncode:
            return result.returncode
    return 0


def merge_groups(root: Path, output: Path) -> dict:
    """Fail closed on incomplete execution, identity drift, or missing core lines/branches."""
    identities = []
    data = []
    for group in map(str, range(1, 7)):
        directory = output / group
        if not (directory / "result.json").is_file():
            raise ValueError(f"missing_group:{group}")
        receipt = json.loads((directory / "result.json").read_text())
        if receipt["status"] != "passed" or receipt["exit_code"] != 0:
            raise ValueError(f"unsuccessful_group:{group}")
        identities.append(receipt["source"])
        expected = len(test_plan(root)[group])
        for index in range(expected):
            path = directory / f".coverage.{index}"
            if not path.is_file() or not (directory / f"junit-{index}.xml").is_file():
                raise ValueError(f"missing_group_artifacts:{group}:{index}")
            data.append(str(path))
    if any(identity != source_identity(root) for identity in identities):
        raise ValueError("group_source_identity_mismatch")
    env = {**os.environ, "COVERAGE_FILE": str(output / ".coverage.combined")}
    subprocess.run(
        [sys.executable, "-m", "coverage", "combine", "--keep", *data],
        cwd=root,
        env=env,
        check=True,
    )
    report = output / "coverage.json"
    subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", str(report)], cwd=root, env=env, check=True
    )
    subprocess.run(
        [sys.executable, "-m", "coverage", "xml", "-o", str(output / "coverage.xml")],
        cwd=root,
        env=env,
        check=True,
    )
    coverage = json.loads(report.read_text())
    if not coverage["meta"].get("branch_coverage"):
        raise ValueError("branch_coverage_missing")
    prefix = root / "self_improving/harness/x2env"
    files = {str((root / name).resolve()): value for name, value in coverage["files"].items()}
    failures = []
    for path in prefix.rglob("*.py"):
        if path.relative_to(prefix).as_posix() in EXTERNAL_ADAPTERS:
            continue
        value = files.get(str(path.resolve()))
        if (
            value is None
            or value["missing_lines"]
            or value.get("missing_branches")
            or value.get("excluded_lines")
        ):
            failures.append(str(path.relative_to(root)))
    gate = {
        "status": "failed" if failures else "passed",
        "core_failures": failures,
        "external_adapters": list(EXTERNAL_ADAPTERS),
        "source": identities[0],
        "require_statement_percent": 100,
        "require_branch_percent": 100,
        "pending_noncoverage_gates": list(PENDING_GATES),
    }
    (output / "coverage-gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    return gate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=[*map(str, range(1, 7)), "root", "merge", "all"])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output = (args.output or Path(tempfile.mkdtemp(prefix="x2env-ci-", dir="/var/tmp"))).resolve()
    if args.worker:
        if args.group == "merge":
            return 0 if merge_groups(root, output)["status"] == "passed" else 1
        return worker(root, args.group, output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "pending-gates.json").write_text(
        json.dumps(
            {
                "status": "not_run",
                "gates": list(PENDING_GATES),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"CI evidence: {output}", flush=True)
    groups = [*map(str, range(1, 7)), "root", "merge"] if args.group == "all" else [args.group]
    failed = False
    for group in groups:
        result = run_bounded(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                group,
                "--worker",
                "--output",
                str(output if group == "merge" else output / group),
            ],
            output=output / group,
            cwd=root,
            identity=source_identity(root),
        )
        failed |= result["status"] != "passed"
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
