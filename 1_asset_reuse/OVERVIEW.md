# OVERVIEW — 资产复用管线（1_asset_reuse）

> 一句话定位：让 RoboTwin 和 Isaac/USD 生态两边的资产互通，并且都能被 env-gen 的
> 「文字 → 场景 → 物理验证」全流程复用的一条资产管线。

## 1. 这是什么

**三条在役功能线 + 一层横切防护**，均已端到端验证：

- **线 B（反向引入）**：外部 USD 物体 → RoboTwin 布局资产。刚体单件打样（s8）、
  关节体 USD→URDF（s13）、清单驱动批量导入（import_*）三种形态；
- **线 C（全流程复用）**：外部资产注册进 env-gen 的 asset catalog，被文字请求的
  grounding 真实选中、通过 SAPIEN 物理回放与全量验证；
- **线 D（检索选定）**：把「人工挑资产、手写清单」升级为自动化——类目需求或场景
  prompt → 四级信任梯度检索 → 门禁淘汰 → 自动走批量引入管线，全程留决策证据；
- **横切防护**：惯例继承 + 尺寸策略 + 关节平衡门 + 目录准入，防三类历史问题结构性
  复发（四类机制总表见 `README.md`）。
- **线 A（正向转换，RoboTwin → Isaac USD）已于 2026-08-10 归档**至
  `archive/1_forward_convert/`：其目标（Transfer 资产侧阻塞消除）已达成，且方向与
  上述引入管线相反、无法纳入统一编号；结论与产物留档，代码不再维护。

打样样本：YCB 025_mug（外部引入，红色马克杯，注册为 `301_cup`）；
001_bottle / 036_cabinet 是已归档线 A 的样本，见 `archive/1_forward_convert/README.md`。批量线已按清单导入 NVIDIA 资产 25 个模型：**17 通过入库**（9 个资产
类目，catalog available 18→27），8 个带原因淘汰（见 `import_matrix.json`）。所有转换以
「双后端物理验证 + 哈希账本」证明没走样。

## 2. 整体 输入 → 输出

```
输入（只读）                                    输出
├─ RoboTwin 资产库（134 物体，jingxiang 处）     ├─ AssetBundle 账本 JSON（每资产一份，双后端表示+哈希+结构化未知）
├─ NVIDIA Isaac 资产服务器（HTTP，逐 prop 拉）   ├─ （线A 产物 bottle.usd / cabinet.usd 已随线 A 归档，见 archive/）
├─ GitHub 公开仓库（tier2 检索源，逐仓声明许可）  ├─ ../data/asset_library/（外部资产池，RoboTwin 布局）
├─ openxsim IR 库（AssetBundle 数据结构）        ├─ ../data/robotwin_shadow/（影子根）+ ../data/scene_gen_ext/（扩展 overrides+catalog）
└─ env-gen 上游（catalog/场景编译/验证器）       ├─ 检索证据 selection_evidence / coverage_report / asset_gap_blocker
                                               └─ 双后端验证报告 / e2e 场景包 / 回放视频截图
```

**成功标志**：线 B = 外部资产在 SAPIEN 静置验证 pass；线 C = 文字 prompt 生成的场景 `resolved_scene.json`
中 asset_id 为外部资产，且全量验证 `fail=0 not_run=0`；线 D = 检索到的资产走完 B/C 全部
门禁入库。

**关键中间产物**：`staging_manifest.json`（批量两阶段交接）、`import_matrix.json`（逐模型
通过/淘汰记录）、`external_overrides_fragment.yml`（catalog 构建输入）、
`selection_evidence.json`（检索决策链）。

## 3. 阶段总览

代码全在本目录 `{scripts,lib,configs,tests}/`；数据产物在 `../data/`（不入 git）；
打样总入口 `scripts/run_smoke.sh`（4 步）。scripts/ 内按**外部资产引入的流程顺序**编号分夹：`1_search/ → 2_convert/ → 3_materialize/ → 4_validate/ → 5_catalog/`；横切工具在 `ledger/`（账本工具链）与 `probe/`（一次性探测/自检）（各夹地图见 `scripts/README.md`）。`sN` 文件名序号保留不改，阶段归属以文件夹为准。

| 阶段 | 职责（一句话） | 代码 |
|---|---|---|
| B · 反向引入打样 | NVIDIA USD→RoboTwin 布局 GLB（探测 + 单件全流程） | `s7` `s8a` `s8b` |
| B批量 · 清单批量导入 | manifest 驱动、两阶段（Kit 转换 / 物化验证）、逐模型硬门 | `import_fetch_convert` `import_materialize` |
| B关节 · 关节体反向 | USD articulation→URDF + 逐 link 网格，SAPIEN 关节扫掠验证 | `s13a` `s13b` |
| C · 影子根 + 验收 | 影子根 + 扩展 catalog + 目录准入 + 三层运行时验收 | `s9` `s14` `s10` `s11` `s12` |
| D · 检索选定 | 类目/场景需求→四级检索→门禁→自动引进 | `lib/a1–a4` `acquire_batch` `scene_acquire` |
| 防护（横切） | 惯例继承 + 尺寸策略（被 B批量/B关节/D 调用） | `lib/conventions.py` |

```
线B: 外部 USD ──(刚体s8 / 关节s13 / 批量import_*)──> RoboTwin 布局资产 ──验证──> 账本 + asset_library
线C: asset_library ──s9 影子根+catalog(±s14 准入)──> grounding 选中 ──SAPIEN 回放──> 全量验证 PASS
线D: 类目/prompt ──a1 四级检索──a2 门禁──> 复用 B批量管线入库 ──> 覆盖报告 / 缺口结构化输出
```

## 4. 分阶段详解

（每阶段：目标 + 输入→输出 / 文件表 / 参数。批量验收纪律与已知限制见 `README.md`，
不重复。Kit 步骤跑 conda `isaac-smoke`（py3.11），SAPIEN/上游步骤跑 `env-gen-yuxin`
（py3.10），Kit 前置 `export OMNI_KIT_ACCEPT_EULA=YES`。）

### 4.1 线 A · 正向转换（RoboTwin → Isaac USD）——【已归档 2026-08-10】

6 个脚本（`robotwin_asset` `s1` `s2` `s4` `s6` `s15`）整体移入
`archive/1_forward_convert/`，`run_smoke.sh` 不再调用。归档理由、逐文件功能、
恢复步骤见 `archive/1_forward_convert/README.md`。

**三个原属本线、但本身不是「正向转换」的脚本未归档**，已按语义归位且仍在生产链路上：
`s3_validate_sapien.py` → `scripts/4_validate/`（SAPIEN 侧物理验证）、
`s5_check_ir.py` → `scripts/ledger/`（账本 IR 校验）、
`s0_verify_isaac.py` → `scripts/probe/`（Isaac 环境自检）。

<details><summary>归档前的原始记录（保留备查）</summary>

- **目标**：RoboTwin 私有布局资产转成 Isaac 可加载 USD，双后端证明物理不走样，注册进账本。
- **输入 → 输出**：RoboTwin 001_bottle / 036_cabinet → `bottle.usd` / `cabinet.usd` +
  双后端验证 JSON + 账本 isaacsim 表示 + 证据渲染。

| 文件 | 功能 |
|---|---|
| `robotwin_asset.py` | 读 RoboTwin 私有布局 → AssetBundle JSON（sapien 侧表示） |
| `s0_verify_isaac.py` | 一次性环境自检：headless SimulationApp + 转换扩展可加载 |
| `s1_convert_rigid.py` | 刚体 GLB→USD + 物理装配（凸分解碰撞、烘焙缩放 0.05 与 Y-up→Z-up 旋转） |
| `s2_convert_articulated.py` | URDF→USD 关节体导入 + USD 关节 prim 与 URDF 声明核对 |
| `s3_validate_sapien.py` | SAPIEN 侧原始资产静置验证（settle + 渲染） |
| `s4_validate_isaac.py` | Isaac 侧转换产物静置验证（位移/倾角/穿模/关节有限性） |
| `s5_check_ir.py` | 账本过 openxsim 校验 + `representation_for("isaacsim")` 命中检查 |
| `s6_verdict.py` | 按产物内容总判定（Kit 吞退出码，不能看返回值） |
| `s15_evidence_shots.py` | 证据渲染：bottle 定机位正面照（world-frame 相机约定）+ cabinet 抽屉 DriveAPI 实际驱动（关/半/开三帧 + 关节遥测） |

**参数**：s1/s2 `--bundle --out-dir`；s3/s4 `--out`；s15 `--assets-dir`（A 线结果目录）`--out`。

**一键运行**（归档前）：`run_smoke.sh` 步 1–7，产物落
`results/_test/20260802_smoke_bottle_cabinet_glb2usd/`。

</details>

### 4.2 线 B · 反向引入打样（NVIDIA USD → RoboTwin GLB）

- **目标**：证明 USD 生态资产能反向进 RoboTwin 布局并过 SAPIEN 验证（单件打样）。
- **输入 → 输出**：NVIDIA 服务器 YCB 025_mug →
  `../data/asset_library/301_cup/{visual,collision,model_data0.json}` + 账本。

| 文件 | 功能 |
|---|---|
| `s7_probe_reverse.py` | 一次性探测：USD→GLB/GLTF/OBJ 反向可行性 + 按六类缺口类目列举资产服务器选品 |
| `s8a_fetch_convert_usd.py` | S3 列举 API 精确拉单 prop（源镜像 + SOURCE_MANIFEST 哈希清单 + upAxis 元数据），asset_converter 反向导出 GLB |
| `s8b_materialize_validate.py` | 规范化物化（upAxis 驱动 Z-up→Y-up 旋转、原点=底部中心、实测包围盒写 model_data）+ SAPIEN settle + 账本 |

**参数**：s7 `--out`；s8a `--source-dir --out`；s8b `--glb --source-dir --library-dir --out`。

**一键运行**：`run_smoke.sh` 步 1–2（产物落 `results/_test/20260803_smoke_usd2envgen/`）。

### 4.3 线 B批量 · 清单驱动批量导入

- **目标**：一份 manifest 批量引入外部资产——一次 Kit 会话转全部，逐模型进程隔离验证，
  未过门的带原因淘汰、物理隔离出资产池。
- **输入 → 输出**：`configs/external_manifest.json` → 资产池多模型目录 + 逐模型 bundle +
  `import_matrix.json` + `external_overrides_fragment.yml`。

| 文件 | 功能 |
|---|---|
| `import_fetch_convert.py` | 阶段 1（isaac-smoke）：镜像各组服务器目录（除 .thumbs，逐文件哈希）、读源 USD upAxis、单次 SimulationApp 会话全量转 GLB，写 `staging_manifest.json` |
| `import_materialize.py` | 阶段 2（env-gen-yuxin）：GLB 规范化→物化→SAPIEN settle 硬门（位移<2mm、z>-2mm、倾角<15°/平躺物 45°）→账本+矩阵+fragment；惯例继承与尺寸策略在此落账 |
| `configs/external_manifest.json` | 人工清单：组（服务器 prefix）+ 逐条 usd/asset/model/category/aliases/colors/footprint，可选 `collision: coacd`（离线精分解）、`size_policy` |

**参数**：

| 参数 | 含义 | 默认 |
|---|---|---|
| fetch: `--manifest --source-root --staging` | 清单 / 源镜像根 / 两阶段交接目录 | 必填 |
| mat: `--staging --library-dir --out --overrides-fragment` | 交接 / 资产池 / 结果 / fragment 输出 | 必填 |
| mat: `--reference-catalog` | 惯例继承 + 尺寸参照的上游 catalog | `external/env-gen-github/data/scene_gen/asset_catalog.json` |
| mat: `--only-index N` | worker 模式：只处理单条记录（主进程逐条自拉子进程，进程隔离防原生崩溃） | 无 |

**一键运行**：README「用法」节的批量导入命令块（fetch → materialize → s9 重建）。

### 4.4 线 B关节 · 关节体反向（USD → URDF）

- **目标**：关节体资产反向进 RoboTwin（URDF + 逐 link 网格），SAPIEN 验证关节真的能动。
- **输入 → 输出**：源 USD articulation → 实例目录（如 `314_cabinet/0/`）的
  `mobility.urdf` + 逐 link OBJ + `model_data0.json` + 截图 + bundle。

| 文件 | 功能 |
|---|---|
| `s13a_usd2urdf.py` | 阶段 1（isaac-smoke）：读 UsdPhysics 关节（revolute/prismatic/fixed）与刚体 link，逐 link rest-frame 导出网格，产 URDF（基座关节抬到 bbox 底=0，metersPerUnit 缩放） |
| `s13b_validate_articulated.py` | 阶段 2（env-gen-yuxin）：SAPIEN 加载（fix_root_link）、dof/limits 与导出报告核对、120 步 settle 有限、逐可动关节驱到双极限不爆炸；写 model_data0/截图/bundle |

**参数**：

| 参数 | 含义 | 默认 |
|---|---|---|
| s13a `--usd --out-dir` | 源 USD / 实例目录 | 必填 |
| s13a `--size-policy --category` | `match_category` \| `absolute:<m>`；match 模式需 `--category` | 无 |
| s13a `--reference-catalog --scale` | 尺寸参照 catalog / metersPerUnit 之上的额外统一缩放（入账） | 上游 catalog / 1.0 |
| s13b `--instance-dir --source-usd --out` | 实例目录 / 源 USD / 结果目录 | 必填 |
| s13b `--allow-free-joints` | 自由漂移关节（>5°/5mm）默认拒绝；此开关显式豁免并记录实测平衡位 | 关 |

### 4.5 线 C · 影子根 + 扩展 catalog + 运行时验收

- **目标**：外部资产池无侵入接入上游运行时；用「e2e / 运行时扫查 / external-only /
  目录准入」四层验收确保进了 catalog 的资产都真能用。
- **输入 → 输出**：真 RoboTwin + `../data/asset_library/` → 影子根 + 扩展
  overrides/catalog → 场景包 + 回放证据 + `validation_full`。

| 文件 | 功能 |
|---|---|
| `s9_build_shadow_root.py` | symlink 全镜像影子根 + 注入外部资产（上游零写入）；扩展 overrides（category/aliases/colors/**stable_orientation_wxyz**/z_policy/footprint）；**上游扫描器原样**跑出扩展 catalog；`--admission` 可串 s14 |
| `s14_catalog_admission.py` | 目录准入（**视图层**）：每个外部资产（3XX）用单资产过滤 catalog 编译标准 prompt，求解器接受与否只决定本视图去留（可逆；池层永不动；重跑 s9 即恢复） |
| `s10_e2e_scene.sh` | e2e 验收：prompt "Place a red mug on the table." → 必须 ground 到 301_cup → 回放 `fail=0` → 全量验证 `fail=0 not_run=0`，四连判定 |
| `s11_runtime_load_sweep.py` | 全部入库外部模型走 RoboTwin **真 create_actor 路径**加载 + settle（cwd=影子根），封「裸 SAPIEN 能载 ≠ env-gen 运行时能载」的缺口 |
| `s12_external_only_e2e.sh` | external-only catalog（掩蔽 RoboTwin，`asset_catalog_external_only.json`）跑 5 类 prompt：每类必须 ground 到我方资产（3XX）且回放 + 全量验证全过 |

**参数**：

| 参数 | 含义 | 默认 |
|---|---|---|
| s9 `--library-dir --shadow --ext-dir` | 资产池 / 影子根 / 扩展产物目录 | 必填 |
| s9 `--robotwin-root --upstream` | 真 RoboTwin / env-gen 上游 | jingxiang 处 RoboTwin / `external/env-gen-github` |
| s9 `--admission` | 建完即跑准入：`report`（只报告）/ `enforce`（滤出本视图） | 不跑 |
| s9 `--extra-overrides` | 追加 overrides 片段（批量导入的 fragment） | 无 |
| s14 `--catalog --work-dir --report` | 视图 catalog / 编译暂存 / 报告 | 必填 |
| s14 `--enforce` | 把 not_admitted 外部条目滤出视图文件（记录在报告，可逆） | 只报告 |
| s11 `--shadow --catalog --out` | 影子根 / catalog / 报告目录 | 必填 |

s10/s12 无参数（路径写死在脚本头部变量）。**一键运行**：`run_smoke.sh` 步 3–4。

### 4.6 线 D · 检索选定层（自动获取）

- **目标**：类目清单或场景 prompt 驱动，按信任梯度自动找资产、过门禁、复用批量管线
  引进，全程留决策证据；查缺补漏而非静默失败。
- **输入 → 输出**：`acquire_categories.json` 或 prompt → 入库资产 +
  `configs/acquired_manifest.json`（自动生成清单）+ `selection_evidence.json` /
  `coverage_report.json` / `asset_gap_blocker.json`。

| 文件 | 功能 |
|---|---|
| `lib/a1_providers.py` | 四级 provider + 注册表：tier0 `robotwin_local`（已在库直接复用）→ tier1 `nvidia_server`（已知授权源）→ tier2 `github_tree`（公开仓库、逐仓声明许可）→ tier3 `github_discovery`（默认开启的更广搜索）；命中即停，不做无谓下探 |
| `lib/a2_selection.py` | 候选门禁 `gate()` 与淘汰码（unsupported_format / thumbs_artifact / oversize / license_blocked…）+ 选定簿记 |
| `lib/a3_webfetch.py` | 拉取 web（GitHub）候选、`to_glb`/`synth_staging_record` 合成 staging 记录对接 materialize |
| `lib/a4_coverage.py` | 场景需求抽取 + 覆盖判定（只读 import 上游 parser/grounding/catalog） |
| `scripts/1_search/acquire_batch.py` | 批量引擎：类目条目→检索→门禁→复用 import 管线物化；per-category PASS/FAIL + SUMMARY，按内容判定；entry 亦接受 `pinned`（指定服务器 USD key）与 `local`（本地网格文件），跳过搜索但仍过门禁 + 物化 |
| `scripts/1_search/scene_acquire.py` | 场景驱动自适应：prompt→覆盖检查→缺口自动引进→重查→生成场景；仍未覆盖的写 `asset_gap_blocker.json`（生成式兜底的结构化输入） |
| `configs/providers.json` | provider 开关/层级/源配置 + 全局 `top_k`/`max_fallback`/`max_size_bytes`/`license_gate` |
| `configs/acquire_categories.json` | 类目需求清单（category + aliases） |
| `configs/acquired_manifest.json` | acquire_batch 自动生成的已引进清单（与手写 external_manifest 同构） |
| `tests/`（7 文件 + fixtures） | 检索层单测：providers / selection / webfetch / coverage / acquire_batch / scene_acquire / 分层搜索（`fixtures/mini_catalog.json`） |

**参数**：acquire_batch `--categories --providers --dev-root --out`，`--refresh-index`
重建 NVIDIA key 索引；scene_acquire `--prompt --seed --catalog --providers --dev-root --out`。
运行环境 env-gen-yuxin。设计决策（八项、淘汰码表、幂等与回退语义）见
`docs/2026-08-03-asset-retrieval-integration-design.md`。

### 4.7 横切防护 · 惯例继承 + 尺寸策略（`lib/conventions.py`）

纯 stdlib，双 conda 环境可 import；被 `import_materialize`、`s13a`、线 D 复用。两条设计规则：

- **类目语义可继承**：`is_static / z_policy / footprint` 从参照 catalog 同类先例照抄
  （账本记 `conventions_inherited_from`；无先例标「惯例未验证」）。
- **资产几何语义绝不继承**：`stable_orientation` 取决于网格自身轴约定，由规范化管线按
  kind 决定（刚体规范化 Y-up→X+90，URDF Z-up→identity）。

`resolve_size` 实现 `size_policy`：`match_category`（默认，同类中位数参照，容差
[0.6,1.6] 外才缩放）/ `absolute:<m>` / `none`；缩放系数与参照来源入账本。
四类防护机制（含 s13b 关节平衡门、s14 目录准入）总表见 `README.md`。

## 5. 关键概念 / 术语

- **AssetBundle / representation**：账本结构——一个资产多个后端表示（sapien=GLB、
  isaacsim=USD），各后端按格式取用；两方向转换都往同一结构里填。
- **影子根（shadow root）**：symlink 出的 RoboTwin 目录镜像 + 注入的外部资产，让"按名字+
  目录约定加载"的上游运行时无感知地看到扩展资产池；真目录零改动。
- **池层 vs 视图层**：资产池（`asset_library` + 账本）只进不出、永不拒收；catalog 是
  池上的**视图**，准入（s14）只过滤视图且可逆，重跑 s9 即全量恢复。
- **四级信任梯度（tier0–3）**：检索按「本地已有→已知授权源→逐仓核许可的公开仓→
  广搜」的信任降序依次询问，命中即停。
- **自校准缩放**：实测转换产物包围盒对齐目标米制尺寸，不假设转换器单位约定。
- **规范化**：外部网格对齐 RoboTwin 惯例（Y-up、原点=底部中心、model_data 布局），由源
  USD 元数据驱动，非启发式。
- **stable_orientation_wxyz**：catalog 中"网格坐标→世界直立姿态"的四元数；Y-up 网格为
  X+90（[0.7071, 0.7071, 0, 0]），求解器以 yaw∘此值合成最终朝向——缺它资产会躺着生成。
- **结构化未知**：查不到的参数（质量/许可证/接触点）记 unknown+运行默认，不编造。
- **按内容判定**：成败以验证 JSON 与账本注册为准（Kit 会吞退出码）。

## 6. 依赖 / 来历

| 依赖 | 性质 | 位置 |
|---|---|---|
| RoboTwin 资产库 + envs 运行时 | **外部只读**（影子根引用） | `/home/jingxiang/workspace/.../external/RoboTwin` |
| NVIDIA Isaac Assets 5.1（YCB 等） | 外部 HTTP 源，逐文件哈希入账；许可证标 unknown 待查 | `omniverse-content-production.s3-us-west-2.amazonaws.com` |
| GitHub 公开仓库（tier2 检索源） | 外部 HTTP 源，逐仓声明许可证（如 KhronosGroup/glTF-Sample-Assets 逐模型许可） | `configs/providers.json` 白名单 |
| env-gen 上游（catalog/编译/验证器） | **外部只读**，全部经 CLI 参数消费 | `env-gen-dev/external/env-gen-github` |
| openxsim（AssetBundle IR） | 项目内 vendored | `env-gen-dev/shared/openxsim/` |
| Isaac Sim 5.1.0.0（pip 版） | 双向转换 + Isaac 侧验证 | conda `isaac-smoke`（py3.11） |
| SAPIEN 3.0.0b1 + trimesh | SAPIEN 侧验证与网格处理 | conda `env-gen-yuxin`（py3.10） |

任务上下文：Phase 2 · 4.5 Asset reuse。线 A 产物曾为 4.7 Transfer 解除资产侧阻塞，
该结论已达成并随线 A 归档（`archive/1_forward_convert/`）。
局部细节（用法命令、批量验收纪律、已知限制、防护机制表）见同目录 `README.md`。
