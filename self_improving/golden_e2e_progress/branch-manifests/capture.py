"""Read-only Git intake inventory; never copies source payloads or changes Git state."""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

LIMIT = 50 * 1024 * 1024
OWNED = "self_improving/golden_e2e_progress/branch-manifests/"
ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}


def git(root, *args, check=True):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=ENV)
    if check and result.returncode:
        raise RuntimeError(f"git {args[0]} failed ({result.returncode}) at {root}")
    return result.stdout


def string(root, *args):
    return git(root, *args).decode(errors="surrogateescape").strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sensitive(path):
    return bool(
        re.search(
            r"(^|/)(\.env(?:\..*)?|id_(?:rsa|ed25519)|credentials[^/]*|secrets?[^/]*)$|\.(pem|key)$",
            path,
            re.I,
        )
    )


def payload_record(root, path, oid=None, mode=None):
    result = {"path": path}
    if oid is not None:
        result.update(git_oid=oid, mode=mode)
        if mode == "160000":
            return {**result, "kind": "gitlink", "sha256": None}
        if set(oid) == {"0"}:
            return {**result, "kind": "deleted", "size_bytes": 0, "sha256": None}
        size = int(string(root, "cat-file", "-s", oid))
        result.update(kind="symlink" if mode == "120000" else "file", size_bytes=size)
        if sensitive(path) or size > LIMIT:
            return {
                **result,
                "sha256": None,
                "blocked": "sensitive_path" if sensitive(path) else "over_50MiB",
            }
        return {**result, "sha256": digest(git(root, "cat-file", "blob", oid))}
    target = Path(root) / path
    try:
        before = target.lstat()
    except FileNotFoundError:
        return {**result, "kind": "deleted", "size_bytes": 0, "sha256": None}
    result["size_bytes"] = before.st_size
    if sensitive(path) or before.st_size > LIMIT:
        return {
            **result,
            "sha256": None,
            "blocked": "sensitive_path" if sensitive(path) else "over_50MiB",
        }
    if stat.S_ISLNK(before.st_mode):
        data = os.fsencode(os.readlink(target))
        result["kind"] = "symlink"
    elif stat.S_ISREG(before.st_mode):
        data = target.read_bytes()
        result["kind"] = "file"
    else:
        return {**result, "kind": "directory_or_special", "sha256": None}
    after = target.lstat()
    result.update(
        sha256=digest(data),
        stable=(before.st_size, before.st_mtime_ns, before.st_ino)
        == (after.st_size, after.st_mtime_ns, after.st_ino),
    )
    return result


def dirty(root, recurse=True):
    status = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    index = {}
    for row in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if row:
            header, path = row.split(b"\t", 1)
            mode, oid, stage = header.decode().split()
            index.setdefault(os.fsdecode(path), []).append((mode, oid, stage))
    staged_paths = [
        os.fsdecode(p)
        for p in git(root, "diff", "--cached", "--name-only", "--no-renames", "-z").split(b"\0")
        if p
    ]
    unstaged_paths = [
        os.fsdecode(p)
        for p in git(root, "diff", "--name-only", "--no-renames", "-z").split(b"\0")
        if p
    ]
    untracked_paths = [
        os.fsdecode(p)
        for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if p
    ]
    staged = []
    for path in staged_paths:
        entries = index.get(path, [("000000", "0" * 40, "0")])
        staged.extend(
            {**payload_record(root, path, oid, mode), "index_stage": stage}
            for mode, oid, stage in entries
        )
    submodules = []
    if recurse:
        for path, entries in index.items():
            for mode, oid, stage in entries:
                if mode != "160000":
                    continue
                subroot = Path(root) / path
                initialized = (subroot / ".git").exists()
                item = {
                    "path": path,
                    "gitlink": oid,
                    "index_stage": stage,
                    "initialized": initialized,
                }
                if initialized:
                    item.update(
                        current_head=string(subroot, "rev-parse", "HEAD"), dirty=dirty(subroot)
                    )
                submodules.append(item)
    return {
        "status_sha256": digest(status),
        "staged": staged,
        "unstaged": [
            payload_record(root, p, index[p][0][1], "160000")
            if p in index and index[p][0][0] == "160000"
            else payload_record(root, p)
            for p in unstaged_paths
        ],
        "untracked": [payload_record(root, p) for p in untracked_paths if not p.startswith(OWNED)],
        "capture_owned_excluded": [p for p in untracked_paths if p.startswith(OWNED)],
        "submodules": submodules,
        "status_stable": status
        == git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"),
    }


def refs(root):
    fields = (
        "%(refname)%00%(objectname)%00%(tree)%00%(upstream)%00%(symref)%00"
        "%(authorname)%00%(authoremail)"
    )
    return [
        dict(
            zip(
                ("ref", "commit", "tree", "upstream", "symref", "author_name", "author_email"),
                row.split("\0"),
            )
        )
        for row in string(
            root, "for-each-ref", f"--format={fields}", "refs/heads", "refs/remotes/origin"
        ).splitlines()
    ]


def worktrees(root):
    records, current = [], {}
    for token in git(root, "worktree", "list", "--porcelain", "-z").split(b"\0"):
        if not token:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = os.fsdecode(token).partition(" ")
        current[key] = value or True
    return records


def capture(root):
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    start_refs, start_worktrees = refs(root), worktrees(root)
    baselines = {
        "bingsheng": string(root, "rev-parse", "ea26524^{commit}"),
        "integration": string(root, "rev-parse", "6e88985^{commit}"),
    }
    relations = {}
    for row in start_refs:
        commit = row["commit"]
        if commit not in relations:
            relations[commit] = {}
            for label, base in baselines.items():
                bases = string(root, "merge-base", "--all", base, commit).splitlines()
                behind, ahead = map(
                    int,
                    string(
                        root, "rev-list", "--left-right", "--count", base + "..." + commit
                    ).split(),
                )
                cherry = [
                    {"equivalent": line[0] == "-", "commit": line[2:]}
                    for line in string(root, "cherry", base, commit).splitlines()
                ]
                relations[commit][label] = {
                    "merge_bases": bases,
                    "ahead": ahead,
                    "behind": behind,
                    "cherry": cherry,
                }
        row.update(
            relations=relations[commit],
            source_author={"name": row.pop("author_name"), "email": row.pop("author_email")},
            origin_owner="pending_source_audit",
            upstream_owner="pending_source_audit",
            integration_owner="Bingsheng Harness workstream (responsibility label)",
            disposition={
                "status": "pending",
                "target_feature_commit": None,
                "basis": (
                    "Canonical feature selection not yet audited; ancestry and patch equivalence "
                    "recorded separately"
                ),
            },
        )
    trees = []
    for entry in start_worktrees:
        item = {
            **entry,
            "detached": bool(entry.get("detached")),
            "prunable": entry.get("prunable", False),
        }
        path = Path(entry["worktree"])
        if path.is_dir() and not entry.get("prunable"):
            item.update(
                commit=string(path, "rev-parse", "HEAD"),
                tree=string(path, "rev-parse", "HEAD^{tree}"),
                dirty=dirty(path),
            )
        else:
            item["capture_status"] = "unavailable_prunable_or_missing"
        trees.append(item)
    end_refs, end_worktrees = refs(root), worktrees(root)
    for item in trees:
        if "dirty" in item:
            after = dirty(item["worktree"])
            item["dirty_stable_through_capture"] = item["dirty"] == after
            if not item["dirty_stable_through_capture"]:
                item["dirty_at_end"] = after
    initial_identity = [(r["ref"], r["commit"], r["upstream"], r["symref"]) for r in start_refs]
    end_identity = [(r["ref"], r["commit"], r["upstream"], r["symref"]) for r in end_refs]
    return {
        "schema_version": "harness.branch_intake.v1",
        "capture_started_at": started,
        "capture_finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "repo": str(root),
        "remote_observation": {
            "basis": (
                "origin refs after parent-reported successful fetch --prune; "
                "no network read by capture"
            ),
            "observed_at": started,
        },
        "baselines": baselines,
        "refs": start_refs,
        "worktrees": trees,
        "consistency": {
            "atomic": False,
            "refs_stable": initial_identity == end_identity,
            "worktree_registration_stable": start_worktrees == end_worktrees,
            "end_refs": end_refs,
        },
        "scope": {
            "ignored_files": "excluded",
            "gitdirs": "not_traversed",
            "payloads": "never_copied",
            "file_hash_limit_bytes": LIMIT,
            "secret_scan": (
                "sensitive filename guard only; parent snapshot requires separate content scan"
            ),
            "archive_snapshot": "not_created",
            "dispositions": "pending_canonical_source_audit",
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = capture(args.repo)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=True)
        stream.write("\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "refs": len(result["refs"]),
                "worktrees": len(result["worktrees"]),
                "consistency": {k: v for k, v in result["consistency"].items() if k != "end_refs"},
            }
        )
    )
