"""Harness-owned preview input and workflow access, backed by the Golden journal.

The user enters through start. Internal agents receive the resulting workflow ID;
they do not own a second state machine or choose a foreign input binding.
"""

import base64
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from PIL import Image

from .artifacts import LocalArtifactStore
from .golden_execution import GoldenExecutionCatalog
from .golden_run import GoldenRunHarness, GoldenRunNotFoundError
from .golden_store import SQLiteGoldenRunStore
from .limited_image_preview_input_evidence import read_limited_preview_input
from .limited_image_preview_inputs import LimitedImagePreviewInputs
from .schemas.common import ArtifactRef
from .schemas.limited_image_preview_service import (
    PreviewAsset,
    PreviewResourceLink,
    PreviewServiceResult,
)
from .schemas.workflow import RunReadRequest, RunStartRequest
from .trusted_state import TrustedWorldState, WorldFactEvidence


class LimitedImagePreviewService:
    """One principal's server-owned facade over existing workflow state and CAS."""

    def __init__(
        self,
        *,
        artifact_store,
        workflow_store,
        input_builder,
        registry_snapshot,
        principal_id,
        execution_catalog=None,
    ):
        if (
            type(artifact_store) is not LocalArtifactStore
            or type(workflow_store) is not SQLiteGoldenRunStore
            or type(input_builder) is not LimitedImagePreviewInputs
            or input_builder.store is not artifact_store
            or execution_catalog is not None
            and type(execution_catalog) is not GoldenExecutionCatalog
        ):
            raise TypeError(
                "preview service requires exact shared Golden, CAS and input authorities"
            )
        self.store = artifact_store
        self.journal = workflow_store
        self.inputs = input_builder
        self.registry_snapshot = registry_snapshot
        self.principal_id = principal_id
        self.execution_catalog = execution_catalog
        self.harness = self._harness()

    def _harness(self, workflow_id=None):
        values = dict(
            artifact_store=self.store,
            workflow_store=self.journal,
            execution_catalog=self.execution_catalog,
        )
        if workflow_id is not None:
            values["workflow_id_factory"] = lambda: workflow_id
        return GoldenRunHarness(**values)

    def _put(self, value, name, schema):
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        if schema == "harness.world_fact_evidence.v1":
            raw += b"\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_bytes(raw)
            return self.store.put_file(
                path, name=name, media_type="application/json", schema_version=schema
            )

    def _request(self, workflow_id):
        return RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=self.principal_id,
            workflow_run_id=UUID(str(workflow_id)),
        )

    def _input(self, snapshot):
        state = TrustedWorldState.model_validate_json(
            self.store.resolve(snapshot.state_ref).path.read_bytes()
        )
        facts = [fact for fact in state.facts if fact.key == "request.preview_input"]
        if len(facts) != 1:
            raise ValueError("workflow must retain its one original preview input")
        ref = ArtifactRef.model_validate(facts[0].value)
        value, _ = read_limited_preview_input(self.store, ref, snapshot.workflow_run_id)
        return ref, value

    def _link(self, workflow_id, ref):
        return PreviewResourceLink(
            uri=f"harness://workflows/{workflow_id}/artifacts/{ref.sha256}",
            name=ref.name,
            mime_type=ref.media_type,
        )

    def _projection(self, snapshot, stage, **values):
        _, document = self._input(snapshot)
        prompt = ArtifactRef.model_validate(document["effective_prompt"])
        values.setdefault("resource_links", (self._link(snapshot.workflow_run_id, prompt),))
        return PreviewServiceResult(
            workflow_id=snapshot.workflow_run_id,
            revision=snapshot.revision,
            stage=stage,
            status="active",
            snapshot=snapshot,
            source_prompt=prompt,
            **values,
        ).model_dump(mode="json")

    def start(self, *, user_text, idempotency_key, raw_image_ref=None, raw_image_path=None):
        if (raw_image_ref is None) == (raw_image_path is None):
            raise ValueError("start requires exactly one raw image source")
        if raw_image_path is not None:
            path = Path(raw_image_path)
            if path.is_symlink() or not path.is_file():
                raise ValueError("raw image must be an existing regular file")
            with Image.open(path) as image:
                media_type = Image.MIME.get(image.format)
            if media_type is None or not media_type.startswith("image/"):
                raise ValueError("raw file has no supported image media type")
            raw_image_ref = self.store.put_file(
                path,
                name=path.name,
                media_type=media_type,
                schema_version=None,
            )
        raw_image_ref = ArtifactRef.model_validate(raw_image_ref)
        identity = json.dumps([self.principal_id, idempotency_key], separators=(",", ":")).encode()
        workflow_id = UUID(
            bytes=hashlib.sha256(b"limited-preview-start\0" + identity).digest()[:16], version=4
        )
        ref = self.inputs.start(
            workflow_id=workflow_id, raw_image_ref=raw_image_ref, user_text=user_text
        )
        try:
            snapshot = self.harness.read(self._request(workflow_id))
        except GoldenRunNotFoundError:
            snapshot = None
        if snapshot is not None:
            original, _ = self._input(snapshot)
            if original != ref:
                raise ValueError("start idempotency key is already bound to different input")
            return self._projection(snapshot, "start")
        document, _ = read_limited_preview_input(self.store, ref, workflow_id)
        values = {
            "request.preview_input": ref.model_dump(mode="json"),
            "request.image_source": document["source_media"],
            "request.objective": user_text,
        }
        now = datetime.now(timezone.utc)
        facts = tuple(
            self._put(
                WorldFactEvidence(
                    source_kind="user_input",
                    key=key,
                    value=value,
                    observed_at=now,
                    valid_until=None,
                ).model_dump(mode="json"),
                "preview-input-fact.json",
                "harness.world_fact_evidence.v1",
            )
            for key, value in values.items()
        )
        request = RunStartRequest(
            schema_version="harness.run_start_request.v1",
            principal_id=self.principal_id,
            idempotency_key=idempotency_key,
            workspace="production",
            requested_profile="genesis.rigid_scene@1",
            registry_snapshot=self.registry_snapshot,
            initial_fact_evidence=tuple(
                sorted(facts, key=lambda r: (r.sha256, r.bytes, r.media_type, r.schema_version))
            ),
        )
        snapshot = self._harness(workflow_id).start(request)
        return self._projection(snapshot, "start")

    def asset_lookup(self, *, workflow_id, query="mouse"):
        snapshot = self.harness.read(self._request(workflow_id))
        ref, document = self._input(snapshot)
        catalog = ArtifactRef.model_validate(document["catalog_ref"])
        delivery = self.harness.read_workflow_artifact_audited(
            self._request(workflow_id),
            sha256=catalog.sha256,
            resource_uri=self._link(snapshot.workflow_run_id, catalog).uri,
        )
        lookup = self.inputs.lookup(
            workflow_id=snapshot.workflow_run_id, input_ref=ref, query=query
        )
        selected = json.loads(self.store.resolve(lookup).path.read_bytes())
        return self._projection(
            snapshot,
            "asset_lookup",
            asset=PreviewAsset(
                asset_id=selected["asset_id"],
                label="mouse",
                representation=selected["representation"],
                qualification=selected["qualification"],
            ),
            artifacts=(catalog, delivery.access_receipt),
            resource_links=(self._link(snapshot.workflow_run_id, catalog),),
        )

    def status(self, *, workflow_id):
        snapshot = self.harness.read(self._request(workflow_id))
        return self._projection(snapshot, "status")

    def read_resource(self, workflow_id, uri):
        access_prefix = f"harness://workflows/{UUID(str(workflow_id))}/resource-access/"
        if uri.startswith(access_prefix) and len(uri.removeprefix(access_prefix)) == 64:
            _, payload = self.harness.read_resource_access_receipt(
                self._request(workflow_id),
                sha256=uri.removeprefix(access_prefix),
            )
            return {
                "contents": [
                    {"uri": uri, "mimeType": "application/json", "text": payload.decode("utf-8")}
                ]
            }
        prefix = f"harness://workflows/{UUID(str(workflow_id))}/artifacts/"
        if not uri.startswith(prefix) or len(uri.removeprefix(prefix)) != 64:
            raise ValueError("resource must belong to this exact workflow")
        access = self.harness.read_workflow_artifact_audited(
            self._request(workflow_id),
            sha256=uri.removeprefix(prefix),
            resource_uri=uri,
        )
        item = {"uri": uri, "mimeType": access.resource.media_type}
        try:
            item["text"] = access.payload.decode("utf-8")
        except UnicodeDecodeError:
            item["blob"] = base64.b64encode(access.payload).decode("ascii")
        receipt_uri = (
            f"harness://workflows/{workflow_id}/resource-access/{access.access_receipt.sha256}"
        )
        return {
            "contents": [
                item,
                {
                    "uri": receipt_uri,
                    "mimeType": "application/json",
                    "text": access.access_receipt_payload.decode("utf-8"),
                },
            ]
        }
