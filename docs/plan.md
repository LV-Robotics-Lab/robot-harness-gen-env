# env-gen-dev 实现计划

**Goal:** 在 env-gen 之上加两个新能力——资产复用、仿真环境迁移——不改上游。
**Architecture:** env-gen 作为外部只读依赖（pristine 克隆 + `git pull` 同步）；两任务作编号阶段（`1_asset_reuse/`、`2_sim_migration/`），只 import/adapter 引用上游，产物落 `results/`。

## Global Constraints
- **上游只读**：绝不修改 `external/env-gen-github` 里的任何文件。
- **运行环境**：统一用 `env-gen-yuxin` conda 环境（scene_gen 已 editable 安装）。
- **产物落发起项目**：运行结果一律入 `results/<YYYYMMDD>_<目的>/`，不塞 external/ 或项目根。
- ⏳ **全量前预检门**：若任一任务出现"并发扇出 / 批量跑且全量很贵"的步骤（如批量迁移多场景），在放全量前加 `sN_preflight`——并发==串行一致性 + 探最高并发 + 定最终并发值。不过不放全量。（见 skill `preflight-gate`）

---
### Task 1: 资产复用（阶段 1）
**Files:** Create `1_asset_reuse/{lib/,sN_*.py}`
- [ ] ⏳ brainstorm 资产复用的具体机制 → 填 `docs/design.md` 第 4 节
- [ ] Step: 待设计后拆分步骤
- [ ] Verify: 待定
- [ ] Commit

### Task 2: 仿真环境迁移（阶段 2）
**引擎已就位**：openxsim 搬入 `shared/openxsim/`（后端中立 IR + 多后端 + 一致性；47 测试通过）。
**Files:** Create `2_sim_migration/lib/`（env-gen↔openxsim 桥接）
- [x] 搬入 openxsim + 标准外壳（README/UPSTREAM）+ 冒烟(--help)/测试(47 passed)
- [ ] ⏳ brainstorm：`resolved_scene` → openxsim IR 的桥接设计 + 首个目标后端
- [ ] Step: 写桥接 adapter → `2_sim_migration/lib/`
- [ ] Verify: 一个 env-gen 场景经 openxsim `transfer` 跑到目标后端 + 一致性通过
- [ ] Commit

> 注：本计划当前为脚手架骨架；两任务的 Step/Verify 在各自 brainstorm 后细化。
