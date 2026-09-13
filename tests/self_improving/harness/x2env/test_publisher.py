"""Explicit gh/git subprocess boundary double; no real GitHub mutations."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest


class Remote:
    def __init__(self, commit, tag):
        self.commit, self.tag = commit, tag
        self.release = None
        self.assets = {}
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        result = b""
        code = 0
        error = b""
        if argv[0] == "git":
            result = (
                "tag"
                if "cat-file" in argv
                else "b" * 40
                if argv[-1] == f"refs/tags/{self.tag}"
                else self.commit
            ).encode() + b"\n"
            if argv[1] == "status":
                result = b""
        elif argv[1:3] == ["release", "create"]:
            assert "--draft" in argv and "--prerelease" in argv and "--verify-tag" in argv
            self.release = {
                "id": 7,
                "tag_name": self.tag,
                "draft": True,
                "prerelease": True,
                "html_url": "https://github.com/o/r/releases/tag/" + self.tag,
            }
        elif argv[1:3] == ["release", "upload"]:
            assert "--clobber" not in argv
            path = Path(argv[4])
            assert path.name not in self.assets
            self.assets[path.name] = (len(self.assets) + 1, path.read_bytes())
        elif argv[1:3] == ["release", "edit"]:
            self.release["draft"] = False
        elif argv[1] == "api":
            endpoint = argv[2]
            if "/git/ref/tags/" in endpoint:
                result = json.dumps({"object": {"type": "tag", "sha": "b" * 40}}).encode()
            elif "/git/tags/" in endpoint:
                result = json.dumps({"object": {"type": "commit", "sha": self.commit}}).encode()
            elif "/releases/tags/" in endpoint:
                if self.release is None:
                    code, error = 1, b"HTTP 404"
                else:
                    result = json.dumps(self.release).encode()
            elif endpoint.endswith("assets?per_page=100"):
                assert "--paginate" in argv and "--slurp" in argv
                rows = [
                    dict(
                        id=id,
                        name=name,
                        size=len(raw),
                        state="uploaded",
                        url=f"https://api.github.com/repos/o/r/releases/assets/{id}",
                        browser_download_url=f"https://github.com/o/r/releases/download/{self.tag}/{name}",
                    )
                    for name, (id, raw) in self.assets.items()
                ]
                result = json.dumps([rows[:1], rows[1:]]).encode()
            elif "/releases/assets/" in endpoint:
                id = int(endpoint.rsplit("/", 1)[1])
                result = next(raw for aid, raw in self.assets.values() if aid == id)
            else:
                raise AssertionError(argv)
        else:
            raise AssertionError(argv)
        if hasattr(kwargs.get("stdout"), "write"):
            kwargs["stdout"].write(result)
            result = None
        return subprocess.CompletedProcess(argv, code, result, error)


def test_draft_upload_download_receipt_then_publish_and_readonly_resume(tmp_path):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher, PublicationAsset

    commit = "a" * 40
    tag = "x2env-evidence-" + commit
    remote = Remote(commit, tag)
    raw = b"qualification fixture"
    sha = hashlib.sha256(raw).hexdigest()
    name = f"qv1-manifest-{sha}.json"
    path = tmp_path / name
    path.write_bytes(raw)
    asset = PublicationAsset(name=name, path=path, sha256=sha, size_bytes=len(raw))
    publisher = GitHubArtifactPublisher("o/r", tmp_path, runner=remote)
    result = publisher.publish(commit, tag, (asset,), output_root=tmp_path / "first")
    assert result.status == "published" and remote.release["draft"] is False
    assert len(remote.assets) == 2
    count = len(remote.calls)
    resumed = publisher.publish(commit, tag, (asset,), output_root=tmp_path / "readonly")
    assert resumed.status == "verified_readonly"
    assert not any(call[1] == "release" for call in remote.calls[count:])


def request(tmp_path):
    from self_improving.harness.x2env.publisher import PublicationAsset

    commit = "a" * 40
    tag = "x2env-evidence-" + commit
    data = b"fixture"
    sha = hashlib.sha256(data).hexdigest()
    name = f"qv1-evidence-{sha}.json"
    path = tmp_path / name
    path.write_bytes(data)
    return commit, tag, PublicationAsset(name, path, sha, len(data))


def test_different_annotated_tag_object_same_commit_cannot_publish(tmp_path):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)

    class DifferentAnnotation(Remote):
        def __call__(self, argv, **kwargs):
            if argv[1] == "api" and "/git/ref/tags/" in argv[2]:
                return subprocess.CompletedProcess(
                    argv, 0, json.dumps({"object": {"type": "tag", "sha": "c" * 40}}).encode(), b""
                )
            return super().__call__(argv, **kwargs)

    remote = DifferentAnnotation(commit, tag)
    result = GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
        commit, tag, (asset,), output_root=tmp_path / "different-tag"
    )
    assert result.status == "failed"
    assert result.error_code == "annotated_tag_object_mismatch"
    assert remote.release is None


def test_local_and_remote_annotation_cannot_drift_together_during_upload(tmp_path):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)

    class BothTagsChanged(Remote):
        def __call__(self, argv, **kwargs):
            if self.assets and argv[:2] == ["git", "rev-parse"] and argv[-1] == f"refs/tags/{tag}":
                return subprocess.CompletedProcess(argv, 0, b"c" * 40, b"")
            if self.assets and argv[1] == "api" and "/git/ref/" in argv[2]:
                return subprocess.CompletedProcess(
                    argv, 0, json.dumps({"object": {"type": "tag", "sha": "c" * 40}}).encode(), b""
                )
            return super().__call__(argv, **kwargs)

    remote = BothTagsChanged(commit, tag)
    result = GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
        commit, tag, (asset,), output_root=tmp_path / "tag-drift"
    )
    assert result.status == "failed"
    assert result.error_code == "frozen_tag_object_changed"
    assert remote.release["draft"]


def test_dirty_frozen_worktree_cannot_publish(tmp_path):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)

    class Dirty(Remote):
        def __call__(self, argv, **kwargs):
            if argv[:2] == ["git", "status"]:
                return subprocess.CompletedProcess(argv, 0, b" M source.py\n", b"")
            return super().__call__(argv, **kwargs)

    remote = Dirty(commit, tag)
    result = GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
        commit, tag, (asset,), output_root=tmp_path / "dirty"
    )
    assert result.status == "failed"
    assert result.error_code == "dirty_frozen_worktree"
    assert remote.release is None


def test_device_source_cannot_be_published_as_empty_file(tmp_path):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher, PublicationAsset

    commit, tag, _ = request(tmp_path)
    sha = hashlib.sha256(b"").hexdigest()
    asset = PublicationAsset(f"empty-{sha}.bin", Path("/dev/null"), sha, 0)
    remote = Remote(commit, tag)
    result = GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
        commit, tag, (asset,), output_root=tmp_path / "device"
    )
    assert result.status == "failed"
    assert result.error_code == "publication_source_must_be_regular"
    assert remote.release is None


def test_changed_staging_bytes_are_rejected_before_upload(tmp_path):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)
    output = tmp_path / "tampered"

    class StagingMutation(Remote):
        def __call__(self, argv, **kwargs):
            result = super().__call__(argv, **kwargs)
            if argv[1:3] == ["release", "create"]:
                (output / "staging" / asset.name).write_bytes(b"mutated staging")
            return result

    remote = StagingMutation(commit, tag)
    result = GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
        commit, tag, (asset,), output_root=output
    )
    assert result.status == "failed"
    assert result.error_code == "staged_asset_digest_mismatch"
    assert not remote.assets


@pytest.mark.parametrize(
    "location,payload",
    [
        ("release", []),
        ("release", 4),
        ("assets", [None]),
        ("assets", [[None]]),
        ("tag", {"object": None}),
    ],
)
def test_malformed_remote_metadata_returns_failure_receipt(tmp_path, location, payload):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)

    class Malformed(Remote):
        def __call__(self, argv, **kwargs):
            if argv[1] == "api" and (
                (location == "release" and "/releases/tags/" in argv[2])
                or (location == "assets" and argv[2].endswith("assets?per_page=100"))
                or (location == "tag" and "/git/ref/tags/" in argv[2])
            ):
                return subprocess.CompletedProcess(argv, 0, json.dumps(payload).encode(), b"")
            return super().__call__(argv, **kwargs)

    remote = Malformed(commit, tag)
    result = GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
        commit, tag, (asset,), output_root=tmp_path / "malformed"
    )
    assert result.status == "failed"
    assert result.receipt.is_file()
    assert not any(call[1:3] == ["release", "edit"] for call in remote.calls)


def test_post_seal_remote_asset_change_is_reported_invalid_without_cleanup(tmp_path):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)

    class ConcurrentUpload(Remote):
        def __call__(self, argv, **kwargs):
            result = super().__call__(argv, **kwargs)
            if argv[1:3] == ["release", "edit"]:
                self.assets["unapproved.json"] = (42, b"unapproved")
            return result

    remote = ConcurrentUpload(commit, tag)
    result = GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
        commit, tag, (asset,), output_root=tmp_path / "raced"
    )
    assert result.status == "failed"
    assert result.error_code == "published_asset_set_changed"
    assert remote.release["draft"] is False
    assert "unapproved.json" in remote.assets


@pytest.mark.parametrize(
    "fault",
    ["repository", "commit", "tag", "timeout", "empty", "relative_output", "symlink_output"],
)
def test_invalid_publication_requests_do_not_call_remote(tmp_path, fault):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)
    remote = Remote(commit, tag)
    kwargs = {"timeout": 5, "output_root": tmp_path / "attempt"}
    assets = (asset,)
    if fault == "repository":
        with pytest.raises(ValueError):
            GitHubArtifactPublisher("-o/r/evil", tmp_path, runner=remote)
        return
    if fault == "commit":
        commit = "a" * 39
    if fault == "tag":
        tag = "other"
    if fault == "timeout":
        kwargs["timeout"] = 1771
    if fault == "empty":
        assets = ()
    if fault == "relative_output":
        kwargs["output_root"] = Path("relative")
    if fault == "symlink_output":
        link = tmp_path / "link"
        link.symlink_to(tmp_path, target_is_directory=True)
        kwargs["output_root"] = link / "output"
    with pytest.raises(ValueError):
        GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
            commit, tag, assets, **kwargs
        )
    assert remote.calls == []


@pytest.mark.parametrize(
    "fault", ["name", "sha", "reserved", "negative_size", "size", "bytes", "relative", "symlink"]
)
def test_invalid_asset_is_rejected_before_remote_write(tmp_path, fault):
    from dataclasses import replace

    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)
    if fault == "name":
        asset = replace(asset, name="../" + asset.name)
    if fault == "sha":
        asset = replace(asset, sha256="z" * 64, name="z" * 64)
    if fault == "reserved":
        asset = replace(asset, name="qv1-publication-receipt-" + asset.name)
    if fault == "negative_size":
        asset = replace(asset, size_bytes=-1)
    if fault == "size":
        asset = replace(asset, size_bytes=asset.size_bytes + 1)
    if fault == "bytes":
        asset.path.write_bytes(b"changed")
    if fault == "relative":
        asset = replace(asset, path=Path(asset.path.name))
    if fault == "symlink":
        link = tmp_path / "source-link"
        link.symlink_to(asset.path)
        asset = replace(asset, path=link)
    remote = Remote(commit, tag)
    result = GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
        commit, tag, (asset,), output_root=tmp_path / "attempt"
    )
    assert result.status == "failed"
    assert remote.release is None and not remote.assets


@pytest.mark.parametrize(
    "fault", ["head", "lightweight_local", "lightweight_remote", "tag_blob", "tag_cycle"]
)
def test_frozen_git_identity_errors_prevent_any_release(tmp_path, fault):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)

    class BadIdentity(Remote):
        def __call__(self, argv, **kwargs):
            raw = None
            if fault == "head" and argv[:2] == ["git", "rev-parse"]:
                raw = b"c" * 40
            if fault == "lightweight_local" and argv[:2] == ["git", "cat-file"]:
                raw = b"commit"
            if fault == "lightweight_remote" and argv[1] == "api" and "/git/ref/" in argv[2]:
                raw = json.dumps({"object": {"type": "commit", "sha": commit}}).encode()
            if fault in {"tag_blob", "tag_cycle"} and argv[1] == "api" and "/git/tags/" in argv[2]:
                raw = json.dumps(
                    {"object": {"type": "blob" if fault == "tag_blob" else "tag", "sha": "b" * 40}}
                ).encode()
            if raw is not None:
                return subprocess.CompletedProcess(argv, 0, raw, b"")
            return super().__call__(argv, **kwargs)

    remote = BadIdentity(commit, tag)
    result = GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
        commit, tag, (asset,), output_root=tmp_path / "identity"
    )
    assert result.status == "failed"
    assert remote.release is None


@pytest.mark.parametrize("fault", ["duplicate", "uri", "size"])
def test_remote_asset_listing_must_be_unique_and_bound(tmp_path, fault):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)

    class BadListing(Remote):
        def __call__(self, argv, **kwargs):
            result = super().__call__(argv, **kwargs)
            if argv[1] == "api" and argv[2].endswith("assets?per_page=100") and self.assets:
                pages = json.loads(result.stdout)
                row = pages[0][0]
                if fault == "duplicate":
                    pages.append([dict(row)])
                if fault == "uri":
                    row["url"] = "https://example.org/other"
                if fault == "size":
                    row["size"] += 1
                result.stdout = json.dumps(pages).encode()
            return result

    remote = BadListing(commit, tag)
    result = GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
        commit, tag, (asset,), output_root=tmp_path / "badlisting"
    )
    assert result.status == "failed" and remote.release["draft"]
    assert result.error_code in {
        "duplicate_remote_assets",
        "invalid_remote_asset_metadata",
        "remote_asset_size_mismatch",
    }


@pytest.mark.parametrize(
    "fault",
    [
        "unconfirmed_create",
        "readonly_empty",
        "readonly_no_receipt",
        "before_asset",
        "before_receipt",
        "before_seal",
        "unconfirmed_seal",
        "final_state",
        "extra_after_receipt",
        "receipt_bytes",
        "staging_symlink",
        "staging_device",
    ],
)
def test_concurrent_release_and_staging_changes_preserve_failure(tmp_path, fault):
    import os

    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)
    output = tmp_path / "changed"

    class Changes(Remote):
        state_after_edit = 0
        edited = False

        def __call__(self, argv, **kwargs):
            if fault == "unconfirmed_seal" and argv[1:3] == ["release", "edit"]:
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            if argv[1:3] == ["release", "edit"]:
                self.edited = True
            result = super().__call__(argv, **kwargs)
            if argv[1:3] == ["release", "create"]:
                if fault == "unconfirmed_create":
                    self.release = None
                if fault in {"staging_symlink", "staging_device"}:
                    path = output / "staging" / asset.name
                    path.unlink()
                    if fault == "staging_symlink":
                        path.symlink_to(asset.path)
                    else:
                        os.mkfifo(path)
            if (
                argv[1] == "api"
                and argv[2].endswith("assets?per_page=100")
                and not self.assets
                and fault == "before_asset"
            ):
                self.release["draft"] = False
            if (
                argv[1] == "api"
                and "/releases/assets/" in argv[2]
                and len(self.assets) == 1
                and fault == "before_receipt"
            ):
                self.release["draft"] = False
            if (
                argv[1] == "api"
                and "/git/ref/" in argv[2]
                and len(self.assets) == 2
                and fault == "before_seal"
            ):
                self.release["draft"] = False
            if (
                argv[1:3] == ["release", "upload"]
                and len(self.assets) == 2
                and fault == "extra_after_receipt"
            ):
                self.assets["extra.json"] = (44, b"extra")
            if argv[1] == "api" and "/releases/tags/" in argv[2]:
                if self.edited:
                    self.state_after_edit += 1
                    if fault == "final_state" and self.state_after_edit == 2:
                        value = json.loads(result.stdout)
                        value["draft"] = True
                        result.stdout = json.dumps(value).encode()
                if fault == "receipt_bytes":
                    for path in (output / "staging").glob("qv1-publication-receipt-*"):
                        path.write_bytes(b"changed-receipt")
            return result

    remote = Changes(commit, tag)
    if fault.startswith("readonly"):
        remote.release = {
            "id": 7,
            "tag_name": tag,
            "draft": False,
            "prerelease": True,
            "html_url": f"https://github.com/o/r/releases/tag/{tag}",
        }
        if fault == "readonly_no_receipt":
            remote.assets[asset.name] = (1, asset.path.read_bytes())
    result = GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
        commit, tag, (asset,), output_root=output
    )
    assert result.status == "failed", result
    assert json.loads(result.receipt.read_bytes())["remote_cleanup_performed"] is False
    assert all("delete" not in call and "--clobber" not in call for call in remote.calls)


@pytest.mark.parametrize("phase", ["command", "copy", "final", "subprocess"])
def test_deadline_failure_is_receipted_without_secrets(tmp_path, monkeypatch, phase):
    import time

    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    class Deadline(Remote):
        def __call__(self, argv, **kwargs):
            if phase == "subprocess":
                raise subprocess.TimeoutExpired(argv, 1, stderr=b"SENSITIVE_TEST_SECRET")
            result = super().__call__(argv, **kwargs)
            if phase == "command" and argv[:2] == ["git", "status"]:
                clock[0] = 2.0
            if phase == "copy" and argv[1] == "api" and "/git/tags/" in argv[2]:
                clock[0] = 2.0
            if (
                phase == "final"
                and argv[1] == "api"
                and argv[2].endswith("assets?per_page=100")
                and self.release
                and not self.release["draft"]
            ):
                clock[0] = 2.0
            return result

    result = GitHubArtifactPublisher("o/r", tmp_path, runner=Deadline(commit, tag)).publish(
        commit, tag, (asset,), output_root=tmp_path / "deadline", timeout=1
    )
    assert result.status == "failed" and result.error_code == "publication_timeout"
    assert b"SENSITIVE_TEST_SECRET" not in result.receipt.read_bytes()


def test_partial_upload_failure_keeps_draft_and_resumes_without_clobber(tmp_path):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)

    class Interrupted(Remote):
        fail = True

        def __call__(self, argv, **kwargs):
            if argv[1:3] == ["release", "upload"] and self.assets and self.fail:
                self.fail = False
                return subprocess.CompletedProcess(argv, 1, b"", b"simulated interrupted upload")
            return super().__call__(argv, **kwargs)

    remote = Interrupted(commit, tag)
    publisher = GitHubArtifactPublisher("o/r", tmp_path, runner=remote)
    failed = publisher.publish(commit, tag, (asset,), output_root=tmp_path / "first")
    assert failed.status == "failed" and remote.release["draft"] and len(remote.assets) == 1
    success = publisher.publish(commit, tag, (asset,), output_root=tmp_path / "resume")
    assert success.status == "published"
    uploads = [call for call in remote.calls if call[1:3] == ["release", "upload"]]
    assert sum(Path(call[4]).name == asset.name for call in uploads) == 1


@pytest.mark.parametrize("fault", ["digest", "extra", "not_prerelease", "release_uri"])
def test_existing_release_conflict_never_overwrites_or_publishes(tmp_path, fault):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)
    remote = Remote(commit, tag)
    remote.release = dict(
        id=7,
        tag_name=tag,
        draft=True,
        prerelease=fault != "not_prerelease",
        html_url="https://example.org/release"
        if fault == "release_uri"
        else f"https://github.com/o/r/releases/tag/{tag}",
    )
    remote.assets[asset.name] = (1, b"changed" if fault == "digest" else asset.path.read_bytes())
    if fault == "extra":
        remote.assets["unknown.json"] = (2, b"unknown")
    result = GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
        commit, tag, (asset,), output_root=tmp_path / "attempt"
    )
    assert result.status == "failed" and remote.release["draft"]
    assert not any(call[1:3] == ["release", "edit"] for call in remote.calls)
    assert remote.assets[asset.name][1] == (
        b"changed" if fault == "digest" else asset.path.read_bytes()
    )


def test_tag_drift_during_upload_cannot_publish(tmp_path):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)

    class Drift(Remote):
        def __call__(self, argv, **kwargs):
            if argv[1] == "api" and "/git/tags/" in argv[2] and self.assets:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps({"object": {"type": "commit", "sha": "c" * 40}}).encode(),
                    b"",
                )
            return super().__call__(argv, **kwargs)

    remote = Drift(commit, tag)
    result = GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
        commit, tag, (asset,), output_root=tmp_path / "attempt"
    )
    assert result.status == "failed" and remote.release["draft"]


def test_asset_id_change_after_receipt_creation_cannot_seal_stale_receipt(tmp_path):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher

    commit, tag, asset = request(tmp_path)

    class Replaced(Remote):
        def __call__(self, argv, **kwargs):
            result = super().__call__(argv, **kwargs)
            if argv[1:3] == ["release", "upload"] and len(self.assets) == 2:
                _, raw = self.assets[asset.name]
                self.assets[asset.name] = (100, raw)
            return result

    remote = Replaced(commit, tag)
    result = GitHubArtifactPublisher("o/r", tmp_path, runner=remote).publish(
        commit, tag, (asset,), output_root=tmp_path / "attempt"
    )
    assert result.status == "failed" and remote.release["draft"]


def test_same_asset_set_is_idempotent_independent_of_input_order(tmp_path):
    from self_improving.harness.x2env.publisher import GitHubArtifactPublisher, PublicationAsset

    commit, tag, first = request(tmp_path)
    data = b"second"
    sha = hashlib.sha256(data).hexdigest()
    name = f"qv1-other-{sha}.json"
    path = tmp_path / name
    path.write_bytes(data)
    second = PublicationAsset(name, path, sha, len(data))
    remote = Remote(commit, tag)
    publisher = GitHubArtifactPublisher("o/r", tmp_path, runner=remote)
    assert (
        publisher.publish(commit, tag, (first, second), output_root=tmp_path / "one").status
        == "published"
    )
    assert (
        publisher.publish(commit, tag, (second, first), output_root=tmp_path / "two").status
        == "verified_readonly"
    )
