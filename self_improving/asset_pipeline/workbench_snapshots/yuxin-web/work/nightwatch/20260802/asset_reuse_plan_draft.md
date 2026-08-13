# 资产复用（1_asset_reuse）初步执行方案 — 基于 2026-08-02 冒烟结论

> 状态：草案（冒烟已全绿）。正式化前需群内/用户拍板文末 4 个决策点。
> 对应任务卡：Phase 2 · 4.5 Asset reuse（RoboTwin→Isaac 转换线）。

## 0. 冒烟已验证的地基（不再是假设）

- **工具链可用**：Isaac Sim 5.1 在 lv-5090（Blackwell）无头可跑；asset_converter 吃
  RoboTwin GLB、URDF importer 吃规范化后的 PartNet mobility.urdf。
- **语义翻译打通**：Y-up→Z-up、自校准缩放（实测包围盒对齐，不依赖转换器单位约定）、
  视觉/碰撞配对装配（碰撞挂 convexDecomposition + guide）。
- **双后端验收可行**：同一资产 SAPIEN（原始）+ Isaac（转换）静置验证全过。
- **账本闭环**：源哈希→转换器版本/参数→产物哈希 + 结构化未知（质量、许可证、米制）。
- **与 Transfer 咬合**：AssetBundle 注册 isaacsim 表示后，openxsim
  `representation_for("isaacsim")` 命中，编译 blocker 消除。

## 1. 分阶段执行计划

### P1 · 晋升试验夹（1–2 天，先出方案等 ok）
- `work/asset_spike/` 脚本参数化（`--asset-id/--model-id` 任意资产；输出目录按资产分组），
  移入 `1_asset_reuse/{lib,scripts}`，规范化器独立成 `lib/urdf_normalizer.py`。
- 修 Isaac 相机取景；判定沿用"产物内容为准"（s6 模式固化为 lib）。
- README/docs 同步；feature 分支 commit + push（git 门禁走 plan-before-acting）。

### P2 · 刚体批量线（catalog 驱动）
- 输入：env-gen `asset_catalog.json`（130 条，含 visual/collision 路径与 scale）。
- 每资产：bundle 构建 → 转换+装配 → 双后端静置 → 通过/有条件/淘汰三态记录（含原因），
  汇总成矩阵报告（PDF 4.5 的 Top-K/淘汰要求）。
- 磁盘紧张（29G）：产物按批清理，只留 bundle+哈希+失败样本。

### P3 · 关节体批量线
- URDF 规范化器扩展：重名几何（已做）、空 base link、缺 inertial、mesh 缺失检测。
- 关节验收加严：dof/limit 对齐（已做）+ 关节扫掠测试（每关节从 lower 驱动到 upper，
  验证可动且不爆炸）——这是"关节有效性"的实证。
- 米制策略落地（见决策点 2）。

### P4 · 质检加严（对齐 4.5 验收要点）
- 双工况：贴地静置（已有）+ 参数化跌落对照（记录两引擎差异，喂给 Transfer 的损失量化）。
- 渲染对比：SAPIEN/Isaac 同视角截图并排存档。
- 全部判定进回归：固定输入+配置+种子 → 预期产物哈希。

### P5 · 账本与许可
- provenance schema 从 bundle metadata 提炼进 `shared/`（与 Zheng Ye/Harness 冻结接口对齐）。
- 补查 RoboTwin 资产上游许可（Objaverse/PartNet-Mobility 条款），unknown 逐步清零。

### P6 · 与 Transfer 端到端咬合
- bundle 注册表落 `data/asset_bundles/`；openxsim 编译消费转换资产做一次端到端
  text→场景→迁移 Isaac 的演示——两个任务的交汇验收点。

## 2. 冒烟固化的技术决策（批量线沿用）

1. **自校准缩放**：目标尺寸 = trimesh scene.bounds × model_data scale（权威）；
   converter 输出实测包围盒对齐；extents 注释仅作交叉校验（偏差>5% 报警）。
2. **上游零改动**：一切规范化产出派生副本，哈希双向记录。
3. **判定以产物内容为准**：Kit 退出码不可信；验证 JSON + 注册命中才算过。
4. **结构化未知**：质量/惯量/摩擦/许可/米制，缺失记 unknown + 运行默认，不编造。

## 3. 待拍板决策点

1. 晋升 + git 提交的 ok（P1 前置）。
2. 关节体米制策略：distance_scale=1.0（现状，柜子约 1m）vs 按类别真值表校准。
3. 质量默认：统一 0.1kg vs 类别默认表。
4. 批量范围：catalog available=18 先行 vs 全 134（磁盘 29G 是约束，建议 18 先行）。
