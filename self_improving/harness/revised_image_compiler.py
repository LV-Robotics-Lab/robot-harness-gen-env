"""Versioned revised-image compilation over durable existing asset qualification."""

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .artifacts import LocalArtifactStore
from .genesis_scene_execution_v2 import GenesisSceneExecutionVerifierV2
from .image2env_genesis_compile import Image2EnvGenesisCompiler
from .revised_tabletop_scene import (
    DerivedTabletopScene,
    derive_revised_tabletop_scene,
    read_revision_artifact,
    verify_revised_tabletop_scene,
)
from .schemas.common import ArtifactRef
from .schemas.image2env_genesis_compile import Image2EnvGenesisCompileRequestV1
from .schemas.native_prompt_revision import (
    Image2EnvGenesisCompileReceiptV2,
    Image2EnvGenesisCompileRequestV2,
    PromptRevisionV3,
)
from .schemas.x2env_compile_evidence import SceneInterpretationProposalV1


@dataclass(frozen=True)
class RevisedImage2EnvGenesisCompiler:
    artifact_store: LocalArtifactStore
    predecessor_compiler: Image2EnvGenesisCompiler

    def __post_init__(self):
        if (
            type(self.artifact_store) is not LocalArtifactStore
            or type(self.predecessor_compiler) is not Image2EnvGenesisCompiler
        ):
            raise TypeError("revised compiler requires exact CAS and durable predecessor compiler")
        if self.artifact_store.root != self.predecessor_compiler.artifact_store.root:
            raise ValueError("revised compiler and predecessor must share the exact CAS")

    def _read(self, ref, model):
        raw = read_revision_artifact(self.artifact_store, ref)
        value = model.model_validate_json(raw)
        if (
            ref.schema_version != value.schema_version
            or ref.media_type != "application/json"
            or raw not in (_canonical(value), _canonical(value) + b"\n")
        ):
            raise ValueError("revised compilation requires exact typed canonical JSON")
        return value

    def _parent(self, ref):
        if ref.schema_version == "harness.image2env_genesis_compile_receipt.v1":
            receipt, scene = self.predecessor_compiler.read(ref)
            request = self._read(receipt.request, Image2EnvGenesisCompileRequestV1)
        elif ref.schema_version == "harness.image2env_genesis_compile_receipt.v2":
            receipt, scene = self.read(ref)
            request = self._read(receipt.request, Image2EnvGenesisCompileRequestV2)
        else:
            # The request contract exhaustively restricts the third case to
            # replay v2. Its public verifier proves the actual physics closure
            # and exact compile scene/asset binding before returning a request.
            verified = GenesisSceneExecutionVerifierV2(
                self.artifact_store, self.predecessor_compiler
            ).read_receipt(ref)
            return self._parent(verified.request.compile_receipt)
        return receipt, scene, request

    def _inputs(self, request_ref):
        request = self._read(request_ref, Image2EnvGenesisCompileRequestV2)
        inputs = request.compile_input
        # J1 validates hashes of embedded typed payloads and exact UTF-8 prompt;
        # resolving those same references proves their actual canonical bytes.
        self._read(inputs.attempt.prompt_revision, PromptRevisionV3)
        proposal = self._read(inputs.interpretation_proposal, SceneInterpretationProposalV1)
        read_revision_artifact(self.artifact_store, inputs.attempt.effective_prompt)
        parent, prior, parent_request = self._parent(inputs.attempt.predecessor_environment)
        parent_input = parent_request.compile_input
        original_proposal = self._read(
            parent_input.interpretation_proposal, SceneInterpretationProposalV1
        )
        if (
            inputs.source_media != parent_input.source_media
            or inputs.asset_catalog != parent_input.asset_catalog
            or request.qualification_run_id != parent_request.qualification_run_id
            or proposal.objects != original_proposal.objects
            or proposal.relations != original_proposal.relations
        ):
            raise ValueError(
                "revision changed source image, qualified asset or non-layout semantics"
            )
        return request, parent, prior

    def _sources(self, parent):
        sources = {
            ref.name: (ref, Path(__file__).parent / ref.name.replace("__", "/"))
            for ref in parent.implementation
        }
        for name in (
            "revised_image_compiler.py",
            "revised_tabletop_scene.py",
            "schemas/native_prompt_revision.py",
            "genesis_scene_execution_v2.py",
            "genesis_scene_replay_batched.py",
            "schemas/genesis_scene_execution_v2.py",
        ):
            path = Path(__file__).parent / name
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            ref = ArtifactRef(
                name=name.replace("/", "__"),
                uri="artifact://sha256/" + digest,
                sha256=digest,
                bytes=len(raw),
                media_type="text/x-python",
                schema_version=None,
            )
            sources[ref.name] = (ref, path)
        return tuple(sources[name] for name in sorted(sources))

    def compile(self, request_ref):
        request, parent, _ = self._inputs(request_ref)
        layout = request.compile_input.attempt.revision.layout_proposal
        sources = self._sources(parent)
        derived = derive_revised_tabletop_scene(
            self.artifact_store, layout=layout, predecessor_scene=parent.scene
        )
        for expected, path in sources:
            if (
                self.artifact_store.put_file(
                    path, name=expected.name, media_type=expected.media_type, schema_version=None
                )
                != expected
            ):
                raise ValueError("implementation changed during revised compilation")
        receipt = Image2EnvGenesisCompileReceiptV2(
            schema_version="harness.image2env_genesis_compile_receipt.v2",
            request=request_ref,
            scene=derived.scene,
            qualification=parent.qualification,
            layout_proposal=self._publish(layout, "layout-proposal.json"),
            original_source=derived.original_source,
            derived_source_scene=derived.derived_source,
            source_object_id=layout.source_object_id,
            proposal_object_id=layout.source_object_id,
            scene_object_id="object",
            implementation_sha256=_source_digest(sources),
            implementation=tuple(ref for ref, _ in sources),
            scope="static_scene_candidate",
            simulator_executed=False,
        )
        return self._publish(receipt, "revised-native-compile-receipt.json")

    def _publish(self, value, name):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_bytes(_canonical(value))
            return self.artifact_store.put_file(
                path, name=name, media_type="application/json", schema_version=value.schema_version
            )

    def read(self, receipt_ref):
        receipt = self._read(receipt_ref, Image2EnvGenesisCompileReceiptV2)
        request, parent, _ = self._inputs(receipt.request)
        layout = request.compile_input.attempt.revision.layout_proposal
        sources = self._sources(parent)
        if (
            receipt.implementation != tuple(ref for ref, _ in sources)
            or receipt.implementation_sha256 != _source_digest(sources)
            or receipt.qualification != parent.qualification
            or receipt.source_object_id != layout.source_object_id
            or read_revision_artifact(self.artifact_store, receipt.layout_proposal)
            != _canonical(layout)
        ):
            raise ValueError("revised receipt differs from current implementation or exact input")
        for ref, _ in sources:
            read_revision_artifact(self.artifact_store, ref)
        scene = verify_revised_tabletop_scene(
            self.artifact_store,
            layout=layout,
            predecessor_scene=parent.scene,
            derived=DerivedTabletopScene(
                receipt.original_source, receipt.derived_source_scene, receipt.scene
            ),
        )
        return receipt, scene


def _canonical(value):
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _source_digest(sources):
    return hashlib.sha256(
        json.dumps(
            [r.model_dump(mode="json") for r, _ in sources],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
