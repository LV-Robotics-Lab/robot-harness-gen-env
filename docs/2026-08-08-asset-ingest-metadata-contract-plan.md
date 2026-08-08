# asset_ledger.v1 入库 metadata 契约 · 实现计划（r2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 `docs/2026-08-08-asset-ingest-metadata-contract-design.md`（r2）——每资产一份权威账本 `ledger.json`（含 models[]），validator + 拆包适配 + 验证回填（内容锚定）+ fragment 生成化（license-gate 开关）。

**Architecture:** 新增 `1_asset_reuse/lib/ledger.py`（schema 常量 + builder/upsert + validator + 带锁 verification append + `to_ir_bundles` 拆包，纯 stdlib），backfill 把现存 per-model bundle **聚合**成每资产账本，`gen_fragment.py` 从账本生成 fragment（latest-settle 过滤 + default pose 投影 + `--license-gate`），最后切换 materialize / s13b / s11 三个管线接入点。

**Tech Stack:** Python 3.10（conda `env-gen-yuxin`）、pytest、PyYAML（仅 scripts/）、fcntl（stdlib）。

## Global Constraints

- 工作目录：lv-5090 `/home/jingxiang/yuxin/env-gen-dev`；开发分支 `feat/asset-ledger-v1`（自 `feat/env-gen-ir-bridge` 切出）。
- 运行环境：`conda activate env-gen-yuxin`；测试 `cd 1_asset_reuse && python -m pytest tests/<file> -v`；既有 42 测试必须保持通过。
- `1_asset_reuse/lib/` 纯 stdlib（双 conda 环境可 import）；PyYAML 只出现在 `scripts/`。
- 上游只读；fragment YAML 输出语义与 `data/scene_gen_ext/external_overrides_fragment_merged.yml` 一致（s9 零改动）；**openxsim 本体零改动**（拆包适配在我方 lib）。
- 账本权威位置：`data/asset_library/<asset>/ledger.json`（每资产一份含 models[]）；**账本纳入 git**（Task 1 配 .gitignore 豁免）；`results/<run>/bundles/` 写 per-model 摊平快照（=`to_ir_bundles` 输出）。
- **共享工作区并发（重要）**：另一会话在本仓活跃开发（2026-08-08：Pipeline Studio 前端 + scripts/ 目录重组为 `{a_forward,b_batch,b_reverse,c_catalog,d_acquire}/`，且 `import_materialize.py`/`run_smoke.sh`/多个测试有未提交改动）。纪律：每任务动手前 `git status && git log --oneline -3` 核对现场；**提交一律 `git commit <明确路径>` 带 pathspec**（裸 `git commit` 会连他人暂存一起提交，已有先例）；Task 5–7 涉及 `import_materialize.py`/`s13b`/`s11`，动手前与并发会话协调改动窗口；计划中的行号以当前文件实际内容为准重新定位。
- 工作区有他人未提交改动：**禁止 `git add -A`**，逐文件 add；本计划不改 OVERVIEW.md。
- 池层只进不出；未知值结构化（mass/friction 三态 status，`estimated` 必带 estimator；friction runtime_default 可 null）；不编造。
- verification：append-only、fcntl 锁、条目带 `run_id`/`timestamp`（秒级 ISO）/`verified_digest`；**读取一律 latest-per-(backend,check) 且 digest 一致**——禁止 `any(pass)`。
- license gate：`gen_fragment --license-gate` 默认关；无论开关每次运行打印 unknown 计数警告。license 核查（audit）**待议，不在本计划**。

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `.gitignore` | 修改 | `data/asset_library/*/ledger.json` 豁免级联 |
| `1_asset_reuse/lib/ledger.py` | 新建 | 常量、`Violation`、`validate_ledger`、`derive_usable`、`new_model_entry`、`upsert_model`、`append_verification`、`reps_digest`、`latest_verification`、`to_ir_bundles`、`ledger_path` |
| `1_asset_reuse/tests/test_ledger.py` | 新建 | validator 正反例、builder/upsert、锁与去重、latest 语义、拆包等价 |
| `1_asset_reuse/scripts/backfill_ledger_v1.py` | 新建 | per-model 旧 bundle 聚合升 v1（dry-run 默认、幂等） |
| `1_asset_reuse/tests/test_backfill_ledger.py` | 新建 | tmp 迷你池聚合正确性 + 幂等 |
| `1_asset_reuse/scripts/gen_fragment.py` | 新建 | 账本→fragment（latest-settle+digest 过滤、default pose 投影、--license-gate、unknown 警告） |
| `1_asset_reuse/tests/test_gen_fragment.py` | 新建 | 投影/过滤/gate/警告/幂等 |
| `1_asset_reuse/scripts/b_batch/import_materialize.py` | 修改（bundle 构造/写出/fragment 三处；行号按当前文件重定位——该文件另有并发未提交改动） | builder+upsert 落账、validator 门、快照渲制、fragment 改调 generator |
| `1_asset_reuse/scripts/b_reverse/s13b_validate_articulated.py` | 修改 | 关节体 v1 落账 + balance_gate + joint_sweep 验证条目 |
| `1_asset_reuse/scripts/c_catalog/s11_runtime_load_sweep.py` | 修改 | runtime_load 验证回填 |
| `1_asset_reuse/scripts/a_forward/s5_check_ir.py` | 修改（AssetBundle.from_dict 调用处） | 改经 `to_ir_bundles` 消费权威账本 |
| `1_asset_reuse/README.md` | 修改 | 账本契约用法 + 发布纪律（license-gate） |

**Interfaces 总览（Task 2 产出，后续任务消费）：**

```python
# 1_asset_reuse/lib/ledger.py —— 纯 stdlib
SCHEMA_VERSION = "asset_ledger.v1"
KINDS = ("rigid", "articulated")
BACKENDS = ("sapien", "isaacsim", "portable")
ROLES = ("visual", "collision", "visual_and_collision", "snapshot")
CHECKS = ("settle", "joint_sweep", "runtime_load", "e2e", "admission_report")
VERDICTS = ("pass", "fail")
MASS_STATUS = ("known", "estimated", "unknown")
DEFAULT_BASIS = ("global_constant", "category_typical", "urdf_inertial", "none")
X90_WXYZ / IDENTITY_WXYZ            # re-export from conventions

@dataclass
class Violation: path: str; code: str; message: str

def ledger_path(library_dir, asset) -> Path            # <library>/<asset>/ledger.json
def validate_ledger(ledger: dict, *, check_files=True) -> list[Violation]
def derive_usable(ledger: dict, model_id: int) -> tuple[bool, list[str]]
def new_model_entry(**kw) -> dict                       # 签名见 Task 2 Step 5
def upsert_model(ledger: dict | None, *, asset, category, kind, aliases, colors,
                 materials, tags, model_entry, semantic_name=None) -> dict
    # ledger=None 时新建资产账本；已存在时资产级字段必须一致否则 ValueError（写时抓漂移），
    # 同 model_id 整条替换（重导入语义）
def reps_digest(model_entry: dict, backend: str) -> str
    # sha256(",".join(sorted(该 backend 各 representation 的 sha256)))
def append_verification(path, model_id: int, entry: dict) -> dict
    # fcntl 锁 + 同 (backend,check,run_id,verified_digest) 去重 + 原子写
def latest_verification(model_entry, backend, check) -> dict | None
    # timestamp 最新一条；verified_digest != reps_digest(model, backend) 时返回 None（失效）
def to_ir_bundles(ledger: dict) -> list[dict]
    # 每 model 摊平：{"asset_id": f"{ledger['asset_id']}_m{m['model_id']}", "category",
    #  "representations"(剔除 role=snapshot), "source", "physical"(含展开的 conventions),
    #  "articulation", "tags"} —— 与旧 per-model bundle 同构，可过 IR AssetBundle.from_dict
```

---

### Task 1: 分支 + .gitignore 账本豁免

**Files:** Modify: `.gitignore`

- [ ] **Step 1: 切分支**

```bash
cd /home/jingxiang/yuxin/env-gen-dev
git checkout feat/env-gen-ir-bridge && git checkout -b feat/asset-ledger-v1
```

- [ ] **Step 2: .gitignore 加豁免级联**（先 `grep -n "^data" .gitignore` 确认现有 data/ 忽略写法，按其形态适配；若为 `data/` 整目录忽略，追加）

```gitignore
# asset ledger 是唯一权威入库记录，必须有 git 历史（spec §3.9 治理注记）
!data/asset_library/
data/asset_library/*
!data/asset_library/*/
data/asset_library/*/*
!data/asset_library/*/ledger.json
```

- [ ] **Step 3: 验证豁免生效**

```bash
touch data/asset_library/_probe/ledger.json 2>/dev/null || mkdir -p data/asset_library/_probe && touch data/asset_library/_probe/ledger.json
git check-ignore data/asset_library/_probe/ledger.json && echo "STILL IGNORED (bad)" || echo "TRACKED OK"
git check-ignore data/asset_library/302_can/model_data0.json && echo "model_data ignored OK"
rm -rf data/asset_library/_probe
```
Expected: `TRACKED OK` + `model_data ignored OK`。

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore 豁免 asset ledger（唯一权威记录需 git 历史）"
```

---

### Task 2: `lib/ledger.py` — 常量 + validator + builder + 锁 + 拆包

**Files:** Create: `1_asset_reuse/lib/ledger.py` / Test: `1_asset_reuse/tests/test_ledger.py`

**Interfaces:** Consumes `conventions.py` 的 X90_WXYZ/IDENTITY_WXYZ；Produces 见总览。

- [ ] **Step 1: 写失败测试——合法账本 + 反例参数表**

`tests/test_ledger.py`（`make_valid()` 是全部测试的基底；字段抄 spec §3）：

```python
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger


def make_model(**over):
    m = {
        "model_id": 0,
        "physical": {
            "mesh_bbox_m": [0.078, 0.051, 0.053],
            "mesh_up_axis": "Y",
            "origin_convention": "bottom-center",
            "scale_applied": 1.0,
            "size_resolution": {"mode": "match_category", "actual_max_dim_m": 0.078,
                                "scale": 1.0, "reference_max_dim_m": None,
                                "reference_assets": [], "verdict": "no_precedent"},
            "conventions": {"is_static": False, "z_policy": "origin_on_table",
                            "footprint_shape": "box",
                            "stable_poses": [{"pose_id": "upright",
                                              "orientation_wxyz": ledger.X90_WXYZ,
                                              "is_default": True}],
                            "inherited_from": None},
            "mass_kg": {"value": None, "status": "unknown", "runtime_default_kg": 0.1,
                        "runtime_default_basis": "global_constant"},
            "friction": {"value": None, "status": "unknown", "runtime_default": None,
                         "runtime_default_basis": "none"},
        },
        "representations": [
            {"format": "glb", "uri": "/tmp/x/visual.glb", "backend": "sapien",
             "role": "visual", "sha256": "0" * 64, "size_bytes": 10,
             "metadata": {"derived_from": "src.usd",
                          "converter": "omni.kit.asset_converter@isaac-5.1",
                          "conversion_params": {"rotated_z2y": True}}},
        ],
        "articulation": {},
        "source": {"library": "NVIDIA Isaac Assets 5.1", "group": "acq_315_shears",
                   "file": "061_foam_brick.usd",
                   "license": {"spdx": None, "status": "unknown",
                               "terms_note": "NVIDIA asset EULA"},
                   "retrieved_at": "2026-08-08",
                   "source_manifest_path": "/tmp/x/SOURCE_MANIFEST.json"},
        "verification": [
            {"backend": "sapien", "check": "settle", "verdict": "pass",
             "run_id": "20260808_import", "timestamp": "2026-08-08T10:00:00",
             "verified_digest": ledger.reps_digest.__wrapped__  # 占位，Step 5 改为真调用
             if False else "d" * 64,
             "report_path": "/tmp/x/import_matrix.json"},
        ],
    }
    m.update(over)
    return m


def make_valid(**over):
    b = {
        "schema_version": "asset_ledger.v1",
        "asset_id": "external_315_shears",
        "category": "shears",
        "semantic_name": "shears",
        "kind": "rigid",
        "tags": ["rigid", "external", "batch"],
        "semantics": {"aliases": ["shears", "scissors"], "colors": [], "materials": []},
        "models": [make_model()],
    }
    b.update(over)
    return b


def test_valid_ledger_no_violations():
    assert ledger.validate_ledger(make_valid(), check_files=False) == []


def _del(path):
    def f(b):
        node = b
        parts = path.split(".")
        for p in parts[:-1]:
            node = node[int(p)] if p.isdigit() else node[p]
        last = parts[-1]
        del node[int(last)] if last.isdigit() else node[last]
    return f


def _set(path, value):
    def f(b):
        node = b
        parts = path.split(".")
        for p in parts[:-1]:
            node = node[int(p)] if p.isdigit() else node[p]
        last = parts[-1]
        node[int(last) if last.isdigit() else last] = value
    return f


CASES = [
    (_del("schema_version"), "needs_backfill"),
    (_set("schema_version", "asset_ledger.v0"), "bad_schema_version"),
    (_del("semantic_name"), "missing"),
    (_set("kind", "soft"), "bad_enum"),
    (_set("semantics.aliases", []), "empty_aliases"),
    (_set("models", []), "no_models"),
    (_set("models.0.physical.conventions.stable_poses", []), "no_stable_pose"),
    (_set("models.0.physical.conventions.stable_poses",
          [{"pose_id": "a", "orientation_wxyz": ledger.X90_WXYZ, "is_default": True},
           {"pose_id": "b", "orientation_wxyz": ledger.IDENTITY_WXYZ, "is_default": True}]),
     "multiple_default_poses"),
    (_set("models.0.physical.conventions.stable_poses",
          [{"pose_id": "a", "orientation_wxyz": [1, 1, 0, 0], "is_default": True}]),
     "bad_quaternion"),
    (_set("models.0.physical.mass_kg",
          {"value": None, "status": "known", "runtime_default_kg": 0.1,
           "runtime_default_basis": "global_constant"}), "unknown_shape"),
    (_set("models.0.physical.mass_kg",
          {"value": 0.5, "status": "estimated", "runtime_default_kg": 0.1,
           "runtime_default_basis": "global_constant"}), "estimator_required"),
    (_set("models.0.physical.mass_kg",
          {"value": None, "status": "unknown", "runtime_default_kg": 0.1,
           "runtime_default_basis": "vibes"}), "bad_enum"),
    (_del("models.0.physical.friction"), "missing"),
    (_set("models.0.representations", []), "no_sapien_representation"),
    (_set("models.0.source.license", "unknown"), "license_not_structured"),
    (_del("models.0.source.retrieved_at"), "missing"),
    (_set("models.0.verification.0.check", "fly"), "bad_enum"),
    (_del("models.0.verification.0.verified_digest"), "missing"),
    (_del("models.0.verification.0.run_id"), "missing"),
    (_set("usable", True), "derived_field_handwritten"),
]


@pytest.mark.parametrize("mutate,code", CASES)
def test_violations(mutate, code):
    b = make_valid()
    mutate(b)
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert code in codes, f"expected {code}, got {codes}"


def test_duplicate_model_id():
    b = make_valid(models=[make_model(), make_model()])   # 两个 model_id=0
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert "duplicate_model_id" in codes


def test_articulated_requires_articulation():
    b = make_valid(kind="articulated")
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert "articulation_required" in codes


def test_check_files(tmp_path):
    f = tmp_path / "visual.glb"; f.write_bytes(b"mesh")
    b = make_valid()
    b["models"][0]["representations"][0]["uri"] = str(f)   # sha 仍 0*64
    codes = [v.code for v in ledger.validate_ledger(b, check_files=True)]
    assert "sha256_mismatch" in codes
    b2 = make_valid()
    b2["models"][0]["representations"][0]["uri"] = str(tmp_path / "gone.glb")
    assert "file_missing" in [v.code for v in ledger.validate_ledger(b2, check_files=True)]


def test_ledger_path():
    assert str(ledger.ledger_path("/lib", "315_shears")) == "/lib/315_shears/ledger.json"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /home/jingxiang/yuxin/env-gen-dev/1_asset_reuse && python -m pytest tests/test_ledger.py -v
```
Expected: `ImportError: lib.ledger`。

- [ ] **Step 3: 实现常量 + `validate_ledger` + `derive_usable` + `reps_digest` + `ledger_path`**

模块 docstring 照 `conventions.py` 惯例，声明「本文件常量表为契约规范文本，spec §3 为文档视图」。检查顺序（每条对应 Step 1 一个反例）：

1. `schema_version` 缺→`needs_backfill`、不符→`bad_schema_version`（两者直接返回，不用 v1 规则误报）。
2. 资产级必填：`asset_id/category/semantic_name/kind/tags/semantics.aliases/models`；`kind`∈KINDS；aliases 空→`empty_aliases`；models 空→`no_models`；model_id 重复→`duplicate_model_id`；账本内手写 `usable`/`missing` 且与推导不符→`derived_field_handwritten`。
3. 逐 model 必填（REQUIRED_MODEL 常量表，点路径同 r1）：physical 各项 + conventions（is_static/z_policy/footprint_shape/stable_poses/inherited_from）+ mass_kg/friction + source 各项。
4. `stable_poses`：空→`no_stable_pose`；`is_default:true` 计数≠1→`multiple_default_poses`（0 个也用此码，message 区分）；逐条 orientation_wxyz 模长容差 1e-6→`bad_quaternion`。
5. `mass_kg`/`friction`：`status`∈MASS_STATUS 否则 `bad_enum`；`status=="known"` 且 value None→`unknown_shape`；`status=="estimated"` 且无 `estimator`→`estimator_required`；`runtime_default_basis`∈DEFAULT_BASIS 否则 `bad_enum`。
6. representations：至少一条 backend=="sapien"（role != "snapshot"）→否则 `no_sapien_representation`；逐条 format/uri/role/sha256/size_bytes 存在、role∈ROLES、backend∈BACKENDS、sha256 匹配 64hex。
7. `kind=="articulated"` 且 articulation 缺 `joint_names`→`articulation_required`。
8. license 非 dict 或缺 spdx/status/terms_note→`license_not_structured`；status∈("declared","unknown")。
9. verification 逐条：backend/check/verdict/run_id/timestamp/verified_digest/report_path 齐全（缺→`missing`）；check∈CHECKS、verdict∈VERDICTS 否则 `bad_enum`。
10. `check_files=True`：逐 representation uri 存在（`file_missing`）、sha256 实算一致（`sha256_mismatch`）。

`reps_digest(model_entry, backend)`：`hashlib.sha256(",".join(sorted(r["sha256"] for r in reps if r["backend"]==backend and r["role"]!="snapshot")).encode()).hexdigest()`。
`derive_usable(ledger, model_id)`：对该 model 跑第 3/4/6/7 条存在性检查，返回 `(ok, missing_paths)`。

- [ ] **Step 4: 跑测试至 Step 1 部分全 PASS**

```bash
python -m pytest tests/test_ledger.py -v
```

- [ ] **Step 5: 补 builder/upsert/append/latest/拆包的失败测试 → 实现**

追加测试：

```python
def test_new_model_entry_and_upsert():
    m = ledger.new_model_entry(
        model=0, representations=make_model()["representations"],
        mesh_bbox_m=[0.078, 0.051, 0.053], mesh_up_axis="Y",
        origin_convention="bottom-center", scale_applied=1.0,
        size_resolution=make_model()["physical"]["size_resolution"],
        conventions=make_model()["physical"]["conventions"],
        source=make_model()["source"], verification=make_model()["verification"])
    led = ledger.upsert_model(None, asset="315_shears", category="shears", kind="rigid",
                              aliases=["shears"], colors=[], materials=[],
                              tags=["rigid", "external"], model_entry=m)
    assert led["asset_id"] == "external_315_shears"      # 缺省前缀规则，可传 asset_id_prefix 覆盖
    assert ledger.validate_ledger(led, check_files=False) == []
    m1 = dict(m, model_id=1)
    led2 = ledger.upsert_model(led, asset="315_shears", category="shears", kind="rigid",
                               aliases=["shears"], colors=[], materials=[],
                               tags=["rigid", "external"], model_entry=m1)
    assert [x["model_id"] for x in led2["models"]] == [0, 1]
    with pytest.raises(ValueError):                       # 资产级漂移写时即抓
        ledger.upsert_model(led2, asset="315_shears", category="shears", kind="rigid",
                            aliases=["tin"], colors=[], materials=[],
                            tags=["rigid", "external"], model_entry=dict(m, model_id=2))


def test_append_and_latest(tmp_path):
    p = tmp_path / "ledger.json"
    led = make_valid()
    dig = ledger.reps_digest(led["models"][0], "sapien")
    led["models"][0]["verification"][0]["verified_digest"] = dig
    p.write_text(json.dumps(led))
    fail = {"backend": "sapien", "check": "settle", "verdict": "fail",
            "run_id": "r2", "timestamp": "2026-08-08T12:00:00",
            "verified_digest": dig, "report_path": "r2.json"}
    out = ledger.append_verification(p, 0, fail)
    assert len(out["models"][0]["verification"]) == 2     # append-only
    latest = ledger.latest_verification(out["models"][0], "sapien", "settle")
    assert latest["verdict"] == "fail"                    # 新 fail 压过旧 pass —— 禁 any(pass)
    assert ledger.append_verification(p, 0, fail)["models"][0]["verification"] \
           == out["models"][0]["verification"]            # 同 (backend,check,run_id,digest) 去重
    stale = dict(fail, run_id="r3", timestamp="2026-08-08T13:00:00",
                 verified_digest="e" * 64, verdict="pass")
    out3 = ledger.append_verification(p, 0, stale)
    assert ledger.latest_verification(out3["models"][0], "sapien", "settle") is None
    # ↑ 最新条 digest 与当前 reps 不符 → 失效返回 None（如实报未验证）


def test_to_ir_bundles_roundtrip():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                           / "shared/openxsim/source/agenticsim"))
    from agenticsim.openxsim.ir import AssetBundle
    flat = ledger.to_ir_bundles(make_valid())
    assert len(flat) == 1 and flat[0]["asset_id"] == "external_315_shears_m0"
    ab = AssetBundle.from_dict(flat[0])                   # 旧读者形状兼容
    ab.validate()
    assert ab.representation_for("sapien") is not None
    assert all(r["role"] != "snapshot" for r in flat[0]["representations"])
```

实现要点：`new_model_entry` 组装 model dict（mass 缺省 `{value:None,status:"unknown",runtime_default_kg:0.1,runtime_default_basis:"global_constant"}`，articulated 调用方传 `mass_override` 用 `urdf_inertial` basis；friction 缺省 basis="none" + note）；`upsert_model(ledger=None→新建)` 资产级字段比对不一致 raise、同 model_id 整条替换；`append_verification` 用 `fcntl.flock` 锁 `path.with_suffix(".lock")`、去重、`tempfile.mkstemp`+`replace` 原子写；`latest_verification` 按 timestamp 取最新一条再核 digest；`to_ir_bundles` 摊平（conventions 展开进 physical、剔 snapshot、asset_id 加 `_m<N>`）。

- [ ] **Step 6: 全量测试 + 回归**

```bash
python -m pytest tests/test_ledger.py -v && python -m pytest tests/ -q
```

- [ ] **Step 7: Commit**

```bash
git add 1_asset_reuse/lib/ledger.py 1_asset_reuse/tests/test_ledger.py
git commit -m "feat(ledger): v1 每资产账本——validator+upsert+锁 append+latest 语义+IR 拆包"
```

---

### Task 3: `backfill_ledger_v1.py` — 现存 per-model bundle 聚合升 v1

**Files:** Create: `1_asset_reuse/scripts/backfill_ledger_v1.py` / Test: `1_asset_reuse/tests/test_backfill_ledger.py`

**Interfaces:** Consumes Task 2 全部；Produces `data/asset_library/<asset>/ledger.json` + 报告 `{"written", "skipped", "violations", "notes"}`。

- [ ] **Step 1: 写失败测试（tmp 迷你池：1 资产 2 model，旧 bundle 两份 + fragment + import_matrix，形态照 r1 计划的 `_mini_pool` 但加第二个 model 与 `models."1"` fragment 条目）**

断言要点（完整测试文件结构同 r1 计划 Task 3，改动处）：

```python
def test_apply_aggregates_to_one_ledger(tmp_path):
    lib, results, frag = _mini_pool(tmp_path)             # 造 399_widget m0+m1
    r = _run(lib, results, frag, tmp_path / "rep", apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((lib / "399_widget/ledger.json").read_text())
    assert led["schema_version"] == "asset_ledger.v1"
    assert [m["model_id"] for m in led["models"]] == [0, 1]   # 聚合为一份
    assert led["semantics"]["aliases"] == ["widget", "gadget"]  # 资产级一次
    sp = led["models"][0]["physical"]["conventions"]["stable_poses"]
    assert sp[0]["pose_id"] == "upright" and sp[0]["is_default"] is True
    v = led["models"][0]["verification"][0]
    assert v["check"] == "settle" and len(v["verified_digest"]) == 64
    assert "run_id" in v and "T" in v["timestamp"]
    report = json.loads((tmp_path / "rep/backfill_report.json").read_text())
    assert report["violations"] == {}
```

外加 dry-run 不落盘、二次 `--apply` 幂等（账本内容不变）两个测试，结构同 r1。

- [ ] **Step 2: 跑测试确认失败**（脚本不存在）

- [ ] **Step 3: 实现**

流程（参数 `--library-dir --results-root --fragment --out [--apply]`）：
1. 枚举资产目录×`model_data<N>.json` → (asset, N) 清单；`ledger.json` 已存在且 v1 → 整资产 skip（幂等）。
2. 每 (asset, N) 在 `results_root` glob `*/bundles/{asset}_m{N}.json` 取 mtime 最新为基底；找不到记 `no_bundle_found`。
3. 旧 bundle → `new_model_entry`：conventions 合入 fragment `models[str(N)]`（`stable_pose_id`+`stable_orientation_wxyz` **合成 stable_poses 单元素列表** `is_default:true`）；`precedent`→`inherited_from`；mass 补 `runtime_default_basis:"global_constant"`（articulated→`urdf_inertial`）；friction/origin_convention/converter/license 结构化/retrieved_at/source_manifest_path 规则同 r1 计划；verification 从同 run `import_matrix.json` 合成一条（`run_id`=run 目录名、`timestamp`=目录名日期+`T00:00:00`、`verified_digest`=`reps_digest(entry,"sapien")`、verdict 按 status）。
4. 逐资产 `upsert_model` 聚合（aliases/colors 取 fragment 资产级条目；无条目→aliases=[category]+报告 `aliases_defaulted`）。
5. `validate_ledger(check_files=True)` → violations 进报告；`--apply` 写 `ledger_path(lib, asset)`；有 violation exit 1。

- [ ] **Step 4: 跑测试 PASS** → **Step 5: Commit**

```bash
git add 1_asset_reuse/scripts/backfill_ledger_v1.py 1_asset_reuse/tests/test_backfill_ledger.py
git commit -m "feat(ledger): backfill——per-model 旧 bundle 聚合升每资产 v1 账本"
```

---

### Task 4: `gen_fragment.py` — 账本 → fragment（latest 过滤 + gate）

**Files:** Create: `1_asset_reuse/scripts/gen_fragment.py` / Test: `1_asset_reuse/tests/test_gen_fragment.py`

**Interfaces:** Produces `generate(library_dir, *, license_gate=False) -> tuple[dict, dict]`（fragment dict, stats dict 含 `unknown_license_models` 计数）+ `write_yaml(frag, path)` + CLI `--library-dir --out [--license-gate]`。YAML 结构与现 merged fragment 同构。

- [ ] **Step 1: 写失败测试**

```python
import json, sys
from pathlib import Path
import pytest
yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "1_asset_reuse/scripts"))
sys.path.insert(0, str(REPO / "1_asset_reuse"))
from lib import ledger
from tests.test_ledger import make_valid
import gen_fragment


def _write(lib, asset, led):
    for m in led["models"]:                                # digest 补真值
        for v in m["verification"]:
            v["verified_digest"] = ledger.reps_digest(m, v["backend"])
    p = lib / asset / "ledger.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(led))
    return led


def test_projection_default_pose(tmp_path):
    _write(tmp_path, "315_shears", make_valid())
    frag, stats = gen_fragment.generate(tmp_path)
    m = frag["315_shears"]["models"]["0"]
    assert m["stable_pose_id"] == "upright"                 # 列表→标量投影
    assert m["stable_orientation_wxyz"] == ledger.X90_WXYZ
    assert "is_static" not in m                             # False 不输出
    assert stats["unknown_license_models"] == 1             # 警告计数


def test_latest_fail_excluded(tmp_path):
    led = make_valid()
    led["models"][0]["verification"].append(
        {"backend": "sapien", "check": "settle", "verdict": "fail",
         "run_id": "r2", "timestamp": "2026-08-08T12:00:00",
         "verified_digest": "补真值占位", "report_path": "r.json"})
    _write(tmp_path, "315_shears", led)
    frag, _ = gen_fragment.generate(tmp_path)
    assert "315_shears" not in frag                         # latest=fail → 出视图（禁 any(pass)）


def test_stale_digest_excluded(tmp_path):
    led = make_valid()
    _write(tmp_path, "315_shears", led)
    p = tmp_path / "315_shears/ledger.json"
    led2 = json.loads(p.read_text())
    led2["models"][0]["verification"][0]["verified_digest"] = "e" * 64
    p.write_text(json.dumps(led2))
    frag, _ = gen_fragment.generate(tmp_path)
    assert "315_shears" not in frag                         # digest 失效=未验证


def test_license_gate(tmp_path):
    _write(tmp_path, "315_shears", make_valid())
    frag_off, _ = gen_fragment.generate(tmp_path)
    frag_on, _ = gen_fragment.generate(tmp_path, license_gate=True)
    assert "315_shears" in frag_off and "315_shears" not in frag_on


def test_cli_warns_unknown(tmp_path, capsys):
    _write(tmp_path, "315_shears", make_valid())
    gen_fragment.main(["--library-dir", str(tmp_path), "--out", str(tmp_path / "f.yml")])
    assert "unknown license" in capsys.readouterr().err.lower()   # 无论开关必打警告
```

（`test_latest_fail_excluded` 里 fail 条目的 digest 在 `_write` 统一补真值。）

- [ ] **Step 2: 确认失败** → **Step 3: 实现**

`generate`：glob `*/ledger.json` → 逐资产逐 model：`ledger.latest_verification(m, "sapien", "settle")` 非 None 且 verdict=="pass" 才候选；`license_gate=True` 时再要求 `m["source"]["license"]["status"] == "declared"`；统计 unknown 计数进 stats。投影：资产级 category/aliases/colors 直取（账本唯一，无聚合冲突问题）；model 级从 `stable_poses` 取 `is_default` 条写 `stable_pose_id`/`stable_orientation_wxyz` 标量 + z_policy/footprint_shape(+is_static 仅 true)。`main`：无论开关向 stderr 打 `WARNING: N models with unknown license in view`（gate 开启时改为 `excluded by license gate`）。YAML 手排格式同 r1 计划（对齐现 fragment 行格式）。

- [ ] **Step 4: PASS** → **Step 5: Commit**

```bash
git add 1_asset_reuse/scripts/gen_fragment.py 1_asset_reuse/tests/test_gen_fragment.py
git commit -m "feat(ledger): gen_fragment——latest-settle+digest 过滤、default pose 投影、license-gate 开关+常显警告"
```

---

### Task 5: `import_materialize.py` 切 v1 + 快照 + validator 门

**Files:** Modify: `1_asset_reuse/scripts/b_batch/import_materialize.py`（bundle 构造 / 落账写出 / fragment 写出三处——原 378-441、493 一带，行号按当前文件重定位；**该文件有并发会话未提交改动，动手前先协调**）

**Interfaces:** Consumes `new_model_entry/upsert_model/validate_ledger/ledger_path/reps_digest`（Task 2）、`gen_fragment.generate/write_yaml`（Task 4）。

- [ ] **Step 1: bundle 构造改 builder（378-437 行）**

```python
        settle_entry = {"backend": "sapien", "check": "settle",
                        "verdict": "pass" if checks["pass"] else "fail",
                        "run_id": out.name, "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
                        "verified_digest": "",              # entry 组装后由 reps_digest 回填
                        "report_path": str(out / "import_matrix.json"),
                        "thresholds": {"settle_disp_m": 0.002, "z_min_m": -0.002,
                                       "tilt_deg": 15, "tilt_deg_flat": 45}}   # 数值抄脚本现有硬门常量
        conv_v1 = {**{k: conv[k] for k in ("is_static", "z_policy", "footprint_shape")},
                   "stable_poses": [{"pose_id": "upright",
                                     "orientation_wxyz": ledger_mod.X90_WXYZ,
                                     "is_default": True}],
                   "inherited_from": conv.get("precedent")}
        entry = new_model_entry(model=int(model), representations=reps,   # reps 含快照条，见 Step 2
                                mesh_bbox_m=size, mesh_up_axis="Y",
                                origin_convention="bottom-center",
                                scale_applied=size_res["scale"], size_resolution=size_res,
                                conventions=conv_v1, source=source_v1,
                                verification=[settle_entry])
        settle_entry["verified_digest"] = reps_digest(entry, "sapien")
```

（`reps` 与 `source_v1` 组装规则同 r1 计划 Task 5——converter/conversion_params 固化、license 结构化、retrieved_at 取 staging_manifest mtime、source_manifest_path 按实际镜像位置。）

- [ ] **Step 2: settle 通过后渲快照（owner 决定 #2）**

settle 场景销毁前，挂 offscreen 相机渲一张正面照（**复用 `scripts/a_forward/s3_validate_sapien.py` 的渲染写法**——它已实现 settle+渲染，把相机段搬过来）：存 `<library_dir>/<asset>/snapshots/m<N>_default.png`，追加 representation：

```python
        reps.append({"format": "png", "uri": str(snap_path), "backend": "portable",
                     "role": "snapshot", "sha256": sha256(snap_path),
                     "size_bytes": snap_path.stat().st_size,
                     "metadata": {"yaw_deg": 0, "camera": "front-default",
                                  "renderer": "sapien-3.0.0b1"}})
```

渲染失败不挡入库（snapshot 可选）：except 后打 stderr 警告并继续。

- [ ] **Step 3: 落账 upsert + validator 门（原 438 行一带）**

```python
        led_path = ledger_path(args.library_dir, asset)
        existing = json.loads(led_path.read_text()) if led_path.exists() else None
        led = upsert_model(existing, asset=asset, category=meta.get("category", "unknown"),
                           kind="rigid", aliases=aliases, colors=colors, materials=[],
                           tags=["rigid", "external", "batch"], model_entry=entry)
        violations = validate_ledger(led, check_files=True)
        if violations or not checks["pass"]:
            row["status"] = "rejected"
            row.setdefault("reasons", []).extend(f"schema_violation:{v.code}" for v in violations)
        else:
            led_path.write_text(json.dumps(led, indent=2, ensure_ascii=False))
        (bundles_dir / f"{asset}_m{model}.json").write_text(       # 运行快照=摊平形态
            json.dumps(to_ir_bundles(led if not violations else
                       upsert_model(None, asset=asset, category=meta.get("category", "unknown"),
                                    kind="rigid", aliases=aliases, colors=colors, materials=[],
                                    tags=["rigid", "external", "batch"], model_entry=entry)
                       )[-1], indent=2, ensure_ascii=False))
```

（settle fail 的 model：快照 bundle 照写（池层记录），权威账本不 upsert 失败条——与现行「淘汰隔离出资产池」语义一致；实现时若原逻辑先 emit row 再写文件，保持原顺序。）

- [ ] **Step 4: fragment 写出改调 generator（原 493 行 frag_lines 块删除）**

```python
import gen_fragment
frag, stats = gen_fragment.generate(args.library_dir)
gen_fragment.write_yaml(frag, Path(args.overrides_fragment))
print(f"WARNING: {stats['unknown_license_models']} models with unknown license in view",
      file=sys.stderr)
```

- [ ] **Step 5: 冒烟（ast.parse + 关键字断言 + 全量 pytest，同 r1 计划 Task 5 Step 4 写法，断言改 `upsert_model(`/`schema_violation`/`role": "snapshot"` 存在、`frag_lines` 不存在）** → **Step 6: Commit**

```bash
git add 1_asset_reuse/scripts/b_batch/import_materialize.py
git commit 1_asset_reuse/scripts/b_batch/import_materialize.py -m "feat(ledger): materialize 切 v1 账本（upsert+validator 门+快照渲制+fragment 生成化）"
```

---

### Task 6: `s13b` 关节体 v1 落账

**Files:** Modify: `1_asset_reuse/scripts/b_reverse/s13b_validate_articulated.py` / Test: 追加 `test_ledger.py`

- [ ] **Step 1: 追加 articulated builder 测试**（同 r1 计划 Task 6 Step 1，改动：conventions 用 `stable_poses:[{pose_id:"upright", orientation_wxyz: IDENTITY_WXYZ, is_default: True}]`，mass_override basis=`urdf_inertial`，断言 `validate_ledger` 0 violation + `balance_gate` 在 `models[0]["articulation"]`）
- [ ] **Step 2: 跑测试**（builder 已支持则 PASS）
- [ ] **Step 3: 改 s13b 落账**：定位现有 `*_bundle.json` 写出块 → `new_model_entry`（articulation 含 joint_names/types/limits/closed_qpos/open_qpos + `balance_gate={"free_joints_allowed": bool(args.allow_free_joints), "measured_equilibrium": measured_eq if args.allow_free_joints else None}`；verification 初条 check=`joint_sweep`、run_id=输出目录名、timestamp 秒级、digest=`reps_digest(entry,"sapien")`）→ `upsert_model`（kind="articulated"）写权威位置 + 摊平快照双写。
- [ ] **Step 4: `ast.parse` + 全量 pytest** → **Step 5: Commit**

```bash
git add 1_asset_reuse/scripts/b_reverse/s13b_validate_articulated.py 1_asset_reuse/tests/test_ledger.py
git commit 1_asset_reuse/scripts/b_reverse/s13b_validate_articulated.py 1_asset_reuse/tests/test_ledger.py -m "feat(ledger): s13b 关节体 v1 落账（balance_gate+joint_sweep 验证条目）"
```

---

### Task 7: `s11` 回填 + `s5` 改读权威账本

**Files:** Modify: `scripts/c_catalog/s11_runtime_load_sweep.py`（rows 写出后）、`scripts/a_forward/s5_check_ir.py`（AssetBundle.from_dict 调用处）

- [ ] **Step 1: s11**——rows 写出后逐 row：

```python
lp = ledger_path(args.library_dir, row["asset"])
if lp.exists():
    led = json.loads(lp.read_text())
    m = next(x for x in led["models"] if x["model_id"] == row["model"])
    append_verification(lp, row["model"], {
        "backend": "sapien", "check": "runtime_load",
        "verdict": "pass" if row["ok"] else "fail",
        "run_id": out.stem, "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "verified_digest": reps_digest(m, "sapien"), "report_path": str(out)})
```

（row 键名实现时对准 s11 实际字段；无 `--library-dir` 参数则新增，默认 `../data/asset_library`。）

- [ ] **Step 2: s5**——`AssetBundle.from_dict(data)` 处改：输入是权威账本时（含 `models` 键）先 `to_ir_bundles(data)` 逐条 from_dict+validate；摊平快照照旧直读（向后兼容分支）。
- [ ] **Step 3: 追加单测**：`test_ledger.py` 加 runtime_load 往返（append 后 `latest_verification(m,"sapien","runtime_load")` 命中且 digest 有效），同 r1 计划 Task 7 Step 2 改形。
- [ ] **Step 4: pytest + ast.parse 两脚本** → **Step 5: Commit**

```bash
git add 1_asset_reuse/scripts/c_catalog/s11_runtime_load_sweep.py 1_asset_reuse/scripts/a_forward/s5_check_ir.py 1_asset_reuse/tests/test_ledger.py
git commit 1_asset_reuse/scripts/c_catalog/s11_runtime_load_sweep.py 1_asset_reuse/scripts/a_forward/s5_check_ir.py 1_asset_reuse/tests/test_ledger.py -m "feat(ledger): s11 runtime_load 回填 + s5 经 to_ir_bundles 消费权威账本"
```

---

### Task 8: 真实池 backfill + fragment 等价守门 + 切换验收

**Files:** Modify: `1_asset_reuse/README.md`；产物 `results/20260808_ledger_v1_backfill/`

- [ ] **Step 1: dry-run**（命令同 r1 计划 Task 8 Step 1）——报告 violations 逐条归因：修 backfill 映射或显式 unknown，**不放宽 validator**。
- [ ] **Step 2: `--apply`**；`ls ../data/asset_library/*/ledger.json | wc -l` 期望 = 资产数（16）；报告 violations=={}。
- [ ] **Step 3: fragment 语义等价守门**（yaml.safe_load 双方 assert 相等，脚本同 r1 计划）——注意生成版由 stable_poses 投影而来，等价即证明投影无损；不等价先归因后切换。
- [ ] **Step 4: 切换 + 全量回归**：cp 生成版为 s9 输入；`pytest tests/ -q`（42+新增）+ openxsim 49 不破。
- [ ] **Step 5: e2e**：`scripts/c_catalog/s9_build_shadow_root.py` 重建（参数同 README 用法节）+ `bash scripts/c_catalog/s10_e2e_scene.sh` 四连判定照过（脚本已重组进 c_catalog/，s10 头部写死的路径若因重组失效，先修 s10 头部变量——属重组适配，非本计划范围扩张）。
- [ ] **Step 6: 账本入 git**：

```bash
git add data/asset_library/*/ledger.json
git commit -m "data: 现存池资产 v1 权威账本（backfill 产物，validator 0 error）"
```

- [ ] **Step 7: README 用法节**：账本契约段（权威位置/backfill/gen_fragment 命令、"fragment 是生成物，改账本再生成"纪律、**发布纪律：对外发布前必须 `--license-gate` 且 unknown 归零**）。Commit：

```bash
git add 1_asset_reuse/README.md
git commit -m "docs: 账本契约用法与发布纪律（license-gate）"
```

- [ ] **Step 8: 收尾核对** `git log --oneline feat/env-gen-ir-bridge..HEAD`；合并时机由用户决定。

---

## Self-Review 记录（r2）

- **Spec 覆盖**：§3 资产级/models[] 全字段→Task 2；stable_poses 改形→Task 2/3/4/5/6；verification 语义（digest/run_id/timestamp/latest/锁/去重）→Task 2/5/6/7 + gen_fragment 过滤；git-track→Task 1/8；mass 三态+basis→Task 2（validator+builder）；快照→Task 5 Step 2；license-gate+警告→Task 4/5/8；拆包适配→Task 2 `to_ir_bundles` + Task 7 s5；backfill 聚合→Task 3。s4（A 线）仍范围外（账本不在池权威位置）；license audit 待议不在本计划（spec §8）。
- **占位符扫描**：无 TBD；「实现时对准实际键名」两处（s11 row 键、s13b 变量）均给定位方式与语义约束；make_model 中 digest 占位注明由 Step 5 改真调用。
- **类型一致性**：`validate_ledger(ledger,*,check_files)`、`upsert_model(ledger|None,...)->dict`、`append_verification(path,model_id,entry)`、`latest_verification(model_entry,backend,check)->dict|None`、`reps_digest(model_entry,backend)->str`、`to_ir_bundles(ledger)->list[dict]`、`generate(library_dir,*,license_gate=False)->(dict,dict)` 全计划引用一致；淘汰码 `schema_violation:<code>` Task 5 引入、Task 8 README 记录。
