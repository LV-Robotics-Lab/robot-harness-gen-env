"""Fail-closed helpers shared by active ``asset_ledger.v3`` writers.

The ledger contract deliberately keeps its validator separate from its atomic
serializer.  Production writers must compose the two in the safe order:
prove the complete file closure against disk, then replace the ledger.  This
module is that composition point; one-off scripts should not hand-roll it.

The evidence helpers operate inside a cooperative Python/runtime boundary.
They bind the files and module entrypoints observable to this process, but do
not establish an OS security boundary against arbitrary code running as the
same user, attest loader-opened file descriptors, or enumerate transitive
``dlopen``/ELF dependencies, drivers, and preload state.
"""

import errno
import hashlib
import json
import os
import platform
import secrets
import shutil
import stat
import sys
from pathlib import Path

from . import ledger


class LedgerWriteError(ValueError):
    """A candidate ledger failed the authoritative file-backed contract."""

    def __init__(self, violations):
        self.violations = tuple(violations)
        first = self.violations[0]
        super().__init__(f"{first.path} [{first.code}] {first.message}")


class EvidenceDigestError(ValueError):
    """A verification fact is not bound to the current representation set."""


class RepresentationClosureError(ValueError):
    """A writer could not enumerate a representation's loader closure."""


class ConcurrentLedgerUpdateError(RuntimeError):
    """The ledger changed after a writer derived its replacement candidate."""


class VerificationEvidenceError(ValueError):
    """An immutable verification-evidence artifact could not be published."""


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(path):
    path = Path(path)
    return {
        "uri": ledger.to_portable_uri(path),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }


def provenance_file_record(path):
    """Describe the exact regular file executed, without following a link."""

    path = Path(path).resolve(strict=True)
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    with ledger._open_parent_directory(path) as (directory_fd, name):
        fd = os.open(name, flags, dir_fd=directory_fd)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size < 0:
                raise VerificationEvidenceError(f"provenance file is not regular: {path}")
            digest = hashlib.sha256()
            while chunk := os.read(fd, 1024 * 1024):
                digest.update(chunk)
        finally:
            os.close(fd)
    return {
        "uri": ledger.to_portable_uri(path),
        "sha256": digest.hexdigest(),
        "bytes": metadata.st_size,
    }


def _snapshot_member(source_asset_dir, asset_key, record):
    if not ledger.artifact_file_record_is_current(record):
        raise VerificationEvidenceError("execution input is stale, missing, or unsafe")
    try:
        source = ledger._absolute_local_path(ledger.resolve_uri(record["uri"]))
        relative = source.relative_to(source_asset_dir)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise VerificationEvidenceError(
            "execution input is outside the authoritative asset directory"
        ) from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise VerificationEvidenceError("execution input has a non-portable asset-relative path")
    target = Path("assets") / "objects" / asset_key / relative
    return source, {
        "path": target.as_posix(),
        "sha256": record["sha256"],
        "bytes": record["bytes"],
    }


def _copy_snapshot_member(source, target, expected):
    target.parent.mkdir(parents=True, exist_ok=True)
    source_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    target_flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    )
    digest = hashlib.sha256()
    size = 0
    with ledger._open_parent_directory(source) as (source_dir_fd, source_name):
        source_fd = os.open(source_name, source_flags, dir_fd=source_dir_fd)
        try:
            metadata = os.fstat(source_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != expected["bytes"]:
                raise VerificationEvidenceError("execution source changed before snapshot copy")
            target_fd = os.open(target, target_flags, 0o444)
            try:
                while chunk := os.read(source_fd, 1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(target_fd, view)
                        view = view[written:]
                os.fsync(target_fd)
            finally:
                os.close(target_fd)
        finally:
            os.close(source_fd)
    if size != expected["bytes"] or digest.hexdigest() != expected["sha256"]:
        raise VerificationEvidenceError("execution source changed while snapshotting")


def _make_tree_read_only(root):
    for directory, directories, files in os.walk(root, topdown=False, followlinks=False):
        directory_path = Path(directory)
        for name in files:
            (directory_path / name).chmod(0o444)
        for name in directories:
            (directory_path / name).chmod(0o555)
        directory_path.chmod(0o555)


def _remove_private_tree(root):
    if not root.exists():
        return
    for directory, directories, files in os.walk(root, topdown=False, followlinks=False):
        directory_path = Path(directory)
        for name in files:
            try:
                (directory_path / name).chmod(0o600)
            except OSError:
                pass
        for name in directories:
            try:
                (directory_path / name).chmod(0o700)
            except OSError:
                pass
        try:
            directory_path.chmod(0o700)
        except OSError:
            pass
    shutil.rmtree(root)


def publish_execution_snapshot(
    snapshot_store,
    *,
    source_asset_dir,
    asset_key,
    model_entry,
    backend="sapien",
    asset_files=None,
    extra_files=None,
):
    """Publish a content-addressed pre/post snapshot for one cooperative run.

    The snapshot does not attest which file descriptors a third-party loader
    opened while it ran.
    """

    asset_key = ledger.canonical_asset_key(asset_key)
    model_id = ledger.canonical_model_id(model_entry.get("model_id"))
    source_asset_dir = ledger._absolute_local_path(source_asset_dir)
    if ledger._symlink_component(source_asset_dir) is not None or not source_asset_dir.is_dir():
        raise VerificationEvidenceError("authoritative asset directory is missing or unsafe")
    source_asset_dir = source_asset_dir.resolve(strict=True)
    source_asset_uri = ledger.to_portable_uri(source_asset_dir)
    reps_digest = ledger.reps_digest(model_entry, backend)
    sources = {}
    roles = []
    for representation_index, representation in enumerate(model_entry.get("representations", [])):
        if (
            not isinstance(representation, dict)
            or representation.get("backend") != backend
            or representation.get("role") == "snapshot"
        ):
            continue
        closure = []
        primary = None
        for record in representation.get("files", []):
            source, member = _snapshot_member(source_asset_dir, asset_key, record)
            previous = sources.setdefault(member["path"], (source, member))
            if previous != (source, member):
                raise VerificationEvidenceError("snapshot target has conflicting source records")
            closure.append(member)
            if (record.get("uri"), record.get("sha256")) == (
                representation.get("uri"),
                representation.get("sha256"),
            ):
                primary = member
        if primary is None or not closure:
            raise VerificationEvidenceError("representation primary is absent from its closure")
        roles.append(
            {
                "representation_index": representation_index,
                "role": representation["role"],
                "primary": primary,
                "closure": closure,
            }
        )
    if not roles:
        raise VerificationEvidenceError("execution snapshot has no loader-visible representation")
    ancillary = []
    for name, path in sorted((asset_files or {}).items()):
        try:
            name = ledger.canonical_asset_key(name)
            record = provenance_file_record(path)
            source, member = _snapshot_member(source_asset_dir, asset_key, record)
        except (OSError, ValueError, ledger.UnsafeAssetKeyError) as exc:
            raise VerificationEvidenceError(f"invalid loader asset file {name!r}") from exc
        member = {"name": name, **member}
        previous = sources.setdefault(member["path"], (source, member))
        if previous[0] != source or {
            key: value for key, value in previous[1].items() if key != "name"
        } != {key: value for key, value in member.items() if key != "name"}:
            raise VerificationEvidenceError("loader asset file conflicts with snapshot closure")
        ancillary.append(member)
    extras = []
    for name, path in sorted((extra_files or {}).items()):
        try:
            name = ledger.canonical_asset_key(name)
            record = provenance_file_record(path)
        except (OSError, ValueError, ledger.UnsafeAssetKeyError) as exc:
            raise VerificationEvidenceError(f"invalid execution extra {name!r}") from exc
        source = ledger._absolute_local_path(ledger.resolve_uri(record["uri"]))
        suffix = Path(path).suffix
        target = Path("inputs") / name / f"payload{suffix}"
        member = {
            "name": name,
            "path": target.as_posix(),
            "sha256": record["sha256"],
            "bytes": record["bytes"],
        }
        sources[member["path"]] = (source, member)
        extras.append(member)
    manifest = {
        "schema": ledger.EXECUTION_SNAPSHOT_SCHEMA,
        "asset_key": asset_key,
        "model_id": model_id,
        "backend": backend,
        "reps_digest": reps_digest,
        "asset_relpath": f"assets/objects/{asset_key}",
        "roles": roles,
        "ancillary": ancillary,
        "extras": extras,
        "source_asset_uri": source_asset_uri,
    }
    manifest_payload = ledger.canonical_json_bytes(manifest)
    manifest_digest = hashlib.sha256(manifest_payload).hexdigest()
    snapshot_store, store_fd = _open_or_create_evidence_directory(snapshot_store)
    final_name = f"{manifest_digest}.execution"
    final_root = snapshot_store / final_name
    stage_name = f".{manifest_digest}.{secrets.token_hex(8)}.tmp"
    stage_root = snapshot_store / stage_name
    try:
        try:
            os.mkdir(stage_name, 0o700, dir_fd=store_fd)
            for relative, (source, expected) in sources.items():
                _copy_snapshot_member(source, stage_root / relative, expected)
                source_record = {
                    "uri": ledger.to_portable_uri(source),
                    "sha256": expected["sha256"],
                    "bytes": expected["bytes"],
                }
                if not ledger.artifact_file_record_is_current(source_record):
                    raise VerificationEvidenceError(
                        "authoritative execution input changed after snapshot copy"
                    )
            manifest_path = stage_root / "manifest.json"
            manifest_path.write_bytes(manifest_payload)
            with manifest_path.open("rb") as stream:
                os.fsync(stream.fileno())
            _make_tree_read_only(stage_root)
            try:
                os.rename(stage_name, final_name, src_dir_fd=store_fd, dst_dir_fd=store_fd)
                os.fsync(store_fd)
            except OSError as exc:
                if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
                _remove_private_tree(stage_root)
        except Exception:
            _remove_private_tree(stage_root)
            raise
    finally:
        os.close(store_fd)
    manifest_record = _file_record(final_root / "manifest.json")
    snapshot = {
        "schema": ledger.EXECUTION_SNAPSHOT_SCHEMA,
        "root_uri": ledger.to_portable_uri(final_root),
        "manifest": manifest_record,
        "asset_key": asset_key,
        "model_id": model_id,
        "backend": backend,
        "reps_digest": reps_digest,
        "source_asset_uri": source_asset_uri,
    }
    if not ledger.execution_snapshot_is_current(snapshot):
        raise VerificationEvidenceError("published execution snapshot is not immutable and current")
    return snapshot


def _loaded_module_records(modules):
    requested = []
    prefixes = set()
    for module in modules:
        name = getattr(module, "__name__", None)
        if not isinstance(name, str) or not name:
            raise VerificationEvidenceError("runtime capability module has no canonical name")
        requested.append(name)
        prefixes.add(name.split(".", 1)[0])
    records = []
    seen_paths = set()
    for name, module in sorted(sys.modules.items()):
        if not any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes):
            continue
        path = getattr(module, "__file__", None)
        if path is None:
            continue
        record = provenance_file_record(path)
        identity = (record["uri"], record["sha256"], record["bytes"])
        if identity in seen_paths:
            continue
        seen_paths.add(identity)
        records.append({"name": name, "file": record})
    if not records or any(not any(row["name"] == name for row in records) for name in requested):
        raise VerificationEvidenceError("runtime capability module tree is not file-backed")
    return records


def capture_runtime_capability(*, loader_modules, sapien_module, entrypoint, config):
    """Capture file-backed Python/extension modules and exact runtime config.

    This is intentionally not a transitive native dependency or driver
    inventory; that stronger claim needs a separate OS-level attestor.
    """

    if not isinstance(entrypoint, str) or not entrypoint.strip():
        raise VerificationEvidenceError("runtime entrypoint must be a non-empty string")
    if not isinstance(config, dict) or not config:
        raise VerificationEvidenceError("runtime config must be a non-empty canonical document")
    ledger.canonical_json_bytes(config)
    loader_records = _loaded_module_records(loader_modules)
    sapien_records = _loaded_module_records([sapien_module])
    native_modules = [
        record["name"]
        for record in sapien_records
        if Path(ledger.resolve_uri(record["file"]["uri"])).suffix.lower()
        in {".so", ".pyd", ".dll", ".dylib"}
    ]
    capability = {
        "schema": ledger.RUNTIME_CAPABILITY_SCHEMA,
        "entrypoint": entrypoint,
        "loader_modules": loader_records,
        "sapien_modules": sapien_records,
        "native_modules": native_modules,
        "config": config,
    }
    if not ledger.runtime_capability_is_current(capability):
        raise VerificationEvidenceError("captured runtime capability is incomplete or stale")
    return capability


def build_verification_evidence(
    *,
    asset_key,
    model_id,
    backend,
    check,
    verdict,
    run_id,
    timestamp,
    reps_digest,
    script_path,
    inputs,
    thresholds,
    result,
    physical_facts_digest=None,
    execution_snapshot=None,
    runtime_capability=None,
):
    """Build one fully provenance-bound canonical evidence envelope.

    The capability and invocation are separate canonical documents with
    separate digests.  They intentionally capture the actual executable and
    script bytes instead of trusting a producer-supplied tool-name constant.
    """

    asset_key = ledger.canonical_asset_key(asset_key)
    model_id = ledger.canonical_model_id(model_id)
    if backend not in ledger.BACKENDS:
        raise VerificationEvidenceError(f"unsupported verification backend: {backend!r}")
    if check not in ledger.CHECKS:
        raise VerificationEvidenceError(f"unsupported verification check: {check!r}")
    if verdict not in ledger.VERDICTS:
        raise VerificationEvidenceError(f"unsupported verification verdict: {verdict!r}")
    if not isinstance(run_id, str) or not run_id.strip():
        raise VerificationEvidenceError("run_id must be a non-empty string")
    if not ledger._is_iso_datetime(timestamp):
        raise VerificationEvidenceError("timestamp must be canonical second-precision ISO time")
    if not ledger._is_sha256(reps_digest):
        raise VerificationEvidenceError("reps_digest must be a sha256 digest")
    if physical_facts_digest is not None and not ledger._is_sha256(physical_facts_digest):
        raise VerificationEvidenceError("physical_facts_digest must be null or a sha256 digest")
    if not ledger.verification_inputs_are_current(inputs):
        raise VerificationEvidenceError(
            "inputs must contain a non-empty task and at least one current regular file record"
        )
    if not isinstance(thresholds, dict):
        raise VerificationEvidenceError("thresholds must be a canonical document")
    if not isinstance(result, dict):
        raise VerificationEvidenceError("result must be a canonical document")

    interpreter = {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "executable": provenance_file_record(sys.executable),
    }
    script = provenance_file_record(script_path)
    capability = {
        "schema": ledger.VERIFICATION_CAPABILITY_SCHEMA,
        "issuer": None,
        "backend": backend,
        "check": check,
        "interpreter": interpreter,
        "script": script,
        "thresholds": thresholds,
        "runtime": runtime_capability,
    }
    capability_sha256 = hashlib.sha256(ledger.canonical_json_bytes(capability)).hexdigest()
    invocation = {
        "schema": ledger.VERIFICATION_INVOCATION_SCHEMA,
        "issuer": None,
        "asset_key": asset_key,
        "model_id": model_id,
        "backend": backend,
        "check": check,
        "reps_digest": reps_digest,
        "physical_facts_digest": physical_facts_digest,
        "execution_snapshot": execution_snapshot,
        "interpreter": interpreter,
        "script": script,
        "inputs": inputs,
        "thresholds": thresholds,
        "capability_sha256": capability_sha256,
    }
    invocation_digest = hashlib.sha256(ledger.canonical_json_bytes(invocation)).hexdigest()
    envelope = {
        "schema": ledger.VERIFICATION_EVIDENCE_SCHEMA,
        "asset_key": asset_key,
        "model_id": model_id,
        "backend": backend,
        "check": check,
        "verdict": verdict,
        "run_id": run_id,
        "timestamp": timestamp,
        "reps_digest": reps_digest,
        "physical_facts_digest": physical_facts_digest,
        "capability": capability,
        "capability_sha256": capability_sha256,
        "invocation": invocation,
        "invocation_digest": invocation_digest,
        "result": result,
        "qualification": None,
    }
    # Reject NaN, non-string dict keys, and otherwise non-canonical values at
    # construction time rather than after a receipt has been assembled.
    ledger.canonical_json_bytes(envelope)
    return envelope


def issue_qualified_verification(
    *,
    issuer,
    asset_key,
    model_id,
    run_id,
    timestamp,
    reps_digest,
    inputs,
    thresholds,
    result,
    model_entry=None,
    execution_snapshot=None,
    runtime_capability=None,
):
    """Issue one cooperative-runtime result; callers cannot supply its verdict.

    The closed issuer registry supplies the real producer script and the
    backend/check pair.  The shared strict evaluator derives the verdict from
    typed physical facts and thresholds both now and on every later read.
    This helper is not an authority boundary against arbitrary same-process
    Python; production evidence assumes the sealed script is the cooperative
    caller named by the qualification record.
    """

    try:
        spec = ledger.qualified_verification_issuer(issuer)
        if not ledger.execution_snapshot_matches_model(
            execution_snapshot, model_entry, spec["backend"]
        ):
            raise ledger.VerificationResultError(
                "qualified verification requires a current immutable execution snapshot"
            )
        if not ledger.qualified_runtime_capability_is_current(runtime_capability, spec):
            raise ledger.VerificationResultError(
                "qualified verification requires a current loader runtime capability"
            )
        if not ledger.qualified_verification_thresholds_are_allowed(thresholds, spec):
            raise ledger.VerificationResultError(
                "qualified verification thresholds violate the issuer threshold policy"
            )
        if (
            execution_snapshot.get("asset_key") != ledger.canonical_asset_key(asset_key)
            or execution_snapshot.get("model_id") != ledger.canonical_model_id(model_id)
            or execution_snapshot.get("backend") != spec["backend"]
            or execution_snapshot.get("reps_digest") != reps_digest
        ):
            raise ledger.VerificationResultError(
                "execution snapshot does not bind the current authoritative model"
            )
        verdict = ledger.qualified_verification_verdict(
            spec["backend"], spec["check"], thresholds, result
        )
        if spec["check"] in {"settle", "runtime_load", "joint_sweep"}:
            physical_facts_digest = ledger.settle_physical_facts_digest(model_entry)
            if spec["check"] == "settle" and not ledger.settle_result_matches_physical_facts(
                model_entry, result, verdict
            ):
                raise ledger.VerificationResultError(
                    "passing settle result does not match the promoted stable pose/z_policy"
                )
            if spec[
                "check"
            ] == "runtime_load" and not ledger.runtime_load_result_matches_physical_facts(
                model_entry, result
            ):
                raise ledger.VerificationResultError(
                    "runtime-load result does not match the promoted stable pose/z_policy"
                )
            if spec[
                "check"
            ] == "joint_sweep" and not ledger.joint_sweep_result_matches_physical_facts(
                model_entry, result
            ):
                raise ledger.VerificationResultError(
                    "joint-sweep result does not match the fixed-root placement facts"
                )
        else:
            physical_facts_digest = None
    except ledger.VerificationResultError as exc:
        raise VerificationEvidenceError(str(exc)) from exc
    qualified_inputs = dict(inputs)
    qualified_inputs["execution_manifest"] = execution_snapshot["manifest"]
    envelope = build_verification_evidence(
        asset_key=asset_key,
        model_id=model_id,
        backend=spec["backend"],
        check=spec["check"],
        verdict=verdict,
        run_id=run_id,
        timestamp=timestamp,
        reps_digest=reps_digest,
        script_path=spec["script_path"],
        inputs=qualified_inputs,
        thresholds=thresholds,
        result=result,
        physical_facts_digest=physical_facts_digest,
        execution_snapshot=execution_snapshot,
        runtime_capability=runtime_capability,
    )
    envelope["qualification"] = {
        "schema": ledger.VERIFICATION_QUALIFICATION_SCHEMA,
        "issuer": issuer,
        "input_attestation": ledger.VERIFICATION_INPUT_ATTESTATION,
        "opened_file_attested": False,
        "threat_model": ledger.VERIFICATION_THREAT_MODEL,
    }
    capability = envelope["capability"]
    capability["issuer"] = issuer
    capability_sha256 = hashlib.sha256(ledger.canonical_json_bytes(capability)).hexdigest()
    envelope["capability_sha256"] = capability_sha256
    invocation = envelope["invocation"]
    invocation["issuer"] = issuer
    invocation["capability_sha256"] = capability_sha256
    invocation_digest = hashlib.sha256(ledger.canonical_json_bytes(invocation)).hexdigest()
    envelope["invocation_digest"] = invocation_digest
    ledger.canonical_json_bytes(envelope)
    return envelope


def _open_or_create_evidence_directory(path):
    path = ledger._absolute_local_path(path)
    if ledger._symlink_component(path.parent) is not None:
        raise VerificationEvidenceError(f"evidence parent traverses a symlink: {path.parent}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    with ledger._open_parent_directory(path) as (parent_fd, name):
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
        directory_fd = os.open(name, flags, dir_fd=parent_fd)
    return path, directory_fd


def publish_verification_evidence(evidence_dir, envelope):
    """Publish canonical evidence once under its content digest."""

    payload = ledger.canonical_json_bytes(envelope)
    digest = hashlib.sha256(payload).hexdigest()
    filename = f"{digest}.verification.json"
    evidence_dir, directory_fd = _open_or_create_evidence_directory(evidence_dir)
    stage_name = f".{digest}.{secrets.token_hex(8)}.tmp"
    stage_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    stage_created = False
    try:
        fd = os.open(stage_name, stage_flags, 0o600, dir_fd=directory_fd)
        stage_created = True
        try:
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("evidence staging write made no progress")
                view = view[written:]
            os.fchmod(fd, 0o444)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(
                stage_name,
                filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            read_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            fd = os.open(filename, read_flags, dir_fd=directory_fd)
            try:
                metadata = os.fstat(fd)
                existing = bytearray()
                while chunk := os.read(fd, 1024 * 1024):
                    existing.extend(chunk)
            finally:
                os.close(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size != len(payload)
                or bytes(existing) != payload
            ):
                raise VerificationEvidenceError(
                    "content-addressed evidence path already holds different bytes"
                )
        else:
            os.fsync(directory_fd)
    finally:
        if stage_created:
            try:
                os.unlink(stage_name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)
    path = evidence_dir / filename
    return {
        "uri": ledger.to_portable_uri(path),
        "sha256": digest,
        "bytes": len(payload),
        "schema": envelope["schema"],
        "run_id": envelope["run_id"],
        "invocation_digest": envelope["invocation_digest"],
        "capability_sha256": envelope["capability_sha256"],
    }


def receipt_from_evidence(envelope, record):
    """Derive, never retype, the ledger receipt bound by an evidence file."""

    expected = {
        "schema": envelope.get("schema"),
        "run_id": envelope.get("run_id"),
        "invocation_digest": envelope.get("invocation_digest"),
        "capability_sha256": envelope.get("capability_sha256"),
    }
    if any(record.get(field) != value for field, value in expected.items()):
        raise VerificationEvidenceError("artifact record does not describe the evidence envelope")
    return {
        "backend": envelope["backend"],
        "check": envelope["check"],
        "verdict": envelope["verdict"],
        "run_id": envelope["run_id"],
        "timestamp": envelope["timestamp"],
        "verified_digest": envelope["reps_digest"],
        "evidence": dict(record),
    }


def representation_files(primary):
    """Return the exact recursive loader closure, sorted by canonical URI.

    Parsing uses the contract's own closed descriptor set, so a writer cannot
    claim that an opaque USD/GLB/URDF is dependency-free merely because it did
    not understand the container.
    """

    # Keep the lexical path until the symlink guard has inspected every
    # component. ``Path.resolve()`` here would erase the evidence that the
    # caller crossed a symlink before we had a chance to reject it.
    primary = ledger._absolute_local_path(primary)
    pending = [(primary, primary.suffix.lower().lstrip("."))]
    members = set()
    while pending:
        source, asset_format = pending.pop()
        if source in members:
            continue
        try:
            if not source.is_file():
                raise RepresentationClosureError(f"representation member is missing: {source}")
            if ledger._symlink_component(source) is not None:
                raise RepresentationClosureError(
                    f"representation member traverses a symlink: {source}"
                )
            members.add(source)
            references = ledger._closure_refs(source, asset_format)
            for reference_spec in references:
                reference = (
                    reference_spec.uri
                    if isinstance(reference_spec, ledger._ClosureReference)
                    else reference_spec
                )
                dependency = ledger._safe_closure_target(source, reference)
                pending.append((dependency, dependency.suffix.lower().lstrip(".")))
        except (OSError, ledger._ClosureInspectionError) as exc:
            raise RepresentationClosureError(
                f"cannot enumerate loader closure for {source}: {exc}"
            ) from exc
    return sorted((_file_record(path) for path in members), key=lambda item: item["uri"])


def validate_for_write(document):
    """Raise with typed violations unless the on-disk closure is valid."""

    violations = ledger.validate_ledger(document, check_files=True)
    if violations:
        raise LedgerWriteError(violations)


def write_validated(path, document, *, expected=None):
    """CAS a file-validated candidate under the ledger lock.

    Existing ledgers require the exact document the caller read.  A concurrent
    receipt append then produces a typed conflict instead of being silently
    overwritten by a stale whole-ledger replacement.
    """

    path = Path(path)
    with ledger._locked_ledger(path) as locked:
        validate_for_write(document)
        previous = None
        existed = ledger._locked_path_exists(locked)
        if existed:
            if expected is None:
                raise ConcurrentLedgerUpdateError(
                    "replacing an existing ledger requires the expected prior document"
                )
            current = ledger._read_locked_json(locked)
            if current != expected:
                raise ConcurrentLedgerUpdateError("ledger changed before validated write")
            previous = current
        elif expected is not None:
            raise ConcurrentLedgerUpdateError("expected ledger disappeared before validated write")
        ledger._atomic_write_json(
            path,
            document,
            locked=locked,
            pre_replace=lambda: validate_for_write(document),
        )
        try:
            validate_for_write(document)
        except LedgerWriteError as validation_error:
            try:
                if existed:
                    ledger._atomic_write_json(path, previous, locked=locked)
                else:
                    os.unlink(locked.ledger_name, dir_fd=locked.directory_fd)
                    os.fsync(locked.directory_fd)
            except OSError as rollback_error:
                raise ConcurrentLedgerUpdateError(
                    "ledger closure changed at publication and rollback failed"
                ) from rollback_error
            raise validation_error


def commit_then_cleanup(path, document, *, expected, cleanup):
    """CAS a ledger change before running destructive cleanup under its lock.

    ``document=None`` means the CAS deletes the ledger (including the
    expect-absent case).  Cleanup is deliberately not rolled back: after the
    authoritative ledger has dropped those references, an interrupted cleanup
    can leave only harmless orphan files.  A stale CAS runs no cleanup at all.
    """

    path = Path(path)
    with ledger._locked_ledger(path) as locked:
        exists = ledger._locked_path_exists(locked)
        if exists:
            if expected is None:
                raise ConcurrentLedgerUpdateError("cleanup expected no ledger but one exists")
            current = ledger._read_locked_json(locked)
            if current != expected:
                raise ConcurrentLedgerUpdateError("ledger changed before cleanup CAS")
        elif expected is not None:
            raise ConcurrentLedgerUpdateError("expected ledger disappeared before cleanup CAS")

        if document is None:
            if exists:
                os.unlink(locked.ledger_name, dir_fd=locked.directory_fd)
                os.fsync(locked.directory_fd)
        else:
            validate_for_write(document)
            ledger._atomic_write_json(
                path,
                document,
                locked=locked,
                pre_replace=lambda: validate_for_write(document),
            )
        cleanup()


def quarantine_empty_asset_directory(path):
    """Atomically remove an empty asset shell while holding its ledger lock.

    The lock file lives inside the asset directory, so unlinking it and then
    calling ``rmdir`` would create a split-lock race.  Instead, this operation
    verifies that the pinned directory contains only the lock and empty known
    output directories, renames the whole directory out of the executable
    library view, and reclaims the private quarantine after releasing the
    still-open lock inode.  Writers that opened the old directory before the
    rename fail the current-directory recheck in :func:`ledger._locked_ledger`.
    """

    path = Path(path)
    asset_directory = path.parent
    quarantine = None
    with ledger._locked_ledger(path) as locked:
        if ledger._locked_path_exists(locked):
            return False
        entries = set(os.listdir(locked.directory_fd))
        known_directories = {"visual", "collision", "snapshots"}
        if not entries.issubset({"ledger.lock", *known_directories}):
            return False
        lock_metadata = os.stat("ledger.lock", dir_fd=locked.directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(lock_metadata.st_mode):
            raise OSError("asset ledger lock is not a regular file")
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        for name in sorted(entries.intersection(known_directories)):
            child_fd = os.open(name, directory_flags, dir_fd=locked.directory_fd)
            try:
                if os.listdir(child_fd):
                    return False
            finally:
                os.close(child_fd)

        with ledger._open_parent_directory(asset_directory) as (parent_fd, asset_name):
            current = os.stat(asset_name, dir_fd=parent_fd, follow_symlinks=False)
            pinned = os.fstat(locked.directory_fd)
            if (
                not stat.S_ISDIR(current.st_mode)
                or current.st_dev != pinned.st_dev
                or current.st_ino != pinned.st_ino
            ):
                raise ConcurrentLedgerUpdateError(
                    "asset directory changed before empty-shell quarantine"
                )
            quarantine_name = f"_{asset_name}.empty-{secrets.token_hex(8)}"
            os.rename(
                asset_name,
                quarantine_name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
            quarantine = asset_directory.parent / quarantine_name

    assert quarantine is not None
    try:
        _remove_private_tree(quarantine)
    except OSError:
        # The authoritative library path was already removed atomically.  A
        # hidden quarantine orphan is recoverable and must not make a rejected
        # import look accepted or recreate an executable asset shell.
        pass
    return True


def append_validated_verification(path, model_id, entry, *, asset_key=None):
    """Append one digest-bound receipt under the ledger lock, or write nothing."""

    path = Path(path)
    model_id = ledger.canonical_model_id(model_id)
    if asset_key is not None:
        asset_key = ledger.canonical_asset_key(asset_key)
    with ledger._locked_ledger(path) as locked:
        document = ledger._read_locked_json(locked)
        if asset_key is not None and ledger._get(document, "external_ids.env_gen") != asset_key:
            raise ledger.LedgerIdentityError(
                "external_ids.env_gen changed before verification append"
            )
        model = next(
            (
                candidate
                for candidate in document.get("models", [])
                if candidate.get("model_id") == model_id
            ),
            None,
        )
        if model is None:
            raise ValueError(f"no model with model_id={model_id}")
        expected_digest = ledger.reps_digest(model, entry.get("backend"))
        if entry.get("verified_digest") != expected_digest:
            raise EvidenceDigestError(
                "verification verified_digest does not match the current "
                f"{entry.get('backend')!r} representation set"
            )
        if asset_key is not None:
            trusted = ledger.verification_from_trusted_evidence(
                model, entry.get("evidence"), asset_key
            )
            if trusted != entry:
                raise EvidenceDigestError(
                    "verification entry is not backed by current immutable evidence"
                )
        validate_for_write(document)
        candidate = json.loads(json.dumps(document))
        candidate_model = next(
            item for item in candidate["models"] if item.get("model_id") == model_id
        )
        identity = tuple(
            entry.get(field) for field in ("backend", "check", "run_id", "verified_digest")
        )
        canonical_entry = json.dumps(
            entry,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for receipt in candidate_model.get("verification", []):
            existing_identity = tuple(
                receipt.get(field) for field in ("backend", "check", "run_id", "verified_digest")
            )
            if existing_identity != identity:
                continue
            canonical_existing = json.dumps(
                receipt,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if canonical_existing == canonical_entry:
                return document
            raise ledger.VerificationConflictError(
                "verification producer identity already exists with different canonical fact"
            )
        candidate_model.setdefault("verification", []).append(dict(entry))
        validate_for_write(candidate)
        ledger._atomic_write_json(
            path,
            candidate,
            locked=locked,
            pre_replace=lambda: validate_for_write(candidate),
        )
        return candidate
