# 进度与结果

## 2026-09-09 初始化

- Dashboard/ClawCross 项目状态读取：失败，三个公开状态 URL 和 Harness dashboard 均返回 HTTP 404。
- 当前分支：`worktree/bingsheng`。
- 当前 HEAD：`d8b3787`（`chore(portal): remove PEARL evidence portal`）。
- 当前工作树已有用户修改和未跟踪文件；本任务不会批量暂存、还原或覆盖这些内容。
- 本仓库的本地 `worktree/gujie` ref 指向旧祖先 `a25bc58`，不能代表 Gujie 当前实现。
- Gujie 的真实独立克隆位于
  `/home/jingxiang/gujie/gen-env`：当前 HEAD `a8ced27`，其 `origin/worktree/gujie` 同样指向
  `a8ced27`；该提交比已合并 bingsheng 的 `4f6ef23` 多 52 个提交。该工作目录另有大量未提交
  Genesis adapter、测试和文档修改，审计必须把 committed ref 与 dirty workspace 分开，且保持只读。
- 历史 `gujie-x2env-design.md` 明确自称“架构设计阶段”，不能作为已实现 x2env 的证据；真实实现
  资格必须从上述独立克隆的代码、测试和运行产物重新判断。
- 功能基线、Genesis 可用性和路由成功率：待调研后测量，历史测试数字不冒充当前 HEAD 基线。

## 2026-09-09 当前工作树测试基线

- 命令：`pytest -q`
- 结果：`3015 passed, 19 skipped, 1 failed in 142.21s`。
- 唯一失败：`test_checked_in_replay_qualification_matches_current_source_identity`。
- 原因：当前用户修改的 `self_improving/harness/IMPLEMENTATION_LOG.md` 与
  `text2env.replay@1.0.0` qualification manifest 固定的 bytes/SHA-256 不一致；fail-closed 行为符合
  设计，但当前工作树不是可晋升基线。
- 该数字只是当前共享工作树观察值；不能与后续切片测试相加，也不能代表 clean HEAD 或 Genesis
  真实运行通过。
- `python -m self_improving --json`：`ready=false`，因为 HEAD 已删除
  `apps/pearl_evidence_portal`，但 registry 仍把它列为 required；统一平台门当前在测试前失败。
- `python script/export_harness_schemas.py --check`：20 份 Harness schema snapshot 全部通过。
- 当前默认 Python 不含 Genesis。Gujie 独立克隆已有隔离环境
  `/home/jingxiang/gujie/gen-env/venv/genesis`，可导入 Genesis `1.3.3`，源码绑定到该克隆的
  `external/genesis-world`；这只证明依赖可导入，不证明任何 golden case 已运行。
- 在 Gujie 当前 dirty workspace 上运行默认离线 suite：
  `venv/genesis/bin/python -m pytest -q self_improving/sim_adapters/genesis/tests`，结果
  `770 passed, 55 skipped in 230.98s`。跳过项包含显式 opt-in 的真实 Genesis/模型/媒体用例；该结果
  证明大量确定性合同测试可用，但不计作真实物理或 sim-ready golden-line 证据。

## 2026-09-09 Gujie 现存运行产物初检

- 文本任务“场景中有一张桌子。上面放着一个黄色杯子。整体摆放简洁。”的 `TaskOutput.verify()`
  成功复核 1,925 个 manifest 文件，终态为 `physics_passed`。
- 其 Genesis 物理报告记录 `simulation_executed=true`、1,500 steps、桌/杯完整指标通过；最终 orbit
  媒体为 180/180 个唯一解码帧、30 FPS、6 秒。主线程已目视检查 overview：内容为桌面、黄色杯子
  和简洁背景。
- 该产物来自 Gujie dirty workspace，不在当前 Harness run/Registry/ToolResult/portable receipt 中，且
  报告含部署绝对 locator；目前只能作为可接入的真实 Genesis 竖切，不是本任务最终 golden case。
- 图片重建任务“从鼠标原图重新重建并保留 SimFoundry 原始支撑平面”仅到 `scene_built`；preview
  可见重建鼠标，但 physics/final_render 均为 `not_run`，遗留失败为 native-service capability preflight。

## 2026-09-09 并行审计复核

- System 2、portable receipt、validate-v2 和 replay bridge 专项：`756 passed in 24.01s`。
- Asset ledger audit/migration/settle trust 专项：`87 passed`；asset admission：`104 passed`。
- 动态 ledger audit：历史 asset library `audited=66 clean=1 violating=65`；upstream
  `audited=97 clean=0 violating=97`。历史 162 份合计恰好 4,082 条违规。
- `migrate_v3 --dry-run`：预期 exit 1，`migrated=0 failed=163`，确认 migration 与 runtime
  qualification 之间存在 bootstrap deadlock。
- RoboTwin 827 个 representation primary 中 825 个可按 SHA-256 从本机源恢复，2 个 USD 缺失；
  第三方旧副本只有 27 个资产 cohort 可进入 exact-byte staging。
- 详细 claim/evidence/caveat 见 `AUDIT.md`。本次审计未修改 Gujie workspace、ledger 或资产字节。
