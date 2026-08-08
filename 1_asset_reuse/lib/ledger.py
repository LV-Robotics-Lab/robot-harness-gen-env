"""Per-asset ledger contract: validator, builder, verification lock, IR unpack.

Pure stdlib so both conda envs (isaac-smoke py3.11 / env-gen-yuxin py3.10) can
import it. May import lib.conventions (also pure stdlib) but nothing else.

The constant tables below (KINDS, BACKENDS, ROLES, CHECKS, VERDICTS,
MASS_STATUS, DEFAULT_BASIS, REQUIRED_MODEL, ...) ARE the contract. spec §3 is
a documentation view of these tables, not the other way around: if the two
ever disagree, this file wins and spec §3 needs to be updated to match.
"""

import hashlib
import fcntl
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lib import conventions

SCHEMA_VERSION = "asset_ledger.v1"
KINDS = ("rigid", "articulated")
BACKENDS = ("sapien", "isaacsim", "portable")
ROLES = ("visual", "collision", "visual_and_collision", "snapshot")
CHECKS = ("settle", "joint_sweep", "runtime_load", "e2e", "admission_report")
VERDICTS = ("pass", "fail")
MASS_STATUS = ("known", "estimated", "unknown")
DEFAULT_BASIS = ("global_constant", "category_typical", "urdf_inertial", "none")

# Re-exported from conventions.py (not `from ... import X90_WXYZ` — an
# unused-import formatter strips names that are only ever re-exported).
X90_WXYZ = conventions.X90_WXYZ
IDENTITY_WXYZ = conventions.IDENTITY_WXYZ

# Asset-level required fields (dotted paths, relative to the ledger root).
REQUIRED_ASSET = (
    "asset_id",
    "category",
    "semantic_name",
    "kind",
    "tags",
    "semantics.aliases",
    "models",
)

# Asset-level fields that may not be null even when the key is present
# (present-but-null is otherwise invisible to the presence-only REQUIRED_ASSET
# check, since the key does exist).
NOT_NULLABLE_ASSET = (
    "asset_id",
    "category",
    "semantic_name",
    "kind",
    "tags",
    "semantics",
    "models",
)

# Per-model required fields (dotted paths, relative to a models[] entry).
REQUIRED_MODEL = (
    "model_id",
    "physical.mesh_bbox_m",
    "physical.mesh_up_axis",
    "physical.origin_convention",
    "physical.scale_applied",
    "physical.size_resolution",
    "physical.conventions.is_static",
    "physical.conventions.z_policy",
    "physical.conventions.footprint_shape",
    "physical.conventions.stable_poses",
    "physical.conventions.inherited_from",
    "physical.mass_kg",
    "physical.friction",
    "source.library",
    "source.group",
    "source.file",
    "source.license",
    "source.retrieved_at",
    "source.source_manifest_path",
    "verification",
)

# Per-model fields that may not be null even when the key is present (same
# present-but-null gap as NOT_NULLABLE_ASSET, but for REQUIRED_MODEL). Not
# every REQUIRED_MODEL path belongs here:
#   - physical.conventions.inherited_from is null BY DESIGN (no precedent).
#   - physical.mass_kg / physical.friction: a null here is already caught as
#     "not a dict" -> unknown_shape (see _validate_model), a more specific
#     code than a blanket "missing" would be.
#   - source.license: a null here is already caught by _validate_license
#     (not isinstance(..., dict)) -> license_not_structured.
# Nested optional sub-fields (source.license.spdx,
# physical.size_resolution.reference_max_dim_m, friction.runtime_default,
# ...) are legitimately nullable and are intentionally NOT on this list --
# only REQUIRED_MODEL's own (whole-field) paths are checked here.
NOT_NULLABLE_MODEL = (
    "model_id",
    "physical.mesh_bbox_m",
    "physical.mesh_up_axis",
    "physical.origin_convention",
    "physical.scale_applied",
    "physical.size_resolution",
    "physical.conventions.is_static",
    "physical.conventions.z_policy",
    "physical.conventions.footprint_shape",
    "physical.conventions.stable_poses",
    "source.library",
    "source.group",
    "source.file",
    "source.retrieved_at",
    "source.source_manifest_path",
    "verification",
)

# Derived fields that must never be handwritten into a ledger on disk; they
# are computed by derive_usable() at read time.
DERIVED_FIELDS = ("usable", "missing")

_MISSING = object()


@dataclass
class Violation:
    path: str
    code: str
    message: str


def _get(node, dotted_path):
    """Resolve a dotted path (list indices as digit segments) against node.
    Returns _MISSING (the sentinel) if any segment is absent/out of range."""
    cur = node
    for part in dotted_path.split("."):
        if part.isdigit():
            idx = int(part)
            if not isinstance(cur, list) or idx >= len(cur) or idx < 0:
                return _MISSING
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or part not in cur:
                return _MISSING
            cur = cur[part]
    return cur


def _check_required(node, required_paths, prefix, out):
    """Append a `missing` Violation for every path in required_paths that is
    absent from node. prefix is prepended to the reported violation path."""
    for p in required_paths:
        if _get(node, p) is _MISSING:
            full = f"{prefix}.{p}" if prefix else p
            out.append(Violation(full, "missing", f"required field missing: {full}"))


def ledger_path(library_dir, asset):
    """<library_dir>/<asset>/ledger.json"""
    return Path(library_dir) / asset / "ledger.json"


def reps_digest(model_entry, backend):
    """sha256(",".join(sorted(sha256 of each representation for `backend`,
    excluding role=='snapshot')))."""
    shas = sorted(
        r["sha256"]
        for r in model_entry.get("representations", [])
        if r.get("backend") == backend and r.get("role") != "snapshot"
    )
    return hashlib.sha256(",".join(shas).encode()).hexdigest()


def _validate_stable_poses(poses, prefix, out):
    if not poses:
        out.append(Violation(prefix, "no_stable_pose", "stable_poses is empty"))
        return
    n_default = sum(1 for p in poses if p.get("is_default"))
    if n_default != 1:
        out.append(
            Violation(
                prefix,
                "multiple_default_poses",
                f"expected exactly 1 is_default pose, got {n_default}",
            )
        )
    for i, pose in enumerate(poses):
        wxyz = pose.get("orientation_wxyz")
        if not wxyz or len(wxyz) != 4:
            out.append(
                Violation(
                    f"{prefix}.{i}.orientation_wxyz",
                    "bad_quaternion",
                    "not a length-4 quaternion",
                )
            )
            continue
        norm = math.sqrt(sum(c * c for c in wxyz))
        if abs(norm - 1.0) > 1e-6:
            out.append(
                Violation(
                    f"{prefix}.{i}.orientation_wxyz",
                    "bad_quaternion",
                    f"orientation_wxyz is not a unit quaternion (norm={norm})",
                )
            )


def _validate_mass_or_friction(block, prefix, out):
    status = block.get("status")
    if status not in MASS_STATUS:
        out.append(
            Violation(
                f"{prefix}.status",
                "bad_enum",
                f"status {status!r} not in {MASS_STATUS}",
            )
        )
    elif status == "known" and block.get("value") is None:
        out.append(
            Violation(
                f"{prefix}.value", "unknown_shape", "status=known but value is None"
            )
        )
    elif status == "estimated" and not block.get("estimator"):
        out.append(
            Violation(
                f"{prefix}.estimator",
                "estimator_required",
                "status=estimated requires an estimator",
            )
        )
    basis = block.get("runtime_default_basis")
    if basis not in DEFAULT_BASIS:
        out.append(
            Violation(
                f"{prefix}.runtime_default_basis",
                "bad_enum",
                f"runtime_default_basis {basis!r} not in {DEFAULT_BASIS}",
            )
        )


def _validate_representations(reps, prefix, out):
    has_sapien = any(
        r.get("backend") == "sapien" and r.get("role") != "snapshot" for r in reps
    )
    if not has_sapien:
        out.append(
            Violation(
                prefix,
                "no_sapien_representation",
                "no non-snapshot sapien representation",
            )
        )
    for i, r in enumerate(reps):
        rp = f"{prefix}.{i}"
        for field in ("format", "uri", "role", "sha256", "size_bytes"):
            if field not in r or r[field] is None:
                out.append(
                    Violation(
                        f"{rp}.{field}",
                        "missing",
                        f"required field missing: {rp}.{field}",
                    )
                )
        if "role" in r and r["role"] is not None and r["role"] not in ROLES:
            out.append(
                Violation(
                    f"{rp}.role", "bad_enum", f"role {r['role']!r} not in {ROLES}"
                )
            )
        if "backend" not in r or r.get("backend") is None:
            out.append(
                Violation(
                    f"{rp}.backend", "missing", f"required field missing: {rp}.backend"
                )
            )
        elif r["backend"] not in BACKENDS:
            out.append(
                Violation(
                    f"{rp}.backend",
                    "bad_enum",
                    f"backend {r['backend']!r} not in {BACKENDS}",
                )
            )
        sha = r.get("sha256")
        if sha is not None and not (
            isinstance(sha, str)
            and len(sha) == 64
            and all(c in "0123456789abcdef" for c in sha.lower())
        ):
            out.append(
                Violation(f"{rp}.sha256", "bad_sha256", "sha256 is not 64 hex chars")
            )


def _validate_license(license_block, prefix, out):
    if not isinstance(license_block, dict):
        out.append(Violation(prefix, "license_not_structured", "license is not a dict"))
        return
    for field in ("spdx", "status", "terms_note"):
        if field not in license_block:
            out.append(
                Violation(
                    prefix, "license_not_structured", f"license missing field: {field}"
                )
            )
            return
    if license_block["status"] not in ("declared", "unknown"):
        out.append(
            Violation(
                f"{prefix}.status",
                "bad_enum",
                f"license status {license_block['status']!r} not in ('declared', 'unknown')",
            )
        )


def _validate_verification(verifications, prefix, out):
    required = (
        "backend",
        "check",
        "verdict",
        "run_id",
        "timestamp",
        "verified_digest",
        "report_path",
    )
    for i, v in enumerate(verifications):
        vp = f"{prefix}.{i}"
        for field in required:
            if field not in v or v[field] is None:
                out.append(
                    Violation(
                        f"{vp}.{field}",
                        "missing",
                        f"required field missing: {vp}.{field}",
                    )
                )
        if "check" in v and v["check"] is not None and v["check"] not in CHECKS:
            out.append(
                Violation(
                    f"{vp}.check", "bad_enum", f"check {v['check']!r} not in {CHECKS}"
                )
            )
        if "verdict" in v and v["verdict"] is not None and v["verdict"] not in VERDICTS:
            out.append(
                Violation(
                    f"{vp}.verdict",
                    "bad_enum",
                    f"verdict {v['verdict']!r} not in {VERDICTS}",
                )
            )


def _validate_model(model, prefix, out):
    _check_required(model, REQUIRED_MODEL, prefix, out)

    # present-but-null is invisible to the presence-only check above (the
    # key does exist); see NOT_NULLABLE_MODEL's docstring for which
    # REQUIRED_MODEL paths are excluded and why.
    for p in NOT_NULLABLE_MODEL:
        if _get(model, p) is None:
            full = f"{prefix}.{p}"
            out.append(Violation(full, "missing", f"required field is null: {full}"))

    conv = _get(model, "physical.conventions")
    if isinstance(conv, dict):
        poses = conv.get("stable_poses")
        if poses is not None:
            _validate_stable_poses(
                poses, f"{prefix}.physical.conventions.stable_poses", out
            )

    mass = _get(model, "physical.mass_kg")
    if mass is not _MISSING:
        if isinstance(mass, dict):
            _validate_mass_or_friction(mass, f"{prefix}.physical.mass_kg", out)
        else:
            out.append(
                Violation(
                    f"{prefix}.physical.mass_kg",
                    "unknown_shape",
                    "mass_kg is not a dict",
                )
            )

    friction = _get(model, "physical.friction")
    if friction is not _MISSING:
        if isinstance(friction, dict):
            _validate_mass_or_friction(friction, f"{prefix}.physical.friction", out)
        else:
            out.append(
                Violation(
                    f"{prefix}.physical.friction",
                    "unknown_shape",
                    "friction is not a dict",
                )
            )

    reps = model.get("representations")
    if reps is not None:
        _validate_representations(reps, f"{prefix}.representations", out)
    else:
        out.append(
            Violation(
                f"{prefix}.representations",
                "no_sapien_representation",
                "no representations",
            )
        )

    license_block = _get(model, "source.license")
    if license_block is not _MISSING:
        _validate_license(license_block, f"{prefix}.source.license", out)

    verifications = model.get("verification")
    if verifications is not None:
        _validate_verification(verifications, f"{prefix}.verification", out)


def validate_ledger(ledger, *, check_files=True):
    out = []

    # schema_version is checked first and short-circuits: a document that
    # isn't declared (or doesn't match) v1 shouldn't get flooded with
    # unrelated v1-shape violations below.
    schema_version = ledger.get("schema_version", _MISSING)
    if schema_version is _MISSING:
        out.append(
            Violation("schema_version", "needs_backfill", "schema_version is missing")
        )
        return out
    elif schema_version != SCHEMA_VERSION:
        out.append(
            Violation(
                "schema_version",
                "bad_schema_version",
                f"schema_version {schema_version!r} != {SCHEMA_VERSION!r}",
            )
        )
        return out

    for field in DERIVED_FIELDS:
        if field in ledger:
            out.append(
                Violation(
                    field,
                    "derived_field_handwritten",
                    f"{field} is derived, must not be handwritten",
                )
            )

    _check_required(ledger, REQUIRED_ASSET, "", out)

    # present-but-null is invisible to the presence-only check above (the key
    # does exist); a null value on any of these is just as unusable as an
    # absent key, so it's reported the same way.
    for field in NOT_NULLABLE_ASSET:
        if field in ledger and ledger[field] is None:
            out.append(Violation(field, "missing", f"required field is null: {field}"))

    kind = ledger.get("kind")
    if kind is not None and "kind" in ledger and kind not in KINDS:
        out.append(Violation("kind", "bad_enum", f"kind {kind!r} not in {KINDS}"))

    aliases = _get(ledger, "semantics.aliases")
    if aliases is not None and aliases != _MISSING and aliases == []:
        out.append(
            Violation(
                "semantics.aliases", "empty_aliases", "semantics.aliases is empty"
            )
        )

    models = ledger.get("models")
    if models is not None:
        if not isinstance(models, list):
            out.append(
                Violation(
                    "models",
                    "bad_type",
                    f"models must be a list, got {type(models).__name__}",
                )
            )
        elif models == []:
            out.append(Violation("models", "no_models", "models is empty"))
        else:
            seen_ids = {}
            for i, m in enumerate(models):
                mid = m.get("model_id")
                if mid is not None:
                    seen_ids.setdefault(mid, []).append(i)
                _validate_model(m, f"models.{i}", out)
                if kind == "articulated":
                    articulation = m.get("articulation")
                    if (
                        not isinstance(articulation, dict)
                        or "joint_names" not in articulation
                    ):
                        out.append(
                            Violation(
                                f"models.{i}.articulation",
                                "articulation_required",
                                "kind=articulated requires articulation.joint_names",
                            )
                        )
            for mid, idxs in seen_ids.items():
                if len(idxs) > 1:
                    out.append(
                        Violation(
                            "models",
                            "duplicate_model_id",
                            f"model_id {mid!r} used by models {idxs}",
                        )
                    )

    if check_files and isinstance(models, list):
        for i, m in enumerate(models):
            for j, r in enumerate(m.get("representations", [])):
                uri = r.get("uri")
                if not uri:
                    continue
                p = Path(uri)
                rp = f"models.{i}.representations.{j}"
                if not p.exists():
                    out.append(
                        Violation(f"{rp}.uri", "file_missing", f"file not found: {uri}")
                    )
                    continue
                expected_sha = r.get("sha256")
                if expected_sha:
                    actual_sha = hashlib.sha256(p.read_bytes()).hexdigest()
                    if actual_sha != expected_sha:
                        out.append(
                            Violation(
                                f"{rp}.sha256",
                                "sha256_mismatch",
                                f"sha256 mismatch for {uri}: expected {expected_sha}, got {actual_sha}",
                            )
                        )

    return out


def derive_usable(ledger, model_id):
    """Existence-only check for one model (structural checks 3/4/6/7 in the
    validator step numbering): required fields present, stable pose present,
    a sapien representation present, and (if articulated) articulation
    present. Returns (ok, missing_paths)."""
    models = ledger.get("models", [])
    model = next((m for m in models if m.get("model_id") == model_id), None)
    if model is None:
        return False, [f"models[model_id={model_id}]"]

    missing = []
    prefix = f"models[model_id={model_id}]"
    for p in REQUIRED_MODEL:
        if _get(model, p) is _MISSING:
            missing.append(f"{prefix}.{p}")

    poses = _get(model, "physical.conventions.stable_poses")
    if poses is _MISSING or not poses:
        missing.append(f"{prefix}.physical.conventions.stable_poses")

    reps = model.get("representations", [])
    if not any(
        r.get("backend") == "sapien" and r.get("role") != "snapshot" for r in reps
    ):
        missing.append(f"{prefix}.representations[backend=sapien]")

    if ledger.get("kind") == "articulated":
        articulation = model.get("articulation")
        if not isinstance(articulation, dict) or "joint_names" not in articulation:
            missing.append(f"{prefix}.articulation.joint_names")

    return len(missing) == 0, missing


# ---------------------------------------------------------------------------
# Builder / upsert / verification lock / IR unpack
# ---------------------------------------------------------------------------


def new_model_entry(
    *,
    model,
    representations,
    mesh_bbox_m,
    mesh_up_axis,
    origin_convention,
    scale_applied,
    size_resolution,
    conventions,
    source,
    verification,
    articulation=None,
    mass_override=None,
    friction_override=None,
):
    """Assemble one models[] entry. mass/friction default to the
    conservative unknown shape unless an override is supplied."""
    mass_kg = mass_override or {
        "value": None,
        "status": "unknown",
        "runtime_default_kg": 0.1,
        "runtime_default_basis": "global_constant",
    }
    friction = friction_override or {
        "value": None,
        "status": "unknown",
        "runtime_default": None,
        "runtime_default_basis": "none",
    }
    return {
        "model_id": model,
        "physical": {
            "mesh_bbox_m": mesh_bbox_m,
            "mesh_up_axis": mesh_up_axis,
            "origin_convention": origin_convention,
            "scale_applied": scale_applied,
            "size_resolution": size_resolution,
            "conventions": conventions,
            "mass_kg": mass_kg,
            "friction": friction,
        },
        "representations": representations,
        "articulation": articulation or {},
        "source": source,
        "verification": verification,
    }


def upsert_model(
    ledger,
    *,
    asset,
    category,
    kind,
    aliases,
    colors,
    materials,
    tags,
    model_entry,
    semantic_name=None,
    asset_id_prefix="external",
):
    """ledger=None creates a new per-asset ledger. If ledger already exists,
    asset-level fields must match what's already on disk (ValueError on
    drift). A model_entry with an existing model_id replaces that entry
    wholesale (re-import semantics); otherwise it's appended."""
    semantic_name = semantic_name or category

    if ledger is None:
        ledger = {
            "schema_version": SCHEMA_VERSION,
            "asset_id": f"{asset_id_prefix}_{asset}",
            "category": category,
            "semantic_name": semantic_name,
            "kind": kind,
            "tags": list(tags),
            "semantics": {
                "aliases": list(aliases),
                "colors": list(colors),
                "materials": list(materials),
            },
            "models": [],
        }
    else:
        ledger = json.loads(json.dumps(ledger))  # deep copy, don't mutate caller's dict
        existing_asset_id = ledger.get("asset_id") or ""
        if not existing_asset_id.endswith(f"_{asset}"):
            raise ValueError(
                f"asset param {asset!r} does not match existing asset_id {existing_asset_id!r}"
            )
        expected = {
            "category": category,
            "kind": kind,
            "semantic_name": semantic_name,
            "tags": list(tags),
            "semantics.aliases": list(aliases),
            "semantics.colors": list(colors),
            "semantics.materials": list(materials),
        }
        for path, value in expected.items():
            current = _get(ledger, path)
            if current != value:
                raise ValueError(
                    f"asset-level field drift on {path!r}: existing={current!r} incoming={value!r}"
                )

    models = ledger["models"]
    mid = model_entry["model_id"]
    for i, m in enumerate(models):
        if m.get("model_id") == mid:
            models[i] = model_entry
            break
    else:
        models.append(model_entry)

    return ledger


def _lock_path(path):
    return Path(path).with_suffix(".lock")


def _atomic_write_json(path, data):
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.chmod(tmp_name, 0o644)  # mkstemp defaults to 0600; shared machine
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


def append_verification(path, model_id, entry):
    """Append one verification entry for a model, under an fcntl lock, with
    atomic replace. Deduplicates on (backend, check, run_id, verified_digest):
    appending an identical tuple again is a no-op."""
    path = Path(path)
    lock_path = _lock_path(path)
    lock_path.touch(exist_ok=True)
    with open(lock_path, "w") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            ledger = json.loads(path.read_text())
            models = ledger["models"]
            for m in models:
                if m.get("model_id") == model_id:
                    model = m
                    break
            else:
                raise ValueError(f"no model with model_id={model_id}")

            key = (
                entry["backend"],
                entry["check"],
                entry["run_id"],
                entry["verified_digest"],
            )
            existing_keys = {
                (v["backend"], v["check"], v["run_id"], v["verified_digest"])
                for v in model["verification"]
            }
            if key not in existing_keys:
                model["verification"].append(entry)
                _atomic_write_json(path, ledger)
            return ledger
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def latest_verification(model_entry, backend, check):
    """Most recent (by timestamp) verification entry for (backend, check).
    Returns None if there is none, or if the latest entry's verified_digest
    no longer matches reps_digest(model_entry, backend) (stale -> report as
    unverified rather than trusting a superseded pass)."""
    candidates = [
        v
        for v in model_entry.get("verification", [])
        if v.get("backend") == backend and v.get("check") == check
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda v: v["timestamp"])
    if latest.get("verified_digest") != reps_digest(model_entry, backend):
        return None
    return latest


def to_ir_bundles(ledger):
    """Flatten each models[] entry into a standalone per-model bundle dict
    shaped like the legacy IR AssetBundle: conventions expanded into
    `physical`, snapshot representations dropped, asset_id suffixed with
    `_m<model_id>`."""
    bundles = []
    for m in ledger.get("models", []):
        physical = dict(m.get("physical", {}))
        conventions = physical.pop("conventions", {})
        physical.update(conventions)
        reps = [r for r in m.get("representations", []) if r.get("role") != "snapshot"]
        bundles.append(
            {
                "asset_id": f"{ledger['asset_id']}_m{m['model_id']}",
                "category": ledger.get("category"),
                "representations": reps,
                "source": m.get("source"),
                "physical": physical,
                "articulation": m.get("articulation", {}),
                "tags": ledger.get("tags", []),
            }
        )
    return bundles
