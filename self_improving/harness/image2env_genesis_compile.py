"""Compile actual image evidence into Gujie-derived native finite-table scene candidates."""

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .artifacts import LocalArtifactStore
from .genesis_asset_qualification_application import GenesisAssetQualificationApplication
from .gujie_scene_adapter import adapt_gujie_tabletop_scene, read_gujie_tabletop_scene
from .schemas.common import ArtifactRef
from .schemas.image2env_genesis_compile import (
    Image2EnvGenesisCompileReceiptV1,
    Image2EnvGenesisCompileRequestV1,
)
from .schemas.x2env_compile_evidence import GenesisAssetQualificationV1
from .x2env_compile_closure import ExactReuseCompileClosureVerifier


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def _implementation():
    # Explicit executable boundary; no mutable ledgers or deployment paths.
    paths = (
        "artifacts.py",
        "genesis_asset_qualification_application.py",
        "genesis_runtime_execution.py",
        "genesis_runtime_manifest.py",
        "genesis_asset_probe.py",
        "gujie_scene_adapter.py",
        "image2env_genesis_compile.py",
        "image_ingest.py",
        "x2env_compile_closure.py",
        "schemas/base.py",
        "schemas/common.py",
        "schemas/genesis_runtime_execution.py",
        "schemas/genesis_runtime_manifest.py",
        "schemas/genesis_asset_probe.py",
        "schemas/genesis_scene_replay.py",
        "schemas/image2env_genesis_compile.py",
        "schemas/x2env.py",
        "schemas/x2env_compile_evidence.py",
    )
    result = []
    for name in sorted(paths):
        path = Path(__file__).parent / name
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        result.append(
            (
                ArtifactRef(
                    name=name.replace("/", "__"),
                    uri=f"artifact://sha256/{sha}",
                    sha256=sha,
                    bytes=len(data),
                    media_type="text/x-python",
                    schema_version=None,
                ),
                path,
            )
        )
    result.sort(key=lambda item: item[0].name)
    return tuple(result)


@dataclass(frozen=True)
class Image2EnvGenesisCompiler:
    artifact_store: LocalArtifactStore
    qualification_application: GenesisAssetQualificationApplication

    def _read(self, ref, model):
        data = self.artifact_store.resolve(ref).path.read_bytes()
        value = model.model_validate_json(data)
        if (
            ref.schema_version != value.schema_version
            or ref.media_type != "application/json"
            or ref.uri != f"artifact://sha256/{ref.sha256}"
            or data != _canonical(value.model_dump(mode="json"))
        ):
            raise ValueError("native compile requires canonical typed CAS JSON")
        return value

    def _inputs(self, request_ref):
        if (
            type(self.artifact_store) is not LocalArtifactStore
            or type(self.qualification_application) is not GenesisAssetQualificationApplication
        ):
            raise TypeError("compile requires the durable qualification application and local CAS")
        if self.qualification_application.artifact_store.root != self.artifact_store.root:
            raise ValueError("durable qualification must use the same CAS")
        request = self._read(request_ref, Image2EnvGenesisCompileRequestV1)
        run = self.qualification_application.read(request.qualification_run_id)
        if run is None or run.status != "qualified" or run.qualification_ref is None:
            raise ValueError("durable qualification is not a completed trusted run")
        qualification = GenesisAssetQualificationV1.model_validate_json(
            self.artifact_store.resolve(run.qualification_ref).path.read_bytes()
        )
        implementation = _implementation()
        digest = hashlib.sha256(
            _canonical([ref.model_dump(mode="json") for ref, _ in implementation])
        ).hexdigest()
        verified = ExactReuseCompileClosureVerifier(
            self.artifact_store,
            20_000_000,
            20_000_000,
            digest,
            self.qualification_application.issuer_sha256,
            qualification.runtime_lock,
            (run.qualification_ref,),
        ).preflight(request.compile_input)
        asset = verified.object_matches[0].asset
        source = json.loads(self.artifact_store.resolve(request.source_scene).path.read_bytes())
        obj = verified.proposal.objects[0]
        if (
            source.get("metadata", {}).get("name") != obj.object_id
            or source.get("metadata", {}).get("category") != obj.category
            or source.get("representation") != asset.representation_ref.model_dump(mode="json")
            or asset.qualification_ref != run.qualification_ref
        ):
            raise ValueError("proposal, Gujie source and trusted qualified asset differ")
        return request, run, asset, obj, implementation, digest

    def compile(self, request_ref):
        request, run, asset, obj, implementation, digest = self._inputs(request_ref)
        scene = adapt_gujie_tabletop_scene(
            self.artifact_store,
            source_ref=request.source_scene,
            representation_ref=asset.representation_ref,
            table_center_m=request.table_center_m,
            table_size_m=request.table_size_m,
            table_friction=request.table_friction,
        )
        for ref, path in implementation:
            actual = self.artifact_store.put_file(
                path, name=ref.name, media_type=ref.media_type, schema_version=None
            )
            if actual != ref:
                raise ValueError("implementation changed while publishing")
        receipt = Image2EnvGenesisCompileReceiptV1(
            schema_version="harness.image2env_genesis_compile_receipt.v1",
            request=request_ref,
            scene=scene,
            qualification=run.qualification_ref,
            source_object_id=obj.object_id,
            proposal_object_id=obj.object_id,
            scene_object_id="object",
            implementation_sha256=digest,
            implementation=tuple(ref for ref, _ in implementation),
            scope="static_scene_candidate",
            simulator_executed=False,
        )
        with tempfile.TemporaryDirectory(prefix="native-compile-") as staging:
            path = Path(staging) / "receipt.json"
            path.write_bytes(_canonical(receipt.model_dump(mode="json")))
            return self.artifact_store.put_file(
                path,
                name="native-image-compile-receipt.json",
                media_type="application/json",
                schema_version=receipt.schema_version,
            )

    def read(self, receipt_ref):
        receipt = self._read(receipt_ref, Image2EnvGenesisCompileReceiptV1)
        request, run, asset, obj, implementation, digest = self._inputs(receipt.request)
        expected = tuple(ref for ref, _ in implementation)
        if (
            receipt.implementation != expected
            or receipt.implementation_sha256 != digest
            or receipt.qualification != run.qualification_ref
            or receipt.proposal_object_id != obj.object_id
        ):
            raise ValueError("native receipt implementation or trusted input differs")
        for ref in expected:
            self.artifact_store.resolve(ref)
        scene = read_gujie_tabletop_scene(self.artifact_store, receipt.scene)
        if (
            scene.source,
            scene.representation,
            scene.table_center_m,
            scene.table_size_m,
            scene.table_friction,
        ) != (
            request.source_scene,
            asset.representation_ref,
            request.table_center_m,
            request.table_size_m,
            request.table_friction,
        ):
            raise ValueError("native receipt scene differs from exact compile input")
        return receipt, scene
