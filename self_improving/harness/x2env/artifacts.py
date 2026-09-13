"""Bounded traversal of explicit ArtifactRef records, never arbitrary hash strings."""

import json

from .contracts import ArtifactRef


def artifact_closure(
    store, roots, *, max_bytes=256 * 1024 * 1024, max_refs=2048, on_invalid_json=None
):
    """Strict by default; failure exporters may explicitly record unparsed JSON leaves."""
    if type(max_bytes) is not int or max_bytes < 1 or type(max_refs) is not int or max_refs < 1:
        raise ValueError("invalid artifact graph budget")
    pending, seen, byte_hashes = list(roots), {}, set()
    total_bytes = 0
    keys = {"sha256", "size_bytes", "media_type"}
    while pending:
        ref = pending.pop()
        if not isinstance(ref, ArtifactRef):
            raise TypeError("artifact graph roots must be typed ArtifactRef")
        if ref in seen:
            continue
        if len(seen) >= max_refs:
            raise ValueError("artifact graph reference budget exceeded")
        if ref.sha256 not in byte_hashes:
            total_bytes += ref.size_bytes
            if total_bytes > max_bytes:
                raise ValueError("artifact graph byte budget exceeded")
            byte_hashes.add(ref.sha256)
        data = store.read_artifact(ref)
        seen[ref] = None
        if ref.media_type != "application/json":
            continue
        try:
            nodes = [json.loads(data)]
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            if on_invalid_json is None:
                raise
            on_invalid_json(ref, error)
            continue
        visited = 0
        while nodes:
            item = nodes.pop()
            visited += 1
            if visited > 200000:
                raise ValueError("artifact JSON node budget exceeded")
            if isinstance(item, dict):
                if keys <= item.keys():
                    pending.append(ArtifactRef.model_validate({k: item[k] for k in keys}))
                nodes.extend(item.values())
            elif isinstance(item, list):
                nodes.extend(item)
    return tuple(seen)
