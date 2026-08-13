# 值守日志 2026-08-11（新加坡时）

**任务**：把昨夜的检索方案落成代码并跑通全流程，覆盖各种资产来源情况。用户晚上回来验收。
**分支**：`feat/ledger-v2`（续用）。每个阶段一对 commit，可逐段回退。

## 交接假设（用户已离开，按保守默认）
- L2 视觉模型：**本地 Qwen2.5-VL-3B-Instruct**（用户已拍板本地），复用上游 rendered_critic 的
  离线加载约定（HF_HUB_OFFLINE + local_files_only），不申请任何 key。模型已缓存 7.1GB。
- 「全范围覆盖」理解为四类资产来源 × 两条入库路径：
  tier0 本地复用 / tier1 NVIDIA 服务器 / tier2 GitHub 指定仓 / tier3 GitHub 广搜；
  刚体批量 与 关节体单件。生成资产(4.6)尚不存在，只做接口预留不做实现。
- 不改实验语义：不动 prompt、seed、物理验证阈值、catalog 构建方式。
- 每阶段必须自证：单测 + 与基线对比，不破坏既有 218 passed。

## 基线（本次改动前）
- 测试：`PYTHONPATH=.:scripts/1_search:../shared/openxsim/source/agenticsim:../external/env-gen-github pytest tests -q`
  → **218 passed / 11 failed**，11 个全部是 test_backfill_ledger.py（v1 一次性工具，按规矩未动）
- 账本：asset_library 15/15 clean，upstream_ledgers 16/16 clean
- e2e：s10 PASS，s12 5/5

## 阶段计划
| 阶段 | 内容 | 自证方式 |
|---|---|---|
| P1 | L0 语料清洗 + L1 词法词边界 | 误命中归零、既有测试不破 |
| P2 | L1 视觉通道：缩略图索引 + CLIP + RRF | 融合 top-1 ≥ 93% |
| P3 | L2 本地 VLM 身份确认闸 | 6 个不在库查询全被拦 |
| P4 | L3 账本回写 identity/colors/materials | 账本 validate 0 error |
| P5 | 全来源端到端 | 四类来源 × 两条路径逐一跑通 |

## 时间线

### 16:10 起 — P1/P2/P3 落地
基线校正：文档里的 PYTHONPATH 约定（scripts/d_acquire 已重组为 1_search）跑出真实基线
**218 passed / 11 failed**，11 个全部是 test_backfill_ledger（v1 一次性工具）。
昨天报的「157 passed」是漏了 PYTHONPATH 的错误数字。

- **P1**（commit 5528d20）词法词边界 + 语料清洗。实测 trash bin 3→0、teddy bear 2→0、
  cardboard box 候选池 33→6。**如实记录代价**：黏字名 sm_whitecorrugatedbox 不再匹配
  "box"，这部分召回交给视觉通道，已写进测试断言。
- **P2**（commit 7100cb1）视觉通道 + RRF。缩略图 465/465 镜像 52s，CLIP 索引 8.3s，
  缓存后查询 0.00s。修接线 bug：a2.gate 用 provider 字面名判格式，nvidia_visual 的 USD
  被全判 unsupported_format。
- **P3**（commit 88a5766）本地 VLM 身份闸。在库 5/5 首选即 match（0.6-2.7s，顺带出
  colors），不在库 4/4 拦下。

### 出 bug 处置记录
1. **heredoc 写坏源文件**：用 ssh heredoc 打补丁时引号转义把 acquire_batch.py 第 354 行
   写成乱码 `readonly -=569Xl`。**处置**：git checkout 恢复（P2 已 commit，损失仅本次
   未提交编辑），改为本地编辑 + scp 重做四处补丁。**教训已改工作方式**：不再用 heredoc
   改源码。无产物污染。
2. **昨日埋的隐患**：--identity-basis 昨天改成必填，但 acquire_batch 未传 → 整条 acquire
   路径实际已断，今天接线时才发现。已修（P3 内）。

### 关键设计决定（用户不在，我代做，已在 commit message 与代码注释标出）
- **no_thumbnail 判 unverifiable 而非 rejected**：否则 GitHub 来源（无缩略图）会被
  整体封死。放行但身份降级为 requested_by_acquire + verified=false。

### 19:10–20:20 SGT — P5 全来源端到端（部分完成）
沙箱 `/tmp/acq_sandbox_20260811`（生产资产池全程未写入，已用 git status 核实）。
三类目 × acquire_batch：

| 类目 | 结果 | 说明 |
|---|---|---|
| beaker | 检索→闸→转换 全通，**物化失败** | 身份闸判 match(seen_as=beaker, colors=[white])；Kit 转换 PASS；物化报 FileNotFoundError: 305_beaker/visual/base0.glb |
| trash bin | reused_local | ⚠️ 走了 tier0 短路，**身份闸未参与** |
| cup | reused_local | 正确（池内确有 021_cup / 301_cup） |

又修两处集成缺口（commit 9de1a30）：--identity-basis choices 漏 vlm；nvidia_visual 路径未按 dev-root 解析。

**未完成 / 未root-cause**：物化阶段 FileNotFoundError。已定位到具体文件与工序（worker
模式直跑可复现），但未查根因，也未在生产 dev-root 上重试——那会真写资产池，用户不在
不做。tier2/3 GitHub 来源本轮未跑。

**遗留观察（值得单独决定）**：tier0 命中即返回 reused_local，身份闸完全不参与。
tier0 只声明「本地已有该类目」不产生入库，所以不构成池污染风险；但如果 tier0 的
别名匹配是错的，会导致「以为有其实没有」而跳过采购。是否让身份闸也覆盖 tier0 复用，
需要用户定。

### 17:00–19:00 SGT — 三件遗留全部闭环（commit a2f21f8 + f12f5ef）
1. **物化 root cause**：driver 起隔离子进程没转发必填的 --identity-basis → worker 死在
   argparse、无 row 文件 → 报成幽灵 native crash。连带修 worker 目录创建、run_smoke。
2. **tier0 假复用**："garbage can" 的 token "can" 命中 302_can → 假 reused_local。
   改整短语精确匹配。同时把身份闸移进 tier 走查（accept_fn）：整层被驳回继续下探，
   否则视觉通道的无条件 top-N 会让 lantern 永远停在 tier1 的垃圾上。
3. **tier2/3 打通 + post-render 补检**：web 候选下载前无图，unverifiable 放行实测被打穿
   （AnimatedColorsCube 冒充 trash bin 免检进物化）。收口=物化渲染 settle 快照后补看一眼：
   match 升级 vlm/verified=true；mismatch 拒收（当场拦下 chess pieces）。配套：web 源镜像
   进 _source/、GLB 注册 portable 不谎称 isaacsim、profile 如实 sapien_only、溯源 url 入账。

**最终矩阵（/tmp/acq_final4 + final3，生产池零写入，git status data/ 干净）**：
cup=tier0复用 / beaker=NVIDIA预检导入 / duck=GitHub post-render导入(url入账) /
trash bin=三道门全拦 / lantern=物理门拒(62.6°)。账本 audit clean，tests 234 passed。

### 21:00–23:20 SGT — 三项指令全部闭环（commit 见 git log 尾部三条）
① 误判加固：V1 置信度门无效（3B 错得也自信）、图像二选一失败（又选回错的）、
   V2 开放复核唯一有效；模型升 7B 后花器在闭合问题就如实答 cylinder，误放 1→0、
   正放 11/11。7B 设为默认，3B 可配置回退。
② License：池内 unknown 归零（301_cup 重导入回归修复+结构性保全）、SPDX 白名单
   自动 declared、SCEA(Duck) 记档待签、证据快照+decisions.json 落 _source。
③ 场景级演练：crate(NVIDIA) 与 duck(GitHub) 全通；发现两类真集成问题——
   仓库尺寸资产放不进桌面工作区（size_policy 解）、reorient+coacd 旋钮交互穿地
   （记为已知边界，beaker 保留为诚实物理拒绝案例）。

### 23:5x SGT — 生产首跑（owner 授权）完成
315_crate + 316_duck 入生产池，s9 重建后 catalog 143 条（外部 18/可用 17），
license unknown=0，audit 17/17 clean。场景演练 crate/duck 全通，红杯回归无影响。
两处小事故均已处置：acquire 尾段元组调用错误（崩在 s9 前，数据无损，已修+补跑）；
s9 相对路径被内部错误拼接（用绝对路径通过，根因待查——低优先，沙箱与生产惯例均为绝对路径）。

### 08-12 00:3x SGT — 放量验证至 2k+ 量级（owner 指令）
语料 1,232 → **3,571 张可搜图**（+DigitalTwin 仓储 2,205 USD、+IsaacLab 1,139 USD；
索引 28,927 条目，151MB 缩略图，镜像 127s 硬链接复用 0 失败）。

**性能（复杂度结论三度验证）**：词法 0.029ms（465 时 0.014 / 1232 时 0.025）、
视觉 3.87ms（3.72）——查询代价与语料规模脱钩；CLIP 索引一次性 25s。

**闸门（7B+开放复核，20 题重标注）**：误放 0/5、正放 15/15 满分。
★ 放量价值直接兑现：昨天两个「如实说没有」（trash bin / teddy bear）今天在
新语料里找到真货（OfficeTrashCan_A02 / teddy_bear.usd）并通过闸门。
纵深冗余也起效：fire extinguisher 首选被 7B 误读为 portable device，
第 2/3 候选补上正确接受。

**发现一个评测方法论问题（如实记录）**：封闭 gold 的 30 题指标在放量后失真
（表面 93.3→50.0），抽查证实 top-1 多为新语料的真同类（真纸箱/真碗），
旧名单不认识它们而已。放量后的质量评测需开放世界标注——记为后续工作；
运行层面的正确性以闸门验证后接受为准（satisfies 15/15+0）。
产物：results/20260812_scale2k/report.json；索引/缩略图在 data/asset_index/*_2k*。
