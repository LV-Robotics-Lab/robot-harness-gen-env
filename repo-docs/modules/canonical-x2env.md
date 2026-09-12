# Canonical x2env（收敛实施中）

视频输入优先读取视频流的时长，仅在流缺少该字段时读取容器时长；不会因为容器缺少重复字段而
拒绝合法视频。输入限制或解码超时仍保留已产生证据并返回结构化失败。输入与证据闭包等四模块
已完成定向100%语句/分支覆盖，但完整workflow、真实矩阵和全仓覆盖仍须分别验收。

新的通用入口正在 `self_improving/harness/x2env/` 实现，不能把旧实验或固定preview的运行证据
视为这份代码已通过验收。目前是输入、SceneIR、ArtifactRef/ToolResult和类型化能力注册的组件实现；
尚无可调用的完整canonical运行时。

已接通的开发slice是`Harness.submit`持久唯一handle，`resume`先处理原始媒体，再由受管理Codex
给出SceneIR建议；controller决定接受或因关键未知项blocked。`status`从同一SQLite恢复，部分输入
资产和模型日志保留在CAS。缺后续资产解析器时明确blocked；当前`package`尚不提供环境包。
推进要求活进程身份、完整快照和当前running操作一致；产物先核CAS再推进。旧owner退出但
对应模型子进程仍存活或身份不明时明确blocked，不能通过重复resume启动第二份模型。
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
资产视觉seam使用`CodexBackend.assess_asset_candidates`注入原`a6_verify.verify_candidate`，
复用原开放/闭合身份问题和属性门；默认Qwen不被调用。判断绑定候选预览CAS，仍只属视觉建议。
`web_resolver.py`通过原provider获取，再消费受管理Codex的有界规范化参数；已知尺寸不可改写，
未知质量/摩擦/尺寸明确为模型估计。原web检索下载已有真实证据，完整web→视觉→编译链仍待运行。
`SourceRouter`按固定来源顺序合并partial，只把未解决实体交给下一来源；`allowed_sources`不能重排
许可/选择优先级。重建resolver消费实际提案、原adapter和部署派生授权再入库，缺图/许可不伪造成功。
Gujie重建通过`adapters/reconstruction.py`调用固定外部SAM2与TRELLIS接口，原Hunyuan入口非默认保留。
原链已生成真实新geometry，但canonical adapter尚未完成GPU/完整E2E验证；输入派生授权与软件/
模型许可分离，缺授权不能入库为许可明确的新资产。
`reconstruction_planning.py`把真实源图和SceneIR交给同一模型transport产生SAM2像素框/点；
缺源图不能用primitive代替，估计尺寸/物性与输入显式字段分别记账，未来mesh轴向由实际输出格式决定。
`genesis_runtime.py`接收已解析世界位姿、相对URDF/物性路径及完整成员摘要，子进程仅能读包与
声明运行环境。新内核已有一次2刚体load/step/media冒烟；固定双dt物理profile仍待评估，不代表
整条canonical或可移植包已通过。后续seed/实际顶点/净接触力增量与旧smoke证据分开记录。
`compile.py`把共享SceneIR和已登记依赖转换为上述运行输入；foreground位置指几何中心，
结构支撑坐标指上表面中心，编译时转为实际URDF底部origin。显式结构policy补齐的未知项
逐项记录；已知尺寸冲突、未实现inside/关节拒绝，不用少实体场景代替请求。
`asset_revision.py`只创建不可变子版本，目前支持base_color、mass、friction；无实际变化拒绝。
AssetVersion v2用世界顶点/面摘要区分几何与属性变化，换色不能被计作重建新geometry。
审批来源仍由controller保证；该组件不自行授予重试预算或物理通过。
`AssetPreviewRenderer`从版本内的真实资产重新渲染；local resolver保留版本/运行/图片完整回执，
拒绝裸图或来源缺链。资产预览是身份与属性视觉判断输入，不等于用户完整场景或物理通过。
controller现已将local resolver、compile、双profile replay、observe、diagnosis和validate接到同一workflow journal，部署缺少
执行器时blocked；物理缺证不被视觉通过覆盖，修订/最终包消费仍待接，不生成名为成功的用户包。
部分运行错误日志进入CAS；集成测试的模型/运行时是明确替身，真实整链仍待运行。
`assessment.py`核两个独立profile的进程、实际加载几何/物性、连续轨迹和媒体后重算冻结断言。
纯轨迹分析与执行证据分开；26项/动态刚体是当前限定profile，不是旧mouse 34项或机器人策略验证。
`CodexBackend.assess_and_diagnose`消费实际相机时间与绑定replay，模型准入TTL为300秒；回执时间
不能使旧图变新。输出仅为视觉意图判断和版本绑定的有限修订建议，预算和执行仍归controller。
`revision.py`统一场景/资产最多2次预算；位置/朝向真的改变bytes，资产生成新child版本。
成功revision由journal原子清空旧下游指针，再进入compile/replay，旧证据保留不可改；真实fallback尚待运行。
跨Store复制一个receipt不代表来源完整；`artifacts.artifact_closure`按显式ArtifactRef递归核验JSON
证据图，Registry与包导出共用。真实preview曾因此暴露缺失来源记录，原运行/视觉结果保留，
但不能作为来源完整或成功包资格使用；新导入必须复制全部必要证据。
`package.py`导出完整typed证据及父资产版本闭包、相对资产和包自有loader，外部运行环境单独声明。
`package_loader.py`与在线runtime共用进程启动器。已有独立复制加载/步进/新媒体冒烟，但初态
嵌入且未执行固定双dt物理门，因此review包一律sim_ready=false；最终workflow签发与三次资格仍待完成。

Python模型是唯一schema编辑源；`script/export_x2env_schemas.py --check`检测生成文件漂移。
Codex输出另生成strict projection：可空字段仍必须出现；provenance是固定六字段对象，joint positions
使用name/value记录数组。此约束来自真实`invalid_json_schema`失败，非场景生成失败。
参见[官方严格结构化输出要求](https://developers.openai.com/api/docs/guides/structured-outputs)。
后续stage类型与descriptor会随真实执行接口补齐，不用空成功回执替代实现。

验收与当前进度见[固定计划](../../self_improving/golden_e2e_progress/CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md)
和[TODO](../../self_improving/golden_e2e_progress/TODO.md)。旧System 2/MCP/Qwen页面属于待分类历史，
不代表新的默认控制关系；新关系由Harness控制，Codex仅是受管理的理解与诊断后端。
