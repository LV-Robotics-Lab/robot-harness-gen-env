"""Development provenance through real Git repositories, never runtime qualification."""

import base64
import csv
import hashlib
import io
import subprocess
import sys

import pytest


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """Real metadata discovery over an unsigned, minimal installed-layout fixture."""
    root = tmp_path / "site"
    module = root / "self_improving/harness/x2env"
    module.mkdir(parents=True)
    info = root / "robot_harness_gen_env-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Name: robot-harness-gen-env\nVersion: 0.1.0\n")
    for name in (
        "asset_preview.py",
        "genesis_runtime.py",
        "genesis_child.py",
        "package_loader.py",
        "source_identity.py",
    ):
        (module / name).write_bytes(b"# installed fixture\n")

    def record():
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        for path in sorted([*module.glob("*.py"), info / "METADATA"]):
            data = path.read_bytes()
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
            writer.writerow([path.relative_to(root).as_posix(), "sha256=" + digest, len(data)])
        writer.writerow(["robot_harness_gen_env-0.1.0.dist-info/RECORD", "", ""])
        (info / "RECORD").write_bytes(stream.getvalue().encode())

    record()
    monkeypatch.syspath_prepend(str(root))
    return root, module, info, record


def test_installed_record_pin_observes_bytes_without_wheel_signature(installed):
    from self_improving.harness.x2env.source_identity import (
        InstalledSourcePolicy,
        capture_source_identity,
    )

    root, module, info, _ = installed
    pin = hashlib.sha256((info / "RECORD").read_bytes()).hexdigest()
    identity = capture_source_identity(
        module, policy=InstalledSourcePolicy(expected_record_sha256=pin)
    )
    assert identity.kind == "installed_distribution"
    assert identity.distribution_version == "0.1.0"
    assert identity.installation_root == str(root)
    assert identity.record_sha256 == pin
    assert len(identity.members) == 5
    assert identity.authority == "development_provenance_only"
    assert capture_source_identity(module) == identity


@pytest.mark.parametrize(
    ("attack", "error"),
    [
        ("missing_record", "source_record_missing_or_ambiguous"),
        ("no_self_record", "source_record_missing_or_ambiguous"),
        ("duplicate_self_record", "source_record_missing_or_ambiguous"),
        ("duplicate_member", "source_record_invalid"),
        ("short_row", "source_record_invalid"),
        ("missing_member", "source_record_member_missing"),
        ("modified_member", "source_record_member_mismatch"),
        ("wrong_size", "source_record_member_mismatch"),
        ("wrong_algorithm", "source_record_member_mismatch"),
        ("metadata_name", "source_distribution_metadata_mismatch"),
        ("metadata_version", "source_distribution_metadata_mismatch"),
        ("record_symlink", "source_member_unsafe"),
        ("member_symlink", "source_member_unsafe"),
        ("metadata_symlink", "source_member_unsafe"),
    ],
)
def test_installed_metadata_and_paths_fail_closed(installed, attack, error):
    from self_improving.harness.x2env.source_identity import capture_source_identity

    root, module, info, rebuild = installed
    record = info / "RECORD"
    rows = list(csv.reader(io.StringIO(record.read_text())))
    member = module / "asset_preview.py"
    member_name = "self_improving/harness/x2env/asset_preview.py"
    if attack == "missing_record":
        record.unlink()
    elif attack == "no_self_record":
        rows = rows[:-1]
    elif attack == "duplicate_self_record":
        rows.append(rows[-1])
    elif attack == "duplicate_member":
        rows.append(rows[0])
    elif attack == "short_row":
        rows.append(["malformed"])
    elif attack == "missing_member":
        rows = [r for r in rows if r[0] != member_name]
    elif attack == "modified_member":
        member.write_text("# corrupted after installation\n")
    elif attack in {"wrong_size", "wrong_algorithm"}:
        row = next(r for r in rows if r[0] == member_name)
        row[2 if attack == "wrong_size" else 1] = "999" if attack == "wrong_size" else "md5=abc"
    elif attack.startswith("metadata_") and attack != "metadata_symlink":
        (info / "METADATA").write_text(
            "Name: wrong-name\nVersion: 0.1.0\n"
            if attack == "metadata_name"
            else "Name: robot-harness-gen-env\nVersion: \n"
        )
        rebuild()
    else:
        path = {
            "record_symlink": record,
            "member_symlink": member,
            "metadata_symlink": info / "METADATA",
        }[attack]
        outside = root.parent / "outside"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    if attack in {
        "no_self_record",
        "duplicate_self_record",
        "duplicate_member",
        "short_row",
        "missing_member",
        "wrong_size",
        "wrong_algorithm",
    }:
        with record.open("w", newline="") as stream:
            csv.writer(stream).writerows(rows)
    with pytest.raises(ValueError, match=f"^{error}$"):
        capture_source_identity(module)


def test_installed_wrong_pin_and_unowned_module_are_rejected(installed, tmp_path):
    from self_improving.harness.x2env.source_identity import (
        InstalledSourcePolicy,
        capture_source_identity,
    )

    _, module, _, _ = installed
    with pytest.raises(ValueError, match="^source_record_pin_mismatch$"):
        capture_source_identity(
            module, policy=InstalledSourcePolicy(expected_record_sha256="0" * 64)
        )
    alien = tmp_path / "alien"
    alien.mkdir()
    with pytest.raises(ValueError, match="^source_distribution_member_mismatch$"):
        capture_source_identity(alien, policy=InstalledSourcePolicy())


def test_git_member_symlink_and_relative_policy_are_rejected(tmp_path):
    from self_improving.harness.x2env.source_identity import (
        GitSourcePolicy,
        capture_source_identity,
    )

    root, module, _ = checkout(tmp_path)
    with pytest.raises(ValueError, match="^source_git_root_mismatch$"):
        capture_source_identity(module, policy=GitSourcePolicy(root="checkout"))
    member = module / "asset_preview.py"
    original = tmp_path / "outside.py"
    original.write_bytes(member.read_bytes())
    member.unlink()
    member.symlink_to(original)
    with pytest.raises(ValueError, match="^source_member_unsafe$"):
        capture_source_identity(module, policy=GitSourcePolicy(root=str(root)))


def test_missing_distribution_is_not_treated_as_git(tmp_path, monkeypatch):
    from self_improving.harness.x2env.source_identity import capture_source_identity

    # Restrict only metadata's standard search environment; no fake Distribution object.
    monkeypatch.setattr(sys, "path", [str(tmp_path)])
    with pytest.raises(ValueError, match="^source_distribution_missing$"):
        capture_source_identity(tmp_path)


def test_record_exceeding_standard_csv_field_limit_is_rejected(installed):
    from self_improving.harness.x2env.source_identity import capture_source_identity

    _, module, info, _ = installed
    with (info / "RECORD").open("a") as stream:
        stream.write("x" * (csv.field_size_limit() + 1) + ",,\n")
    with pytest.raises(ValueError, match="^source_record_invalid$"):
        capture_source_identity(module)


def test_git_configured_worktree_cannot_claim_different_source_root(tmp_path):
    from self_improving.harness.x2env.source_identity import capture_source_identity

    root, module, _ = checkout(tmp_path)
    other = tmp_path / "other-worktree"
    other.mkdir()
    subprocess.run(["git", "-C", str(root), "config", "core.worktree", str(other)], check=True)
    with pytest.raises(ValueError, match="^source_git_root_mismatch$"):
        capture_source_identity(module)


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
    (module / "untracked-development-note.txt").write_text("untracked source work\n")
    untracked = capture_source_identity(module, policy=policy)
    assert untracked.head == head and untracked.dirty is True
    assert untracked.status_sha256 != identity.status_sha256
    assert untracked.members == identity.members
    (module / "genesis_child.py").write_text("# modified source\n")
    changed = capture_source_identity(module, policy=policy)
    assert changed.head == head and changed.dirty is True
    assert changed.members != identity.members
    with pytest.raises(ValueError, match="source_git_commit_mismatch"):
        capture_source_identity(
            module, policy=GitSourcePolicy(root=str(root), expected_commit="0" * 40)
        )


def test_broken_canonical_checkout_marker_never_selects_installed_distribution(tmp_path):
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
