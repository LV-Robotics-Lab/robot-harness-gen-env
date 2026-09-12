"""Per-asset ledger contract: validator, builder, verification lock, IR unpack.

Pure stdlib so both conda envs (isaac-smoke py3.11 / env-gen-yuxin py3.10) can
import it. May import lib.conventions (also pure stdlib) but nothing else.

The constant tables below (KINDS, PROFILES, SOURCE_KINDS, BACKENDS, ROLES,
CHECKS, VERDICTS, MASS_STATUS, INERTIAL_BASIS, IDENTITY_BASIS,
REQUIRED_MODEL, REQUIRED_SOURCE, PROFILE_REQUIRED_MODEL, ...) ARE the
contract. spec §3 is a documentation view of these tables, not the other way
around: if the two ever disagree, this file wins and spec §3 needs to be
updated to match.

v2 changes the shape of "required" itself. In v1 there was one required table
for every model; v2 has three, composed:

  REQUIRED_MODEL          always, whatever the asset is
  REQUIRED_SOURCE[kind]   by how the model came to exist (fetched vs generated)
  PROFILE_REQUIRED_MODEL  by what the asset is FOR (SAPIEN only vs cross-backend)

The reason is that "does this field have a reader" stopped being a global
question: a transfer compiler genuinely reads inertial data, and an asset that
never leaves SAPIEN genuinely has no reader for it. Making it globally
required forces most of the library to carry structured unknowns; making it
globally optional leaves migration with nothing to check. Declaring intent
(`profile`) and keying the requirement off that is the same mechanism
OpenUSD's applied API schemas and NVIDIA's SimReady profiles use.

Orthogonal to all three: a field whose value is UNRECOVERABLE once ingest is
over (source licence terms, generation prompt/seed/model version) is required
under every profile and every kind. Profiles relax what must be *checked*;
they never relax what must be *captured*.
"""

import copy
import datetime
import fcntl
import hashlib
import json
import math
import os
import platform
import re
import secrets
import shlex
import stat
import struct
import sys
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from . import conventions

# representations[].frame / geometry_state are OPTIONAL on purpose, and their
# absence carries meaning -- do not "helpfully" backfill them:
#   * GLBs this pipeline wrote -> both present (up_axis Y, scale baked,
#     bottom-centre origin); import_materialize stamps them at write time
#     (added 2026-08-24; before that only migrate_v3's 08-15 cohort had them,
#     so every later import silently landed without).
#   * NVIDIA _source USDs (49 as of 2026-08-24) -> both ABSENT. They are
#     untouched third-party artifacts: their up axis is NVIDIA's fact, not
#     ours, and no pxr/USD reader exists on this machine to measure it, so
#     claiming one would be invention. Absent scale_baked is also the CORRECT
#     value for them: usd_enrich neutralizes object scale only when
#     scale_baked is True, and these reps are NOT pre-scaled -- asserting True
#     would recreate the E2 bug (a cracker box compiled 2.8527x = exactly
#     1/scale too large, 2026-08-15).
SCHEMA_VERSION = "asset_ledger.v3"
# v3 (2026-08-15, 四路辩证+实验裁决定稿), relative to v2:
#   DELETED  semantic_name, tags (零决策消费者: semantic_name 79/79 恒等于
#            category 且账本→catalog 通路不存在; tags 全词表是 kind/source
#            复制品), mass/friction 的 runtime_default 对 ("runtime" 是谎言:
#            真实运行时质量走上游 catalog 标量通路, 唯一读者是 archive 死码),
#            physical.mesh_up_axis / origin_convention (与 kind 100% 互锁,
#            消费面为零; 帧事实真正的归属是每个 representation 自己 -- 同一
#            model 的 GLB 是 Y-up 已烘 scale, USD 是 Z-up 未烘, per-model
#            字段根本描述不了, 见 representations[].frame)
#   DEMOTED  size_resolution.{mode,reference_*,verdict} / verification[]
#            .report_path / source.source_manifest_path -> 可选留痕
#   ADDED    external_ids (账本/上游目录/IR 三套命名的映射曾全靠代码约定,
#            ledger-backed Isaac 链在第一个真实资产上断裂),
#            models[].appearance (508 份模型级实测色此前无契约位置),
#            physical.placement (可放置性几何此前只活在 s9 生成层),
#            physical.restitution + friction 拆 static/dynamic (业界最小
#            物理集四规范交集), inertial.principal_axes_wxyz (可选),
#            stable_poses[].measured_against 与 placement.measured_against
#            (同库曾并存两种互斥姿态语义; 同网格换加载器停留差 9mm 实证
#            测量-栈耦合), representations[].frame/geometry_state/files[]/
#            collision_meta, BACKENDS += mujoco,
#            semantics.category_anchor / source.license.attribution (可选)
KINDS = ("rigid", "articulated")

# What the asset is FOR. Declared, never derived: an asset that happens to
# own an isaacsim USD is not thereby promoted to cross_backend, and one that
# declares cross_backend without a USD is not thereby demoted -- it owes one,
# and profile_requirement_unmet is how that debt stays visible instead of
# reading as a silent absence. Adding a profile later (e.g. contact_rich,
# once the L0-L4 transfer-consistency thresholds exist) is append-only.
PROFILES = ("sapien_only", "cross_backend")

# How the model came to exist. `retrieved` covers anything that existed
# before we went and got it (a download, a local library); `generated` covers
# anything a model produced, which by definition has no URL, no source
# mirror, and no retrieval date -- and instead has a prompt, a seed and a
# model version that cease to exist the moment the run ends.
SOURCE_KINDS = ("retrieved", "generated")

BACKENDS = ("sapien", "isaacsim", "mujoco", "portable")
ROLES = ("visual", "collision", "visual_and_collision", "snapshot")
# Domain-separates the receipt digest from the v1 implementation, which only
# hashed representations[].sha256 and therefore did not bind loader closure
# members or representation metadata. Existing v1 receipts intentionally
# become stale; only a new runtime check may issue a v2 digest.
REPS_DIGEST_VERSION = "asset-representation-set.v2"
VERIFICATION_EVIDENCE_SCHEMA = "asset_verification_evidence.v2"
VERIFICATION_INVOCATION_SCHEMA = "asset_verification_invocation.v2"
VERIFICATION_CAPABILITY_SCHEMA = "asset_verification_capability.v2"
VERIFICATION_QUALIFICATION_SCHEMA = "asset_verification_qualification.v1"
VERIFICATION_INPUT_ATTESTATION = "immutable_snapshot_pre_post.v1"
VERIFICATION_THREAT_MODEL = "cooperative_runtime_no_concurrent_host_mutator"
EXECUTION_SNAPSHOT_SCHEMA = "asset_execution_snapshot.v2"
RUNTIME_CAPABILITY_SCHEMA = "asset_runtime_capability.v1"
_VERIFICATION_EVIDENCE_SUFFIX = ".verification.json"
_VERIFICATION_EVIDENCE_MAX_BYTES = 4 * 1024 * 1024
# Closed to modes emitted by production writers today. New values need a
# concrete writer/consumer and evidence semantics before becoming contract.
COLLISION_MODES = ("explicit_mesh", "unknown")
# generation_qc is the generator's own pre-physics screening (truncated
# geometry, duplicate bodies, aesthetic threshold). It rides in the same
# verification[] list rather than a parallel field so that "which gates has
# this model passed" keeps exactly one answer and one read semantics.
CHECKS = (
    "settle",
    "joint_sweep",
    "runtime_load",
    "e2e",
    "admission_report",
    "generation_qc",
)
VERDICTS = ("pass", "fail")
MASS_STATUS = ("known", "estimated", "unknown")

# Where a centre-of-mass / inertia tensor came from. engine_derived with null
# values is a complete, informative answer -- it says "not an asset fact, the
# engine infers this from collision geometry" -- and is what most rigid
# models legitimately carry. Silence would say the same thing far less
# usefully, which is why cross_backend requires the KEY, not a value.
INERTIAL_BASIS = ("measured", "urdf_inertial", "engine_derived", "none")

# How this asset's identity (category/aliases) was decided. These are the
# four routes that actually exist today -- measured by the 2026-08-10
# semantics audit over all 31 ledgers -- plus `vlm` for visual re-checking.
# `unknown` is legal and honest: 2 of 31 assets came in through the
# single-articulated-asset path, which keeps no manifest, so their identity
# claim genuinely cannot be attributed after the fact.
IDENTITY_BASIS = (
    "upstream_catalog",
    "manifest_human",
    "requested_by_acquire",
    "vlm",
    "unknown",
)

# Re-exported from conventions.py (not `from ... import X90_WXYZ` — an
# unused-import formatter strips names that are only ever re-exported).
X90_WXYZ = conventions.X90_WXYZ
IDENTITY_WXYZ = conventions.IDENTITY_WXYZ

# Asset-level required fields (dotted paths, relative to the ledger root).
REQUIRED_ASSET = (
    "asset_id",
    "external_ids.env_gen",
    "category",
    "kind",
    "profile",
    "semantics.aliases",
    "semantics.identity.basis",
    "semantics.identity.verified",
    "models",
)

# Asset-level fields that may not be null even when the key is present
# (present-but-null is otherwise invisible to the presence-only REQUIRED_ASSET
# check, since the key does exist).
NOT_NULLABLE_ASSET = (
    "asset_id",
    "category",
    "kind",
    "profile",
    "semantics",
    "models",
)

# Per-model required fields (dotted paths, relative to a models[] entry).
REQUIRED_MODEL = (
    "model_id",
    "physical.mesh_bbox_m",
    "physical.size_resolution",
    "physical.conventions.is_static",
    "physical.conventions.z_policy",
    "physical.conventions.footprint_shape",
    "physical.conventions.stable_poses",
    "physical.conventions.inherited_from",
    "physical.mass_kg",
    "physical.friction",
    # source.kind selects which REQUIRED_SOURCE branch applies; source.license
    # is required under BOTH branches (unrecoverable-after-ingest rule: terms
    # pages change and disappear, and a generated asset's licence is its
    # model's output terms, not "none").
    "source.kind",
    "source.license",
    "verification",
)

# v1 had physical.scale_applied here too. It was removed in v2, not renamed:
# every writer set it from size_resolution["scale"] (import_materialize.py,
# s13b, backfill_upstream all did), it was equal to that value in 31/31
# ledgers on disk, and nothing anywhere read it. The scale now lives once,
# inside the decision record that explains it.

# Branch tables keyed by source.kind. The two branches are disjoint by
# construction -- see _validate_source, which reports a model carrying fields
# from the other branch as source_field_mismatch rather than ignoring them
# (the realistic failure is a ledger copy-pasted from a neighbour and only
# half-edited, which no presence check would otherwise catch).
REQUIRED_SOURCE = {
    "retrieved": (
        "source.library",
        "source.group",
        "source.file",
        "source.retrieved_at",
        # source_manifest_path was required through v2; demoted in v3: 70/87
        # equalled the `_source/<group>/SOURCE_MANIFEST.json` convention the
        # writer itself constructs, and 16 of the 17 exceptions pointed into
        # a checkout that no longer exists. The convention is the contract;
        # the field is an optional override.
    ),
    "generated": (
        "source.generator.tool",
        "source.generator.tool_version",
        "source.generator.model",
        "source.generator.model_version",
        "source.generator.input",
        "source.generator.seed",
        "source.generator.params",
        "source.generator.generated_at",
    ),
}

# Fields from the OTHER branch, whose presence means the two got mixed.
FOREIGN_SOURCE_FIELDS = {
    "retrieved": ("source.generator",),
    "generated": (
        "source.library",
        "source.group",
        "source.file",
        "source.url",
        "source.retrieved_at",
        "source.source_manifest_path",
    ),
}

# Required only under the named profile. sapien_only adds nothing: an asset
# that never leaves SAPIEN has no reader for inertial data, and demanding it
# would only produce structured unknowns nobody consults.
PROFILE_REQUIRED_MODEL = {
    "sapien_only": (),
    "cross_backend": ("physical.inertial",),
}

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
# physical.size_resolution.reference_max_dim_m, friction.static,
# ...) are legitimately nullable and are intentionally NOT on this list --
# only REQUIRED_MODEL's own (whole-field) paths are checked here.
NOT_NULLABLE_MODEL = (
    "model_id",
    "physical.mesh_bbox_m",
    "physical.size_resolution",
    "physical.conventions.is_static",
    "physical.conventions.z_policy",
    "physical.conventions.footprint_shape",
    "physical.conventions.stable_poses",
    "source.kind",
    "verification",
)

# Per-branch non-nullables. Deliberately NOT the whole REQUIRED_SOURCE list:
#   - source.generator.model_version and .seed are required KEYS whose value
#     may legitimately be null. A null seed is not an omission, it is the
#     statement "this generation is not reproducible" -- which is a fact a
#     gate can act on, and strictly more informative than an absent field.
NOT_NULLABLE_SOURCE = {
    "retrieved": (
        "source.library",
        "source.group",
        "source.file",
        "source.retrieved_at",
    ),
    "generated": (
        "source.generator.tool",
        "source.generator.tool_version",
        "source.generator.model",
        "source.generator.input",
        "source.generator.params",
        "source.generator.generated_at",
    ),
}

# Derived fields that must never be handwritten into a ledger on disk; they
# are computed by derive_usable() at read time.
DERIVED_FIELDS = ("usable", "missing")

# v3 deleted these fields rather than making them optional. Accepting a
# document that merely changed its version string while retaining them makes
# the migration impossible to audit: two writers can then emit two different
# meanings under the same schema version.
DELETED_ASSET_FIELDS = ("semantic_name", "tags")
DELETED_MODEL_FIELDS = (
    "physical.mesh_up_axis",
    "physical.origin_convention",
)

_MISSING = object()


class RepresentationDigestError(ValueError):
    """A backend has no loader-visible representation to bind evidence to."""


class VerificationConflictError(ValueError):
    """One producer identity was reused for a different verification fact."""


class VerificationDigestError(ValueError):
    """A new verification fact is stale or bound to another backend set."""


class UnsafeAssetKeyError(ValueError):
    """An asset lookup key is not one canonical portable path segment."""


class UnsafeAssetPathError(ValueError):
    """An asset lookup would traverse a symlink or leave its library root."""


class LedgerIdentityError(ValueError):
    """A ledger's declared env-gen identity disagrees with its location."""


class InvalidModelIdError(ValueError):
    """A model id is not a canonical non-negative integer."""


class DuplicateAssetModelError(ValueError):
    """One input batch repeats the same canonical asset/model identity."""


class VerificationResultError(ValueError):
    """A qualified issuer result or decision threshold is not canonical."""


@dataclass
class Violation:
    path: str
    code: str
    message: str


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
_PORTABLE_ASSET_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_WINDOWS_RESERVED_SEGMENTS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


def _is_iso_date(value):
    """True iff value is a str in canonical YYYY-MM-DD form (exactly 10
    chars, date only -- no time component) that also parses via
    datetime.date.fromisoformat. Used for source.retrieved_at, which is a
    date-of-acquisition, not a timestamp.

    The regex is checked first, not just the fromisoformat try/except: bare
    fromisoformat() alone would accept shapes that are simply the wrong
    field's format (e.g. a full "YYYY-MM-DDTHH:MM:SS" datetime string is a
    valid date-ish prefix to some parsers but is not a bare date) -- see
    _is_iso_datetime's docstring for the fuller rationale, which applies
    symmetrically here."""
    if not isinstance(value, str) or len(value) != 10 or not _ISO_DATE_RE.match(value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_iso_datetime(value):
    """True iff value is a str in canonical YYYY-MM-DDTHH:MM:SS form
    (exactly 19 chars, 'T'-separated, second precision) that also parses via
    datetime.datetime.fromisoformat. Used for verification.timestamp.

    The regex runs first and is load-bearing, not cosmetic: bare
    fromisoformat() alone accepts a strictly larger set that differs
    BETWEEN py3.10 and py3.11 (e.g. a 'Z' suffix, fractional seconds, or a
    compact "YYYYMMDD" form are 3.11-only extensions) -- this project runs
    both interpreters (isaac-smoke py3.11 / env-gen-yuxin py3.10), so
    accepting fromisoformat's raw acceptance set would make a ledger's
    validity depend on which interpreter happened to validate it. The
    space-separated form ("YYYY-MM-DD HH:MM:SS", accepted by fromisoformat
    on both versions) is doubly dangerous even though both interpreters
    agree on it: ' ' (0x20) sorts below every digit, while 'T' (0x54) sorts
    above every digit, so on the same date a space-form entry compares as
    "earlier" than a T-form entry EVEN IF its actual time-of-day is later
    (e.g. "2026-08-08 15:00:00" < "2026-08-08T09:00:00" as strings, though
    15:00 is chronologically after 09:00) -- silently corrupting
    latest_verification's timestamp-max "latest" semantics (max() by string
    comparison) if a space-form entry ever slips in alongside canonical
    ones. Guards against exactly the incident
    shape that motivated this check in the first place: a directory name
    like "batch_v3" naively sliced into date components produced
    "batc-h_-v3T00:00:00" -- syntactically a string, semantically garbage,
    and silently accepted everywhere downstream."""
    if not isinstance(value, str) or len(value) != 19 or not _ISO_DATETIME_RE.match(value):
        return False
    try:
        datetime.datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


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


def _reject_deleted_fields(node, paths, prefix, out):
    for path in paths:
        if _get(node, path) is _MISSING:
            continue
        full = f"{prefix}.{path}" if prefix else path
        out.append(
            Violation(
                full,
                "deleted_field",
                f"field was deleted from {SCHEMA_VERSION}: {full}",
            )
        )


def _validate_portable_identifier(value, path, out):
    """Require the same portable storage segment used by every writer."""

    try:
        canonical_asset_key(value)
    except UnsafeAssetKeyError:
        out.append(
            Violation(
                path,
                "unsafe_identifier",
                f"{path} must be one canonical portable asset key",
            )
        )


# Path portability contract (public repo: no /home/<user> in tracked ledgers;
# a path written on one machine must resolve on another). Anchor mirrors
# runtime_config.ASSET_PIPELINE_ROOT without importing it (lib stays pure
# stdlib): env override first, else this checkout's active root.
ACTIVE_ROOT = Path(os.environ.get("ASSET_PIPELINE_ROOT", Path(__file__).resolve().parents[2]))

QUALIFIED_VERIFICATION_ISSUERS = {
    "asset.materialize_settle.v1": {
        "backend": "sapien",
        "check": "settle",
        "script": "asset_reuse/scripts/3_materialize/import_materialize.py",
        "thresholds": {
            "max_late_drift_m": (0.002,),
            "min_support_z_m": (-0.005,),
            "max_tilt_deg": (15.0, 45.0),
        },
        "runtime": {
            "entrypoint": "SAPIEN ActorBuilder GLB visual/collision settle",
            "loader_module_root": "sapien",
            "sapien_module_root": "sapien",
            "config": {
                "loader_root": "immutable_snapshot_explicit_paths",
                "timestep_s": 0.01,
                "settle_steps": 900,
                "sample_interval_steps": 50,
                "spawn_pose_wxyz": [math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0],
                "spawn_z_m": 0.005,
                "collision_loader": "add_multiple_convex_collisions_from_file",
            },
        },
    },
    "asset.settle_repair.v1": {
        "backend": "sapien",
        "check": "settle",
        "script": "asset_reuse/scripts/ledger/settle_repair.py",
        "thresholds": {
            "max_late_drift_m": (0.002,),
            "min_support_z_m": (-0.005,),
            "max_tilt_deg": (181.0,),
        },
        "runtime": {
            "entrypoint": "RoboTwin envs.utils.create_actor",
            "loader_module_root": "envs",
            "sapien_module_root": "sapien",
            "config": {
                "loader_root": "immutable_snapshot_cwd",
                "timestep_s": 1 / 250,
                "settle_steps": 1500,
                "late_window_steps": 100,
                "spawn_height_m": 0.30,
                "convex": True,
            },
        },
    },
    "asset.s11_runtime_load.v1": {
        "backend": "sapien",
        "check": "runtime_load",
        "script": "asset_reuse/scripts/4_validate/s11_runtime_load_sweep.py",
        "thresholds": {
            "max_late_drift_m": (0.002,),
            "min_final_z_m": (-0.005,),
        },
        "runtime": {
            "entrypoint": "RoboTwin envs.utils create_actor/create_sapien_urdf_obj",
            "loader_module_root": "envs",
            "sapien_module_root": "sapien",
            "config": {
                "loader_root": "immutable_snapshot_cwd",
                "timestep_s": 0.01,
                "steps": 200,
                "late_window_start_step": 150,
                "convex": True,
                "rigid_pose_wxyz": [math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0],
                "urdf_fix_root_link": True,
            },
        },
    },
    "asset.s13b_joint_sweep.v1": {
        "backend": "sapien",
        "check": "joint_sweep",
        "script": "asset_reuse/scripts/4_validate/s13b_validate_articulated.py",
        "thresholds": {
            "min_screenshot_std": (1.0,),
            "allow_free_joints": (False, True),
        },
        "runtime": {
            "entrypoint": "SAPIEN Scene.create_urdf_loader.load",
            "loader_module_root": "sapien",
            "sapien_module_root": "sapien",
            "config": {
                "loader_root": "immutable_snapshot_explicit_path",
                "fix_root_link": True,
                "timestep_s": 0.01,
                "settle_steps": 120,
                "late_window_start_step": 89,
                "joint_limit_steps": 30,
                "render_resolution": [640, 480],
            },
        },
    },
}

# The public table documents the allowed issuers, but it is deliberately not
# the authority consulted at issuance/read time: callers in the same process
# can mutate a dict.  Keep an import-time copy as the closed production
# contract for the real entrypoint, module roots, configuration and thresholds.
_PRODUCTION_QUALIFIED_VERIFICATION_ISSUERS = copy.deepcopy(QUALIFIED_VERIFICATION_ISSUERS)


def qualified_verification_issuer(issuer):
    """Return one closed production issuer description or fail typed."""

    try:
        spec = copy.deepcopy(_PRODUCTION_QUALIFIED_VERIFICATION_ISSUERS[issuer])
    except (KeyError, TypeError) as exc:
        raise VerificationResultError(f"verification issuer is not qualified: {issuer!r}") from exc
    return {
        **spec,
        "issuer": issuer,
        "script_path": _absolute_local_path(ACTIVE_ROOT / spec["script"]),
    }


def qualified_verification_backends():
    """Return backends for which the sealed registry can currently issue evidence."""

    return tuple(
        sorted({spec["backend"] for spec in _PRODUCTION_QUALIFIED_VERIFICATION_ISSUERS.values()})
    )


def qualified_verification_thresholds_are_allowed(thresholds, issuer_spec):
    """Return whether thresholds match the fixed policy of one issuer."""

    policy = issuer_spec.get("thresholds") if isinstance(issuer_spec, dict) else None
    if (
        not isinstance(thresholds, dict)
        or not isinstance(policy, dict)
        or frozenset(thresholds) != frozenset(policy)
    ):
        return False
    for name, allowed in policy.items():
        value = thresholds[name]
        if (
            not isinstance(allowed, tuple)
            or not allowed
            or not any(
                type(value) is type(candidate) and value == candidate for candidate in allowed
            )
        ):
            return False
    return True


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _strict_thresholds(thresholds, fields):
    if not isinstance(thresholds, dict) or frozenset(thresholds) != frozenset(fields):
        raise VerificationResultError(
            f"decision thresholds must contain exactly {sorted(fields)!r}"
        )
    if not all(_finite_number(thresholds[field]) for field in fields):
        raise VerificationResultError("decision thresholds must be finite numbers")


def _strict_result(result, schema, fields):
    expected = frozenset({"schema", "details", *fields})
    if not isinstance(result, dict) or frozenset(result) != expected:
        raise VerificationResultError(f"{schema} result must contain exactly {sorted(expected)!r}")
    if result.get("schema") != schema or not isinstance(result.get("details"), dict):
        raise VerificationResultError(f"result must use {schema} with object details")


def z_policy_from_origin(origin_z_m):
    if not _finite_number(origin_z_m):
        raise VerificationResultError("origin_z_m must be a finite number")
    return "origin_on_table" if abs(origin_z_m) < 0.02 else "center_on_table"


def qualified_verification_verdict(backend, check, thresholds, result):
    """Validate typed physical facts and derive their verdict.

    Producers never supply the verdict accepted by the ledger.  The closed
    evaluator here is shared by issuance and every later trust decision.
    """

    if backend != "sapien":
        raise VerificationResultError(f"no qualified result evaluator for backend {backend!r}")
    if check == "settle":
        threshold_fields = {
            "max_late_drift_m",
            "min_support_z_m",
            "max_tilt_deg",
        }
        result_fields = {
            "finite",
            "late_drift_m",
            "support_z_m",
            "tilt_deg",
            "rest_orientation_wxyz",
            "origin_z_m",
            "derived_z_policy",
        }
        _strict_thresholds(thresholds, threshold_fields)
        _strict_result(result, "asset_settle_result.v2", result_fields)
        orientation = result["rest_orientation_wxyz"]
        metrics = tuple(result[field] for field in ("late_drift_m", "support_z_m", "tilt_deg"))
        origin_z_m = result["origin_z_m"]
        derived_z_policy = result["derived_z_policy"]
        if (
            type(result["finite"]) is not bool
            or any(value is not None and not _finite_number(value) for value in metrics)
            or (result["finite"] and not all(_finite_number(value) for value in metrics))
            or not isinstance(orientation, list)
            or len(orientation) != 4
            or not all(_finite_number(value) for value in orientation)
            or abs(math.sqrt(sum(value * value for value in orientation)) - 1.0) > 1e-6
            or (origin_z_m is not None and not _finite_number(origin_z_m))
            or derived_z_policy not in {None, "origin_on_table", "center_on_table"}
            or (origin_z_m is None) != (derived_z_policy is None)
            or (result["finite"] and origin_z_m is None)
            or (origin_z_m is not None and derived_z_policy != z_policy_from_origin(origin_z_m))
        ):
            raise VerificationResultError("settle result fields are not canonical physical facts")
        passed = (
            result["finite"]
            and all(_finite_number(value) for value in metrics)
            and result["late_drift_m"] < thresholds["max_late_drift_m"]
            and result["support_z_m"] > thresholds["min_support_z_m"]
            and result["tilt_deg"] < thresholds["max_tilt_deg"]
        )
    elif check == "runtime_load":
        threshold_fields = {"max_late_drift_m", "min_final_z_m"}
        result_fields = {
            "loaded",
            "finite",
            "late_drift_m",
            "final_z_m",
            "spawn_pose_id",
            "spawn_orientation_wxyz",
            "observed_spawn_orientation_wxyz",
            "observed_spawn_origin_z_m",
            "fix_root_link",
            "z_policy",
        }
        _strict_thresholds(thresholds, threshold_fields)
        _strict_result(result, "asset_runtime_load_result.v3", result_fields)
        if (
            type(result["loaded"]) is not bool
            or type(result["finite"]) is not bool
            or type(result["fix_root_link"]) is not bool
        ):
            raise VerificationResultError(
                "runtime_load loaded/finite/fix_root_link facts must be bool"
            )
        metrics = (result["late_drift_m"], result["final_z_m"])
        orientation = result["spawn_orientation_wxyz"]
        observed_orientation = result["observed_spawn_orientation_wxyz"]
        observed_origin_z = result["observed_spawn_origin_z_m"]
        if (
            any(value is not None and not _finite_number(value) for value in metrics)
            or not isinstance(result["spawn_pose_id"], str)
            or not result["spawn_pose_id"]
            or not isinstance(orientation, list)
            or len(orientation) != 4
            or not all(_finite_number(value) for value in orientation)
            or abs(math.sqrt(sum(value * value for value in orientation)) - 1.0) > 1e-6
            or (
                observed_orientation is not None
                and (
                    not isinstance(observed_orientation, list)
                    or len(observed_orientation) != 4
                    or not all(_finite_number(value) for value in observed_orientation)
                    or abs(math.sqrt(sum(value * value for value in observed_orientation)) - 1.0)
                    > 1e-6
                )
            )
            or (observed_origin_z is not None and not _finite_number(observed_origin_z))
            or (result["loaded"] and (observed_orientation is None or observed_origin_z is None))
            or result["z_policy"] not in {"origin_on_table", "center_on_table"}
        ):
            raise VerificationResultError(
                "runtime_load facts must bind one finite stable placement"
            )
        passed = (
            result["loaded"]
            and result["finite"]
            and _finite_number(result["late_drift_m"])
            and _finite_number(result["final_z_m"])
            and result["late_drift_m"] < thresholds["max_late_drift_m"]
            and result["final_z_m"] > thresholds["min_final_z_m"]
        )
    elif check == "joint_sweep":
        threshold_fields = {"min_screenshot_std", "allow_free_joints"}
        if (
            not isinstance(thresholds, dict)
            or frozenset(thresholds) != frozenset(threshold_fields)
            or not _finite_number(thresholds.get("min_screenshot_std"))
            or type(thresholds.get("allow_free_joints")) is not bool
        ):
            raise VerificationResultError(
                "joint_sweep thresholds require finite min_screenshot_std and bool "
                "allow_free_joints"
            )
        result_fields = {
            "loaded",
            "dof",
            "expected_dof",
            "limits_match",
            "settle_finite",
            "converged",
            "free_joint_count",
            "sweep_finite",
            "screenshot_std",
            "fix_root_link",
            "root_pose_id",
            "root_orientation_wxyz",
            "z_policy",
        }
        _strict_result(result, "asset_joint_sweep_result.v2", result_fields)
        bool_fields = (
            "loaded",
            "limits_match",
            "settle_finite",
            "converged",
            "sweep_finite",
        )
        if (
            any(type(result[field]) is not bool for field in bool_fields)
            or type(result["fix_root_link"]) is not bool
            or any(
                type(result[field]) is not int or result[field] < 0
                for field in ("dof", "expected_dof", "free_joint_count")
            )
            or not _finite_number(result["screenshot_std"])
            or not isinstance(result["root_pose_id"], str)
            or not result["root_pose_id"]
            or not isinstance(result["root_orientation_wxyz"], list)
            or len(result["root_orientation_wxyz"]) != 4
            or not all(_finite_number(value) for value in result["root_orientation_wxyz"])
            or abs(math.sqrt(sum(value * value for value in result["root_orientation_wxyz"])) - 1.0)
            > 1e-6
            or result["z_policy"] not in {"origin_on_table", "center_on_table"}
        ):
            raise VerificationResultError("joint_sweep result fields are not canonical")
        passed = (
            result["loaded"]
            and result["fix_root_link"]
            and result["dof"] == result["expected_dof"]
            and result["limits_match"]
            and result["settle_finite"]
            and result["converged"]
            and (result["free_joint_count"] == 0 or thresholds["allow_free_joints"])
            and result["sweep_finite"]
            and result["screenshot_std"] > thresholds["min_screenshot_std"]
        )
    else:
        raise VerificationResultError(f"check {check!r} has no qualified physical evaluator")
    return "pass" if passed else "fail"


def _default_stable_pose(model_entry):
    try:
        conventions_block = model_entry["physical"]["conventions"]
        poses = conventions_block["stable_poses"]
    except (KeyError, TypeError) as exc:
        raise VerificationResultError("model has no canonical stable-pose facts") from exc
    defaults = [pose for pose in poses if isinstance(pose, dict) and pose.get("is_default") is True]
    if len(defaults) != 1 or not isinstance(conventions_block.get("z_policy"), str):
        raise VerificationResultError("model must have exactly one default pose and one z_policy")
    return conventions_block, defaults[0]


def settle_physical_facts_digest(model_entry):
    """Hash the exact stable-pose facts a settle pass is allowed to promote."""

    conventions_block, default_pose = _default_stable_pose(model_entry)
    document = {
        "schema": "asset_settle_physical_facts.v2",
        "is_static": conventions_block.get("is_static"),
        "z_policy": conventions_block["z_policy"],
        "default_stable_pose": default_pose,
    }
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def settle_result_matches_physical_facts(model_entry, result, verdict):
    """Require a passing measured rest orientation to be the promoted default."""

    if verdict != "pass":
        return True
    _conventions_block, default_pose = _default_stable_pose(model_entry)
    return result.get("rest_orientation_wxyz") == default_pose.get(
        "orientation_wxyz"
    ) and result.get("derived_z_policy") == _conventions_block.get("z_policy")


def runtime_load_result_matches_physical_facts(model_entry, result):
    """Bind a runtime-load observation to the promoted placement it executed."""

    conventions_block, default_pose = _default_stable_pose(model_entry)
    expected_orientation = default_pose.get("orientation_wxyz")
    observed_orientation = result.get("observed_spawn_orientation_wxyz")
    orientations_match = (
        isinstance(expected_orientation, list)
        and isinstance(observed_orientation, list)
        and len(expected_orientation) == len(observed_orientation) == 4
        and abs(
            abs(
                sum(left * right for left, right in zip(expected_orientation, observed_orientation))
            )
            - 1.0
        )
        <= 1e-6
    )
    declared_placement_matches = (
        result.get("spawn_pose_id") == default_pose.get("pose_id")
        and result.get("spawn_orientation_wxyz") == expected_orientation
        and result.get("fix_root_link") is conventions_block.get("is_static")
        and result.get("z_policy") == conventions_block.get("z_policy")
    )
    if not result.get("loaded"):
        return declared_placement_matches
    return (
        declared_placement_matches
        and orientations_match
        and z_policy_from_origin(result.get("observed_spawn_origin_z_m"))
        == conventions_block.get("z_policy")
    )


def joint_sweep_result_matches_physical_facts(model_entry, result):
    """Bind a fixed-root articulation sweep to its published placement facts."""

    conventions_block, default_pose = _default_stable_pose(model_entry)
    return (
        conventions_block.get("is_static") is True
        and result.get("fix_root_link") is True
        and result.get("root_pose_id") == default_pose.get("pose_id")
        and result.get("root_orientation_wxyz") == default_pose.get("orientation_wxyz")
        and result.get("z_policy") == conventions_block.get("z_policy")
    )


def to_portable_uri(path):
    """ACTIVE_ROOT-relative posix string when under the active tree, else the
    absolute path unchanged (caller decides whether that is acceptable)."""
    p = Path(path).resolve()
    try:
        return p.relative_to(ACTIVE_ROOT.resolve()).as_posix()
    except ValueError:
        return str(p)


def resolve_uri(uri):
    """Inverse of to_portable_uri: relative uris anchor at ACTIVE_ROOT."""
    p = Path(uri)
    return p if p.is_absolute() else ACTIVE_ROOT / p


def canonical_asset_key(value):
    """Return one canonical portable asset segment or fail closed."""

    if (
        not isinstance(value, str)
        or _PORTABLE_ASSET_KEY_RE.fullmatch(value) is None
        or value.endswith(".")
        or value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_SEGMENTS
    ):
        raise UnsafeAssetKeyError(
            f"asset key must be one canonical portable path segment: {value!r}"
        )
    return value


def canonical_json_bytes(value):
    """Canonical UTF-8 JSON used by immutable trust artifacts."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def canonical_model_id(value):
    """Return a model id whose JSON and path spelling has one meaning."""

    if type(value) is not int or value < 0:
        raise InvalidModelIdError(
            f"model_id must be a canonical non-negative integer, got {value!r}"
        )
    return value


def claim_asset_model(seen, asset, model_id):
    """Canonicalize and claim one batch identity, rejecting a second claim."""

    identity = (canonical_asset_key(asset), canonical_model_id(model_id))
    if identity in seen:
        raise DuplicateAssetModelError(f"duplicate asset/model input: {identity[0]} m{identity[1]}")
    seen.add(identity)
    return identity


def _safe_asset_path(root, candidate):
    root = _absolute_local_path(root)
    candidate = _absolute_local_path(candidate)
    if not candidate.is_relative_to(root):
        raise UnsafeAssetPathError(f"asset path escapes library root: {candidate}")
    for inspected in (root, candidate):
        try:
            symlink = _symlink_component(inspected)
        except _ClosureInspectionError as exc:
            raise UnsafeAssetPathError(str(exc)) from exc
        if symlink is not None:
            raise UnsafeAssetPathError(f"asset path traverses symlink component: {symlink}")
    return candidate


_ASSET_DIR_NAME = re.compile(r"^\d+_")


def _is_asset_name(name):
    """Asset directories are `<number>_<category>` (301_cup, 001_bottle); the
    provider grouping level (nvidia/, objaverse/, github/) never is. A pure
    name test, so classifying a library costs no extra stat() per entry."""
    return bool(_ASSET_DIR_NAME.match(name))


def asset_dir(library_dir, asset):
    """Locate `asset`'s directory in a library laid out EITHER flat
    (<library_dir>/<asset>/) or grouped by source provider
    (<library_dir>/<provider>/<asset>/).

    Both layouts are accepted deliberately: the reader change and the
    directory move are separate commits, so every intermediate state stays
    runnable and either half can be rolled back on its own. Returns None when
    the asset is absent -- callers needing it raise with their own context.
    """
    asset = canonical_asset_key(asset)
    root = _safe_asset_path(library_dir, library_dir)
    flat = _safe_asset_path(root, root / asset)
    hits = [flat] if flat.is_dir() else []
    if not root.exists():
        return None
    if not root.is_dir():
        raise UnsafeAssetPathError(f"library root is not a directory: {root}")
    for provider in sorted(root.iterdir()):
        if provider.name.startswith("_") or provider == flat:
            continue
        provider = _safe_asset_path(root, provider)
        if not provider.is_dir():
            continue
        candidate = _safe_asset_path(root, provider / asset)
        if candidate.is_dir():
            hits.append(candidate)
    if len(hits) > 1:
        raise ValueError(
            "asset %s resolves to %d directories (%s) -- a library must hold "
            "one directory per asset id" % (asset, len(hits), sorted(map(str, hits)))
        )
    return hits[0] if hits else None


def iter_assets(library_dir):
    """Yield every asset directory, flat or provider-grouped, sorted by asset
    id so reports/fragments stay diffable across runs AND across layouts.

    `_`-prefixed entries (_source/ and friends) are library-internal, never
    assets and never providers. A duplicated asset id raises rather than
    silently resolving to one of them: during the layout migration a duplicate
    means an interrupted move, and picking a winner would hide it.
    """
    root = _safe_asset_path(library_dir, library_dir)
    if not root.is_dir():
        return
    found = {}

    def claim(path):
        if path.name in found:
            raise ValueError(
                "asset %s exists twice in library: %s and %s" % (path.name, found[path.name], path)
            )
        found[path.name] = path

    for entry in sorted(root.iterdir()):
        entry = _safe_asset_path(root, entry)
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        if _is_asset_name(entry.name):
            claim(entry)
            continue
        for sub in sorted(entry.iterdir()):
            sub = _safe_asset_path(root, sub)
            # No name test under a provider dir: depth already says "asset",
            # and RoboTwin ships natives that are not `<digits>_<category>`
            # at all (cube, vis_box, sapien-block1/2, objaverse). The name
            # test is only needed at the root, where the flat legacy layout
            # makes provider dirs and asset dirs siblings.
            if sub.is_dir() and not sub.name.startswith("_"):
                claim(sub)
    for name in sorted(found):
        yield found[name]


def ledger_path(library_dir, asset):
    """`asset`'s ledger.json, in whichever layout the library uses.

    Falls back to the flat path when the asset does not exist yet, so callers
    that are about to CREATE an asset still get a writable target.
    """
    asset = canonical_asset_key(asset)
    root = _safe_asset_path(library_dir, library_dir)
    found = asset_dir(root, asset)
    asset_path = found or _safe_asset_path(root, root / asset)
    return _safe_asset_path(root, asset_path / "ledger.json")


def ensure_asset_directory(library_dir, asset):
    """Return an existing asset directory or safely create its flat layout."""

    asset = canonical_asset_key(asset)
    root = _safe_asset_path(library_dir, library_dir)
    if not root.is_dir():
        raise UnsafeAssetPathError(f"library root is not a directory: {root}")
    found = asset_dir(root, asset)
    if found is not None:
        return found
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    with _open_parent_directory(root / asset) as (root_fd, name):
        try:
            os.mkdir(name, 0o755, dir_fd=root_fd)
        except FileExistsError:
            pass
        asset_fd = os.open(name, flags, dir_fd=root_fd)
        os.close(asset_fd)
        os.fsync(root_fd)
    created = asset_dir(root, asset)
    if created is None:
        raise UnsafeAssetPathError(f"asset directory disappeared during creation: {asset}")
    return created


@dataclass(frozen=True)
class LoadedAssetLedger:
    asset_key: str
    asset_dir: Path
    ledger_path: Path
    document: dict


def load_asset_ledger(library_dir, asset):
    """Load one flat/grouped ledger and prove its location-bound identity."""

    asset = canonical_asset_key(asset)
    found = asset_dir(library_dir, asset)
    if found is None:
        raise FileNotFoundError(f"asset {asset!r} is absent from {library_dir}")
    path = _safe_asset_path(_absolute_local_path(library_dir), found / "ledger.json")
    if not path.is_file():
        raise FileNotFoundError(f"asset {asset!r} has no ledger.json")
    with _open_parent_directory(path) as (directory_fd, name):
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        with os.fdopen(os.open(name, flags, dir_fd=directory_fd), "r") as stream:
            document = json.load(stream)
    env_gen = _get(document, "external_ids.env_gen")
    if env_gen != asset:
        raise LedgerIdentityError(
            f"external_ids.env_gen {env_gen!r} does not match asset key {asset!r}"
        )
    return LoadedAssetLedger(asset, found, path, document)


def reps_digest(model_entry, backend):
    """Hash the complete loader-visible representation set for ``backend``.

    The digest covers every non-snapshot representation as canonical JSON,
    including files[] closure hashes and loader-affecting frame, geometry, and
    collision metadata. Representation list order is not semantic, so the
    canonical objects are sorted before hashing. The explicit version/backend
    domain prevents old top-level-sha receipts or another backend's empty set
    from being accepted under this contract. URI locators are intentionally
    part of execution identity: relocating an otherwise identical file makes a
    receipt stale. Cross-machine stability therefore needs a future logical
    URI/CAS locator contract; this digest must not pretend absolute paths are
    portable in the meantime.
    """
    representations = sorted(
        json.dumps(
            representation,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for representation in model_entry.get("representations", [])
        if isinstance(representation, dict)
        and representation.get("backend") == backend
        and representation.get("role") != "snapshot"
    )
    if not representations:
        raise RepresentationDigestError(
            f"backend {backend!r} has no loader-visible representation; refusing empty digest"
        )
    payload = json.dumps(
        {
            "backend": backend,
            "digest_version": REPS_DIGEST_VERSION,
            "representations": representations,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_measurement_provenance(provenance, prefix, out):
    """Validate the minimum replay handle for a measured claim.

    A backend name alone does not identify the measuring stack invocation;
    stable poses therefore require both the backend and a non-empty run id.
    """
    if not isinstance(provenance, dict):
        out.append(
            Violation(
                prefix,
                "measured_against_required",
                "measured data must record measured_against with backend and run_id",
            )
        )
        return
    backend = provenance.get("backend")
    if backend not in BACKENDS:
        out.append(
            Violation(
                f"{prefix}.backend",
                "bad_enum" if backend is not None else "measured_against_required",
                f"backend {backend!r} not in {BACKENDS}",
            )
        )
    run_id = provenance.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        out.append(
            Violation(
                f"{prefix}.run_id",
                "measured_against_required",
                "measured_against.run_id must be a non-empty string",
            )
        )


def _validate_stable_poses(poses, prefix, out):
    if not poses:
        out.append(Violation(prefix, "no_stable_pose", "stable_poses is empty"))
        return
    if not isinstance(poses, list):
        out.append(Violation(prefix, "bad_type", "stable_poses must be a list"))
        return
    n_default = sum(1 for p in poses if isinstance(p, dict) and p.get("is_default"))
    if n_default != 1:
        out.append(
            Violation(
                prefix,
                "multiple_default_poses",
                f"expected exactly 1 is_default pose, got {n_default}",
            )
        )
    for i, pose in enumerate(poses):
        pose_prefix = f"{prefix}.{i}"
        if not isinstance(pose, dict):
            out.append(Violation(pose_prefix, "bad_type", "stable pose is not a dict"))
            continue
        _validate_measurement_provenance(
            pose.get("measured_against"), f"{pose_prefix}.measured_against", out
        )
        wxyz = pose.get("orientation_wxyz")
        if (
            not isinstance(wxyz, (list, tuple))
            or len(wxyz) != 4
            or not all(
                isinstance(component, (int, float)) and not isinstance(component, bool)
                for component in wxyz
            )
        ):
            out.append(
                Violation(
                    f"{pose_prefix}.orientation_wxyz",
                    "bad_quaternion",
                    "not a length-4 quaternion",
                )
            )
            continue
        norm = math.sqrt(sum(c * c for c in wxyz))
        if abs(norm - 1.0) > 1e-6:
            out.append(
                Violation(
                    f"{pose_prefix}.orientation_wxyz",
                    "bad_quaternion",
                    f"orientation_wxyz is not a unit quaternion (norm={norm})",
                )
            )


def _validate_measured_block(block, prefix, value_keys, out):
    """v3 measured-data blocks (appearance / physical.placement).

    The one hard rule, learned the expensive way: a measured number is a
    property of (asset x measuring stack), not of the asset -- the same mesh
    rested 9 mm lower after a loader change, and the same library once held
    two mutually exclusive stable-pose semantics with no field to tell them
    apart. So any measured block MUST say what it was measured against."""
    if not isinstance(block, dict):
        out.append(Violation(prefix, "unknown_shape", f"{prefix} is not a dict"))
        return
    ma = block.get("measured_against")
    if not isinstance(ma, dict) or not ma.get("backend"):
        out.append(
            Violation(
                f"{prefix}.measured_against",
                "measured_against_required",
                "measured data must record the stack it was measured against (at minimum: backend)",
            )
        )
    elif ma.get("backend") not in BACKENDS:
        out.append(
            Violation(
                f"{prefix}.measured_against.backend",
                "bad_enum",
                f"backend {ma.get('backend')!r} not in {BACKENDS}",
            )
        )
    for k in value_keys:
        if k not in block:
            out.append(
                Violation(f"{prefix}.{k}", "missing", f"required field missing: {prefix}.{k}")
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
        out.append(Violation(f"{prefix}.value", "unknown_shape", "status=known but value is None"))
    elif status == "estimated" and not block.get("estimator"):
        out.append(
            Violation(
                f"{prefix}.estimator",
                "estimator_required",
                "status=estimated requires an estimator",
            )
        )
    # v3: the runtime_default pair is gone. The engine-default constant lives
    # with the engine (runtime_config / the compiler), not copied into every
    # ledger -- 86/87 v2 ledgers carried the same global 0.1 and nothing in
    # the live tree ever read it back.
    # v3: friction may carry a static/dynamic split (industry-minimal set:
    # UsdPhysics / KHR_physics / SDFormat all model two coefficients). One
    # envelope, two optional values; a lone legacy `value` remains legal and
    # is read as "both, unsplit".
    if prefix.endswith(".friction"):
        for k in ("static", "dynamic"):
            v = block.get(k)
            if v is not None and not isinstance(v, (int, float)):
                out.append(
                    Violation(
                        f"{prefix}.{k}",
                        "unknown_shape",
                        f"friction.{k} must be a number or null",
                    )
                )


def _is_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value.lower())
    )


def _validate_representation_files(representation, prefix, out):
    files = representation.get("files", _MISSING)
    if files is _MISSING or files == []:
        out.append(
            Violation(
                f"{prefix}.files",
                "missing",
                f"required field missing or empty: {prefix}.files",
            )
        )
        return
    if not isinstance(files, list):
        out.append(Violation(f"{prefix}.files", "bad_type", "representation files must be a list"))
        return

    primary = (representation.get("uri"), representation.get("sha256"))
    primary_found = False
    seen_uris = set()
    valid_uris = []
    for j, member in enumerate(files):
        member_prefix = f"{prefix}.files.{j}"
        if not isinstance(member, dict):
            out.append(Violation(member_prefix, "bad_type", "file member must be a dict"))
            continue
        extra_fields = sorted(set(member) - {"uri", "sha256", "bytes"})
        if extra_fields:
            out.append(
                Violation(
                    member_prefix,
                    "unexpected_field",
                    f"file member has unsupported fields: {extra_fields}",
                )
            )
        uri = member.get("uri")
        if not isinstance(uri, str) or not uri.strip():
            out.append(
                Violation(
                    f"{member_prefix}.uri",
                    "bad_type",
                    "file member uri must be a non-empty string",
                )
            )
        else:
            valid_uris.append(uri)
            if uri in seen_uris:
                out.append(
                    Violation(
                        f"{member_prefix}.uri",
                        "duplicate_file_uri",
                        f"files uri must be unique: {uri}",
                    )
                )
            seen_uris.add(uri)
        sha = member.get("sha256")
        if not _is_sha256(sha):
            out.append(
                Violation(
                    f"{member_prefix}.sha256",
                    "bad_sha256",
                    "sha256 is not 64 hex chars",
                )
            )
        size = member.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            out.append(
                Violation(
                    f"{member_prefix}.bytes",
                    "bad_type",
                    "file member bytes must be a non-negative integer",
                )
            )
        if (uri, sha) == primary:
            primary_found = True

    if valid_uris != sorted(valid_uris):
        out.append(
            Violation(
                f"{prefix}.files",
                "files_not_sorted",
                "files members must be strictly sorted by uri",
            )
        )

    if not primary_found:
        out.append(
            Violation(
                f"{prefix}.files",
                "primary_file_missing",
                "representation uri/sha256 must identify one files member",
            )
        )


_CLOSURE_TEXT_MAX_BYTES = 16 * 1024 * 1024
_CLOSURE_LEAF_FORMATS = frozenset(
    {
        "bin",
        "bmp",
        "exr",
        "gif",
        "jpeg",
        "jpg",
        "npy",
        "ply",
        "png",
        "stl",
        "tga",
        "webp",
    }
)


class _ClosureInspectionError(ValueError):
    pass


@dataclass(frozen=True)
class _ClosureReference:
    uri: str
    expected_bytes: int | None = None


def _absolute_local_path(path):
    try:
        raw_path = os.fspath(path)
        if not isinstance(raw_path, str) or any(ord(char) < 0x20 for char in raw_path):
            raise ValueError("path contains control characters")
        return Path(os.path.abspath(raw_path))
    except (OSError, TypeError, ValueError) as exc:
        raise _ClosureInspectionError("local file path cannot be inspected safely") from exc


def _symlink_component(path):
    absolute = _absolute_local_path(path)
    for component in (absolute, *absolute.parents):
        try:
            if component.is_symlink():
                return component
        except (OSError, ValueError) as exc:
            raise _ClosureInspectionError("local file path cannot be inspected safely") from exc
    return None


def _closure_text(path):
    try:
        return _closure_payload(path).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _ClosureInspectionError("dependency-bearing document is not UTF-8 text") from exc


def _closure_payload(path):
    with path.open("rb") as stream:
        payload = stream.read(_CLOSURE_TEXT_MAX_BYTES + 1)
    if len(payload) > _CLOSURE_TEXT_MAX_BYTES:
        raise _ClosureInspectionError(
            f"dependency-bearing document exceeds {_CLOSURE_TEXT_MAX_BYTES} bytes"
        )
    return payload


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise _ClosureInspectionError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_json_constant(value):
    raise _ClosureInspectionError(f"non-finite JSON number: {value}")


def _closure_json(text):
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise _ClosureInspectionError("JSON document is malformed") from exc
    except (RecursionError, OverflowError) as exc:
        raise _ClosureInspectionError("JSON document exceeds structural limits") from exc
    if not isinstance(value, dict):
        raise _ClosureInspectionError("JSON root must be an object")
    return value


def _gltf_closure_refs(document, *, allow_implicit_buffer):
    refs = []
    implicit_buffers = 0
    for collection_name in ("buffers", "images"):
        collection = document.get(collection_name, [])
        if not isinstance(collection, list):
            raise _ClosureInspectionError(f"glTF {collection_name} must be a list")
        for entry in collection:
            if not isinstance(entry, dict):
                raise _ClosureInspectionError(f"glTF {collection_name} member must be an object")
            if collection_name == "buffers":
                byte_length = entry.get("byteLength")
                if (
                    not isinstance(byte_length, int)
                    or isinstance(byte_length, bool)
                    or byte_length < 1
                ):
                    raise _ClosureInspectionError(
                        "glTF buffer.byteLength must be a positive integer"
                    )
            uri = entry.get("uri")
            if uri is not None:
                if not isinstance(uri, str) or not uri:
                    raise _ClosureInspectionError("glTF URI must be a non-empty string")
                if not uri.lower().startswith("data:"):
                    refs.append(
                        _ClosureReference(
                            uri,
                            entry["byteLength"] if collection_name == "buffers" else None,
                        )
                    )
                continue
            if collection_name == "buffers":
                implicit_buffers += 1
                if not allow_implicit_buffer or implicit_buffers > 1:
                    raise _ClosureInspectionError("glTF buffer has no bound payload")
            elif not isinstance(entry.get("bufferView"), int) or isinstance(
                entry.get("bufferView"), bool
            ):
                raise _ClosureInspectionError("glTF image has no URI or bufferView")
    return tuple(refs)


def _glb_closure_refs(path):
    payload = _closure_payload(path)
    if len(payload) < 20:
        raise _ClosureInspectionError("GLB is truncated")
    magic, version, declared_size = struct.unpack_from("<4sII", payload, 0)
    if magic != b"glTF" or version != 2 or declared_size != len(payload):
        raise _ClosureInspectionError("GLB header is invalid")
    offset = 12
    json_document = None
    binary_chunks = 0
    binary_chunk_size = None
    binary_chunk = None
    chunk_index = 0
    while offset < len(payload):
        if offset + 8 > len(payload):
            raise _ClosureInspectionError("GLB chunk header is truncated")
        chunk_size, chunk_type = struct.unpack_from("<II", payload, offset)
        if chunk_size % 4:
            raise _ClosureInspectionError("GLB chunk length is not 4-byte aligned")
        offset += 8
        end = offset + chunk_size
        if end > len(payload):
            raise _ClosureInspectionError("GLB chunk payload is truncated")
        chunk = payload[offset:end]
        offset = end
        if chunk_index == 0 and chunk_type != 0x4E4F534A:
            raise _ClosureInspectionError("GLB first chunk is not JSON")
        if chunk_type == 0x4E4F534A:
            if json_document is not None:
                raise _ClosureInspectionError("GLB has multiple JSON chunks")
            unpadded_json = chunk.rstrip(b" ")
            if unpadded_json != chunk.rstrip(b" \t\r\n\x00"):
                raise _ClosureInspectionError("GLB JSON chunk padding must use spaces")
            try:
                json_document = _closure_json(unpadded_json.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise _ClosureInspectionError("GLB JSON chunk is not UTF-8") from exc
        elif chunk_type == 0x004E4942:
            binary_chunks += 1
            binary_chunk_size = chunk_size
            binary_chunk = chunk
            if binary_chunks > 1:
                raise _ClosureInspectionError("GLB has multiple BIN chunks")
        else:
            raise _ClosureInspectionError(f"GLB has unsupported chunk type {chunk_type:#x}")
        chunk_index += 1
    references = _gltf_closure_refs(json_document, allow_implicit_buffer=binary_chunks == 1)
    if binary_chunks == 1:
        implicit_buffers = [
            entry for entry in json_document.get("buffers", []) if entry.get("uri") is None
        ]
        if len(implicit_buffers) != 1:
            raise _ClosureInspectionError("GLB BIN chunk has no unique implicit buffer")
        declared_size = implicit_buffers[0].get("byteLength")
        if not declared_size <= binary_chunk_size <= declared_size + 3:
            raise _ClosureInspectionError("GLB BIN chunk does not match buffer.byteLength")
        if any(binary_chunk[declared_size:]):
            raise _ClosureInspectionError("GLB BIN chunk padding must use zero bytes")
    return references


def _closure_xml(text):
    # ElementTree does not resolve external entities, but an internal entity
    # expansion can still consume disproportionate memory.  Asset descriptors
    # have no legitimate reason to carry a DTD, so reject it before parsing.
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise _ClosureInspectionError("XML DTD/entity declarations are unsupported")
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise _ClosureInspectionError("dependency-bearing XML is malformed") from exc


def _closure_refs(path, asset_format):
    """Return direct external references consumed by a supported descriptor.

    This is deliberately a closed parser set.  Treating an opaque container as
    dependency-free would recreate the exact false-positive the v3 files[]
    contract is meant to prevent.
    """
    asset_format = str(asset_format or "").lower().lstrip(".")
    if asset_format in _CLOSURE_LEAF_FORMATS:
        return ()
    if asset_format == "glb":
        return _glb_closure_refs(path)
    text = _closure_text(path)
    if asset_format == "urdf":
        root = _closure_xml(text)
        refs = []
        for element in root.iter():
            local = element.tag.rsplit("}", 1)[-1]
            if local in {"mesh", "texture", "include"}:
                filename = element.get("filename")
                if not isinstance(filename, str) or not filename.strip():
                    raise _ClosureInspectionError(f"URDF {local} must declare a non-empty filename")
                refs.append(filename)
        return tuple(refs)
    if asset_format == "obj":
        refs = []
        for line in text.splitlines():
            try:
                fields = shlex.split(line, comments=True, posix=True)
            except ValueError as exc:
                raise _ClosureInspectionError("OBJ contains invalid quoting") from exc
            if fields and fields[0].lower() == "mtllib":
                if len(fields) < 2:
                    raise _ClosureInspectionError("OBJ mtllib command has no path")
                refs.extend(fields[1:])
        return tuple(refs)
    if asset_format == "mtl":
        refs = []
        texture_commands = {
            "bump",
            "decal",
            "disp",
            "map_bump",
            "map_d",
            "map_ka",
            "map_kd",
            "map_ke",
            "map_ks",
            "map_ns",
            "norm",
            "refl",
        }
        for line in text.splitlines():
            try:
                fields = shlex.split(line, comments=True, posix=True)
            except ValueError as exc:
                raise _ClosureInspectionError("MTL contains invalid quoting") from exc
            if fields and fields[0].lower() in texture_commands:
                if len(fields) < 2:
                    raise _ClosureInspectionError("MTL texture command has no path")
                # Options precede the path; the final token is the referenced
                # filename for the production MTL dialects this writer emits.
                refs.append(fields[-1])
        return tuple(refs)
    if asset_format == "gltf":
        return _gltf_closure_refs(_closure_json(text), allow_implicit_buffer=False)
    if asset_format == "dae":
        root = _closure_xml(text)
        refs = []
        for element in root.iter():
            value = (element.text or "").strip()
            if (
                element.tag.rsplit("}", 1)[-1] == "init_from"
                and value
                and not value.startswith("#")
            ):
                refs.append(value)
            raw_url = element.get("url")
            if raw_url and not raw_url.startswith("#"):
                refs.append(raw_url.split("#", 1)[0])
        return tuple(refs)
    if asset_format in {"usd", "usda"}:
        if not text.lstrip().startswith("#usda"):
            raise _ClosureInspectionError("USD is not auditable USDA text")
        return tuple(match.group(1) for match in re.finditer(r"@([^@]+)@", text))
    if asset_format == "usdc":
        raise _ClosureInspectionError("binary USDC dependency closure requires a USD resolver")
    raise _ClosureInspectionError(f"unsupported dependency semantics for format={asset_format!r}")


def _safe_closure_target(source, reference):
    if not isinstance(reference, str) or not reference.strip():
        raise _ClosureInspectionError("dependency reference must be a non-empty string")
    if reference != reference.strip():
        raise _ClosureInspectionError(f"non-canonical dependency path is forbidden: {reference}")
    try:
        parsed = urlsplit(reference)
    except ValueError as exc:
        raise _ClosureInspectionError(
            f"dependency URI cannot be parsed safely: {reference}"
        ) from exc
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise _ClosureInspectionError(
            f"external or decorated dependency URI is forbidden: {reference}"
        )
    if "\\" in reference or "%" in reference or any(ord(char) < 0x20 for char in reference):
        raise _ClosureInspectionError(f"non-canonical dependency path is forbidden: {reference}")
    relative = PurePosixPath(parsed.path)
    if relative.is_absolute() or ".." in relative.parts:
        raise _ClosureInspectionError(f"dependency path escapes its asset tree: {reference}")
    if any(part in {"", "."} for part in parsed.path.split("/")):
        raise _ClosureInspectionError(f"non-canonical dependency path is forbidden: {reference}")
    candidate = _absolute_local_path(source.parent / Path(*relative.parts))
    try:
        symlink = _symlink_component(candidate)
    except _ClosureInspectionError as exc:
        raise _ClosureInspectionError(
            f"cannot inspect dependency path safely: {reference}"
        ) from exc
    if symlink is not None:
        raise _ClosureInspectionError(f"dependency path traverses a symlink: {reference}")
    return candidate


def _validate_representation_closure(representation, prefix, out):
    files = representation.get("files")
    if not isinstance(files, list):
        return
    declared = {}
    for member in files:
        if not isinstance(member, dict):
            continue
        uri = member.get("uri")
        if isinstance(uri, str) and uri:
            try:
                member_path = _absolute_local_path(resolve_uri(uri))
                if _symlink_component(member_path) is not None:
                    return
            except _ClosureInspectionError:
                return
            existing_uri = declared.get(member_path)
            if existing_uri is not None and existing_uri != uri:
                out.append(
                    Violation(
                        f"{prefix}.files",
                        "representation_file_path_alias",
                        "multiple files[] URIs resolve to the same local file: "
                        f"{existing_uri!r}, {uri!r}",
                    )
                )
                return
            declared[member_path] = uri
    primary_uri = representation.get("uri")
    if not isinstance(primary_uri, str) or not primary_uri:
        return
    primary = next((path for path, uri in declared.items() if uri == primary_uri), None)
    if primary is None:
        return

    pending = [(primary, representation.get("format"), ())]
    inspected = set()
    while pending:
        source, asset_format, ancestors = pending.pop()
        if source in ancestors:
            cycle_start = ancestors.index(source)
            cycle = (*ancestors[cycle_start:], source)
            out.append(
                Violation(
                    f"{prefix}.files",
                    "representation_file_dependency_cycle",
                    "loader dependency cycle: " + " -> ".join(path.name for path in cycle),
                )
            )
            continue
        if source in inspected:
            continue
        inspected.add(source)
        try:
            references = _closure_refs(source, asset_format)
        except (OSError, _ClosureInspectionError) as exc:
            out.append(
                Violation(
                    f"{prefix}.files",
                    "representation_file_closure_unverifiable",
                    "cannot prove dependency closure for "
                    f"{declared.get(source, source.name)}: {exc}",
                )
            )
            continue
        for reference_spec in references:
            if isinstance(reference_spec, _ClosureReference):
                reference = reference_spec.uri
                expected_bytes = reference_spec.expected_bytes
            else:
                reference = reference_spec
                expected_bytes = None
            try:
                dependency = _safe_closure_target(source, reference)
            except _ClosureInspectionError as exc:
                out.append(
                    Violation(
                        f"{prefix}.files",
                        "representation_file_closure_incomplete",
                        str(exc),
                    )
                )
                continue
            if dependency not in declared:
                out.append(
                    Violation(
                        f"{prefix}.files",
                        "representation_file_closure_incomplete",
                        f"loader dependency is not recorded in files[]: {reference}",
                    )
                )
                continue
            if expected_bytes is not None:
                try:
                    actual_bytes = dependency.stat().st_size
                except OSError as exc:
                    out.append(
                        Violation(
                            f"{prefix}.files",
                            "representation_file_closure_unverifiable",
                            f"cannot inspect dependency payload size for {reference}: {exc}",
                        )
                    )
                else:
                    if actual_bytes != expected_bytes:
                        out.append(
                            Violation(
                                f"{prefix}.files",
                                "representation_file_payload_size_mismatch",
                                f"{reference} declares {expected_bytes} bytes, "
                                f"file has {actual_bytes}",
                            )
                        )
            dependency_format = dependency.suffix.lower().lstrip(".")
            pending.append((dependency, dependency_format, (*ancestors, source)))

    extra = sorted(declared[path] for path in declared.keys() - inspected)
    if extra:
        out.append(
            Violation(
                f"{prefix}.files",
                "representation_file_closure_extra",
                "files[] contains members the loader cannot reach from the primary: "
                + ", ".join(extra),
            )
        )


def _validate_collision_meta(representation, prefix, out):
    role = representation.get("role")
    collision_meta = representation.get("collision_meta", _MISSING)
    if role not in ("collision", "visual_and_collision"):
        if collision_meta is not _MISSING:
            out.append(
                Violation(
                    f"{prefix}.collision_meta",
                    "collision_meta_forbidden",
                    f"role={role!r} must not carry collision_meta",
                )
            )
        return

    if collision_meta is _MISSING or collision_meta is None:
        out.append(
            Violation(
                f"{prefix}.collision_meta",
                "missing",
                "collision-bearing representation requires collision_meta",
            )
        )
        return
    if not isinstance(collision_meta, dict):
        out.append(
            Violation(
                f"{prefix}.collision_meta",
                "bad_type",
                "collision_meta must be a dict",
            )
        )
        return

    mode = collision_meta.get("mode")
    if mode is None:
        out.append(
            Violation(
                f"{prefix}.collision_meta.mode",
                "missing",
                "collision_meta.mode is required",
            )
        )
    elif not isinstance(mode, str) or not mode.strip():
        out.append(
            Violation(
                f"{prefix}.collision_meta.mode",
                "bad_type",
                "collision_meta.mode must be a non-empty string",
            )
        )
    elif mode not in COLLISION_MODES:
        out.append(
            Violation(
                f"{prefix}.collision_meta.mode",
                "bad_enum",
                f"collision_meta.mode {mode!r} not in {COLLISION_MODES}",
            )
        )

    convex = collision_meta.get("convex", _MISSING)
    if convex is not _MISSING and not isinstance(convex, bool):
        out.append(
            Violation(
                f"{prefix}.collision_meta.convex",
                "bad_type",
                "collision_meta.convex must be a bool",
            )
        )
    for field in ("approximation", "decomposer"):
        value = collision_meta.get(field, _MISSING)
        if value is not _MISSING and (not isinstance(value, str) or not value.strip()):
            out.append(
                Violation(
                    f"{prefix}.collision_meta.{field}",
                    "bad_type",
                    f"collision_meta.{field} must be a non-empty string",
                )
            )
    params = collision_meta.get("params", _MISSING)
    if params is not _MISSING and not isinstance(params, dict):
        out.append(
            Violation(
                f"{prefix}.collision_meta.params",
                "bad_type",
                "collision_meta.params must be a dict",
            )
        )
    hull_count = collision_meta.get("hull_count", _MISSING)
    if hull_count is not _MISSING and (
        not isinstance(hull_count, int) or isinstance(hull_count, bool) or hull_count < 0
    ):
        out.append(
            Violation(
                f"{prefix}.collision_meta.hull_count",
                "bad_type",
                "collision_meta.hull_count must be a non-negative integer",
            )
        )
    unknown_reason = collision_meta.get("unknown_reason", _MISSING)
    if mode == "unknown" and unknown_reason is _MISSING:
        out.append(
            Violation(
                f"{prefix}.collision_meta.unknown_reason",
                "missing",
                "collision_meta.mode=unknown requires unknown_reason",
            )
        )
    elif unknown_reason is not _MISSING and (
        not isinstance(unknown_reason, str) or not unknown_reason.strip()
    ):
        out.append(
            Violation(
                f"{prefix}.collision_meta.unknown_reason",
                "bad_type",
                "collision_meta.unknown_reason must be a non-empty string",
            )
        )


def _validate_representations(reps, prefix, out):
    if not isinstance(reps, list):
        out.append(Violation(prefix, "bad_type", "representations must be a list"))
        return
    has_sapien = any(
        isinstance(r, dict) and r.get("backend") == "sapien" and r.get("role") != "snapshot"
        for r in reps
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
        if not isinstance(r, dict):
            out.append(Violation(rp, "bad_type", "representation is not a dict"))
            continue
        # v3 deletes the top-level size_bytes scalar. Byte evidence belongs to
        # each files[] member so multi-file USD/URDF representations cannot
        # silently omit a payload while retaining one reassuring byte count.
        for field in ("format", "uri", "role", "sha256"):
            if field not in r or r[field] is None:
                out.append(
                    Violation(
                        f"{rp}.{field}",
                        "missing",
                        f"required field missing: {rp}.{field}",
                    )
                )
        for field in ("format", "uri"):
            value = r.get(field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                out.append(
                    Violation(
                        f"{rp}.{field}",
                        "bad_type",
                        f"representation {field} must be a non-empty string",
                    )
                )
        if "role" in r and r["role"] is not None and r["role"] not in ROLES:
            out.append(Violation(f"{rp}.role", "bad_enum", f"role {r['role']!r} not in {ROLES}"))
        if "backend" not in r or r.get("backend") is None:
            out.append(
                Violation(f"{rp}.backend", "missing", f"required field missing: {rp}.backend")
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
        if sha is not None and not _is_sha256(sha):
            out.append(Violation(f"{rp}.sha256", "bad_sha256", "sha256 is not 64 hex chars"))
        asset_format = r.get("format")
        uri = r.get("uri")
        if isinstance(asset_format, str) and isinstance(uri, str):
            try:
                parsed_uri = urlsplit(uri)
            except ValueError:
                out.append(
                    Violation(
                        f"{rp}.format",
                        "representation_format_mismatch",
                        f"format={asset_format!r} cannot be checked against malformed uri {uri!r}",
                    )
                )
            else:
                suffix = PurePosixPath(parsed_uri.path).suffix.lower().lstrip(".")
                compatible = asset_format.lower() == suffix or (
                    asset_format.lower() == "usd" and suffix in {"usd", "usda", "usdc"}
                )
                if not suffix or not compatible:
                    suffix_label = f".{suffix}" if suffix else "no suffix"
                    out.append(
                        Violation(
                            f"{rp}.format",
                            "representation_format_mismatch",
                            f"format={asset_format!r} does not match uri suffix {suffix_label}",
                        )
                    )
        if "size_bytes" in r:
            out.append(
                Violation(
                    f"{rp}.size_bytes",
                    "deleted_field",
                    "representations[].size_bytes was deleted in v3; use files[].bytes",
                )
            )
        _validate_representation_files(r, rp, out)
        _validate_collision_meta(r, rp, out)


def _validate_license(license_block, prefix, out):
    if not isinstance(license_block, dict):
        out.append(Violation(prefix, "license_not_structured", "license is not a dict"))
        return
    for field in ("spdx", "status", "terms_note"):
        if field not in license_block:
            out.append(
                Violation(prefix, "license_not_structured", f"license missing field: {field}")
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


def _validate_identity(identity, prefix, out):
    """semantics.identity: how this asset's category/aliases were decided.

    Without it the identity claim is unattributable -- `category: "cup"` reads
    the same whether a human read it off the source page, a retrieval run
    asserted it because "cup" was the query, or nobody knows. Those have very
    different failure modes: a `requested_by_acquire` identity is what we
    ASKED for, so a loose search gate silently yields an asset that is not
    actually a cup while grounding keeps selecting it as one."""
    if not isinstance(identity, dict):
        out.append(Violation(prefix, "bad_type", "semantics.identity is not a dict"))
        return
    basis = identity.get("basis")
    if basis not in IDENTITY_BASIS:
        out.append(
            Violation(
                f"{prefix}.basis",
                "bad_enum",
                f"identity basis {basis!r} not in {IDENTITY_BASIS}",
            )
        )
    verified = identity.get("verified")
    if not isinstance(verified, bool):
        out.append(
            Violation(
                f"{prefix}.verified",
                "bad_type",
                f"identity verified must be a bool, got {type(verified).__name__}",
            )
        )


def _validate_inertial(block, prefix, out):
    """physical.inertial = {com_m, inertia_diagonal_kgm2, [inertia_off_diagonal],
    status, [estimator], basis}. Centre of mass and inertia share ONE envelope
    because they share one provenance: both come off the URDF inertial block,
    or both are inferred by the engine from collision geometry -- there is no
    real case where one is measured and the other guessed."""
    if not isinstance(block, dict):
        out.append(Violation(prefix, "unknown_shape", "inertial is not a dict"))
        return
    status = block.get("status")
    if status not in MASS_STATUS:
        out.append(
            Violation(
                f"{prefix}.status",
                "bad_enum",
                f"status {status!r} not in {MASS_STATUS}",
            )
        )
    elif status == "known" and (
        block.get("com_m") is None and block.get("inertia_diagonal_kgm2") is None
    ):
        out.append(
            Violation(
                f"{prefix}.status",
                "unknown_shape",
                "status=known but neither com_m nor inertia_diagonal_kgm2 is set",
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
    basis = block.get("basis")
    if basis not in INERTIAL_BASIS:
        out.append(
            Violation(
                f"{prefix}.basis",
                "bad_enum",
                f"inertial basis {basis!r} not in {INERTIAL_BASIS}",
            )
        )
    for field, length in (("com_m", 3), ("inertia_diagonal_kgm2", 3)):
        value = block.get(field)
        if value is None:
            continue
        if not isinstance(value, (list, tuple)) or len(value) != length:
            out.append(
                Violation(
                    f"{prefix}.{field}",
                    "bad_type",
                    f"{field} must be null or a {length}-vector",
                )
            )


def _validate_source(source, prefix, out):
    """source.kind selects the branch; the branches are disjoint."""
    if not isinstance(source, dict):
        out.append(Violation(prefix, "unknown_shape", "source is not a dict"))
        return
    kind = source.get("kind")
    if kind not in SOURCE_KINDS:
        out.append(
            Violation(
                f"{prefix}.kind",
                "bad_source_kind",
                f"source kind {kind!r} not in {SOURCE_KINDS}",
            )
        )
        return  # branch tables are meaningless without a valid branch

    model_node = {"source": source}
    for path in REQUIRED_SOURCE[kind]:
        if _get(model_node, path) is _MISSING:
            out.append(
                Violation(
                    f"{prefix}.{path[len('source.') :]}",
                    "missing",
                    f"source.kind={kind} requires {path}",
                )
            )
    for path in NOT_NULLABLE_SOURCE[kind]:
        if _get(model_node, path) is None:
            out.append(
                Violation(
                    f"{prefix}.{path[len('source.') :]}",
                    "missing",
                    f"source.kind={kind} requires a non-null {path}",
                )
            )
    for path in FOREIGN_SOURCE_FIELDS[kind]:
        if _get(model_node, path) is not _MISSING:
            out.append(
                Violation(
                    f"{prefix}.{path[len('source.') :]}",
                    "source_field_mismatch",
                    f"source.kind={kind} must not carry {path} (it belongs to the other branch)",
                )
            )

    # Acquisition dates: same canonical YYYY-MM-DD rule on both branches, and
    # the same violation code -- retrieved_at and generated_at are the same
    # question ("when did this model enter our possession") asked of the two
    # ways a model can come into existence.
    for field in ("retrieved_at", "generator.generated_at"):
        value = _get(model_node, f"source.{field}")
        if value is _MISSING or value is None:
            continue
        if not _is_iso_date(value):
            out.append(
                Violation(
                    f"{prefix}.{field}",
                    "bad_timestamp",
                    f"{field} {value!r} is not a canonical ISO date (YYYY-MM-DD)",
                )
            )

    if kind == "generated":
        params = _get(model_node, "source.generator.params")
        if params is not _MISSING and not isinstance(params, dict):
            out.append(
                Violation(
                    f"{prefix}.generator.params",
                    "bad_type",
                    "generator.params must be a dict (frozen verbatim, "
                    "never interpreted by the validator)",
                )
            )
        inp = _get(model_node, "source.generator.input")
        if isinstance(inp, dict) and inp.get("type") not in ("text", "image", "video"):
            out.append(
                Violation(
                    f"{prefix}.generator.input.type",
                    "bad_enum",
                    f"input type {inp.get('type')!r} not in ('text', 'image', 'video')",
                )
            )


def _verification_entry_is_well_formed(entry):
    if not isinstance(entry, dict):
        return False
    report_path = entry.get("report_path", _MISSING)
    return (
        entry.get("backend") in BACKENDS
        and entry.get("check") in CHECKS
        and entry.get("verdict") in VERDICTS
        and isinstance(entry.get("run_id"), str)
        and bool(entry["run_id"].strip())
        and _is_iso_datetime(entry.get("timestamp"))
        and _is_sha256(entry.get("verified_digest"))
        and (
            report_path is _MISSING or (isinstance(report_path, str) and bool(report_path.strip()))
        )
    )


def _verification_conflict_indices(verifications):
    """Return receipt indices that reuse one producer identity with another fact."""

    seen = {}
    conflicts = set()
    for index, entry in enumerate(verifications):
        if not isinstance(entry, dict):
            continue
        identity = tuple(
            entry.get(field) for field in ("backend", "check", "run_id", "verified_digest")
        )
        try:
            hash(identity)
            canonical = json.dumps(
                entry,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            continue
        previous = seen.get(identity)
        if previous is None:
            seen[identity] = (index, canonical)
        elif previous[1] != canonical:
            conflicts.update((previous[0], index))
    return conflicts


def _validate_verification(verifications, prefix, out):
    # report_path was required through v2 and is optional in v3: no reader
    # anywhere checks the file exists or opens it (gen_fragment /
    # latest_verification / usd_enrich consume backend/check/verdict/
    # timestamp/verified_digest only). It stays legal as non-empty capture,
    # but this validator does not claim that the external report is authentic.
    if not isinstance(verifications, list):
        out.append(Violation(prefix, "bad_type", "verification must be a list"))
        return
    for index in sorted(_verification_conflict_indices(verifications)):
        out.append(
            Violation(
                f"{prefix}.{index}",
                "verification_conflict",
                "verification producer identity is reused for different canonical facts",
            )
        )
    required = (
        "backend",
        "check",
        "verdict",
        "run_id",
        "timestamp",
        "verified_digest",
    )
    for i, v in enumerate(verifications):
        vp = f"{prefix}.{i}"
        if not isinstance(v, dict):
            out.append(Violation(vp, "bad_type", "verification entry must be a dict"))
            continue
        for field in required:
            if field not in v or v[field] is None:
                out.append(
                    Violation(
                        f"{vp}.{field}",
                        "missing",
                        f"required field missing: {vp}.{field}",
                    )
                )
        if "backend" in v and v["backend"] is not None and v["backend"] not in BACKENDS:
            out.append(
                Violation(
                    f"{vp}.backend",
                    "bad_enum",
                    f"backend {v['backend']!r} not in {BACKENDS}",
                )
            )
        if "check" in v and v["check"] is not None and v["check"] not in CHECKS:
            out.append(
                Violation(f"{vp}.check", "bad_enum", f"check {v['check']!r} not in {CHECKS}")
            )
        if "verdict" in v and v["verdict"] is not None and v["verdict"] not in VERDICTS:
            out.append(
                Violation(
                    f"{vp}.verdict",
                    "bad_enum",
                    f"verdict {v['verdict']!r} not in {VERDICTS}",
                )
            )
        if "timestamp" in v and v["timestamp"] is not None and not _is_iso_datetime(v["timestamp"]):
            out.append(
                Violation(
                    f"{vp}.timestamp",
                    "bad_timestamp",
                    f"timestamp {v['timestamp']!r} is not a valid ISO date/datetime",
                )
            )
        run_id = v.get("run_id")
        if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()):
            out.append(
                Violation(
                    f"{vp}.run_id",
                    "bad_type",
                    "verification run_id must be a non-empty string",
                )
            )
        verified_digest = v.get("verified_digest")
        if verified_digest is not None and not _is_sha256(verified_digest):
            out.append(
                Violation(
                    f"{vp}.verified_digest",
                    "bad_sha256",
                    "verified_digest is not 64 hex chars",
                )
            )
        report_path = v.get("report_path", _MISSING)
        if report_path is not _MISSING and (
            not isinstance(report_path, str) or not report_path.strip()
        ):
            out.append(
                Violation(
                    f"{vp}.report_path",
                    "bad_type",
                    "report_path must be a non-empty string when present",
                )
            )
        evidence = v.get("evidence", _MISSING)
        if evidence is not _MISSING and not _evidence_record_is_well_formed(evidence):
            out.append(
                Violation(
                    f"{vp}.evidence",
                    "bad_evidence_record",
                    "evidence must be the exact immutable artifact record",
                )
            )


def _validate_model(model, prefix, out, profile=None):
    if not isinstance(model, dict):
        out.append(Violation(prefix, "bad_type", "model entry is not a dict"))
        return
    _check_required(model, REQUIRED_MODEL, prefix, out)
    _reject_deleted_fields(model, DELETED_MODEL_FIELDS, prefix, out)
    model_id = model.get("model_id", _MISSING)
    if model_id is not _MISSING:
        try:
            canonical_model_id(model_id)
        except InvalidModelIdError as exc:
            out.append(Violation(f"{prefix}.model_id", "bad_model_id", str(exc)))
    for envelope_name in ("mass_kg", "friction"):
        envelope = _get(model, f"physical.{envelope_name}")
        if not isinstance(envelope, dict):
            continue
        for field in envelope:
            if field.startswith("runtime_default"):
                path = f"{prefix}.physical.{envelope_name}.{field}"
                out.append(
                    Violation(
                        path,
                        "deleted_field",
                        f"field was deleted from {SCHEMA_VERSION}: {path}",
                    )
                )

    # Profile-conditional requirements. An unknown/absent profile adds none:
    # validate_ledger has already reported it as bad_profile, and piling
    # requirement failures on top of that would just bury the real cause.
    for p in PROFILE_REQUIRED_MODEL.get(profile, ()):
        if _get(model, p) is _MISSING:
            full = f"{prefix}.{p}"
            out.append(
                Violation(
                    full,
                    "profile_requirement_unmet",
                    f"profile={profile} requires {full} "
                    f"(the value may be a structured unknown; the key may not be absent)",
                )
            )

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
            _validate_stable_poses(poses, f"{prefix}.physical.conventions.stable_poses", out)

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

    inertial = _get(model, "physical.inertial")
    if inertial is not _MISSING:
        _validate_inertial(inertial, f"{prefix}.physical.inertial", out)

    # v3 optional blocks. Optional means the KEY may be absent; a present
    # block still has to be well-formed -- a malformed measurement record is
    # worse than none, because readers trust it.
    restitution = _get(model, "physical.restitution")
    if restitution is not _MISSING and restitution is not None:
        if isinstance(restitution, dict):
            _validate_mass_or_friction(restitution, f"{prefix}.physical.restitution", out)
        else:
            out.append(
                Violation(
                    f"{prefix}.physical.restitution",
                    "unknown_shape",
                    "restitution is not a dict",
                )
            )

    appearance = model.get("appearance")
    if appearance is not None:
        _validate_measured_block(
            appearance,
            f"{prefix}.appearance",
            value_keys=("colors_measured",),
            out=out,
        )

    placement = _get(model, "physical.placement")
    if placement is not _MISSING and placement is not None:
        _validate_measured_block(
            placement,
            f"{prefix}.physical.placement",
            value_keys=(),
            out=out,
        )

    _check_size_invariant(model, prefix, out)

    # extras is the deliberate escape hatch: structured data an upstream
    # producer already emits (affordance masks, Young's modulus, whatever the
    # generator attaches) that we have no reader for yet. Typed but never
    # interpreted, and it can never influence usable/missing -- so it costs
    # nothing to keep and loses nothing that a later field promotion would
    # need. Borrowed wholesale from glTF's `extras` plus its round-trip rule:
    # what you don't understand, you preserve rather than drop.
    extras = model.get("extras")
    if extras is not None and not isinstance(extras, dict):
        out.append(
            Violation(
                f"{prefix}.extras",
                "bad_type",
                f"extras must be a dict, got {type(extras).__name__}",
            )
        )

    license_block = _get(model, "source.license")
    if license_block is not _MISSING:
        _validate_license(license_block, f"{prefix}.source.license", out)

    source = model.get("source")
    if source is not None:
        _validate_source(source, f"{prefix}.source", out)

    verifications = model.get("verification")
    if verifications is not None:
        _validate_verification(verifications, f"{prefix}.verification", out)


def _check_size_invariant(model, prefix, out):
    """max(mesh_bbox_m) == actual_max_dim_m * scale, to 1e-3 relative.

    mesh_bbox_m is MEASURED off the converted mesh; actual_max_dim_m is the
    PRE-scale dimension the sizing decision was taken against. Keeping both is
    redundant only in the sense that a checksum is redundant: when they stop
    agreeing, a converter's unit assumption has silently changed -- the exact
    failure that is invisible to the eye and catastrophic downstream (an
    asset 100x too large still validates field-by-field).

    v2 pins actual_max_dim_m to the pre-scale reading, which is what
    conventions.resolve_size has always produced. backfill_upstream wrote the
    post-scale reading instead (its own max(mesh_bbox_m)), which is why 12 of
    39 models on disk failed this identity before migration: same field name,
    two meanings, two writers. That is the ambiguity this check exists to
    prevent from recurring."""
    bbox = _get(model, "physical.mesh_bbox_m")
    sizing = _get(model, "physical.size_resolution")
    if not isinstance(bbox, (list, tuple)) or not bbox or not isinstance(sizing, dict):
        return
    actual = sizing.get("actual_max_dim_m")
    scale = sizing.get("scale")
    if not isinstance(actual, (int, float)) or not isinstance(scale, (int, float)):
        return
    if not all(isinstance(e, (int, float)) for e in bbox):
        return
    expected = actual * scale
    measured = max(bbox)
    if abs(measured - expected) > 1e-3 * max(abs(expected), 1e-9):
        out.append(
            Violation(
                f"{prefix}.physical.size_resolution.actual_max_dim_m",
                "size_invariant_mismatch",
                f"max(mesh_bbox_m)={measured!r} != actual_max_dim_m*scale="
                f"{expected!r} (actual_max_dim_m is the PRE-scale dimension)",
            )
        )


def validate_ledger(ledger, *, check_files=True):
    out = []

    # schema_version is checked first and short-circuits: a document that
    # isn't declared (or doesn't match) v1 shouldn't get flooded with
    # unrelated v1-shape violations below.
    schema_version = ledger.get("schema_version", _MISSING)
    if schema_version is _MISSING:
        out.append(Violation("schema_version", "needs_backfill", "schema_version is missing"))
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

    _reject_deleted_fields(ledger, DELETED_ASSET_FIELDS, "", out)
    _check_required(ledger, REQUIRED_ASSET, "", out)

    asset_id = ledger.get("asset_id", _MISSING)
    if asset_id is not _MISSING:
        _validate_portable_identifier(asset_id, "asset_id", out)
    env_gen_id = _get(ledger, "external_ids.env_gen")
    if env_gen_id is not _MISSING:
        _validate_portable_identifier(env_gen_id, "external_ids.env_gen", out)

    # present-but-null is invisible to the presence-only check above (the key
    # does exist); a null value on any of these is just as unusable as an
    # absent key, so it's reported the same way.
    for field in NOT_NULLABLE_ASSET:
        if field in ledger and ledger[field] is None:
            out.append(Violation(field, "missing", f"required field is null: {field}"))

    kind = ledger.get("kind")
    if kind is not None and "kind" in ledger and kind not in KINDS:
        out.append(Violation("kind", "bad_enum", f"kind {kind!r} not in {KINDS}"))

    profile = ledger.get("profile")
    if profile is not None and "profile" in ledger and profile not in PROFILES:
        out.append(Violation("profile", "bad_profile", f"profile {profile!r} not in {PROFILES}"))
        profile = None  # don't let an invalid profile select a requirement table

    identity = _get(ledger, "semantics.identity")
    if identity is not _MISSING:
        _validate_identity(identity, "semantics.identity", out)

    aliases = _get(ledger, "semantics.aliases")
    if aliases is not None and aliases != _MISSING and aliases == []:
        out.append(Violation("semantics.aliases", "empty_aliases", "semantics.aliases is empty"))

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
                if not isinstance(m, dict):
                    _validate_model(m, f"models.{i}", out, profile=profile)
                    continue
                mid = m.get("model_id")
                try:
                    mid = canonical_model_id(mid)
                except InvalidModelIdError:
                    pass
                else:
                    seen_ids.setdefault(mid, []).append(i)
                _validate_model(m, f"models.{i}", out, profile=profile)
                # cross_backend's other half: declaring the intent to migrate
                # and owning no target-backend representation is a debt, and
                # this is where it becomes visible. Under sapien_only the same
                # absence is simply correct, which is the whole point of
                # asking the asset to declare what it is for.
                if profile == "cross_backend":
                    reps = m.get("representations") or []
                    if not isinstance(reps, list) or not any(
                        isinstance(r, dict)
                        and r.get("backend") == "isaacsim"
                        and r.get("role") != "snapshot"
                        for r in reps
                    ):
                        out.append(
                            Violation(
                                f"models.{i}.representations",
                                "profile_requirement_unmet",
                                "profile=cross_backend requires a non-snapshot "
                                "isaacsim representation",
                            )
                        )
                if kind == "articulated":
                    articulation = m.get("articulation")
                    if not isinstance(articulation, dict) or "joint_names" not in articulation:
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
            if not isinstance(m, dict):
                continue
            representations = m.get("representations")
            if not isinstance(representations, list):
                continue
            for j, r in enumerate(representations):
                if not isinstance(r, dict):
                    continue
                rp = f"models.{i}.representations.{j}"
                files = r.get("files")
                if isinstance(files, list):
                    for k, member in enumerate(files):
                        if not isinstance(member, dict):
                            continue
                        uri = member.get("uri")
                        if not isinstance(uri, str) or not uri:
                            continue
                        member_prefix = f"{rp}.files.{k}"
                        try:
                            p = _absolute_local_path(resolve_uri(uri))
                            symlink = _symlink_component(p)
                        except _ClosureInspectionError as exc:
                            out.append(
                                Violation(
                                    f"{member_prefix}.uri",
                                    "representation_file_path_unverifiable",
                                    f"cannot inspect file path safely: {uri!r}: {exc}",
                                )
                            )
                            continue
                        if symlink is not None:
                            out.append(
                                Violation(
                                    f"{member_prefix}.uri",
                                    "representation_file_symlink_forbidden",
                                    f"file path traverses symlink {symlink}: {uri}",
                                )
                            )
                            continue
                        try:
                            if not p.is_file():
                                out.append(
                                    Violation(
                                        f"{member_prefix}.uri",
                                        "file_missing",
                                        f"file not found: {uri}",
                                    )
                                )
                                continue
                            expected_size = member.get("bytes")
                            actual_size = p.stat().st_size
                            if isinstance(expected_size, int) and expected_size != actual_size:
                                out.append(
                                    Violation(
                                        f"{member_prefix}.bytes",
                                        "bytes_mismatch",
                                        f"byte count mismatch for {uri}: expected "
                                        f"{expected_size}, got {actual_size}",
                                    )
                                )
                            expected_sha = member.get("sha256")
                            if _is_sha256(expected_sha):
                                actual_sha = _sha256_file(p)
                                if actual_sha != expected_sha:
                                    out.append(
                                        Violation(
                                            f"{member_prefix}.sha256",
                                            "sha256_mismatch",
                                            f"sha256 mismatch for {uri}: expected "
                                            f"{expected_sha}, got {actual_sha}",
                                        )
                                    )
                        except OSError as exc:
                            out.append(
                                Violation(
                                    f"{member_prefix}.uri",
                                    "representation_file_unverifiable",
                                    f"cannot read representation file safely: {uri}: {exc}",
                                )
                            )
                _validate_representation_closure(r, rp, out)

    return out


def derive_usable(ledger, model_id):
    """Existence-only check for one model (structural checks 3/4/6/7 in the
    validator step numbering): required fields present, stable pose present,
    a sapien representation present, and (if articulated) articulation
    present. Returns (ok, missing_paths)."""
    models = ledger.get("models", [])
    if not isinstance(models, list):
        return False, ["models"]
    model = next(
        (m for m in models if isinstance(m, dict) and m.get("model_id") == model_id),
        None,
    )
    if model is None:
        return False, [f"models[model_id={model_id}]"]

    missing = []
    prefix = f"models[model_id={model_id}]"
    profile = ledger.get("profile")
    env_gen_id = _get(ledger, "external_ids.env_gen")
    if not isinstance(env_gen_id, str) or not env_gen_id.strip():
        missing.append("external_ids.env_gen")
    for p in REQUIRED_MODEL:
        if _get(model, p) is _MISSING:
            missing.append(f"{prefix}.{p}")

    # Branch and profile requirements count toward usable exactly as the
    # unconditional ones do -- "usable" means "meets the contract this asset
    # declared", not "meets some fixed subset of it".
    source_kind = _get(model, "source.kind")
    for p in REQUIRED_SOURCE.get(source_kind, ()):
        if _get(model, p) is _MISSING:
            missing.append(f"{prefix}.{p}")
    for p in PROFILE_REQUIRED_MODEL.get(profile, ()):
        if _get(model, p) is _MISSING:
            missing.append(f"{prefix}.{p}")

    poses = _get(model, "physical.conventions.stable_poses")
    if poses is _MISSING or not poses:
        missing.append(f"{prefix}.physical.conventions.stable_poses")
    elif isinstance(poses, list):
        for pose_index, pose in enumerate(poses):
            provenance = pose.get("measured_against") if isinstance(pose, dict) else None
            if not (
                isinstance(provenance, dict)
                and provenance.get("backend") in BACKENDS
                and isinstance(provenance.get("run_id"), str)
                and provenance["run_id"].strip()
            ):
                missing.append(
                    f"{prefix}.physical.conventions.stable_poses.{pose_index}.measured_against"
                )
    else:
        missing.append(f"{prefix}.physical.conventions.stable_poses")

    reps = model.get("representations", [])
    if not isinstance(reps, list):
        reps = []
    for rep_index, representation in enumerate(reps):
        if not isinstance(representation, dict):
            missing.append(f"{prefix}.representations.{rep_index}")
            continue
        files = representation.get("files")
        if not isinstance(files, list) or not files:
            missing.append(f"{prefix}.representations.{rep_index}.files")
        if representation.get("role") in ("collision", "visual_and_collision"):
            collision_meta = representation.get("collision_meta")
            collision_mode = (
                collision_meta.get("mode") if isinstance(collision_meta, dict) else None
            )
            if not (
                isinstance(collision_meta, dict)
                and collision_mode in COLLISION_MODES
                and (
                    collision_mode != "unknown"
                    or (
                        isinstance(collision_meta.get("unknown_reason"), str)
                        and collision_meta["unknown_reason"].strip()
                    )
                )
            ):
                missing.append(f"{prefix}.representations.{rep_index}.collision_meta")
    if not any(
        isinstance(r, dict) and r.get("backend") == "sapien" and r.get("role") != "snapshot"
        for r in reps
    ):
        missing.append(f"{prefix}.representations[backend=sapien]")
    if profile == "cross_backend" and not any(
        isinstance(r, dict) and r.get("backend") == "isaacsim" and r.get("role") != "snapshot"
        for r in reps
    ):
        missing.append(f"{prefix}.representations[backend=isaacsim]")

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
    size_resolution,
    conventions,
    source,
    verification,
    mesh_up_axis=None,
    origin_convention=None,
    articulation=None,
    mass_override=None,
    friction_override=None,
    inertial=None,
    appearance=None,
    placement=None,
    restitution=None,
    extras=None,
):
    """Assemble one models[] entry. mass/friction default to the
    conservative unknown shape unless an override is supplied.

    v3: mesh_up_axis / origin_convention are accepted for caller
    compatibility and DISCARDED (their facts live per-representation in
    frame/geometry_state now); the runtime_default pair on mass/friction is
    gone (the engine default lives with the engine). appearance / placement /
    restitution are the v3 measured blocks, optional at write time."""
    del mesh_up_axis, origin_convention
    mass_kg = dict(mass_override or {"value": None, "status": "unknown"})
    friction = dict(friction_override or {"value": None, "status": "unknown"})
    for envelope in (mass_kg, friction):
        for field in tuple(envelope):
            if field.startswith("runtime_default"):
                envelope.pop(field)
    physical = {
        "mesh_bbox_m": mesh_bbox_m,
        "size_resolution": size_resolution,
        "conventions": conventions,
        "mass_kg": mass_kg,
        "friction": friction,
    }
    if inertial is not None:
        physical["inertial"] = inertial
    if placement is not None:
        physical["placement"] = placement
    if restitution is not None:
        physical["restitution"] = restitution
    entry = {
        "model_id": model,
        "physical": physical,
        "representations": representations,
        "articulation": articulation or {},
        "source": source,
        "verification": verification,
    }
    if appearance is not None:
        entry["appearance"] = appearance
    if extras is not None:
        entry["extras"] = extras
    return entry


def unknown_inertial(basis="engine_derived"):
    """The honest default for a model whose mass distribution the engine will
    infer from collision geometry. Not a placeholder: it states positively
    that no asset-side measurement exists, which is what a transfer compiler
    needs to know when two backends disagree about how something topples."""
    return {
        "com_m": None,
        "inertia_diagonal_kgm2": None,
        "status": "unknown",
        "basis": basis,
    }


def upsert_model(
    ledger,
    *,
    asset,
    category,
    kind,
    profile,
    identity,
    aliases,
    colors,
    materials,
    tags,
    model_entry,
    semantic_name=None,
    asset_id_prefix="external",
    attribute_basis=None,
):
    """ledger=None creates a new per-asset ledger. If ledger already exists,
    asset-level fields must match what's already on disk (ValueError on
    drift). A model_entry with an existing model_id replaces that entry
    wholesale (re-import semantics); otherwise it's appended.

    `profile` and `identity` are required, without defaults, on purpose. A
    default profile would be the writer quietly deciding what an asset is for;
    a default identity basis would be the writer quietly asserting where a
    category came from. Both are exactly the kind of unattributed claim the
    v2 contract exists to make impossible.

    `semantic_name` still falls back to `category`, and in 31/31 ledgers on
    disk it IS the category. That is fine and deliberate: upstream's scorer
    reads entry.semantic_name normalised (lower(), spaces -> underscores) and
    compares it for EQUALITY against a single-token query category, so a
    descriptive value like "red plastic mug" could never match anything and
    would only delete a rung from the ladder. The field exists for structural
    isomorphism with the upstream catalog, not to carry a description --
    descriptive text belongs in extras."""
    # v3: semantic_name and tags are accepted for caller compatibility and
    # DISCARDED -- 79/79 v2 ledgers had semantic_name == category, and tags
    # was a copy of kind/source facts. The catalog derives its own.
    del semantic_name, tags

    if ledger is None:
        ledger = {
            "schema_version": SCHEMA_VERSION,
            "asset_id": f"{asset_id_prefix}_{asset}",
            "external_ids": {"env_gen": asset},
            "category": category,
            "kind": kind,
            "profile": profile,
            "semantics": {
                "aliases": list(aliases),
                "colors": list(colors),
                "materials": list(materials),
                "identity": dict(identity),
            },
            "models": [],
        }
        # 可选：属性来源标记（manifest/vlm/mixed）。仅在建新账本时写入；
        # 已有账本保留其原值（不参与下方漂移检查——它是标注元信息，不是资产事实）。
        if attribute_basis:
            ledger["semantics"]["attribute_basis"] = dict(attribute_basis)
    else:
        ledger = json.loads(json.dumps(ledger))  # deep copy, don't mutate caller's dict
        existing_asset_id = ledger.get("asset_id") or ""
        if not existing_asset_id.endswith(f"_{asset}"):
            raise ValueError(
                f"asset param {asset!r} does not match existing asset_id {existing_asset_id!r}"
            )
        expected = {
            "external_ids.env_gen": asset,
            "category": category,
            "kind": kind,
            "profile": profile,
            "semantics.aliases": list(aliases),
            "semantics.colors": list(colors),
            "semantics.materials": list(materials),
            "semantics.identity": dict(identity),
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


@dataclass(frozen=True)
class _LockedLedger:
    """A ledger name pinned to one safely-opened parent directory."""

    directory_fd: int
    ledger_name: str


@contextmanager
def _open_parent_directory(path):
    """Open every parent component without following a symlink.

    An ``O_NOFOLLOW`` check on only ``ledger.lock`` is insufficient: a
    symlink anywhere in its parent path can redirect both the lock and the
    eventual ledger replacement.  Walking from ``/`` with directory-relative
    opens also pins the directory against a later rename/replace race.
    """

    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise OSError("safe ledger paths require O_NOFOLLOW and O_DIRECTORY")
    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.name:
        raise OSError(f"ledger path has no file name: {path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    directory_fd = os.open(os.sep, flags)
    try:
        for component in absolute.parent.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        yield directory_fd, absolute.name
    finally:
        os.close(directory_fd)


@contextmanager
def _locked_ledger(path):
    """Hold a ledger's advisory lock without following any path symlink.

    ``open(..., 'w')`` follows symlinks and truncates their targets before
    flock runs.  The parent walk and final-component open both fail closed,
    and the yielded handle keeps later reads/replaces in that same directory.
    """

    absolute = Path(os.path.abspath(os.fspath(path)))
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if not hasattr(os, "O_NOFOLLOW"):
        raise OSError("safe ledger locking requires O_NOFOLLOW")
    flags |= os.O_NOFOLLOW
    with _open_parent_directory(path) as (directory_fd, ledger_name):
        lock_name = Path(ledger_name).with_suffix(".lock").name
        fd = os.open(lock_name, flags, 0o644, dir_fd=directory_fd)
        try:
            os.fchmod(fd, 0o644)
            with os.fdopen(fd, "r+") as lock_file:
                fd = -1
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                try:
                    current = os.stat(absolute.parent, follow_symlinks=False)
                    pinned = os.fstat(directory_fd)
                    if (
                        not stat.S_ISDIR(current.st_mode)
                        or current.st_dev != pinned.st_dev
                        or current.st_ino != pinned.st_ino
                    ):
                        raise OSError("ledger directory changed while waiting for its lock")
                    yield _LockedLedger(directory_fd, ledger_name)
                finally:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)
        finally:
            if fd >= 0:
                os.close(fd)


@contextmanager
def _locked_ledger_at(directory_fd):
    """Lock ``ledger.json`` relative to an already-pinned directory fd.

    Callers that already performed a no-follow directory walk must not turn
    that descriptor back into a ``/proc/self/fd`` pathname: doing so both
    re-enters path resolution and makes the normal parent walker reject the
    procfs magic link.  Duplicating the descriptor gives this context its own
    lifetime while preserving the caller's pinned directory identity.
    """

    owned_directory_fd = os.dup(directory_fd)
    try:
        if not stat.S_ISDIR(os.fstat(owned_directory_fd).st_mode):
            raise NotADirectoryError("ledger directory fd is not a directory")
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        lock_fd = os.open("ledger.lock", flags, 0o644, dir_fd=owned_directory_fd)
        try:
            os.fchmod(lock_fd, 0o644)
            with os.fdopen(lock_fd, "r+") as lock_file:
                lock_fd = -1
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                try:
                    yield _LockedLedger(owned_directory_fd, "ledger.json")
                finally:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)
        finally:
            if lock_fd >= 0:
                os.close(lock_fd)
    finally:
        os.close(owned_directory_fd)


def _read_locked_json(locked):
    """Read the final ledger component from its pinned directory."""

    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(locked.ledger_name, flags, dir_fd=locked.directory_fd)
    try:
        with os.fdopen(fd, "r") as stream:
            fd = -1
            return json.load(stream)
    finally:
        if fd >= 0:
            os.close(fd)


def _locked_path_exists(locked):
    """Return whether the pinned ledger exists, rejecting a final symlink."""

    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(locked.ledger_name, flags, dir_fd=locked.directory_fd)
    except FileNotFoundError:
        return False
    else:
        os.close(fd)
        return True


def _atomic_write_json(path, data, *, locked=None, pre_replace=None):
    """Durably replace JSON inside a symlink-free, pinned parent directory."""

    parent_context = None
    if locked is None:
        parent_context = _open_parent_directory(path)
        directory_fd, ledger_name = parent_context.__enter__()
        locked = _LockedLedger(directory_fd, ledger_name)

    tmp_name = None
    fd = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        for _attempt in range(100):
            candidate = f"{locked.ledger_name}.{secrets.token_hex(8)}.tmp"
            try:
                fd = os.open(candidate, flags, 0o600, dir_fd=locked.directory_fd)
            except FileExistsError:
                continue
            tmp_name = candidate
            break
        else:
            raise FileExistsError("could not allocate a unique ledger temporary file")

        with os.fdopen(fd, "w") as f:
            fd = -1
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fchmod(f.fileno(), 0o644)
            os.fsync(f.fileno())  # content + mode durable before replace, not just OS-buffered
        if pre_replace is not None:
            pre_replace()
        os.replace(
            tmp_name,
            locked.ledger_name,
            src_dir_fd=locked.directory_fd,
            dst_dir_fd=locked.directory_fd,
        )
        tmp_name = None
        os.fsync(locked.directory_fd)
    except BaseException:
        raise
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp_name is not None:
            try:
                os.unlink(tmp_name, dir_fd=locked.directory_fd)
            except FileNotFoundError:
                pass
        if parent_context is not None:
            parent_context.__exit__(None, None, None)


def append_verification(path, model_id, entry):
    """Append one verification entry for a model, under an fcntl lock, with
    atomic replace. Deduplicates on (backend, check, run_id, verified_digest):
    appending an identical tuple again is a no-op."""
    path = Path(path)
    with _locked_ledger(path) as locked:
        ledger = _read_locked_json(locked)
        models = ledger["models"]
        for m in models:
            if m.get("model_id") == model_id:
                model = m
                break
        else:
            raise ValueError(f"no model with model_id={model_id}")

        expected_digest = reps_digest(model, entry["backend"])
        if entry["verified_digest"] != expected_digest:
            raise VerificationDigestError(
                "verification digest does not match current backend representations"
            )

        identity = (
            entry["backend"],
            entry["check"],
            entry["run_id"],
            entry["verified_digest"],
        )
        canonical_entry = json.dumps(
            entry,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for existing in model["verification"]:
            existing_identity = (
                existing["backend"],
                existing["check"],
                existing["run_id"],
                existing["verified_digest"],
            )
            if existing_identity != identity:
                continue
            canonical_existing = json.dumps(
                existing,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if canonical_existing == canonical_entry:
                return ledger
            raise VerificationConflictError(
                "verification producer identity already exists with different canonical fact"
            )
        model["verification"].append(dict(entry))
        _atomic_write_json(path, ledger, locked=locked)
        return ledger


def write_ledger(path, ledger):
    """Write a full ledger dict under an fcntl lock, atomically (tmpfile +
    os.replace via _atomic_write_json). For writers that replace/insert a
    whole models[] entry in one shot (e.g. import_materialize's
    upsert_model) rather than appending a single verification record --
    a plain path.write_text() from a driver process that gets SIGKILLed
    mid-write (crash-isolation subprocesses are killed on timeout) would
    leave a torn ledger.json, breaking every later reader of that asset
    (other models in the same ledger, gen_fragment's full-library scan)."""
    path = Path(path)
    with _locked_ledger(path) as locked:
        _atomic_write_json(path, ledger, locked=locked)


def write_ledger_at(directory_fd, ledger):
    """Atomically write ``ledger.json`` inside an already-pinned directory.

    This narrow seam is for callers such as generated-asset admission that
    hold a no-follow directory descriptor across staging and publication.  It
    deliberately fixes the basename instead of accepting another path-like
    value that could reintroduce traversal.
    """

    with _locked_ledger_at(directory_fd) as locked:
        _atomic_write_json("ledger.json", ledger, locked=locked)


_EVIDENCE_RECORD_FIELDS = frozenset(
    {
        "uri",
        "sha256",
        "bytes",
        "schema",
        "run_id",
        "invocation_digest",
        "capability_sha256",
    }
)
_EVIDENCE_ENVELOPE_FIELDS = frozenset(
    {
        "schema",
        "asset_key",
        "model_id",
        "backend",
        "check",
        "verdict",
        "run_id",
        "timestamp",
        "reps_digest",
        "physical_facts_digest",
        "capability",
        "capability_sha256",
        "invocation",
        "invocation_digest",
        "result",
        "qualification",
    }
)
_CAPABILITY_FIELDS = frozenset(
    {
        "schema",
        "issuer",
        "backend",
        "check",
        "interpreter",
        "script",
        "thresholds",
        "runtime",
    }
)
_INVOCATION_FIELDS = frozenset(
    {
        "schema",
        "asset_key",
        "model_id",
        "backend",
        "check",
        "reps_digest",
        "physical_facts_digest",
        "execution_snapshot",
        "interpreter",
        "script",
        "inputs",
        "thresholds",
        "capability_sha256",
        "issuer",
    }
)
_QUALIFICATION_FIELDS = frozenset(
    {
        "schema",
        "issuer",
        "input_attestation",
        "opened_file_attested",
        "threat_model",
    }
)


def _artifact_file_record_is_well_formed(record):
    return (
        isinstance(record, dict)
        and frozenset(record) == frozenset({"uri", "sha256", "bytes"})
        and isinstance(record.get("uri"), str)
        and bool(record["uri"].strip())
        and _is_sha256(record.get("sha256"))
        and type(record.get("bytes")) is int
        and record["bytes"] >= 0
    )


def artifact_file_record_is_current(record):
    """Re-open and hash one local artifact without following any symlink.

    Artifact records are claims about bytes, not cached observations.  Every
    trust decision therefore pins the parent directory, opens the final name
    with ``O_NOFOLLOW``, and compares both the descriptor size and streamed
    digest with the record.
    """

    if not _artifact_file_record_is_well_formed(record):
        return False
    uri = record["uri"]
    if (
        urlsplit(uri).scheme
        or "\\" in uri
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in uri)
    ):
        return False
    try:
        path = _absolute_local_path(resolve_uri(uri))
        if _symlink_component(path) is not None:
            return False
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        with _open_parent_directory(path) as (directory_fd, name):
            with os.fdopen(os.open(name, flags, dir_fd=directory_fd), "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != record["bytes"]:
                    return False
                digest = hashlib.sha256()
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
    except (OSError, TypeError, ValueError, _ClosureInspectionError):
        return False
    return digest.hexdigest() == record["sha256"]


def _read_canonical_artifact(record, *, max_bytes):
    if not _artifact_file_record_is_well_formed(record) or record["bytes"] > max_bytes:
        return None
    uri = record["uri"]
    if (
        urlsplit(uri).scheme
        or "\\" in uri
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in uri)
    ):
        return None
    try:
        path = _absolute_local_path(resolve_uri(uri))
        if _symlink_component(path) is not None:
            return None
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        with _open_parent_directory(path) as (directory_fd, name):
            with os.fdopen(os.open(name, flags, dir_fd=directory_fd), "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != record["bytes"]:
                    return None
                payload = stream.read(max_bytes + 1)
    except (OSError, TypeError, ValueError, _ClosureInspectionError):
        return None
    if (
        len(payload) != record["bytes"]
        or len(payload) > max_bytes
        or hashlib.sha256(payload).hexdigest() != record["sha256"]
    ):
        return None
    try:
        document = json.loads(payload)
        if canonical_json_bytes(document) != payload:
            return None
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return document


def _snapshot_relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def _paths_are_samefile(left, right):
    try:
        return Path(left).samefile(Path(right))
    except (OSError, TypeError, ValueError):
        return False


def execution_snapshot_is_current(snapshot):
    """Re-read one immutable execution tree and verify its complete manifest."""

    fields = {
        "schema",
        "root_uri",
        "manifest",
        "asset_key",
        "model_id",
        "backend",
        "reps_digest",
        "source_asset_uri",
    }
    if not isinstance(snapshot, dict) or frozenset(snapshot) != frozenset(fields):
        return False
    try:
        asset_key = canonical_asset_key(snapshot.get("asset_key"))
        model_id = canonical_model_id(snapshot.get("model_id"))
    except (UnsafeAssetKeyError, InvalidModelIdError):
        return False
    if (
        snapshot.get("schema") != EXECUTION_SNAPSHOT_SCHEMA
        or snapshot.get("backend") not in BACKENDS
        or not _is_sha256(snapshot.get("reps_digest"))
    ):
        return False
    manifest_record = snapshot.get("manifest")
    manifest = _read_canonical_artifact(manifest_record, max_bytes=1024 * 1024)
    if not isinstance(manifest, dict):
        return False
    expected_manifest_fields = {
        "schema",
        "asset_key",
        "model_id",
        "backend",
        "reps_digest",
        "asset_relpath",
        "roles",
        "ancillary",
        "extras",
        "source_asset_uri",
    }
    if frozenset(manifest) != frozenset(expected_manifest_fields):
        return False
    try:
        root = _absolute_local_path(resolve_uri(snapshot["root_uri"]))
        manifest_path = _absolute_local_path(resolve_uri(manifest_record["uri"]))
        source_asset_dir = _absolute_local_path(resolve_uri(snapshot["source_asset_uri"]))
        unsafe_root = _symlink_component(root)
        unsafe_source = _symlink_component(source_asset_dir)
    except (KeyError, OSError, TypeError, ValueError):
        return False
    if (
        unsafe_root is not None
        or unsafe_source is not None
        or not source_asset_dir.is_dir()
        or snapshot["source_asset_uri"] != to_portable_uri(source_asset_dir)
        or root.name != f"{manifest_record.get('sha256')}.execution"
        or not _paths_are_samefile(manifest_path.parent, root)
        or manifest_path.name != "manifest.json"
        or manifest.get("schema") != EXECUTION_SNAPSHOT_SCHEMA
        or manifest.get("asset_key") != asset_key
        or manifest.get("model_id") != model_id
        or manifest.get("backend") != snapshot["backend"]
        or manifest.get("reps_digest") != snapshot["reps_digest"]
        or manifest.get("asset_relpath") != f"assets/objects/{asset_key}"
        or manifest.get("source_asset_uri") != snapshot["source_asset_uri"]
        or not isinstance(manifest.get("roles"), list)
        or not manifest["roles"]
        or not isinstance(manifest.get("ancillary"), list)
        or not isinstance(manifest.get("extras"), list)
    ):
        return False
    expected_paths = {PurePosixPath("manifest.json")}
    for role_entry in manifest["roles"]:
        if (
            not isinstance(role_entry, dict)
            or frozenset(role_entry)
            != frozenset({"representation_index", "role", "primary", "closure"})
            or type(role_entry.get("representation_index")) is not int
            or role_entry["representation_index"] < 0
            or role_entry.get("role") not in ROLES
            or role_entry.get("role") == "snapshot"
            or not isinstance(role_entry.get("closure"), list)
            or not role_entry["closure"]
        ):
            return False
        closure = role_entry["closure"]
        if role_entry.get("primary") not in closure:
            return False
        for member in closure:
            if (
                not isinstance(member, dict)
                or frozenset(member) != frozenset({"path", "sha256", "bytes"})
                or (relative := _snapshot_relative_path(member.get("path"))) is None
                or not _is_sha256(member.get("sha256"))
                or type(member.get("bytes")) is not int
                or member["bytes"] < 0
            ):
                return False
            expected_paths.add(relative)
            record = {
                "uri": str(root / Path(*relative.parts)),
                "sha256": member["sha256"],
                "bytes": member["bytes"],
            }
            if not artifact_file_record_is_current(record):
                return False
    for ancillary in manifest["ancillary"]:
        if (
            not isinstance(ancillary, dict)
            or frozenset(ancillary) != frozenset({"name", "path", "sha256", "bytes"})
            or not isinstance(ancillary.get("name"), str)
            or not ancillary["name"]
            or (relative := _snapshot_relative_path(ancillary.get("path"))) is None
            or not _is_sha256(ancillary.get("sha256"))
            or type(ancillary.get("bytes")) is not int
            or ancillary["bytes"] < 0
        ):
            return False
        expected_paths.add(relative)
        if not artifact_file_record_is_current(
            {
                "uri": str(root / Path(*relative.parts)),
                "sha256": ancillary["sha256"],
                "bytes": ancillary["bytes"],
            }
        ):
            return False
    for extra in manifest["extras"]:
        if (
            not isinstance(extra, dict)
            or frozenset(extra) != frozenset({"name", "path", "sha256", "bytes"})
            or not isinstance(extra.get("name"), str)
            or not extra["name"]
            or (relative := _snapshot_relative_path(extra.get("path"))) is None
            or not _is_sha256(extra.get("sha256"))
            or type(extra.get("bytes")) is not int
            or extra["bytes"] < 0
        ):
            return False
        expected_paths.add(relative)
        if not artifact_file_record_is_current(
            {
                "uri": str(root / Path(*relative.parts)),
                "sha256": extra["sha256"],
                "bytes": extra["bytes"],
            }
        ):
            return False
    expected_directories = {PurePosixPath(".")}
    for expected_path in expected_paths:
        parent = expected_path.parent
        while parent != PurePosixPath("."):
            expected_directories.add(parent)
            parent = parent.parent
    try:
        actual_paths = set()
        actual_directories = set()
        for directory, directories, files in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            if directory_path.stat().st_mode & 0o222:
                return False
            actual_directories.add(PurePosixPath(directory_path.relative_to(root).as_posix()))
            for name in directories:
                child = directory_path / name
                if child.is_symlink():
                    return False
            for name in files:
                child = directory_path / name
                if child.is_symlink() or child.stat().st_mode & 0o222:
                    return False
                actual_paths.add(PurePosixPath(child.relative_to(root).as_posix()))
    except (OSError, RuntimeError, ValueError):
        return False
    return actual_paths == expected_paths and actual_directories == expected_directories


def _expected_snapshot_roles(model_entry, backend, source_asset_dir, asset_key):
    roles = []
    try:
        representations = model_entry.get("representations", [])
    except AttributeError:
        return None
    for representation_index, representation in enumerate(representations):
        if (
            not isinstance(representation, dict)
            or representation.get("backend") != backend
            or representation.get("role") == "snapshot"
        ):
            continue
        files = representation.get("files")
        if not isinstance(files, list) or not files:
            return None
        closure = []
        primary = None
        for record in files:
            if not artifact_file_record_is_current(record):
                return None
            try:
                source = _absolute_local_path(resolve_uri(record["uri"])).resolve(strict=True)
                relative = source.relative_to(source_asset_dir)
            except (KeyError, OSError, RuntimeError, TypeError, ValueError):
                return None
            if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
                return None
            member = {
                "path": (Path("assets") / "objects" / asset_key / relative).as_posix(),
                "sha256": record["sha256"],
                "bytes": record["bytes"],
            }
            closure.append(member)
            if (record.get("uri"), record.get("sha256")) == (
                representation.get("uri"),
                representation.get("sha256"),
            ):
                primary = member
        if primary is None:
            return None
        roles.append(
            {
                "representation_index": representation_index,
                "role": representation.get("role"),
                "primary": primary,
                "closure": closure,
            }
        )
    return roles or None


def execution_snapshot_matches_model(snapshot, model_entry, backend):
    """Bind a current snapshot's role/primary/closure to authoritative model bytes."""

    if not execution_snapshot_is_current(snapshot):
        return False
    try:
        model_id = canonical_model_id(model_entry.get("model_id"))
        asset_key = canonical_asset_key(snapshot.get("asset_key"))
        source_asset_dir = _absolute_local_path(resolve_uri(snapshot["source_asset_uri"])).resolve(
            strict=True
        )
        expected_digest = reps_digest(model_entry, backend)
        manifest = _read_canonical_artifact(snapshot["manifest"], max_bytes=1024 * 1024)
    except (
        AttributeError,
        InvalidModelIdError,
        KeyError,
        OSError,
        RepresentationDigestError,
        RuntimeError,
        TypeError,
        UnsafeAssetKeyError,
        ValueError,
    ):
        return False
    expected_roles = _expected_snapshot_roles(model_entry, backend, source_asset_dir, asset_key)
    return (
        isinstance(manifest, dict)
        and snapshot.get("model_id") == model_id
        and snapshot.get("backend") == backend
        and snapshot.get("reps_digest") == expected_digest
        and manifest.get("roles") == expected_roles
    )


def runtime_capability_is_current(capability):
    """Validate and re-hash the loaded runtime module tree and fixed config."""

    fields = {
        "schema",
        "entrypoint",
        "loader_modules",
        "sapien_modules",
        "native_modules",
        "config",
    }
    if (
        not isinstance(capability, dict)
        or frozenset(capability) != frozenset(fields)
        or capability.get("schema") != RUNTIME_CAPABILITY_SCHEMA
        or not isinstance(capability.get("entrypoint"), str)
        or not capability["entrypoint"].strip()
        or not isinstance(capability.get("config"), dict)
        or not capability["config"]
    ):
        return False
    try:
        canonical_json_bytes(capability["config"])
    except (TypeError, ValueError):
        return False
    module_groups = (capability.get("loader_modules"), capability.get("sapien_modules"))
    if any(not isinstance(group, list) or not group for group in module_groups):
        return False
    sapien_names = set()
    expected_native_modules = set()
    for group_index, group in enumerate(module_groups):
        group_names = set()
        for module in group:
            if (
                not isinstance(module, dict)
                or frozenset(module) != frozenset({"name", "file"})
                or not isinstance(module.get("name"), str)
                or not module["name"]
                or module["name"] in group_names
                or not artifact_file_record_is_current(module.get("file"))
            ):
                return False
            group_names.add(module["name"])
            if group_index == 1:
                sapien_names.add(module["name"])
                if Path(resolve_uri(module["file"]["uri"])).suffix.lower() in {
                    ".so",
                    ".pyd",
                    ".dll",
                    ".dylib",
                }:
                    expected_native_modules.add(module["name"])
    native_modules = capability.get("native_modules")
    return (
        isinstance(native_modules, list)
        and len(native_modules) == len(set(native_modules))
        and all(isinstance(name, str) and name in sapien_names for name in native_modules)
        and native_modules == sorted(expected_native_modules)
    )


def qualified_runtime_capability_is_current(capability, issuer_spec):
    """Enforce the closed loader/SAPIEN/config policy for one qualified issuer."""

    if not runtime_capability_is_current(capability):
        return False
    policy = issuer_spec.get("runtime") if isinstance(issuer_spec, dict) else None
    if not isinstance(policy, dict):
        return False
    loader_root = policy.get("loader_module_root")
    sapien_root = policy.get("sapien_module_root")

    def belongs_to(module, root):
        name = module.get("name") if isinstance(module, dict) else None
        return isinstance(name, str) and (name == root or name.startswith(f"{root}."))

    return (
        capability.get("entrypoint") == policy.get("entrypoint")
        and capability.get("config") == policy.get("config")
        and isinstance(loader_root, str)
        and isinstance(sapien_root, str)
        and all(belongs_to(module, loader_root) for module in capability["loader_modules"])
        and all(belongs_to(module, sapien_root) for module in capability["sapien_modules"])
        and bool(capability["native_modules"])
    )


def model_representation_files_are_current(model_entry, backend):
    """Check every loader-visible representation member for one backend."""

    found = False
    for representation in model_entry.get("representations", []):
        if (
            not isinstance(representation, dict)
            or representation.get("backend") != backend
            or representation.get("role") == "snapshot"
        ):
            continue
        files = representation.get("files")
        if not isinstance(files, list) or not files:
            return False
        found = True
        if not all(artifact_file_record_is_current(member) for member in files):
            return False
    return found


def verification_inputs_are_current(inputs):
    """Validate the task document and every hash-bound invocation input."""

    if (
        not isinstance(inputs, dict)
        or not inputs
        or any(not isinstance(name, str) or not name.strip() for name in inputs)
        or not isinstance(inputs.get("task"), dict)
        or not inputs["task"]
    ):
        return False
    artifacts = [record for name, record in inputs.items() if name != "task"]
    return bool(artifacts) and all(artifact_file_record_is_current(record) for record in artifacts)


def _interpreter_record_is_well_formed(record):
    return (
        isinstance(record, dict)
        and frozenset(record) == frozenset({"implementation", "version", "executable"})
        and isinstance(record.get("implementation"), str)
        and bool(record["implementation"].strip())
        and isinstance(record.get("version"), str)
        and bool(record["version"].strip())
        and _artifact_file_record_is_well_formed(record.get("executable"))
    )


def _interpreter_record_is_current(record):
    if (
        not _interpreter_record_is_well_formed(record)
        or record["implementation"] != platform.python_implementation()
        or record["version"] != platform.python_version()
        or not artifact_file_record_is_current(record["executable"])
    ):
        return False
    try:
        return Path(resolve_uri(record["executable"]["uri"])).samefile(sys.executable)
    except (OSError, TypeError, ValueError):
        return False


def _evidence_record_is_well_formed(record):
    return (
        isinstance(record, dict)
        and frozenset(record) == _EVIDENCE_RECORD_FIELDS
        and isinstance(record.get("uri"), str)
        and bool(record["uri"].strip())
        and _is_sha256(record.get("sha256"))
        and type(record.get("bytes")) is int
        and 0 < record["bytes"] <= _VERIFICATION_EVIDENCE_MAX_BYTES
        and record.get("schema") == VERIFICATION_EVIDENCE_SCHEMA
        and isinstance(record.get("run_id"), str)
        and bool(record["run_id"].strip())
        and _is_sha256(record.get("invocation_digest"))
        and _is_sha256(record.get("capability_sha256"))
    )


def _read_evidence_envelope(record):
    if not _evidence_record_is_well_formed(record):
        return None
    uri = record["uri"]
    if (
        urlsplit(uri).scheme
        or "\\" in uri
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in uri)
    ):
        return None
    try:
        path = _absolute_local_path(resolve_uri(uri))
        if path.name != f"{record['sha256']}{_VERIFICATION_EVIDENCE_SUFFIX}":
            return None
        if _symlink_component(path) is not None:
            return None
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        with _open_parent_directory(path) as (directory_fd, name):
            with os.fdopen(os.open(name, flags, dir_fd=directory_fd), "rb") as stream:
                fd = stream.fileno()
                metadata = os.fstat(fd)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != record["bytes"]:
                    return None
                payload = stream.read(_VERIFICATION_EVIDENCE_MAX_BYTES + 1)
    except (OSError, TypeError, ValueError):
        return None
    if (
        len(payload) != record["bytes"]
        or len(payload) > _VERIFICATION_EVIDENCE_MAX_BYTES
        or hashlib.sha256(payload).hexdigest() != record["sha256"]
    ):
        return None
    try:
        envelope = json.loads(payload)
        if canonical_json_bytes(envelope) != payload:
            return None
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return envelope if isinstance(envelope, dict) else None


def verification_from_trusted_evidence(model_entry, evidence_record, asset_key):
    """Re-read and verify one immutable artifact against the current model.

    Returns the only receipt the artifact is allowed to produce, or ``None``.
    No result is cached: replacing or corrupting the file immediately removes
    trust on the next call.
    """

    try:
        asset_key = canonical_asset_key(asset_key)
        model_id = canonical_model_id(model_entry.get("model_id"))
    except (UnsafeAssetKeyError, InvalidModelIdError, AttributeError):
        return None
    envelope = _read_evidence_envelope(evidence_record)
    if envelope is None or frozenset(envelope) != _EVIDENCE_ENVELOPE_FIELDS:
        return None
    capability = envelope.get("capability")
    invocation = envelope.get("invocation")
    qualification = envelope.get("qualification")
    if (
        envelope.get("schema") != VERIFICATION_EVIDENCE_SCHEMA
        or envelope.get("asset_key") != asset_key
        or envelope.get("model_id") != model_id
        or envelope.get("backend") not in BACKENDS
        or envelope.get("check") not in CHECKS
        or envelope.get("check") == "generation_qc"
        or envelope.get("verdict") not in VERDICTS
        or not isinstance(envelope.get("run_id"), str)
        or not envelope["run_id"].strip()
        or not _is_iso_datetime(envelope.get("timestamp"))
        or not _is_sha256(envelope.get("reps_digest"))
        or not isinstance(envelope.get("result"), dict)
        or not isinstance(capability, dict)
        or frozenset(capability) != _CAPABILITY_FIELDS
        or not isinstance(invocation, dict)
        or frozenset(invocation) != _INVOCATION_FIELDS
        or not isinstance(qualification, dict)
        or frozenset(qualification) != _QUALIFICATION_FIELDS
    ):
        return None
    try:
        issuer_spec = qualified_verification_issuer(qualification.get("issuer"))
    except VerificationResultError:
        return None
    if (
        qualification.get("schema") != VERIFICATION_QUALIFICATION_SCHEMA
        or qualification.get("input_attestation") != VERIFICATION_INPUT_ATTESTATION
        or qualification.get("opened_file_attested") is not False
        or qualification.get("threat_model") != VERIFICATION_THREAT_MODEL
        or issuer_spec["backend"] != envelope["backend"]
        or issuer_spec["check"] != envelope["check"]
    ):
        return None
    if (
        capability.get("schema") != VERIFICATION_CAPABILITY_SCHEMA
        or capability.get("issuer") != issuer_spec["issuer"]
        or capability.get("backend") != envelope["backend"]
        or capability.get("check") != envelope["check"]
        or not _interpreter_record_is_current(capability.get("interpreter"))
        or not _artifact_file_record_is_well_formed(capability.get("script"))
        or not isinstance(capability.get("thresholds"), dict)
        or not qualified_verification_thresholds_are_allowed(
            capability.get("thresholds"), issuer_spec
        )
        or not qualified_runtime_capability_is_current(capability.get("runtime"), issuer_spec)
    ):
        return None
    if not artifact_file_record_is_current(capability["script"]):
        return None
    try:
        if not Path(resolve_uri(capability["script"]["uri"])).samefile(issuer_spec["script_path"]):
            return None
    except (OSError, TypeError, ValueError):
        return None
    capability_sha256 = hashlib.sha256(canonical_json_bytes(capability)).hexdigest()
    if (
        envelope.get("capability_sha256") != capability_sha256
        or evidence_record.get("capability_sha256") != capability_sha256
    ):
        return None
    if (
        invocation.get("schema") != VERIFICATION_INVOCATION_SCHEMA
        or invocation.get("issuer") != issuer_spec["issuer"]
        or invocation.get("asset_key") != asset_key
        or invocation.get("model_id") != model_id
        or invocation.get("backend") != envelope["backend"]
        or invocation.get("check") != envelope["check"]
        or invocation.get("reps_digest") != envelope["reps_digest"]
        or invocation.get("physical_facts_digest") != envelope["physical_facts_digest"]
        or not execution_snapshot_matches_model(
            invocation.get("execution_snapshot"), model_entry, envelope["backend"]
        )
        or invocation.get("interpreter") != capability["interpreter"]
        or invocation.get("script") != capability["script"]
        or invocation.get("thresholds") != capability["thresholds"]
        or not verification_inputs_are_current(invocation.get("inputs"))
        or invocation.get("capability_sha256") != capability_sha256
    ):
        return None
    execution_snapshot = invocation["execution_snapshot"]
    if (
        execution_snapshot.get("asset_key") != asset_key
        or execution_snapshot.get("model_id") != model_id
        or execution_snapshot.get("backend") != envelope["backend"]
        or execution_snapshot.get("reps_digest") != envelope["reps_digest"]
    ):
        return None
    invocation_digest = hashlib.sha256(canonical_json_bytes(invocation)).hexdigest()
    if (
        envelope.get("invocation_digest") != invocation_digest
        or evidence_record.get("invocation_digest") != invocation_digest
        or evidence_record.get("run_id") != envelope["run_id"]
        or evidence_record.get("schema") != envelope["schema"]
    ):
        return None
    try:
        current_digest = reps_digest(model_entry, envelope["backend"])
    except RepresentationDigestError:
        return None
    if envelope["reps_digest"] != current_digest:
        return None
    try:
        derived_verdict = qualified_verification_verdict(
            envelope["backend"],
            envelope["check"],
            capability["thresholds"],
            envelope["result"],
        )
    except VerificationResultError:
        return None
    if envelope["verdict"] != derived_verdict:
        return None
    if envelope["check"] in {"settle", "runtime_load", "joint_sweep"}:
        try:
            physical_facts_digest = settle_physical_facts_digest(model_entry)
            result_matches = (
                (
                    envelope["check"] != "settle"
                    or settle_result_matches_physical_facts(
                        model_entry,
                        envelope["result"],
                        derived_verdict,
                    )
                )
                and (
                    envelope["check"] != "runtime_load"
                    or runtime_load_result_matches_physical_facts(model_entry, envelope["result"])
                )
                and (
                    envelope["check"] != "joint_sweep"
                    or joint_sweep_result_matches_physical_facts(model_entry, envelope["result"])
                )
            )
        except VerificationResultError:
            return None
        if envelope.get("physical_facts_digest") != physical_facts_digest or not result_matches:
            return None
    elif envelope.get("physical_facts_digest") is not None:
        return None
    return {
        "backend": envelope["backend"],
        "check": envelope["check"],
        "verdict": envelope["verdict"],
        "run_id": envelope["run_id"],
        "timestamp": envelope["timestamp"],
        "verified_digest": envelope["reps_digest"],
        "evidence": dict(evidence_record),
    }


def latest_verification(model_entry, backend, check):
    """Most recent (by timestamp) verification entry for (backend, check).
    Returns None if there is none, or if the latest entry's verified_digest
    no longer matches reps_digest(model_entry, backend) (stale -> report as
    unverified rather than trusting a superseded pass). A malformed receipt
    poisons the verification set instead of being skipped in favour of an
    older pass, and a tied latest timestamp is ambiguous rather than ordered by
    list position: invalid or ambiguous evidence must never improve the trust
    decision."""
    verifications = model_entry.get("verification", [])
    if not isinstance(verifications, list) or any(
        not _verification_entry_is_well_formed(entry) for entry in verifications
    ):
        return None
    if _verification_conflict_indices(verifications):
        return None
    candidates = [
        v for v in verifications if v.get("backend") == backend and v.get("check") == check
    ]
    if not candidates:
        return None
    latest_timestamp = max(v["timestamp"] for v in candidates)
    latest_candidates = [v for v in candidates if v["timestamp"] == latest_timestamp]
    if len(latest_candidates) != 1:
        return None
    latest = latest_candidates[0]
    try:
        current_digest = reps_digest(model_entry, backend)
    except RepresentationDigestError:
        return None
    if latest.get("verified_digest") != current_digest:
        return None
    return latest


def latest_trusted_verification(model_entry, backend, check, asset_key):
    """Return the latest receipt only when its immutable artifact is trusted."""

    if check == "generation_qc":
        return None
    latest = latest_verification(model_entry, backend, check)
    if latest is None:
        return None
    trusted = verification_from_trusted_evidence(model_entry, latest.get("evidence"), asset_key)
    return trusted if trusted == latest else None


def to_ir_bundles(ledger):
    """Flatten each models[] entry into a standalone per-model bundle dict
    shaped like the legacy IR AssetBundle: conventions expanded into
    `physical`, snapshot representations dropped, asset_id suffixed with
    `_m<model_id>`.

    v3 removed handwritten ledger tags because they duplicated canonical
    kind/source facts.  The legacy IR still exposes tags, so this adapter
    derives its compatibility vocabulary instead of silently emitting an
    empty list (`retrieved` was historically spelled `external` there).
    """
    bundles = []
    for m in ledger.get("models", []):
        physical = dict(m.get("physical", {}))
        conventions = physical.pop("conventions", {})
        physical.update(conventions)
        reps = [r for r in m.get("representations", []) if r.get("role") != "snapshot"]
        tags = []
        kind = ledger.get("kind")
        if kind in KINDS:
            tags.append(kind)
        source = m.get("source")
        source_kind = source.get("kind") if isinstance(source, dict) else None
        if source_kind == "retrieved":
            tags.append("external")
        elif source_kind == "generated":
            tags.append("generated")
        bundles.append(
            {
                "asset_id": f"{ledger['asset_id']}_m{m['model_id']}",
                "category": ledger.get("category"),
                "representations": reps,
                "source": m.get("source"),
                "physical": physical,
                "articulation": m.get("articulation", {}),
                "tags": tags,
            }
        )
    return bundles
