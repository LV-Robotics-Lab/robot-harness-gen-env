# 资产检索选定层接入设计（retrieval → 现有引入管线）

- 日期：2026-08-03
- 状态：已评审通过，待实施计划
- 阶段：`1_asset_reuse/`（Asset reuse 任务卡的"搜索官方仓库、排序 Top-K、记录淘汰原因"部分）
- 前置：批量引入管线（`import_fetch_convert.py` → `import_materialize.py` → `s9_build_shadow_root.py`）已端到端验证（线 B/C 绿灯）

## 1 背景与目标

现状：资产引入靠人工手写 `configs/external_manifest.json`，检索、排序、候选留证、淘汰原因均缺失；openxsim 已有 `AssetScout`（provider 协议 + 聚合排序 + 证据落盘）但未接入本管线。

目标（两层）：
1. **场景驱动自适应（最终形态）**：`scene_acquire.py --prompt` 一条命令完成"需求提取 → 覆盖检查 → 缺口自动按四级路径引进 → 场景生成"，落地任务卡"资产缺失时先进入复用检索，复用失败后才进入生成兜底"的语义；
2. **批量检索引擎（心脏 + 验证工具）**：类别清单 → 多源检索 → 排序选定 → 淘汰留证 → 自动 manifest → 现有引入管线 → 注册扩展 catalog 的全自动闭环。场景驱动层的第 ③ 步整体复用它，清单来源从"人手写"变为"场景需求推导"；批量 CLI 独立保留，用于流程验证与资产库预热。

互联网检索（GitHub 两级）为实跑通路，更多货源以 provider 协议保留接口。

## 2 已确认决策

| 决策点 | 结论 |
|---|---|
| v1 完成标准 | 全自动闭环（机器选定，无人工圈选环节） |
| 货源 | 四级信任梯度：本地 RoboTwin → NVIDIA 资产服务器 → GitHub 白名单仓库 → GitHub 全网发现（实现但默认关） |
| 触发方式 | **场景驱动为主**（`scene_acquire.py --prompt`，自动闭环到场景生成）；类别清单批量模式（`acquire_categories.json`）保留作引擎验证与资产库预热 |
| 需求提取与缺口判定 | 只读 import 上游 `parse_rule_based`（prompt→SceneSpec）与 `ground_object`（grounding 打分）——与上游 solve 同源，杜绝"我们认为够、solve 认为缺"的判定漂移 |
| 复用失败兜底 | 检索耗尽的需求落结构化 blocker（exhausted 清单），即未来 Generation fallback 的标准输入接缝；上游 `--generate-missing-assets`（程序化占位几何 proxy，非真资产）v1 不使用，留作 v2 可选降级 |
| 去重策略 | 本地已有同类则跳过引进，记 `already_available_locally`（本身即一条淘汰原因） |
| 实现路线 | 薄检索层：新写 provider/门禁/编排；聚合排序、GitHub 搜索、通用下载直接 import openxsim 现成件，**openxsim 零修改** |
| 旁路入口 | `pinned`（指定服务器资产，跳过检索）与 `local`（本地文件直接注册，跳过拉取）均进 v1；local **条目形态**排最后一个里程碑、可独立砍（其底层"本地文件 → 物化注册"通路被 GitHub 汇流复用，属主线不可砍） |
| 互联网资产处理 | GitHub 候选经通用下载落地后，内部并入 local 注册路径；v1 网络源限 glb/gltf/obj，USD 留 v2 |
| 许可证 | 自动抓取（GitHub SPDX）或白名单配置；查不到记结构化 unknown，不拦截；`license_gate` 开关预留，v1 默认关 |

## 3 架构与数据流

### 3.1 场景驱动主流程（scene_acquire.py）

```
输入: --prompt "Place a red kettle on the table." --seed N
 ① 需求提取   只读 import 上游 parse_rule_based → SceneSpec（类别+颜色等属性）
 ② 覆盖检查   逐对象调上游 ground_object(需求, 当前扩展 catalog)
              可命中 → 零动作（tier 0 快路径）
 ③ 缺口引进   缺口合成清单条目（category+aliases+属性 token）→ 调用批量引擎（§3.2）
 ④ 场景生成   跑上游 generate_scene（catalog 已含新资产）→ solve/打包/验证
 ⑤ 兜底       仍失败 → failure_report + exhausted 清单 → 结构化 blocker
产物: 场景包 + coverage_report（逐需求：命中/引进/失败）+ selection_evidence
```

### 3.2 批量检索引擎（被 ③ 调用；也可独立批跑）

```
configs/acquire_categories.json（类别清单，三种条目形态）
  │
  ├─ tier 0  RoboTwinLocalProvider：查上游 catalog（category/aliases）
  │          命中 → already_available_locally，status=reused_local，结束
  ├─ tier 1  NvidiaAssetServerProvider：S3 键索引匹配（首跑建本地索引缓存）
  ├─ tier 2  openxsim GitHubTreeSearchProvider：白名单仓库文件树搜索
  ├─ tier 3  openxsim GitHubRepositoryDiscoveryProvider：全网发现（默认关闭）
  │          （逐级下探：高层级有可过门禁的候选即停；evidence 记 tiers_consulted）
  │
  ├─ AssetScout 聚合 + _relevance 排序 → Top-K
  ├─ 候选硬门禁 → 逐候选淘汰码（§7）
  ├─ 选定第 1 名 → 生成 manifest 条目（configs/acquired_manifest.json，编号续 3XX）
  │
  ├─ 拉取（双通路，§8）→ 规范化 → SAPIEN 静置门禁 → asset_library 物化
  │   → AssetBundle 账本 → s9 重建影子根 + 扩展 catalog
  │
  └─ 排名回退：选中者在管线任一环节失败 → validation_failed:<gate> 等淘汰码，
      取下一名重试（默认最多 2 次回退）；耗尽 → status=exhausted
输出：selection_evidence.json + acquired_manifest.json + 新资产（bundle/catalog 条目）
```

## 4 组件与文件

| 文件 | 性质 | 职责 |
|---|---|---|
| `1_asset_reuse/lib/a1_providers.py` | 新增 | `NvidiaAssetServerProvider`（包装现有 S3 列举逻辑 + 键索引缓存）、`RoboTwinLocalProvider`（上游 catalog 匹配），均实现 openxsim `AssetSearchProvider` 协议；provider 注册表 + 四级分级调度（配置驱动） |
| `1_asset_reuse/lib/a2_selection.py` | 新增 | 候选硬门禁、淘汰码枚举、`selection_evidence.json` 写盘、manifest 条目生成与 3XX 编号分配 |
| `1_asset_reuse/lib/a3_webfetch.py` | 新增 | GitHub 候选经 openxsim `download_candidate` 落地 → 合成 local 注册条目的胶水（~80 行） |
| `1_asset_reuse/lib/a4_coverage.py` | 新增 | 覆盖检查：逐 SceneSpec 对象调上游 `ground_object` 判命中/缺口，产出缺口清单与 `coverage_report.json`（~80 行） |
| `1_asset_reuse/scripts/acquire_batch.py` | 新增 | 批量引擎编排 CLI（env-gen-yuxin）：检索选定 + subprocess 串现有三脚本（双 conda 环境模式沿用 run_smoke.sh）+ 回退循环 + 按内容判定汇总 |
| `1_asset_reuse/scripts/scene_acquire.py` | 新增 | 场景驱动编排 CLI：需求提取（import 上游 `parse_rule_based`）→ a4 覆盖检查 → 调 acquire_batch 引擎 → 跑上游 `generate_scene` → 兜底 blocker（~100 行） |
| `1_asset_reuse/configs/acquire_categories.json` | 新增 | 输入类别清单 |
| `1_asset_reuse/configs/providers.json` | 新增 | provider 启停与参数（白名单仓库、tier 3 开关、license_gate 开关、大小上限、Top-K/回退次数） |
| `configs/acquired_manifest.json`、`results/**/selection_evidence.json` | 生成物 | 自动 manifest（与手写 external_manifest.json 分文件同 schema）、检索证据 |
| openxsim / env-gen 上游 / RoboTwin / 现有三脚本 | **零修改** | 仅 import 或 subprocess 消费 |

lib 命名约定：`a<N>_<function>`（a = acquire，避开 scripts/ 的 s 序号），序号表示在 acquire 流程中的**被调用位置**（a1 搜 → a2 选 → a3 取 → a4 覆盖检查）；它们是被 import 的库模块，非依次执行的脚本。

上游只读 import 说明：`parse_rule_based`、`ground_object`、`load_catalog` 均从 `external/env-gen-github` 的 `scene_gen` 包只读 import（s10 已有 `-m scene_gen.validator` 先例），上游零修改。

**里程碑**：M1 批量引擎核心（a1 + a2 + acquire_batch，NVIDIA 通路的 searched 形态）→ M2 场景驱动层（a4 + scene_acquire，达成最终目标验收）→ M3 GitHub 通路（a3 + 本地文件物化汇入）与 pinned/local 条目形态。M2 依赖 M1；M3 内仅 local **条目形态**可独立砍（其底层物化通路被 a3 复用，见 §2）。

注：openxsim 已迁至 `shared/openxsim/`（commit 6bb94c3，两阶段共同消费）；本设计通过 PYTHONPATH（`shared/openxsim/source/agenticsim`）import `agenticsim.openxsim.*`。所引现成件的位置：`shared/openxsim/source/agenticsim/agenticsim/openxsim/assets.py`（AssetScout、两个 GitHub provider、`download_candidate`、`AssetCandidate`）。

## 5 清单条目三形态

```json
[
  {"category": "hammer", "aliases": ["hammer"]},
  {"category": "kettle", "pinned": {"prefix": "Assets/Isaac/5.1/Isaac/Props/XX", "usd": "kettle.usd"}},
  {"category": "teapot", "local": {"path": "/abs/path/teapot.glb", "up_axis": "Y", "source_note": "…"}}
]
```

- **searched**（默认）：走完整检索排序。
- **pinned**：跳过检索排序，evidence 标 `pinned_by_user`；无候选列表故不回退，失败即 exhausted。
- **local**：再跳过拉取，从规范化开始走后半段。
- 三形态**汇流于门禁之后**：规范化、SAPIEN 静置门禁、账本注册、catalog 重建一步不少。

## 6 providers 配置与信任梯度

`providers.json` 逐 provider 一段：`enabled`、tier 序号、参数（NVIDIA：prefix 根列表 + 索引缓存路径；github_tree：`[{repository, branch, license}]` 白名单；github_discovery：`repository_limit`、token 环境变量名）。全局段：`top_k`（默认 5）、`max_fallback`（默认 2）、`max_size_bytes`、`license_gate`（默认 false）。

新货源接入方式 = 新写一个实现 `search(query, limit) -> list[AssetCandidate]` 的类 + providers.json 加一段。这是"保留接口"的具体含义。

## 7 selection_evidence schema 与淘汰码

每次批跑一份，`schema: envgen.asset_selection_evidence.v1`。顶层：run id、清单哈希、providers 配置快照。每类别：query（category/aliases）、entry_mode（searched/pinned/local）、tiers_consulted、provider_errors、候选全列表（candidate_id/source/url/score/license/verdict/rejection）、selected（含 asset 编号与 model 序号）、attempts、status（imported / reused_local / exhausted / search_failed）。

场景驱动模式额外产出两份：`coverage_report.json`（`envgen.scene_coverage.v1`：逐场景对象需求 → covered / acquired / exhausted，含 grounding 命中的 asset_id 与得分）；检索耗尽时 `asset_gap_blocker.json`（结构化 blocker：未满足需求清单 + 各级淘汰摘要，作为未来 Generation fallback 的输入）。

| 淘汰码 | 含义 | 产生阶段 |
|---|---|---|
| `already_available_locally` | 本地已有同类（类别级） | 检索前 |
| `unsupported_format` | 格式不在支持表（网络源 v1 仅 glb/gltf/obj） | 候选门禁 |
| `no_token_match` | 类别词与路径/名称不匹配 | 候选门禁 |
| `thumbs_artifact` | 缩略图/伪影文件 | 候选门禁 |
| `oversize` | 超出 `max_size_bytes` | 候选门禁 |
| `license_blocked` | license_gate 开启且许可证 unknown/黑名单（v1 默认不启用） | 候选门禁 |
| `outranked` | 排名低于选中者且未被尝试 | 排序 |
| `fetch_failed` / `convert_failed` | 拉取 / 转换失败 | 管线 |
| `validation_failed:<gate>` | 物化门禁失败，gate 名取自 materialize 现有判定（settle/penetration/tilt/…） | 管线 |

## 8 拉取双通路与汇流

```
NVIDIA 候选 → 现有 import_fetch_convert.py（S3 镜像 + USD→GLB，isaac-smoke，不动）─┐
GitHub 候选 → openxsim download_candidate（HTTP 下载 + sha256 + 来源页记录）        ├→ 同一后半段
pinned      → 归入 NVIDIA 通路（本质是免检索的服务器资产）                          │  （规范化→门禁→
local       → 直接进后半段                                                        ─┘   物化→账本→catalog）
```

GitHub 下载物在内部被合成为一条 local 注册条目（含 provider 元数据、license、源哈希），与用户手工 local 条目走完全相同的代码路径——网络引入不产生第二套物化逻辑。

## 9 错误处理、幂等、写入边界

- **类别间隔离**：任一类别失败（含 provider 网络错误）不中断批次；逐类别 PASS/FAIL 行 + 汇总行，任一 FAIL 退出码非零（按内容判定惯例）。
- **回退边界**：searched 最多 `max_fallback` 次；pinned/local 不回退。
- **幂等**：重跑同一清单，已引进类别被 tier 0 拦截变 `reused_local`；3XX 编号从 asset_library 现有目录扫描分配，不漂移。
- **写入边界**：只写 `data/`、`1_asset_reuse/configs/acquired_manifest.json`、`results/`；RoboTwin、env-gen 上游、openxsim 零写入。
- **索引缓存**：NVIDIA 键索引带抓取时间戳，`--refresh-index` 强制重建；缓存命中时检索全离线。

## 10 测试与验收（DoD）

单测（pytest，env-gen-yuxin，全离线，fixture 提供键列表/仓库树样本）：
1. provider 匹配与排序（含 tier 下探逻辑）；
2. 候选门禁逐淘汰码；
3. manifest 生成与编号分配（含幂等重跑）；
4. 三种条目形态合成与汇流；
5. evidence schema 完整性（每个非选中候选必有淘汰码）；
6. 覆盖检查（fixture SceneSpec + 缩小版 catalog：命中/缺口/属性不满足三种判定，与上游 `ground_object` 结果一致）。

集成验收（lv-5090 实跑一次，清单 5 条）：
- `cup` → 预期 `reused_local`；
- `hammer` → 预期从 NVIDIA/YCB 检索引进（048_hammer）；
- 一条 pinned、一条 local（用线 B 已产出的 mug GLB 当样本）、一条走 GitHub 白名单仓库；
- 验收：① evidence 完整且逐候选有淘汰码；② 扩展 catalog 条目数正确增长、上游扫描器识别新资产；③ 新资产 AssetBundle 完整、SAPIEN 静置验证 pass；④ 立即重跑第二次：零新引进、全部 `reused_local`（幂等证明）。

场景驱动验收（最关键，对应最终目标）：
- `scene_acquire.py --prompt "Place a red mug on the table."` → 覆盖检查全命中（tier 0），**零引进**直接出场景，PASS；
- `scene_acquire.py --prompt` 一个 catalog 没有的物品（如 kettle/banana，实施时以 NVIDIA 源确有为准）→ 自动引进 → 场景生成 PASS，coverage_report 记录 acquired 链路；
- 一个四级全搜不到的物品 → `asset_gap_blocker.json` 落盘、退出码非零、无脏数据入库。

## 11 非目标（v2 顺延）

- Anchor2Env 图片/视频入口的场景驱动接入（v1 只接 Text2Env 的 prompt 通路）；
- 检索耗尽后的降级策略（上游程序化 proxy、真 3D 生成）——v1 止于结构化 blocker；
- 网络源 USD 资产（需 Isaac 反转换通路接入）；
- license_gate 默认开启与许可证黑白名单策略；
- 语义检索/视觉相似度排序（现为关键词 + 规则打分）；
- GitHub 之外的新货源（Objaverse、Sketchfab 等，经 provider 协议接入）。

## 12 风险与开放问题

- **GitHub 全网发现质量不可控**：默认关闭；开启时依赖下游门禁 + 回退兜底，首次开启建议人工抽查 evidence。
- **NVIDIA 键索引规模**：prefix 根配置控制列举范围，避免全桶扫描；索引缓存落盘复用。
- **同类多义词匹配漏检**：关键词匹配可能漏掉命名不规范的资产（v1 已知局限，记入 no_token_match 统计观察）。
- ~~openxsim 目录迁移~~：已完成（`shared/openxsim/`，commit 6bb94c3），本设计所有引用以该位置为准，风险消除。
- **上游内部 API 依赖**：`parse_rule_based`/`ground_object` 是上游 Python 内部函数而非 CLI 契约，上游 `git pull` 后签名可能变。缓解：a4 单测以上游真函数跑 fixture（上游一变测试即红）；上游同步节奏本就由人控制（pristine 克隆手动 pull）。
