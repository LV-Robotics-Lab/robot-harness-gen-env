"""Deep admission and isolated publication for limited image previews."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .limited_image_preview_execution import validate_preview_receipt
from .revised_image_compiler import RevisedImage2EnvGenesisCompiler
from .schemas.common import ArtifactRef
from .schemas.genesis_runtime_manifest import GenesisRuntimeManifestV1
from .schemas.genesis_scene_execution_v2 import (
    GenesisSceneExecutionReceiptV2,
    GenesisSceneExecutionRequestV2,
)
from .schemas.genesis_scene_execution_v3 import (
    GenesisSceneExecutionReceiptV3,
    GenesisSceneExecutionRequestV3,
)
from .schemas.genesis_scene_replay import GenesisSceneReportV1, GenesisSceneSpecV1
from .schemas.image2env_genesis_compile import (
    Image2EnvGenesisCompileReceiptV1,
    Image2EnvGenesisCompileRequestV1,
)
from .schemas.limited_image_preview import (
    LIMITED_IMAGE_PREVIEW_LIBRARY_INDEX_SCHEMA_ID,
    LimitedImagePreviewIndexEntryV1,
    LimitedImagePreviewLibraryIndexV1,
    LimitedImagePreviewManifestV1,
    LimitedImagePreviewStageV1,
    limited_image_preview_library_index_sha256,
)
from .schemas.limited_image_preview_execution import LimitedSceneValidateInputV1
from .schemas.native_prompt_revision import (
    Image2EnvGenesisCompileReceiptV2,
    Image2EnvGenesisCompileRequestV2,
    PromptRevisionV3,
)
from .schemas.x2env import ImageSourceMedia
from .schemas.x2env_compile_evidence import (
    AssetRepresentationV1,
    GenesisAssetQualificationV1,
    GenesisRuntimeLockV1,
    SceneInterpretationProposalV1,
)

_CAPABILITY_TOKEN = object()
_LIBRARY_TOKEN = object()
_INDEX_NAME = "preview-index.json"
_LOCK_NAME = ".preview-index.lock"


class LimitedImagePreviewLibraryError(RuntimeError):
    """A preview is unverified, unavailable, or inconsistent with its project index."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class PublishedLimitedImagePreview:
    """The small stable record returned by list and publish operations."""

    preview_id: str
    manifest: ArtifactRef
    mode: str
    final_scene_sha256: str


class VerifiedLimitedImagePreview:
    """Opaque capability issued only after every preview release gate is reread."""

    __slots__ = ("_artifact_root", "_compiler", "_manifest", "_manifest_ref", "_token")

    def __init__(
        self,
        *,
        artifact_root: Path,
        compiler: RevisedImage2EnvGenesisCompiler,
        manifest: LimitedImagePreviewManifestV1,
        manifest_ref: ArtifactRef,
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN:
            raise TypeError("verified previews are issued only by the admission verifier")
        self._artifact_root = artifact_root
        self._compiler = compiler
        self._manifest = manifest
        self._manifest_ref = manifest_ref
        self._token = _token

    @property
    def manifest(self) -> LimitedImagePreviewManifestV1:
        return self._manifest

    @property
    def manifest_ref(self) -> ArtifactRef:
        return self._manifest_ref


class LimitedImagePreviewLibrary:
    """An isolated, append-only project index over immutable preview manifests."""

    __slots__ = ("_artifact_store", "_compiler", "_project_id", "_root", "_token")

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        compiler: RevisedImage2EnvGenesisCompiler,
        root: Path,
        project_id: str,
        _token: object,
    ) -> None:
        if _token is not _LIBRARY_TOKEN:
            raise TypeError("preview libraries must be created or loaded by their factory")
        self._artifact_store = artifact_store
        self._compiler = compiler
        self._root = root
        self._project_id = project_id
        self._token = _token

    def publish(
        self,
        preview: VerifiedLimitedImagePreview,
    ) -> PublishedLimitedImagePreview:
        """Atomically append an admitted manifest or return its existing entry."""

        if (
            type(preview) is not VerifiedLimitedImagePreview
            or preview._token is not _CAPABILITY_TOKEN
        ):
            raise TypeError("publish requires an exact verified preview capability")
        if preview._artifact_root != self._artifact_store.root:
            raise LimitedImagePreviewLibraryError(
                "foreign_artifact_store",
                "verified preview belongs to another artifact store",
            )
        if preview._compiler is not self._compiler:
            raise LimitedImagePreviewLibraryError(
                "foreign_compiler",
                "verified preview belongs to another exact compiler binding",
            )
        checked = admit_limited_image_preview(
            artifact_store=self._artifact_store,
            compiler=self._compiler,
            manifest=preview.manifest_ref,
        )
        if checked.manifest != preview.manifest:
            raise LimitedImagePreviewLibraryError(
                "capability_drift",
                "verified preview differs from its current canonical manifest",
            )
        entry = _entry(checked)
        with self._locked_index() as current:
            by_id = {item.preview_id: item for item in current.entries}
            existing = by_id.get(entry.preview_id)
            if existing is not None:
                if existing != entry:
                    raise LimitedImagePreviewLibraryError(
                        "preview_id_conflict",
                        "preview ID is already bound to a different manifest",
                    )
                return _published(existing)
            updated_entries = tuple(
                sorted((*current.entries, entry), key=lambda item: item.preview_id)
            )
            updated = _new_index(self._project_id, updated_entries)
            _write_index(self._root / _INDEX_NAME, updated)
        return _published(entry)

    def list(self) -> tuple[PublishedLimitedImagePreview, ...]:
        """List only entries whose complete preview evidence still verifies."""

        index = self._read_index()
        published: list[PublishedLimitedImagePreview] = []
        for entry in index.entries:
            verified = admit_limited_image_preview(
                artifact_store=self._artifact_store,
                compiler=self._compiler,
                manifest=entry.manifest,
            )
            if _entry(verified) != entry:
                raise LimitedImagePreviewLibraryError(
                    "index_entry_drift",
                    "preview index entry differs from its admitted manifest",
                )
            published.append(_published(entry))
        return tuple(published)

    def get(self, preview_id: str) -> VerifiedLimitedImagePreview:
        """Deeply reload one indexed preview as a fresh verified capability."""

        return self._verified_entry(preview_id)

    def reuse(self, preview_id: str) -> VerifiedLimitedImagePreview:
        """Return a deep-verified capability suitable for idempotent republication."""

        return self._verified_entry(preview_id)

    def materialize(self, preview_id: str, destination: Path) -> Path:
        """Restore a deterministic review tree without copying runtime deployments."""

        verified = self._verified_entry(preview_id)
        target = destination.expanduser().resolve()
        if target.exists():
            raise LimitedImagePreviewLibraryError(
                "destination_exists",
                f"destination already exists: {target}",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
        try:
            for relative, ref in _materialized_members(
                self._artifact_store,
                verified.manifest,
                verified.manifest_ref,
            ):
                output = staging / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                source = _resolve(self._artifact_store, ref, relative.as_posix())
                shutil.copyfile(source, output)
                if output.stat().st_size != ref.bytes or _sha256(output) != ref.sha256:
                    raise LimitedImagePreviewLibraryError(
                        "materialized_member_drift",
                        f"materialized member differs from CAS: {relative.as_posix()}",
                    )
            os.replace(staging, target)
            return target
        except LimitedImagePreviewLibraryError:
            raise
        except OSError as error:
            raise LimitedImagePreviewLibraryError(
                "materialize_failed",
                f"preview could not be materialized: {error}",
            ) from error
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _verified_entry(self, preview_id: str) -> VerifiedLimitedImagePreview:
        index = self._read_index()
        matching = tuple(entry for entry in index.entries if entry.preview_id == preview_id)
        if len(matching) != 1:
            raise LimitedImagePreviewLibraryError(
                "preview_not_found",
                f"preview is not indexed for project {self._project_id}: {preview_id}",
            )
        entry = matching[0]
        verified = admit_limited_image_preview(
            artifact_store=self._artifact_store,
            compiler=self._compiler,
            manifest=entry.manifest,
        )
        if _entry(verified) != entry:
            raise LimitedImagePreviewLibraryError(
                "index_entry_drift",
                "preview index entry differs from its admitted manifest",
            )
        return verified

    def _read_index(self) -> LimitedImagePreviewLibraryIndexV1:
        return _read_index(self._root / _INDEX_NAME, self._project_id)

    class _IndexLock:
        def __init__(self, library: LimitedImagePreviewLibrary) -> None:
            self._library = library
            self._stream: Any = None

        def __enter__(self) -> LimitedImagePreviewLibraryIndexV1:
            lock = self._library._root / _LOCK_NAME
            if lock.is_symlink():
                raise LimitedImagePreviewLibraryError(
                    "unsafe_library_path",
                    "preview index lock cannot be a symlink",
                )
            self._stream = lock.open("a+b")
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX)
            return self._library._read_index()

        def __exit__(self, *_: object) -> None:
            assert self._stream is not None
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            self._stream.close()

    def _locked_index(self) -> LimitedImagePreviewLibrary._IndexLock:
        return self._IndexLock(self)


def admit_limited_image_preview(
    *,
    artifact_store: LocalArtifactStore,
    compiler: RevisedImage2EnvGenesisCompiler,
    manifest: ArtifactRef,
) -> VerifiedLimitedImagePreview:
    """Deeply verify a limited preview using only its CAS identities."""

    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("preview admission requires exact LocalArtifactStore")
    _require_compiler(artifact_store, compiler)
    try:
        checked = _read_model(
            artifact_store,
            manifest,
            LimitedImagePreviewManifestV1,
            "preview manifest",
        )
        source_media = _read_model(
            artifact_store,
            checked.source_media,
            ImageSourceMedia,
            "image source media",
        )
        query = _read_model(
            artifact_store,
            checked.asset_query,
            SceneInterpretationProposalV1,
            "asset query",
        )
        representation = _read_model(
            artifact_store,
            checked.asset_representation,
            AssetRepresentationV1,
            "asset representation",
        )
        qualification = _read_model(
            artifact_store,
            checked.asset_qualification,
            GenesisAssetQualificationV1,
            "asset qualification",
        )
        _resolve(artifact_store, checked.raw_image, "raw image")
        _resolve(artifact_store, source_media.canonical_image, "canonical image")
        _read_json(artifact_store, source_media.probe, "image media probe")
        prompt_path = _resolve(artifact_store, checked.input_text, "input text")
        prompt_path.read_bytes().decode("utf-8", errors="strict")
        if (
            source_media.source != checked.raw_image
            or query.source_media_sha256 != checked.raw_image.sha256
            or query.effective_prompt != checked.input_text
        ):
            raise LimitedImagePreviewLibraryError(
                "input_binding_mismatch",
                "asset query differs from the exact raw image and prompt",
            )
        if (
            qualification.representation != checked.asset_representation
            or qualification.subject.model_subject != representation.subject
            or qualification.subject.representation_sha256 != representation.representation_sha256
            or qualification.subject.loader_closure_sha256 != representation.loader_closure_sha256
            or qualification.requested_profile.value != checked.requested_profile
        ):
            raise LimitedImagePreviewLibraryError(
                "asset_binding_mismatch",
                "asset qualification differs from the selected representation or profile",
            )
        _read_model(
            artifact_store,
            qualification.runtime_lock,
            GenesisRuntimeLockV1,
            "asset runtime lock",
        )
        for member in representation.members:
            _resolve(artifact_store, member.artifact, f"asset member {member.logical_path}")
        for check in qualification.checks:
            for evidence in check.evidence:
                _resolve(artifact_store, evidence, f"asset qualification check {check.code}")
        _verify_stage(
            artifact_store,
            compiler,
            checked,
            source_media,
            checked.initial_stage,
        )
        if checked.revised_stage is not None:
            _verify_stage(
                artifact_store,
                compiler,
                checked,
                source_media,
                checked.revised_stage,
            )
        return VerifiedLimitedImagePreview(
            artifact_root=artifact_store.root,
            compiler=compiler,
            manifest=checked,
            manifest_ref=manifest,
            _token=_CAPABILITY_TOKEN,
        )
    except LimitedImagePreviewLibraryError:
        raise
    except (ArtifactResolutionError, UnicodeDecodeError, ValidationError, ValueError) as error:
        raise LimitedImagePreviewLibraryError(
            "preview_admission_failed",
            f"limited preview admission failed: {error}",
        ) from error


def create_limited_image_preview_library(
    *,
    artifact_store: LocalArtifactStore,
    compiler: RevisedImage2EnvGenesisCompiler,
    root: Path,
    project_id: str,
) -> LimitedImagePreviewLibrary:
    """Create one empty isolated project index."""

    store, library_root = _library_inputs(artifact_store, compiler, root)
    library_root.mkdir(parents=True, exist_ok=True)
    index_path = library_root / _INDEX_NAME
    if index_path.exists() or index_path.is_symlink():
        raise LimitedImagePreviewLibraryError(
            "library_exists",
            f"preview library already exists: {library_root}",
        )
    index = _new_index(project_id, ())
    _write_index(index_path, index)
    return LimitedImagePreviewLibrary(
        artifact_store=store,
        compiler=compiler,
        root=library_root,
        project_id=project_id,
        _token=_LIBRARY_TOKEN,
    )


def load_limited_image_preview_library(
    *,
    artifact_store: LocalArtifactStore,
    compiler: RevisedImage2EnvGenesisCompiler,
    root: Path,
    project_id: str,
) -> LimitedImagePreviewLibrary:
    """Load an existing index after its exact canonical identity is verified."""

    store, library_root = _library_inputs(artifact_store, compiler, root)
    _read_index(library_root / _INDEX_NAME, project_id)
    return LimitedImagePreviewLibrary(
        artifact_store=store,
        compiler=compiler,
        root=library_root,
        project_id=project_id,
        _token=_LIBRARY_TOKEN,
    )


def _verify_stage(
    store: LocalArtifactStore,
    compiler: RevisedImage2EnvGenesisCompiler,
    manifest: LimitedImagePreviewManifestV1,
    source_media: ImageSourceMedia,
    stage: LimitedImagePreviewStageV1,
) -> None:
    for ref, label in (
        (stage.compile_request, "compile request"),
        (stage.compile_receipt, "compile receipt"),
        (stage.replay_receipt, "replay receipt"),
        (stage.replay, "replay evidence"),
        (stage.fresh_observation, "fresh observation"),
        (stage.workflow_head, "workflow head"),
    ):
        _read_json(store, ref, label)
    runtime = _read_model(
        store,
        stage.runtime_manifest,
        GenesisRuntimeManifestV1,
        "runtime manifest",
    )
    for member in runtime.members:
        _resolve(store, member.artifact, f"runtime member {member.root}/{member.path}")
    if stage.prompt_revision is not None:
        revision = _read_model(store, stage.prompt_revision, PromptRevisionV3, "prompt revision")
        initial = manifest.initial_stage
        proposal = revision.layout_proposal
        if (
            revision.workflow_run_id != manifest.workflow_run_id
            or revision.principal_id != manifest.principal_id
            or revision.source_prompt != manifest.input_text
            or initial.workflow_head not in revision.failure_receipts
            or revision.predecessor_environment
            not in {initial.compile_receipt, initial.replay_receipt}
            or proposal.position_xy_m
            != (manifest.requested_layout.x_m, manifest.requested_layout.y_m)
            or proposal.yaw_rad != manifest.requested_layout.yaw_rad
        ):
            raise LimitedImagePreviewLibraryError(
                "prompt_revision_mismatch",
                "PromptRevisionV3 differs from the exact workflow failure and requested layout",
            )
    for ref, label in (
        (stage.baseline_preview, "baseline preview"),
        (stage.half_dt_preview, "half-dt preview"),
    ):
        _resolve(store, ref, label)
    selected_compiler = compiler.predecessor_compiler if stage.phase == "initial" else compiler
    recalculated = validate_preview_receipt(
        store,
        selected_compiler,
        LimitedSceneValidateInputV1(
            schema_version="harness.limited_scene_validate_input.v1",
            execution_receipt=stage.replay_receipt,
            expected_layout=stage.validation_result.expected_layout,
        ),
    )
    if recalculated != stage.validation_result:
        raise LimitedImagePreviewLibraryError(
            "validation_recalculation_mismatch",
            "stored limited validation differs from the deep native replay verifier",
        )
    receipt_model = (
        GenesisSceneExecutionReceiptV2
        if stage.phase == "initial"
        else GenesisSceneExecutionReceiptV3
    )
    request_model = (
        GenesisSceneExecutionRequestV2
        if stage.phase == "initial"
        else GenesisSceneExecutionRequestV3
    )
    compile_model = (
        Image2EnvGenesisCompileReceiptV1
        if stage.phase == "initial"
        else Image2EnvGenesisCompileReceiptV2
    )
    compile_request_model = (
        Image2EnvGenesisCompileRequestV1
        if stage.phase == "initial"
        else Image2EnvGenesisCompileRequestV2
    )
    receipt = _read_model(store, stage.replay_receipt, receipt_model, "replay receipt")
    request = _read_model(store, receipt.request, request_model, "replay request")
    compiled = _read_model(store, stage.compile_receipt, compile_model, "compile receipt")
    compile_request = _read_model(
        store,
        stage.compile_request,
        compile_request_model,
        "compile request",
    )
    if (
        request.compile_receipt != stage.compile_receipt
        or request.scene != stage.scene
        or request.runtime_manifest != stage.runtime_manifest
        or request.asset_qualification != manifest.asset_qualification
        or compiled.request != stage.compile_request
        or compiled.scene != stage.scene
        or compiled.qualification != manifest.asset_qualification
        or receipt.replay != stage.replay
        or receipt.report != stage.replay_report
        or receipt.baseline_preview != stage.baseline_preview
        or receipt.half_dt_preview != stage.half_dt_preview
        or receipt.retained_index != stage.retained_media_index
    ):
        raise LimitedImagePreviewLibraryError(
            "native_stage_binding_mismatch",
            "preview stage differs from its deeply verified compile/replay receipt",
        )
    scene = _read_model(store, stage.scene, GenesisSceneSpecV1, "scene")
    report = _read_model(store, stage.replay_report, GenesisSceneReportV1, "replay report")
    decision = _read_model(
        store,
        stage.validation,
        type(stage.validation_result),
        "preview validation",
    )
    if decision != stage.validation_result:
        raise LimitedImagePreviewLibraryError(
            "validation_binding_mismatch",
            "inline validation differs from its immutable artifact",
        )
    expected_scene_source = (
        compile_request.source_scene if stage.phase == "initial" else compiled.derived_source_scene
    )
    if (
        compile_request.compile_input.source_media != source_media
        or source_media.source != manifest.raw_image
    ):
        raise LimitedImagePreviewLibraryError(
            "input_binding_mismatch",
            "native compile input differs from the admitted image source media",
        )
    if (
        scene.source != expected_scene_source
        or scene.representation != manifest.asset_representation
    ):
        raise LimitedImagePreviewLibraryError(
            "scene_binding_mismatch",
            "scene differs from the typed native source envelope or representation",
        )
    _verify_layout(scene, decision)
    if report.status != "passed":
        raise LimitedImagePreviewLibraryError(
            "replay_not_passed",
            "replay report did not pass",
        )
    head = _read_json(store, stage.workflow_head, "workflow head")
    if (
        head.get("workflow_run_id") != str(manifest.workflow_run_id)
        or head.get("principal_id") != manifest.principal_id
    ):
        raise LimitedImagePreviewLibraryError(
            "workflow_binding_mismatch",
            "stage workflow head belongs to another workflow or principal",
        )


def _verify_layout(scene: GenesisSceneSpecV1, decision: BaseModel) -> None:
    actual_xy = scene.position_m[:2]
    w, x, y, z = scene.orientation_wxyz
    actual_yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    actual = decision.actual_layout
    if (
        abs(actual_xy[0] - actual.x_m) > 1e-9
        or abs(actual_xy[1] - actual.y_m) > 1e-9
        or abs(
            math.atan2(
                math.sin(actual_yaw - actual.yaw_rad),
                math.cos(actual_yaw - actual.yaw_rad),
            )
        )
        > 1e-9
    ):
        raise LimitedImagePreviewLibraryError(
            "requested_layout_mismatch",
            "scene position/yaw differs from the allowed requested-layout fields",
        )
    expected = decision.expected_layout
    if expected is None:
        if decision.semantic_status != "pass" or decision.status != "passed":
            raise LimitedImagePreviewLibraryError(
                "semantic_validation_mismatch",
                "source-layout preservation must pass the semantic gate",
            )
        return
    position_delta = math.hypot(actual.x_m - expected.x_m, actual.y_m - expected.y_m)
    yaw_delta = abs(
        math.atan2(
            math.sin(actual.yaw_rad - expected.yaw_rad),
            math.cos(actual.yaw_rad - expected.yaw_rad),
        )
    )
    passed = position_delta <= decision.position_tolerance_m and yaw_delta <= (
        decision.yaw_tolerance_rad
    )
    if passed != (decision.semantic_status == "pass"):
        raise LimitedImagePreviewLibraryError(
            "semantic_validation_mismatch",
            "limited validation status differs from the measured scene layout",
        )


def _read_model(
    store: LocalArtifactStore,
    ref: ArtifactRef,
    model: type[BaseModel],
    label: str,
) -> Any:
    payload = _read_json_bytes(store, ref, label)
    value = model.model_validate_json(payload)
    if ref.schema_version != getattr(value, "schema_version", None):
        raise LimitedImagePreviewLibraryError(
            "schema_binding_mismatch",
            f"{label} schema differs from its ArtifactRef",
        )
    if payload not in _canonical_variants(value):
        raise LimitedImagePreviewLibraryError(
            "noncanonical_json",
            f"{label} is not canonical JSON",
        )
    return value


def _read_json(store: LocalArtifactStore, ref: ArtifactRef, label: str) -> dict[str, Any]:
    payload = _read_json_bytes(store, ref, label)
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("schema_version") != ref.schema_version:
        raise LimitedImagePreviewLibraryError(
            "schema_binding_mismatch",
            f"{label} schema differs from its ArtifactRef",
        )
    if payload not in _canonical_variants(value):
        raise LimitedImagePreviewLibraryError(
            "noncanonical_json",
            f"{label} is not canonical JSON",
        )
    return value


def _read_json_bytes(store: LocalArtifactStore, ref: ArtifactRef, label: str) -> bytes:
    if (
        ref.uri != f"artifact://sha256/{ref.sha256}"
        or ref.media_type != "application/json"
        or ref.schema_version is None
    ):
        raise LimitedImagePreviewLibraryError(
            "reference_type_mismatch",
            f"{label} must be exact typed CAS JSON",
        )
    return _resolve(store, ref, label).read_bytes()


def _resolve(store: LocalArtifactStore, ref: ArtifactRef, label: str) -> Path:
    if ref.uri != f"artifact://sha256/{ref.sha256}":
        raise LimitedImagePreviewLibraryError(
            "non_cas_reference",
            f"{label} must use its exact CAS URI",
        )
    try:
        return store.resolve(ref).path
    except ArtifactResolutionError as error:
        raise LimitedImagePreviewLibraryError(
            "artifact_unavailable",
            f"{label} is unavailable: {error}",
        ) from error


def _library_inputs(
    artifact_store: LocalArtifactStore,
    compiler: RevisedImage2EnvGenesisCompiler,
    root: Path,
) -> tuple[LocalArtifactStore, Path]:
    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("preview library requires exact LocalArtifactStore")
    _require_compiler(artifact_store, compiler)
    candidate = root.expanduser()
    if candidate.is_symlink():
        raise LimitedImagePreviewLibraryError(
            "unsafe_library_path",
            "preview library root cannot be a symlink",
        )
    return artifact_store, candidate.resolve()


def _require_compiler(
    artifact_store: LocalArtifactStore,
    compiler: RevisedImage2EnvGenesisCompiler,
) -> None:
    if type(compiler) is not RevisedImage2EnvGenesisCompiler:
        raise TypeError("preview admission requires exact revised compiler binding")
    if (
        compiler.artifact_store is not artifact_store
        or compiler.predecessor_compiler.artifact_store is not artifact_store
    ):
        raise LimitedImagePreviewLibraryError(
            "compiler_store_mismatch",
            "preview compiler and both native replay verifiers require the exact CAS instance",
        )


def _read_index(path: Path, project_id: str) -> LimitedImagePreviewLibraryIndexV1:
    if path.is_symlink():
        raise LimitedImagePreviewLibraryError(
            "unsafe_library_path",
            "preview project index cannot be a symlink",
        )
    try:
        payload = path.read_bytes()
        index = LimitedImagePreviewLibraryIndexV1.model_validate_json(payload)
    except (OSError, ValidationError, ValueError) as error:
        raise LimitedImagePreviewLibraryError(
            "index_invalid",
            f"preview project index is unavailable or invalid: {error}",
        ) from error
    if payload != _canonical(index, newline=True) or index.project_id != project_id:
        raise LimitedImagePreviewLibraryError(
            "index_invalid",
            "preview project index is noncanonical or belongs to another project",
        )
    return index


def _write_index(path: Path, index: LimitedImagePreviewLibraryIndexV1) -> None:
    if path.is_symlink():
        raise LimitedImagePreviewLibraryError(
            "unsafe_library_path",
            "preview project index cannot be a symlink",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".preview-index.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical(index, newline=True))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o444)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as error:
        raise LimitedImagePreviewLibraryError(
            "index_write_failed",
            f"preview project index could not be written: {error}",
        ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _entry(preview: VerifiedLimitedImagePreview) -> LimitedImagePreviewIndexEntryV1:
    manifest = preview.manifest
    final_stage = manifest.revised_stage or manifest.initial_stage
    return LimitedImagePreviewIndexEntryV1(
        preview_id=manifest.preview_id,
        manifest=preview.manifest_ref,
        mode=manifest.mode,
        final_scene_sha256=final_stage.scene.sha256,
    )


def _new_index(
    project_id: str,
    entries: tuple[LimitedImagePreviewIndexEntryV1, ...],
) -> LimitedImagePreviewLibraryIndexV1:
    data = {
        "schema_version": LIMITED_IMAGE_PREVIEW_LIBRARY_INDEX_SCHEMA_ID,
        "index_sha256": "0" * 64,
        "project_id": project_id,
        "revision": len(entries),
        "entries": entries,
    }
    data["index_sha256"] = limited_image_preview_library_index_sha256(data)
    return LimitedImagePreviewLibraryIndexV1.model_validate(data)


def _published(entry: LimitedImagePreviewIndexEntryV1) -> PublishedLimitedImagePreview:
    return PublishedLimitedImagePreview(
        preview_id=entry.preview_id,
        manifest=entry.manifest,
        mode=entry.mode,
        final_scene_sha256=entry.final_scene_sha256,
    )


def _materialized_members(
    store: LocalArtifactStore,
    manifest: LimitedImagePreviewManifestV1,
    manifest_ref: ArtifactRef,
) -> tuple[tuple[Path, ArtifactRef], ...]:
    representation = _read_model(
        store,
        manifest.asset_representation,
        AssetRepresentationV1,
        "asset representation",
    )
    qualification = _read_model(
        store,
        manifest.asset_qualification,
        GenesisAssetQualificationV1,
        "asset qualification",
    )
    image_suffix = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }.get(manifest.raw_image.media_type, ".image")
    members: list[tuple[Path, ArtifactRef]] = [
        (Path("manifest.json"), manifest_ref),
        (Path(f"inputs/raw-image{image_suffix}"), manifest.raw_image),
        (Path("inputs/prompt.txt"), manifest.input_text),
        (Path("inputs/source-media.json"), manifest.source_media),
        (Path("inputs/asset-query.json"), manifest.asset_query),
        (Path("asset/representation.json"), manifest.asset_representation),
        (Path("asset/qualification.json"), manifest.asset_qualification),
        (Path("asset/runtime-lock.json"), qualification.runtime_lock),
    ]
    for member in representation.members:
        relative = _safe_member_path(member.logical_path)
        members.append((Path("asset/members") / relative, member.artifact))
    for stage in (manifest.initial_stage, manifest.revised_stage):
        if stage is None:
            continue
        prefix = Path("stages") / stage.phase
        stage_members = (
            ("compile-request.json", stage.compile_request),
            ("compile-receipt.json", stage.compile_receipt),
            ("scene.json", stage.scene),
            ("runtime-manifest.json", stage.runtime_manifest),
            ("replay-receipt.json", stage.replay_receipt),
            ("replay.json", stage.replay),
            ("replay-report.json", stage.replay_report),
            ("baseline.mp4", stage.baseline_preview),
            ("half-dt.mp4", stage.half_dt_preview),
            ("retained-media-index.json", stage.retained_media_index),
            ("limited-validation.json", stage.validation),
            ("workflow-head.json", stage.workflow_head),
        )
        members.extend((prefix / name, ref) for name, ref in stage_members)
        if stage.fresh_observation is not None:
            members.append((prefix / "fresh-observation.json", stage.fresh_observation))
        if stage.prompt_revision is not None:
            members.append((prefix / "prompt-revision.json", stage.prompt_revision))
    paths = tuple(relative.as_posix() for relative, _ in members)
    if len(paths) != len(set(paths)):
        raise LimitedImagePreviewLibraryError(
            "materialization_collision",
            "preview materialization paths are not unique",
        )
    return tuple(members)


def _safe_member_path(value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value or "\\" in value:
        raise LimitedImagePreviewLibraryError(
            "unsafe_member_path",
            f"asset member path is unsafe: {value}",
        )
    return Path(*path.parts)


def _canonical_variants(value: object) -> tuple[bytes, bytes]:
    body = _canonical(value, newline=False)
    return body, body + b"\n"


def _canonical(value: object, *, newline: bool) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return payload + (b"\n" if newline else b"")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "LimitedImagePreviewLibrary",
    "LimitedImagePreviewLibraryError",
    "PublishedLimitedImagePreview",
    "VerifiedLimitedImagePreview",
    "admit_limited_image_preview",
    "create_limited_image_preview_library",
    "load_limited_image_preview_library",
]
