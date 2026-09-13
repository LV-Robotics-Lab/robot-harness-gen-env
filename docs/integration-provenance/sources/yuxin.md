# Yuxin 工作与来源档案

## 角色与归属口径

Yuxin 主要完成资产检索、筛选、下载/转换、复用、物化、ledger、catalog admission 和跨仿真验证。
Yuxin、HYX、huyuxinn 是同一来源域的历史名称。资产模型、NVIDIA/Objaverse/GitHub 服务、RoboTwin、
SAPIEN 和 Isaac Sim 仍属于各自上游；下表归属的是 Yuxin 编写的资产管线与数据整理工作。

`active/` 是合并后的复合实现，并非每一条当前代码都由 Yuxin 独自编写。原 Yuxin 来源由
`source_inventory.json` 固定；合并后 `yuhang5090` 增加了 exact/similar/none、视觉相似、字段补齐和
上游入库，BorisGuo 完成工作区归并/可移植改造，Bingsheng 又增加 v3 evidence integrity 与 Harness
准入。下表优先说明 Yuxin 建立的能力域，并在复合模块处单列后续作者。

## 具体模块与功能

| Yuxin 模块 | Yuxin 完成的具体功能 | Harness 当前/计划接入 | 当前状态 |
|---|---|---|---|
| `lib/a1_providers.py`、`lib/a7_objaverse.py` | 建立本地/NVIDIA/GitHub/Objaverse 分层 provider，规范查询并返回带来源身份的候选 | 作为 asset search provider 层；结果先是候选，不能直接取得资格 | integrated capability，Golden Skill 待接 |
| `lib/a2_selection.py` | 对候选执行类别、许可证、格式、大小等选择门并记录拒绝原因 | 映射为 exact reuse/digital cousin 的 typed selection result | integrated capability，Golden Skill 待接 |
| `lib/a3_webfetch.py` | 下载或读取源模型，核对 source SHA-256，转换为 GLB/staging record，并补取许可证信息 | 所有网络与本地源文件重新入 CAS；下载 URL、hash、license 与 converter identity 进入 receipt | integrated capability，Golden Skill 待接 |
| `lib/a4_coverage.py`、`lib/conventions.py` | 从 prompt 提取缺失类目；检查 catalog coverage；生成 acquisition entry；按类目解析尺寸策略并继承已验证惯例 | 用于 compile 前资产缺口诊断和 acquisition plan；未知尺寸保持结构化 unknown | integrated capability，Golden Skill 待接 |
| `lib/a5_visual.py` | 镜像缩略图、构建/缓存视觉 embedding、融合多路排名，并对本地资产计算视觉相似度 | 作为检索 scorer；排名不是物理或语义最终通过证据 | integrated capability，Golden Skill 待接 |
| `lib/a6_verify.py` | Yuxin 来源域建立名称/图片核对和批量验货；合并后 `yuhang5090` 在 `50e91a4`、`a67efc8` 扩展三档匹配与视觉相似校准 | D001 后由 Codex 视觉判读 adapter 替代独立 VLM 中枢，但沿用结构化检查项和候选证据边界 | composite historical capability，需改接 Codex |
| `scripts/1_search/acquire_batch.py`、`scene_acquire.py` | 编排批量和场景级缺口发现、tier walk、候选筛选、下载与 acquisition 报告 | 拆成 durable asset-acquisition operation；每步返回 ToolResult/Blocker，支持续跑 | integrated capability，Golden Skill 待接 |
| `scripts/2_convert/import_fetch_convert.py`、`scripts/3_materialize/import_materialize.py` | 把外部源转换、staging、规范化和物化为 RoboTwin 布局资产；写 overrides fragment 与产物 hash | 作为 materialize backend；新产物必须进 Harness CAS 和 v3 ledger，且不沿用不完整旧证据 | integrated capability，Golden Skill 待接 |
| `lib/ledger.py`、`lib/ledger_writes.py` | Yuxin 在 `0df4fb4` 定稿 ledger v3、在 `9b94596` 收紧 portable URI；后续 `yuhang5090` 补 frame/geometry/upstream 字段，Bingsheng 在 `567969e`、`db24ea9` 加入 Harness admission 与 evidence integrity | 是资产证据合同的复合来源；Bingsheng 的 `assets.py`/`asset_repair.py` 继续做 fail-closed admission/repair | composite，历史数据 blocked |
| `scripts/ledger/migrate_v3.py`、`settle_repair.py`、`writeback_verification.py` | 迁移旧账本、修复 settle 结果并把 SAPIEN/Isaac 验证写回 ledger | 当前工具存在 bootstrap deadlock；Bingsheng 负责新增可审计 staging/repair seam，而不是就地绕过验证 | integrated tools，blocked |
| `scripts/5_catalog/s9_build_shadow_root.py`、`s14_catalog_admission.py`、`s10_e2e_scene.sh` | 构建不污染 RoboTwin 的 shadow root 和扩展 catalog；逐资产标准 prompt 准入；跑文字到场景的资产复用回归 | 复用 shadow/catalog 思路；正式 Golden line 改由 Registry qualified Skill 和统一 promotion 授权 | integrated capability，需 Harness 化 |
| `2_sim_migration/lib/usd_enrich.py`、`scripts/isaac_settle_check.py` | enrich USD 物理元数据，并在 Isaac Sim 做 settle 验证 | 作为跨仿真/资产质量辅助证据；Genesis 最终门仍需独立执行 | integrated auxiliary capability |
| `active/data/asset_library/`、`active/data/upstream_ledgers/` | 整理外部资产池和上游资产 ledger，保留来源、模型、representation 与验证历史 | 当前审计对象；不得因 byte match 或历史 pass 自动晋升 | integrated data，162 份历史 ledger blocked |
| `active/web/app.py` | 提供资产检索/核对工作台和结果查看入口 | 可参考前端交互；最终工作台必须绑定 Harness 真实 event/receipt | integrated historical UI，非 Golden UI |

## 权威来源与固定锚点

原工作区 `/home/jingxiang/yuxin/env-gen-dev` 已在整合后移除。权威索引为
`self_improving/source_inventory.json`：

- source main：`ecea99faaec4e082e1fdbc3716ee498cea8dbf87`
- web studio v2：`ec477bc96e8f7bcae0d28a6dfe3d1ed40b1592e1`
- working-tree snapshot：`0b87fed8a2903be8a13f146b9a2b549658a79b74`
- history merge：`899c649179608d950f7ac776a645d21734af2d20`
- 本仓库历史 ref：`worktree/hyx@e5ad817348dbf2e0bfb6753013181fbcc34a1e36`
- alias screening archive：`324dcae1ef0f8875dccc44e227fd4f5a38be1b32`
- asset sources archive：`8c2f058215b4d69d10f15c269148397a9f14ccd2`
- asset reuse ABC archive：`4982cc3660d82743b849fd0ba9cbdfd04933e895`

规范整合树是 `self_improving/asset_pipeline/active/`；overlay、archive 和 source-only workbench 只用于
追溯。历史说明见 `self_improving/contributor_notes/yuxin-gen-env-overview-legacy.md`。

## Yuxin 与 Bingsheng 的接力边界

Yuxin 完成资产检索/复用管线和历史资产数据；Bingsheng 负责将这些能力接入统一 Skill/Registry/CAS、
修复或隔离历史 evidence debt，并为 fresh generation、exact reuse、digital cousin 三种来源重新签发
可信 receipt。当前 162 份历史 ledger 恰有 4,082 条 v3 违规，因此“管线已整合”不能写成“资产已获
runtime qualification”。
