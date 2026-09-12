# Asset Retrieval + Scene-Driven Acquisition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现设计文档 `1_asset_reuse/docs/2026-08-03-asset-retrieval-integration-design.md` 的检索选定层：批量引擎（四级 provider 检索 → 门禁选定 → 现有引入管线）+ 场景驱动自适应层（prompt → 覆盖检查 → 缺口自动引进 → 场景生成）。

**Architecture:** 薄检索层（`lib/a1–a4` + 两个编排 CLI），聚合排序/GitHub 搜索/下载复用 openxsim 现成件（零修改）；拉取、物化、验证、catalog 重建全部 subprocess 调用现有已验证脚本（零修改）。

**Tech Stack:** Python 3（conda env-gen-yuxin / isaac-smoke）、pytest、openxsim `agenticsim.openxsim.assets`、上游 `scene_gen`（只读 import）。

## Global Constraints

- **一切代码与命令都在远程 lv-5090 上**。本机编辑：文件写在本地 scratchpad 后 `scp <file> lv-5090:<目标路径>`；命令一律 `ssh lv-5090 "..."`。
- **隔离工作区**（主树上有并行会话，勿直接使用）：
  ```bash
  ssh lv-5090 "cd /home/jingxiang/yuxin/env-gen-dev && git worktree add -b feat/asset-retrieval ../env-gen-dev-asset HEAD"
  ```
  下文 `WT=/home/jingxiang/yuxin/env-gen-dev-asset`，`MAIN=/home/jingxiang/yuxin/env-gen-dev`。worktree 没有 `data/`、`external/`、`shared/openxsim` 的 deps（均不入 git）——所以所有跨树引用一律用**主树绝对路径**（只读）。
- **常量**（原样使用）：
  - `PY_SAP=/home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python`
  - `PY_ISA=/home/jingxiang/miniconda3/envs/isaac-smoke/bin/python`（Isaac 步骤需 `OMNI_KIT_ACCEPT_EULA=YES`）
  - `OXS=/home/jingxiang/yuxin/env-gen-dev/shared/openxsim/source/agenticsim`（openxsim import 路径，已验证 `from agenticsim.openxsim.assets import AssetScout` 可用）
  - `UP=/home/jingxiang/yuxin/env-gen-github`（上游 env-gen，只读；`scene_gen` 包在其根目录）
  - `RT=/home/jingxiang/workspace/alchedata-self-improving-agents/external/RoboTwin`（只读）
  - 主树扩展 catalog（只读输入）：`MAIN/data/scene_gen_ext/asset_catalog.json`
- **测试命令模板**（每次跑测试用这个）：
  ```bash
  ssh lv-5090 "cd /home/jingxiang/yuxin/env-gen-dev-asset/1_asset_reuse && PYTHONPATH=.:/home/jingxiang/yuxin/env-gen-dev/shared/openxsim/source/agenticsim:/home/jingxiang/yuxin/env-gen-github /home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python -m pytest tests -q"
  ```
- **零修改**：`UP`、`OXS`、`RT`、主树 `data/`、现有 `1_asset_reuse/scripts/import_*.py` / `s9_build_shadow_root.py` 一律只读。集成运行的所有写入落在 `WT/data/` 与 `WT/results/_test/` 下（自包含）。
- **按内容判定**：Isaac/Kit 相关子进程会吞退出码——成败以产物文件内容为准，不以 returncode 为准。
- 提交都在 `WT`（分支 feat/asset-retrieval），消息风格 `feat(1_asset_reuse): ...`，结尾加：
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- 淘汰码/schema 名/字段名以设计文档 §7 为准；本计划代码中出现的常量即规范值。

---

### Task 1: 工作区 + 测试脚手架 + NvidiaAssetServerProvider

**Files:**
- Create: `1_asset_reuse/lib/__init__.py`（空文件）
- Create: `1_asset_reuse/lib/a1_providers.py`
- Create: `1_asset_reuse/tests/__init__.py`（空文件）
- Test: `1_asset_reuse/tests/test_a1_providers.py`

**Interfaces:**
- Produces: `NvidiaAssetServerProvider(prefixes: list[str], index_path: Path, bucket=BUCKET, list_keys_fn=None)`，方法 `ensure_index(refresh=False) -> dict[str, list[[key, size]]]`、`search(query: str, limit=20) -> list[AssetCandidate]`；模块级 `BUCKET`、`list_bucket_keys(prefix, bucket, timeout_s=120) -> list[tuple[str, int]]`、`_tokens(query) -> set[str]`
- Consumes: openxsim `AssetCandidate`（字段：candidate_id/name/category/download_url/source_page/format/provider/license/score/metadata）

- [ ] **Step 1: 建 worktree**

Run: `ssh lv-5090 "cd /home/jingxiang/yuxin/env-gen-dev && git worktree add -b feat/asset-retrieval ../env-gen-dev-asset HEAD && ls ../env-gen-dev-asset/1_asset_reuse"`
Expected: 列出 scripts/ configs/ docs/ 等（无 data/、无 external/）。

- [ ] **Step 2: 写失败测试**

`tests/test_a1_providers.py`：

```python
import json
from pathlib import Path

from lib.a1_providers import BUCKET, NvidiaAssetServerProvider

FAKE_KEYS = [
    ("Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned/019_pitcher_base.usd", 4200000),
    ("Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned/024_bowl.usd", 3100000),
    ("Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned/.thumbs/256x256/024_bowl.usd.png", 9000),
    ("Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned/Materials/wood.mdl", 12000),
]
PREFIX = "Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned"


def make_provider(tmp_path, calls=None):
    def fake_list(prefix, bucket=BUCKET, timeout_s=120):
        if calls is not None:
            calls.append(prefix)
        return FAKE_KEYS
    return NvidiaAssetServerProvider([PREFIX], tmp_path / "idx.json", list_keys_fn=fake_list)


def test_search_matches_usd_by_token(tmp_path):
    p = make_provider(tmp_path)
    got = p.search("pitcher")
    assert len(got) == 1
    c = got[0]
    assert c.candidate_id.endswith("019_pitcher_base.usd")
    assert c.format == "usd"
    assert c.provider == "nvidia_server"
    assert c.metadata["key"].endswith("019_pitcher_base.usd")
    assert c.metadata["size_bytes"] == 4200000
    assert c.download_url.startswith(BUCKET)


def test_search_skips_thumbs_and_non_usd(tmp_path):
    p = make_provider(tmp_path)
    got = p.search("bowl")
    assert len(got) == 1 and ".thumbs" not in got[0].metadata["key"]
    assert p.search("wood") == []


def test_index_cached_after_first_build(tmp_path):
    calls = []
    p = make_provider(tmp_path, calls)
    p.search("bowl")
    p.search("pitcher")
    assert calls == [PREFIX]
    assert json.loads((tmp_path / "idx.json").read_text())[PREFIX]
```

- [ ] **Step 3: 跑测试确认失败**

用 Global Constraints 的测试命令模板。Expected: `ModuleNotFoundError: No module named 'lib'`（或 a1_providers 不存在）。

- [ ] **Step 4: 实现 a1_providers.py（最小实现）**

```python
"""a1: tier providers + registry for the acquire retrieval layer."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from agenticsim.openxsim.assets import AssetCandidate

BUCKET = "https://omniverse-content-production.s3-us-west-2.amazonaws.com"


def _tokens(query: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 1}


def list_bucket_keys(prefix, bucket=BUCKET, timeout_s=120):
    ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    out, token = [], None
    while True:
        url = f"{bucket}/?list-type=2&prefix={urllib.parse.quote(prefix)}"
        if token:
            url += f"&continuation-token={urllib.parse.quote(token)}"
        root = ET.fromstring(urllib.request.urlopen(url, timeout=timeout_s).read())
        for c in root.iter(f"{ns}Contents"):
            out.append((c.find(f"{ns}Key").text, int(c.find(f"{ns}Size").text)))
        t = root.find(f"{ns}NextContinuationToken")
        if t is None:
            return out
        token = t.text


class NvidiaAssetServerProvider:
    name = "nvidia_server"

    def __init__(self, prefixes, index_path, bucket=BUCKET, list_keys_fn=None):
        self.prefixes = list(prefixes)
        self.index_path = Path(index_path)
        self.bucket = bucket
        self._list = list_keys_fn or list_bucket_keys

    def ensure_index(self, refresh=False):
        if self.index_path.is_file() and not refresh:
            return json.loads(self.index_path.read_text())
        index = {p: [[k, s] for k, s in self._list(p, self.bucket)] for p in self.prefixes}
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(index, indent=1))
        return index

    def search(self, query, limit=20):
        toks = _tokens(query)
        out = []
        for prefix, entries in self.ensure_index().items():
            for key, size in entries:
                base = key.rsplit("/", 1)[-1].lower()
                if ".thumbs" in key or not base.endswith(".usd"):
                    continue
                hits = sum(1 for t in toks if t in base)
                if toks and not hits:
                    continue
                out.append(
                    AssetCandidate(
                        candidate_id=f"nvidia:{key}",
                        name=key.rsplit("/", 1)[-1],
                        category=key.rsplit("/", 2)[-2].lower(),
                        download_url=f"{self.bucket}/{urllib.parse.quote(key)}",
                        source_page=f"{self.bucket}/{urllib.parse.quote(key)}",
                        format="usd",
                        provider=self.name,
                        license="unknown (NVIDIA Omniverse asset server)",
                        score=float(hits),
                        metadata={"key": key, "size_bytes": size, "prefix": prefix},
                    )
                )
        return sorted(out, key=lambda c: (-c.score, c.candidate_id))[:limit]
```

- [ ] **Step 5: 跑测试确认通过**（同模板命令）Expected: 3 passed。

- [ ] **Step 6: Commit**

```bash
ssh lv-5090 "cd /home/jingxiang/yuxin/env-gen-dev-asset && git add 1_asset_reuse/lib 1_asset_reuse/tests && git commit -m 'feat(1_asset_reuse): a1 NvidiaAssetServerProvider with cached S3 key index

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>'"
```

---

### Task 2: RoboTwinLocalProvider（tier 0）+ mini catalog fixture

**Files:**
- Modify: `1_asset_reuse/lib/a1_providers.py`（追加类）
- Create: `1_asset_reuse/tests/fixtures/mini_catalog.json`（由真 catalog 过滤生成）
- Test: `1_asset_reuse/tests/test_a1_providers.py`（追加）

**Interfaces:**
- Produces: `RoboTwinLocalProvider(catalog_path)`，`search(query, limit=20) -> list[AssetCandidate]`（命中条目 `format="catalog_entry"`、`metadata={"asset_id","colors"}`）
- fixture：`tests/fixtures/mini_catalog.json` 为 load_catalog 兼容的完整 catalog 结构，仅含 cup 与 cabinet 条目（后续 Task 7 复用）

- [ ] **Step 1: 生成 fixture（从主树真 catalog 过滤，保结构）**

```bash
ssh lv-5090 "cd /home/jingxiang/yuxin/env-gen-dev-asset/1_asset_reuse && mkdir -p tests/fixtures && /home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python - <<'EOF'
import json
src = json.load(open('/home/jingxiang/yuxin/env-gen-dev/data/scene_gen_ext/asset_catalog.json'))
keep = []
for e in (src['entries'] if isinstance(src, dict) else src):
    if e['category'] in {'cup', 'cabinet'}:
        keep.append(e)
assert keep, 'no cup/cabinet entries found'
if isinstance(src, dict):
    src['entries'] = keep
    out = src
else:
    out = keep
json.dump(out, open('tests/fixtures/mini_catalog.json', 'w'), indent=1)
print('kept', len(keep))
EOF"
```
Expected: `kept N`（N≥2）。

- [ ] **Step 2: 追加失败测试**

```python
from lib.a1_providers import RoboTwinLocalProvider

FIX = Path(__file__).parent / "fixtures" / "mini_catalog.json"


def test_local_provider_hits_alias():
    got = RoboTwinLocalProvider(FIX).search("mug")
    assert got and got[0].format == "catalog_entry"
    assert got[0].metadata["asset_id"]
    assert got[0].provider == "robotwin_local"


def test_local_provider_miss_returns_empty():
    assert RoboTwinLocalProvider(FIX).search("pitcher") == []
```

- [ ] **Step 3: 跑测试确认失败**（ImportError: RoboTwinLocalProvider）

- [ ] **Step 4: 追加实现**

```python
class RoboTwinLocalProvider:
    name = "robotwin_local"

    def __init__(self, catalog_path):
        self.catalog_path = Path(catalog_path)

    def search(self, query, limit=20):
        data = json.loads(self.catalog_path.read_text())
        entries = data["entries"] if isinstance(data, dict) else data
        toks = _tokens(query)
        out = []
        for e in entries:
            names = {str(n).lower() for n in [e.get("category", ""), e.get("semantic_name", ""), *e.get("aliases", [])]}
            hits = sum(1 for t in toks if t in names)
            if not hits:
                continue
            out.append(
                AssetCandidate(
                    candidate_id=f"catalog:{e['asset_id']}",
                    name=e["asset_id"],
                    category=e.get("category", ""),
                    download_url=f"file://{e.get('asset_path', '')}",
                    source_page=str(self.catalog_path),
                    format="catalog_entry",
                    provider=self.name,
                    license="already registered",
                    score=float(hits),
                    metadata={"asset_id": e["asset_id"], "colors": e.get("colors", [])},
                )
            )
        return sorted(out, key=lambda c: (-c.score, c.candidate_id))[:limit]
```

- [ ] **Step 5: 跑测试确认通过**（5 passed）

- [ ] **Step 6: Commit**（`feat(1_asset_reuse): a1 RoboTwinLocalProvider (tier-0 catalog hits) + mini catalog fixture`，同 Co-Authored-By 格式）

---

### Task 3: providers.json 装载 + 四级分级调度

**Files:**
- Create: `1_asset_reuse/configs/providers.json`
- Modify: `1_asset_reuse/lib/a1_providers.py`（追加 `Tier`、`load_providers`、`tiered_search`）
- Test: `1_asset_reuse/tests/test_tiered_search.py`

**Interfaces:**
- Produces: `Tier(tier: int, provider)`（dataclass）；`load_providers(config: dict) -> tuple[list[Tier], dict]`（第二项为 globals）；`tiered_search(tiers, query, *, viable_fn, limit=20) -> dict`，返回 `{"tier0_hit": AssetCandidate|None, "candidates": list, "tiers_consulted": list[int], "provider_errors": list[dict]}`。语义：tier 0 命中即返回；tier≥1 逐级下探，出现**可过门禁**（viable_fn 为真）的候选即停。
- Consumes: Task 1/2 的 providers；openxsim `AssetScout`（聚合去重排序）、`GitHubTreeSearchProvider`、`GitHubRepositoryDiscoveryProvider`

- [ ] **Step 1: 写 configs/providers.json**

```json
{
  "globals": {"top_k": 5, "max_fallback": 2, "max_size_bytes": 200000000, "license_gate": false},
  "providers": {
    "robotwin_local": {"enabled": true, "tier": 0,
      "catalog": "/home/jingxiang/yuxin/env-gen-dev/data/scene_gen_ext/asset_catalog.json"},
    "nvidia_server": {"enabled": true, "tier": 1,
      "prefixes": ["Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned"],
      "index_path": "data/asset_index/nvidia_keys.json"},
    "github_tree": {"enabled": false, "tier": 2, "repositories": [
      {"repository": "KhronosGroup/glTF-Sample-Assets", "branch": "main",
       "license": "per-model (see source_page); repo docs list per-asset licenses"}]},
    "github_discovery": {"enabled": false, "tier": 3, "repository_limit": 5}
  }
}
```

- [ ] **Step 2: 写失败测试**

```python
from dataclasses import replace
from pathlib import Path

from agenticsim.openxsim.assets import AssetCandidate

from lib.a1_providers import Tier, load_providers, tiered_search


def cand(cid, provider="fake", fmt="usd"):
    return AssetCandidate(candidate_id=cid, name=cid, category="x",
                          download_url=f"https://x/{cid}", source_page="https://x",
                          format=fmt, provider=provider, license="unknown", score=1.0,
                          metadata={"key": cid, "size_bytes": 1})


class FakeProvider:
    def __init__(self, name, results=None, err=None):
        self.name, self.results, self.err = name, results or [], err

    def search(self, query, limit=20):
        if self.err:
            raise RuntimeError(self.err)
        return self.results


def test_tier0_hit_stops_everything():
    t1 = FakeProvider("t1", [cand("a")])
    res = tiered_search([Tier(0, FakeProvider("t0", [cand("local")])), Tier(1, t1)],
                        "cup", viable_fn=lambda c: True)
    assert res["tier0_hit"].candidate_id == "local"
    assert res["tiers_consulted"] == [0]


def test_descends_until_viable():
    tiers = [Tier(0, FakeProvider("t0", [])),
             Tier(1, FakeProvider("t1", [cand("bad")])),
             Tier(2, FakeProvider("t2", [cand("good")]))]
    res = tiered_search(tiers, "cup", viable_fn=lambda c: c.candidate_id == "good")
    assert res["tiers_consulted"] == [0, 1, 2]
    assert [c.candidate_id for c in res["candidates"]] == ["good"]


def test_provider_error_recorded_and_continues():
    tiers = [Tier(0, FakeProvider("t0", [])),
             Tier(1, FakeProvider("boom", err="down")),
             Tier(2, FakeProvider("t2", [cand("good")]))]
    res = tiered_search(tiers, "cup", viable_fn=lambda c: True)
    assert res["candidates"]
    assert any("down" in e["error"] for e in res["provider_errors"])


def test_load_providers_from_config(tmp_path):
    cfg = {"globals": {"top_k": 3}, "providers": {
        "robotwin_local": {"enabled": True, "tier": 0,
                           "catalog": str(Path(__file__).parent / "fixtures" / "mini_catalog.json")},
        "nvidia_server": {"enabled": True, "tier": 1, "prefixes": ["P"],
                          "index_path": str(tmp_path / "idx.json")},
        "github_tree": {"enabled": False, "tier": 2, "repositories": []},
        "github_discovery": {"enabled": False, "tier": 3}}}
    tiers, g = load_providers(cfg)
    assert [t.tier for t in tiers] == [0, 1]
    assert g["top_k"] == 3
```

- [ ] **Step 3: 跑测试确认失败**（ImportError: Tier）

- [ ] **Step 4: 追加实现（a1_providers.py 末尾）**

```python
from dataclasses import dataclass

from agenticsim.openxsim.assets import AssetScout


@dataclass
class Tier:
    tier: int
    provider: object


def load_providers(config):
    g = dict(config.get("globals", {}))
    pc = config["providers"]
    tiers = []
    if pc.get("robotwin_local", {}).get("enabled"):
        tiers.append(Tier(pc["robotwin_local"].get("tier", 0),
                          RoboTwinLocalProvider(pc["robotwin_local"]["catalog"])))
    if pc.get("nvidia_server", {}).get("enabled"):
        n = pc["nvidia_server"]
        tiers.append(Tier(n.get("tier", 1), NvidiaAssetServerProvider(n["prefixes"], n["index_path"])))
    if pc.get("github_tree", {}).get("enabled"):
        from agenticsim.openxsim.assets import GitHubTreeSearchProvider

        for repo in pc["github_tree"]["repositories"]:
            tiers.append(Tier(pc["github_tree"].get("tier", 2),
                              GitHubTreeSearchProvider(repo["repository"],
                                                       branch=repo.get("branch", "main"),
                                                       license=repo.get("license", "unknown"))))
    if pc.get("github_discovery", {}).get("enabled"):
        from agenticsim.openxsim.assets import GitHubRepositoryDiscoveryProvider

        d = pc["github_discovery"]
        tiers.append(Tier(d.get("tier", 3),
                          GitHubRepositoryDiscoveryProvider(repository_limit=d.get("repository_limit", 5))))
    return tiers, g


def tiered_search(tiers, query, *, viable_fn, limit=20):
    consulted, errors = [], []
    for tier_no in sorted({t.tier for t in tiers}):
        group = [t.provider for t in tiers if t.tier == tier_no]
        consulted.append(tier_no)
        scout = AssetScout(group)
        try:
            cands = scout.search(query, limit=limit)
        except Exception as exc:  # noqa: BLE001
            errors.append({"tier": tier_no, "provider": group[0].name, "error": str(exc)})
            continue
        errors.extend({"tier": tier_no, **e} for e in scout.last_errors)
        if tier_no == 0:
            if cands:
                return {"tier0_hit": cands[0], "candidates": [],
                        "tiers_consulted": consulted, "provider_errors": errors}
            continue
        if any(viable_fn(c) for c in cands):
            return {"tier0_hit": None, "candidates": cands,
                    "tiers_consulted": consulted, "provider_errors": errors}
    return {"tier0_hit": None, "candidates": [], "tiers_consulted": consulted,
            "provider_errors": errors}
```

注意：`AssetScout([provider])` 聚合时单个 provider 抛错会进 `scout.last_errors` 而非抛出（openxsim 行为），所以 `test_provider_error_recorded_and_continues` 断言的是 last_errors 路径；实现里 try/except 仅防 AssetScout 构造/极端错误。若测试因此失败（candidates 为空），把 FakeProvider("boom") 的错误断言改为检查 `provider_errors` 里有记录即可——以 openxsim 实际行为为准，不改 openxsim。

- [ ] **Step 5: 跑测试确认通过**（9 passed）

- [ ] **Step 6: Commit**（`feat(1_asset_reuse): a1 provider registry + 4-tier trust-gradient dispatch`）

---

### Task 4: a2 候选门禁 + 淘汰码

**Files:**
- Create: `1_asset_reuse/lib/a2_selection.py`
- Test: `1_asset_reuse/tests/test_a2_selection.py`

**Interfaces:**
- Produces: 淘汰码常量 `REJ_UNSUPPORTED="unsupported_format"`、`REJ_THUMBS="thumbs_artifact"`、`REJ_OVERSIZE="oversize"`、`REJ_LICENSE="license_blocked"`、`REJ_OUTRANKED="outranked"`、`REJ_FETCH="fetch_failed"`、`REJ_CONVERT="convert_failed"`、`ALREADY="already_available_locally"`；`WEB_FORMATS={"glb","gltf","obj"}`、`SERVER_FORMATS={"usd"}`；
  `gate(candidate, globals_cfg) -> None | tuple[code, detail]`；
  `gate_candidates(candidates, globals_cfg) -> list[dict]`，元素 `{"candidate", "verdict": "viable"|"rejected", "rejection": None|{"code","detail"}}`；
  `candidate_dict(c) -> dict`（candidate_id/provider/url/format/license/score）

- [ ] **Step 1: 写失败测试**

```python
from agenticsim.openxsim.assets import AssetCandidate

from lib import a2_selection as a2


def cand(fmt="usd", provider="nvidia_server", key="a/b.usd", size=100, license="unknown x"):
    return AssetCandidate(candidate_id=key, name=key, category="x",
                          download_url="https://x", source_page="https://x",
                          format=fmt, provider=provider, license=license, score=1.0,
                          metadata={"key": key, "size_bytes": size})

G = {"max_size_bytes": 1000, "license_gate": False}


def test_server_candidate_must_be_usd():
    assert a2.gate(cand(fmt="usd"), G) is None
    assert a2.gate(cand(fmt="glb"), G)[0] == a2.REJ_UNSUPPORTED


def test_web_candidate_formats():
    assert a2.gate(cand(fmt="glb", provider="github_tree"), G) is None
    assert a2.gate(cand(fmt="usd", provider="github_tree"), G)[0] == a2.REJ_UNSUPPORTED


def test_thumbs_oversize_license():
    assert a2.gate(cand(key="a/.thumbs/x.usd"), G)[0] == a2.REJ_THUMBS
    assert a2.gate(cand(size=2000), G)[0] == a2.REJ_OVERSIZE
    assert a2.gate(cand(), {**G, "license_gate": True})[0] == a2.REJ_LICENSE


def test_gate_candidates_records_every_rejection():
    recs = a2.gate_candidates([cand(), cand(fmt="glb")], G)
    assert [r["verdict"] for r in recs] == ["viable", "rejected"]
    assert recs[1]["rejection"]["code"] == a2.REJ_UNSUPPORTED
```

- [ ] **Step 2: 跑测试确认失败**（No module named lib.a2_selection）

- [ ] **Step 3: 实现 a2_selection.py**

```python
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
    if globals_cfg.get("license_gate") and str(candidate.license).lower().startswith("unknown"):
        return (REJ_LICENSE, candidate.license)
    return None


def gate_candidates(candidates, globals_cfg):
    records = []
    for c in candidates:
        r = gate(c, globals_cfg)
        records.append({"candidate": c, "verdict": "viable" if r is None else "rejected",
                        "rejection": None if r is None else {"code": r[0], "detail": r[1]}})
    return records


def candidate_dict(c):
    return {"candidate_id": c.candidate_id, "provider": c.provider, "url": c.download_url,
            "format": c.format, "license": c.license, "score": c.score}
```

- [ ] **Step 4: 跑测试确认通过**（13 passed 累计）

- [ ] **Step 5: Commit**（`feat(1_asset_reuse): a2 candidate gates with explicit rejection codes`）

---

### Task 5: 编号分配 + manifest 生成 + evidence 落盘

**Files:**
- Modify: `1_asset_reuse/lib/a2_selection.py`（追加）
- Test: `1_asset_reuse/tests/test_a2_selection.py`（追加）

**Interfaces:**
- Produces: `allocate_asset(category, library_dir, manifest_path) -> tuple[str, int]`（同类别→同资产目录下一个 model 序号；新类别→下一个空闲 3XX 号、model 0）；
  `build_manifest_group(candidate, asset, model, entry) -> dict`（external_manifest 的 group 结构：name/prefix/items，item 含 usd/asset/model/category/aliases[/colors][/flat]）；
  `append_manifest(manifest_path, group) -> Path`（同名 group 覆盖式追加）；
  `write_evidence(path, *, run_id, providers_snapshot, categories) -> None`（schema `envgen.asset_selection_evidence.v1`）

- [ ] **Step 1: 写失败测试**

```python
import json
from pathlib import Path


def test_allocate_new_category_gets_next_number(tmp_path):
    lib = tmp_path / "library"
    (lib / "301_cup").mkdir(parents=True)
    (lib / "301_cup" / "model_data0.json").write_text("{}")
    asset, model = a2.allocate_asset("pitcher", lib, tmp_path / "m.json")
    assert (asset, model) == ("302_pitcher", 0)


def test_allocate_same_category_appends_model(tmp_path):
    lib = tmp_path / "library"
    (lib / "301_cup").mkdir(parents=True)
    (lib / "301_cup" / "model_data0.json").write_text("{}")
    asset, model = a2.allocate_asset("cup", lib, tmp_path / "m.json")
    assert (asset, model) == ("301_cup", 1)


def test_allocate_sees_pending_manifest(tmp_path):
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"groups": [{"name": "g", "prefix": "p", "items": [
        {"usd": "x.usd", "asset": "301_cup", "model": 0, "category": "cup", "aliases": ["cup"]}]}]}))
    assert a2.allocate_asset("bowl", tmp_path / "nolib", m) == ("302_bowl", 0)
    assert a2.allocate_asset("cup", tmp_path / "nolib", m) == ("301_cup", 1)


def test_manifest_group_and_append(tmp_path):
    c = cand(key="Assets/Props/YCB/Axis_Aligned/019_pitcher_base.usd")
    g = a2.build_manifest_group(c, "302_pitcher", 0, {"category": "pitcher", "aliases": ["pitcher"]})
    assert g["prefix"] == "Assets/Props/YCB/Axis_Aligned"
    assert g["items"][0] == {"usd": "019_pitcher_base.usd", "asset": "302_pitcher",
                             "model": 0, "category": "pitcher", "aliases": ["pitcher"]}
    p = a2.append_manifest(tmp_path / "acq.json", g)
    a2.append_manifest(p, g)
    assert len(json.loads(p.read_text())["groups"]) == 1


def test_write_evidence_schema(tmp_path):
    a2.write_evidence(tmp_path / "e.json", run_id="r1", providers_snapshot={"x": 1},
                      categories=[{"query": {"category": "cup"}, "status": "reused_local"}])
    d = json.loads((tmp_path / "e.json").read_text())
    assert d["schema"] == "envgen.asset_selection_evidence.v1"
    assert d["categories"][0]["status"] == "reused_local"
```

- [ ] **Step 2: 跑测试确认失败**（AttributeError: allocate_asset）

- [ ] **Step 3: 追加实现**

```python
import json
import re
from pathlib import Path


def allocate_asset(category, library_dir, manifest_path):
    numbers, model_counts = set(), {}

    def note(name, count):
        m = re.match(r"^(3\d\d)_(.+)$", name)
        if not m:
            return
        numbers.add(int(m.group(1)))
        model_counts[name] = max(model_counts.get(name, 0), count)

    lib = Path(library_dir)
    if lib.is_dir():
        for p in lib.iterdir():
            if p.is_dir():
                note(p.name, len(list(p.glob("model_data*.json"))))
    mp = Path(manifest_path)
    if mp.is_file():
        for g in json.loads(mp.read_text()).get("groups", []):
            for i in g["items"]:
                note(i["asset"], i["model"] + 1)
    for name, count in sorted(model_counts.items()):
        if name.split("_", 1)[1] == category:
            return name, count
    n = max(numbers, default=300) + 1
    return f"{n}_{category}", 0


def build_manifest_group(candidate, asset, model, entry):
    key = candidate.metadata["key"]
    item = {"usd": key.rsplit("/", 1)[-1], "asset": asset, "model": model,
            "category": entry["category"], "aliases": entry.get("aliases", [entry["category"]])}
    if entry.get("colors"):
        item["colors"] = entry["colors"]
    if entry.get("flat"):
        item["flat"] = True
    return {"name": f"acq_{asset}", "prefix": key.rsplit("/", 1)[0], "items": [item]}


def append_manifest(manifest_path, group):
    p = Path(manifest_path)
    data = json.loads(p.read_text()) if p.is_file() else {
        "comment": "auto-generated by acquire_batch", "groups": []}
    data["groups"] = [g for g in data["groups"] if g["name"] != group["name"]] + [group]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    return p


def write_evidence(path, *, run_id, providers_snapshot, categories):
    payload = {"schema": "envgen.asset_selection_evidence.v1", "run_id": run_id,
               "providers": providers_snapshot, "categories": categories}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=1, ensure_ascii=False))
```

- [ ] **Step 4: 跑测试确认通过**（18 passed 累计）

- [ ] **Step 5: Commit**（`feat(1_asset_reuse): a2 asset numbering, manifest generation, evidence writer`）

---

### Task 6: acquire_batch 编排 + M1 集成验收（pitcher 实引进）

**Files:**
- Create: `1_asset_reuse/scripts/acquire_batch.py`
- Create: `1_asset_reuse/configs/acquire_categories.json`
- Test: `1_asset_reuse/tests/test_acquire_batch.py`

**Interfaces:**
- Produces: CLI `acquire_batch.py --categories F --providers F --dev-root D --out DIR [--refresh-index]`；
  可注入函数 `process_entry(entry, tiers, globals_cfg, paths, runner) -> dict`（category 记录，status ∈ imported/reused_local/exhausted/search_failed）与 `main(argv, runner=None, tiers=None) -> int`；
  `runner(cmd: list[str], env: dict|None) -> int`（默认 subprocess）；
  `check_imported(library_dir, asset, model) -> bool`（判 `model_data{model}.json` + `visual/base{model}.glb` 存在——按内容判定）；
  paths 键：`py_sap/py_isa/scripts/library/source/out/manifest/fragment_dir`
- Consumes: Task 3 `load_providers/tiered_search`、Task 4/5 的 a2 全部接口；现有 `import_fetch_convert.py`（--manifest/--source-root/--staging）、`import_materialize.py`（--staging/--library-dir/--out/--overrides-fragment）、`s9_build_shadow_root.py`（--library-dir/--shadow/--ext-dir/--extra-overrides）

- [ ] **Step 1: 写 configs/acquire_categories.json（M1 验收清单）**

```json
[
  {"category": "cup", "aliases": ["cup", "mug"]},
  {"category": "pitcher", "aliases": ["pitcher"]}
]
```

- [ ] **Step 2: 写失败测试（注入 fake tiers + fake runner，全离线）**

```python
import json
from pathlib import Path

from agenticsim.openxsim.assets import AssetCandidate

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import acquire_batch as ab
from lib.a1_providers import Tier


def cand(key, score=1.0):
    return AssetCandidate(candidate_id=f"nvidia:{key}", name=key, category="x",
                          download_url=f"https://x/{key}", source_page="https://x",
                          format="usd", provider="nvidia_server", license="unknown",
                          score=score, metadata={"key": key, "size_bytes": 10})


class FakeProvider:
    def __init__(self, name, results):
        self.name, self.results = name, results

    def search(self, query, limit=20):
        return self.results


def paths(tmp_path):
    return {"py_sap": "PY_SAP", "py_isa": "PY_ISA",
            "scripts": Path(__file__).resolve().parents[1] / "scripts",
            "library": tmp_path / "library", "source": tmp_path / "source",
            "out": tmp_path / "out", "manifest": tmp_path / "acquired_manifest.json",
            "fragment_dir": tmp_path / "fragments"}


def test_reused_local_makes_no_pipeline_calls(tmp_path):
    calls = []
    tiers = [Tier(0, FakeProvider("t0", [cand("local")]))]
    rec = ab.process_entry({"category": "cup"}, tiers, {}, paths(tmp_path),
                           lambda cmd, env=None: calls.append(cmd) or 0)
    assert rec["status"] == "reused_local" and calls == []


def test_import_with_fallback_on_failed_materialize(tmp_path):
    p = paths(tmp_path)
    calls = []

    def runner(cmd, env=None):
        calls.append([str(c) for c in cmd])
        if "import_materialize.py" in str(cmd[1]) and len(
                [c for c in calls if "import_materialize.py" in c[1]]) == 2:
            d = p["library"] / "301_pitcher"
            (d / "visual").mkdir(parents=True, exist_ok=True)
            (d / "visual" / "base0.glb").write_bytes(b"x")
            (d / "model_data0.json").write_text("{}")
        return 0

    tiers = [Tier(0, FakeProvider("t0", [])),
             Tier(1, FakeProvider("t1", [cand("a/first.usd", 2.0), cand("a/second.usd", 1.0)]))]
    rec = ab.process_entry({"category": "pitcher"}, tiers, {"max_fallback": 2}, p, runner)
    assert rec["status"] == "imported"
    assert rec["attempts"] == 2
    assert rec["selected"]["candidate_id"].endswith("second.usd")
    failed = [c for c in rec["candidates"] if c["verdict"] == "rejected"]
    assert failed and failed[0]["rejection"]["code"].startswith("validation_failed")
    assert json.loads(p["manifest"].read_text())["groups"]


def test_exhausted_when_all_attempts_fail(tmp_path):
    tiers = [Tier(0, FakeProvider("t0", [])),
             Tier(1, FakeProvider("t1", [cand("a/x.usd")]))]
    rec = ab.process_entry({"category": "pitcher"}, tiers, {"max_fallback": 0},
                           paths(tmp_path), lambda cmd, env=None: 0)
    assert rec["status"] == "exhausted"
```

- [ ] **Step 3: 跑测试确认失败**（No module named acquire_batch）

- [ ] **Step 4: 实现 scripts/acquire_batch.py**

```python
#!/usr/bin/env python3
"""Batch acquire engine: category entries -> tiered search -> gates -> existing import pipeline.

Content-judged: per-category PASS/FAIL lines + SUMMARY; artifacts decide, not exit codes
of Kit subprocesses. Writes selection_evidence.json per run.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import a1_providers as a1  # noqa: E402
from lib import a2_selection as a2  # noqa: E402

PY_SAP = "/home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python"
PY_ISA = "/home/jingxiang/miniconda3/envs/isaac-smoke/bin/python"


def default_runner(cmd, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([str(c) for c in cmd], env=e).returncode


def check_imported(library_dir, asset, model):
    d = Path(library_dir) / asset
    return (d / f"model_data{model}.json").is_file() and (d / "visual" / f"base{model}.glb").is_file()


def process_entry(entry, tiers, globals_cfg, paths, runner):
    category = entry["category"]
    query = " ".join([category, *entry.get("colors", []), *entry.get("aliases", [])])
    rec = {"query": {"category": category, "aliases": entry.get("aliases", [category])},
           "entry_mode": "searched", "candidates": [], "attempts": 0}
    res = a1.tiered_search(tiers, query, viable_fn=lambda c: a2.gate(c, globals_cfg) is None,
                           limit=int(globals_cfg.get("top_k", 5)))
    rec["tiers_consulted"] = res["tiers_consulted"]
    rec["provider_errors"] = res["provider_errors"]
    if res["tier0_hit"] is not None:
        rec["status"] = "reused_local"
        rec["local_reuse"] = {"asset_id": res["tier0_hit"].metadata.get("asset_id"),
                              "reason": a2.ALREADY}
        return rec
    gated = a2.gate_candidates(res["candidates"], globals_cfg)
    viable = [r for r in gated if r["verdict"] == "viable"]
    if not viable:
        rec["status"] = "search_failed" if not res["candidates"] else "exhausted"
        rec["candidates"] = [{**a2.candidate_dict(r["candidate"]),
                              "verdict": r["verdict"], "rejection": r["rejection"]} for r in gated]
        return rec
    max_attempts = 1 + int(globals_cfg.get("max_fallback", 2))
    imported = None
    for r in viable[:max_attempts]:
        candidate = r["candidate"]
        rec["attempts"] += 1
        asset, model = a2.allocate_asset(category, paths["library"], paths["manifest"])
        group = a2.build_manifest_group(candidate, asset, model, entry)
        out = Path(paths["out"])
        out.mkdir(parents=True, exist_ok=True)
        staging = out / f"staging_{asset}_m{model}"
        tmp_manifest = out / f"manifest_{asset}_m{model}.json"
        tmp_manifest.write_text(json.dumps({"groups": [group]}, indent=1))
        fragment = Path(paths["fragment_dir"]) / f"{asset}_m{model}.yml"
        fragment.parent.mkdir(parents=True, exist_ok=True)
        runner([paths["py_isa"], "-u", paths["scripts"] / "import_fetch_convert.py",
                "--manifest", tmp_manifest, "--source-root", paths["source"],
                "--staging", staging], env={"OMNI_KIT_ACCEPT_EULA": "YES"})
        runner([paths["py_sap"], paths["scripts"] / "import_materialize.py",
                "--staging", staging, "--library-dir", paths["library"],
                "--out", out, "--overrides-fragment", fragment])
        if check_imported(paths["library"], asset, model):
            r["verdict"] = "selected"
            imported = (candidate, asset, model)
            a2.append_manifest(paths["manifest"], group)
            break
        r["verdict"] = "rejected"
        r["rejection"] = {"code": "validation_failed:materialize",
                          "detail": f"{asset} m{model} not materialized; see import matrix under {out}"}
    used = rec["attempts"]
    for r in viable[used if imported is None else max(used, 1):]:
        if r["verdict"] == "viable":
            r["verdict"] = "outranked"
            r["rejection"] = {"code": a2.REJ_OUTRANKED, "detail": "ranked below selected"}
    rec["candidates"] = [{**a2.candidate_dict(r["candidate"]),
                          "verdict": r["verdict"], "rejection": r.get("rejection")} for r in gated]
    if imported:
        candidate, asset, model = imported
        rec["status"] = "imported"
        rec["selected"] = {**a2.candidate_dict(candidate), "asset": asset, "model": model}
    else:
        rec["status"] = "exhausted"
    return rec


def main(argv=None, runner=None, tiers=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--categories", required=True)
    ap.add_argument("--providers", required=True)
    ap.add_argument("--dev-root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--refresh-index", action="store_true")
    a = ap.parse_args(argv)
    runner = runner or default_runner
    dev = Path(a.dev_root)
    cfg = json.loads(Path(a.providers).read_text())
    if tiers is None:
        tiers, globals_cfg = a1.load_providers(cfg)
        for t in tiers:
            if getattr(t.provider, "name", "") == "nvidia_server":
                t.provider.index_path = dev / t.provider.index_path if not Path(
                    t.provider.index_path).is_absolute() else Path(t.provider.index_path)
                t.provider.ensure_index(refresh=a.refresh_index)
    else:
        globals_cfg = cfg.get("globals", {})
    paths = {"py_sap": PY_SAP, "py_isa": PY_ISA,
             "scripts": Path(__file__).resolve().parent,
             "library": dev / "data" / "asset_library",
             "source": dev / "data" / "asset_library" / "_source",
             "out": Path(a.out), "manifest": dev / "1_asset_reuse" / "configs" / "acquired_manifest.json",
             "fragment_dir": Path(a.out) / "fragments"}
    entries = json.loads(Path(a.categories).read_text())
    results = [process_entry(e, tiers, globals_cfg, paths, runner) for e in entries]
    imported = [r for r in results if r["status"] == "imported"]
    if imported:
        merged = Path(a.out) / "overrides_ext_all.yml"
        merged.write_text("\n".join(p.read_text() for p in sorted(
            Path(paths["fragment_dir"]).glob("*.yml"))))
        runner([PY_SAP, paths["scripts"] / "s9_build_shadow_root.py",
                "--library-dir", paths["library"], "--shadow", dev / "data" / "robotwin_shadow",
                "--ext-dir", dev / "data" / "scene_gen_ext", "--extra-overrides", merged])
    a2.write_evidence(Path(a.out) / "selection_evidence.json", run_id=Path(a.out).name,
                      providers_snapshot=cfg, categories=results)
    ok = True
    for r in results:
        good = r["status"] in {"imported", "reused_local"}
        ok = ok and good
        print(f"{'PASS' if good else 'FAIL'} {r['query']['category']} status={r['status']}")
    print(f"SUMMARY {'PASS' if ok else 'FAIL'} imported={len(imported)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 跑单测确认通过**（21 passed 累计）

- [ ] **Step 6: M1 集成实跑（真网络 + 真 Isaac/SAPIEN，约 10-20 分钟）**

```bash
ssh lv-5090 "cd /home/jingxiang/yuxin/env-gen-dev-asset/1_asset_reuse && PYTHONPATH=.:/home/jingxiang/yuxin/env-gen-dev/shared/openxsim/source/agenticsim:/home/jingxiang/yuxin/env-gen-github /home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python scripts/acquire_batch.py --categories configs/acquire_categories.json --providers configs/providers.json --dev-root /home/jingxiang/yuxin/env-gen-dev-asset --out /home/jingxiang/yuxin/env-gen-dev-asset/results/_test/20260803_acquire_m1"
```
Expected（按内容判定）：
- stdout：`PASS cup status=reused_local`、`PASS pitcher status=imported`、`SUMMARY PASS imported=1`；
- `WT/data/asset_library/301_pitcher/{visual/base0.glb, model_data0.json}` 存在；
- `results/_test/20260803_acquire_m1/selection_evidence.json` 中 pitcher 的 candidates 逐条带 verdict，非选中者有 rejection；
- `WT/data/scene_gen_ext/asset_catalog.json` 存在且包含 `301_pitcher`（131 条：130 RoboTwin + pitcher）。
若 materialize 失败：查看 `--out` 下 import matrix JSON（文件名以实际产物为准，`ls` 该目录），把失败 gate 名对照 `validation_failed:materialize` 记录核对；若 s9 参数不符，先 `ssh lv-5090 "$PY_SAP .../s9_build_shadow_root.py --help"` 核对后调整 main() 中调用。

- [ ] **Step 7: 幂等重跑**

同命令 `--out .../20260803_acquire_m1_rerun`。Expected: `PASS pitcher status=reused_local`？——注意：tier 0 provider 读的是 providers.json 里配置的**主树** catalog（无 pitcher）。这是配置问题不是代码问题：把 `configs/providers.json` 的 `robotwin_local.catalog` 改为 `"data/scene_gen_ext/asset_catalog.json"`（相对 dev-root），并在 `load_providers` 后按 dev_root 解析（同 nvidia index_path 的处理：main() 里对 robotwin_local 也做相对路径解析，指向 WT 重建出的 catalog；文件不存在时回退主树绝对路径 `/home/jingxiang/yuxin/env-gen-dev/data/scene_gen_ext/asset_catalog.json`）。实现该解析逻辑 + 补一个单测（fake：dev_root 下有 catalog 时用它，没有时用 fallback），再重跑本步。Expected: `PASS cup status=reused_local`、`PASS pitcher status=reused_local`、`SUMMARY PASS imported=0`，evidence 里 pitcher 的 local_reuse.reason == "already_available_locally"。

- [ ] **Step 8: Commit**（`feat(1_asset_reuse): acquire_batch engine — M1 green (pitcher imported, idempotent rerun)`）

---

### Task 7: a4_coverage 需求提取 + 覆盖检查

**Files:**
- Create: `1_asset_reuse/lib/a4_coverage.py`
- Test: `1_asset_reuse/tests/test_a4_coverage.py`

**Interfaces:**
- Produces: `extract_needs(prompt, seed=0) -> tuple[SceneSpec, list[dict]]`（dict: object_id/category/color）；
  `check_coverage(spec, catalog_path) -> list[dict]`（覆盖记录：object_id/category/color/status("covered"|"gap")/asset_id/model_id/score 或 detail）；
  `gaps_to_entries(records) -> list[dict]`（去重后的 acquire 清单条目）；
  `write_coverage_report(path, prompt, seed, records)`（schema `envgen.scene_coverage.v1`）
- Consumes: 上游 `scene_gen.parser.parse_rule_based(prompt, seed=)`、`scene_gen.catalog.load_catalog(Path)`、`scene_gen.grounding.ground_object(obj, catalog, *, seed=) -> GroundedSelection`（无候选时 raise `scene_gen.schema.SceneSpecError`）；GroundedSelection 字段 `.entry.asset_id`、`.model.model_id`、`.score`

- [ ] **Step 1: 写失败测试（用真上游函数 + Task 2 的 mini_catalog fixture）**

```python
import json
from pathlib import Path

from lib import a4_coverage as a4

FIX = Path(__file__).parent / "fixtures" / "mini_catalog.json"


def test_extract_needs_red_mug():
    spec, needs = a4.extract_needs("Place a red mug on the table.", seed=42)
    assert needs[0]["category"] == "cup" and needs[0]["color"] == "red"


def test_coverage_covered_and_gap():
    spec, _ = a4.extract_needs("Place a red mug on the table.", seed=42)
    recs = a4.check_coverage(spec, FIX)
    assert recs[0]["status"] == "covered" and recs[0]["asset_id"]
    spec2, _ = a4.extract_needs("Place a hammer on the table.", seed=42)
    recs2 = a4.check_coverage(spec2, FIX)
    assert recs2[0]["status"] == "gap"


def test_gaps_to_entries_dedup_and_color():
    records = [
        {"object_id": "a", "category": "bowl", "color": None, "status": "gap", "detail": "x"},
        {"object_id": "b", "category": "bowl", "color": None, "status": "gap", "detail": "x"},
        {"object_id": "c", "category": "cup", "color": "blue", "status": "gap", "detail": "x"},
        {"object_id": "d", "category": "cup", "color": None, "status": "covered", "asset_id": "301_cup",
         "model_id": 0, "score": 100.0},
    ]
    entries = a4.gaps_to_entries(records)
    assert entries == [{"category": "bowl", "aliases": ["bowl"]},
                       {"category": "cup", "aliases": ["cup"], "colors": ["blue"]}]


def test_write_coverage_report(tmp_path):
    a4.write_coverage_report(tmp_path / "c.json", "p", 42,
                             [{"object_id": "a", "category": "bowl", "color": None,
                               "status": "gap", "detail": "x"}])
    d = json.loads((tmp_path / "c.json").read_text())
    assert d["schema"] == "envgen.scene_coverage.v1" and d["objects"][0]["status"] == "gap"
```

- [ ] **Step 2: 跑测试确认失败**（No module named lib.a4_coverage）

- [ ] **Step 3: 实现 a4_coverage.py**

```python
"""a4: scene-need extraction and coverage check via upstream grounding (read-only imports)."""
from __future__ import annotations

import json
from pathlib import Path

from scene_gen.catalog import load_catalog
from scene_gen.grounding import ground_object
from scene_gen.parser import parse_rule_based
from scene_gen.schema import SceneSpecError


def extract_needs(prompt, seed=0):
    spec = parse_rule_based(prompt, seed=seed)
    needs = [{"object_id": o.object_id, "category": o.category, "color": o.color}
             for o in spec.objects]
    return spec, needs


def check_coverage(spec, catalog_path):
    catalog = load_catalog(Path(catalog_path))
    records = []
    for obj in spec.objects:
        base = {"object_id": obj.object_id, "category": obj.category, "color": obj.color}
        try:
            sel = ground_object(obj, catalog, seed=spec.seed)
            records.append({**base, "status": "covered", "asset_id": sel.entry.asset_id,
                            "model_id": sel.model.model_id, "score": sel.score})
        except SceneSpecError as exc:
            records.append({**base, "status": "gap", "detail": str(exc)})
    return records


def gaps_to_entries(records):
    seen, entries = set(), []
    for r in records:
        if r["status"] != "gap":
            continue
        key = (r["category"], r.get("color"))
        if key in seen:
            continue
        seen.add(key)
        entry = {"category": r["category"], "aliases": [r["category"]]}
        if r.get("color"):
            entry["colors"] = [r["color"]]
        entries.append(entry)
    return entries


def write_coverage_report(path, prompt, seed, records):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(
        {"schema": "envgen.scene_coverage.v1", "prompt": prompt, "seed": seed,
         "objects": records}, indent=1, ensure_ascii=False))
```

- [ ] **Step 4: 跑测试确认通过**（25 passed 累计。若 `test_coverage_covered_and_gap` 中 hammer 在 mini_catalog 意外命中（cup/cabinet 之外），说明过滤没生效——回查 fixture 生成步骤）

- [ ] **Step 5: Commit**（`feat(1_asset_reuse): a4 coverage check reusing upstream parser+grounding`）

---

### Task 8: scene_acquire 编排 + M2 集成三连测

**Files:**
- Create: `1_asset_reuse/scripts/scene_acquire.py`
- Test: `1_asset_reuse/tests/test_scene_acquire.py`

**Interfaces:**
- Produces: CLI `scene_acquire.py --prompt P --seed N --catalog F --providers F --dev-root D --out DIR`；`main(argv, runner=None) -> int`；`runner(cmd, cwd=None, env=None) -> int`。产物：`coverage_report.json`（总是）、`acquire_categories.json` + `acquire/`（有缺口时）、`asset_gap_blocker.json`（耗尽时，schema `envgen.asset_gap_blocker.v1`）、`scenes/<scene_id>/resolved_scene.json`（成功时）。退出码：0=场景生成 PASS；1=缺口未满足（blocker）或场景生成失败。
- Consumes: Task 7 的 a4 全部接口；Task 6 的 acquire_batch CLI；上游 `script/generate_scene.py --prompt --seed --asset-catalog --out-root`（cwd 必须为 UP）

- [ ] **Step 1: 写失败测试（注入 runner，全离线）**

```python
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scene_acquire as sa

FIX = str(Path(__file__).parent / "fixtures" / "mini_catalog.json")


def run_main(tmp_path, prompt, runner):
    return sa.main(["--prompt", prompt, "--seed", "42", "--catalog", FIX,
                    "--providers", str(tmp_path / "p.json"), "--dev-root", str(tmp_path),
                    "--out", str(tmp_path / "out")], runner=runner)


def test_tier0_covered_generates_without_acquire(tmp_path):
    (tmp_path / "p.json").write_text("{}")
    calls = []

    def runner(cmd, cwd=None, env=None):
        calls.append([str(c) for c in cmd])
        scene = tmp_path / "out" / "scenes" / "s1"
        scene.mkdir(parents=True, exist_ok=True)
        (scene / "resolved_scene.json").write_text("{}")
        return 0

    rc = run_main(tmp_path, "Place a red mug on the table.", runner)
    assert rc == 0
    assert len(calls) == 1 and "generate_scene.py" in calls[0][1]
    report = json.loads((tmp_path / "out" / "coverage_report.json").read_text())
    assert report["objects"][0]["status"] == "covered"


def test_gap_triggers_acquire_then_blocker_when_still_missing(tmp_path):
    (tmp_path / "p.json").write_text("{}")
    calls = []
    rc = run_main(tmp_path, "Place a hammer on the table.",
                  lambda cmd, cwd=None, env=None: calls.append([str(c) for c in cmd]) or 0)
    assert rc == 1
    assert any("acquire_batch.py" in c[1] for c in calls)
    assert not any("generate_scene.py" in c[1] for c in calls)
    blocker = json.loads((tmp_path / "out" / "asset_gap_blocker.json").read_text())
    assert blocker["schema"] == "envgen.asset_gap_blocker.v1"
    assert blocker["unmet"][0]["category"] == "hammer"
```

（第二个测试里 acquire 后重查覆盖仍 gap——因为 fake runner 并没真的引进，且 dev-root 下无重建 catalog，回退用原 catalog——正是 blocker 分支。）

- [ ] **Step 2: 跑测试确认失败**（No module named scene_acquire）

- [ ] **Step 3: 实现 scripts/scene_acquire.py**

```python
#!/usr/bin/env python3
"""Scene-driven adaptive acquisition: prompt -> coverage -> acquire gaps -> generate scene."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import a4_coverage as a4  # noqa: E402

UP = "/home/jingxiang/yuxin/env-gen-github"


def default_runner(cmd, cwd=None, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([str(c) for c in cmd], cwd=cwd, env=e).returncode


def main(argv=None, runner=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--providers", required=True)
    ap.add_argument("--dev-root", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    runner = runner or default_runner
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    spec, _ = a4.extract_needs(a.prompt, a.seed)
    records = a4.check_coverage(spec, a.catalog)
    gaps = a4.gaps_to_entries(records)
    catalog = a.catalog
    if gaps:
        (out / "acquire_categories.json").write_text(json.dumps(gaps, indent=1, ensure_ascii=False))
        runner([sys.executable, Path(__file__).with_name("acquire_batch.py"),
                "--categories", out / "acquire_categories.json", "--providers", a.providers,
                "--dev-root", a.dev_root, "--out", out / "acquire"])
        rebuilt = Path(a.dev_root) / "data" / "scene_gen_ext" / "asset_catalog.json"
        if rebuilt.is_file():
            catalog = str(rebuilt)
        records = a4.check_coverage(spec, catalog)
    a4.write_coverage_report(out / "coverage_report.json", a.prompt, a.seed, records)
    remaining = [r for r in records if r["status"] == "gap"]
    if remaining:
        (out / "asset_gap_blocker.json").write_text(json.dumps(
            {"schema": "envgen.asset_gap_blocker.v1", "prompt": a.prompt, "seed": a.seed,
             "unmet": remaining,
             "note": "retrieval exhausted across all tiers; input for generation fallback"},
            indent=1, ensure_ascii=False))
        print(f"FAIL scene_acquire: {len(remaining)} unmet -> {out / 'asset_gap_blocker.json'}")
        return 1
    runner([sys.executable, "script/generate_scene.py", "--prompt", a.prompt,
            "--seed", str(a.seed), "--asset-catalog", catalog,
            "--out-root", out / "scenes"], cwd=UP)
    scenes = sorted((out / "scenes").glob("*/resolved_scene.json"))
    if scenes:
        print(f"PASS scene_acquire scene={scenes[-1].parent.name}")
        return 0
    print("FAIL scene_acquire: no resolved scene produced")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑单测确认通过**（27 passed 累计）

- [ ] **Step 5: M2 集成三连测（实跑）**

先造"过滤掉 bowl 的 catalog"（模拟真实缺口，自包含）：
```bash
ssh lv-5090 "cd /home/jingxiang/yuxin/env-gen-dev-asset && /home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python - <<'EOF'
import json
src = json.load(open('/home/jingxiang/yuxin/env-gen-dev/data/scene_gen_ext/asset_catalog.json'))
entries = src['entries'] if isinstance(src, dict) else src
kept = [e for e in entries if e['category'] != 'bowl' and 'bowl' not in e.get('aliases', [])]
print('removed', len(entries) - len(kept))
if isinstance(src, dict):
    src['entries'] = kept
    out = src
else:
    out = kept
import pathlib; pathlib.Path('results/_test').mkdir(parents=True, exist_ok=True)
json.dump(out, open('results/_test/catalog_no_bowl.json', 'w'), indent=1)
EOF"
```
三连测（`SCENE_CMD` 为下述公共前缀）：
```bash
SCENE_CMD='cd /home/jingxiang/yuxin/env-gen-dev-asset/1_asset_reuse && PYTHONPATH=.:/home/jingxiang/yuxin/env-gen-dev/shared/openxsim/source/agenticsim:/home/jingxiang/yuxin/env-gen-github /home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python scripts/scene_acquire.py --providers configs/providers.json --dev-root /home/jingxiang/yuxin/env-gen-dev-asset'
# ① tier0 快路径（主树全量 catalog，零引进）：
ssh lv-5090 "$SCENE_CMD --prompt 'Place a red mug on the table.' --seed 42 --catalog /home/jingxiang/yuxin/env-gen-dev/data/scene_gen_ext/asset_catalog.json --out ../results/_test/20260803_scene_tier0"
# ② 缺口自动引进（无 bowl catalog → 检索引进 YCB 024_bowl → 场景 PASS）：
ssh lv-5090 "$SCENE_CMD --prompt 'Place a bowl on the table.' --seed 42 --catalog ../results/_test/catalog_no_bowl.json --out ../results/_test/20260803_scene_acquire"
# ③ 四级耗尽 → blocker：
ssh lv-5090 "$SCENE_CMD --prompt 'Place a remote control on the table.' --seed 42 --catalog /home/jingxiang/yuxin/env-gen-dev/data/scene_gen_ext/asset_catalog.json --out ../results/_test/20260803_scene_blocker"
```
Expected（按内容判定）：
- ①：exit 0；coverage_report 全 covered；`out/acquire/` 不存在（零引进）；scenes/ 下有 resolved_scene.json。
- ②：exit 0；coverage_report 先 gap 后（重查）covered；`WT/data/asset_library/3XX_bowl/` 已物化；resolved_scene.json 中 bowl 对象的 asset_id 为新引进资产。注意 ② 依赖 Task 6 Step 7 的 catalog 解析逻辑（dev-root 重建 catalog 优先）。
- ③：exit 1；`asset_gap_blocker.json` 存在且 unmet[0].category == "remote_control"；scenes/ 不存在；`WT/data/` 无新增脏数据。

- [ ] **Step 6: Commit**（`feat(1_asset_reuse): scene_acquire — scene-driven adaptive acquisition, M2 green`）

---

### Task 9: a3_webfetch + GitHub 通路（M3 第一部分）

**Files:**
- Create: `1_asset_reuse/lib/a3_webfetch.py`
- Modify: `1_asset_reuse/scripts/acquire_batch.py`（process_entry 增加 web 候选分支）
- Modify: `1_asset_reuse/configs/providers.json`（github_tree.enabled → true）
- Test: `1_asset_reuse/tests/test_a3_webfetch.py`

**Interfaces:**
- Produces: `to_glb(src, dst) -> Path`（glb 直拷；gltf/obj 经 trimesh 转出）；
  `synth_staging_record(glb_path, source_path, source_sha, asset, model, entry, up_axis="Y") -> dict`（含 import_materialize 消费的全部字段：group/usd/usd_local/usd_sha256/asset/model/category/aliases/glb/glb_sha256/up_axis/status="converted"）；
  `stage_web_candidate(candidate, entry, asset, model, staging_dir, cache_dir, fetch_fn=None) -> dict`（fetch_fn 默认 openxsim `download_candidate`，写 `staging_dir/staging_manifest.json`）
- Consumes: openxsim `download_candidate(candidate, cache_dir) -> DownloadedAsset`（字段 `.path`、`.sha256`）；acquire_batch 分支规则：`candidate.provider` 以 `github` 开头 → 走 `stage_web_candidate` + import_materialize（跳过 import_fetch_convert）

- [ ] **Step 1: 写失败测试（fetch_fn 注入，全离线）**

```python
import json
from pathlib import Path
from types import SimpleNamespace

from agenticsim.openxsim.assets import AssetCandidate

from lib import a3_webfetch as a3


def gh_cand(name="Lantern.glb"):
    return AssetCandidate(candidate_id=f"github:x/{name}", name=name, category="lantern",
                          download_url=f"https://raw.test/{name}", source_page="https://gh",
                          format="glb", provider="github_tree", license="CC0", score=1.0,
                          metadata={"path": name})


def test_synth_record_has_materialize_fields(tmp_path):
    glb = tmp_path / "a.glb"
    glb.write_bytes(b"glTF")
    rec = a3.synth_staging_record(glb, tmp_path / "src.glb", "ab" * 32, "301_lantern", 0,
                                  {"category": "lantern", "aliases": ["lantern"]})
    for field in ("group", "usd", "usd_local", "usd_sha256", "asset", "model",
                  "category", "aliases", "glb", "glb_sha256", "up_axis", "status"):
        assert field in rec
    assert rec["status"] == "converted" and rec["up_axis"] == "Y"


def test_stage_web_candidate_writes_staging_manifest(tmp_path):
    src = tmp_path / "cache" / "Lantern.glb"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"glTF-binary-bytes")

    def fake_fetch(candidate, cache_dir):
        return SimpleNamespace(path=str(src), sha256="cd" * 32)

    rec = a3.stage_web_candidate(gh_cand(), {"category": "lantern", "aliases": ["lantern"]},
                                 "301_lantern", 0, tmp_path / "staging", tmp_path / "cache",
                                 fetch_fn=fake_fetch)
    manifest = json.loads((tmp_path / "staging" / "staging_manifest.json").read_text())
    assert manifest == [rec]
    assert Path(rec["glb"]).is_file()
```

- [ ] **Step 2: 跑测试确认失败**（No module named lib.a3_webfetch）

- [ ] **Step 3: 实现 a3_webfetch.py**

```python
"""a3: fetch web (GitHub) candidates and synthesize staging records for materialize."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def to_glb(src, dst):
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".glb":
        dst.write_bytes(src.read_bytes())
    else:
        import trimesh

        trimesh.load(str(src)).export(str(dst))
    return dst


def synth_staging_record(glb_path, source_path, source_sha, asset, model, entry, up_axis="Y"):
    return {"group": f"web_{asset}", "usd": Path(source_path).name,
            "usd_local": str(source_path), "usd_sha256": source_sha,
            "asset": asset, "model": model, "category": entry["category"],
            "aliases": entry.get("aliases", [entry["category"]]),
            "glb": str(glb_path), "glb_sha256": _sha256(glb_path),
            "up_axis": up_axis, "status": "converted"}


def stage_web_candidate(candidate, entry, asset, model, staging_dir, cache_dir, fetch_fn=None):
    if fetch_fn is None:
        from agenticsim.openxsim.assets import download_candidate as fetch_fn
    downloaded = fetch_fn(candidate, cache_dir)
    staging = Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)
    glb = to_glb(downloaded.path, staging / f"{asset}_m{model}.glb")
    record = synth_staging_record(glb, downloaded.path, downloaded.sha256, asset, model, entry)
    (staging / "staging_manifest.json").write_text(json.dumps([record], indent=1))
    return record
```

- [ ] **Step 4: acquire_batch 增加 web 分支**

在 `process_entry` 的 attempt 循环里，把"写 tmp_manifest + runner(import_fetch_convert)"两步改为按 provider 分支：

```python
        if str(candidate.provider).startswith("github"):
            from lib import a3_webfetch as a3w

            try:
                a3w.stage_web_candidate(candidate, entry, asset, model, staging,
                                        Path(paths["out"]) / "webcache")
            except Exception as exc:  # noqa: BLE001
                r["verdict"] = "rejected"
                r["rejection"] = {"code": a2.REJ_FETCH, "detail": f"{type(exc).__name__}: {exc}"}
                continue
        else:
            tmp_manifest.write_text(json.dumps({"groups": [group]}, indent=1))
            runner([paths["py_isa"], "-u", paths["scripts"] / "import_fetch_convert.py",
                    "--manifest", tmp_manifest, "--source-root", paths["source"],
                    "--staging", staging], env={"OMNI_KIT_ACCEPT_EULA": "YES"})
```
（materialize 调用与 check_imported 判定两分支共用，不变。web 分支的 manifest group 仍照常生成/追加——`metadata["key"]` 对 GitHub 候选不存在，`build_manifest_group` 会 KeyError：改为 `key = candidate.metadata.get("key") or candidate.metadata.get("path", candidate.name)`，本任务顺带修 + 在 test_a2_selection.py 补一个 github 候选 group 生成的单测。）

- [ ] **Step 5: 跑全部单测确认通过**（30+ passed）

- [ ] **Step 6: M3 GitHub 集成实跑**

`configs/providers.json` 置 `github_tree.enabled: true`，然后批跑一条 lantern：
```bash
ssh lv-5090 "cd /home/jingxiang/yuxin/env-gen-dev-asset/1_asset_reuse && echo '[{\"category\": \"lantern\", \"aliases\": [\"lantern\"]}]' > /tmp/acq_lantern.json && PYTHONPATH=.:/home/jingxiang/yuxin/env-gen-dev/shared/openxsim/source/agenticsim:/home/jingxiang/yuxin/env-gen-github /home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python scripts/acquire_batch.py --categories /tmp/acq_lantern.json --providers configs/providers.json --dev-root /home/jingxiang/yuxin/env-gen-dev-asset --out /home/jingxiang/yuxin/env-gen-dev-asset/results/_test/20260803_acquire_lantern"
```
Expected：tiers_consulted 含 [0,1,2]（本地无、NVIDIA 无、GitHub 命中 KhronosGroup/glTF-Sample-Assets 的 Lantern.glb）；`PASS lantern status=imported`；evidence 中该候选 license 为白名单配置值；`WT/data/asset_library/3XX_lantern/` 物化成功。若 Lantern 因倾斜门禁失败（灯笼模型重心问题）：这是**门禁正确工作**——改用同仓库别的直立模型（如 `Box.glb`→category "box" 会 tier0 命中，换 "avocado"：`Avocado.glb` 直立小模型）重跑并在 commit 信息注明；淘汰记录本身就是产出。

- [ ] **Step 7: Commit**（`feat(1_asset_reuse): a3 web fetch path — GitHub tier live (M3 part 1)`）

---

### Task 10: pinned/local 条目形态 + 文档 + 最终验收

**Files:**
- Modify: `1_asset_reuse/scripts/acquire_batch.py`（entry 三形态分派）
- Modify: `1_asset_reuse/lib/a2_selection.py`（pinned 候选合成）
- Modify: `1_asset_reuse/OVERVIEW.md`（新增"线 D：检索选定层"小节）
- Test: `1_asset_reuse/tests/test_acquire_batch.py`（追加）

**Interfaces:**
- Produces: entry 形态识别——`{"pinned": {"prefix", "usd"}}` → `a2.pinned_candidate(entry) -> AssetCandidate`（provider="nvidia_server"、metadata.key=prefix+"/"+usd、score=0、license 同 NVIDIA）、entry_mode="pinned"、无回退；`{"local": {"path", "up_axis"}}` → 直接 `a3_webfetch.synth_staging_record` 路线（复用 to_glb + staging）、entry_mode="local"、无回退。两形态都跳过 tiered_search，但**必须**过 gate + materialize 门禁。
- Consumes: Task 9 的 a3 接口；Task 4/5/6 全部

- [ ] **Step 1: 写失败测试**

```python
def test_pinned_entry_skips_search_but_gates(tmp_path):
    p = paths(tmp_path)

    def runner(cmd, env=None):
        if "import_materialize.py" in str(cmd[1]):
            d = p["library"] / "301_kettle"
            (d / "visual").mkdir(parents=True, exist_ok=True)
            (d / "visual" / "base0.glb").write_bytes(b"x")
            (d / "model_data0.json").write_text("{}")
        return 0

    entry = {"category": "kettle", "pinned": {
        "prefix": "Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned", "usd": "019_pitcher_base.usd"}}
    rec = ab.process_entry(entry, [], {}, p, runner)
    assert rec["entry_mode"] == "pinned"
    assert rec["status"] == "imported"
    assert rec["attempts"] == 1


def test_local_entry_materializes_from_file(tmp_path):
    p = paths(tmp_path)
    src = tmp_path / "teapot.glb"
    src.write_bytes(b"glTF-bytes")

    def runner(cmd, env=None):
        if "import_materialize.py" in str(cmd[1]):
            d = p["library"] / "301_teapot"
            (d / "visual").mkdir(parents=True, exist_ok=True)
            (d / "visual" / "base0.glb").write_bytes(b"x")
            (d / "model_data0.json").write_text("{}")
        return 0

    entry = {"category": "teapot", "local": {"path": str(src), "up_axis": "Y"}}
    rec = ab.process_entry(entry, [], {}, p, runner)
    assert rec["entry_mode"] == "local"
    assert rec["status"] == "imported"
    staging = list(Path(p["out"]).glob("staging_*/staging_manifest.json"))
    assert staging and json.loads(staging[0].read_text())[0]["up_axis"] == "Y"
```

- [ ] **Step 2: 跑测试确认失败**（entry_mode 仍为 searched / KeyError）

- [ ] **Step 3: 实现三形态分派**

a2_selection.py 追加：

```python
def pinned_candidate(entry):
    from agenticsim.openxsim.assets import AssetCandidate

    pin = entry["pinned"]
    key = f"{pin['prefix'].rstrip('/')}/{pin['usd']}"
    return AssetCandidate(
        candidate_id=f"pinned:{key}", name=pin["usd"], category=entry["category"],
        download_url=key, source_page=key, format="usd", provider="nvidia_server",
        license="unknown (NVIDIA Omniverse asset server; pinned_by_user)", score=0.0,
        metadata={"key": key, "pinned_by_user": True})
```

acquire_batch.py 的 `process_entry` 开头改为三形态分派（searched 逻辑保持原样抽为内部路径）：

```python
    if "pinned" in entry:
        rec = {"query": {"category": category}, "entry_mode": "pinned",
               "candidates": [], "attempts": 0, "tiers_consulted": [], "provider_errors": []}
        candidate = a2.pinned_candidate(entry)
        gated = a2.gate_candidates([candidate], globals_cfg)
        if gated[0]["verdict"] != "viable":
            rec["candidates"] = [{**a2.candidate_dict(candidate), "verdict": "rejected",
                                  "rejection": gated[0]["rejection"]}]
            rec["status"] = "exhausted"
            return rec
        return _attempt_import(rec, [gated[0]], entry, category, globals_cfg, paths,
                               runner, max_attempts=1)
    if "local" in entry:
        rec = {"query": {"category": category}, "entry_mode": "local",
               "candidates": [], "attempts": 1, "tiers_consulted": [], "provider_errors": []}
        from lib import a3_webfetch as a3w

        asset, model = a2.allocate_asset(category, paths["library"], paths["manifest"])
        out = Path(paths["out"])
        staging = out / f"staging_{asset}_m{model}"
        src = Path(entry["local"]["path"])
        if not src.is_file():
            rec["status"] = "exhausted"
            rec["candidates"] = [{"candidate_id": f"local:{src}", "provider": "local",
                                  "url": str(src), "format": src.suffix.lstrip("."),
                                  "license": "user-provided", "score": 0.0,
                                  "verdict": "rejected",
                                  "rejection": {"code": a2.REJ_FETCH, "detail": "file missing"}}]
            return rec
        glb = a3w.to_glb(src, staging / f"{asset}_m{model}.glb")
        record = a3w.synth_staging_record(glb, src, a3w._sha256(src), asset, model, entry,
                                          up_axis=entry["local"].get("up_axis", "Y"))
        (staging / "staging_manifest.json").write_text(json.dumps([record], indent=1))
        fragment = Path(paths["fragment_dir"]) / f"{asset}_m{model}.yml"
        fragment.parent.mkdir(parents=True, exist_ok=True)
        runner([paths["py_sap"], paths["scripts"] / "import_materialize.py",
                "--staging", staging, "--library-dir", paths["library"],
                "--out", out, "--overrides-fragment", fragment])
        if check_imported(paths["library"], asset, model):
            rec["status"] = "imported"
            rec["selected"] = {"candidate_id": f"local:{src}", "provider": "local",
                               "url": str(src), "format": "glb", "license": "user-provided",
                               "score": 0.0, "asset": asset, "model": model}
        else:
            rec["status"] = "exhausted"
        return rec
```

（重构说明：把原 searched 的 attempt 循环抽成 `_attempt_import(rec, viable, entry, category, globals_cfg, paths, runner, max_attempts)`，searched 与 pinned 共用；重构后先跑全部旧单测确认不破坏。）

- [ ] **Step 4: 跑全部单测确认通过**

- [ ] **Step 5: M3 集成——pinned + local 实跑**

```bash
ssh lv-5090 "cd /home/jingxiang/yuxin/env-gen-dev-asset/1_asset_reuse && cat > /tmp/acq_m3.json <<'EOF'
[
  {"category": "kettle", "pinned": {"prefix": "Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned", "usd": "019_pitcher_base.usd"}},
  {"category": "teacup", "local": {"path": "/home/jingxiang/yuxin/env-gen-dev/data/asset_library/301_cup/visual/base0.glb", "up_axis": "Y"}}
]
EOF
PYTHONPATH=.:/home/jingxiang/yuxin/env-gen-dev/shared/openxsim/source/agenticsim:/home/jingxiang/yuxin/env-gen-github /home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python scripts/acquire_batch.py --categories /tmp/acq_m3.json --providers configs/providers.json --dev-root /home/jingxiang/yuxin/env-gen-dev-asset --out /home/jingxiang/yuxin/env-gen-dev-asset/results/_test/20260803_acquire_m3"
```
Expected：两条均 `status=imported`（kettle 物化为 pinned 的 pitcher USD、teacup 从主树 mug GLB 物化）；evidence 中 kettle 候选 metadata 带 pinned_by_user、teacup provider 为 local。

- [ ] **Step 6: OVERVIEW.md 增"线 D"小节**

在 `## 3 阶段总览` 表格后追加（照现有文风）：

```markdown
| D | ⑫ 检索选定层（四级 provider→门禁→自动引进） | `lib/a1–a4` + `acquire_batch.py` |
| D | ⑬ 场景驱动自适应（prompt→覆盖→缺口引进→场景） | `scene_acquire.py` |
```
并在"分阶段详解"末尾追加一段（3-5 句）：说明四级信任梯度、selection_evidence/coverage_report/asset_gap_blocker 三产物、与设计文档 `docs/2026-08-03-asset-retrieval-integration-design.md` 的对应关系。

- [ ] **Step 7: 最终验收（设计 §10 全清单复跑）**

依次重跑：全部单测（一条命令）→ M1 批跑（Task 6 Step 6 的 out 换 `_final`）→ M2 三连（Task 8 Step 5）→ M3 两条（本任务 Step 5）。全部 Expected 达成后记录各 evidence/report 路径清单到 commit 信息。

- [ ] **Step 8: Commit + 汇报**

```bash
ssh lv-5090 "cd /home/jingxiang/yuxin/env-gen-dev-asset && git add -A 1_asset_reuse && git commit -m 'feat(1_asset_reuse): pinned/local entry forms + docs — retrieval layer v1 complete

M1 batch engine (NVIDIA searched), M2 scene-driven adaptive (tier0/acquire/blocker
all green), M3 GitHub path + pinned/local. Evidence artifacts under results/_test/.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>'"
```
完成后向用户汇报，并提请两个人工决策：① feat/asset-retrieval 分支何时合回/如何与 feat/env-gen-ir-bridge 协调；② worktree 里引进的资产（data/asset_library）要不要同步回主树 data/。

---

## Self-Review 已核对项

- 设计 §2 八项决策均有对应任务（四级梯度=T3、全自动+回退=T6、场景驱动=T7/8、pinned/local=T10、GitHub=T9、许可证 license_gate=T4、结构化 blocker=T8、幂等=T6 Step 7）。
- 设计 §7 淘汰码：`validation_failed:<gate>` 当前粒度为 `validation_failed:materialize`（细分 gate 名需读 materialize 的 import matrix 文件名，T6 Step 6 给了核对指引）——此为 v1 已知粗化，evidence 里 detail 指向矩阵文件，可追溯。
- 类型/签名一致性：`process_entry(entry, tiers, globals_cfg, paths, runner)`、`tiered_search(..., viable_fn=..., limit=...)`、`gate(candidate, globals_cfg)` 在 T3/4/6/10 间一致；`paths` 字典键在 T6 定义、T10 复用一致。
- 风险预案内置：AssetScout last_errors 行为差异（T3 Step 4 注）、s9/materialize 参数漂移（T6 Step 6 核对指引）、Lantern 倾斜门禁（T9 Step 6 备选 Avocado）、catalog 解析 dev-root 优先（T6 Step 7）。
