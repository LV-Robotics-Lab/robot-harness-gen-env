"""Bounded private operator diagnostics, never source or runtime qualification authority."""

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

_EXCLUDED = {
    "assets",
    "outputs",
    "output",
    "data",
    "datasets",
    "external",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "secrets",
    "credentials",
}
_MAX_SOURCE_BYTES = 2 * 1024 * 1024


def capture_invocation_identity(workdir, configuration):
    """Capture Git/source/config identity without scanning declared runtime or asset bytes.

    configuration is a JSON file Path or JSON-compatible mapping. This snapshot is not
    atomic across concurrent edits; the existing development authority remains mandatory.
    """
    workdir = Path(workdir).resolve(strict=True)
    if isinstance(configuration, (str, Path)):
        payload = Path(configuration).read_bytes()
        config = json.loads(payload)
        basis = "raw_file_bytes"
    else:
        config = dict(configuration)
        payload = json.dumps(
            config, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        basis = "canonical_json"
    roots = config.get("runtime_roots", {})
    if not isinstance(roots, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in roots.items()
    ):
        raise ValueError("runtime_roots must declare string paths")

    def git(*args):
        return subprocess.check_output(
            ["git", "--no-optional-locks", "-C", str(workdir), *args],
            stderr=subprocess.DEVNULL,
        )

    root = Path(git("rev-parse", "--show-toplevel").decode().strip())
    if root != workdir:
        raise ValueError("workdir must be the Git worktree root")
    head = git("rev-parse", "HEAD").decode().strip()
    status = git(
        "status", "--porcelain=v1", "-z", "--untracked-files=normal", "--ignore-submodules=dirty"
    ).split(b"\0")
    entries = []
    iterator = iter(status)
    for record in iterator:
        if not record:
            continue
        item = {"status": record[:2].decode(), "path": record[3:].decode(errors="replace")}
        if "R" in item["status"] or "C" in item["status"]:
            item["original_path"] = next(iterator).decode(errors="replace")
        entries.append(item)
    # Stream diff to a digest; neither diff nor configuration plaintext is returned.
    with subprocess.Popen(
        [
            "git",
            "--no-optional-locks",
            "-C",
            str(root),
            "diff",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=dirty",
            "HEAD",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as process:
        digest = hashlib.sha256()
        for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
            digest.update(chunk)
        if process.wait() != 0:
            raise ValueError("Git diff identity could not be captured")
    pathspec = ["*.py", "pyproject.toml"] + [
        f":(exclude)**/{name}/**" for name in sorted(_EXCLUDED)
    ]
    sources = {}
    skipped = []
    for raw in git("ls-files", "--others", "--exclude-standard", "-z", "--", *pathspec).split(
        b"\0"
    ):
        if not raw:
            continue
        relative = Path(raw.decode())
        path = root / relative
        if any(part in _EXCLUDED for part in relative.parts):
            continue
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root):
            skipped.append({"path": relative.as_posix(), "reason": "symlink"})
            continue
        if not path.is_file() or path.stat().st_size > _MAX_SOURCE_BYTES:
            skipped.append({"path": relative.as_posix(), "reason": "not_small_regular_source"})
            continue
        sources[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "schema_version": "harness.experiment_invocation_identity.v1",
        "authority": "private_operator_diagnostic_only",
        "snapshot_atomic": False,
        "git": {
            "head": head,
            "dirty": bool(entries),
            "porcelain": entries,
            "diff_sha256": digest.hexdigest(),
            "diff_basis": "git_diff_binary_HEAD",
            "untracked_source_sha256": sources,
            "untracked_source_skipped": skipped,
        },
        "python": {"executable": sys.executable, "version": platform.python_version()},
        "configuration": {"sha256": hashlib.sha256(payload).hexdigest(), "hash_basis": basis},
        "runtime": {
            "roots": roots,
            "declaration_sha256": hashlib.sha256(
                json.dumps(roots, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "identity_basis": "declared_root_paths_only_not_runtime_bytes",
            "verification_status": "declared_not_deep_verified",
        },
    }
