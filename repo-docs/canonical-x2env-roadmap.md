# Canonical x2env 后续开发方向与计划

本页是 matrix v2 推送后的开发建议，不把排期写成已完成能力，也不重新启动已结束的两小时验收窗口。基线是[四例结果](../self_improving/golden_e2e_progress/qualification-matrix-v2-results.json)和[最终审计](../self_improving/golden_e2e_progress/CANONICAL_MATRIX_V2_PUSH_AUDIT_20260913.md)。本轮文档更新不执行下列功能项目。

## P0：把现有实验入口变成容易部署和诊断的服务组件

先减少首次安装和运行的人工拼接。现有本机入口绑定私有模型路由、固定源码及运行环境，其他机器还需显式配置。优先做公开的配置检查命令、资源缺失清单、可复制的依赖说明和最小部署示例。检查只读取版本、pin 和可用性，不打印密钥，不自动升级共享 runtime。

然后测量真实请求的 interpret、资产预览、ground、replay、observe、validate、package wall。保留模型 max 的已授权设置，优先减少重复读取和预览，单独比较模型长尾与物理运行耗时。每次核验总预算建议沿用 1170 秒运行加 30 秒收尾；超时保留产物，只有实质改动、性能优化或新授权才再跑。

验收：在新环境从文档安装，配置缺项能定位到具体字段；现有四模态样例分别运行并得到可追溯结果；每阶段有耗时、失败码和路径。若开发 CLI 增加新子命令，先定 API 并更新帮助/测试/文档，不能只在说明中虚构命令。

## P1：接通 web 与真实新资产重建

资产搜索继续复用 Yuxin engine，围绕现有 `WebConfig` 接入固定 provider config、许可记录及所需索引。先在隔离目录完成一次真实下载、规范化、Registry 登记、Genesis replay/validate；记录作者、许可、来源 URL、原始 bytes 和新版本。许可或服务不可用保留受阻，不绕过来源检查。

重建继续从固定 Gujie/替代后端的 adapter seam 接入。先确认外部 API、source commit、解释器/配置/模型 pin、授权和资源可用，再让同一 Harness 执行新几何获取。必须证明新 digest 在运行前不在库中，完成规范化和场景运行；旧 mesh 复制和简单形状代替不能计作重建成功。

验收：local/web/reconstruction 各一个独立真实成功；至少一次来源耗尽后转下一来源成功；每条来源都有完整失败日志。此阶段完成前，不恢复旧 v1 全来源成功的宣传。

## P2：多物体、动态支撑和关节的实际能力

优先从两物体 `can on plate` 开始，然后 basket 内放 can、重复类别多物体、最后 articulation 与多点支撑。已存在的[增量支撑契约](../self_improving/golden_e2e_progress/CANONICAL_MULTI_ENTITY_SUPPORT_SPEC.md)和 `measured_support.py` 是起点；组件存在不意味着物理已经通过。

每个切片贯穿 SceneIR、资产拓扑、compile、runtime trace、contact pair、评估器、包内 loader。用真实内腔及带孔面域检查完整 footprint，防止凸包把容器填满或 source 穿过 plate 后接触桌面。颜色、尺寸和对象身份必须一一绑定，避免两个 can 合成一个。

“打开柜子＋粉红鼠标”用于检验对象关系和关节表达；不建立该 prompt 的专用生成分支。成功必须有真实 joint、limits、初始开角、支撑链与物理报告；失败按具体阶段保留。后续是否恢复 4+4+4、≥8/12，由新矩阵及用户批准决定，不回填旧 v1。

验收：每种新关系至少一例完整运行及一例误报攻击测试；真实复制包运行；阈值先固定再跑。对共享 stable support/loader/validator 合同的改动仍遵守仓库要求的 RoboTwin/SAPIEN 回放。

## P3：完整修复与恢复闭环

现阶段真实颜色 child 有 S01/S04 证据，布局修复和多种故障恢复主要由公共 seam 测试证明。后续选择一个自然发生的布局失败，让模型根据新鲜观测与物理诊断提出 ScenePatch，controller 校验并重编译；再覆盖一个允许的资产参数修订。前后场景/资产 bytes 和物理结果均需变化。

完善 dead-owner 恢复、修订后未提交结果的审计、clarification 的公开提交方式与可读错误。现有 `resume` 不接受 clarification 参数；实现前先补合同，不能把计划中的参数写进当前用户命令。

验收：单 workflow 修订预算不返还；重复错误停止；parent asset 不变；恢复不重跑已提交 operation；每条真实 failure/recovery 都有时间、owner 和产物证据。

## P4：包分发、质量和持续集成

完善新的安装环境下 copy/load/reset/step，区分 initial reset 与 step 后恢复；增加一个多对象包和一个重建包的隔离复制。包 runtime 只要求实际执行所需依赖，重建来源身份作为 build provenance 留存。

修复发布器 draft-by-tag 404 的已知问题，验证按 release ID 幂等补齐、无覆盖上传、逐件下载核 hash；已存在证据保持原义。为失败包和成功包提供可检索的 release/workflow 索引。

测试优先补当前 coverage 报告里的高风险控制分支与坏数据攻击，不盲目追求百分比。所有 active 测试、lint、schema 和测量完整性继续为门；只有真实外部资源准备好后才引入受控 runtime CI。

## P5：机器人策略、采集与呈现层

环境生成稳定后，再定义机器人加载、reset、action、observation、termination 和数据集格式，完成非零动作及轨迹验证。策略成功率、机器人碰撞、任务奖励和数据采集质量需独立报告，不能从环境 `succeeded` 推导。

之后按实际用户需求增加 REST/API 服务和前端；所有入口复用同一 Harness。MCP 仅在有外部 consumer 时作为薄协议适配，不能重新建立外部 agent 掌控业务流程。digital cousin 先验证匹配和物理可用性，再考虑扩大默认路由。

## 执行与提交方法

每个目标采用“合同和可复现失败 → 最小 TDD 纵切 → 定向检查 → 小型真实运行 → provenance/指南/结果 → 独立提交”。并行工作按文件所有权分配，主线程审阅 API 和验收。进度写入 [TODO](../self_improving/golden_e2e_progress/TODO.md) 和 [RESULTS](../self_improving/golden_e2e_progress/RESULTS.md)，大产物保存到明确路径并绑定 hash。

下一轮建议顺序为 P0 的部署检查和测量、P1 的 web、P1 的重建，然后 P2 的 can-on-plate；P3/P4 跟随实际失败补齐。资源阻断先给出所需环境和已完成产物，避免盲目模型重试。

`worktree/bingsheng` 文档或功能 push 与 main PR 分开。本轮只推送用户要求的文档；main PR 仍待单独批准。
