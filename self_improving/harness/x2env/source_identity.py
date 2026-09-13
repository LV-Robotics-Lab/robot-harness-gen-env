"""Observed preview source provenance, not release or runtime qualification."""

import base64
import csv
import hashlib
import io
import subprocess
from importlib import metadata
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from .contracts import Model, Sha256

SOURCE_MEMBERS = (
    "asset_preview.py",
    "genesis_runtime.py",
    "genesis_child.py",
    "package_loader.py",
    "source_identity.py",
)


class GitSourcePolicy(Model):
    kind: Literal["git"] = "git"
    root: str
    expected_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")] | None = None


class InstalledSourcePolicy(Model):
    """Optional deployment pin; RECORD alone is not a wheel signature or release authority."""

    kind: Literal["installed"] = "installed"
    expected_record_sha256: Sha256 | None = None


SourceIdentityPolicy = Annotated[
    GitSourcePolicy | InstalledSourcePolicy, Field(discriminator="kind")
]


class SourceMember(Model):
    path: str
    sha256: Sha256
    size_bytes: int


class GitSourceIdentity(Model):
    kind: Literal["git_checkout"] = "git_checkout"
    authority: Literal["development_provenance_only"] = "development_provenance_only"
    scope: Literal["preview_execution_sources"] = "preview_execution_sources"
    root: str
    head: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    dirty: bool
    status_sha256: Sha256
    diff_sha256: Sha256
    members: tuple[SourceMember, ...]


class InstalledSourceIdentity(Model):
    kind: Literal["installed_distribution"] = "installed_distribution"
    authority: Literal["development_provenance_only"] = "development_provenance_only"
    scope: Literal["preview_execution_sources"] = "preview_execution_sources"
    distribution_name: Literal["robot-harness-gen-env"] = "robot-harness-gen-env"
    distribution_version: str
    installation_root: str
    record_sha256: Sha256
    metadata_sha256: Sha256
    members: tuple[SourceMember, ...]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, check=True, timeout=20
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("source_git_unavailable") from exc


def capture_source_identity(
    module_root: Path, *, policy: SourceIdentityPolicy | None = None
) -> GitSourceIdentity | InstalledSourceIdentity:
    """Bind on-disk preview members; explicit Git failures never select another source.

    Auto respects checkout markers, including broken ones. Explicit installed mode instead
    requires distribution ownership, so unrelated ancestor markers do not select its identity.
    No claim is made about the original wheel archive or the full dependency environment.
    """
    module_root = Path(module_root).resolve(strict=True)
    checkout = next((p for p in (module_root, *module_root.parents) if (p / ".git").exists()), None)
    if isinstance(policy, InstalledSourcePolicy):
        return _installed(module_root, policy)
    if isinstance(policy, GitSourcePolicy):
        requested = Path(policy.root)
        if not requested.is_absolute() or requested.resolve() != checkout:
            raise ValueError("source_git_root_mismatch")
    if checkout is None:
        return _installed(module_root, InstalledSourcePolicy())
    actual = Path(_git(checkout, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if actual != checkout:
        raise ValueError("source_git_root_mismatch")
    head = _git(checkout, "rev-parse", "HEAD").decode().strip()
    if policy is not None and policy.expected_commit and head != policy.expected_commit:
        raise ValueError("source_git_commit_mismatch")
    status = _git(checkout, "status", "--porcelain", "--untracked-files=all")
    diff = _git(checkout, "diff", "HEAD", "--binary")
    members = []
    for name in SOURCE_MEMBERS:
        path = module_root / name
        if path.is_symlink() or not path.resolve().is_relative_to(checkout):
            raise ValueError("source_member_unsafe")
        data = path.read_bytes()
        members.append(SourceMember(path=name, sha256=_sha(data), size_bytes=len(data)))
    return GitSourceIdentity(
        root=str(checkout),
        head=head,
        dirty=bool(status),
        status_sha256=_sha(status),
        diff_sha256=_sha(diff),
        members=tuple(members),
    )


def _installed(module_root: Path, policy: InstalledSourcePolicy) -> InstalledSourceIdentity:
    try:
        dist = metadata.distribution("robot-harness-gen-env")
    except metadata.PackageNotFoundError as exc:
        raise ValueError("source_distribution_missing") from exc
    install_root = Path(dist.locate_file("")).resolve()
    prefix = "self_improving/harness/x2env/"
    if Path(dist.locate_file(prefix)).resolve() != module_root:
        raise ValueError("source_distribution_member_mismatch")

    def read_owned(path: Path) -> bytes:
        if (
            path.is_symlink()
            or path.absolute() != path.resolve()
            or not path.is_relative_to(install_root)
        ):
            raise ValueError("source_member_unsafe")
        return path.read_bytes()

    record_text = dist.read_text("RECORD")
    if record_text is None:
        raise ValueError("source_record_missing_or_ambiguous")
    # read_text normalizes line endings; use it only to locate the raw RECORD member.
    try:
        records = [
            row[0]
            for row in csv.reader(io.StringIO(record_text))
            if row and row[0].endswith(".dist-info/RECORD")
        ]
    except csv.Error as exc:
        raise ValueError("source_record_invalid") from exc
    if len(records) != 1:
        raise ValueError("source_record_missing_or_ambiguous")
    record = read_owned(Path(dist.locate_file(records[0])))
    if policy.expected_record_sha256 and _sha(record) != policy.expected_record_sha256:
        raise ValueError("source_record_pin_mismatch")
    rows = {}
    try:
        for row in csv.reader(io.StringIO(record.decode("utf-8"))):
            if len(row) != 3 or row[0] in rows:
                raise ValueError("source_record_invalid")
            rows[row[0]] = row[1:]
    except (UnicodeError, csv.Error) as exc:
        raise ValueError("source_record_invalid") from exc

    def verified(name: str) -> bytes:
        if name not in rows:
            raise ValueError("source_record_member_missing")
        data = read_owned(Path(dist.locate_file(name)))
        expected_hash = "sha256=" + base64.urlsafe_b64encode(
            hashlib.sha256(data).digest()
        ).decode().rstrip("=")
        if rows[name] != [expected_hash, str(len(data))]:
            raise ValueError("source_record_member_mismatch")
        return data

    metadata_name = str(records[0]).removesuffix("RECORD") + "METADATA"
    metadata_data = verified(metadata_name)
    if dist.metadata["Name"] != "robot-harness-gen-env" or not dist.version:
        raise ValueError("source_distribution_metadata_mismatch")
    members = []
    for name in SOURCE_MEMBERS:
        data = verified(prefix + name)
        members.append(SourceMember(path=name, sha256=_sha(data), size_bytes=len(data)))
    return InstalledSourceIdentity(
        distribution_version=dist.version,
        installation_root=str(install_root),
        record_sha256=_sha(record),
        metadata_sha256=_sha(metadata_data),
        members=tuple(members),
    )
