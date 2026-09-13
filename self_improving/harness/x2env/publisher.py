"""No-clobber GitHub evidence prerelease publisher. Tags are caller-owned and read-only.

Uses gh's documented release flags and REST assets Accept: application/octet-stream.
https://docs.github.com/en/rest/releases/assets#get-a-release-asset
"""

import hashlib
import json
import os
import re
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class PublicationAsset:
    name: str
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class PublicationResult:
    status: Literal["published", "verified_readonly", "failed"]
    receipt: Path
    release_id: int | None
    error_code: str | None


def _hash(path):
    digest = hashlib.sha256()
    size = 0
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symbolic_publication_source")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("publication_source_must_be_regular")
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(data)
            digest.update(data)
    return digest.hexdigest(), size


class GitHubArtifactPublisher:
    def __init__(self, repo, worktree, *, gh="gh", runner=subprocess.run):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("invalid GitHub repository")
        self.repo, self.worktree, self.gh, self.runner = repo, Path(worktree), gh, runner

    def publish(self, commit, tag, assets, *, output_root, timeout=1770):
        """Transport already-qualified caller artifacts; this module never grants qualification.

        The qualification caller must validate the frozen manifest and approve the
        complete asset set before invocation. A published/downloaded result is not
        evidence that this precondition was met, nor authorization to advance Git.
        """
        if (
            not re.fullmatch(r"[0-9a-f]{40}", commit)
            or not re.fullmatch(r"x2env-evidence-" + commit + r"(?:-attempt-[1-9][0-9]*)?", tag)
            or type(timeout) is not int
            or not 1 <= timeout <= 1770
            or not assets
        ):
            raise ValueError("invalid frozen publication request")
        root = Path(output_root)
        if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError("publication output must be new absolute nonsymbolic directory")
        root.mkdir(parents=True, exist_ok=False)
        staging = root / "staging"
        staging.mkdir()
        downloads = root / "downloads"
        downloads.mkdir()
        start = time.monotonic()
        calls, verified = [], []
        release = None
        frozen_tag_object = None
        status, error = "failed", None
        prefix = f"repos/{self.repo}"

        def command(argv, *, destination=None, allow404=False):
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                raise ValueError("publication_timeout")
            index = len(calls)
            record = {"argv": argv, "index": index}
            calls.append(record)
            try:
                options = dict(
                    cwd=self.worktree,
                    stdin=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=remaining,
                    check=False,
                )
                if destination:
                    with destination.open("xb") as output:
                        result = self.runner(argv, stdout=output, **options)
                else:
                    result = self.runner(argv, stdout=subprocess.PIPE, **options)
                record.update(
                    exit_code=result.returncode,
                    stderr_sha256=hashlib.sha256(result.stderr or b"").hexdigest(),
                    stderr_bytes=len(result.stderr or b""),
                )
                if result.returncode:
                    if allow404 and b"HTTP 404" in (result.stderr or b""):
                        return None
                    raise ValueError("github_or_git_command_failed")
                if destination:
                    return destination
                return result.stdout
            finally:
                (root / "commands.json").write_text(json.dumps(calls, sort_keys=True))

        def api(endpoint, *extra, allow404=False):
            raw = command([self.gh, "api", endpoint, *extra], allow404=allow404)
            return json.loads(raw) if raw is not None else None

        def release_state():
            found = api(f"{prefix}/releases/tags/{tag}", allow404=True)
            if found is not None and (
                not isinstance(found, dict)
                or found.get("tag_name") != tag
                or found.get("prerelease") is not True
                or type(found.get("id")) is not int
                or found["id"] <= 0
                or type(found.get("draft")) is not bool
                or found.get("html_url") != f"https://github.com/{self.repo}/releases/tag/{tag}"
            ):
                raise ValueError("invalid_release_identity_or_prerelease")
            return found

        def listed():
            pages = api(
                f"{prefix}/releases/{release['id']}/assets?per_page=100", "--paginate", "--slurp"
            )
            if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
                raise ValueError("invalid_remote_asset_metadata")
            rows = [row for page in pages for row in page]
            if any(
                not isinstance(row, dict)
                or not isinstance(row.get("name"), str)
                or type(row.get("id")) is not int
                or type(row.get("size")) is not int
                or row["size"] < 0
                for row in rows
            ):
                raise ValueError("invalid_remote_asset_metadata")
            if len({r["name"] for r in rows}) != len(rows) or len({r["id"] for r in rows}) != len(
                rows
            ):
                raise ValueError("duplicate_remote_assets")
            for row in rows:
                if (
                    type(row["id"]) is not int
                    or row["id"] <= 0
                    or row.get("state") != "uploaded"
                    or row.get("url")
                    != f"https://api.github.com/{prefix}/releases/assets/{row['id']}"
                    or row.get("browser_download_url")
                    != f"https://github.com/{self.repo}/releases/download/{tag}/{row['name']}"
                ):
                    raise ValueError("invalid_remote_asset_metadata")
            return {r["name"]: r for r in rows}

        def verify(row, asset):
            if row["size"] != asset.size_bytes:
                raise ValueError("remote_asset_size_mismatch")
            path = downloads / f"{len(calls)}-{row['id']}"
            command(
                [
                    self.gh,
                    "api",
                    f"{prefix}/releases/assets/{row['id']}",
                    "-H",
                    "Accept: application/octet-stream",
                ],
                destination=path,
            )
            if _hash(path) != (asset.sha256, asset.size_bytes):
                raise ValueError("remote_asset_digest_mismatch")
            return {
                "name": asset.name,
                "asset_id": row["id"],
                "api_uri": row["url"],
                "download_uri": row["browser_download_url"],
                "size_bytes": asset.size_bytes,
                "sha256": asset.sha256,
                "download_verified": True,
            }

        def frozen_identity():
            nonlocal frozen_tag_object
            if command(["git", "status", "--porcelain", "--untracked-files=normal"]).strip():
                raise ValueError("dirty_frozen_worktree")
            for expression in ("HEAD", f"refs/tags/{tag}^{{commit}}"):
                actual = command(["git", "rev-parse", expression]).decode().strip()
                if actual != commit:
                    raise ValueError("local_commit_or_tag_mismatch")
            if command(["git", "cat-file", "-t", f"refs/tags/{tag}"]).strip() != b"tag":
                raise ValueError("evidence_tag_must_be_annotated")
            tag_object = command(["git", "rev-parse", f"refs/tags/{tag}"]).decode().strip()
            remote = api(f"{prefix}/git/ref/tags/{tag}")["object"]
            if remote["type"] != "tag":
                raise ValueError("remote_tag_not_annotated")
            if not re.fullmatch(r"[0-9a-f]{40}", tag_object) or remote.get("sha") != tag_object:
                raise ValueError("annotated_tag_object_mismatch")
            for _ in range(4):
                if remote["type"] == "commit":
                    break
                if remote["type"] != "tag":
                    raise ValueError("invalid_remote_tag_object")
                remote = api(f"{prefix}/git/tags/{remote['sha']}")["object"]
            if remote != {"type": "commit", "sha": commit} and not (
                remote.get("type") == "commit" and remote.get("sha") == commit
            ):
                raise ValueError("remote_commit_mismatch")
            if frozen_tag_object is not None and frozen_tag_object != tag_object:
                raise ValueError("frozen_tag_object_changed")
            frozen_tag_object = tag_object

        try:
            frozen_identity()
            planned = {}
            for asset in assets:
                if (
                    not re.fullmatch(r"[A-Za-z0-9_.-]+", asset.name)
                    or asset.sha256 not in asset.name
                    or not re.fullmatch(r"[0-9a-f]{64}", asset.sha256)
                    or asset.name in planned
                    or asset.name.startswith("qv1-publication-receipt-")
                    or type(asset.size_bytes) is not int
                    or asset.size_bytes < 0
                ):
                    raise ValueError("invalid_digest_asset_name")
                source = Path(asset.path)
                if not source.is_absolute():
                    raise ValueError("publication_source_must_be_absolute")
                if any(p.is_symlink() for p in (source, *source.parents)):
                    raise ValueError("symbolic_publication_source")
                copy = staging / asset.name
                # Nonblocking open prevents a raced-in FIFO from hanging before its type check.
                fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as src:
                    metadata = os.fstat(src.fileno())
                    if not stat.S_ISREG(metadata.st_mode):
                        raise ValueError("publication_source_must_be_regular")
                    if metadata.st_size != asset.size_bytes:
                        raise ValueError("local_asset_digest_mismatch")
                    with copy.open("xb") as dst:
                        while data := src.read(1024 * 1024):
                            if time.monotonic() - start >= timeout:
                                raise ValueError("publication_timeout")
                            dst.write(data)
                if _hash(copy) != (asset.sha256, asset.size_bytes):
                    raise ValueError("local_asset_digest_mismatch")
                planned[asset.name] = PublicationAsset(
                    asset.name, copy, asset.sha256, asset.size_bytes
                )
            planned = dict(sorted(planned.items()))
            release = release_state()
            if release is None:
                command(
                    [
                        self.gh,
                        "release",
                        "create",
                        tag,
                        "--repo",
                        self.repo,
                        "--verify-tag",
                        "--target",
                        commit,
                        "--draft",
                        "--prerelease",
                        "--latest=false",
                        "--title",
                        tag,
                        "--notes",
                        "qualification evidence only; not a main merge or product release",
                    ]
                )
                release = release_state()
                if release is None or not release["draft"]:
                    raise ValueError("draft_creation_not_confirmed")
            readonly = not release["draft"]
            remote_assets = listed()
            for name, asset in planned.items():
                if name not in remote_assets:
                    if readonly:
                        raise ValueError("published_release_incomplete_readonly")
                    current = release_state()
                    if current is None or current["id"] != release["id"] or not current["draft"]:
                        raise ValueError("release_became_published_or_changed")
                    if _hash(asset.path) != (asset.sha256, asset.size_bytes):
                        raise ValueError("staged_asset_digest_mismatch")
                    command(
                        [self.gh, "release", "upload", tag, str(asset.path), "--repo", self.repo]
                    )
                    remote_assets = listed()
                verified.append(verify(remote_assets[name], asset))
            publication = {
                "schema_version": "x2env.publication_receipt.v1",
                "commit": commit,
                "tag": tag,
                "annotated_tag_object": frozen_tag_object,
                "repository": self.repo,
                "release_id": release["id"],
                "release_uri": release["html_url"],
                "assets": verified,
                "scope": "qualification_evidence_only",
                "qualification_verified_by_publisher": False,
                "prerelease": True,
            }
            raw = json.dumps(publication, sort_keys=True, separators=(",", ":")).encode()
            sha = hashlib.sha256(raw).hexdigest()
            name = f"qv1-publication-receipt-{sha}.json"
            path = staging / name
            path.write_bytes(raw)
            receipt_asset = PublicationAsset(name, path, sha, len(raw))
            expected = set(planned) | {name}
            if set(remote_assets) - expected:
                raise ValueError("unexpected_remote_assets")
            if name not in remote_assets:
                if readonly:
                    raise ValueError("published_release_missing_receipt_readonly")
                current = release_state()
                if current is None or current["id"] != release["id"] or not current["draft"]:
                    raise ValueError("release_became_published_or_changed")
                if _hash(path) != (receipt_asset.sha256, receipt_asset.size_bytes):
                    raise ValueError("staged_asset_digest_mismatch")
                command([self.gh, "release", "upload", tag, str(path), "--repo", self.repo])
            remote_assets = listed()
            if set(remote_assets) != expected:
                raise ValueError("remote_asset_set_mismatch")
            # Re-download every asset from the final listing before changing draft state.
            verified = [
                verify(remote_assets[a.name], a) for a in (*planned.values(), receipt_asset)
            ]
            if verified[:-1] != publication["assets"]:
                raise ValueError("publication_receipt_asset_identity_changed")
            if not readonly:
                frozen_identity()
                current = release_state()
                if current is None or current["id"] != release["id"] or not current["draft"]:
                    raise ValueError("release_became_published_or_changed")
                command(
                    [
                        self.gh,
                        "release",
                        "edit",
                        tag,
                        "--repo",
                        self.repo,
                        "--draft=false",
                        "--prerelease",
                        "--latest=false",
                    ]
                )
                current = release_state()
                if current is None or current["id"] != release["id"] or current["draft"]:
                    raise ValueError("publish_not_confirmed")
            frozen_identity()
            current = release_state()
            if current is None or current["id"] != release["id"] or current["draft"]:
                raise ValueError("published_release_state_changed")
            if listed() != remote_assets:
                raise ValueError("published_asset_set_changed")
            if time.monotonic() - start > timeout:
                raise ValueError("publication_timeout")
            status = "verified_readonly" if readonly else "published"
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            error = (
                "publication_timeout" if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
            )
        final = root / "publication-result.json"
        final.write_text(
            json.dumps(
                {
                    "status": status,
                    "error_code": error,
                    "commit": commit,
                    "tag": tag,
                    "annotated_tag_object": frozen_tag_object,
                    "release": release,
                    "verified_assets": verified,
                    "commands": calls,
                    "wall_seconds": time.monotonic() - start,
                    "remote_cleanup_performed": False,
                    "authority": "artifact_transport_and_download_verification_only",
                    "qualification_verified_by_publisher": False,
                },
                sort_keys=True,
            )
        )
        return PublicationResult(status, final, release["id"] if release else None, error)
