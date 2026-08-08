# asset_ledger.v1 入库 metadata 契约 · 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 `docs/2026-08-08-asset-ingest-metadata-contract-design.md`——账本成为唯一权威入库记录（schema v1 + validator + 验证回填），fragment 变为生成物。

**Architecture:** 新增 `1_asset_reuse/lib/ledger.py`（schema 常量 + builder + validator + verification append，纯 stdlib），backfill 脚本把现存池资产升 v1 并落到权威位置 `data/asset_library/<asset>/ledger_m<N>.json`，`gen_fragment.py` 从账本生成 overrides fragment，最后把 `import_materialize` / `s13b` / `s11` 切到 builder/append 路径。

**Tech Stack:** Python 3.10（conda `env-gen-yuxin`）、pytest、PyYAML（仅脚本层用；`lib/ledger.py` 纯 stdlib 同 `conventions.py` 惯例）。

## Global Constraints

- 工作目录：lv-5090 `/home/jingxiang/yuxin/env-gen-dev`；开发分支：`feat/asset-ledger-v1`（自 `feat/env-gen-ir-bridge` 切出）。
- 运行环境：`conda activate env-gen-yuxin`（全部任务；本计划不含 Kit/isaac-smoke 步骤）。
- `1_asset_reuse/lib/` 下模块**纯 stdlib**（双 conda 环境可 import，同 `conventions.py` 先例）；PyYAML 只允许出现在 `scripts/`。
- 上游只读：不改 `external/env-gen-github` 任何文件；fragment YAML 的**输出格式必须与现有 `data/scene_gen_ext/external_overrides_fragment_merged.yml` 语义一致**（s9 消费方零改动）。
- 工作区有他人未提交改动（`1_asset_reuse/OVERVIEW.md`、`configs/providers.json`）：**禁止 `git add -A`**，一律逐文件 add；本计划不改 OVERVIEW.md。
- 账本权威位置（spec §4 补充，Task 1 入档）：`data/asset_library/<asset>/ledger_m<N>.json`；`results/<run>/bundles/` 保留为运行快照。
- 池层只进不出：backfill 与验证只追加/升级，不删除任何资产或账本。
- 未知值结构化：`{"value": null, "status": "unknown", "runtime_default*": ...}`，禁止编造（friction 的 runtime_default 为 null + note，引擎默认材质，不冒充实测）。
- 测试运行方式：`cd 1_asset_reuse && python -m pytest tests/<file> -v`（现有 42 测试必须保持通过）。

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `docs/2026-08-08-asset-ingest-metadata-contract-design.md` | 修改（§4 一行） | 入档账本权威位置 |
| `1_asset_reuse/lib/ledger.py` | 新建 | schema 常量、`Violation`、`validate_bundle`、`derive_usable`、`new_bundle` builder、`append_verification`、`ledger_path` |
| `1_asset_reuse/tests/test_ledger.py` | 新建 | validator 正反例、builder、append、派生 usable |
| `1_asset_reuse/scripts/backfill_ledger_v1.py` | 新建 | 现存池资产账本升 v1 落权威位置（dry-run 默认） |
| `1_asset_reuse/tests/test_backfill_ledger.py` | 新建 | tmp 迷你池 backfill 正确性 + 幂等 |
| `1_asset_reuse/scripts/gen_fragment.py` | 新建 | 全量账本 → fragment YAML（聚合一致性检查 + settle-pass 过滤） |
| `1_asset_reuse/tests/test_gen_fragment.py` | 新建 | 生成结构、不一致报错、过滤规则 |
| `1_asset_reuse/scripts/import_materialize.py` | 修改（378-441、493 一带） | 落账走 `new_bundle` + validator 门 + 写权威位置 + fragment 改调 gen_fragment |
| `1_asset_reuse/scripts/s13b_validate_articulated.py` | 修改（bundle 写出处） | 关节体 v1 落账 + `balance_gate` |
| `1_asset_reuse/scripts/s11_runtime_load_sweep.py` | 修改（96 行后） | 每资产 append `runtime_load` verification |
| `1_asset_reuse/README.md` | 修改（用法节） | 账本契约用法 + backfill/gen_fragment 命令 |

**Interfaces 总览（后续任务消费 Task 2 的产出）：**

```python
# 1_asset_reuse/lib/ledger.py
SCHEMA_VERSION = "asset_ledger.v1"
X90_WXYZ = [0.7071067811865476, 0.7071067811865476, 0.0, 0.0]   # re-export from conventions
IDENTITY_WXYZ = [1.0, 0.0, 0.0, 0.0]

@dataclass
class Violation:
    path: str      # "physical.conventions.stable_orientation_wxyz"
    code: str      # "missing" | "needs_backfill" | "bad_quaternion" | "id_model_mismatch" | ...
    message: str

def ledger_path(library_dir: str | Path, asset: str, model: int) -> Path
    # -> <library_dir>/<asset>/ledger_m<model>.json

def validate_bundle(bundle: dict, *, check_files: bool = True) -> list[Violation]
def derive_usable(bundle: dict) -> tuple[bool, list[str]]        # (usable, missing)
def new_bundle(**kwargs) -> dict                                  # 签名见 Task 2 Step 5
def append_verification(path: str | Path, entry: dict) -> dict    # 读-追加-原子写，返回新账本
```

---

### Task 1: 分支 + spec 补充「账本权威位置」

**Files:**
- Modify: `docs/2026-08-08-asset-ingest-metadata-contract-design.md`（§4 组件表下方）

**Interfaces:** Produces: 常量事实——账本权威位置 `data/asset_library/<asset>/ledger_m<N>.json`，后续所有任务引用。

- [ ] **Step 1: 切分支**

```bash
cd /home/jingxiang/yuxin/env-gen-dev
git checkout feat/env-gen-ir-bridge && git checkout -b feat/asset-ledger-v1
```

- [ ] **Step 2: spec §4 尾部（「`model_data0.json` 不是派生视图」段之前）插入**

```markdown
**账本权威位置**：`data/asset_library/<asset>/ledger_m<N>.json`——账本与资产本体同目录、随池走；
`results/<run>/bundles/` 里的 bundle 保留为当次运行快照，不作权威。读者（gen_fragment / openxsim / s11）一律读权威位置。
```

- [ ] **Step 3: Commit**

```bash
git add docs/2026-08-08-asset-ingest-metadata-contract-design.md
git commit -m "docs(spec): 补充账本权威位置 data/asset_library/<asset>/ledger_m<N>.json"
```

---

### Task 2: `lib/ledger.py` — schema 常量 + validator + builder

**Files:**
- Create: `1_asset_reuse/lib/ledger.py`
- Test: `1_asset_reuse/tests/test_ledger.py`

**Interfaces:**
- Consumes: `lib/conventions.py` 的 `X90_WXYZ` / `IDENTITY_WXYZ`（re-export，不复制数值）。
- Produces: 见「Interfaces 总览」。`new_bundle` 完整签名在 Step 5。

- [ ] **Step 1: 写失败测试——合法账本 0 violation + 反例参数表**

`tests/test_ledger.py`（`make_valid()` 是全部测试的基底，字段值抄自 spec §3 必填表）：

```python
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger


def make_valid(**over):
    b = {
        "schema_version": "asset_ledger.v1",
        "asset_id": "external_315_shears_m0",
        "model_id": 0,
        "category": "shears",
        "semantic_name": "shears",
        "kind": "rigid",
        "tags": ["rigid", "external", "batch"],
        "semantics": {"aliases": ["shears", "scissors"], "colors": [], "materials": []},
        "physical": {
            "mesh_bbox_m": [0.078, 0.051, 0.053],
            "mesh_up_axis": "Y",
            "origin_convention": "bottom-center",
            "scale_applied": 1.0,
            "size_resolution": {"mode": "match_category", "actual_max_dim_m": 0.078,
                                "scale": 1.0, "reference_max_dim_m": None,
                                "reference_assets": [], "verdict": "no_precedent"},
            "conventions": {"is_static": False, "z_policy": "origin_on_table",
                            "footprint_shape": "box", "stable_pose_id": "upright",
                            "stable_orientation_wxyz": ledger.X90_WXYZ,
                            "inherited_from": None},
            "mass_kg": {"value": None, "status": "unknown", "runtime_default_kg": 0.1},
            "friction": {"value": None, "status": "unknown", "runtime_default": None},
        },
        "representations": [
            {"format": "glb", "uri": "/tmp/x/visual.glb", "backend": "sapien",
             "role": "visual", "sha256": "0" * 64, "size_bytes": 10,
             "metadata": {"derived_from": "src.usd", "converter": "omni.kit.asset_converter@isaac-5.1",
                          "conversion_params": {"rotated_z2y": True}}},
        ],
        "articulation": {},
        "source": {"library": "NVIDIA Isaac Assets 5.1", "group": "acq_315_shears",
                   "file": "061_foam_brick.usd",
                   "license": {"spdx": None, "status": "unknown",
                               "terms_note": "NVIDIA asset EULA; YCB terms for ycb group"},
                   "retrieved_at": "2026-08-08",
                   "source_manifest_path": "/tmp/x/SOURCE_MANIFEST.json"},
        "verification": [
            {"backend": "sapien", "check": "settle", "verdict": "pass",
             "date": "2026-08-08", "report_path": "/tmp/x/report.json"},
        ],
    }
    b.update(over)
    return b


def test_valid_bundle_no_violations():
    assert ledger.validate_bundle(make_valid(), check_files=False) == []


def _del(path):
    def f(b):
        node = b
        parts = path.split(".")
        for p in parts[:-1]:
            node = node[p]
        del node[parts[-1]]
    return f


def _set(path, value):
    def f(b):
        node = b
        parts = path.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = value
    return f


CASES = [
    (_del("schema_version"), "needs_backfill"),
    (_set("schema_version", "asset_ledger.v0"), "bad_schema_version"),
    (_set("model_id", 1), "id_model_mismatch"),          # asset_id 后缀是 _m0
    (_del("semantic_name"), "missing"),
    (_set("kind", "soft"), "bad_enum"),
    (_set("semantics.aliases", []), "empty_aliases"),
    (_set("physical.conventions.stable_orientation_wxyz", [1, 1, 0, 0]), "bad_quaternion"),
    (_del("physical.conventions.stable_pose_id"), "missing"),
    (_del("physical.friction"), "missing"),
    (_set("physical.mass_kg", {"value": None, "status": "known", "runtime_default_kg": 0.1}),
     "unknown_shape"),                                    # status=known 但 value=null
    (_set("representations", []), "no_sapien_representation"),
    (_set("source.license", "unknown"), "license_not_structured"),
    (_del("source.retrieved_at"), "missing"),
    (_set("verification", [{"backend": "sapien", "check": "fly", "verdict": "pass",
                            "date": "2026-08-08", "report_path": "r.json"}]), "bad_enum"),
    (_set("usable", True), "derived_field_handwritten"),  # usable 禁手写：仅当与推导不符时报
]


@pytest.mark.parametrize("mutate,code", CASES)
def test_violations(mutate, code):
    b = make_valid()
    mutate(b)
    codes = [v.code for v in ledger.validate_bundle(b, check_files=False)]
    assert code in codes, f"expected {code}, got {codes}"


def test_articulated_requires_articulation():
    b = make_valid(kind="articulated", asset_id="external_314_cabinet_m0",
                   category="cabinet", semantic_name="cabinet")
    codes = [v.code for v in ledger.validate_bundle(b, check_files=False)]
    assert "articulation_required" in codes


def test_derive_usable():
    ok, missing = ledger.derive_usable(make_valid())
    assert ok and missing == []
    b = make_valid()
    del b["physical"]["conventions"]["stable_orientation_wxyz"]
    ok, missing = ledger.derive_usable(b)
    assert not ok and "physical.conventions.stable_orientation_wxyz" in missing


def test_check_files_sha_mismatch(tmp_path):
    f = tmp_path / "visual.glb"
    f.write_bytes(b"mesh")
    b = make_valid()
    b["representations"][0]["uri"] = str(f)          # sha256 仍是 0*64 → 不匹配
    codes = [v.code for v in ledger.validate_bundle(b, check_files=True)]
    assert "sha256_mismatch" in codes
    b2 = make_valid()
    b2["representations"][0]["uri"] = str(tmp_path / "gone.glb")
    codes2 = [v.code for v in ledger.validate_bundle(b2, check_files=True)]
    assert "file_missing" in codes2


def test_ledger_path():
    p = ledger.ledger_path("/lib", "315_shears", 0)
    assert str(p) == "/lib/315_shears/ledger_m0.json"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /home/jingxiang/yuxin/env-gen-dev/1_asset_reuse
python -m pytest tests/test_ledger.py -v
```
Expected: 收集即失败——`ModuleNotFoundError`/`ImportError: lib.ledger`。

- [ ] **Step 3: 实现 `lib/ledger.py`（validator 部分）**

结构照 `conventions.py` 惯例（模块 docstring 说明设计规则 + 纯 stdlib）。核心骨架：

```python
"""asset_ledger.v1 —— 入库账本契约：常量、校验、构造、验证回填。

设计规则（见 docs/2026-08-08-asset-ingest-metadata-contract-design.md）：
- 账本是唯一权威入库记录；usable/missing 只由 derive_usable 推导，手写即报错。
- 查不到的值结构化 unknown，不编造。
- 纯 stdlib：双 conda 环境均可 import（同 conventions.py 先例）。
"""
import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .conventions import X90_WXYZ, IDENTITY_WXYZ  # re-export，数值单一来源

SCHEMA_VERSION = "asset_ledger.v1"
KINDS = ("rigid", "articulated")
BACKENDS = ("sapien", "isaacsim", "portable")
ROLES = ("visual", "collision", "visual_and_collision")
CHECKS = ("settle", "joint_sweep", "runtime_load", "e2e", "admission_report")
VERDICTS = ("pass", "fail")
_SHA = re.compile(r"[0-9a-f]{64}$")
_ID_SUFFIX = re.compile(r"_m(\d+)$")

# (path, 适用kind或None) —— derive_usable 与 validate_bundle 共用同一张必填表
REQUIRED = [
    ("asset_id", None), ("model_id", None), ("category", None),
    ("semantic_name", None), ("kind", None), ("tags", None),
    ("semantics.aliases", None),
    ("physical.mesh_bbox_m", None), ("physical.mesh_up_axis", None),
    ("physical.origin_convention", None), ("physical.scale_applied", None),
    ("physical.size_resolution", None),
    ("physical.conventions.is_static", None), ("physical.conventions.z_policy", None),
    ("physical.conventions.footprint_shape", None),
    ("physical.conventions.stable_pose_id", None),
    ("physical.conventions.stable_orientation_wxyz", None),
    ("physical.conventions.inherited_from", None),
    ("physical.mass_kg", None), ("physical.friction", None),
    ("source.library", None), ("source.group", None), ("source.file", None),
    ("source.license", None), ("source.retrieved_at", None),
    ("source.source_manifest_path", None),
]

@dataclass
class Violation:
    path: str
    code: str
    message: str
```

`validate_bundle(bundle, *, check_files=True)` 按顺序检查（每条对应 Step 1 的一个反例）：
1. `schema_version` 缺失 → `needs_backfill`；不等于 `SCHEMA_VERSION` → `bad_schema_version`（这两种情况**直接返回**，不再往下用 v1 规则误报）。
2. `REQUIRED` 表逐条查存在（点路径逐层 `dict.get`），缺 → `missing`。
3. `kind` ∈ KINDS 否则 `bad_enum`；`kind=="articulated"` 且 `articulation` 缺 `joint_names` → `articulation_required`。
4. `asset_id` 后缀 `_m(\d+)` 与 `model_id` 不等 → `id_model_mismatch`。
5. `semantics.aliases` 空列表 → `empty_aliases`。
6. `stable_orientation_wxyz`：长度 4 且 |Σx²−1| ≤ 1e-6，否则 `bad_quaternion`。
7. `mass_kg`/`friction`：必含 `value`/`status` 键；`status=="known"` 而 `value is None` → `unknown_shape`；`status` ∉ ("known","unknown") → `bad_enum`。
8. representations：至少一条 `backend=="sapien"` 否则 `no_sapien_representation`；每条查 `format/uri/role/sha256/size_bytes`（`role` ∈ ROLES、`backend` ∈ BACKENDS、sha 匹配 `_SHA`、size ≥ 0）。
9. `source.license` 不是 dict 或缺 `spdx/status/terms_note` → `license_not_structured`；`status` ∉ ("declared","unknown") → `bad_enum`。
10. `verification` 每条查 `backend/check/verdict/date/report_path`；`check` ∉ CHECKS 或 `verdict` ∉ VERDICTS → `bad_enum`。
11. 账本里出现 `usable`/`missing` 键且与 `derive_usable` 推导不一致 → `derived_field_handwritten`。
12. `check_files=True` 时逐 representation：`Path(uri)` 不存在 → `file_missing`；存在且 sha256 实算不等 → `sha256_mismatch`（sha 用 `hashlib.sha256(f.read_bytes())`）。

`derive_usable(bundle)`：对 `REQUIRED` + kind 条件项跑存在性检查（等价 validate 第 2/3 条），返回 `(len(missing)==0, missing)`。

`ledger_path(library_dir, asset, model)`：`Path(library_dir) / asset / f"ledger_m{model}.json"`。

- [ ] **Step 4: 跑测试**

```bash
python -m pytest tests/test_ledger.py -v
```
Expected: Step 1 全部 PASS（builder/append 测试还没写，此时文件里只有上面这些）。

- [ ] **Step 5: 补 builder + append 的失败测试，再实现**

追加到 `tests/test_ledger.py`：

```python
def test_new_bundle_valid_rigid():
    b = ledger.new_bundle(
        asset="315_shears", model=0, category="shears", kind="rigid",
        aliases=["shears"], colors=[], materials=[],
        representations=make_valid()["representations"],
        mesh_bbox_m=[0.078, 0.051, 0.053], mesh_up_axis="Y",
        origin_convention="bottom-center", scale_applied=1.0,
        size_resolution=make_valid()["physical"]["size_resolution"],
        conventions=make_valid()["physical"]["conventions"],
        source=make_valid()["source"], tags=["rigid", "external"],
        verification=[],
    )
    assert b["asset_id"] == "external_315_shears_m0" and b["model_id"] == 0
    assert b["semantic_name"] == "shears"          # 缺省 = category
    assert ledger.validate_bundle(b, check_files=False) == []


def test_new_bundle_articulated_requires_articulation():
    with pytest.raises(ValueError):
        ledger.new_bundle(asset="314_cabinet", model=0, category="cabinet",
                          kind="articulated", aliases=["cabinet"], colors=[],
                          materials=[], representations=make_valid()["representations"],
                          mesh_bbox_m=[1, 1, 1], mesh_up_axis="Z",
                          origin_convention="bottom-center", scale_applied=1.0,
                          size_resolution=make_valid()["physical"]["size_resolution"],
                          conventions=make_valid()["physical"]["conventions"],
                          source=make_valid()["source"], tags=["articulated"],
                          verification=[])         # articulation 缺失


def test_append_verification(tmp_path):
    p = tmp_path / "ledger_m0.json"
    p.write_text(json.dumps(make_valid()))
    e = {"backend": "sapien", "check": "runtime_load", "verdict": "pass",
         "date": "2026-08-08", "report_path": "r2.json"}
    out = ledger.append_verification(p, e)
    assert len(out["verification"]) == 2           # append-only
    out2 = ledger.append_verification(p, e)
    assert len(out2["verification"]) == 3          # 重复追加也不覆盖
    assert json.loads(p.read_text()) == out2
```

实现：

```python
def new_bundle(*, asset, model, category, kind, aliases, colors, materials,
               representations, mesh_bbox_m, mesh_up_axis, origin_convention,
               scale_applied, size_resolution, conventions, source, tags,
               verification, semantic_name=None, articulation=None,
               mass_runtime_default_kg=0.1):
    if kind == "articulated" and not (articulation or {}).get("joint_names"):
        raise ValueError("articulated bundle requires articulation.joint_names")
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "asset_id": f"external_{asset}_m{model}",
        "model_id": int(model),
        "category": category,
        "semantic_name": semantic_name or category,
        "kind": kind,
        "tags": list(tags),
        "semantics": {"aliases": list(aliases), "colors": list(colors),
                      "materials": list(materials)},
        "physical": {
            "mesh_bbox_m": mesh_bbox_m, "mesh_up_axis": mesh_up_axis,
            "origin_convention": origin_convention, "scale_applied": scale_applied,
            "size_resolution": size_resolution, "conventions": conventions,
            "mass_kg": {"value": None, "status": "unknown",
                        "runtime_default_kg": mass_runtime_default_kg},
            "friction": {"value": None, "status": "unknown", "runtime_default": None,
                         "note": "engine default material; not overridden by pipeline"},
        },
        "representations": list(representations),
        "articulation": articulation or {},
        "source": source,
        "verification": list(verification),
    }
    return bundle


def append_verification(path, entry):
    path = Path(path)
    bundle = json.loads(path.read_text())
    bundle.setdefault("verification", []).append(dict(entry))
    tmp = Path(tempfile.mkstemp(dir=path.parent, suffix=".tmp")[1])
    tmp.write_text(json.dumps(bundle, indent=2, ensure_ascii=False))
    tmp.replace(path)                              # 原子替换
    return bundle
```

- [ ] **Step 6: 全量跑本文件测试 + 既有测试回归**

```bash
python -m pytest tests/test_ledger.py -v && python -m pytest tests/ -q
```
Expected: test_ledger 全 PASS；既有 42 测试不破。

- [ ] **Step 7: Commit**

```bash
git add 1_asset_reuse/lib/ledger.py 1_asset_reuse/tests/test_ledger.py
git commit -m "feat(ledger): asset_ledger.v1 schema 常量 + validator + builder + verification append"
```

---

### Task 3: `backfill_ledger_v1.py` — 现存池资产升 v1

**Files:**
- Create: `1_asset_reuse/scripts/backfill_ledger_v1.py`
- Test: `1_asset_reuse/tests/test_backfill_ledger.py`

**Interfaces:**
- Consumes: `ledger.validate_bundle` / `ledger.ledger_path` / `ledger.SCHEMA_VERSION`（Task 2）。
- Produces: 权威账本文件 `data/asset_library/<asset>/ledger_m<N>.json`（Task 4/6/7 读它）；报告 JSON `{"assets": [...], "violations": {...}, "written": N}`。

- [ ] **Step 1: 写失败测试（tmp 迷你池）**

`tests/test_backfill_ledger.py`：

```python
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "1_asset_reuse/scripts/backfill_ledger_v1.py"


def _mini_pool(tmp_path):
    lib = tmp_path / "asset_library"
    a = lib / "399_widget"
    (a / "visual").mkdir(parents=True)
    (a / "collision").mkdir(parents=True)
    vis = a / "visual/base0.glb"; vis.write_bytes(b"V")
    col = a / "collision/base0.glb"; col.write_bytes(b"V")
    (a / "model_data0.json").write_text(json.dumps({"extents": [0.1, 0.2, 0.1]}))
    src = lib / "_source/acq_399_widget"
    src.mkdir(parents=True)
    (src / "SOURCE_MANIFEST.json").write_text(json.dumps({"files": {"w.usd": "ab" * 32}}))
    # 旧版 bundle（v0，无 schema_version）落在一次历史运行里
    run = tmp_path / "results/20260803_import/bundles"
    run.mkdir(parents=True)
    sha = hashlib.sha256(b"V").hexdigest()
    (run / "399_widget_m0.json").write_text(json.dumps({
        "asset_id": "external_399_widget_m0", "category": "widget",
        "representations": [
            {"format": "glb", "uri": str(vis), "backend": "sapien", "role": "visual",
             "sha256": sha, "size_bytes": 1,
             "metadata": {"derived_from": "w.usd", "rotated_z2y": True,
                          "origin": "bottom-center normalized"}},
            {"format": "glb", "uri": str(col), "backend": "sapien", "role": "collision",
             "sha256": sha, "size_bytes": 1, "metadata": {}},
        ],
        "source": {"library": "NVIDIA Isaac Assets 5.1", "group": "acq_399_widget",
                   "file": "w.usd", "license": "unknown (test)"},
        "physical": {"mass_kg": {"value": None, "status": "unknown", "runtime_default_kg": 0.1},
                     "mesh_bbox_m": [0.1, 0.2, 0.1], "scale_applied": 1.0,
                     "size_resolution": {"mode": "match_category", "actual_max_dim_m": 0.2,
                                         "scale": 1.0, "reference_max_dim_m": None,
                                         "reference_assets": [], "verdict": "no_precedent"},
                     "conventions": {"is_static": False, "z_policy": "origin_on_table",
                                     "footprint_shape": "box", "precedent": None,
                                     "note": "no precedent"},
                     "scale": [1.0, 1.0, 1.0], "mesh_up_axis": "Y"},
        "articulation": {}, "tags": ["rigid", "external", "batch"],
    }))
    (run.parent / "import_matrix.json").write_text(json.dumps([
        {"asset": "399_widget", "model": 0, "status": "accepted",
         "settled": True, "no_penetration": True, "tilt_ok": True},
    ]))
    frag = tmp_path / "fragment.yml"
    frag.write_text(
        "  399_widget:\n    category: widget\n    aliases: [widget, gadget]\n"
        "    models:\n      \"0\":\n        stable_pose_id: upright\n"
        "        stable_orientation_wxyz: [0.7071067811865476, 0.7071067811865476, 0.0, 0.0]\n"
        "        z_policy: origin_on_table\n        footprint_shape: box\n")
    return lib, tmp_path / "results", frag


def _run(lib, results, frag, out, apply=False):
    cmd = [sys.executable, str(SCRIPT), "--library-dir", str(lib),
           "--results-root", str(results), "--fragment", str(frag),
           "--out", str(out)] + (["--apply"] if apply else [])
    return subprocess.run(cmd, capture_output=True, text=True)


def test_dry_run_writes_nothing(tmp_path):
    lib, results, frag = _mini_pool(tmp_path)
    r = _run(lib, results, frag, tmp_path / "rep")
    assert r.returncode == 0, r.stderr
    assert not (lib / "399_widget/ledger_m0.json").exists()


def test_apply_upgrades_to_v1(tmp_path):
    lib, results, frag = _mini_pool(tmp_path)
    r = _run(lib, results, frag, tmp_path / "rep", apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((lib / "399_widget/ledger_m0.json").read_text())
    assert led["schema_version"] == "asset_ledger.v1"
    assert led["model_id"] == 0 and led["kind"] == "rigid"
    assert led["semantics"]["aliases"] == ["widget", "gadget"]
    conv = led["physical"]["conventions"]
    assert conv["stable_pose_id"] == "upright" and conv["inherited_from"] is None
    assert led["source"]["license"]["status"] == "unknown"
    assert led["source"]["license"]["terms_note"] == "unknown (test)"
    assert led["verification"][0]["check"] == "settle"
    assert led["verification"][0]["verdict"] == "pass"
    report = json.loads((tmp_path / "rep/backfill_report.json").read_text())
    assert report["violations"] == {}              # validator 清零


def test_idempotent(tmp_path):
    lib, results, frag = _mini_pool(tmp_path)
    _run(lib, results, frag, tmp_path / "rep1", apply=True)
    before = (lib / "399_widget/ledger_m0.json").read_text()
    r = _run(lib, results, frag, tmp_path / "rep2", apply=True)
    assert r.returncode == 0
    after = json.loads((lib / "399_widget/ledger_m0.json").read_text())
    # 幂等：已是 v1 的账本跳过（verification 不重复追加）
    assert after == json.loads(before)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_backfill_ledger.py -v
```
Expected: FAIL——脚本文件不存在（subprocess returncode != 0）。

- [ ] **Step 3: 实现 `scripts/backfill_ledger_v1.py`**

参数：`--library-dir --results-root --fragment --out`，开关 `--apply`（缺省 dry-run 只出报告）。流程：

```python
#!/usr/bin/env python3
"""一次性 backfill：把现存池资产的旧版 bundle 升 asset_ledger.v1 并落权威位置。

来源优先级：results/*/bundles/<asset>_m<N>.json 取 mtime 最新一份为基底；
semantics/conventions 缺口从 fragment YAML 回填；settle 验证从同 run 的
import_matrix.json 回填。已是 v1 的账本跳过（幂等）。
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger
```

关键逻辑（全部落在 `main()`，单文件自足）：
1. 枚举 `library_dir` 下资产目录（跳过 `_source`）×其中每个 `model_data<N>.json` → (asset, N) 清单。
2. 对每个 (asset, N)：若 `ledger_path(...)` 已存在且 `schema_version == v1` → 记 `skipped`（幂等）。
3. 否则在 `results_root` 下 glob `*/bundles/{asset}_m{N}.json`，按 mtime 取最新；找不到 → 报告记 `no_bundle_found`（不臆造账本）。
4. 升级 dict（纯改键，不动文件）：
   - 顶层补 `schema_version/model_id=N/semantic_name=category/kind`（`kind = "articulated" if bundle["articulation"] else "rigid"`）；
   - `semantics`：`aliases/colors` 取 fragment 资产级条目（`yaml.safe_load(fragment)[asset]`），`materials=[]`；fragment 无该资产 → aliases 退化为 `[category]` 并在报告记 `aliases_defaulted`；
   - `physical.conventions`：合入 fragment `models[str(N)]` 的 `stable_pose_id/stable_orientation_wxyz/z_policy/footprint_shape(/is_static)`；`precedent`→`inherited_from` 改名（`note` 保留）；
   - `physical.origin_convention`：从 sapien visual representation 的 `metadata.origin` 取（`"bottom-center normalized"` → `"bottom-center"`），取不到记 violation；
   - `physical.friction` 补结构化 unknown（同 `new_bundle` 的值）；
   - representation metadata：`rotated_z2y` 收进 `conversion_params`，`converter` 取不到时置 `"unknown (pre-v1 import)"`——如实标注而非编造版本号；
   - `source.license` 字符串 → `{"spdx": None, "status": "unknown", "terms_note": <原字符串>}`；
   - `source.retrieved_at`：`_source/<group>/SOURCE_MANIFEST.json` 的 mtime 日期，无则 bundle 文件 mtime 日期（报告记 basis）；
   - `source.source_manifest_path`：`_source/<group>/SOURCE_MANIFEST.json` 存在则填，否则 `None` + violation（如实暴露，人工补）；
   - `verification`：从 bundle 同 run 目录的 `import_matrix.json` 找 (asset, model) 行 → 一条 `{"backend": "sapien", "check": "settle", "verdict": "pass" if row["status"]=="accepted" else "fail", "date": <run目录名前8位格式化 YYYY-MM-DD>, "report_path": <matrix 路径>}`；矩阵缺行 → `verification=[]` + 报告记 `no_settle_record`。
5. `ledger.validate_bundle(bundle, check_files=True)` → violations 进报告（键=`f"{asset}_m{N}"`）。
6. `--apply` 时写 `ledger_path(library_dir, asset, N)`（`json.dumps(indent=2, ensure_ascii=False)`）；dry-run 只写报告。
7. 报告 `out/backfill_report.json`：`{"written": n, "skipped": [...], "violations": {...}, "notes": {...}}`；有 violation 时 exit code 1（dry-run 与 apply 同判）。

- [ ] **Step 4: 跑测试**

```bash
python -m pytest tests/test_backfill_ledger.py -v
```
Expected: 3 个测试 PASS。

- [ ] **Step 5: Commit**

```bash
git add 1_asset_reuse/scripts/backfill_ledger_v1.py 1_asset_reuse/tests/test_backfill_ledger.py
git commit -m "feat(ledger): backfill 脚本——现存池资产升 v1 落权威位置（dry-run 默认、幂等）"
```

---

### Task 4: `gen_fragment.py` — 账本 → overrides fragment

**Files:**
- Create: `1_asset_reuse/scripts/gen_fragment.py`
- Test: `1_asset_reuse/tests/test_gen_fragment.py`

**Interfaces:**
- Consumes: 权威账本（Task 3 产出位置）；`ledger.ledger_path`。
- Produces: `generate(library_dir: Path) -> dict`（资产名→fragment 条目，供 Task 5 的 materialize 复用）+ CLI `--library-dir --out`（写 YAML 文件）。**YAML 输出结构与 `data/scene_gen_ext/external_overrides_fragment_merged.yml` 完全同构**（顶层两空格缩进的资产名键；s9 消费方零改动）。

- [ ] **Step 1: 写失败测试**

`tests/test_gen_fragment.py`：

```python
import json
import sys
from pathlib import Path

import pytest
yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "1_asset_reuse/scripts"))
sys.path.insert(0, str(REPO / "1_asset_reuse"))
from tests.test_ledger import make_valid          # 复用合法账本基底
import gen_fragment


def _write_ledger(lib, asset, model, **over):
    b = make_valid()
    b["asset_id"] = f"external_{asset}_m{model}"
    b["model_id"] = model
    b.update(over)
    p = lib / asset / f"ledger_m{model}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(b))
    return b


def test_generate_basic(tmp_path):
    _write_ledger(tmp_path, "315_shears", 0)
    frag = gen_fragment.generate(tmp_path)
    entry = frag["315_shears"]
    assert entry["category"] == "shears"
    assert entry["aliases"] == ["shears", "scissors"]
    m = entry["models"]["0"]
    assert m["stable_pose_id"] == "upright"
    assert m["z_policy"] == "origin_on_table"
    assert "is_static" not in m                    # False 时不输出（对齐现有 fragment）


def test_settle_pass_filter(tmp_path):
    b = make_valid()
    b["verification"] = [{"backend": "sapien", "check": "settle", "verdict": "fail",
                          "date": "2026-08-08", "report_path": "r.json"}]
    _write_ledger(tmp_path, "315_shears", 0, verification=b["verification"])
    frag = gen_fragment.generate(tmp_path)
    assert "315_shears" not in frag                # 无 settle pass → 不进视图（池层仍在）


def test_asset_level_conflict_raises(tmp_path):
    _write_ledger(tmp_path, "302_can", 0, category="can",
                  semantics={"aliases": ["can"], "colors": [], "materials": []})
    _write_ledger(tmp_path, "302_can", 1, category="can",
                  semantics={"aliases": ["tin"], "colors": [], "materials": []})
    with pytest.raises(gen_fragment.FragmentConflict):
        gen_fragment.generate(tmp_path)            # 同资产 aliases 不一致 → 报错不取并集


def test_yaml_shape_matches_existing_convention(tmp_path):
    _write_ledger(tmp_path, "315_shears", 0)
    out = tmp_path / "frag.yml"
    gen_fragment.main(["--library-dir", str(tmp_path), "--out", str(out)])
    loaded = yaml.safe_load(out.read_text())
    assert set(loaded["315_shears"].keys()) <= {"category", "aliases", "colors", "models"}
    assert list(loaded["315_shears"]["models"].keys()) == ["0"]   # model 键是字符串
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_gen_fragment.py -v
```
Expected: FAIL——`import gen_fragment` 报 ModuleNotFoundError。

- [ ] **Step 3: 实现 `scripts/gen_fragment.py`**

```python
#!/usr/bin/env python3
"""从权威账本生成 external_overrides_fragment.yml（fragment 是派生视图，手拼停用）。

过滤：至少一条 {backend: sapien, check: settle, verdict: pass} 才进视图（池层照收）。
聚合：资产级字段（category/aliases/colors）取各 model 账本一致值，不一致抛
FragmentConflict——不静默取并集（spec §4 聚合规则）。
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


class FragmentConflict(ValueError):
    pass


MODEL_KEYS = ("stable_pose_id", "stable_orientation_wxyz", "z_policy", "footprint_shape")


def _settle_passed(bundle):
    return any(v.get("backend") == "sapien" and v.get("check") == "settle"
               and v.get("verdict") == "pass" for v in bundle.get("verification", []))


def generate(library_dir):
    groups = defaultdict(dict)                     # asset -> {model:int -> bundle}
    for p in sorted(Path(library_dir).glob("*/ledger_m*.json")):
        b = json.loads(p.read_text())
        groups[p.parent.name][b["model_id"]] = b
    frag = {}
    for asset, models in sorted(groups.items()):
        passed = {m: b for m, b in models.items() if _settle_passed(b)}
        if not passed:
            continue
        heads = [(b["category"], tuple(b["semantics"]["aliases"]),
                  tuple(b["semantics"]["colors"])) for b in passed.values()]
        if len(set(heads)) != 1:
            raise FragmentConflict(f"{asset}: asset-level fields differ across models: {heads}")
        cat, aliases, colors = heads[0]
        entry = {"category": cat, "aliases": list(aliases)}
        if colors:
            entry["colors"] = list(colors)
        entry["models"] = {}
        for m in sorted(passed):
            conv = passed[m]["physical"]["conventions"]
            md = {k: conv[k] for k in MODEL_KEYS}
            if conv.get("is_static"):
                md["is_static"] = True             # 仅 true 时输出，对齐现有 fragment
            entry["models"][str(m)] = md
        frag[asset] = entry
    return frag
```

YAML 输出不用 `yaml.dump`（避免风格漂移），按现有 fragment 的行格式手排（与 `import_materialize.py:493` 一带现在的 frag_lines 写法同族）：两空格缩进、`aliases: [a, b]` 流式列表、四元数原样浮点。`main(argv)` 解析 `--library-dir --out`，写文件末尾带换行。

- [ ] **Step 4: 跑测试**

```bash
python -m pytest tests/test_gen_fragment.py -v
```
Expected: 4 个测试 PASS。

- [ ] **Step 5: Commit**

```bash
git add 1_asset_reuse/scripts/gen_fragment.py 1_asset_reuse/tests/test_gen_fragment.py
git commit -m "feat(ledger): gen_fragment——账本生成 overrides fragment（settle-pass 过滤+聚合一致性）"
```

---

### Task 5: `import_materialize.py` 切 v1 落账 + validator 门 + fragment 生成化

**Files:**
- Modify: `1_asset_reuse/scripts/import_materialize.py`（378-441 bundle 块、438 写出、493 fragment 块）
- Test: 复用 `tests/test_ledger.py`（builder 已测）+ 新增矩阵行断言见 Step 3

**Interfaces:**
- Consumes: `ledger.new_bundle` / `validate_bundle` / `ledger_path`（Task 2）、`gen_fragment.generate`（Task 4）。
- Produces: 新导入资产直接产 v1 账本（运行快照 `<out>/bundles/` + 权威位置双写）；`import_matrix.json` 行新增淘汰码形态 `"schema_violation:<code>"`。

- [ ] **Step 1: 改 bundle 构造（378-437 行）为 `ledger.new_bundle` 调用**

文件头部加 `from lib.ledger import new_bundle, validate_bundle, ledger_path`（该脚本已有 `sys.path` 注入 lib 的写法，沿用）。原 dict 字面量替换为：

```python
        bundle = new_bundle(
            asset=asset, model=int(model), category=meta.get("category", "unknown"),
            kind="rigid",
            aliases=list(meta.get("aliases", []) or [meta.get("category", "unknown")]),
            colors=list(meta.get("colors", [])), materials=[],
            representations=reps,                  # 原三条 representation，见下
            mesh_bbox_m=size, mesh_up_axis="Y",
            origin_convention="bottom-center", scale_applied=size_res["scale"],
            size_resolution=size_res, conventions=conv_v1,
            source=source_v1, tags=["rigid", "external", "batch"],
            verification=[settle_entry],
        )
```

其中三个就地构造的变量：

```python
        reps = [ ...原三条 representation dict，visual 条 metadata 改为
                 {"derived_from": r["usd"],
                  "converter": "omni.kit.asset_converter@isaac-5.1",
                  "conversion_params": {"rotated_z2y": rotated}}... ]
        conv_v1 = {**conv,                          # conventions.py 继承结果
                   "stable_pose_id": "upright",
                   "stable_orientation_wxyz": ledger_mod.X90_WXYZ,   # 刚体规范化 Y-up → X+90
                   "inherited_from": conv.pop("precedent", None)}
        source_v1 = {"library": "NVIDIA Isaac Assets 5.1", "group": r["group"],
                     "file": r["usd"],
                     "license": {"spdx": None, "status": "unknown",
                                 "terms_note": "NVIDIA asset EULA; YCB dataset terms for ycb group"},
                     "retrieved_at": dt.date.fromtimestamp(
                         Path(args.staging, "staging_manifest.json").stat().st_mtime).isoformat(),
                     "source_manifest_path": str(source_manifest) if source_manifest.exists() else None}
        settle_entry = {"backend": "sapien", "check": "settle",
                        "verdict": "pass" if checks["pass"] else "fail",
                        "date": dt.date.today().isoformat(),
                        "report_path": str(out / "import_matrix.json"),
                        "thresholds": {"settle_disp_m": 0.002, "z_min_m": -0.002,
                                       "tilt_deg": 15, "tilt_deg_flat": 45}}
```

（`source_manifest = Path(args.source_root或library)/"_source"/r["group"]/"SOURCE_MANIFEST.json"`——按 fetch 阶段实际镜像位置取，实现时以 staging_manifest 记录的路径为准。thresholds 数值抄脚本内现有硬门常量，勿另定。）

- [ ] **Step 2: 落账双写 + validator 门**

原 438-440 行写快照后追加：

```python
        violations = validate_bundle(bundle, check_files=True)
        if violations:
            row["status"] = "rejected"
            row.setdefault("reasons", []).extend(
                f"schema_violation:{v.code}" for v in violations)
        else:
            authoritative = ledger_path(args.library_dir, asset, int(model))
            authoritative.write_text(json.dumps(bundle, indent=2, ensure_ascii=False))
```

注意顺序：settle 硬门失败的模型 `checks["pass"]=False` → `settle_entry.verdict="fail"`，账本**仍写快照**（池层记录），但不写权威位置、不进 fragment——与现行「淘汰物理隔离出资产池」语义一致。

- [ ] **Step 3: fragment 写出改调 generator**

删除 493 行一带的 frag_lines 手拼块，替换为：

```python
import gen_fragment                                 # scripts/ 同目录
frag = gen_fragment.generate(args.library_dir)
gen_fragment.write_yaml(frag, Path(args.overrides_fragment))
```

（`write_yaml` 是 Task 4 `main()` 里的写文件函数，提出来公用。）

- [ ] **Step 4: 冒烟验证（不跑 SAPIEN 全链）**

```bash
python - <<'EOF'
import sys; sys.path.insert(0, "1_asset_reuse")
import ast
src = open("1_asset_reuse/scripts/import_materialize.py").read()
ast.parse(src)                                     # 语法完整
assert "new_bundle(" in src and "schema_violation" in src and "frag_lines" not in src
print("materialize wiring OK")
EOF
python -m pytest tests/ -q
```
Expected: `materialize wiring OK`；既有测试全过。（真实批量导入验证在 Task 8 验收跑。）

- [ ] **Step 5: Commit**

```bash
git add 1_asset_reuse/scripts/import_materialize.py
git commit -m "feat(ledger): import_materialize 切 v1 落账（builder+validator 门+权威双写+fragment 生成化）"
```

---

### Task 6: `s13b` 关节体 v1 落账 + `balance_gate`

**Files:**
- Modify: `1_asset_reuse/scripts/s13b_validate_articulated.py`（bundle 写出处，实现时定位 `json.dump.*bundle` 一带）
- Test: 追加到 `1_asset_reuse/tests/test_ledger.py`

**Interfaces:**
- Consumes: `ledger.new_bundle`（`kind="articulated"`、`articulation` 必填）。
- Produces: 关节体权威账本，`articulation` 含 `joint_names/joint_types/limits/closed_qpos/open_qpos/balance_gate`。

- [ ] **Step 1: 写失败测试（articulated builder 全形态）**

追加到 `tests/test_ledger.py`：

```python
def test_new_bundle_articulated_full():
    art = {"joint_names": ["drawer_0"], "joint_types": ["prismatic"],
           "limits": [[0.0, 0.3]], "closed_qpos": [0.0], "open_qpos": [0.3],
           "balance_gate": {"free_joints_allowed": False, "measured_equilibrium": None}}
    b = ledger.new_bundle(asset="314_cabinet", model=0, category="cabinet",
                          kind="articulated", aliases=["cabinet"], colors=[], materials=[],
                          representations=make_valid()["representations"],
                          mesh_bbox_m=[0.6, 0.4, 0.8], mesh_up_axis="Z",
                          origin_convention="base-at-floor", scale_applied=1.0,
                          size_resolution=make_valid()["physical"]["size_resolution"],
                          conventions={**make_valid()["physical"]["conventions"],
                                       "stable_orientation_wxyz": ledger.IDENTITY_WXYZ},
                          source=make_valid()["source"], tags=["articulated", "external"],
                          verification=[], articulation=art)
    assert ledger.validate_bundle(b, check_files=False) == []
    assert b["kind"] == "articulated"
    assert b["articulation"]["balance_gate"]["free_joints_allowed"] is False
```

- [ ] **Step 2: 跑测试**

```bash
python -m pytest tests/test_ledger.py -v -k articulated_full
```
Expected: PASS（builder Task 2 已支持 articulation；若 FAIL 先修 builder）。

- [ ] **Step 3: 改 s13b 落账**

定位 s13b 现有 bundle 写出块（写 `*_bundle.json` 处），替换为 `new_bundle(...)` 调用：`kind="articulated"`；`articulation` 从脚本已有的 dof/limits 核对结果与导出报告组装，`balance_gate={"free_joints_allowed": bool(args.allow_free_joints), "measured_equilibrium": measured_eq if args.allow_free_joints else None}`（`measured_eq` 即现有「记录实测平衡位」逻辑的输出变量）；`conventions.stable_orientation_wxyz = IDENTITY_WXYZ`（URDF Z-up → identity，抄 conventions.py 设计规则）；verification 初条 `{"backend": "sapien", "check": "joint_sweep", "verdict": ..., "date": ..., "report_path": <s13b 报告路径>}`；写权威位置 `ledger_path(instance 所在 library_dir, asset, model)` + 原路径快照双写。

- [ ] **Step 4: 语法 + 回归**

```bash
python -c "import ast; ast.parse(open('1_asset_reuse/scripts/s13b_validate_articulated.py').read()); print('OK')"
python -m pytest tests/ -q
```
Expected: OK；全部测试过。

- [ ] **Step 5: Commit**

```bash
git add 1_asset_reuse/scripts/s13b_validate_articulated.py 1_asset_reuse/tests/test_ledger.py
git commit -m "feat(ledger): s13b 关节体 v1 落账（articulation+balance_gate+joint_sweep 验证初条）"
```

---

### Task 7: `s11` 运行时扫查回填 verification

**Files:**
- Modify: `1_asset_reuse/scripts/s11_runtime_load_sweep.py`（96 行 `out.write_text` 之后）
- Test: `tests/test_ledger.py` 的 `test_append_verification` 已覆盖 append 语义；新增路径打通断言见 Step 2

**Interfaces:**
- Consumes: `ledger.append_verification` / `ledger_path`（Task 2）。
- Produces: s11 每扫一个资产，权威账本追加一条 `runtime_load` 记录。

- [ ] **Step 1: 改 s11**

`rows` 写出后追加（s11 的 row 里已有资产名与通过状态字段，实现时对准实际键名）：

```python
from lib.ledger import append_verification, ledger_path   # 头部，沿用脚本 sys.path 写法
import datetime as dt

for row in rows:
    lp = ledger_path(args.library_dir, row["asset"], row["model"])
    if not lp.exists():
        continue                                   # 未 backfill 的旧资产：跳过并在 stdout 记一行
    append_verification(lp, {
        "backend": "sapien", "check": "runtime_load",
        "verdict": "pass" if row["ok"] else "fail",
        "date": dt.date.today().isoformat(),
        "report_path": str(out),
    })
```

s11 参数表如无 `--library-dir` 则新增（默认 `../data/asset_library`，与 s9 的 `--library-dir` 缺省一致）。

- [ ] **Step 2: 单测（模拟 rows 回填路径）**

追加到 `tests/test_ledger.py`：

```python
def test_runtime_load_roundtrip(tmp_path):
    lp = tmp_path / "315_shears/ledger_m0.json"
    lp.parent.mkdir(parents=True)
    lp.write_text(json.dumps(make_valid()))
    ledger.append_verification(lp, {"backend": "sapien", "check": "runtime_load",
                                    "verdict": "pass", "date": "2026-08-08",
                                    "report_path": "sweep.json"})
    got = json.loads(lp.read_text())
    checks = [(v["check"], v["verdict"]) for v in got["verification"]]
    assert ("runtime_load", "pass") in checks and ("settle", "pass") in checks
```

- [ ] **Step 3: 跑测试 + 语法查**

```bash
python -m pytest tests/test_ledger.py -v -k runtime_load
python -c "import ast; ast.parse(open('1_asset_reuse/scripts/s11_runtime_load_sweep.py').read()); print('OK')"
```
Expected: PASS；OK。

- [ ] **Step 4: Commit**

```bash
git add 1_asset_reuse/scripts/s11_runtime_load_sweep.py 1_asset_reuse/tests/test_ledger.py
git commit -m "feat(ledger): s11 运行时扫查结果回填账本 verification（runtime_load）"
```

---

### Task 8: 真实池 backfill + fragment 等价验证 + 切换与验收

**Files:**
- Modify: `1_asset_reuse/README.md`（「用法」节追加账本契约命令块；不动 OVERVIEW.md）
- 产物: `results/20260808_ledger_v1_backfill/`

- [ ] **Step 1: 真实池 dry-run**

```bash
cd /home/jingxiang/yuxin/env-gen-dev/1_asset_reuse
python scripts/backfill_ledger_v1.py \
  --library-dir ../data/asset_library --results-root ../results \
  --fragment ../data/scene_gen_ext/external_overrides_fragment_merged.yml \
  --out ../results/20260808_ledger_v1_backfill
cat ../results/20260808_ledger_v1_backfill/backfill_report.json
```
Expected: 报告列出全部 (asset, model)；violations 若非空，逐条核对——**允许的处理只有两种**：修 backfill 映射逻辑，或确认数据本身缺失后显式补 unknown/null；不许放宽 validator。

- [ ] **Step 2: `--apply` 落权威账本，validator 清零**

```bash
python scripts/backfill_ledger_v1.py ...同上... --apply
ls ../data/asset_library/*/ledger_m*.json | wc -l    # 期望 = 池内 (asset,model) 总数
```
Expected: exit 0、报告 `violations == {}`。

- [ ] **Step 3: fragment 等价验证（切换前的守门）**

```bash
python scripts/gen_fragment.py --library-dir ../data/asset_library \
  --out ../results/20260808_ledger_v1_backfill/fragment_generated.yml
python - <<'EOF'
import yaml
a = yaml.safe_load(open("../data/scene_gen_ext/external_overrides_fragment_merged.yml"))
b = yaml.safe_load(open("../results/20260808_ledger_v1_backfill/fragment_generated.yml"))
assert a == b, {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
print("fragment semantically identical")
EOF
```
Expected: `fragment semantically identical`。若 diff 非空：差异逐条归因（backfill 映射错 / 现有 fragment 里的历史手改），修正后重跑；**在等价前不切换**。

- [ ] **Step 4: 切换 + 全量回归**

```bash
cp ../results/20260808_ledger_v1_backfill/fragment_generated.yml \
   ../data/scene_gen_ext/external_overrides_fragment.yml     # s9 输入位（data/ 不入 git）
python -m pytest tests/ -q                                    # 1_asset_reuse 全量（42+新增）
cd ../shared/openxsim && python -m pytest tests/ -q           # openxsim 49 个不破
```
Expected: 全部 PASS。

- [ ] **Step 5: e2e 验收（重步骤，跑 s9→s10）**

```bash
cd /home/jingxiang/yuxin/env-gen-dev/1_asset_reuse
python scripts/s9_build_shadow_root.py --library-dir ../data/asset_library \
  --shadow ../data/robotwin_shadow --ext-dir ../data/scene_gen_ext \
  --extra-overrides ../data/scene_gen_ext/external_overrides_fragment.yml
bash scripts/s10_e2e_scene.sh
```
Expected: s10 四连判定全过（ground 到 301_cup、回放 fail=0、全量验证 fail=0 not_run=0）——生成侧行为与切换前完全一致。

- [ ] **Step 6: README 用法节追加命令块**

`1_asset_reuse/README.md` 「用法」节追加（原文风格，命令块+一行说明）：账本契约一段——权威位置、`backfill_ledger_v1.py` / `gen_fragment.py` 两命令、"fragment 是生成物，手改无效，改账本再生成"一句纪律。

- [ ] **Step 7: Commit + 收尾**

```bash
git add 1_asset_reuse/README.md
git commit -m "docs: 账本契约用法（backfill/gen_fragment/权威位置/fragment 生成纪律）"
git log --oneline feat/env-gen-ir-bridge..HEAD    # 核对本计划全部提交
```

合并回 `feat/env-gen-ir-bridge`（或 main）由用户决定——工作区并发有先例，不擅自合。

---

## Self-Review 记录

- **Spec 覆盖**：§3 schema→Task 2；§4 组件表 ledger.py/gen_fragment/backfill/materialize/s13b/s11→Task 2-7；§5 迁移路径四步→Task 3（步1）/Task 8 步3（步2）/Task 5-6（步3）/池层不变原则（Task 5 步2 语义）；§6 错误处理→validator 各 code + backfill 报告；§7 测试验收→各 Task 测试 + Task 8。s4（A 线 Isaac 侧验证）**未入本计划**：A 线 RoboTwin 资产的账本不在池权威位置，回填点待 A 线资产入池方案定型，已在 Task 8 README 纪律段之外明确排除——如需可后补小任务。
- **占位符扫描**：无 TBD/TODO；Task 5/6 涉及"实现时对准实际键名/变量"的两处，均已给出定位方式与语义约束（非留白）。
- **类型一致性**：`Violation(path, code, message)`、`validate_bundle(bundle, *, check_files)`、`new_bundle(**kw)`、`append_verification(path, entry)`、`generate(library_dir) -> dict`、`ledger_path(library_dir, asset, model)` 在 Task 2 定义后各任务引用一致；淘汰码 `schema_violation:<code>` 仅 Task 5 引入并写入 README（Task 8）。
