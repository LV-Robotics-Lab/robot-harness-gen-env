"""Narrow public reader for a workflow's immutable preview input roots."""

import json

from .image_ingest import ImageMediaConsumerVerifier
from .schemas.common import ArtifactRef
from .schemas.x2env import ImageSourceMedia


def read_limited_preview_input(store, ref, workflow_id):
    raw = store.resolve(ref).path.read_bytes()
    value = json.loads(raw)
    if (
        ref.schema_version is not None
        or ref.media_type != "application/json"
        or set(value) != {"kind", "workflow_id", "source_media", "effective_prompt", "catalog_ref"}
        or value["kind"] != "limited_preview_input.v1"
        or value["workflow_id"] != str(workflow_id)
        or raw
        != json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ):
        raise ValueError("preview input must be canonical and owned by this workflow")
    media = ImageSourceMedia.model_validate(value["source_media"])
    prompt = ArtifactRef.model_validate(value["effective_prompt"])
    catalog = ArtifactRef.model_validate(value["catalog_ref"])
    if (
        prompt.schema_version != "harness.prompt_text.v1"
        or prompt.media_type != "text/plain"
        or catalog.schema_version != "harness.portable_asset_catalog.v1"
        or catalog.media_type != "application/json"
    ):
        raise ValueError("preview input roots require exact prompt/catalog types")
    ImageMediaConsumerVerifier(store, 20_000_000, 20_000_000).verify(media)
    roots = (ref, media.source, media.canonical_image, media.probe, prompt, catalog)
    for root in roots:
        store.resolve(root)
    return value, roots
