"""Public capture/read/deployment checks for declared runtime byte closures."""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.genesis_runtime_manifest import (
    RuntimeManifestError,
    capture_runtime_manifest,
    read_runtime_manifest,
    verify_runtime_deployment,
)
from self_improving.harness.schemas.genesis_runtime_manifest import RuntimeProcessIdentityV1


def test_declared_runtime_closure_survives_relocation(tmp_path):
    roots = {}
    for role in ("backend", "distributions", "genesis", "interpreter", "native", "stdlib"):
        root = tmp_path / "source" / role
        root.mkdir(parents=True)
        (root / "member").write_bytes(b"runtime bytes")
        roots[role] = root
    identity = RuntimeProcessIdentityV1(
        implementation="cpython",
        version="3.12.3",
        abi="cpython-312-x86_64-linux-gnu",
        platform="linux-x86_64",
        environment=(("OMP_NUM_THREADS", "1"),),
    )
    store = LocalArtifactStore(tmp_path / "cas")
    ref = capture_runtime_manifest(store, roots=roots, process_identity=identity)
    manifest = read_runtime_manifest(store, ref)
    assert manifest.authority == "declared_byte_closure_only"
    assert len(manifest.members) == 6
    assert all(member.artifact.bytes == 13 for member in manifest.members)
    shutil.copytree(tmp_path / "source", tmp_path / "relocated")
    relocated = {role: tmp_path / "relocated" / role for role in roots}
    shutil.rmtree(tmp_path / "source")
    assert (
        verify_runtime_deployment(store, ref, roots=relocated, process_identity=identity)
        == manifest
    )
    assert capture_runtime_manifest(store, roots=relocated, process_identity=identity) == ref


@pytest.fixture
def deployment(tmp_path):
    roots = {}
    for role in ("backend", "distributions", "genesis", "interpreter", "native", "stdlib"):
        root = tmp_path / role
        root.mkdir()
        (root / "member").write_bytes(b"runtime bytes")
        roots[role] = root
    identity = RuntimeProcessIdentityV1(
        implementation="cpython",
        version="3.12.3",
        abi="cpython-312-x86_64-linux-gnu",
        platform="linux-x86_64",
        environment=(("OMP_NUM_THREADS", "1"),),
    )
    store = LocalArtifactStore(tmp_path / "cas")
    ref = capture_runtime_manifest(store, roots=roots, process_identity=identity)
    return store, roots, identity, ref


@pytest.mark.parametrize("change", ["missing", "extra", "native", "env", "symlink", "rootlink"])
def test_deployment_drift_fails_closed(deployment, tmp_path, change):
    store, roots, identity, ref = deployment
    if change == "missing":
        (roots["stdlib"] / "member").unlink()
    elif change == "extra":
        (roots["genesis"] / "extra").write_bytes(b"undeclared")
    elif change == "native":
        (roots["native"] / "member").write_bytes(b"native drift")
    elif change == "env":
        identity = identity.model_copy(update={"environment": (("OMP_NUM_THREADS", "2"),)})
    elif change == "symlink":
        (roots["backend"] / "member").unlink()
        (roots["backend"] / "member").symlink_to(roots["native"] / "member")
    else:
        link = tmp_path / "rootlink"
        link.symlink_to(roots["backend"], target_is_directory=True)
        roots["backend"] = link
    with pytest.raises(RuntimeManifestError):
        verify_runtime_deployment(store, ref, roots=roots, process_identity=identity)


@pytest.mark.parametrize("change", ["whitespace", "escape", "duplicate", "unsorted", "file_uri"])
def test_noncanonical_or_unsafe_manifest_is_rejected(deployment, tmp_path, change):
    store, roots, identity, ref = deployment
    payload = json.loads(store.resolve(ref).path.read_bytes())
    if change == "escape":
        payload["members"][0]["path"] = "../member"
    elif change == "duplicate":
        payload["members"].append(payload["members"][0])
    elif change == "unsorted":
        payload["members"].reverse()
    elif change == "file_uri":
        payload["members"][0]["artifact"]["uri"] = store.resolve(
            read_runtime_manifest(store, ref).members[0].artifact
        ).path.as_uri()
    path = tmp_path / "invalid.json"
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"))
        + ("\n" if change == "whitespace" else "")
    )
    invalid = store.put_file(
        path, name=ref.name, media_type=ref.media_type, schema_version=ref.schema_version
    )
    with pytest.raises(ValueError):
        read_runtime_manifest(store, invalid)


@pytest.mark.parametrize("change", ["relative", "parent", "role", "directory_link", "empty"])
def test_capture_rejects_invalid_deployment(deployment, tmp_path, change):
    store, roots, identity, _ = deployment
    if change == "relative":
        roots["backend"] = Path("relative")
    elif change == "parent":
        roots["backend"] = tmp_path / "native" / ".." / "backend"
    elif change == "role":
        roots.pop("backend")
    elif change == "directory_link":
        (roots["backend"] / "linked").symlink_to(roots["native"], target_is_directory=True)
    else:
        (roots["backend"] / "member").unlink()
    with pytest.raises(RuntimeManifestError):
        capture_runtime_manifest(store, roots=roots, process_identity=identity)


@pytest.mark.parametrize(
    "environment",
    [(("Z", "1"), ("A", "2")), (("A", "1"), ("A", "2")), (("BAD=", "1"),), (("A", "nul\x00"),)],
)
def test_invalid_process_environment_is_rejected(environment):
    with pytest.raises(ValueError):
        RuntimeProcessIdentityV1(
            implementation="cpython",
            version="3",
            abi="abi",
            platform="linux",
            environment=environment,
        )


def test_missing_root_role_in_cas_cannot_be_read(deployment, tmp_path):
    store, _, _, ref = deployment
    payload = json.loads(store.resolve(ref).path.read_bytes())
    payload["members"][0]["root"] = "distributions"
    payload["members"][0]["path"] = "another"
    payload["members"][0]["artifact"]["name"] = "distributions/another"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    invalid = store.put_file(
        path, name=ref.name, media_type=ref.media_type, schema_version=ref.schema_version
    )
    with pytest.raises(ValueError, match="six"):
        read_runtime_manifest(store, invalid)
    with pytest.raises(RuntimeManifestError, match="reference"):
        read_runtime_manifest(store, ref.model_copy(update={"media_type": "text/plain"}))


def test_fresh_process_reads_relocated_cas_without_source_roots(deployment, tmp_path):
    store, roots, _, ref = deployment
    target = tmp_path / "other-cas"
    shutil.copytree(store.root, target)
    for root in roots.values():
        shutil.rmtree(root)
    script = """
import json, sys
from pathlib import Path
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.genesis_runtime_manifest import read_runtime_manifest
value = read_runtime_manifest(LocalArtifactStore(Path(sys.argv[1])),
                              ArtifactRef.model_validate_json(sys.argv[2]))
assert len(value.members) == 6
print(value.authority)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(target), ref.model_dump_json()],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.stdout.strip() == "declared_byte_closure_only"


def test_nested_file_and_empty_bytes_are_captured(deployment):
    store, roots, identity, _ = deployment
    directory = roots["genesis"] / "nested"
    directory.mkdir()
    (directory / "empty").touch()
    ref = capture_runtime_manifest(store, roots=roots, process_identity=identity)
    manifest = read_runtime_manifest(store, ref)
    assert any(
        member.path == "nested/empty" and member.artifact.bytes == 0 for member in manifest.members
    )


def test_unreadable_subdirectory_cannot_disappear_from_capture(deployment):
    store, roots, identity, _ = deployment
    directory = roots["genesis"] / "unreadable"
    directory.mkdir()
    (directory / "required").write_bytes(b"required")
    directory.chmod(0)
    try:
        with pytest.raises(RuntimeManifestError):
            capture_runtime_manifest(store, roots=roots, process_identity=identity)
    finally:
        directory.chmod(0o700)


def test_concurrent_member_write_is_rejected(deployment):
    store, roots, identity, _ = deployment
    member = roots["backend"] / "member"
    with member.open("wb") as stream:
        stream.truncate(32 * 1024 * 1024)
    stop = threading.Event()
    started = threading.Event()

    def writer():
        with member.open("r+b", buffering=0) as stream:
            while not stop.is_set():
                stream.seek(0)
                stream.write(b"changed")
                started.set()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert started.wait(5)
        with pytest.raises(RuntimeManifestError, match="member changed"):
            capture_runtime_manifest(store, roots=roots, process_identity=identity)
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()


def test_member_changed_after_publication_prevents_manifest_commit(deployment, tmp_path):
    _, roots, identity, _ = deployment
    store = LocalArtifactStore(tmp_path / "fresh-capture")
    with (roots["stdlib"] / "large").open("wb") as stream:
        stream.truncate(32 * 1024 * 1024)
    stop = threading.Event()
    changed = threading.Event()

    def writer():
        while not stop.is_set():
            if list((store.root / "sha256").glob("*/*")):
                (roots["backend"] / "member").write_bytes(b"after snapshot")
                changed.set()
                return
            time.sleep(0.001)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        with pytest.raises(RuntimeManifestError, match="deployment changed"):
            capture_runtime_manifest(store, roots=roots, process_identity=identity)
        assert changed.is_set()
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()
