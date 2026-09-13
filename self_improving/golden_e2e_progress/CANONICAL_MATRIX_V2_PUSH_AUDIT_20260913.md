# Canonical x2env matrix v2：实施 walkthrough 与 push 审计

状态：四例及一本复制运行门已通过，进入最终冻结测试/Git审计。计时从2026-09-13 06:25:09 UTC开始，08:25:09 UTC为硬截止。
当前收口对象是 Harness 编排与可用入口，不是旧matrix v1资格、通用重建算法或机器人策略平台。
最终`可 push / 不可 push`及实际远端结果由本次外部不可变记录给出：
`/home/jingxiang/bingsheng/canonical-matrix-v2-push-audit-20260913.cKTw0K/push-audit.json`。
本页记录运行事实，不用尚未完成的最后测试预授push；冻结后不为填结果改源码身份。

## 验收权威

- [matrix v2](qualification-matrix-v2.json)、[最新计划](CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md)、
  [Q65及历史决策](CANONICAL_X2ENV_MERGE_DECISIONS.md)、[验收门](ACCEPTANCE.md)。
- v1固定SHA `6272ca667c7df611d4ce68c3aa8fef92bdc9ab1d27ecea68deed6cee3130384d`保留，失败不回填。
- 四简单例全部成功只是进入收尾；至少一本次成功包copy-run、有效fallback/恢复/幂等/退出测试、
  所有active测试、可执行入口文档、Git/归属/用户改动保护仍须审计。普通push后停止，main PR未授权。

## 逐功能实施与证据

1. `306375c` 固定本轮范围与原媒体/seed；`6e46229`按新授权把覆盖百分比改为实测报告。
   [测试门](../../script/x2env_test_groups.py)仍拒绝测试/lint失败、缺失测量、源码漂移，原三业务排除不扩大。
2. `e6570f3` 在同一受管理[模型传输](../harness/x2env/codex.py)和[部署组装](../harness/x2env/deployment.py)
   中接固定Terra路由，max保持。密钥仅私有文件进入子进程环境，未进入Git/argv/回执。
   真实协议1.29s通过；首次模型输出75.95s因55项合同错误拒绝；将同一schema提供给模型后44.72s通过。
3. `e65b789` 修复颜色child审核的精确schema后缀兼容，旧问句/模型/图片绑定保持；修改问句和附加指令攻击仍拒绝。
   [审核实现](../harness/x2env/local_color_execution.py)，81项相邻测试通过。原S01失败保留。
4. `1228f96` 让准确原entity ID字段进入统一设计分类，解决正常缺尺寸/位置被误报澄清。
   [设计合同与分类](../harness/x2env/design_plan.py)，97项测试通过；已知值/冲突/非法轴仍拒绝。
5. `e755421` 把显式结构表面色从SceneIR送到Genesis：
   [compile](../harness/x2env/compile.py)、[runtime契约](../harness/x2env/genesis_runtime.py)、
   [真实child](../harness/x2env/genesis_child.py)。颜色仅结构Box可用，CSS/hex转换为有限RGBA；
   无色保原序列化，未知显式色拒绝，刚体仍由资产材质负责。74项测试通过，尺寸/位姿/物理阈值未变。
6. `d4731c0` / `da78d40` 保留认证资源、失败阶段和可读说明，部分产物/原始错误不被最后缺adapter掩盖；
   65项生命周期邻组、30项failure/CLI测试通过。失败现场仍须阅读各来源receipt以追踪根因。
7. `e754ff9` 独立核上下文内容后绑定真实CAS原字节，消除JSON键序变化导致的完成门误拒；
   完成门114项通过，v3首次漏声明Shapely环境的9项失败补环境后9项通过；设计路径98项通过。
8. `d9898f7` 只在失败导出保留不可解析的JSON叶，明确reference_graph_complete=false；正常完成门仍strict。
   23项测试通过；S04历史坏JSON的独立新导出逐bytes通过，原父failed不变。
9. `9d5909b` interpret仅JSON语法错误最多一次真实模型重生成，原输入/同schema/同deadline/max不变。
   不对schema/来源/认证/超时重试，不手补JSON；78项相邻测试通过，替身与真实触发次数分列。

以上变更均使用测试先复现再修复。测试中的模型/渲染替身不计真实案例；具体日志与来源见
[RESULTS](RESULTS.md)及[归属账](../../docs/integration-provenance/LEDGER.md)。

## 本轮真实案例

### S01：文本，seed 11

输入：`在桌面放一个粉红色鼠标。`，默认资产来源策略。
成功workflow `90f8a08e-e145-44e9-b7f6-b1b325f641ad`，源码`da78d4026084ec8da0f771230a8abf2c82bbff7f`，
339.200397s、revision13、succeeded。真实颜色修订产生不可变child；同一workflow完成compile/replay/observe/validate/出包。
视觉passed、物理passed；包自身仍明确development、sim_ready=false、release_qualified=false。

- 根：`/var/tmp/canonical-matrix-v2-text-multimodal-r2.HKsm7Z/S01`
- 环境：该根`requested-output/environment`；manifest SHA `49918f237e09b6a81fae127d241eb47c82784a5e6ab9bdeec83cfb86f5753338`。
- 汇总/日志：该根`audit-summary.json`、`stdout.log`、`stderr.log`、`requested-output/result.json`。
- 主线程已查看末图：环境内`evidence/826ba5184c958ce94bed14a436fb4516c05c625fe6f98bc82b0cb8cab2a3d65b.png`。
- 首次179.626333s失败仍在`/var/tmp/canonical-matrix-v2-text-multimodal.BXDrkz/S01`，没有改为成功。

### S02：纯图像，seed 23

原`centered-red-cube.png` SHA `a4c62f05001d759a43aa0bf0964bde7e7ee5df08ae546e21d6ba2e182ef27202`，
text=null，默认资产策略。源码`e754ff97d047d4d994b992e3738ccc99dbc0d93f`，
workflow `108e9fbe-8b82-4ad0-80ef-d3e17fc42cd8`，290.985332s，revision10，succeeded。
视觉passed、物理passed；root为`/var/tmp/canonical-matrix-v2-context-fix-20260913.L5g8GB/S02`。
环境包为该root下`requested-output/environment`，manifest SHA
`4ec92c7c828c4c4f78590281445ac6ba4baf1202d85c1240b94ad1310a7bc530`。
视频为包内`evidence/5131407e870ed7e07e0939a341b3b716dc68f243afee4fb151dff1bc08e9afff.mp4`。
日志及逐阶段耗时在该root的`run-summary.json`、`stdout.log`、`stderr.log`；完整绝对路径见下方机读索引。

### S03：纯视频，seed 37

原`centered-red-cube.mp4` SHA `7400aa21cb455c6efb142d96d2f1632d5329cf759aeb01ba498ed39e21dc5df8`，
text=null，输入41帧/33独特帧，目标仅静态环境。源码同S02，默认资产策略，
workflow `6e1d0f79-a688-47d4-8bf2-e64e2271f39c`，312.627s，revision10，succeeded。
视觉passed、物理passed；root为`/var/tmp/canonical-matrix-v2-context-fix-20260913.L5g8GB/S03`。
环境包为该root下`requested-output/environment`，manifest SHA
`68f1b0e7d11cc7665e69dacc007998b594cde8807b94c6b74233ab448c72c267`。
新视频为包内`evidence/62a6228320cb7f243367166e2ea6cd289b11ff334ad8c5796a9715625cd6eb68.mp4`。
日志及逐阶段耗时在该root的`run-summary.json`、`stdout.log`、`stderr.log`。
主线程已查看S02/S03真实最终红方块/棕色桌面图片，不把查看图片当物理验收。

### S04：图像＋文本覆盖，seed 41

固定原图和文本`参考图片，在桌面放一个蓝色方块，颜色以文字要求为准。`。
第四次运行源码`9d5909b1a53732254801ff7ad9a4af678d95aab9`，默认策略，
workflow `04c11992-8fec-4e11-9623-8f6709aedf55`，383.564632s，revision13，succeeded。
真实蓝色资产修订一次、physical26/26、visual passed、validate和package全部完成；
此成功interpret只调用一次，未触发语法重生成，不声称已真实演示该新重试分支。
根`/var/tmp/canonical-matrix-v2-s04-r4.66Jgpe/S04`，环境包为`requested-output/environment`，
manifest SHA `c4a85ff0cd41a3a274cde577a2f3c63a1ddf255f2eca33b704eef22a5603fee9`。
209成员/11,772,987 bytes独立核验通过；主场景视频41帧/30独特帧，两个6帧资产预览另列。
实际包内主视频`evidence/b6d923e8b00a3ac311225220a419d447b6e06937ae47d3ec1b9180a427eee114.mp4`，
末图`evidence/04329bfed61b85242ecb5311f91031207b0f33546d3c108e1ad2e4a50f53205d.png`。
前三次分别369.042957s视觉失败、408.129076s完成门键序误拒、50.793268s模型JSON语法失败。
全部原失败和部分产物保留；最后一次无模型语法修复证据前未原样重跑。

### 不回填的失败与逐成员索引

- S02/S03全部新旧结果、阶段耗时、注册资产、媒体与reports的绝对路径在
  `/var/tmp/canonical-matrix-v2-context-fix-20260913.L5g8GB/final-inventory.json`，SHA
  `26c7bab4506dceb9a97857b1dfd0e352a9497c8db542eaa83280de527a5a9aaa`。
- S02前四次分别50.625s部署拒读路径错误、51.226s字段定位误澄清、317.092s视觉失败、257.521s打包失败。
  S03前两次261.156s视觉失败、311.348s打包失败。没有把任何已失败workflow手动改为成功。
- S04坏JSON独立导出根`/var/tmp/canonical-bad-json-export-check.onwSuZ/export`，0.00445s，
  只证明失败包保存，不能作为该S04成功。原始bad JSON8095 bytes与模型event一致，仍可审查。
- [机读四例结果](qualification-matrix-v2-results.json)固定真实输入/seed/source/workflow/耗时。
  主线程独立只读SQLite状态与公共stdout逐项一致，所有实际包成员bytes/SHA、模型max和原媒体核对通过；
  完整资产、图片、视频、日志和报告绝对路径索引为
  `/home/jingxiang/bingsheng/canonical-matrix-v2-push-audit-20260913.cKTw0K/evidence-index.json`，SHA
  `15f9e7608001195eb68487dfc46416829491b71e535e87b40973aeefe523d87c`。
  该收集器首次把Genesis invocation误当模型而停止，修为明确codex.jsonl transport分类后完成；不改案例。

## 复制包独立运行

S01本次成功包已复制到`/var/tmp/canonical-matrix-v2-copy-2T6YQtGX/package`。
使用声明的外部Genesis/Python环境，在实际Landlock限制下分别执行baseline 1000步、half_dt 2000步；
九处原workspace/state/CAS/资产路径均实际拒读，原manifest/checkout不变，进程退出。

- 总耗时125.649183s；双profile分别61.246362s、62.399475s，物理26/26、初始reset/拓扑通过。
- 报告：`/var/tmp/canonical-matrix-v2-copy-2T6YQtGX/summary.json`及`assessment.json`。
- 新视频：该根`baseline/preview.mp4`、`half_dt/preview.mp4`；各4秒、41帧、2个独特帧、640×480/10fps。
- 新末图：该根`baseline/frames/step-1000.png`；静态场景新执行像素可与原图一致，不以hash变化冒充新执行。
- 独立copy视觉复评not_run；执行证明由新子进程/隔离/轨迹/媒体支持，不改变原包发布字段。
  robot_policy_evaluated=false，data_collection_evaluated=false；post-step reset未验证。

## fallback、恢复与失败边界

真实S01已完成颜色诊断→候选新版本→预览/视觉复验→继续编译，父资产和旧证据未改。
场景布局修订、共同预算/重复指纹停止、原子reservation、取消/超时和幂等由已有有效测试覆盖；
不把替身用例称成新真实场景修复。已提交颜色child可恢复续接；资产mutation owner丢失且无可提交结果
仍须`color_repair_recovery_requires_audit`，失败/取消终态不自动重跑。

公开seam测试范围：repair_budget 5、revision 4、repair_reservations 8、operation_recovery 20、
local_color_harness 6、harness_lifecycle 19项均在同源active组中执行。
恢复测试包含真实普通OS子进程PID/start-ticks/PGID、死owner与PID复用拒绝；模型/渲染仍为测试替身。
本轮真实fallback-B为S01/S04颜色新版本；fallback-A布局真实修订闭环未另运行，既有组件测试通过，
不宣称通用场景修复稳定性。语法一次成功/再次失败停止为外部进程替身验证，真实S04未触发。

## 测试、来源与剩余能力

e6570f3六组及coverage merge通过399.94s，core7522/8179语句、2838/3368分支；缺657行/530边完整报告。
e755421六组/root/merge通过751.47s，root2396 passed/1 skipped。
e754ff9六组/root/merge通过802.83s，root2398 passed/1 skipped；core7557/8213语句、2852/3382分支，
缺656行/530边，三个业务排除和24默认Protocol排除行未扩大。
证据`/var/tmp/canonical-ci-e754ff9-mRyVa7Bd/{summary.json,evidence/coverage-gate.json}`。
后续坏JSON导出及格式重生成不在该固定测量内，已有23/78项定向通过；最终冻结测量另外记录。
local真实来源已用于S01；本轮部署未配置web/reconstruction，provider内部算法/资源覆盖不是本阶段硬门。
Gujie/Yuxin adapter归属见provenance；verified cousin、复杂containment/articulation/动态多点支撑、
robot policy、数据采集仍未授予能力。本轮没有重跑8.5GB闭包或旧资格。

能力状态只按实际范围记录：

- 统一text/image/video/组合入口、local资产解析、真实compile/replay/observe/validate/出包：真实通过（四简单例）。
- S01独立复制load/step/新媒体、S01/S04不可变颜色修订：真实通过（已列限定profile）。
- 故障归因、语法有界重生成、失败包坏JSON保存、已支持恢复：集成接通；测试与真实子集如上分开。
- Yuxin web：受阻（本机实验部署未配置固定provider config及其资源，外部服务可用性未运行）。
- Gujie/替代重建：受阻（本机实验部署未配置固定重建后端与所需模型/runtime；不扩展其算法或质量）。
- verified digital cousin、机器人policy、数据收集：未实现。
- 复杂containment/articulation/多点支撑：合同或设计，未授真实复杂场景能力。
- 实测动态单支撑几何/编译消费：组件实现；未用组件测试升级复杂场景成功。
- 中等/困难矩阵、其他两来源完整case和另外两个copy-run：未实现（本轮未运行）；不将旧v1结果混入4/4。

## 用户入口及最终Git审计

安装、配置、四种输入、查看结果和故障排查见[用户操作指南](../../repo-docs/walkthroughs/canonical-x2env-user-guide.md)。
可复用本机入口为`/home/jingxiang/bingsheng/canonical-x2env-user-entry-20260913.AydTBI/deployment.json`，
同目录`USAGE.md`含实际四种提交、query/package/result命令；`source-current`固定9d5909b，
独立state仅预登记合法mouse/redblock父版本，没有预填SceneIR或复用旧SQLite。
`load_deployment/build_harness/--help/空输入结构拒绝`实测通过；本入口state尚无真实用户案例，
四例证据属于各自隔离部署。模型资源仍为私有网络端点，其他机器须自行配置已声明依赖，非一键云服务。

用户主目录分支46a2718保持clean；远端worktree/bingsheng只读确认732e190，属于集成HEAD祖先。
C01原修改archive `0993082727d55ea78b965439a19642e2c46d65f6`仍在，研究/有效文档已独立吸收，
其余原bytes与恢复回执保留，见[C01无损记录](CANONICAL_C01_INTAKE_20260913.md)。
本轮2012个tracked文件对授权私钥exact bytes扫描零命中，私钥0600且ignored；不是全量安全认证。
最终审计另核Git clean/FF ancestry、测试原始结果、证据上传下载SHA；满足后普通push，绝不force。
GitHub证据prerelease仅标开发matrix v2实验，不伪造旧qualification或正式仿真服务。
push后停止，不创建main PR；到硬截止无论是否满足都返回明确审计。
