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

import datetime
import hashlib
import fcntl
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lib import conventions

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

_MISSING = object()


@dataclass
class Violation:
    path: str
    code: str
    message: str


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


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
    if (
        not isinstance(value, str)
        or len(value) != 19
        or not _ISO_DATETIME_RE.match(value)
    ):
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


# Path portability contract (public repo: no /home/<user> in tracked ledgers;
# a path written on one machine must resolve on another). Anchor mirrors
# runtime_config.ASSET_PIPELINE_ROOT without importing it (lib stays pure
# stdlib): env override first, else this checkout's active root.
ACTIVE_ROOT = Path(
    os.environ.get("ASSET_PIPELINE_ROOT", Path(__file__).resolve().parents[2])
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
    root = Path(library_dir)
    flat = root / asset
    if flat.is_dir():
        return flat
    hits = [
        p
        for p in root.glob("*/" + asset)
        if p.is_dir() and not p.parent.name.startswith("_")
    ]
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
    root = Path(library_dir)
    if not root.is_dir():
        return
    found = {}

    def claim(path):
        if path.name in found:
            raise ValueError(
                "asset %s exists twice in library: %s and %s"
                % (path.name, found[path.name], path)
            )
        found[path.name] = path

    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        if _is_asset_name(entry.name):
            claim(entry)
            continue
        for sub in sorted(entry.iterdir()):
            if sub.is_dir() and _is_asset_name(sub.name):
                claim(sub)
    for name in sorted(found):
        yield found[name]


def ledger_path(library_dir, asset):
    """`asset`'s ledger.json, in whichever layout the library uses.

    Falls back to the flat path when the asset does not exist yet, so callers
    that are about to CREATE an asset still get a writable target.
    """
    found = asset_dir(library_dir, asset)
    return (found or Path(library_dir) / asset) / "ledger.json"


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
                "measured data must record the stack it was measured against "
                "(at minimum: backend)",
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
                Violation(
                    f"{prefix}.{k}", "missing", f"required field missing: {prefix}.{k}"
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
        # size_bytes was required in v1 and is optional in v2: the only reader
        # of a size_bytes anywhere in this project is a2_selection's
        # max_size_bytes gate, which reads the RETRIEVAL CANDIDATE's metadata
        # before ingest -- never the ledger. sha256 carries identity; byte
        # count carried nothing.
        for field in ("format", "uri", "role", "sha256"):
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
                    f"source.kind={kind} must not carry {path} "
                    f"(it belongs to the other branch)",
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


def _validate_verification(verifications, prefix, out):
    # report_path was required through v2 and is optional in v3: no reader
    # anywhere checks the file exists or opens it (gen_fragment /
    # latest_verification / usd_enrich consume backend/check/verdict/
    # timestamp/verified_digest only). It stays legal as capture.
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
        if (
            "timestamp" in v
            and v["timestamp"] is not None
            and not _is_iso_datetime(v["timestamp"])
        ):
            out.append(
                Violation(
                    f"{vp}.timestamp",
                    "bad_timestamp",
                    f"timestamp {v['timestamp']!r} is not a valid ISO date/datetime",
                )
            )


def _validate_model(model, prefix, out, profile=None):
    _check_required(model, REQUIRED_MODEL, prefix, out)

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

    inertial = _get(model, "physical.inertial")
    if inertial is not _MISSING:
        _validate_inertial(inertial, f"{prefix}.physical.inertial", out)

    # v3 optional blocks. Optional means the KEY may be absent; a present
    # block still has to be well-formed -- a malformed measurement record is
    # worse than none, because readers trust it.
    restitution = _get(model, "physical.restitution")
    if restitution is not _MISSING and restitution is not None:
        if isinstance(restitution, dict):
            _validate_mass_or_friction(
                restitution, f"{prefix}.physical.restitution", out
            )
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

    profile = ledger.get("profile")
    if profile is not None and "profile" in ledger and profile not in PROFILES:
        out.append(
            Violation(
                "profile", "bad_profile", f"profile {profile!r} not in {PROFILES}"
            )
        )
        profile = None  # don't let an invalid profile select a requirement table

    identity = _get(ledger, "semantics.identity")
    if identity is not _MISSING:
        _validate_identity(identity, "semantics.identity", out)

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
                _validate_model(m, f"models.{i}", out, profile=profile)
                # cross_backend's other half: declaring the intent to migrate
                # and owning no target-backend representation is a debt, and
                # this is where it becomes visible. Under sapien_only the same
                # absence is simply correct, which is the whole point of
                # asking the asset to declare what it is for.
                if profile == "cross_backend":
                    reps = m.get("representations") or []
                    if not any(
                        r.get("backend") == "isaacsim" and r.get("role") != "snapshot"
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
                p = resolve_uri(uri)
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
    profile = ledger.get("profile")
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

    reps = model.get("representations", [])
    if not any(
        r.get("backend") == "sapien" and r.get("role") != "snapshot" for r in reps
    ):
        missing.append(f"{prefix}.representations[backend=sapien]")
    if profile == "cross_backend" and not any(
        r.get("backend") == "isaacsim" and r.get("role") != "snapshot" for r in reps
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
    mass_kg = mass_override or {"value": None, "status": "unknown"}
    friction = friction_override or {"value": None, "status": "unknown"}
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
            f.flush()
            os.fsync(
                f.fileno()
            )  # crash-durability: on disk before replace, not just OS buffer
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
    lock_path = _lock_path(path)
    lock_path.touch(exist_ok=True)
    with open(lock_path, "w") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            _atomic_write_json(path, ledger)
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
