"""Development provenance through real Git repositories, never runtime qualification."""

import subprocess

import pytest


def checkout(tmp_path):
    root = tmp_path / "checkout"
    module = root / "self_improving/harness/x2env"
    module.mkdir(parents=True)
    for name in (
        "asset_preview.py",
        "genesis_runtime.py",
        "genesis_child.py",
        "package_loader.py",
        "source_identity.py",
    ):
        (module / name).write_text("# fixed source\n")

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    return root, module, git("rev-parse", "HEAD")


def test_real_git_pin_and_dirty_sources_are_observed_without_qualification(tmp_path):
    from self_improving.harness.x2env.source_identity import (
        GitSourcePolicy,
        capture_source_identity,
    )

    root, module, head = checkout(tmp_path)
    policy = GitSourcePolicy(root=str(root), expected_commit=head)
    identity = capture_source_identity(module, policy=policy)
    assert identity.kind == "git_checkout"
    assert identity.head == head and identity.dirty is False
    assert identity.authority == "development_provenance_only"
    assert {m.path for m in identity.members} == {
        "asset_preview.py",
        "genesis_runtime.py",
        "genesis_child.py",
        "package_loader.py",
        "source_identity.py",
    }
    (module / "genesis_child.py").write_text("# modified source\n")
    changed = capture_source_identity(module, policy=policy)
    assert changed.head == head and changed.dirty is True
    assert changed.members != identity.members
    with pytest.raises(ValueError, match="source_git_commit_mismatch"):
        capture_source_identity(
            module, policy=GitSourcePolicy(root=str(root), expected_commit="0" * 40)
        )


def test_broken_checkout_marker_never_silently_selects_installed_distribution(tmp_path):
    from self_improving.harness.x2env.source_identity import capture_source_identity

    module = tmp_path / "broken/self_improving/harness/x2env"
    module.mkdir(parents=True)
    (tmp_path / "broken/.git").write_text("gitdir: nonexistent\n")
    with pytest.raises(ValueError, match="source_git_unavailable"):
        capture_source_identity(module)


def test_explicit_git_requires_executing_source_checkout_not_another_checkout(tmp_path):
    from self_improving.harness.x2env.source_identity import (
        GitSourcePolicy,
        capture_source_identity,
    )

    _, module, _ = checkout(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    root, _, head = checkout(other)
    with pytest.raises(ValueError, match="source_git_root_mismatch"):
        capture_source_identity(
            module, policy=GitSourcePolicy(root=str(root), expected_commit=head)
        )
