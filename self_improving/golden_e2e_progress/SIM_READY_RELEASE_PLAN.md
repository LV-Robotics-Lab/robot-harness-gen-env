# Sim-ready 服务交付计划

状态：用户已批准，开始M1；policy后置，M5开放服务后暂停。

## Gujie整合主线

本计划明确以[Gujie接入审计](../../docs/research/gujie-x2env-integration-audit-20260911.md)为参考。
该报告固定于旧候选`7e145274`，其中“尚无runtime lock/资产资格”等历史状态已被后续证据更新；
其固定来源、能力拆分与尚未接入的scene/import/position/dual-dt等清单仍指导本次整合。审计当时的
policy-ready/最终Golden要求不作为新阶段的前置：本阶段环境sim-ready不要求机器人policy接口。

- M1实际适配固定`eb0b710`中的URDF/scene import、有限布局、支撑图、baseline/half-dt与物理判据；
  保留Gujie实现归属及Genesis/SimFoundry各自上游归属，不只将其当作背景资料。
- M2把其阶段化执行、诊断与最终媒体行为接到Harness同workflow回执，原checkpoint/pass不直接迁移。
- M3接入重建结果与资产生成路径，保留SimFoundry为独立上游；Harness管理的CodexBackend继续承担
  规划/视觉决策。
  video补足完整帧identity与真实执行，不以15帧sample或旧成功记录替代Golden line。
- 每项整合记录固定源码、采用/适配/补齐的具体内容、当前运行证据和限制。优先复用可分离的确定性
  实现；不复制整个vendor，不放宽Harness证据门，不改动同事dirty工作区。

## 本阶段终点

用户从Harness API/CLI提交text/image/video或组合prompt。Harness controller拥有workflow生命周期，
内部调用受管理CodexBackend，并通过分配的exact qualified MCP执行Skill；输出可在固定Genesis中原生
加载、真实步进、通过环境级验证并可搬迁重放的场景包、资产、图片/连续视频及完整回执。验证通过后
由Harness原子入库/发布。完成可访问服务与文档交付，然后暂停。

本阶段不要求机器人reset/action/termination/trajectory或policy成功率，不实现P8。旧E3/E4定义保持
历史含义，不把缺少policy的发布称为旧E4。第一阶段验收改为E2环境门+完整Codex闭环+环境发布/服务
门；批准后正式版本化更新验收、validate/promotion适用profile及benchmark合同，不改写旧v1语义。
默认首发环境profile以`genesis.rigid_scene@1`为起点，支持范围明确声明，超范围请求明确失败。

## M1 — 第一条完整Genesis环境线（P7/P9与P5环境验证部分）

- 复用已完成的单资产资格，把image exact-reuse candidate接成生产compile application与execution
  binding，冻结Skill descriptor/资格；实现完整Scene/Task IR、布局和资产闭包的Genesis环境物化。
- 场景级真实replay，检查碰撞、支撑/包含（请求涉及时）、穿透、稳定性及baseline/half-dt一致性。
- 输出新鲜观测、图片和实际连续视频；环境级validate明确可发布profile和限制。
- 验收：冻结多个图像输入与seed，在新CAS和新绝对目录中真实compile→replay→observe→validate，
  保留失败与媒体；重启/幂等/完整证据关联通过。这一步不等待policy。

## M2 — Codex同workflow闭环（P4/P5/P10/P11）

- 将M1的production compile/replay/observe/validate接入冻结Registry的exact MCP目录，无generic invoke。
- Harness从当前持久状态自动调用真实CodexBackend；Codex在一个workflow中读取输入、选择建议动作、
  读取新鲜观测、诊断、修改prompt，并在允许时显式fallback。Harness校验后执行重编译等exact Skill；
  失败predecessor、receipt和StateDelta完整关联。
- 补齐环境级promotion与发布receipt；本阶段先发布至独立受控库，后续M4完成共享入库全路径。
- 验收：正常case和至少一个诊断修复case都从Harness用户入口真实闭环，Codex调用记录可与Harness逐项
  对账；stdio/client/resource与恢复通过。用户无需操作Codex CLI或MCP配置；Codex自报成功不能代替
  环境验证。

## M3 — 三模态×三资产路径（P9/P10/P11）

- 依次补text与video的生产路径，三组compile/replay/validate共九个Skill复用共享环境运行实现。
- text/image/video各完成exact reuse、digital cousin、fresh generation；同时覆盖environment复用。
- 生成器真实产物须经过staging/几何和物理资格；cousin明确差异及任务等价条件；video完整消费
  序列，关键帧/回退选择有回执，不能仅用首帧冒充视频输入。
- 验收：九个组合各有独立Golden line，冻结多输入及记录seed的采样；另覆盖组合prompt、冲突输入、
  prompt修复与VLM fallback，输出环境包与可观看媒体，不以旧成功缓存替代新执行。

## M4 — 入库与历史债务（P6）

- 完成新资产/复用资产/cousin/环境包的持久入库、检索、重复使用、原子发布与中断恢复。
- 对162份历史ledger及已知4,082条债务逐项形成repair/rebuild/retire或精确外部阻断的终态账目；
  可恢复者真实重验，不能恢复者明确退出可用目录，不能只修少量样本就称历史清理完成。
- 验收：新workflow可消费已发布结果，CAS/ledger/catalog一致；无半发布、无未资格资产泄漏到可用库。
  第一阶段案例所需资产必须完整可用，不能以历史样本替代。

## M5 — 系统性验收、整理、开放服务并暂停（P12/P13/P14）

- 批准后将benchmark终点改为sim-ready环境发布，保留36路由cases、12真实cases、最多30次实验或
  12小时预算、dev/held-out隔离和原有成功率门；不因policy后置放宽环境/证据正确性。
- 指标保留route≥0.95、闭环≥0.90、fallback≥0.80及逐模态门；确定性Golden lines和成功case
  闭包验证100%，错误发布/证据错绑/未记录失败为0。完整保存失败与修复，不挑选成功样本。
- 在固定干净提交完成根测试、schema/格式、真实运行和迁移/恢复；新core覆盖目标保持，不以
  反复局部审计代替功能进展，剩余防御/环境分支逐条说明且不得宣称原始100%。
- 交付最小可用工作台/服务：提交多模态输入、查看状态/失败原因、观看媒体、取得环境包与证据。
  对外入口只暴露同一Harness权威；exact MCP是内部协议适配，用户无需直接操作。默认按项目组可访问的
  受控服务部署，不自动公开互联网。
- 固定release源码、依赖、启动/停止/恢复说明、服务地址和健康检查；完成从服务入口提交到取得
  sim-ready产物的真实冒烟。部署地址或访问范围若现有配置无法确定，在部署准备完成后单独确认。
- 整理本任务分支/工作树和临时产物归属，不reset/覆盖/顺手提交其他用户改动，不删除原始失败证据；
  形成可复现的干净发布工作树，提交小摘要，bulk CAS/资产留在独立数据目录。
- walkthrough逐步关联spec、主要代码、测试、运行回执与媒体；根AGENTS.md链接walkthrough/progress。
  服务开放、资料交付后停止开发并暂停，等待其他项目组联调。

## M6 — 后续跨项目机器人联调（P8/E3及原E4，暂不执行）

与策略项目组共同确定机器人、控制频率、action/observation/termination及数据合同，再实现真实
机器人probe和policy联调；不得提前宣称policy-ready。此阶段须用户重新启动。

## 执行纪律

- 主窗口负责目标、接口决策、进度和宏观验收；subagents负责实现、独立TDD、真实运行，独占文件。
- 沿已批准S1–S6公共seam按功能纵切RED→GREEN，逐功能提交；新版本合同显式迁移consumer。
- 完成一个上述较大功能目标后，再安排2–3个agent集中验收真实功能/全链路/产物与失败；
  不在小提交后反复做代码风格审查，不重跑已充分验证且未变化的全套。
- 验收关注输入是否真正被消费、是否真实调用Genesis/Codex、环境和媒体是否可用、CAS/receipt/state
  是否闭合；任何失败都保留。进度目录与provenance/repo-docs随功能同步。
- 不启动P8，不因底层已有资格就扩大权限；没有依赖关系的收口可并行，不改变M1→M5的主线顺序。
