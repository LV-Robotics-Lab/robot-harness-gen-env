# C01 来源冻结与无损保护记录

## 2026-09-13 后续完成增量

- 用户补充批准持续实施与替代后端，C01 已完成保护/来源分类/clean 基线。
- [92 refs 处置](branch-manifests/20260913-source-intake-disposition.json)：64 absorb、24 supersede、
  4 archive，均绑定冻结 commit、源码路径和目标 C03–C14 切片。absorb 是选择性采用语义的决定，
  不表示代码已经重放；各来源的实际目标 feature SHA 仍须在实现时补齐，不能计作实现完成。
- [其他五份 dirty 保护](branch-manifests/20260913-other-dirty-protection.json)：全部在本地 archive
  和独立恢复目录核验，源 HEAD/index/status/bytes 不变。完整 receipts 在
  `/home/jingxiang/bingsheng/canonical-other-dirty-20260913.yCBQaL/`。
- 用户研究独立提交 `c94eecd`；两份历史证据与五份 mixed 文档的有效 hunk 提交 `46a2718`。
  原始六份新文档保持 byte-exact，未保留进 active 文件的 hunk 仍在 archive。
- 重新核对 bingsheng 全部保护文件未变化后，把 7 个 untracked 普通文件及子模块 STL 移至
  `/home/jingxiang/bingsheng/canonical-premerge-20260913.Jcdaw7/removed-from-active-worktree/`；
  9 个 tracked 文件恢复原基线，然后正常 ff-only 到 `46a2718`。没有丢失数据；Git archive 与
  moved bytes 都可恢复。主目录与子模块现在 clean，未 push。
- 基于该 clean bingsheng 创建新 authority：
  `/home/jingxiang/bingsheng/worktrees/canonical-x2env-consolidation-20260913`，
  branch `codex/canonical-x2env-consolidation-20260913`。不是 merge 历史 integration 的数百提交。
- 以下内容是第一轮保护时的原始状态，仅由本增量覆盖其在途结论。

- 状态：`组件实现`（仅 Git intake / 本地保护操作；C01 整体尚未完成）。
- 批准计划：`0a8abc01839f2c37a64b9c08f643f48985fb4a79`；用户附件与 committed plan 原文同 SHA-256：
  `2f16d98515a89fd5d9cf530fc47ad5a31d521d8329699acf3b8dce5ffe61e656`。
- C00：`6e88985`。用户主目录未清理、未切换、未提交用户 hunks；未建 canonical 开发分支或 push。

## 冻结范围

主会话 `git fetch --prune origin` 成功后，读取
[冻结清单](branch-manifests/20260912T201042Z-pre-consolidation.json)：

- 捕获 UTC `2026-09-12T20:10:42` 至 `20:10:54`（本地日期 2026-09-13）。
- SHA-256 `5ef3a0c0bbf0d8c6f81bef1c9302f3795e780acb234804a4faecc19717f4c02a`。
- 92 refs：86 local heads、6 origin refs（含 symbolic HEAD）；87 worktrees。
- 80 clean、6 dirty、1 prunable；与规划时 79/7/1 不同，以本次实际快照为准。
- 分支和 worktree registration 前后相同；源 dirty bytes 前后相同。integration 自身捕获目录的
  excluded 文件列表在捕获期间变化，因此该项 `dirty_stable_through_capture=false`；原始前后记录保留，
  不把这次非原子捕获说成全局事务 snapshot。
- 每 ref 记录 merge-base、ahead/behind、`git cherry` patch equivalence、Git author 和责任字段。
  **全部 disposition 仍 pending**：没有把已进入历史 integration 等同已进入 canonical。
- `origin/worktree/bingsheng...worktree/bingsheng` 实际为 `0:152`；目标 HEAD 仍 `ea26524`。
- helper [capture.py](branch-manifests/capture.py) 只读来源，排除 ignored/runtime/gitdir，仅保存路径和
  摘要；这是本次 intake 操作工具，不是 canonical 产品能力或 qualification runner。

## bingsheng 已验证保护

在两个仓库分别用 alternate index、`commit-tree`、创建新 local archive ref；未使用 stash、reset、
checkout 覆盖或 `git add -A`。先保护子模块，再让父 archive tree 的 gitlink 指向其 snapshot。

| 仓库 | archive ref（仅本地） | snapshot commit |
| --- | --- | --- |
| 本仓库 | `refs/archive/x2env-premerge/20260912T201153Z/bingsheng` | `0993082727d55ea78b965439a19642e2c46d65f6` |
| OpenReal2Sim | `refs/archive/x2env-premerge/20260912T201153Z/openreal2sim` | `65980ebf86bd3df7884913c464e84909f801e677` |

另有同名前缀 `bingsheng-index` / `openreal2sim-index` 保存原 index tree，snapshot commit 分别以它们
为 parent。原始 index bytes、index/worktree binary diff、逐文件 size/SHA/mode 和恢复校验回执位于：

`/home/jingxiang/bingsheng/canonical-premerge-20260913.Jcdaw7/`

- `bingsheng/receipt.json` SHA-256
  `2f60b5ac1e31d199983ec2e01945b8c045babbeb6df6b6aa519a98dc98972191`；
- `openreal2sim/receipt.json` SHA-256
  `5293ba9880456e88ef53ba8d4db0df4f2b3bd156a8ffe563deb0e645514bf60f`；
- 一次性操作脚本 `protect.py` SHA-256
  `013b0276613688764cb0ec0b7409c1c487880467f1550804307deec9755866b9`。

两份 archive 均在对应 `<name>/restored` 新 detached worktree 实际 checkout。16 个普通 dirty 文件
（511,043 bytes）及子模块 1 个 ASCII STL（5,302 bytes）全部复算 SHA 一致；主会话额外独立检查
archive ref/commit/tree、原文件和恢复文件。原 source HEAD、index bytes 和 porcelain status 前后未变。
STL 仅在子模块本地保护 ref，未导入产品或父仓库资产。扫描未命中有限私钥/token/literal credential
模式，无超过 1 MiB 的待保护文件；不是全面 secret-free 认证。未向远端上传任何 archive。

上述恢复 worktree 和 archive refs 是冻结后的保护动作，不反向扩展原 87 个 worktree 的 intake 范围。
其他五个 dirty worktree 已记录摘要，**尚未建立可恢复 archive**，所以 C01 后续整合门仍未通过。

## 用户 dirty 文件分类（尚未应用）

| 文件 | 处置建议 | 证据/下一步 |
| --- | --- | --- |
| `demo/app.py`、`tests/demo/test_app.py` | duplicate_or_obsolete | AST 与原 HEAD 相同，仅排版；保护后不混入 canonical feature |
| `docs/research/gujie-x2env-integration-audit-20260911.md` | duplicate_or_obsolete | 与 integration committed 文件逐字相同；复用已有 committed source |
| `self_improving/golden_e2e_progress/TODO.md` | duplicate_or_obsolete | 旧 P0/P2 在途状态，不覆盖新的 C00–C15 |
| `repo-docs/references/source-evidence.md` | duplicate_or_obsolete | 包含将已有 replay/qualification 退回未实现的过时描述 |
| `docs/evidence/digital-cousins-dependency-audit-20260906.md`、`replay-vlm-integration-progress-20260906.md` | independent_user_commit | 保留日期与历史结论，不计作 canonical 运行 |
| `self_improving/studies/ASPIRE/` 下四份新增研究文档 | independent_user_commit | integration 无同名文件，独立研究，不混入 canonical 代码 |
| `RESULTS.md`、`IMPLEMENTATION_LOG.md`、`repo-docs/change-log.md`、`code-map.md`、`modules/self-improving-platform.md` | mixed_pending_hunk_split | 保存独立历史/A042/A043/retained 文档索引；排除旧资格状态回退；需逐 hunk 分开提交 |
| 子模块 `finger_0.obj.convex.stl` | duplicate_or_obsolete / local archive only | 既有 finger OBJ 派生小缓存；不作为新来源资产 |

本表是只读分类建议；五份混合文档尚未最终拆分，任何普通用户文件都没有被移除。

## 验证及后续

- `protect.py` 实际执行 exit 0，约 0.405 秒；17 个文件恢复核验通过。
- 主会话独立验证 archive refs/tree 与原/恢复目录 17 个文件通过。
- intake helper 的 ruff、Python compile 检查通过；没有 canonical 功能改动，没有运行全仓 pytest，
  不宣称 active CI、coverage、Genesis 或新 qualification 通过。
- dashboard portfolio/tasks/project 均 HTTP 404；没有 task id，不伪造更新。
- 继续 C01 前须保留原快照，并处理其他 dirty 工作树、逐 ref disposition 和 mixed hunks。
  当前另有 [C07 许可/资源方向阻断](CANONICAL_C07_RESOURCE_BLOCKER_20260913.md)，按计划 §17.3 暂停。
