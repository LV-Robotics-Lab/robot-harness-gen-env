from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path

import pytest

from self_improving.studies.vlm_fallback_prompt_optimization import model_content

STUDY_ROOT = Path(__file__).resolve().parent


class _StringSubclass(str):
    pass


class _IntSubclass(int):
    pass


class _ListSubclass(list):
    pass


def _snapshot(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "models--Qwen--fixture"
    blobs = repository / "blobs"
    snapshot = repository / "snapshots" / ("a" * 40)
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    return snapshot, blobs


def _manifest_with_paths(*paths: str) -> dict:
    content_sha256 = "f612b89bcdbc401379f644d7e48572e3470f77dcd4c39416405d80952ad7089e"
    return {
        "schema_version": "vlm_fallback.model_content_manifest.v2",
        "model_id": "Qwen/fixture",
        "revision": "a" * 40,
        "entries": [
            {
                "path": path,
                "blob_id": content_sha256,
                "blob_id_algorithm": "sha256",
                "size_bytes": 7,
                "content_sha256": content_sha256,
            }
            for path in paths
        ],
    }


def test_build_manifest_hashes_git_and_content_addressed_blobs(tmp_path: Path) -> None:
    snapshot, blobs = _snapshot(tmp_path)
    git_blob_id = "04204c7c9d0e243cb4d1456ba552ab505beb8ea5"
    content_blob_id = "1b465fa6b6bcbc06a3199e3d2d8aec35d37494a712f888b6d5536684dd89d0f0"
    (blobs / git_blob_id).write_bytes(b"config\n")
    (blobs / content_blob_id).write_bytes(b"weights\n")
    (snapshot / "z-config.json").symlink_to(blobs / git_blob_id)
    (snapshot / "weights.bin").symlink_to(blobs / content_blob_id)

    manifest = model_content.build_model_content_manifest(
        snapshot,
        model_id="Qwen/fixture",
        revision="a" * 40,
    )

    assert manifest == {
        "schema_version": "vlm_fallback.model_content_manifest.v2",
        "model_id": "Qwen/fixture",
        "revision": "a" * 40,
        "entries": [
            {
                "path": "weights.bin",
                "blob_id": content_blob_id,
                "blob_id_algorithm": "sha256",
                "size_bytes": 8,
                "content_sha256": content_blob_id,
            },
            {
                "path": "z-config.json",
                "blob_id": git_blob_id,
                "blob_id_algorithm": "git_blob_sha1",
                "size_bytes": 7,
                "content_sha256": (
                    "f612b89bcdbc401379f644d7e48572e3470f77dcd4c39416405d80952ad7089e"
                ),
            },
        ],
    }


def test_canonical_manifest_bytes_are_utf8_compact_and_have_no_newline() -> None:
    content_sha256 = "f612b89bcdbc401379f644d7e48572e3470f77dcd4c39416405d80952ad7089e"
    manifest = {
        "schema_version": "vlm_fallback.model_content_manifest.v2",
        "model_id": "模型/fixture",
        "revision": "a" * 40,
        "entries": [
            {
                "path": "é.json",
                "blob_id": content_sha256,
                "blob_id_algorithm": "sha256",
                "size_bytes": 7,
                "content_sha256": content_sha256,
            }
        ],
    }
    expected = (
        '{"entries":[{"blob_id":"f612b89bcdbc401379f644d7e48572e3470f77dcd4c39416405d80952ad7089e",'
        '"blob_id_algorithm":"sha256","content_sha256":"f612b89bcdbc401379f644d7e48572e3470f77dcd4c39416405d80952ad7089e",'
        '"path":"é.json","size_bytes":7}],"model_id":"模型/fixture",'
        '"revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"schema_version":"vlm_fallback.model_content_manifest.v2"}'
    ).encode("utf-8")

    assert model_content.canonical_manifest_bytes(manifest) == expected
    assert model_content.manifest_sha256(manifest) == (
        "ef9685685f5a5f6560e133c634d2660f327da60deb7031e770ca02961e3027a5"
    )
    assert not expected.endswith(b"\n")


@pytest.mark.parametrize(
    "path",
    [
        "/absolute.json",
        "../escape.json",
        "nested/../escape.json",
        "nested\\windows.json",
        "C:config.json",
        "z:weights.bin",
        "e\u0301.json",
        ".",
        "nul\0.json",
    ],
)
def test_manifest_paths_must_be_canonical_relative_posix_nfc(path: str) -> None:
    with pytest.raises(ValueError, match="path"):
        model_content.validate_model_content_manifest(_manifest_with_paths(path))


@pytest.mark.parametrize(
    ("paths", "match"),
    [
        (("same.json", "same.json"), "duplicate"),
        (("é.json", "z.json"), "UTF-8 sorted"),
    ],
)
def test_manifest_paths_must_be_unique_and_utf8_sorted(paths: tuple[str, ...], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        model_content.validate_model_content_manifest(_manifest_with_paths(*paths))


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value.update(local_snapshot_path="/private/cache"), "top-level fields"),
        (lambda value: value.pop("model_id"), "top-level fields"),
        (lambda value: value.update(schema_version="v1"), "schema_version"),
        (lambda value: value.update(model_id=""), "model_id"),
        (lambda value: value.update(revision="a" * 39), "revision"),
        (lambda value: value["entries"][0].update(total_bytes=7), "entry fields"),
        (lambda value: value["entries"][0].pop("content_sha256"), "entry fields"),
        (lambda value: value["entries"][0].update(blob_id="g" * 64), "blob_id"),
        (
            lambda value: value["entries"][0].update(blob_id_algorithm="git_blob_sha1"),
            "blob_id_algorithm",
        ),
        (lambda value: value["entries"][0].update(size_bytes=True), "size_bytes"),
        (lambda value: value["entries"][0].update(content_sha256="0" * 64), "must match"),
    ],
)
def test_manifest_schema_and_entry_fields_are_exact(mutation, match: str) -> None:
    manifest = copy.deepcopy(_manifest_with_paths("config.json"))
    mutation(manifest)

    with pytest.raises(ValueError, match=match):
        model_content.validate_model_content_manifest(manifest)


def test_manifest_rejects_string_subclasses_at_the_schema_boundary() -> None:
    manifest = _manifest_with_paths("config.json")
    manifest["schema_version"] = _StringSubclass(manifest["schema_version"])

    with pytest.raises(ValueError, match="exact built-in JSON types"):
        model_content.validate_model_content_manifest(manifest)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(model_id=_StringSubclass(value["model_id"])),
        lambda value: value.update(revision=_StringSubclass(value["revision"])),
        lambda value: value.update(entries=_ListSubclass(value["entries"])),
        lambda value: value["entries"][0].update(path=_StringSubclass(value["entries"][0]["path"])),
        lambda value: value["entries"][0].update(
            size_bytes=_IntSubclass(value["entries"][0]["size_bytes"])
        ),
    ],
)
def test_manifest_rejects_non_builtin_types_recursively(mutation) -> None:
    manifest = _manifest_with_paths("config.json")
    mutation(manifest)

    with pytest.raises(ValueError, match="exact built-in JSON types"):
        model_content.validate_model_content_manifest(manifest)


def test_build_manifest_recurses_and_sorts_relative_posix_paths(tmp_path: Path) -> None:
    snapshot, blobs = _snapshot(tmp_path)
    blob_id = "04204c7c9d0e243cb4d1456ba552ab505beb8ea5"
    (blobs / blob_id).write_bytes(b"config\n")
    (snapshot / "nested").mkdir()
    (snapshot / "nested" / "first.json").symlink_to(blobs / blob_id)
    (snapshot / "z-last.json").symlink_to(blobs / blob_id)

    manifest = model_content.build_model_content_manifest(
        snapshot,
        model_id="Qwen/fixture",
        revision="a" * 40,
    )

    assert [entry["path"] for entry in manifest["entries"]] == [
        "nested/first.json",
        "z-last.json",
    ]


@pytest.mark.parametrize(
    ("target_kind", "match"),
    [
        ("broken", "broken"),
        ("escape", "escapes"),
        ("directory", "regular file"),
        ("fifo", "regular file"),
    ],
)
def test_build_manifest_rejects_broken_escaping_and_non_file_links(
    tmp_path: Path, target_kind: str, match: str
) -> None:
    snapshot, blobs = _snapshot(tmp_path)
    if target_kind == "broken":
        target = blobs / ("0" * 40)
    elif target_kind == "escape":
        target = tmp_path / ("0" * 40)
        target.write_bytes(b"outside\n")
    elif target_kind == "directory":
        target = blobs / ("0" * 40)
        target.mkdir()
    else:
        target = blobs / ("0" * 40)
        os.mkfifo(target)
    (snapshot / "entry.json").symlink_to(target)

    with pytest.raises(ValueError, match=match):
        model_content.build_model_content_manifest(
            snapshot,
            model_id="Qwen/fixture",
            revision="a" * 40,
        )


def test_build_manifest_rejects_a_symlink_chain_that_leaves_and_reenters_repository(
    tmp_path: Path,
) -> None:
    snapshot, blobs = _snapshot(tmp_path)
    blob_id = "04204c7c9d0e243cb4d1456ba552ab505beb8ea5"
    blob = blobs / blob_id
    blob.write_bytes(b"config\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "alias").symlink_to(blob)
    (snapshot / "config.json").symlink_to(outside / "alias")

    with pytest.raises(ValueError, match="escapes model repository"):
        model_content.build_model_content_manifest(
            snapshot,
            model_id="Qwen/fixture",
            revision="a" * 40,
        )


def test_load_manifest_requires_the_pinned_canonical_identity(tmp_path: Path) -> None:
    manifest = _manifest_with_paths("config.json")
    manifest_path = tmp_path / "model-content.json"
    manifest_path.write_bytes(model_content.canonical_manifest_bytes(manifest))

    loaded = model_content.load_model_content_manifest(
        manifest_path,
        "523f4ea5ddc246bad4dcbedfc9e46161b3165cb52ba0c6a940dbc9cb02a42f99",
        model_id="Qwen/fixture",
        revision="a" * 40,
    )

    assert loaded == manifest


@pytest.mark.parametrize("pin", ["expected_sha256", "model_id", "revision"])
def test_load_manifest_rejects_non_builtin_identity_pins(tmp_path: Path, pin: str) -> None:
    manifest = _manifest_with_paths("config.json")
    raw = model_content.canonical_manifest_bytes(manifest)
    manifest_path = tmp_path / "model-content.json"
    manifest_path.write_bytes(raw)
    arguments = {
        "expected_sha256": _StringSubclass(
            "523f4ea5ddc246bad4dcbedfc9e46161b3165cb52ba0c6a940dbc9cb02a42f99"
        ),
        "model_id": "Qwen/fixture",
        "revision": "a" * 40,
    }
    if pin != "expected_sha256":
        arguments["expected_sha256"] = hashlib.sha256(raw).hexdigest()
        arguments[pin] = _StringSubclass(arguments[pin])

    with pytest.raises(ValueError, match="exact built-in string"):
        model_content.load_model_content_manifest(manifest_path, **arguments)


def test_verify_model_content_rehashes_the_complete_snapshot(tmp_path: Path) -> None:
    snapshot, blobs = _snapshot(tmp_path)
    blob_id = "04204c7c9d0e243cb4d1456ba552ab505beb8ea5"
    blob = blobs / blob_id
    blob.write_bytes(b"config\n")
    (snapshot / "config.json").symlink_to(blob)
    manifest = model_content.build_model_content_manifest(
        snapshot,
        model_id="Qwen/fixture",
        revision="a" * 40,
    )

    verified = model_content.verify_model_content(snapshot, manifest)

    assert verified.manifest_sha256 == model_content.manifest_sha256(manifest)
    assert len(verified.roster_sha256) == 64

    blob.write_bytes(b"tampered\n")
    with pytest.raises(ValueError, match="blob id|content mismatch"):
        model_content.verify_model_content(snapshot, manifest)


def test_refresh_model_content_rehashes_only_after_roster_drift(tmp_path: Path) -> None:
    snapshot, blobs = _snapshot(tmp_path)
    blob_id = "04204c7c9d0e243cb4d1456ba552ab505beb8ea5"
    blob = blobs / blob_id
    blob.write_bytes(b"config\n")
    (snapshot / "config.json").symlink_to(blob)
    manifest = model_content.build_model_content_manifest(
        snapshot,
        model_id="Qwen/fixture",
        revision="a" * 40,
    )
    baseline = model_content.verify_model_content(snapshot, manifest)

    assert model_content.refresh_model_content(snapshot, manifest, baseline) == baseline

    blob.chmod(0o600)
    refreshed = model_content.refresh_model_content(snapshot, manifest, baseline)
    assert refreshed.manifest_sha256 == baseline.manifest_sha256
    assert refreshed.roster_sha256 != baseline.roster_sha256

    blob.write_bytes(b"tampered\n")
    with pytest.raises(ValueError, match="blob id|content mismatch"):
        model_content.refresh_model_content(snapshot, manifest, refreshed)


def test_refresh_rejects_a_forged_model_content_snapshot(tmp_path: Path) -> None:
    snapshot_a, blobs_a = _snapshot(tmp_path / "a")
    snapshot_b, blobs_b = _snapshot(tmp_path / "b")
    blob_id_a = "04204c7c9d0e243cb4d1456ba552ab505beb8ea5"
    blob_id_b = "8dca2f88bcfeb5fb3ecb832c4170ea85ef7be25c"
    (blobs_a / blob_id_a).write_bytes(b"config\n")
    (blobs_b / blob_id_b).write_bytes(b"different\n")
    (snapshot_a / "config.json").symlink_to(blobs_a / blob_id_a)
    (snapshot_b / "config.json").symlink_to(blobs_b / blob_id_b)
    manifest_a = model_content.build_model_content_manifest(
        snapshot_a,
        model_id="Qwen/fixture-a",
        revision="a" * 40,
    )
    manifest_b = model_content.build_model_content_manifest(
        snapshot_b,
        model_id="Qwen/fixture-b",
        revision="a" * 40,
    )
    baseline_b = model_content.verify_model_content(snapshot_b, manifest_b)
    forged = model_content.ModelContentSnapshot(
        manifest_sha256=model_content.manifest_sha256(manifest_a),
        roster_sha256=baseline_b.roster_sha256,
        _roster=baseline_b._roster,
    )

    with pytest.raises(ValueError, match="issued by verify_model_content"):
        model_content.refresh_model_content(snapshot_b, manifest_a, forged)


def test_empty_snapshot_cannot_claim_model_content_identity(tmp_path: Path) -> None:
    snapshot, _ = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="at least one"):
        model_content.build_model_content_manifest(
            snapshot,
            model_id="Qwen/fixture",
            revision="a" * 40,
        )


@pytest.mark.parametrize(
    ("filename", "expected_sha256", "model_id", "revision"),
    [
        (
            "model_content_3b.json",
            "dd904e42c13f7a47296e1aff8ce8a78090a86451d24b5ccd0d2807d29e6e4cad",
            "Qwen/Qwen2.5-VL-3B-Instruct",
            "66285546d2b821cf421d4f5eb2576359d3770cd3",
        ),
        (
            "model_content_7b.json",
            "9686327d73f5f373917d25577fc06244c69b132927af1c586fbcbc23f3301208",
            "Qwen/Qwen2.5-VL-7B-Instruct",
            "cc594898137f460bfe9f0759e9844b3ce807cfb5",
        ),
    ],
)
def test_checked_in_model_content_manifests_are_exact_canonical_bytes(
    filename: str,
    expected_sha256: str,
    model_id: str,
    revision: str,
) -> None:
    manifest_path = STUDY_ROOT / "amendments" / "01" / filename

    manifest = model_content.load_model_content_manifest(
        manifest_path,
        expected_sha256,
        model_id=model_id,
        revision=revision,
    )

    assert manifest["model_id"] == model_id
    assert not manifest_path.read_bytes().endswith(b"\n")


@pytest.mark.parametrize("claimed_blob_id", ["0" * 40, "0" * 64])
def test_build_manifest_rejects_blob_names_that_do_not_match_content(
    tmp_path: Path, claimed_blob_id: str
) -> None:
    snapshot, blobs = _snapshot(tmp_path)
    blob = blobs / claimed_blob_id
    blob.write_bytes(b"config\n")
    (snapshot / "config.json").symlink_to(blob)

    with pytest.raises(ValueError, match="blob id does not match content"):
        model_content.build_model_content_manifest(
            snapshot,
            model_id="Qwen/fixture",
            revision="a" * 40,
        )
