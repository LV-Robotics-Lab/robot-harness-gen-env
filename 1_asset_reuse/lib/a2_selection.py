"""a2: candidate gates, rejection codes, selection bookkeeping."""

from __future__ import annotations

REJ_UNSUPPORTED = "unsupported_format"
REJ_THUMBS = "thumbs_artifact"
REJ_OVERSIZE = "oversize"
REJ_LICENSE = "license_blocked"
REJ_OUTRANKED = "outranked"
REJ_FETCH = "fetch_failed"
REJ_CONVERT = "convert_failed"
ALREADY = "already_available_locally"

WEB_FORMATS = {"glb", "gltf", "obj"}
SERVER_FORMATS = {"usd"}


def gate(candidate, globals_cfg):
    key = candidate.metadata.get("key", "")
    if ".thumbs" in key:
        return (REJ_THUMBS, f"thumbnail artifact: {key}")
    allowed = SERVER_FORMATS if candidate.provider == "nvidia_server" else WEB_FORMATS
    fmt = candidate.format.lower()
    if fmt not in allowed:
        return (REJ_UNSUPPORTED, f"format {fmt!r} not in {sorted(allowed)}")
    size = candidate.metadata.get("size_bytes")
    if size and size > globals_cfg.get("max_size_bytes", 200_000_000):
        return (REJ_OVERSIZE, f"{size} bytes over limit")
    if globals_cfg.get("license_gate") and str(candidate.license).lower().startswith(
        "unknown"
    ):
        return (REJ_LICENSE, candidate.license)
    return None


def gate_candidates(candidates, globals_cfg):
    records = []
    for c in candidates:
        r = gate(c, globals_cfg)
        records.append(
            {
                "candidate": c,
                "verdict": "viable" if r is None else "rejected",
                "rejection": None if r is None else {"code": r[0], "detail": r[1]},
            }
        )
    return records


def candidate_dict(c):
    return {
        "candidate_id": c.candidate_id,
        "provider": c.provider,
        "url": c.download_url,
        "format": c.format,
        "license": c.license,
        "score": c.score,
    }
