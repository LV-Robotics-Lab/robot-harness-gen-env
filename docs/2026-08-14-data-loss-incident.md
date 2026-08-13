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

## 第二次删除（03:2x）与真实范围

首次恢复完成约一小时后，`~/yuxin` **再次被清空**，且这次查清了真实范围：

- `/home/jingxiang/` 下的 **每个人的目录**（`yuxin`、`gujie`、`bingsheng`、`yeyuxuan`）全部消失；
- `workspace/` 里除 `robot-harness-gen-env`、`lerobot` 外的项目（openxsim-* 等）也不见了；
- 全盘 **已无任何 RoboTwin 副本**（`gujie/RoboTwin` 87G、`bingsheng/RoboTwin` 均已删除）；
- 磁盘占用 967G → 440G。

排查结论：**不是定时任务**（crontab 只有一条 @reboot cloudcli；无 systemd timer、无 /etc/cron.d
条目提及本目录），cloudcli 代理进程自 17:05 起已崩溃退出。因此是交互式的、由人执行的
批量清理（磁盘回收），与我方作业无关——我方脚本的删除动作全部限定在自己的 run 目录内。

## 我为什么停止重建

第一次删除后我已完整恢复（代码、上游、RoboTwin 自有副本 16G、curobo 重编译、
原点校准 441/534、顶面探针、目录 93 个可用类目），一小时内被二次清空。继续做的话：

1. RoboTwin 资产本机已不存在，必须从上游重新下载十几 GB；
2. 而对方正在回收磁盘空间，此刻灌入几十 GB 既可能被再次删除，也会妨碍对方；
3. 真正有价值的东西（代码 + 实测元数据的产出方法）已经全部在 GitHub 上。

因此改为：保住远端、写清损失与恢复步骤、把决定权交回给用户。

## 复机清单（决定迁移路径后按序执行）

1. `git clone git@github-envgendev:huyuxinn/env-gen-dev.git`（main=ad28866）。
2. 上游 env-gen：clone `robot-harness-gen-env`，建 `external/env-gen-github` 软链。
3. RoboTwin：从上游仓库 clone + 跑其 `assets/_download.py` 取回 4.4G 资产（本机已无副本）。
4. curobo：`CUDA_HOME=<带 nvcc 的 conda env> TORCH_CUDA_ARCH_LIST=9.0+PTX
   SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0 pip install -e <robotwin>/envs/curobo --no-build-isolation`
   （sm_120 上必须配合运行时 `CUDA_LAUNCH_BLOCKING=1`，已写进调用点）。
5. 实测链（均已支持分批续测）：`calibrate_native_origins.py` → `probe_top_support.py`
   → `measure_asset_attributes.py`，约 1–1.5 小时 GPU，产物直接 `git commit`（.gitignore
   例外已就位）。
6. 检索索引：`work/oneoff/build_index_50k.py`（NVIDIA 5 万缩略图+CLIP）与
   `build_objaverse_name_index.py`（79.9 万名索引，需 160 个 HuggingFace 分片）。
7. 资产库：17 份账本在 git 内可直接重物化；317–361 共约 44 个当日新资产的账本一并丢失，
   只能按原 prompt 重新采购（每个 2–6 分钟）。

## 给用户的建议（需拍板）

1. **换机器或换路径**：这台机器 24 小时内发生两起大规模删除，且删的是所有人的个人目录。
   建议把工作树迁到不会被批量清理的位置，或直接换一台。
2. **离机备份**：账本 + 四份实测元数据体积很小（几百 KB），现在已经进 git；但资产二进制
   （数 GB）仍无备份，可考虑 rclone 定期推到 Google Drive。
3. **44 个新资产是否重采**：需要 GPU 数小时，且依赖检索索引先重建完成。
