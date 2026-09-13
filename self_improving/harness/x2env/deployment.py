"""Trusted deployment assembly, not model policy or another workflow authority.

Adapters may be disabled here; request source order remains the canonical contract.
Dependencies are checked when used so status/failure packaging remain available.
"""

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .compile import StructuralPolicy
from .contracts import ArtifactRef, Model, Sha256
from .grounding import SceneDesignPolicy
from .source_identity import SourceIdentityPolicy


class CodexConfig(Model):
    executable: str
    sha256: Sha256
    model: Literal["gpt-6-astra"] = "gpt-6-astra"

    @model_validator(mode="after")
    def absolute_executable(self):
        if not Path(self.executable).is_absolute():
            raise ValueError("absolute Codex executable required")
        return self


def _scoped(value):
    path = Path(value)
    if not path.is_absolute() or path == Path("/"):
        raise ValueError("path must be absolute and scoped")
    return value


class RuntimeRoots(Model):
    interpreter: str
    stdlib: str
    distributions: str
    native: str
    genesis: str

    @model_validator(mode="after")
    def scoped(self):
        for value in self.model_dump().values():
            _scoped(value)
        return self


class GenesisConfig(Model):
    runtime_roots: RuntimeRoots
    denied_roots: tuple[str, ...] = ()
    source_identity: SourceIdentityPolicy | None = None

    @model_validator(mode="after")
    def scoped(self):
        for value in self.denied_roots:
            _scoped(value)
        return self


class PinnedFile(Model):
    path: str
    sha256: Sha256

    @model_validator(mode="after")
    def scoped(self):
        _scoped(self.path)
        return self

    def read(self):
        path = Path(self.path)
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("symbolic deployment config")
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("oversized pinned config")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != self.sha256:
            raise ValueError("deployment_pin_mismatch")
        return data


class WebConfig(Model):
    provider_config: PinnedFile
    license_records: PinnedFile | None = None


class ReconstructionConfig(Model):
    source_root: str
    source_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    python: str
    python_sha256: Sha256
    segmentation_runtime: PinnedFile
    reconstruction_runtime: PinnedFile
    model_refs: dict[str, str]
    derivation_authorization: ArtifactRef | None = None

    @model_validator(mode="after")
    def scoped(self):
        _scoped(self.source_root)
        _scoped(self.python)
        return self


class Deployment(Model):
    schema_version: Literal["x2env.deployment.v1"] = "x2env.deployment.v1"
    state_dir: str
    codex: CodexConfig | None = None
    compile_policy: StructuralPolicy | None = None
    scene_design_policy: SceneDesignPolicy = SceneDesignPolicy()
    local_enabled: bool = True
    genesis: GenesisConfig | None = None
    web: WebConfig | None = None
    reconstruction: ReconstructionConfig | None = None
    timeout_seconds: int = Field(default=1770, ge=1, le=1770)

    @model_validator(mode="after")
    def safe_state(self):
        path = Path(self.state_dir)
        if (
            not path.is_absolute()
            or path == Path("/")
            or any(p.is_symlink() for p in (path, *path.parents))
        ):
            raise ValueError("state directory must be absolute non-symbolic and scoped")
        return self


def load_deployment(path) -> Deployment:
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("deployment path must be absolute nonsymbolic")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("deployment file exceeds limit")
    return Deployment.model_validate_json(path.read_bytes())


def build_harness(config):
    from .harness import Harness

    config = Deployment.model_validate(config)
    backend = None
    if config.codex:
        from .codex import CodexBackend

        def backend(store):
            vocabulary, context_error = (), None
            try:
                vocabulary = store.asset_categories()
            except ValueError as exc:
                if str(exc) != "catalog_category_limit":
                    raise
                # Naming advice is optional for all sources, unlike resolver acceptance.
                # Preserve this explicit unavailable snapshot; it is not an empty catalog.
                context_error = "catalog_category_limit"
            return CodexBackend(
                Path(config.codex.executable),
                config.codex.sha256,
                config.codex.model,
                store,
                local_category_vocabulary=vocabulary,
                local_category_context_error=context_error,
            )

    replay = preview = None
    if config.genesis:
        from .replay import GenesisReplayExecutor

        def replay(store):
            return GenesisReplayExecutor(
                store,
                runtime_roots=config.genesis.runtime_roots.model_dump(),
                denied_roots=config.genesis.denied_roots,
            )

        def preview(store, output_root, seed):
            from .asset_preview import AssetPreviewRenderer

            return AssetPreviewRenderer(
                store,
                config.genesis.runtime_roots.model_dump(),
                output_root,
                seed,
                denied_roots=config.genesis.denied_roots,
                source_identity_policy=config.genesis.source_identity,
            ).render

    return Harness(
        Path(config.state_dir),
        scene_design_policy=config.scene_design_policy,
        backend_factory=backend,
        compile_policy=config.compile_policy,
        replay_factory=replay,
        asset_preview_factory=preview,
        contextual_resolver_factory=lambda store, backend, request, bundle: _RequestResolver(
            config, store, backend, request, bundle
        ),
    )


class _RequestResolver:
    """Bind request context only; SourceRouter and Harness retain all execution authority."""

    def __init__(self, config, store, backend, request, bundle):
        self.config, self.store, self.backend, self.request, self.bundle = (
            config,
            store,
            backend,
            request,
            bundle,
        )

    def resolve(self, scene_ir, **kwargs):
        return self._build_router(scene_ir, **kwargs).resolve(scene_ir, **kwargs)

    def resume_after_local_repairs(self, original_resolution_ref, repair_result_refs, **kwargs):
        from .resolver import ResolutionResult

        original = ResolutionResult.model_validate_json(
            self.store.read_artifact(original_resolution_ref)
        )
        return self._build_router(original.resolved.scene_ir, **kwargs).resume_after_local_repairs(
            original_resolution_ref, repair_result_refs, **kwargs
        )

    def _build_router(self, scene_ir, **kwargs):
        from .asset_preview import AssetPreviewRenderer
        from .assets import AssetRegistry
        from .resolver import LocalAssetResolver
        from .source_router import SourceRouter

        config = self.config
        root = Path(kwargs["output_root"])
        deadline = time.monotonic() + kwargs.get("timeout", 600)
        registry = AssetRegistry(self.store)
        preview = None
        if config.genesis:
            preview = AssetPreviewRenderer(
                self.store,
                config.genesis.runtime_roots.model_dump(),
                root / "previews",
                self.request.seed,
                denied_roots=config.genesis.denied_roots,
                source_identity_policy=config.genesis.source_identity,
            ).render

        def remaining():
            value = int(deadline - time.monotonic())
            if value < 1:
                raise ValueError("resolver_timeout")
            return min(600, value)

        local = (
            LocalAssetResolver(self.store, registry, self.backend, preview)
            if config.local_enabled
            else None
        )

        def web_factory():
            from .adapters.yuxin import YuxinProviderAdapter
            from .search_advisory import plan_search
            from .web_resolver import WebAssetResolver

            provider_config = json.loads(config.web.provider_config.read())
            license_records = None
            if config.web.license_records:
                config.web.license_records.read()
                license_records = Path(config.web.license_records.path)
            provider = YuxinProviderAdapter(provider_config, self.store, license_records)

            def prepare(entity, candidate, fetched):
                if self.backend is None:
                    raise FileNotFoundError("managed_codex_asset_preparation")
                return self.backend.prepare_asset(
                    scene_ir,
                    entity,
                    candidate,
                    fetched,
                    output_root=root / "preparation" / str(uuid.uuid4()),
                    timeout=remaining(),
                )

            def query(entity):
                return plan_search(
                    self.backend,
                    scene_ir,
                    entity,
                    store=self.store,
                    output_root=root / "search-advisory" / str(uuid.uuid4()),
                    timeout=remaining(),
                )

            return WebAssetResolver(
                self.store, registry, provider, self.backend, preview, prepare, query_port=query
            )

        web = _ConfiguredSource(self.store, web_factory) if config.web else None

        def reconstruction_factory():
            from .adapters.reconstruction import ReconstructionAdapter, ReconstructionDeployment
            from .reconstruction_resolver import ReconstructionResolver

            settings = config.reconstruction
            if not self.bundle.images and self.bundle.video is None:
                raise ValueError("missing_reconstruction_image")
            if settings.derivation_authorization is None:
                raise ValueError("blocked_derivation_authorization")
            self.store.read_artifact(settings.derivation_authorization)
            settings.segmentation_runtime.read()
            settings.reconstruction_runtime.read()
            adapter = ReconstructionAdapter(
                ReconstructionDeployment(
                    source_root=Path(settings.source_root),
                    source_commit=settings.source_commit,
                    python=Path(settings.python),
                    python_sha256=settings.python_sha256,
                    segmentation_runtime=Path(settings.segmentation_runtime.path),
                    segmentation_runtime_sha256=settings.segmentation_runtime.sha256,
                    reconstruction_runtime=Path(settings.reconstruction_runtime.path),
                    reconstruction_runtime_sha256=settings.reconstruction_runtime.sha256,
                    model_refs=settings.model_refs,
                    derivation_authorization=settings.derivation_authorization,
                ),
                self.store,
            )
            return ReconstructionResolver(
                self.store,
                registry,
                self.backend,
                adapter,
                preview,
                lambda entity: select_reconstruction_image(
                    self.store,
                    self.bundle,
                    scene_ir,
                    entity.id,
                    output_root=root / "media-selection" / str(uuid.uuid4()),
                    timeout=remaining(),
                ),
                seed=self.request.seed,
            )

        reconstruction = (
            _ConfiguredSource(self.store, reconstruction_factory) if config.reconstruction else None
        )
        return SourceRouter(self.store, local=local, web=web, reconstruction=reconstruction)


def select_reconstruction_image(store, bundle, scene_ir, entity_id, *, output_root, timeout=60):
    """Select original canonical pixels, never a previous render or a hand-authored mask.

    Images take precedence; video uses decoded frame zero verified against ingest's
    full sequence. Entity segmentation remains the managed model's subsequent job.
    """
    import subprocess
    from io import BytesIO

    from PIL import Image

    from .reconstruction_resolver import ReconstructionImage

    evidence = {
        "scene_ir": scene_ir.model_dump(mode="json"),
        "entity_id": entity_id,
        "request_sha256": bundle.request_sha256,
    }
    if bundle.images:
        item = bundle.images[0]
        image = item.canonical
        store.read_artifact(image)
        evidence.update(
            selection_basis="first_canonical_input_image",
            image_index=0,
            source_ref=item.source.model_dump(mode="json"),
        )
    elif bundle.video:
        video = bundle.video
        sequence = json.loads(store.read_artifact(video.sequence))
        if sequence.get("full_decode") is not True or sequence.get(
            "source"
        ) != video.source.model_dump(mode="json"):
            raise ValueError("unbound_video_sequence")
        frame = sequence["frames"][0]
        if frame["index"] != 0 or len(sequence["frames"]) != video.frame_count:
            raise ValueError("incomplete_video_sequence")
        root = Path(output_root)
        if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError("unsafe frame extraction root")
        root.mkdir(parents=True, exist_ok=False)
        source = root / "input.video"
        source.write_bytes(store.read_artifact(video.source))
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        (root / "command.json").write_text(json.dumps(command))
        try:
            with (root / "stderr.log").open("wb") as err:
                process = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=err,
                    timeout=min(timeout, 60),
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            (root / "failure.json").write_text(json.dumps({"error_code": "frame_decode_timeout"}))
            raise ValueError("frame_decode_timeout") from exc
        raw = process.stdout
        if (
            process.returncode != 0
            or len(raw) != video.width * video.height * 3
            or len(raw) != frame["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != frame["sha256"]
        ):
            raise ValueError("decoded_video_frame_mismatch")
        png = BytesIO()
        Image.frombytes("RGB", (video.width, video.height), raw).save(png, format="PNG")
        image = store.write_artifact(png.getvalue(), "image/png")
        evidence.update(
            selection_basis="first_verified_decoded_video_frame",
            frame_index=0,
            frame_sha256=frame["sha256"],
            sequence_ref=video.sequence.model_dump(mode="json"),
            source_ref=video.source.model_dump(mode="json"),
            full_frame_count=video.frame_count,
            decoder_log=store.write_artifact(
                (root / "stderr.log").read_bytes(), "text/plain"
            ).model_dump(mode="json"),
        )
    else:
        return None
    evidence["image_ref"] = image.model_dump(mode="json")
    return ReconstructionImage(
        image=image,
        provenance=store.write_artifact(json.dumps(evidence).encode(), "application/json"),
    )


class _ConfiguredSource:
    """Defer an enabled source's dependency checks until SourceRouter selects it."""

    def __init__(self, store, factory):
        self.store, self.factory = store, factory

    def resolve(self, scene_ir, **kwargs):
        try:
            resolver = self.factory()
        except (FileNotFoundError, ImportError, ValueError) as exc:
            from .compile import ResolvedAssetSet
            from .resolver import ResolutionResult

            code = (
                "blocked_external_resource"
                if isinstance(exc, (FileNotFoundError, ImportError))
                else str(exc)
            )
            root = Path(kwargs["output_root"])
            root.mkdir(parents=True, exist_ok=True)
            raw = json.dumps(
                {
                    "status": "blocked",
                    "error_code": code,
                    "resource": str(exc),
                    "scene_ir": scene_ir.model_dump(),
                    "phase": "deployment_adapter",
                }
            ).encode()
            (root / "deployment-error.json").write_bytes(raw)
            return ResolutionResult(
                status="blocked",
                error_code=code,
                resolved=ResolvedAssetSet(scene_ir=scene_ir, assets=()),
                receipt=self.store.write_artifact(raw, "application/json"),
                required_resources=(str(exc),),
            )
        return resolver.resolve(scene_ir, **kwargs)
