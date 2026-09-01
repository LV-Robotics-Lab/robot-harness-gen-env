#!/usr/bin/env python3
"""Fail-closed migration planner: asset_ledger.v2 -> v3.

What it does, per ledger (asset_library/*/ledger.json + upstream_ledgers/*):

  strip    semantic_name, tags, physical.mesh_up_axis, physical
           .origin_convention, mass/friction runtime_default pair
           (v3 deletions -- zero decision consumers, see ledger.py header)
  fold     the stripped mesh_up_axis/origin_convention facts into the GLB
           representation's frame/geometry_state (the GLB is the artifact
           those facts were actually about; the writer baked scale into it)
  require  stable_poses[].measured_against. Missing provenance is emitted as
           typed debt; a migration invocation is not a measurement run and
           therefore may not invent one.
  quarantine legacy appearance/placement sidecars that do not bind their
           measurements to the exact representation digest. Rows are emitted
           as typed measurement_input_unbound debt and never promoted into a
           ledger merely because a caller supplied a JSON document.
  add      external_ids {env_gen: <catalog id>} derived from asset_id
           (the ledger/catalog/IR naming junction stops being a code-side
           guess)
  repair   representations[].files from the legacy primary uri/sha256/
           size_bytes tuple when all three facts are present. Only formats
           whose loader semantics cannot reference another file are thereby
           closure-proven; GLB/glTF/USD/URDF/OBJ and unknown formats retain a
           typed closure debt. Collision provenance is never inferred from
           role.

Every candidate is re-validated with the v3 validator.  ``--dry-run`` scans
the complete configured roots without writing.  A non-dry run may atomically
replace exactly one eligible ledger; if more than one ledger is eligible, the
tool blocks before its first write because it does not yet have a durable
multi-ledger recovery journal.  Source control is not a transaction protocol.
"""

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEV = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DEV / "1_asset_reuse"))

from lib import ledger as L  # noqa: E402

_PROVEN_SINGLE_FILE_FORMATS = frozenset({"png", "stl"})
_FILE_PROOF_CODES = frozenset(
    {
        "bad_sha256",
        "bad_type",
        "bytes_mismatch",
        "duplicate_file_uri",
        "file_missing",
        "files_not_sorted",
        "missing",
        "primary_file_missing",
        "representation_file_closure_incomplete",
        "representation_file_closure_extra",
        "representation_file_closure_unverifiable",
        "representation_file_dependency_cycle",
        "representation_file_path_alias",
        "representation_file_path_unverifiable",
        "representation_file_payload_size_mismatch",
        "representation_file_symlink_forbidden",
        "representation_file_unverifiable",
        "sha256_mismatch",
        "unexpected_field",
    }
)
_ABSENT = object()


def _catalog_id(asset_id: str) -> str:
    for pref in ("external_", "generated_", "robotwin_", "upstream_"):
        if asset_id.startswith(pref):
            return asset_id[len(pref) :]
    return asset_id


def _proven_multifile_representations(document):
    if document.get("schema_version") != L.SCHEMA_VERSION:
        return set()
    violations = L.validate_ledger(document, check_files=True)
    proven = set()
    for model_index, model in enumerate(document.get("models", [])):
        if not isinstance(model, dict):
            continue
        for rep_index, representation in enumerate(model.get("representations", [])):
            if not isinstance(representation, dict):
                continue
            asset_format = str(representation.get("format") or "").lower()
            if asset_format in _PROVEN_SINGLE_FILE_FORMATS:
                continue
            files = representation.get("files")
            if not isinstance(files, list) or not files:
                continue
            rep_prefix = f"models.{model_index}.representations.{rep_index}"
            if not any(
                issue.code in _FILE_PROOF_CODES and issue.path.startswith(rep_prefix)
                for issue in violations
            ):
                proven.add(rep_prefix)
    return proven


@dataclass(frozen=True)
class MigrationIssue:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class MigrationResult:
    document: dict
    changes: tuple[str, ...]
    debts: tuple[MigrationIssue, ...]
    blockers: tuple[MigrationIssue, ...]


class ConcurrentMigrationError(RuntimeError):
    """A ledger changed between batch staging and transactional commit."""


class UnsafeBatchMigrationError(ConcurrentMigrationError):
    """A multi-ledger apply lacks a durable crash-recovery transaction."""


def _issue(path, code, message):
    return MigrationIssue(path=path, code=code, message=message)


def _valid_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value.lower())
    )


def _measurement_bound_to_model(model, block):
    if not isinstance(block, dict):
        return False
    provenance = block.get("measured_against")
    if not isinstance(provenance, dict):
        return False
    backend = provenance.get("backend")
    run_id = provenance.get("run_id")
    if backend not in L.BACKENDS or not isinstance(run_id, str) or not run_id.strip():
        return False
    return provenance.get("verified_digest") == L.reps_digest(model, backend)


def _stable_pose_bound_to_trusted_verification(ledger_document, model, provenance):
    """Require the pose's run id to name a live, deeply verified physical receipt."""

    try:
        asset_key = L.canonical_asset_key(ledger_document["external_ids"]["env_gen"])
    except (KeyError, TypeError, L.UnsafeAssetKeyError):
        return False
    expected_check = "joint_sweep" if ledger_document.get("kind") == "articulated" else "settle"
    for receipt in model.get("verification", []):
        if not isinstance(receipt, dict) or (
            receipt.get("backend") != provenance.get("backend")
            or receipt.get("check") != expected_check
            or receipt.get("run_id") != provenance.get("run_id")
            or receipt.get("verdict") != "pass"
        ):
            continue
        trusted = L.verification_from_trusted_evidence(
            model,
            receipt.get("evidence"),
            asset_key,
        )
        if trusted == receipt:
            return True
    return False


def migrate_document(document, attrs, survey, is_upstream):
    """Repair only facts justified by the document or supplied evidence.

    The function deliberately continues over documents already labelled v3:
    schema_version is a declaration, not proof that content migration ran.
    Missing measurement/collision provenance is returned as typed debt and is
    never replaced by a fabricated migration run id.
    """
    led = json.loads(json.dumps(document))
    changed = []
    debts = []
    blockers = []

    schema_version = led.get("schema_version")
    original_schema_version = schema_version
    if schema_version not in ("asset_ledger.v2", L.SCHEMA_VERSION):
        blockers.append(
            _issue(
                "schema_version",
                "unsupported_schema_version",
                f"cannot migrate schema_version={schema_version!r}",
            )
        )
        return MigrationResult(led, (), (), tuple(blockers))
    if schema_version != L.SCHEMA_VERSION:
        led["schema_version"] = L.SCHEMA_VERSION
        changed.append("schema_version")
    proven_multifile = (
        _proven_multifile_representations(document)
        if original_schema_version == L.SCHEMA_VERSION
        else set()
    )

    for key in ("semantic_name", "tags"):
        if key in led:
            led.pop(key)
            changed.append(f"del {key}")

    asset_id = led.get("asset_id")
    cat_id = _catalog_id(asset_id) if isinstance(asset_id, str) and asset_id else None
    external_ids = led.get("external_ids")
    if external_ids is None:
        if cat_id is None:
            blockers.append(
                _issue(
                    "external_ids.env_gen",
                    "external_id_unrecoverable",
                    "asset_id is missing, so env_gen id cannot be derived",
                )
            )
        else:
            led["external_ids"] = {"env_gen": cat_id}
            changed.append("external_ids.env_gen")
    elif not isinstance(external_ids, dict):
        blockers.append(
            _issue(
                "external_ids",
                "external_ids_bad_type",
                "external_ids must be a dict and will not be overwritten",
            )
        )
    elif "env_gen" not in external_ids:
        if cat_id is None:
            blockers.append(
                _issue(
                    "external_ids.env_gen",
                    "external_id_unrecoverable",
                    "asset_id is missing, so env_gen id cannot be derived",
                )
            )
        else:
            external_ids["env_gen"] = cat_id
            changed.append("external_ids.env_gen")
    elif cat_id is not None and external_ids.get("env_gen") != cat_id:
        blockers.append(
            _issue(
                "external_ids.env_gen",
                "external_id_conflict",
                f"existing env_gen id {external_ids.get('env_gen')!r} conflicts "
                f"with asset_id-derived {cat_id!r}",
            )
        )

    a_models = attrs.get(cat_id, {}) if cat_id is not None else {}
    s_models = survey.get(cat_id, {}) if cat_id is not None else {}
    models = led.get("models")
    if not isinstance(models, list):
        blockers.append(_issue("models", "models_bad_type", "models must be a list"))
        return MigrationResult(led, tuple(changed), tuple(debts), tuple(blockers))

    for model_index, model in enumerate(models):
        model_prefix = f"models.{model_index}"
        if not isinstance(model, dict):
            blockers.append(_issue(model_prefix, "model_bad_type", "model entry must be a dict"))
            continue
        mid = str(model.get("model_id"))
        phys = model.get("physical")
        if not isinstance(phys, dict):
            blockers.append(
                _issue(
                    f"{model_prefix}.physical",
                    "physical_bad_type",
                    "physical must be a dict",
                )
            )
            continue

        up_axis = phys.pop("mesh_up_axis", _ABSENT)
        origin = phys.pop("origin_convention", _ABSENT)
        if up_axis is not _ABSENT:
            changed.append(f"{model_prefix}: del physical.mesh_up_axis")
        if origin is not _ABSENT:
            changed.append(f"{model_prefix}: del physical.origin_convention")
        for envelope_name in ("mass_kg", "friction"):
            envelope = phys.get(envelope_name)
            if not isinstance(envelope, dict):
                continue
            for key in tuple(envelope):
                if key.startswith("runtime_default"):
                    envelope.pop(key)
                    changed.append(f"{model_prefix}.physical.{envelope_name}: del {key}")

        representations = model.get("representations")
        if not isinstance(representations, list):
            blockers.append(
                _issue(
                    f"{model_prefix}.representations",
                    "representations_bad_type",
                    "representations must be a list",
                )
            )
            representations = []
        for rep_index, representation in enumerate(representations):
            rep_prefix = f"{model_prefix}.representations.{rep_index}"
            if not isinstance(representation, dict):
                blockers.append(
                    _issue(
                        rep_prefix,
                        "representation_bad_type",
                        "representation must be a dict",
                    )
                )
                continue
            legacy_size = representation.pop("size_bytes", _ABSENT)
            if legacy_size is not _ABSENT:
                changed.append(f"{rep_prefix}: del size_bytes")

            if "files" not in representation:
                uri = representation.get("uri")
                sha = representation.get("sha256")
                if (
                    isinstance(uri, str)
                    and bool(uri.strip())
                    and _valid_sha256(sha)
                    and isinstance(legacy_size, int)
                    and not isinstance(legacy_size, bool)
                    and legacy_size >= 0
                ):
                    representation["files"] = [{"uri": uri, "sha256": sha, "bytes": legacy_size}]
                    changed.append(f"{rep_prefix}.files")
                else:
                    debts.append(
                        _issue(
                            f"{rep_prefix}.files",
                            "representation_files_evidence_missing",
                            "cannot construct files without observed uri, sha256, and bytes",
                        )
                    )

            files = representation.get("files")
            if isinstance(files, list) and all(
                isinstance(member, dict) and isinstance(member.get("uri"), str) for member in files
            ):
                sorted_files = sorted(files, key=lambda member: member["uri"])
                if sorted_files != files:
                    representation["files"] = sorted_files
                    changed.append(f"{rep_prefix}.files: sort by uri")

            # A document cannot attest to its own dependency closure. In this
            # pure migrator there is no independently captured, digest-bound
            # resolver receipt, so even a pre-filled (or multi-member) files
            # list does not prove that a USD/URDF/OBJ omitted no payload,
            # mesh, MTL, or texture. Keep the debt until a later evidence-aware
            # resolver discharges it explicitly; never infer proof from count.
            asset_format = str(representation.get("format") or "").lower()
            if (
                asset_format not in _PROVEN_SINGLE_FILE_FORMATS
                and rep_prefix not in proven_multifile
            ):
                debts.append(
                    _issue(
                        f"{rep_prefix}.files",
                        "representation_file_closure_unproven",
                        f"format={asset_format!r} may reference dependencies; "
                        "document-contained files do not independently prove closure",
                    )
                )

            role = representation.get("role")
            collision_meta = representation.get("collision_meta")
            if role in ("collision", "visual_and_collision"):
                if collision_meta is None:
                    debts.append(
                        _issue(
                            f"{rep_prefix}.collision_meta",
                            "collision_provenance_missing",
                            "collision metadata is absent and will not be inferred from role",
                        )
                    )
                elif not isinstance(collision_meta, dict):
                    blockers.append(
                        _issue(
                            f"{rep_prefix}.collision_meta",
                            "collision_meta_bad_type",
                            "collision_meta must be a dict",
                        )
                    )
                else:
                    mode = collision_meta.get("mode")
                    if mode == "unknown":
                        debts.append(
                            _issue(
                                f"{rep_prefix}.collision_meta",
                                "collision_provenance_missing",
                                "collision_meta.mode=unknown requires an evidence-producing probe",
                            )
                        )
                    elif mode not in L.COLLISION_MODES:
                        blockers.append(
                            _issue(
                                f"{rep_prefix}.collision_meta.mode",
                                "collision_mode_invalid",
                                f"collision mode {mode!r} not in {L.COLLISION_MODES}",
                            )
                        )

            if (representation.get("format") or "").lower() == "glb" and (
                up_axis not in (_ABSENT, None) or origin not in (_ABSENT, None)
            ):
                if up_axis not in (_ABSENT, None):
                    frame = representation.setdefault("frame", {})
                    if isinstance(frame, dict) and "up_axis" not in frame:
                        frame["up_axis"] = up_axis
                        changed.append(f"{rep_prefix}.frame.up_axis")
                geometry = representation.setdefault("geometry_state", {})
                if isinstance(geometry, dict):
                    if "scale_baked" not in geometry:
                        geometry["scale_baked"] = not is_upstream
                        changed.append(f"{rep_prefix}.geometry_state.scale_baked")
                    if origin not in (_ABSENT, None) and "origin" not in geometry:
                        geometry["origin"] = origin
                        changed.append(f"{rep_prefix}.geometry_state.origin")

        conventions = phys.get("conventions")
        poses = conventions.get("stable_poses") if isinstance(conventions, dict) else None
        if isinstance(poses, list):
            for pose_index, pose in enumerate(poses):
                provenance = pose.get("measured_against") if isinstance(pose, dict) else None
                if not (
                    isinstance(provenance, dict)
                    and provenance.get("backend") in L.BACKENDS
                    and isinstance(provenance.get("run_id"), str)
                    and bool(provenance["run_id"].strip())
                ):
                    debts.append(
                        _issue(
                            f"{model_prefix}.physical.conventions.stable_poses."
                            f"{pose_index}.measured_against",
                            "stable_pose_provenance_missing",
                            "stable pose provenance is absent or incomplete; replay is required",
                        )
                    )
                elif not _stable_pose_bound_to_trusted_verification(led, model, provenance):
                    debts.append(
                        _issue(
                            f"{model_prefix}.physical.conventions.stable_poses."
                            f"{pose_index}.measured_against",
                            "stable_pose_provenance_untrusted",
                            "stable pose run_id does not name a current trusted physical receipt",
                        )
                    )

        appearance = model.get("appearance")
        appearance_row = a_models.get(mid) or {}
        if appearance is not None and not _measurement_bound_to_model(model, appearance):
            debts.append(
                _issue(
                    f"{model_prefix}.appearance.measured_against",
                    "measurement_input_unbound",
                    "appearance is not bound to this model's current representation digest",
                )
            )
        elif appearance is None and appearance_row.get("colors"):
            debts.append(
                _issue(
                    f"{model_prefix}.appearance",
                    "measurement_input_unbound",
                    "legacy appearance sidecar has no per-model representation digest receipt",
                )
            )

        placement = phys.get("placement")
        survey_row = s_models.get(mid) or {}
        if placement is not None and not _measurement_bound_to_model(model, placement):
            debts.append(
                _issue(
                    f"{model_prefix}.physical.placement.measured_against",
                    "measurement_input_unbound",
                    "placement is not bound to this model's current representation digest",
                )
            )
        elif placement is None and survey_row.get("verdict"):
            debts.append(
                _issue(
                    f"{model_prefix}.physical.placement",
                    "measurement_input_unbound",
                    "legacy placement sidecar has no per-model representation digest receipt",
                )
            )

        verifications = model.get("verification")
        if isinstance(verifications, list):
            for verification_index, verification in enumerate(verifications):
                if (
                    isinstance(verification, dict)
                    and verification.get("report_path", object()) is None
                ):
                    verification.pop("report_path")
                    changed.append(
                        f"{model_prefix}.verification.{verification_index}: del null report_path"
                    )

    return MigrationResult(
        led,
        tuple(changed),
        tuple(sorted(debts, key=lambda issue: (issue.path, issue.code))),
        tuple(sorted(blockers, key=lambda issue: (issue.path, issue.code))),
    )


def _migration_failures(result, violations):
    """Return every reason a candidate must not replace its source ledger."""
    return (*result.blockers, *result.debts, *violations)


def _atomic_restore_bytes(path, payload):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _write_staged_document(path, document, *, locked):
    """Write one candidate while the batch already owns every ledger lock."""

    L._atomic_write_json(
        path,
        document,
        locked=locked,
        pre_replace=lambda: _require_commit_candidate_valid(path, document),
    )


def _require_commit_candidate_valid(path, document):
    """Re-prove the candidate and every referenced byte at the commit point."""

    violations = L.validate_ledger(document, check_files=True)
    if not violations:
        return
    summary = ", ".join(f"{issue.path}:{issue.code}" for issue in violations[:4])
    raise ConcurrentMigrationError(
        f"migration candidate or file closure changed before commit: {path}: {summary}"
    )


def _read_locked_bytes(locked):
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(locked.ledger_name, flags, dir_fd=locked.directory_fd)
    try:
        chunks = []
        while chunk := os.read(fd, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _commit_batch(staged):
    """Commit one staged ledger; fail closed for unsafe multi-ledger apply.

    Python rollback after an exception is not recovery from SIGKILL or power
    loss.  Until this one-shot tool has a durable journal/recovery protocol,
    publishing more than one ledger is explicitly blocked before any lock or
    write.  Single-ledger replacement remains atomically safe.
    """

    ordered = sorted(staged, key=lambda item: str(item[0]))
    canonical_paths = [os.path.abspath(os.fspath(item[0])) for item in ordered]
    if len(set(canonical_paths)) != len(canonical_paths):
        raise ConcurrentMigrationError("duplicate ledger path in migration batch")
    if len(ordered) > 1:
        raise UnsafeBatchMigrationError(
            "multi-ledger apply is BLOCKED: no crash-atomic durable recovery protocol"
        )
    if not ordered:
        return

    path, original, document = ordered[0]
    with L._locked_ledger(path) as locked:
        if _read_locked_bytes(locked) != original:
            raise ConcurrentMigrationError(f"ledger changed during migration staging: {path}")
        _require_commit_candidate_valid(path, document)
        _write_staged_document(path, document, locked=locked)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", default=str(DEV / "data/asset_library"))
    ap.add_argument("--upstream", default=str(DEV / "data/upstream_ledgers"))
    ap.add_argument("--attributes", default=str(DEV / "data/scene_gen_ext/asset_attributes.json"))
    ap.add_argument("--survey", default=str(DEV / "data/scene_gen_ext/top_support_survey.json"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    attrs = json.loads(Path(a.attributes).read_text()).get("models", {})
    survey = json.loads(Path(a.survey).read_text()).get("models", {})

    n_ok = n_bad = 0
    staged = []
    for root, is_up in ((Path(a.library), False), (Path(a.upstream), True)):
        for asset_path in L.iter_assets(root):
            lp = asset_path / "ledger.json"
            if not lp.is_file():
                continue
            original = lp.read_bytes()
            led = json.loads(original)
            result = migrate_document(led, attrs, survey, is_up)
            violations = L.validate_ledger(result.document, check_files=True)
            # Profile requirements are release requirements, not advisory
            # debt.  A cross-backend document without inertial/Isaac evidence
            # is still invalid v3 and must never be written merely because all
            # unconditional fields happened to migrate.
            hard = list(violations)
            failures = _migration_failures(result, hard)
            if failures:
                n_bad += 1
                migration_issues = [
                    f"{issue.path}:{issue.code}" for issue in (*result.blockers, *result.debts)
                ]
                print(
                    f"FAIL {lp.parent.name}: "
                    f"{(migration_issues + [f'{v.path}:{v.code}' for v in hard])[:4]}"
                )
                continue
            n_ok += 1
            changed = result.changes or ("already_complete_v3",)
            tagline = ", ".join(changed[:5]) + ("…" if len(changed) > 5 else "")
            print(f"ok   {lp.parent.name}: {tagline}")
            staged.append((lp, original, result.document))
    if not a.dry_run and n_bad == 0:
        try:
            _commit_batch(staged)
        except (OSError, ConcurrentMigrationError) as exc:
            n_bad += 1
            print(f"FAIL transaction: {exc}")
    print(f"\n{'DRYRUN ' if a.dry_run else ''}migrated={n_ok} failed={n_bad}")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
