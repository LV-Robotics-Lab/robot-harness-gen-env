# 1_asset_reuse — 资产复用（阶段 1）

**面向不同仿真生态的资产检索与引进管线**：把「要什么物体」（文字 prompt 或类目清单）
变成「可直接用于场景生成的资产」——按信任梯度检索 本地库 → NVIDIA → GitHub → Objaverse，
AI 看图验货 + 物理质检合格后收进 RoboTwin 布局资产库、注册进 env-gen 扩展 catalog，
被文字场景真实选中。底层依托 RoboTwin(SAPIEN) 与 Isaac/USD 两生态的资产互通能力
（线 B 反向引入 · 线 C 全流程复用 · 线 D 检索选定；线 A 正向转换已归档）。
全景与逐文件说明见本目录 `OVERVIEW.md`，操作指南见 `docs/asset_search_usage.md`。

## 结构

| 路径 | 作用 |
|---|---|
| `scripts/` | 步骤脚本按流程顺序编号分夹：1_search/ 2_convert/ 3_materialize/ 4_validate/ 5_catalog/ + 横切 ledger/ probe/（入口 run_smoke.sh 在根，地图见 scripts/README.md） |
| `archive/` | 已归档不再维护的代码（1_forward_convert/ = 线 A 正向转换，2026-08-10 归档） |
| `archive/external_manifest.json` | 外部资产批量清单（来源 URL、资产归组、类别/别名/footprint） |
| `../data/asset_library/` | 外部资产池（RoboTwin 布局，**按来源分一级**：`nvidia/ objaverse/ github/`；`_source/` 存源镜像+哈希清单，不按来源分。只有 `ledger.json` 入 git，见该目录 README） |
| `../data/robotwin_shadow/` | 影子根（symlink 真 RoboTwin + 注入外部资产；不入 git） |
| `../data/scene_gen_ext/` | 扩展 overrides + catalog（上游扫描器产出；不入 git） |


## 用法

> 资产搜索/引进的完整操作指南（网页 + 命令行清单 + 模板 + 结果解读）→ `docs/asset_search_usage.md`

```bash
export OMNI_KIT_ACCEPT_EULA=YES
export SAPIEN_PYTHON=/path/to/env-gen-python
export ISAAC_PYTHON=/path/to/isaac-sim-python
export ROBOTWIN_ROOT=/path/to/RoboTwin
# 打样回归（B+C 全链路，4 步）—— 全程隔离，产物全落 results/_test/，生产 data/ 只读不写
bash scripts/run_smoke.sh
# 批量导入外部资产（按 archive/external_manifest.json）
"$ISAAC_PYTHON" scripts/2_convert/import_fetch_convert.py \
  --manifest archive/external_manifest.json \
  --source-root ../data/asset_library/_source --staging <staging目录>
"$SAPIEN_PYTHON" scripts/3_materialize/import_materialize.py \
  --staging <staging目录> --library-dir ../data/asset_library \
  --out <结果目录> --overrides-fragment ../data/scene_gen_ext/external_overrides_fragment.yml
# 重建影子根 + 扩展 catalog（消费上面的 fragment）
"$SAPIEN_PYTHON" scripts/5_catalog/s9_build_shadow_root.py \
  --library-dir ../data/asset_library --shadow ../data/robotwin_shadow \
  --ext-dir ../data/scene_gen_ext \
  --extra-overrides ../data/scene_gen_ext/external_overrides_fragment.yml
# e2e 回归（文字→场景选中外部资产→回放→全量验证）
bash scripts/5_catalog/s10_e2e_scene.sh
```

前置：conda `env-gen-yuxin`（SAPIEN 侧）+ `isaac-smoke`（Isaac Sim 5.1，py3.11）。

运行路径统一由 `../runtime_config.py` 管理。仓库内目录默认相对此 checkout；
外部依赖用 `ROBOTWIN_ROOT`、`SAPIEN_PYTHON`、`ISAAC_PYTHON` 注入。高级覆盖项
包括 `ASSET_PIPELINE_ROOT`、`GEN_ENV_ROOT`、`ASSET_CATALOG` 和
`OBJAVERSE_DATA_ROOT`，无需再修改源码里的个人绝对路径。

### 账本契约（asset_ledger.v1）

- **权威位置**：`../data/asset_library/<来源>/<asset>/ledger.json`（每资产一份，含
  `models[]`；随 git 提交，是唯一权威入库记录——`results/**/bundles/` 只是它的摊平快照产物）。
- **现存资产升级 v1**（一次性、幂等；已是 v1 的资产自动跳过）：
  ```bash
  # 先跑 dry-run（不带 --apply）看报告，violations 逐条 triage
  "$SAPIEN_PYTHON" scripts/backfill_ledger_v1.py \
    --library-dir ../data/asset_library --results-root ../results \
    --fragment ../data/scene_gen_ext/external_overrides_fragment_merged.yml \
    --out <报告目录>
  # violations 能清零的资产落账本
  "$SAPIEN_PYTHON" scripts/backfill_ledger_v1.py \
    --library-dir ../data/asset_library --results-root ../results \
    --fragment ../data/scene_gen_ext/external_overrides_fragment_merged.yml \
    --out <报告目录> --apply
  ```
  个别资产若在不编造的前提下无法清零 violations（如缺 `_source/<group>/`
  镜像目录、旧 bundle 从未记录 `mesh_up_axis`/`size_resolution`），会被列进
  报告的 `excluded`，账本不落——不阻塞其它资产，人工补齐数据源后重跑即可纳入。
- **fragment（`external_overrides_fragment*.yml`）是从账本生成的产物，不是权威源**：
  手改不会被后续任何步骤读取，下次重新生成会静默覆盖。要改 fragment 内容，改账本
  （或 backfill 的映射逻辑），再重新生成：
  ```bash
  "$SAPIEN_PYTHON" scripts/gen_fragment.py \
    --library-dir ../data/asset_library --out <生成目录>/fragment.yml
  ```
- **发布纪律**：对外发布前必须加 `--license-gate`（默认关，只过滤本次生成的
  fragment，不改账本）；无论开关每次运行都打印 unknown 许可证计数警告，发布前
  需将其清零：
  ```bash
  "$SAPIEN_PYTHON" scripts/gen_fragment.py \
    --library-dir ../data/asset_library --out <生成目录>/fragment.yml --license-gate
  ```
- **手工删除库内资产文件是不安全操作**：账本 / `results/**/bundles/` 快照 /
  `_source/` 镜像三者以 sha256 + `source_manifest_path` 互相引用，直接 `rm`
  会让引用它的账本条目产生悬空指针而不报错。资产退役需经账本裁剪（从
  `models[]` 移除 + 级联检查无其它引用）——用 `scripts/retire_asset.py
  --library-dir ... --asset <资产名> [--model N] [--apply]`（缺省 dry-run，
  打印将删清单不落盘）；该工具只清理**池内**文件（网格/model_data/快照）
  + 裁剪账本条目，**不**级联清理 `results/**/bundles/` 运行快照或
  `_source/` 镜像（镜像可能被多个资产共享，不清是有意保守设计，需要
  清理这两者仍需手工评估），不要手工删资产池文件。

## 批量验收纪律（4.5 对齐）

- 每模型硬门：settle 位移<2mm、无穿模（原点 z>-2mm）、直立（<15°；自然平躺物 <45°）。
- 未过门的模型进 `import_matrix.json` 淘汰记录（含原因），**不进 catalog**。
- 来源→转换→产物全程 sha256；质量/许可证等未知项标结构化 unknown。
- 判定一律以产物内容为准（Kit 吞退出码）。

## 已知限制（2026-08-20 全库实测复核）

复核方法与明细见 `work/probe_20260818_dustbin_render/`（探针目录，不入 git；结论已固化在此）。

**在役限制**

- **碰撞体多为视觉副本**：65 个资产里 **61 个** collision 直接复用 visual 网格（加载时凸分解），仅 **4 个**走离线 CoACD；清单 `collision: coacd` 可逐条启用。
  ⚠️ 但实测**它与"静不下来"无关**：视觉副本组 60 个实测样本末段旋转超阈仅 **10%**（中位 0.0000），CoACD 组 4 个反而 **25%**。曾假设"原始扫描件当碰撞体 → 静置失败"，**已被普查证伪**，别再往这个方向查。
- **声明位姿只对上游原生件复验过**：`calibrate_native_origins.py` 覆盖 **111 个原生 model**；我方 3XX 资产的 `stable_pose_id` 全部来自惯例继承，**从未按「声明无免检」复验**。当前撤销记录只有 2 条（`017_calculator`、`069_vagetable`），均为原生件。这是最实的一条缺口。
- **12 个资产被视图层排除**（资产没问题，是桌面包络的取舍）：`036/314_cabinet`、`323_pallets`、`326_window`、`332_curtain`、`336_sleigh`、`342_workbench`、`344_scaffold`、`353_shelf`、`359_solar`、`363_sofa`、`011_dustbin`（footprint 0.65×0.74 m）。
- **质量/摩擦/惯量诚实标 unknown**：v3 账本记 `mass_kg = {value: null, status: "unknown"}`，交由引擎推导。（原文"默认 0.1kg、记在各 bundle"是 v1 bundle 时代的说法；全仓 grep 已无 0.1kg 默认值。）

**已关闭（原文与现状不符，保留一行以免再被当作待办）**

- ~~A 线脚本 s1/s2 绑定打样样本~~ —— 线 A 于 2026-08-10 归档，`s1_convert_rigid.py`/`s2_convert_articulated.py` 只存在于 `archive/1_forward_convert/`，不在役。
- ~~外部资产许可证标 unknown~~ —— 已完成：73 个 model 的 `license.status` **全部 declared**（CC-BY-4.0 44 / NVIDIA 条款等 27 / SCEA 1 / CC-BY-SA-4.0 1），unknown 计数 **0**。

**已修复（2026-08-20）：固定静置视界会把"慢"误判成"不停"**

`still_moving` 原先在第 900 步一次性判定，把仍在收敛的物体和真不停的物体一起否掉。
同场景 900 vs 2700 步对照实测：

| 对象 | 900 步 | 延长后 | 结论 |
|---|---|---|---|
| `321_dustbin` | 0.586° fail | **0.362° pass**（+360 步） | 只是慢 |
| `035_apple` | 99.00° fail | **0.000° pass**（+1800 步） | 只是慢 |
| `304_bottle` | 2.298° fail | **0.157° pass**（+3600 步） | 只是慢 |
| `002_bowl` | 144.5° fail | 16.999° **仍 fail** | 真不停 |
| `021_cup` | 168.3° fail | 2.063° **仍 fail** | 真不停 |

**调阈值这条路被实测否掉**：仅旋转超阈的一组落在 0.54–1.71°，真在动的一组从 1.07° 起，**两类重叠**，任何旋转阈值都会同时误伤与漏放。真正干净分离的是位移（≤0.00092 m vs ≥0.00232 m），而它本就没被触发。
修法是 `script/run_scene_runtime.py` 新增 `--settle-converge-max N`：仍在动就按块续跑并重新取窗，直到收敛或到上限。**默认 0 = 行为与改动前逐位相同**（7 个对象回归验证一致），需要的验收链显式开启。

## 自动防护机制（三类历史问题的结构性防复发）

| 机制 | 位置 | 防什么 |
|---|---|---|
| 惯例继承 | `lib/conventions.py` + materialize/fragment | 新资产自动照抄同类先例的 is_static/z_policy/footprint（账本记 `conventions_inherited_from`）；无先例标"惯例未验证"。朝向不继承（资产几何语义，由规范化管线按 kind 决定） |
| 尺寸策略 | manifest `size_policy` + `resolve_size` | match_category（默认，同类中位数参照、容差 [0.6,1.6] 外才缩放）/ absolute:<m> / none；缩放系数与参照来源入账本 |
| 关节平衡门 | `s13b` | 硬门=收敛（末段 qpos 差分<1e-3）；自由漂移关节（>5°/5mm）默认拒绝，`--allow-free-joints` 显式豁免并记录实测平衡位 |
| 目录准入 | `s14_catalog_admission.py`（s9 `--admission`） | 视图层检查：每个外部资产单独编译标准 prompt，不适配只滤出本视图（可逆），资产池永不动 |
