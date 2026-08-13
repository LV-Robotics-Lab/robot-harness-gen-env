# 2026-08-14 数据事故与恢复记录

## 发生了什么

02:14 发现 `lv-5090:/home/jingxiang/yuxin/` **被整体清空**：磁盘占用从 967G 降至 437G
（约 530GB 消失），目录时间戳 01:56，机器未重启（uptime 连续）。同一台机器上
`/home/jingxiang/workspace/alchedata-self-improving-agents`（同事的 workspace）已在
当日中午被删除过一次，本次是第二起。删除动作不是我方发起：我方当时只在跑读多写少的
测量与矩阵任务，且从未对 `~/yuxin` 之外执行过删除。

## 损失与幸存

| 内容 | 状态 |
|---|---|
| 全部代码（含当日 6 个提交） | **完好** — GitHub `huyuxinn/env-gen-dev` main=ecea99f、她的分支=ec477bc |
| 17 份资产账本（301–316） | **完好** — 在 git 内（"账本即契约"设计生效） |
| 14 份上游账本 | 完好 |
| 资产库二进制（约 60 个资产的 GLB/USD） | 丢失 |
| 317–361 共约 44 个当日新采购资产（含账本） | 丢失（尚未提交） |
| 实测元数据（原点校准 430 条、顶面/内腔探针、颜色属性 508 模型、目录） | 丢失（`data/` 全域被 gitignore） |
| 检索索引（NVIDIA 5 万缩略图+CLIP、Objaverse 79.9 万名索引） | 丢失（可脚本重建，需数小时+网络） |
| RoboTwin 本地资产 / 自有 16G 副本 | 丢失，但可从 `gujie/RoboTwin` 重新自有化 |
| 当日 sweep 报告与证据（277 行）、属性矩阵中间结果 | 丢失 |
| 她 checkout 里未提交的池状态 | 丢失 |

## 已完成的恢复

1. 代码：从 GitHub 重新 clone 到 `~/yuxin/env-gen-dev`（main=ecea99f）。
2. 上游 env-gen：从 `workspace/robot-harness-gen-env` clone 到 `~/yuxin/env-gen-github`，
   并重建 `external/env-gen-github` 软链。
3. RoboTwin：从 `gujie/RoboTwin` 重新自有化到 `~/yuxin/robotwin_upstream`（16G，
   排除 data/XPolicyLab/eval_result）。
4. curobo：按 sm_120 兼容配置（CUDA_HOME + PTX 目标）重新编译安装。
5. shadow root 重建通过（142 条目）。
6. 实测链重跑中：校准 → 探针 → 颜色属性，均已改为**分批可续测**。

## 加固（防止同样的东西再丢一次）

- `.gitignore` 增加例外：四份实测元数据 JSON（校准/探针/颜色/运行时撤销）纳入版本控制。
  它们各只有几百 KB，却分别是数小时 GPU 实测的产物——代码因为在远端而毫发无伤，
  测量却因为只躺在 `data/` 而全灭，这个不对称必须消除。
- 新采购资产的账本应在采购当批就提交，而不是攒到复盘时（本次 44 个资产的账本全部
  随目录消失，等于连"它们从哪来"都无据可查）。

## 待用户拍板

- 共享主目录 `/home/jingxiang/` 显然不安全（两天内两起整体删除）。是否把工作树迁到
  独立路径（如 `/data/yuxin/` 或自建卷），并配一条离机备份（rclone → Google Drive，
  只传账本+实测元数据这类小文件）。
- 丢失的 44 个新资产是否需要按当时的 prompt 逐个重新采购（每个 2–6 分钟 GPU）。
