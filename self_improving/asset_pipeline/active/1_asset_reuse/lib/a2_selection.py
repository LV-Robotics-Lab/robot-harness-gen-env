"""a2: candidate gates, rejection codes, selection bookkeeping."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from lib import ledger

REJ_UNSUPPORTED = "unsupported_format"
REJ_THUMBS = "thumbs_artifact"
REJ_OVERSIZE = "oversize"
REJ_LICENSE = "license_blocked"
REJ_OUTRANKED = "outranked"
REJ_FETCH = "fetch_failed"
REJ_CONVERT = "convert_failed"
REJ_IDENTITY = "identity_unverified"
ALREADY = "already_available_locally"

WEB_FORMATS = {"glb", "gltf", "obj"}
SERVER_FORMATS = {"usd"}
# Providers that serve the NVIDIA asset server's own USDs. The visual channel
# (a5) indexes the SAME corpus by thumbnail, so it yields the same USD
# candidates under a different provider name -- keying the format allowlist off
# a single literal name silently rejected every one of them as
# unsupported_format.
SERVER_PROVIDERS = {"nvidia_server", "nvidia_visual"}


def gate(candidate, globals_cfg):
    key = candidate.metadata.get("key", "")
    if ".thumbs" in key:
        return (REJ_THUMBS, f"thumbnail artifact: {key}")
    allowed = SERVER_FORMATS if candidate.provider in SERVER_PROVIDERS else WEB_FORMATS
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


def _asset_profile(library_dir, name):
    lib = Path(library_dir)
    for c in [lib / name, *lib.glob(f"*/{name}")]:
        lp = c / "ledger.json"
        if lp.is_file():
            try:
                return json.loads(lp.read_text()).get("profile")
            except (OSError, ValueError):
                return None
    return None


def allocate_asset(category, library_dir, manifest_path, profile=None):
    # Directory and asset_id come from this name, and the ledger requires an
    # IR-legal identifier -- "301_trash bin" (with the space) was a real
    # allocation before this normalisation.
    category = str(category).strip().lower().replace(" ", "_")
    numbers, model_counts = set(), {}

    def note(name, count):
        m = re.match(r"^(3\d\d)_(.+)$", name)
        if not m:
            return
        numbers.add(int(m.group(1)))
        model_counts[name] = max(model_counts.get(name, 0), count)

    lib = Path(library_dir)
    for p in ledger.iter_assets(lib):
        note(p.name, len(list(p.glob("model_data*.json"))))
    mp = Path(manifest_path)
    if mp.is_file():
        for g in json.loads(mp.read_text()).get("groups", []):
            for i in g["items"]:
                note(i["asset"], i["model"] + 1)
    for name, count in sorted(model_counts.items()):
        if name.split("_", 1)[1] == category:
            if profile is not None:
                existing = _asset_profile(library_dir, name)
                if existing is not None and existing != profile:
                    # 跨来源 profile 冲突（如 NVIDIA cross_backend 资产 vs
                    # web sapien_only 候选）：不往该资产追加模型——资产级
                    # profile 是单一承诺，upsert 防漂移会正确拒绝。改为继续
                    # 找兼容位，找不到则开新编号（2026-08-20 owner 决策 A）。
                    continue
            return name, count
    n = max(numbers, default=300) + 1
    return f"{n}_{category}", 0


def _attr_gap(attr, want_list, known_list):
    """逐值核对一个属性维度。want 不在 known → mismatch；known 为空 → unverified
    （标注缺失 ≠ 满足——不伪造确认）。返回 unmet 差距列表。"""
    unmet = []
    known = [str(x).lower() for x in (known_list or []) if x]
    for w in [str(x).lower() for x in (want_list or []) if x]:
        if not known:
            unmet.append({"attr": attr, "want": w, "got": [], "kind": "unverified"})
        elif w not in known:
            unmet.append({"attr": attr, "want": w, "got": known, "kind": "mismatch"})
    return unmet


def match_local(
    catalog_path, category, *, want_colors=None, want_materials=None, library_dir=None
):
    """本地 catalog 内的同类匹配 → (payload, unmet)。

    命中规则与 tier0 复用同一口径：请求类别 == 池侧 category 或落在池侧
    aliases 里（池侧别名是 curated 身份声明；请求侧别名不参与，见
    tiered_search 的 phrases 注释）。payload=None 表示类别级无命中。
    unmet==[] 即 exact 候选；否则为 similar（差距逐条带 kind）。
    排序：mismatch 数 → 总差距数 → available 优先 → 模型数多者。"""
    try:
        data = json.loads(Path(catalog_path).read_text())
    except (OSError, ValueError):
        return None, None
    cat = str(category).strip().lower().replace(" ", "_")

    def norm(s):
        return str(s or "").strip().lower().replace(" ", "_")

    best = None
    for e in data.get("entries", []):
        names = {norm(e.get("category"))} | {norm(a) for a in (e.get("aliases") or [])}
        if cat not in names:
            continue
        unmet = _attr_gap("color", want_colors, e.get("colors")) + _attr_gap(
            "material", want_materials, e.get("materials")
        )
        n_mis = sum(1 for u in unmet if u["kind"] == "mismatch")
        key = (
            n_mis,
            len(unmet),
            not e.get("available"),
            -len(e.get("models") or []),
            str(e.get("asset_id") or ""),
        )
        if best is None or key < best[0]:
            best = (key, e, unmet)
    if best is None:
        return None, None
    _, e, unmet = best
    models = e.get("models") or []
    m0 = models[0] if models else {}
    payload = {
        "asset_id": e.get("asset_id"),
        "model_id": m0.get("model_id", 0),
        "category": e.get("category"),
        "available": bool(e.get("available")),
        "asset_path": e.get("asset_path"),
        "visual_path": m0.get("visual_path") or m0.get("model_path"),
        "known_colors": e.get("colors") or [],
        "known_materials": e.get("materials") or [],
        "source": "local_catalog",
        "ledger": None,
    }
    if library_dir:
        lib = Path(library_dir)
        aid = str(e.get("asset_id") or "")
        for c in [lib / aid, *lib.glob(f"*/{aid}")]:
            if (c / "ledger.json").is_file():
                payload["ledger"] = str(c / "ledger.json")
                break
    return payload, unmet


KNOWN_ENTRY_KEYS = {
    "category",
    "aliases",
    "colors",
    "materials",
    "sizes",
    "size_decision",
    "size_policy",
    "collision",
    "reorient",
    "flat",
    "pinned",
    "local",
    "allow_similar",
    "comment",
}


def validate_entries(entries):
    """清单条目校验：返回警告文本列表。不阻断执行——警告随 evidence 留痕，
    未知字段照旧忽略（此前是完全静默，字段名拼错无从发现）。"""
    if not isinstance(entries, list):
        return [f"categories 清单必须是列表，收到 {type(entries).__name__}"]
    warnings = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            warnings.append(f"entry[{i}] 不是对象")
            continue
        if not e.get("category"):
            warnings.append(f"entry[{i}] 缺少必填字段 category")
        unknown = sorted(set(e) - KNOWN_ENTRY_KEYS)
        if unknown:
            warnings.append(
                f"entry[{i}] ({e.get('category', '?')}) 未知字段将被忽略: "
                + ", ".join(unknown)
            )
    return warnings


def build_manifest_group(candidate, asset, model, entry):
    key = candidate.metadata.get("key") or candidate.metadata.get(
        "path", candidate.name
    )
    item = {
        "usd": key.rsplit("/", 1)[-1],
        "asset": asset,
        "model": model,
        "category": entry["category"],
        "aliases": entry.get("aliases", [entry["category"]]),
    }
    if entry.get("colors"):
        item["colors"] = entry["colors"]
    if entry.get("materials"):
        item["materials"] = entry["materials"]
    if entry.get("flat"):
        item["flat"] = True
    # Acquisition entries carry the same per-item knobs a hand-written
    # manifest does; dropping them here silently reverted every acquired
    # asset to defaults (a Khronos lantern is 25.664 m tall at scale 1.0 --
    # the sanity gate rejects it, correctly, unless size_policy survives).
    for knob in ("size_policy", "collision", "reorient"):
        if entry.get(knob):
            item[knob] = entry[knob]
    return {
        "name": f"acq_{asset}",
        "prefix": key.rsplit("/", 1)[0],
        "source": candidate.provider,
        "items": [item],
    }


def append_manifest(manifest_path, group):
    p = Path(manifest_path)
    data = (
        json.loads(p.read_text())
        if p.is_file()
        else {"comment": "auto-generated by acquire_batch", "groups": []}
    )
    data["groups"] = [g for g in data["groups"] if g["name"] != group["name"]] + [group]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    return p


def pinned_candidate(entry):
    from agenticsim.openxsim.assets import AssetCandidate

    pin = entry["pinned"]
    key = f"{pin['prefix'].rstrip('/')}/{pin['usd']}"
    return AssetCandidate(
        candidate_id=f"pinned:{key}",
        name=pin["usd"],
        category=entry["category"],
        download_url=key,
        source_page=key,
        format="usd",
        provider="nvidia_server",
        license="unknown (NVIDIA Omniverse asset server; pinned_by_user)",
        score=0.0,
        metadata={"key": key, "pinned_by_user": True},
    )


def write_evidence(
    path,
    *,
    run_id,
    providers_snapshot,
    categories,
    categories_input=None,
    input_warnings=None,
):
    payload = {
        "schema": "envgen.asset_selection_evidence.v1",
        "run_id": run_id,
        "providers": providers_snapshot,
        "categories": categories,
    }
    if input_warnings:
        payload["input_warnings"] = list(input_warnings)
    if categories_input is not None:
        payload["categories_sha256"] = hashlib.sha256(
            json.dumps(categories_input, sort_keys=True).encode()
        ).hexdigest()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=1, ensure_ascii=False))
