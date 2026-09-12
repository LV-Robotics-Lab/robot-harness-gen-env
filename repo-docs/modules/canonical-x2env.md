# Canonical x2env（收敛实施中）

新的通用入口正在 `self_improving/harness/x2env/` 实现，不能把旧实验或固定preview的运行证据
视为这份代码已通过验收。目前是输入、SceneIR、ArtifactRef/ToolResult和类型化能力注册的组件实现；
尚无可调用的完整canonical运行时。

已接通的开发slice是`Harness.submit`持久唯一handle，`resume`先处理原始媒体，再由受管理Codex
给出SceneIR建议；controller决定接受或因关键未知项blocked。`status`从同一SQLite恢复，部分输入
资产和模型日志保留在CAS。缺后续资产解析器时明确blocked；当前`package`尚不提供环境包。
71项小测试通过不等于最终覆盖率门或真实模型/Genesis资格。生产部署、完整恢复与全部后续stage仍在实施。

`contracts.py`拒绝第九实体、重复ID、未知关系、缺字段来源、悬空或循环支撑引用。
意图中的位置和尺寸允许逐轴null，保留用户已知轴而不编造未知厚度/高度；编译前仍必须解析。
模型不合法结果保留有界validation-errors报告及原proposal，不人工修改结果或授予成功。
输入支持文本、图像、视频组合的类型表示，但这不代表媒体理解已接通。
`CapabilityRegistry`仅允许三个公开Skills与三个初始内部Tools，精确版本和输入输出类型必须匹配；
注册一个handler不等于该handler取得仿真资格。

Yuxin检索实现现位于`self_improving/asset_pipeline/active/asset_reuse/lib/`（一次目录搬迁），
canonical调用将复用这一份代码。搬迁仅完成可导入和消费者路径兼容，不代表许可强门、
资产规范化、immutable登记或完整本地/联网业务路径已接通。

现在canonical adapter已经调用原分层检索与下载，实际web搜索/获取Box有独立证据。
`normalization.py`显式烘焙节点变换、Y→Z与目标尺寸，产出单刚体四成员闭包；未知尺度、
动画/关节或不支持材质拒绝，凸包不能代表容器内腔。`assets.py`在原Store中登记不可变版本，
许可、来源、规范化、geometry摘要和父版本分别绑定；静态登记不是sim-ready。
这些组件尚未全部进入同一workflow。真实Codex解释已通过，后续资产解析缺失仍明确blocked。

Python模型是唯一schema编辑源；`script/export_x2env_schemas.py --check`检测生成文件漂移。
Codex输出另生成strict projection：可空字段仍必须出现；provenance是固定六字段对象，joint positions
使用name/value记录数组。此约束来自真实`invalid_json_schema`失败，非场景生成失败。
参见[官方严格结构化输出要求](https://developers.openai.com/api/docs/guides/structured-outputs)。
后续stage类型与descriptor会随真实执行接口补齐，不用空成功回执替代实现。

验收与当前进度见[固定计划](../../self_improving/golden_e2e_progress/CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md)
和[TODO](../../self_improving/golden_e2e_progress/TODO.md)。旧System 2/MCP/Qwen页面属于待分类历史，
不代表新的默认控制关系；新关系由Harness控制，Codex仅是受管理的理解与诊断后端。
