"""Raw-input construction for the explicitly selected, mouse-only preview.

These records bind inputs, not authority. The service must obtain the lookup ref
from this workflow's audited journal before build_initial; the registered compiler
still verifies durable asset qualification. No natural-language or image inference
is performed here, and no repair interpretation or placement is manufactured.
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .artifacts import LocalArtifactStore
from .gujie_scene_adapter import adapt_gujie_tabletop_scene, read_gujie_tabletop_scene
from .image_ingest import ImageMediaConsumerVerifier, ImageMediaIngestor
from .schemas.common import ArtifactRef
from .schemas.image2env_genesis_compile import Image2EnvGenesisCompileRequestV1
from .schemas.x2env import Image2EnvCompileV2Input, ImageSourceMedia
from .schemas.x2env_compile_evidence import (
    PortableAssetCatalogV1,
    SceneInterpretationProposalV1,
    canonical_evidence_sha256,
)


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _Input(_Record):
    kind: Literal["limited_preview_input.v1"]
    workflow_id: UUID
    source_media: ImageSourceMedia
    effective_prompt: ArtifactRef
    catalog_ref: ArtifactRef


class _Lookup(_Record):
    kind: Literal["limited_preview_lookup.v1"]
    workflow_id: UUID
    input_ref: ArtifactRef
    catalog_ref: ArtifactRef
    query: Literal["mouse"]
    asset_id: str
    representation: ArtifactRef
    qualification: ArtifactRef
    source_scene: ArtifactRef


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _mouse_entry(entry):
    # Exact compatibility identity; never substring or category similarity.
    return entry.semantic_name == "mouse" or (
        entry.semantic_name,
        entry.subject.namespace,
        entry.subject.asset_key,
        entry.subject.ledger_asset_id,
        entry.subject.model_id,
        entry.subject.source_record_sha256,
        entry.representation_sha256,
    ) == (
        "pink_mouse",
        "gujie.legacy",
        "simfoundry_pink_mouse_7915827ae4309660",
        "mouse_001",
        0,
        "a7d20cc64d171f1a720fdeefe0d38130a4d85eef3b90b9300241bc790592f21c",
        "5c35efde1c50cdeee04b3529abe5f136b48eaa592a0cf037ac45fe13bad85564",
    )


class LimitedImagePreviewInputs:
    """Build initial exact-reuse requests; never executes or qualifies an asset."""

    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        *,
        asset_catalog: ArtifactRef,
        source_scene: ArtifactRef,
        qualification_run_id: UUID,
        table_center_m: tuple[float, float, float],
        table_size_m: tuple[float, float, float],
        table_friction: float,
        seed: int = 0,
    ):
        self.store = artifact_store
        self.asset_catalog = asset_catalog
        self.source_scene = source_scene
        self.qualification_run_id = qualification_run_id
        self.table_center_m = table_center_m
        self.table_size_m = table_size_m
        self.table_friction = table_friction
        self.seed = seed
        self._catalog()

    def _publish(self, value, *, name, schema=None, media="application/json"):
        data = value if isinstance(value, bytes) else _canonical(value)
        with TemporaryDirectory(prefix="preview-input-") as temporary:
            path = Path(temporary) / "payload"
            path.write_bytes(data)
            return self.store.put_file(path, name=name, media_type=media, schema_version=schema)

    def _read(self, ref, model=None, schema=None):
        if (
            ref.uri != f"artifact://sha256/{ref.sha256}"
            or ref.media_type != "application/json"
            or ref.schema_version != schema
        ):
            raise ValueError("input record requires exact typed CAS JSON")
        raw = self.store.resolve(ref).path.read_bytes()
        value = json.loads(raw)
        if raw not in (_canonical(value), _canonical(value) + b"\n"):
            raise ValueError("input JSON is not canonical")
        return model.model_validate_json(raw) if model else value

    def _catalog(self):
        return self._read(
            self.asset_catalog, PortableAssetCatalogV1, "harness.portable_asset_catalog.v1"
        )

    def start(
        self, *, workflow_id: UUID, raw_image_ref: ArtifactRef, user_text: str
    ) -> ArtifactRef:
        if type(workflow_id) is not UUID or type(user_text) is not str or not user_text.strip():
            raise ValueError("start requires workflow UUID and nonempty original text")
        self._catalog()
        media = ImageMediaIngestor(self.store, 20_000_000, 20_000_000).ingest(raw_image_ref)
        prompt = self._publish(
            user_text.encode("utf-8"),
            name="preview-user-prompt.txt",
            schema="harness.prompt_text.v1",
            media="text/plain",
        )
        record = _Input(
            kind="limited_preview_input.v1",
            workflow_id=workflow_id,
            source_media=media,
            effective_prompt=prompt,
            catalog_ref=self.asset_catalog,
        )
        return self._publish(record.model_dump(mode="json"), name="preview-input.json")

    def _input(self, workflow_id, input_ref):
        record = self._read(input_ref, _Input)
        if record.workflow_id != workflow_id or record.catalog_ref != self.asset_catalog:
            raise ValueError("foreign workflow or catalog input")
        ImageMediaConsumerVerifier(self.store, 20_000_000, 20_000_000).verify(record.source_media)
        prompt = record.effective_prompt
        if (
            prompt.uri != f"artifact://sha256/{prompt.sha256}"
            or prompt.media_type != "text/plain"
            or prompt.schema_version != "harness.prompt_text.v1"
        ):
            raise ValueError("effective prompt requires typed CAS text")
        self.store.resolve(prompt).path.read_bytes().decode("utf-8")
        return record

    def lookup(
        self, *, workflow_id: UUID, input_ref: ArtifactRef, query: str = "mouse"
    ) -> ArtifactRef:
        if query != "mouse":
            raise ValueError("unsupported asset query: only mouse is available")
        self._input(workflow_id, input_ref)
        matches = [item for item in self._catalog().entries if _mouse_entry(item)]
        if len(matches) != 1:
            raise ValueError("mouse lookup requires one exact catalog entry")
        entry = matches[0]
        self.store.resolve(entry.representation)
        self.store.resolve(entry.qualification)
        source = self._read(self.source_scene)
        if source.get("metadata", {}).get("category") != entry.semantic_name or source.get(
            "representation"
        ) != entry.representation.model_dump(mode="json"):
            raise ValueError("mouse source and catalog representation differ")
        record = _Lookup(
            kind="limited_preview_lookup.v1",
            workflow_id=workflow_id,
            input_ref=input_ref,
            catalog_ref=self.asset_catalog,
            query=query,
            asset_id=entry.subject.asset_key,
            representation=entry.representation,
            qualification=entry.qualification,
            source_scene=self.source_scene,
        )
        return self._publish(record.model_dump(mode="json"), name="preview-lookup.json")

    def build_initial(
        self, *, workflow_id: UUID, input_ref: ArtifactRef, lookup_ref: ArtifactRef, asset_id: str
    ) -> ArtifactRef:
        initial = self._input(workflow_id, input_ref)
        lookup = self._read(lookup_ref, _Lookup)
        expected = self.lookup(workflow_id=workflow_id, input_ref=input_ref)
        if expected != lookup_ref or lookup.asset_id != asset_id:
            raise ValueError("missing or foreign workflow lookup lineage")
        scene_ref = adapt_gujie_tabletop_scene(
            self.store,
            source_ref=self.source_scene,
            representation_ref=lookup.representation,
            table_center_m=self.table_center_m,
            table_size_m=self.table_size_m,
            table_friction=self.table_friction,
        )
        scene = read_gujie_tabletop_scene(self.store, scene_ref)
        dimensions = [
            max(row[i] for row in scene.local_vertices_m)
            - min(row[i] for row in scene.local_vertices_m)
            for i in range(3)
        ]
        metadata = self._read(self.source_scene)["metadata"]
        object_id = metadata["name"]
        proposal = dict(
            schema_version="harness.scene_interpretation_proposal.v1",
            route_id="image2env",
            source_media_sha256=initial.source_media.source.sha256,
            effective_prompt=initial.effective_prompt.model_dump(mode="json"),
            objects=[
                dict(
                    object_id=object_id,
                    category=metadata["category"],
                    role="dynamic",
                    dimensions_m=dimensions,
                )
            ],
            relations=[dict(relation="on_table", source=object_id, target="table")],
            unknowns=[],
        )
        proposal["proposal_sha256"] = canonical_evidence_sha256(proposal, "proposal_sha256")
        typed = SceneInterpretationProposalV1.model_validate(proposal)
        proposal_ref = self._publish(
            typed.model_dump(mode="json"), name="preview-proposal.json", schema=typed.schema_version
        )
        compile_input = Image2EnvCompileV2Input(
            schema_version="harness.image2env_compile_input.v2",
            source_media=initial.source_media,
            interpretation_proposal=proposal_ref,
            requested_profile="genesis.rigid_scene@1",
            acquisition_policy="exact_only",
            asset_catalog=self.asset_catalog,
            seed=self.seed,
            predecessor_package=None,
            repair_evidence_refs=(),
            model_execution_allowed=False,
            attempt={"schema_version": "harness.initial_compile_attempt.v1", "kind": "initial"},
        )
        request = Image2EnvGenesisCompileRequestV1(
            schema_version="harness.image2env_genesis_compile_request.v1",
            compile_input=compile_input,
            qualification_run_id=self.qualification_run_id,
            source_scene=self.source_scene,
            table_center_m=self.table_center_m,
            table_size_m=self.table_size_m,
            table_friction=self.table_friction,
        )
        return self._publish(
            request.model_dump(mode="json"),
            name="preview-compile-request.json",
            schema=request.schema_version,
        )
