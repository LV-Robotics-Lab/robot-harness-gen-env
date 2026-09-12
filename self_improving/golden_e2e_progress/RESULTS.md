# 进度与结果

## 2026-09-13 C05/C06 Codex资产视觉seam

- `CodexBackend.assess_asset_candidates`通过原Yuxin `a6_verify.verify_candidate(infer=...)`
  执行闭问/开放问及属性验证；显式注入Codex，不加载默认Qwen。
- 与interpret共用唯一受限transport；candidate预览bytes由CAS核验，调用/原输出/判断入CAS。
- 13项相关传输测试通过，含错误颜色、模型越权工具、非法schema拒绝；测试为显式进程替身。
  尚未进行真实候选视觉判断或同workflow资产接通，不授物理能力。

## 2026-09-13 第三次真实Codex解释通过（不是E2E）

- 固定`4fddb17`，workflow `0d9dd28c-c82a-4b81-b6b9-0641bc2e7cd4`，60.739505s。
  Harness实际接受SceneIR并提交revision2；父状态blocked，明确缺`asset_resolver`。
- 证据：`/home/jingxiang/bingsheng/canonical-managed-interpret-v3-20260913.A4bXu1/`。
  未执行资产、Genesis、包或copy-run，不计S01矩阵通过。

## 2026-09-13 C06 provider、规范化与登记组件

- Yuxin原GitHubTreeSearchProvider实际搜索固定Khronos`90d7ede14c7e280af263824604b427a1ca02cb66`，
  返回4候选；从返回项选择Box后获取1664B，SHA `ed52f7192b8311d700ac0ce80644e3852cd01537e4d62241b9acba023da3d54e`。
  原metadata CC-BY-4.0/Cesium保留；search2.296611s、fetch0.511125s、总2.825764s。
  证据：`/home/jingxiang/bingsheng/canonical-yuxin-box-search-fetch-20260913.pGyaFc/`。
  未做Codex视觉选择；开发dirty身份和执行模块前后SHA均保留，不称固定资格。
- licensed mouse/cube与TRELLIS真实新geometry的静态规范化分别0.008755/0.002217/0.262392s，
  源SHA不变；证据`/home/jingxiang/bingsheng/canonical-trellis1-20260913.1U7bmo/normalization-real-zgd_37gh/result.json`。
- 四文件闭包为URDF/visualGLB/collisionOBJ/physicsJSON；凸包填平凹腔，不授inside能力。
  质量/摩擦为明确供给值、惯量为凸包均匀密度估计，不冒充测量。
- AssetRegistry复用唯一SQLite/CAS，父版本不可变，geometry摘要排除URDF物性/许可/report；
  inspect重核CAS，源许可与receipt独立保存。44项相关测试通过；最终100%覆盖率门尚未达到。

## 2026-09-13 第二次真实Codex解释：transport通过、SceneIR拒绝

- 固定`85cc0af`、workflow `d1e03805-8373-4ff6-8cf0-4403325350dd`，61.945589s。
  原S01 prompt/seed/local约束，仅解释smoke；不是矩阵通过。真实turn.completed证明strict schema被接受。
- 模型输出`structural_support.category=tabletop`和不存在的`tabletop_center` frame，故
  `failed/invalid_model_evidence`。原始失败保留在
  `/home/jingxiang/bingsheng/canonical-managed-interpret-v2-20260913.RN84TR/`。
- 另发现只给两轴尺寸时契约强制三个数，诱发补未知厚度。新增RED回归后允许意图轴为null，
  并明确合法frame/category；规范化与运行时仍要求完全解析，不放松物理检查。
- 校验错误现在保留最多64条位置/类型/原因的CAS报告（排除原输入/context），新增RED后GREEN。
  31项模型/契约测试通过；新修复尚待固定提交后真实重跑。

## 2026-09-13 首次真实Codex解释失败与schema修复

- 固定`19f7762`通过Harness提交S01原prompt/seed（仅解释smoke，不是完整scored case），workflow
  `0e1ab2dd-0f12-4566-843c-c79aa10627be`，2.754564s后`failed/model_exit_failure`。
- 服务端400 `invalid_json_schema`：nullable `media_index`未列入required，模型尚未解释场景。
  现场：`/home/jingxiang/bingsheng/canonical-managed-interpret-20260913.98GjUN/`；失败不覆写。
- 公共schema回归先RED；严格transport projection显式required全部属性；字段provenance改固定六字段，
  joint positions改命名记录数组，三维tuple改同质items+长度约束，消除动态字典/prefixItems。
- 修复后32项相关快测通过；尚待固定新提交后真实重跑，不把测试替身GREEN称为服务端通过。

## 2026-09-13 C04/C05 首个持久输入解释slice

- 单`harness.sqlite`与CAS，submit先持久handle；重复/并发同key同workflow，异请求同key拒绝。
- resume记录running operation后真实Pillow/FFmpeg处理；原图、完整视频逐帧RGB SHA/PTS/时长入CAS。
- 活owner拒绝接管；真实外部decoder测试进程意外退出后，经同workflow resume留下dead-owner记录。
- 错图保留raw ref并failed；缺decoder blocked并列资源。critical unknowns blocked且无新输入不反复问模型。
- managed Codex adapter显式read-only、禁shell/MCP，输出只proposal；模型测试是外部传输替身。
- 71 passed/3.52s。阶段性新包综合语句/分支约91%，不是最终100%；尚无新真实Codex/Genesis资格。
- 已接解释之后仍明确缺asset_resolver；package未物化就不能成功。C04完整恢复/clarification/终态门未完成。

## 2026-09-13 C06a 单份Yuxin provider包迁移

- `1_asset_reuse`的106个tracked文件单次git mv到`asset_reuse`，lib内部相对import，未复制第二引擎。
- 包装配置与活跃Python/测试消费者路径已修复；不可变证据、外部路径和历史Markdown保留原义。
- packaging/import先RED后GREEN；packaging+asset admission+公开新import共107 passed/1.71s。
- 此slice未进行网络/模型/Genesis；旧provider许可与tier0筛选缺口仍由canonical adapter负责修复。

## 2026-09-13 C03a 类型化入口与能力注册

- request、SceneIR、hash-only ArtifactRef与结构化ToolResult已实现；非法输入/图引用与错误状态拒绝。
- 精确release SemVer、canonical命名空间、handler输入输出类型检查；schema生成/check和字段表。
- 各新seam先RED再GREEN；39 passed，新增x2env范围188 statements/62 branches全覆盖，wall约1.14s。
- 这是C03首个独立slice；stage运行时类型、完整descriptor及实际handler接线仍待后续垂直实现。
- 无新Genesis qualification、完整copy-run或远端push。C02固定合同提交为`88b4885`。

## 2026-09-13 C02 冻结验收合同

- 机读 `qualification-matrix-v1.json` 保留12条原prompt、seed、4+4+4分组、4/4与8/12门、
  四份输入媒体摘要、补充门和copy-run优先级；`physics-assertions-v1.json`固定原数值阈值。
- CONTEXT、SEAMS、ACCEPTANCE、SKILL_CONTRACTS 收敛为一个workflow、三个public Skills及内部tools。
- 公开合同测试RED→GREEN，4 passed；四份真实媒体574134 bytes的size/SHA匹配。
- C02只证明合同冻结，不证明compiler、provider、Genesis、copy-run或最终资格通过。

## 2026-09-13 C01 clean 基线与 canonical 开始

- 全部6个dirty工作树和主目录子模块均已本地archive+独立恢复核验；原其他工作树未修改。
- 92个冻结refs选择性分类完成，实际canonical feature SHA暂待对应实现；未机械合并历史tip。
- `c94eecd`保留4份用户ASPIRE原文，`46a2718`保留独立历史证据与有效文档hunks；原bytes可恢复。
- 用户主目录完成明确路径清理和ff-only，目前bingsheng为46a2718 clean；未push。
- 新canonical worktree基于46a2718；旧integration作为固定来源/研究文档而非默认产品authority。

## 2026-09-13 C01 保护结果与 C07 前置阻断

- [C01 完整记录](CANONICAL_C01_INTAKE_20260913.md)：92 refs/87 worktrees 的固定清单，80 clean/
  6 dirty/1 prunable；每 ref disposition 仍 pending，不把历史 patch-equivalence 当 canonical 吸收。
- bingsheng 本地 archive `0993082727d55ea78b965439a19642e2c46d65f6`；OpenReal2Sim 本地 archive
  `65980ebf86bd3df7884913c464e84909f801e677`。17 文件共 516,345 bytes，在两份新 detached worktree
  实际恢复后逐文件 hash 通过；额外独立复查通过。源工作树 HEAD/index/status 和文件保持原样。
- 操作约 0.405 秒、exit 0；capture helper ruff/Python compile 通过。没有 canonical 功能测试、
  全仓 pytest、模型加载或新的 Genesis 运行；没有资格/发布/push。
- [C07 固定源码/资源核对](CANONICAL_C07_RESOURCE_BLOCKER_20260913.md)：存在几何 seam 与本地
  主要权重，但 Hunyuan 自定义输出限制需许可/部署方向，替代链需要新资源。按 §17.3 暂停。
  不将模型许可等同输出资产许可，不用 Apache-2.0 上游声明覆盖第三方条款。
- Dashboard 三入口依旧 404；没有 task id/状态更新。

## 2026-09-13 C00 批准与轻量确认

- 用户明确批准 `0a8abc0` 的 C00–C15 计划；Q01–Q64、4+4+4 matrix 和 Physics Assertions v1 冻结。
- 实际集成 HEAD `0a8abc01839f2c37a64b9c08f643f48985fb4a79`，开始时 clean；主目录仍为
  `ea26524cae7b4a18b5576150376eb6ec1ddddf7b`，原 dirty 清单保留，未切换或清理。
- `git fetch --prune origin` 实际 exit 0；dashboard 三个 JSON 均 HTTP 404，无 task id/写回。
- 当前只登记批准与来源保护前置；没有新的 Codex/Genesis/qualification 成功或远端 push。

## 2026-09-13 canonical x2env 规划结果

- Q01–Q64 已持久化，实施计划已生成：
  [`CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md`](CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md)。
- 计划包含 C00–C15 逐功能提交、固定 4+4+4 matrix、每次核验 30 分钟上限、三个隔离 copy-run、
  GitHub evidence prerelease 和 bingsheng push 后等待 main PR 授权的停止点。
- 本项只有文档/只读代码、分支和现有 evidence 审计；没有运行测试、Codex、provider、Genesis、
  qualification、merge 或 push，不能记为功能或真实通过。

## 2026-09-13 用户多对象 Prompt 实跑失败

- 原样输入“在桌上生成一个打开的柜子，柜子上放着粉红色的鼠标”；workflow
  `14fb808f-da9c-4354-8390-00b9d18dafb1`，源码`55eafc8` clean，命令wall 11.225583937s。
- 真实Codex 9.801852654s后错误输出单对象cabinet/web意图，遗漏鼠标、粉红色、开放状态和层级
  支撑，unknowns为空；acquire随后`web_asset_not_found`。revision2停止，未compile/replay/validate。
- 没有本次PNG/MP4/package；完整机器结果、模型JSONL、失败诊断和SHA见
  [证据笔记](../../docs/evidence/user-open-cabinet-pink-mouse-20260913.md)。这证明当前不能处理该
  多对象/关节场景，不能用旧媒体或单刚体成功案例替代。

## 2026-09-13 最终限定回归、完整包复制验收与审阅入口

- [walkthrough](../../repo-docs/walkthroughs/generic-experiment-20260913.md)已逐阶段链接合同/代码、
  各分支命令与失败、包和媒体；AGENTS.md已指向该页。本轮有限实验版本暂停审阅。
- 限定回归205 passed、4 skipped、1 failed，20.17秒；唯一失败是旧qualification manifest拒绝
  已改变的registry.py，不重建资格、不宣称全仓通过。161 schema snapshots检查通过。
- 16个实验/dev/portable模块语句1465/1902=77.02%、分支412/670=61.49%、综合72.98%；
  包含本次未覆盖的真实模型调用和隔离子进程模块。证据根v2的final-regression-summary.json、
  final-regression-coverage.json、final-regression-junit.xml，不与真实案例成功率混淆。
- 最新完整视频包复制根 `/home/jingxiang/bingsheng/generic-video-package-copy-fxqpws2h`，
  包内代码实际baseline1000步/66.166571731秒/41帧3unique；Landlock8拒绝原包、integration、
  main、state-v3/CAS、assets五根。主窗口查看了新末帧。result SHA
  `42807bd71bc4b4f128a4fb46e68f0caee58fc261f56701fb23616be2e0bb91af`，manifest SHA
  `490d6c574dfbe7ea4bfc4ca31436ad237897f8044d4108513ffacbe9926bc7f5`。
  新MP4与输入同SHA是确定性重跑，不是复制媒体；此次仅baseline，未再跑双dt/robot/data。
- B运行后主窗口逐成员复查acquire/revise_asset版本SHA均匹配原receipt，旧资产未覆盖。
  已导asset-repair-package，package命令b177f312-b09e-4b59-ac96-5c8c3de573aa，1.227715181秒。
- 最后仅格式修正22ffed5；输入5测试、161 schema再次通过，10个主要实验模块ruff检查通过。
  没有重新Genesis，真实案例源码保持536496e标注。主工作树用户未提交清单保留。

## 2026-09-13 实验分支验收增量（固定536496e，待最终测试/新copy-run汇总）

以下均由实际 command JSON 核对；v2根为
`/home/jingxiang/bingsheng/generic-experiment-v2-20260913.1QrGYD`。前三例 invocation_identity
记录 HEAD `536496eac465503e4dfa5fa8270f22d0a3fb1b73`、dirty=false；runtime身份仅声明路径，
`declared_not_deep_verified`，不冒称重新深验runtime。阶段passed仍为development_unqualified、
父snapshot active、parent_terminal=false；robot_policy_evaluated/data_collection_evaluated均false。

| 分支 | 状态 | 实际workflow / 命令wall | 结果与证据 |
|---|---|---|---|
| image-only / local | 真实通过 | `eafcc7f0-3eb4-4401-9ddb-f8de1c178b2d` / 159.306761908s | 7阶段、34/34、visual/validate passed；`controller-local-v3/commands/5e60b94f-fa99-4832-8020-4a28df48114f.json` |
| video-only / generated，centered | 真实通过 | `2d7703c6-46b8-41d6-b6e7-f182cef8df34` / 160.627255317s | 7阶段、physical/visual/validate passed；`controller-v3/commands/8e0b2f7b-f933-485b-a457-da755269ad84.json`；`newvideo-centered-package` |
| multimodal / generated，asset fallback B | 真实通过 | `4ef25a76-2e33-4d83-8df8-e71a62baa44c` / 321.707607539s | 12阶段，一次真实scale修订，最终34/34与visual/validate passed；`controller-v3/commands/fff78fb4-a81b-44b7-b909-6b33c999f4dd.json` |
| text / web，固定e2391bd | 真实通过 | `1b4ed241-59db-4fc4-9e57-01b60e82910a` / 157.955860407s | 7阶段，真实Box检索下载、物理/视觉/validate passed；`controller/commands/0db7940d-3b24-45ff-bdbe-e70030e211cd.json` |

- image实际catalog上下文修复来源`13b30b8`：9测试passed，缺失配置在模型前blocked；不加cube别名。
  实际检索候选`catalog:generated_red_block_2c7f5004`，规范化新版本
  `c93ec51e77e5ddf5c26c6fd33f443908ed02a5c21f26b80da46adde9352a7f9c`；模型解释7.896959s、
  visual9.400983s。导出`image-local-centered-package`有231成员，environment SHA
  `26af1510e1ab24571ab7a4214fa04db9e2894cea33f1a29c8ddf0485bd5d7de6`，导出0.866869096s。
  该图来自旧视频case第一次真实centered replay末帧，不把旧复杂位姿失败覆盖为成功。
- B由真实Codex提出scale=3.5，旧版本
  `2c7f500479d7a7145e53d5df6358f43499069555c13c4c579802c4777e0522bb`不覆盖，新版本
  `4e7e58186d4d9d1bf0b32f3cd163e0adec89bec1e24bd68ee4044d3a679224bb`记录parent与patch，
  尺寸实际改变为约[.1925,.1925,.14]m，再compile/replay/observe/visual/validate通过。
- 旧offset视频`f6fe486a-68c1-4894-b210-6eba1359bf7f`最终失败保留：480.881527850s，
  三次replay物理均passed、两次布局修订后第三次visual仍failed，`repair_budget_exhausted`；
  revision16、active_operation null，未validate/导出包，不继续重试此input。命令
  `controller/commands/ced4ee90-5672-4798-8901-48b8fb8436ba.json`是真实预算停止证据，不是video成功。
- 各输入与local/web/generated来源至少一个小型真实成功例，A/B均有真实修订；不代表随机矩阵、
  高成功率、任意重建或digital cousin。原文本132-member包copy-run已通过；最新包的新copy-run与
  最终测试由独立验收继续，结果尚未计入本增量。正式qualification、新experiment MCP投影、父终态/
  晋升、数值修订、robot/data、历史债务和autoresearch均未授予。

## 历史快照：2026-09-13 通用第二轮在途与失败（终局见顶部增量）

- 固定e2391bd，image-only/local首试workflow`6e8b3c50-1db4-4392-aac5-bda05f2f97d8`
  8.8598秒失败：真实解释7.8353秒选择cube，实际catalog只有block，本地检索零命中；未执行
  compile/replay。原命令记录位于v2根`controller-local/commands/2c7793f5-9706-48f8-ab42-46226572d9dc.json`。
  原因是模型只收到catalog路径没有库内容；修复在独立worktree进行，不修改失败现场或加虚假alias。
- scene fallback A workflow`63968c2d-69d5-428d-80cd-7b156aa11790`真实首轮34项有两项
  support_geometry失败（两profile各一次，observed .015m，原limit .02m）。Codex视觉判断passed、
  联合诊断failed，真实修订只将y=.375变成.369，其余意图不变。
  scene SHA从`a7e339cfc5663d75e16bc32ec646be3127ad9e23dd8041e7425c8e8d9dbad3eb`
  变成`09444e2e162f036a1b73437d70403e3ba1c31bd6aebf939ea8aaa9ea1eb762a6`。
  后续第二轮回放/observe/visual/validate全部通过，12个stage commit，command wall
  308.774588622秒；命令记录`controller/commands/6f052943-d764-4557-891a-b6290d0e729e.json`。
  完整journal与前后失败媒体已导出`scene-repair-package`，导出wall1.212777499秒。
- v2证据根`/home/jingxiang/bingsheng/generic-experiment-v2-20260913.1QrGYD`；video-only另一个
  workflow在途，两者共用immutable asset library但不共用workflow状态。
- video-only workflow`f6fe486a-68c1-4894-b210-6eba1359bf7f`已真实解析41-frame源视频、
  采样[0,20,40]、第一轮回放完成；真实视觉判断位姿不符→一次布局修订，目前第二轮回放在途。

## 2026-09-13 首个通用文本完整线与新包copy-run

- 固定`50eb2ec`：workflow `6c84535b-7c0f-467a-a182-2bcffabd3cde`，从Harness CLI输入自然语言，
  7个真实阶段在同一Golden revision0→7通过。物理34/34、visual passed、validate passed；
  父snapshot仍active，CLI阶段序列passed，不宣称父promoted/terminal。
- workflow started_at→last ended_at约154.561299秒，是journal wall跨度，不冒充单命令精确计时。
  首次解释模型9.235203516秒，baseline子进程65.213秒；完整原始receipt/媒体位于
  `/home/jingxiang/bingsheng/generic-experiment-20260913.ZGPNcg`，包`text-package`。
- 复制132-member约11MB包到`/home/jingxiang/bingsheng/generic-text-package-copy-omub915k/package`，
  manifest SHA `ba4e5c1fb7129c48ef145cb8df29a136596dff3f1e7e0c3258cbf72560c94c3a`。
  包内入口实际baseline1000步/65.366339564秒，41连续帧/4unique；Landlock ABI8拒绝原包、
  集成repo、主repo、原state/CAS、assets五根。主窗口已查看新末帧；结果SHA
  `340eb42ebefdb9df21b5e8729f45ad9ff119949ca0ce45cb7662539a8473bfb5`。
- 本次copy-run只baseline，不把旧双dt记录说成新双dt；机器人/数据采集均false。
- 图像/视频/联网完整线和两类真实fallback仍待验。开发源码改变后旧workflow按authority拒绝
  用新实现恢复，已导出的旧包和旧SQLite不改；后续案例使用新隔离配置。

## 2026-09-13 接线边界与独立审阅修复

- 后续真实模型纵切：Harness start→Codex interpret→generated acquire→compile在同一Golden中
  revision 0→1→2→3，4测试通过/9.98秒（包含3离线服务测试）。未执行Genesis，不能计完整文本E2E。

- stage handlers和policy已整合：真实生成→规范化→静态compile→新资产版本组件测试通过；
  runtime adapter调用既有portable runner与物理评估，不把组件测试当仿真通过。
- 独立审阅复现五项缺口并修复：qualified reader实例边界（30相关测试）、颜色意图不匹配（3编译测试）、
  8线程同版本登记竞争、web Y-up未旋转、cousin只有标签。后三项12测试含真实Box/Avocado联网通过。
- 非对称Avocado明确记录Y→Z变换；cousin现为unsupported_selection，不冒称近似选择接通。
- service start/reopen/幂等与未知stage拒绝2测试通过；连同policy/stages为8通过。尚未实际调用模型推进workflow。
- 原始修复证据 `/home/jingxiang/bingsheng/generic-assets-evidence-20260912.xs7UnK/review-fixes-20260913`。

## 2026-09-13 共享意图编译组件

- `f786e44`：复用 tabletop adapter，原始asset→完整CAS representation→GenesisSceneSpec；
  intent XY/yaw改变scene SHA，representation不变；uniform scale与完整顶点footprint检查生效。
- 联合10 passed、1联网opt-in skip；没有Genesis执行。dimensions_m允许null以保留实测尺寸，
  未知图像尺寸不会被任意猜测拉伸为“精确重建”。

## 2026-09-13 内部 Codex 结构化建议

- `CodexBackend.propose` 新增无工具的解释/视觉建议回合；Harness保存输入、schema、逐行日志和结果，
  不从模型文字推进物理状态。真实text interpretation RED→GREEN：1 passed/8.55秒。
- 原Codex backend与统一输入相邻回归27 passed。此时尚未是同workflow E2E；下一接线使用现有
  controller和Golden执行这些建议。video输入保留全帧身份，模型读取样本另注明。

## 2026-09-13 开发 execution 身份接线

- `fbd0441`：同一 Golden start/submit/read 使用显式 development registry/execution/tool receipt，
  旧 qualified 不重解释；deployment需 execution_mode=development 且 allow_changed_skill_closure=true。
- S1与Registry相关68 passed；新模块覆盖88%。另一次较宽131 passed、1旧资格hash失配，未重签。
- 此结果来自受控echo journal，不声称模拟器/模型执行；下阶段真实业务adapter会经过该同一入口。

## 2026-09-12 包内 Genesis copy-run

- `eff10bb` 候选 8 tests passed。实际归档包运行 68.155 秒，baseline 1000步、41连续帧、24 unique。
- Landlock ABI8 实际拒绝原构建目录、CAS和三处源码树；成功目录
  `/home/jingxiang/bingsheng/generic-portable-copy-nisz5oyp`，新视频 `execution/baseline/preview.mp4`。
- 三次启动失败（0.466、0.466、2.320秒）保留；最终使用已声明完整运行环境配方。
- 双dt物理评估、机器人、数据采集均 not_run。执行版本以包内源码清单为准；后续参数和资源检查
  只离线通过，不冒称最终提交再次回放。尚未完成通用工作流接线。

## 2026-09-12 资产获取组件

- `dc08481` 候选审阅后整合：9 passed，真实本地检索、web搜索/许可/下载和程序几何生成。
- 固定 web Box 来源 `90d7ede14c7e280af263824604b427a1ca02cb66`（Khronos/Cesium，CC-BY-4.0），
  约 0.958 秒；生成红 block 约 0.356 秒；均只完成获取/规范化，physics not_run。
- 持久证据 `/home/jingxiang/bingsheng/generic-assets-evidence-20260912.xs7UnK/cases`。
- 已测 asset patch 新建不可变版本且旧成员不变；完整 controller retry/fallback 尚未接通。

## 2026-09-12 通用输入首切片

- 轻量确认集成 `9460a4b` clean；主目录用户改动保留；Dashboard 三入口 HTTP 404，未同步。
- `ExperimentInputs.ingest/accept_intent` 测试先 RED（缺模块、图片/视频参数、意图接口），逐切片 GREEN
  为 5 passed。FFmpeg 真实生成/完整解码 12-frame 测试视频；未执行 Genesis 或模型，不计 E2E。
- 共享 SceneIntent 绑定原输入 SHA，拒绝多对象、unknowns、非有限位置、未实现支撑和权限字段。
- 当前真实资产、便携 loader 和显式开发 execution 由三个独占 worktree 并行实现，尚未合入。

## 2026-09-12 安全停止与通用实验管线交接

- 当前实现安全点为 `cc6afa236579f2360d808be68ae85e94fcfd81d8`；交接文档提交后的准确 branch
  `HEAD` 以 `git rev-parse HEAD` 为准。用户主工作树仍有既有未提交改动，本会话未修改、暂存、提交
  或清理它们。
- normal workflow `2eaecc85-f73c-4c34-9646-8761d8744ba6` 已从公共 Harness CLI 真实调用一个
  managed Codex attempt、8 个 exact MCP calls 和真实 Genesis compile/replay/observe/validate。父
  workflow 正确提交为 `validated_unreleased`，execution 为 `development_unqualified`；没有 publish，
  正式库前后相同。
- normal review package 有 75 个成员、一个 `harness.genesis_scene_spec.v1` entrypoint、一张图片、
  两段 41-frame 连续 MP4；baseline/half-dt 双时间步检查为 34/34。v4 验收 SHA-256
  `74375a8b985dbc53f39e21fa4872007019303807922fa3ff3a904181ebb6e817`，绝对入口见接手 prompt。
- normal 从 submit 到 materialized package 的公共命令 wall 为 3107.369648408 秒；后置深验另用
  242.613330485 秒。TTL 准入和 child preflight 余量分别为 188.957408、188.931921 秒。当前证据仍有
  21,027,007 次 CAS full-hash scan、约 1.114 TB logical CAS reads、63 次 runtime manifest deep read；
  不把缓存敏感的 storage read 或旧 run 对比扩张为正式性能改善。
- repair workflow `b57b603e-bf60-4f23-a9b6-1023c5fcbf38` 按用户改向被安全中断：compile/replay
  已成功提交 revision 1/2；validate operation `3771480a-b088-441a-9fc4-6a384bc61eed` / child
  `8e42d014-d88a-4bde-9c69-73578651d6d3` 只有 `preflight → running`，没有 terminal RunState、结果、
  ended_at 或 committed revision。父仍 active revision 2，Codex attempt 仍 recorded `started`，其 owner
  PID 已退出。没有手改 SQLite、伪造 terminal 或补跑 status/resume/package/deep verification。
- repair 外层状态 254、内部 exit `-2`、wall 1741.124157161 秒，stderr 是等待内部 Codex 时收到的
  `KeyboardInterrupt`。Harness/Codex/MCP/Genesis 所属进程均已退出；原始日志、CAS、child runs、
  checkpoint、transport 和父状态完整保留。
- development mode 仍固定 `exact_skills_qualified=true` 与
  `changed_exact_skill_closure_allowed=false`。它真实运行 Codex/Skills/Genesis 且隔离正式库，但不能
  直接执行修改后的 exact Skill closure；下一阶段可做已授权的最小开发契约调整，不得伪造资格或让
  release 自动降级。
- 当前包只是已深验的场景描述/资产/证据 closure。尚未做复制到新目录后、不读取原 workspace/CAS
  绝对路径的 Genesis load/step；不授予 portable standalone、robot policy 或 data collection 能力。
- 下一 active 是 text/image/video 和 local/web/generated 资产来源的实际接线，不再以单鼠标深度资格
  为主。完整状态表、Gujie 固定来源、两类 fallback、包目标和执行顺序见
  [`HANDOFF_GENERIC_EXPERIMENT_PIPELINE_20260912.md`](HANDOFF_GENERIC_EXPERIMENT_PIPELINE_20260912.md)。

下列章节是历史结果；若与上述交接状态冲突，以上述交接和实际 Git/日志为准。

## M2 Harness-owned Codex 调用关系纠偏 — 73e836b

- 已定位外部入口假设只集中在 `codex_mcp_acceptance.prepare_*` 的“NEW/start”提示、
  `_preview_progression` 对整条 start 链的固定要求，以及 `codex_mcp_process` 对旧提示和历史 start
  时间窗的复核。它们是调用封装与验收逻辑，不是 Genesis、Skill 或 Golden 状态权威。
- 可原样复用：13 个受限 exact MCP 工具及资源读取、compile/replay/observe/validate 执行器、Gujie
  鼠标资产及资格、CAS、Golden workflow journal、PromptRevisionV3 和隔离发布门。
- 最小改动：Harness 服务先创建并保存任务；新增小型内部 `CodexBackend`，以 assigned workflow、
  当前 snapshot、允许动作、调用预算和截止时间启动既有 `codex exec`/wire recorder；MCP relay 拒绝
  start、外来 workflow 和超预算调用。Harness 保存 backend 请求/响应/失败，再从当前 head 决定继续、
  有限重试或恢复。业务执行和状态提交仍只有现有服务/Golden 一套。
- 这只是架构纠偏和实施边界记录；尚未证明 Harness 入口、CodexBackend 或两条真实案例通过。

## M2 受限预览安全收敛点 — b2005da

- 当前权威分支固定在 `b2005da`，工作树在本次进度更新前干净，public schema 为 **149**。
- 已可复用但尚未组成最终闭环的三个切片：`c45ab01` 提供 durable
  `image2env.compile@3.0.0` 修改后编译；`bb72dc5` 提供绑定 compile receipt v2 的 Genesis scene
  execution v3 producer/verifier；`da00fa0` 提供真实 Codex ELF、逐 invocation MCP 配置、stdio wire
  见证与 workflow evidence 对账。`ce0398a` 的 deep terminal resolver 仅保留为可选 M1 完整性层。
- 当前仍没有真实 Codex 从原始图像与文本开始完成正常案例，也没有读取本次实际观测并修改真实
  布局后再次 Genesis replay/validate 的修复案例；因此不能声称 M2 或预览服务完成。
- 接下来只实现单一预览 manifest/隔离发布库、共享修改后 replay/validate 执行和受限 exact MCP
  服务，并记录七段耗时及 TTL 余量。完成两条真实案例、服务入口冒烟和恢复测试后固定版本并暂停。

## M1 首条完整 Genesis rigid-scene line 收口 — 1cf8908

- 固定 `1cf8908acb83b86b15a7ae8a7c96cb943cc39945`，attempt-2 正式测试 **1 passed / 1610.72s**。
  side 图像 seed 41 的 registered compile `a42a3a83-98ea-4c01-a35f-3003a9109c49` → replay
  `bfc30903-0a69-478b-94ed-3ad2c9bbea03` → fresh observe
  `90af3aa7-6d77-4c5e-87d8-185b3bea36ea` → validate `510c3c7d-c020-4623-b2bc-67e0dd045212`。
  四个终态均 registered、非 qualification candidate，复用同一 trusted Registry/SQLite 权威。
- canonical summary SHA `46f21368f0b7dadb0491809b164fa1dac585f79691d68961001f2c928394f5b6`。
  四项 Skill 资格依次为 `57dff640…`、`fd87ecc1…`、`e05500d5…`、`e12e9589…`；完整 refs、
  UUID、配置与日志位于 `/home/jingxiang/bingsheng/golden-four-skill-workflow-20260911/acceptance-index-2.json`。
- attempt-1 的 replay 成功，但旧 `genesis.observe@2.0.0` 深读后帧龄 314.40631s 超过 TTL300，
  正确失败、无成功 summary、validation 未预留。原日志和终态完整保留。修复新增独立
  `genesis.observe@3.0.0`，两次真实 capture candidate、资格和 roots={} 重启通过；不改 v2 语义。
- 新 capture 实际时间 `2026-09-11T10:57:29.934580Z`，checked `10:58:48.881502Z`，
  delivered `11:01:55.434310Z`，expires `11:02:29.934580Z`，交付剩余 **34.50027s**。
  这是恢复可信 half_dt 最终 pose、归零速度、零物理步的独立进程新帧；不是原仿真进程延续。
- 鼠标完整几何、有限桌面与 ground，baseline/half_dt 各 4s、1,001/2,001 trace rows；
  每 profile 41 PNG、无损 FFV1 MKV 和浏览器 MP4，另新 fresh PNG。34 项检查重新计算通过。
  集中验收分别核对 lineage（56/56）、媒体/CAS、失败及迁移；报告同目录
  `acceptance-lineage.json`、`acceptance-media-cas.json`、`acceptance-failure-relocation.json`。
- 第二路径新进程无 Genesis、无源路径读取，验证 **4,115 objects / 1,970,263,053 bytes** 的输出
  证据及 manifest 索引；未复制/复验 runtime member payload、部署根或应用 SQLite。因此这是
  output recovery，不是本次四应用完整 runtime deployment relocation。
- 代码集中门 **162 passed / 4 skipped / 8 deselected**，126 schema，Ruff/format/diff 均通过。
  覆盖率仍未达全部新 core 100%；历史失败、测试断言修正和未覆盖范围保留，不拼成一次全绿。
- M1 只授 `genesis.rigid_scene@1`；`robot_policy_evaluated=false`。未证明三模态、全新资产生成、
  digital cousins、物性实测或外部 Codex/MCP；当前 active **M2：same-workflow exact MCP**。

## M1 production image compile 完成

- `image2env.compile@2.0.0` 已以 v2 evidence qualification 注册；Gujie mouse overview/top/side
  三视角、seed 17/23/41 均真实 production invoke，产出各自receipt和同一确定性有限桌面scene。
- same-live/restart深读、UUID冲突、nested member缺失、candidate/report/terminal漂移、独立代码目录
  漂移及第二CAS/SQLite搬迁均通过。完整索引及98%原始覆盖边界见
  `docs/evidence/golden-production-image-compile-20260911.md`。
- 此目标只授予static scene candidate；M1继续用两个registered compile终态资格化replay v2，
  第三个终态跑production replay，再接fresh observe和environment validate。

## M1 受约束 Genesis scene producer v1 实跑

- manifest `e37a013b…` 封闭 165,812 members / 8.57 GB logical bytes / 19 backend files，
  逐CAS与部署bytes重验。首次正式运行因宿主诊断产生8个pyc而失败，缓存隔离保留、根因RED/GREEN
  实证后，同manifest/request完整门恢复；旧failed terminal未改写。
- 新UUID正式双dt 712.26s通过：1,001+2,001 rows、各41 PNG+逐像素一致无损MKV，34checks全过；
  两进程Landlock8、network/write实际拒绝、0 external native。详见
  `docs/evidence/golden-genesis-scene-execution-v1-20260911.md`。
- v1只证明低层observed execution，未签最终replay Skill；MP4尚未直接可达，且没有fresh capture
  wall-clock event。runtime v2会一次补这两项，并绑定已注册production compile终态后重新资格化。

## M1 native image compile 实际 RED→GREEN

- Gujie mouse overview 仿真图像 + 人工固定 proposal + seed 17 + 真实 durable asset qualification
  已生成 native compile receipt `2777c9a2…` 与 scene `e543ef65…`，输入和来源不是占位数据。
- 首次真实 RED 1 failed / 17.69 s，GREEN 3 passed / 35.46 s，focused 12 passed / 145.81 s，
  另 6 项 schema 反例通过。原始记录、哈希和 98% core 覆盖边界见
  `docs/evidence/golden-native-image-compile-20260911.md`；不声称 core 全部 100%。
- top/side 仍仅备妥请求，生产 Skill application、fresh scene execution 和 M1 全链路继续实现。
  新正式双 dt 直接由最终 producer 发起以获得 live capability，避免先重复低层 launcher 验收。

## M1 真实 Gujie 有限桌面双 dt 切片

- Gujie 来源坐标/完整几何/有限支撑已实际适配，真实 mouse + Box 桌面完成 1,000 与 2,000 steps，
  轨迹分别 1,001/2,001 行，32 项当时格式物理检查通过；两条各生成 41 PNG、无损视频和 MP4。
- 软件 renderer 为 Mesa llvmpipe；早期 EGL、OSMesa 失败保留。证据见
  `docs/evidence/golden-gujie-scene-software-20260911.md`，不把 render 本身当物理证据。
- adapter 公共测试 41 passed，152 statements / 62 branches 100%；scene replay 公共测试
  46 passed，core 140/54 与 schema 126/26 均 100%。这些合同测试不授予执行权威。
- 当前 M1 继续实现真实 image compile 与固定场景执行来源、持久回执和新 runtime lock；
  尚未完成完整环境线，集中验收留到 M1 全链路闭合后。

## M1启动：Gujie场景与完整Genesis环境线

- 用户批准sim-ready服务计划，并明确把Gujie审计作为实际整合参考。审计原件从用户主工作树读取，
  原样收录至候选树`docs/research/gujie-x2env-integration-audit-20260911.md`；其固定历史结论保留，
  当前runtime/qualification进展另记，不回写或提交用户主工作树其他修改。
- 新M1按scene adapter、环境证据/判据、CPU runner/media三条独占实现并行；机器人P8不启动。
- 真实相机可行性已验证：固定Genesis CPU在mouse+有限Box桌面build/50steps后输出640×480 RGB。
  首次URDF文件名错误日志保留，修正后成功；这仅为renderer smoke，尚无完整M1或scene资格。

## 2026-09-11 固定归档完整测试

- 固定`5f78e6267b0c21167a1ab743bbeda945ce80735d`归档完整pytest：2 failed、4,297 passed、26 skipped，
  263.73s。失败是`test_genesis_asset_probe_schemas.py:619`与`test_x2env_compile_receipt_v2.py:301`
  两处过时93-schema总数断言；实际97份snapshot独立check通过，非依赖环境问题。
- 原始日志保留于`/home/jingxiang/bingsheng/golden-full-suite-5f78e62-20260911.Ezoby6/pytest.log`。
  两断言已修正为97，两个focused文件14 passed/0.69s；97schemas、Ruff/format/diff通过。
  没有重跑整套，不把原始完整运行改记为全绿。原始log SHA256
  `6f01fc40d2fa70c50a8ed7c85b0f77198c0eb7ff570989ba47a63d8381f4a894`。

## 2026-09-11 Durable asset qualification TDD

- 固定提交独立资格验收通过：两精确checks各10/13项evidence，issuer固定、qualification-last，
  第二CAS/可信SQLite无Genesis纯读61.60s，真实qualified CAS坏字节拒绝（修正后纯读1pass/21.54s）。
  另一次真实execute保留断点后并发恢复通过：一方签发、一方拒绝journal已变化，重试同ref。
  该收口组原1 failed/1 passed401.84s必须保留；失败只因测试误预期长度错误而实际为SHA拒绝，
  不与后续纯读重跑合称一次全绿。证据见`docs/evidence/golden-genesis-asset-qualification-20260911.md`。

- 正式GREEN已全部通过：3 passed/865.35s，覆盖修正坏XML失败闭包、fresh live签发及重启幂等、
  真实execute→持久executed→新应用恢复签发。两条真实状态均为qualified；源码固定SHA
  `b0da423a3f9dc304b0adac98d81cf1c0f9c1cd0c80df3c0f9dfde9ab06c07ef2`。
  原始日志`qualification-green.log`与SQLite均保留；第二CAS/SQLite纯读及集中验收已通过。

- 新application复用既有`GenesisAssetQualificationV1`，无schema语义修改；固定issuer独立源码摘要，
  SQLite intent/executed/qualified与failed状态，公开reserve/execute/invoke/read。
- 10个离线用例通过，2个formal skip；真实RED用时252.56s：fresh固定Producer成功后，按预期在
  `qualification commit is not implemented`失败。原始`qualification-red.log`与
  `qualification-red.sqlite`保留于`golden-runtime-bound-20260911.ndky_vr0`。
- GREEN已实现两精确checks、issuer CAS、trusted executed恢复和qualification-last；修正坏XML
  测试、直接live签发、execute后销毁应用并重启签发三个formal用例正在顺序执行，尚未宣称通过。

## 2026-09-11 固定 CPU execution 实现（正式验收进行中）

- 两部署固定提交集中功能验收通过，见`docs/evidence/golden-runtime-execution-acceptance-20260911.json`。
  新增完整probe后的私有backend漂移真实用例1 passed/234.73s：后检拒绝且stdout/stderr、六refs和
  1,001rows清理后仍可读。修正后的坏XML正式重跑也通过。最终launcher覆盖134/137 statements、
  42/46 branches；child142/145 statements、31/34 branches，100%目标仍未达到，具体缺口见
  `docs/evidence/golden-runtime-execution-20260911.md`。

- 第二独立六根部署正式Producer+strict reload通过：1 passed，258.71s，位于
  `/home/jingxiang/bingsheng/golden-runtime-bound-relocated-20260911.i7zlos7a/`；摘要SHA
  `c701aada3e9a3c4ddb0e7e038be1efed4d6b293bb4763a55a48986cfbd977436`。
  固定提交集中验收正在收口；durable qualification实际用例继续运行。

- 固定`fd50576`的正式Producer已通过：新manifest
  `6d25b8e2359b343564bf7a2e845ad559b32bfb26c14a7f433633f3af6c25c14e`，
  166,015成员，manifest 79,543,751 bytes，capture 243.432s。正式execution receipt
  `bc6511ad9028fc2bcaa479d4cf8a9c5287b6d453d259cccf2f9ddd317bf65d78`
  已写入，live capability返回且CAS-only重读一致、1,001rows。真实坏XML与第二部署仍在验收中。
  原始配置和回执位于`/home/jingxiang/bingsheng/golden-runtime-bound-20260911.ndky_vr0/`。
- 正式双用例首轮364.39s：正常用例pass，坏XML被真实child拒绝且保留stdout/stderr和partial-CAS
  索引。失败测试旧断言只允许两份输出，未允许新增索引，因此记录为1 passed/1 failed；不能把该轮
  写成全绿。正在修正测试以严格验证三份证据，原始失败日志保留。launcher真实覆盖综合86%；child
  独立诊断测量129/145 statements、21/34 branches，尚未达到全模块statement/branch 100%。

- 冻结manifest批准配置、固定launcher、archive CPU child、版本化request/observation/receipt公共seam。
  相关离线gate 37 passed/2 formal skips，97 schemas；真实成功路径覆盖单独采集，不称全模块100%。
- 受Landlock与socket限制的真实tracer完成initial+1,000steps及完整观测：3,146文件模块、275原生
  maps；另分类2个Torch动态模块、5 frozen、51 builtin、48其他无文件模块。仅两个精确GL别名
  位于声明根外。正式Producer、第二部署和集中功能验收仍进行中。
- 失败stdout/stderr、partial-probe索引与可用CAS材料在临时目录清理前保存；尚无qualification。

## 2026-09-11 Runtime manifest 大目录与独立部署实跑

- 固定实现 `3662ba6` 的两份manifest源码与staged backend逐字节相同。真实capture耗时311.743s，
  包含六个根、166,141成员、8,542,495,955声明bytes；canonical manifest本身79,598,134bytes，SHA为
  `06ed1a2fe2322903c35caceefcf2f504558c32eee0ae628ea72aa1c25deba927`。
- 源CAS-only和mapped验证通过；第二独立物化目录与第二CAS的新进程再次通过全部成员与过程事实
  重验（76.788s），未import Genesis或原工作树。相同manifest身份不依赖原绝对路径。
- 精确supporting CAS为82,070objects/4,567,960,119bytes；物理总CAS另有两份旧capture留下的
  orphan（8,000bytes），没有把它们算入权威闭包。旧capture在最终不可读目录修复前主动终止并保留。
- 独立staged环境另外fresh执行initial+1,000 Genesis CPU steps（47.96s），1,001rows与deep verifier
  通过；Torch intraop/inter-op=1/1、989接触步、4,040contacts、末态settled。只从原CAS复制资产表示、
  15members与旧v1 lock作为输入，没有复制旧report/trace；新report的确定性SHA与旧probe一致。
- 真实执行最终加载309个映射文件，比import-only多出9项native；完整物化distribution包含其字节。
  130个distribution的49,264个有hash RECORD项全部一致，32,387个无上游hash项另纳入逐字节清单。
- 未完成的权威：probe仍引用旧runtime lock v1，未引用新manifest；manifest的Genesis archive与执行
  checkout分账；两项GL库仍从系统位置加载。bwrap最小命令因UID map权限拒绝，详细命令/失败与其他
  startup/source-import不足样本均保留。没有production qualification、MCP image或环境/policy晋升。
- 运行与哈希索引见 `docs/research/genesis-runtime-closure-audit-20260911.md`；bulk目录/CAS未入Git。
- 集中fixed-commit功能验收也通过：独立进程重读源/第二CAS及新部署映射，模型逐项相同；独立
  deep verifier再闭合fresh probe的23objects/1,001rows。小型可审阅索引为
  `docs/evidence/golden-runtime-manifest-run-20260911.json`，独立验收为
  `docs/evidence/golden-runtime-manifest-acceptance-20260911.json`。独立验收入口第一次误传嵌套frozen
  ref被exact ArtifactRef合同拒绝；重建正确公共输入后通过，未修改实现或删除失败记录。

## 2026-09-11 Runtime content manifest 公共纵切

- 新增 `capture_runtime_manifest` / `read_runtime_manifest` / `verify_runtime_deployment` 三个公共
  interface。六个声明根（backend/distributions/genesis/interpreter/native/stdlib）完整枚举，逐文件
  流式冻结、写 CAS，第二遍重读确认后最后发布 manifest；绝对部署映射不进入内容身份。
- RED→GREEN：首次新模块缺失→1 pass；deployment drift 错误合同缺失→7 pass；canonical JSON、
  路径越界、重复/乱序与 file URI 五个实际未拒绝用例→12 pass；扩展真实并发变化与 fresh subprocess
  换 CAS、删除源目录后纯读重验→27 pass。新模块/schema 合计 147 statements、52 branches 均为100%。
  测试通过 public interface，不 mock Harness 核心或调用 private helper。
- 新增 `harness.genesis_runtime_manifest.v1`，94 snapshots verified；一次集中 Ruff check、format check
  与 diff check 通过。旧 `GenesisRuntimeLockV1` 和 probe v1 未改语义。
- 实际完整枚举测试又发现 `os.fwalk` 默认静默跳过不可读子目录；真实权限反例先未拒绝，补 error
  callback 后拒绝。catalog 相关回归先以旧93项独立清单6 failed/2 passed，更新新入口后8 passed。
- 明确边界：`authority=declared_byte_closure_only`。deployment verification 比较调用方提交的 process
  facts，不自动证明运行中进程、实际 import/native 发现完整性或 process confinement；没有签发资格。
  真实 staged runtime 与大目录 CAS 运行另行记录，不能由这27项测试代替。
- 固定 `a8cf4a4` 全套基线为4218 passed/21 skipped/1 failed（250.10s）；失败节点为 packaging
  environment 缺 pip，补 pip 后又暴露缺 setuptools.build_meta。只补缺失 pip25.3、setuptools84.0.0、
  wheel0.48.0 后，该唯一节点1 passed（0.71s），没有重跑其余通过项或把分次结果写成全套一次绿。
  原始与修复日志保留在 `/tmp/golden-runtime-baseline-*.log`。

## 2026-09-11 Harness-owned Genesis CPU asset probe

- 固定 `7e145274a10e8d80f3966b491e05a512294588fc` 实现无相机
  `GenesisCpuAssetProbeBackend`、typed producer 与 pure-read deep verifier。backend 固定 Genesis
  `0e74bf392781884ccad765c3f344419c86b872ca`、CPU/Newton、`dt=0.004`，显式执行 initial observation +
  steps 1..1000；不创建 camera/renderer/rasterizer/media。
- fresh legacy mouse run 得到 23 CAS objects / 7,711,609 bytes。源为 1 link/0 joints/8 collision members；
  Genesis 实际 load 为 1 link/1 free joint/6 DOF/1 merged geom（502 vertices/1,000 faces），质量
  `0.07000000029802322 kg`、摩擦 `0.2`。
- 1,001 rows 重算为 989 contact steps、4,040 contacts、1 pair、最大穿透
  `0.0004873909056186676 m`、累计法向冲量 `2.7468003143923414 N·s`、末态线/角速度
  `6.854644206770141e-07 m/s` / `2.562796953528175e-05 rad/s`，七项 exact checks 全 pass。
- CAS 复制到第二个绝对路径后，root Python 在不加载 Genesis、不读取 source root 的新进程中重建并
  strict verify 23/23 supporting artifacts 与全部 rows；path/bytes/SHA mismatch 为零。运行摘要 SHA 为
  `36fdcca8cdeafa31aba81ca521b61e214c42e1842529a7808f0d881cad338745`，relocated verification SHA 为
  `58682a8d6f29c9ca69a8bcc8a908dbdf839e092448b49fc5d15de8d378b91bed`。
- focused 为 `77 passed, 1 skipped`；固定 Genesis runtime 节点单跑 `1 passed`；93 schema snapshots 与
  Ruff/diff gate 通过。首次真实长跑暴露 contact force 作用方符号错误并产生零 force/impulse 的失败样本；
  修复后负向用例要求正 contact force/impulse 和末态 settling。
- 边界：未签 `GenesisAssetQualificationV1`。当前 runtime lock 尚未封住 Python/Torch/Quadrants/NumPy/
  native 完整依赖，不进入 production Registry/MCP；也不证明 environment compile/replay、robot policy、
  promotion 或真实质量/摩擦测量。不可变证据见
  `docs/evidence/golden-genesis-cpu-asset-probe-20260911.{md,json}`。

## 2026-09-11 Genesis asset-probe typed evidence

- `5300ab2` 先冻结四层 schema RED，`04e96de4da74ab0fcd207b756203071d414c8a7d` 完成 strict
  self-hashed `GenesisAssetProbeInputV1`、`GenesisAssetLoadEvidenceV1`、
  `GenesisAssetContactTraceV1`、`GenesisAssetProbeReportV1`。
- input 固定10mm release、CPU/Newton、dt=0.004、1000 steps、单环境/单线程/seed0、禁止模型/网络；load
  绑定 representation/runtime/15 members/1 link/0 joint/8 collisions/质量惯量摩擦；trace绑定完整NDJSON与
  contact/penetration/force summary；report要求六项exact pass checks且不含qualification/publishability字段。
- 14 focused pass；新模块190 statements/22 branches 100%；91 Draft202012 snapshots。没有runner、issuer、
  runtime execution或qualification，下一门是producer+deep verifier后再跑fresh Genesis。

## 2026-09-11 legacy mouse representation staging

- TDD `6950af5` 冻结 15-member portable staging seam，`28eb805` 首版 GREEN；独立验收发现 verify→CAS put
  之间重读可变 source 的竞态，`2ddd5e619edf074b80b6563a134b549fba4cc97d` 改为先冻结受控 byte
  snapshot再发布，并增加 member/manifest/extra/symlink 在首次 put 时变化的永久测试。
- 真实输出 16 CAS objects / 5,334,039 bytes；representation self-hash `ebf0f616...693f2e`、CAS ref
  `bf71acba...add1e9`、loader closure `0c23bb38...50904`。relocated source 与重提完全相同。
- 37项独立验收通过；新模块 184 statements/64 branches 100%。静态完整性失效均首写前拒绝；竞态四例
  发布的15-member tuple仍与冻结 manifest逐项相同。
- Gujie `eb0b710` 现成最小真实入口仍强制 Rasterizer/101帧视频/EGL；当前 editable Genesis/runtime lock
  漂移。下一切片先冻结四层 typed probe evidence，再实现无相机 CPU issuer与 fresh 1000-step run。
- 证据：`docs/evidence/golden-legacy-mouse-representation-staging-20260911.{md,json}`；JSON SHA-256
  `fd550e84eb250c4d02e11fe24871fcf9285456807967317a245df9ead1b45d3f`，Markdown SHA-256
  `e90fec459ff286de47116338358d9a33d8f0f9694eb0084dca0f180ef0414544`。
- 边界：仅 unqualified representation；无 runtime lock、asset qualification、Registry/MCP 或 Genesis run。

## 2026-09-11 controlled durable image compile 与 production qualification 审计

- TDD `39823de` 冻结 public application seam，`97a6fad` 完成首版 GREEN；独立完整性验收随后发现 candidate
  receipt CAS 可在绑定前被换靶，`69a269b` 增加所有 RunState artifacts 当前摘要复核、v2 input/receipt 解析
  和 exact deep verifier。第二轮又发现 qualification 可改用 CAS 外相同字节或漂移元数据，最终修复
  `40bfa213c8dace7e99de6d96980d3431f3d87735` 强制 exact CAS URI/name/media/schema。application 未绑定时只能 `evaluate`
  unregistered candidate；绑定前会从同一 CAS/SQLite 重新核对 controlled report 中的 candidate run、
  Invocation、output、21-ref closure、implementation/source tree 与 qualification identity，之后才允许
  caller-owned UUID4 invoke。
- 同实例重提与无 live application 重建都返回同一 terminal；same UUID 的 input/seed 漂移在 handler 前
  identity-fail，intent-only 状态按 pending 拒绝，CAS、events 与 durable rows 不增长。十一项 application pass；
  新模块 166 statements/46 branches 100%；相关 handler/verifier/Registry/RunStore 回归 121 pass。两次独立
  验收分别为 31/40 pass，后者逐项破坏 21/21 closure artifacts与 qualification/report/descriptor/SQLite，
  全部 fail closed。
- 固定 `40bfa213...` 根套件为 4,119 passed / 20 skipped / 1 failed；唯一失败是项目 `.venv` 无 `pip`
  导致 packaging 子进程无法启动。同一 packaging 文件用有 `pip` 的系统 Python 为 2 passed。
- Gujie 指定 exact pass 输出 `/home/jingxiang/gujie/gen-env/output/鼠标_原始平面_物理与渲染` 当前不存在。
  最接近的历史归档有 479/479 manifest、15-member mouse URDF 与 CPU static inspect，但 TaskOutput/workflow
  absolute locator 已失效，两份 physics evidence 被当前 verifier 以 threshold mismatch 拒绝，且记录的 7 个
  runtime module SHA 与固定 `eb0b710` 为 0/7 匹配；可达 Git 历史不存在闭合该实现集的 commit。
- 因此旧 bytes 最多可生成 unqualified `AssetRepresentationV1` candidate。旧 physics pass/contact/render/video
  均不能签当前 runtime lock 或 asset qualification。production 下一门必须由当前 sealed issuer fresh 执行
  asset-scoped dynamic load + collision/contact probe，并在 relocated fresh CAS strict reload。
- 证据：`docs/evidence/golden-image2env-controlled-application-20260911.{md,json}`；JSON SHA-256
  `6a7c804df9e5dcc2bfd32e6dc0b772787508a09afb0a392f307d91b91b404ee3`，Markdown SHA-256
  `42f5608c09ac07d39a5393938d71bf53c1b28ca21ac2aa3844cbbad868c926fa`。
- 边界：controlled qualification 只证明 application durability，不进入 production Registry/MCP；没有运行新
  Genesis/GPU，也没有 simulator-ready、publishability、replay/validate 或 robot-policy。

## 2026-09-11 X2 controlled image exact-reuse compile candidate

- TDD 提交链：`327c78a` 冻结 compile receipt/output v2 → `5b45673` 绑定 canonical compile input 并将
  尚未资格化的 image/video compile descriptor 输出切到 v2 → `44f841a` 冻结 candidate handler RED →
  `c817ebf7353d62b7e6fd5df8a65575e93adaa374` 完成 GREEN。v1 receipt/image output/video output schema 与
  snapshot 保持 parse-only、字节不变；公共 schema 84→87。
- `ExactReuseCompileClosureVerifier.preflight` 在任何派生 CAS 写入前完整消费 image source/probe、proposal、
  prompt、runtime lock、portable catalog 的每个 available representation/member/qualification/check evidence，
  并验证 trust allowlist、issuer、profile、subject 与 loader closure。首个 slice 限制为一个 dynamic object、
  一个 `on_table` relation、无 unknowns，以及大小写敏感的 semantic name/alias 唯一匹配。
- `Image2EnvExactReuseCompileHandler` 只通过 `SkillRegistry.evaluate_candidate` 执行；它确定性发布 compile
  input、SceneIR、TaskIR、asset selection、scene closure、binding、四项 static validation、package 与 v2
  receipt。deep postflight 成功后才发一次完成事件，并返回含完整递归 supporting refs 的 output。
- focused 99 pass；handler+verifier 合计 375 statements/92 branches 100%；87 schema exporter 与 Ruff/diff
  通过。完整根 `.venv` 为 4108 pass、20 skip、1 fail；唯一失败因该隔离环境没有 pip，导致 packaging
  节点无法调用 `python -m pip wheel`。同一 packaging 用系统 Python单跑 2 pass，该项不是实现失败。
- 第一轴独立 archive 验收：fresh CAS `12→21→21`，两次不同 candidate run 的 output/artifacts 完全相同；
  21 个递归 ArtifactRef 与 RunState artifacts 精确相等，CAS 路径/bytes/SHA 全闭合；3×2 RGB PNG 实际
  解码。missing/ambiguous category、missing source/catalog/member/qualification 六类反例全部在派生前零写。
  summary SHA-256 `6e2191b5ee887925705b856cb68b8f3c851d69e09b5e6d7abf6d6c261efd52b5`。
- 第二轴独立 archive 验收覆盖 exact alias、大小写漂移、unknowns、多对象、非 `on_table`、未受信未选
  catalog entry、input receipt 和 postflight graph 漂移，均 fail closed。故障注入使 handler 在 publish
  之后 postflight 失败时，terminal 为 failed、无 output、RunState artifacts 为空，但 append-only CAS 保留
  9 个不可达对象；这是当前没有 batch transaction 的显式边界，不把它误报为零 orphan。
- 证据：`docs/evidence/golden-image2env-exact-reuse-candidate-20260911.{md,json}`。
- 边界：所有输出固定 `simulator_executed=false`、`simulator_ready=false`、`collision_qualified=false`、
  `publishable=false`。没有 production asset issuer/qualification、Genesis/GPU、replay/validate、MCP image
  exposure 或 robot-policy；implementation-bound placement 也不表示图像推理或物理正确。

## 2026-09-11 X1 complete-sequence video ingest 与 strict consumer

- 提交链 `506ad84` → `13e8f01bd963375bdf65d8940434b240cbbb6117` 以 public-seam RED/GREEN
  建立完整视频身份。实现没有改写既有 `harness.video_frame_sequence.v1`：该 v1 仍是 prompt fallback
  的抽帧 lineage；source identity 使用新的 `harness.video_decoded_frame_sequence.v1`、
  `harness.video_media_probe.v1` 与 `harness.video_source_media.v2`。
- `VideoMediaIngestor` 要求 injected decoder 报告 full decode，并为每帧绑定 index/DTS/PTS/duration/
  raw-byte size+SHA；同时重算 CFR timebase/FPS、duration、total/unique frame count 与 domain-separated
  sequence digest。`VideoMediaConsumerVerifier` 从 source/manifest/probe CAS 全量重解码并逐项重算，
  且不写 CAS。
- 门禁：focused 74 pass；`schemas/video_ingest.py` 与 `video_ingest.py` 合计 343 statements/104 branches
  为 100%；相邻 x2env/prompt/compile/replay qualification 回归 155 pass；公共 schema 69→72；完整 root
  `4080 passed, 20 skipped in 261.29s`。
- 第一条独立 archive 验收用 system FFmpeg 6.1.1 实际生成并完整解码 H.264/yuv420p MP4：2517 bytes、
  18 total/6 unique frames、6/1 FPS、1/6 timebase、3000 ms，sequence digest
  `a70265aea01ec383f2975dacba11f2d331bc885893292e3b701f03293e7a31b8`。fresh ingestor 重试与
  consumer 纯读均 CAS 零增长；15/18 sample、逆序、duration 漂移、`fully_decoded=false` 及七类
  consumer evidence 漂移全部拒绝。
- 证据见 `docs/evidence/golden-video-ingest-consumer-20260911.{md,json}`。system FFmpeg 仅是独立
  acceptance adapter，不是 pinned production decoder、sandbox 或 qualification。没有 video compile
  handler/application、production Registry/MCP、Genesis/GPU/物理、publishability 或 robot-policy 主张。

## 2026-09-11 X1 deterministic image ingest 与 strict consumer

- 提交链 `45e0153` → `ae05517` 先以 public-seam RED/GREEN 建立 exact CAS image ingest；第一次
  集中功能验收随后真实发现三项缺口：public probe 未绑定 source/declared MIME、Pillow 会把 ICC 等
  metadata 带入所谓 canonical PNG、下游没有纯读重验 inline `ImageSourceMedia` 的 deep consumer。
  `3fd03f4` 将这些问题固化为 RED，`fe271db55992c6d0880ba2a8903dc233bce2bb3d`
  完成修复，未把第一次验收的失败隐藏为通过。
- `ImageMediaIngestor` 对 exact CAS JPEG/PNG/WEBP 做完整 Pillow 解码、EXIF transpose、尺寸/单帧/
  byte limit 检查，并从规范化像素创建新的 metadata-free image，再发布 deterministic PNG 与 strict
  `harness.image_media_probe.v1`。重复 ingest 同源得到同一 refs，CAS 不增长。
- `ImageMediaConsumerVerifier` 是 pure-read deep consumer：重新 resolve/hash source、canonical 与 probe，
  完整解码 source，重算 canonical PNG、pixel hash、decoder identity 和 canonical probe JSON，再逐项
  绑定 `ImageSourceMedia` 镜像字段；MIME/CAS/动画/上限/损坏/非规范 JSON/字段换靶均 fail closed。
- 门禁：公共 schema snapshot 总数 68→69；focused 38 pass，新 `image_ingest.py` 与
  `schemas/media_ingest.py` 合计 178 statements/44 branches 为 100%；相邻 image/schema 回归 76 pass；
  完整 root 为 `4014 passed, 20 skipped in 258.63s`。第一条独立 archive 验收对 L/RGB/RGBA 的
  clean/ICC/text/EXIF/combined 输入证明逐模式 canonical identity 唯一，PNG 仅含 IHDR/IDAT/IEND；
  JPEG/PNG/WEBP consumer 前后 CAS manifest 字节级不变，8 类漂移全部拒绝。
- 第二条独立 archive 验收同样 PASS：38 项 image tests 与 192 项 x2env/compile/replay qualification
  回归通过，69 份 Draft 2020-12 schema/catalog identity 一致；项目 venv 排除 packaging 的宽回归为
  `4012 passed, 20 skipped`，同机带 pip 的 Python 将 2 项 packaging 单独跑过，与完整 root 4014 项吻合。
  两个未资格化 x2env compile descriptor 仍为 `handler_registered=false`、
  `qualification_available=false`，既有 text compile/replay qualification 未被改写。
- 证据见 `docs/evidence/golden-image-ingest-consumer-20260911.{md,json}`。实现为当前 Harness/Pillow
  原生切片，没有复制 Gujie runtime 源码。边界仍是纯 CPU X1 ingest/consumer：没有 image compile
  handler、qualification、production Registry/MCP、Genesis/GPU/物理、publishability 或 robot-policy。

## 2026-09-11 P10 PromptRevision → exact compile consumption

- 新增 `harness.initial_compile_attempt.v1`、`harness.prompt_revision_compile_attempt.v1` 与 image/video
  compile input v2。revised attempt 明确携带 current PromptRevision、effective prompt、非空排序唯一失败
  receipts 和 predecessor package；旧 v1 输入模型继续 parse-only。尚未资格化的 image/video
  `compile@1.0.0` contract descriptor 在首次 qualification 前切到 v2，exact Skill/MCP 名不变。
- `GoldenRunHarness.submit` 在 command CAS 写入、parent turn reservation 和 child UUID 分配前重验：current
  head/workflow/principal/revision/state、`next_compile_skill_ref`、revision ArtifactRef、revised prompt、失败
  lineage、top-level/attempt predecessor package，以及 video→image selected frame/source identity。当前 head
  是 PromptRevision 时，initial、旧 v1、错误 target 或任何未消费 lineage 的 Skill 都 fail closed。
- 官方 MCP 2.2 public-seam 测试真实构造 controlled observation→blocked failure→四项 audited reads→
  advisory rev3→prompt rev4；正确 v2 image attempt 让 tracking application 恰调用一次并以 controlled
  blocked terminal 推进 rev5，workflow operation predecessor 精确等于 PromptRevision receipt，可信 state
  保持不变。target、revision、effective prompt、failure receipts、predecessor package、fallback frame 六类
  漂移以及旧 v1 输入均在 application/child/CAS/SQLite reservation 前拒绝。
- schema snapshot 总数 64→68；schema/x2env focused 45 pass，消费 seam 20 pass且新 guard
  37 statements/26 branches 100%，相邻 schema/Golden/MCP 回归 337 pass，完整 root 3984 pass/20 skip。
  第一轴独立验收闭合 55 个 CAS objects、49 个 unique reachable refs、两库 integrity 与 no-live restart；
  第二轴独立 archive 复核 68 schemas、100% guard 和未变的 text compile v1 qualification identity。证据见
  `docs/evidence/golden-prompt-revision-compile-consumption-20260911.{md,json}`。
- 边界：该链的 image compile application 是受控 preflight/blocked fixture，没有 image2env handler、
  qualification、资产生成/复用/digital-cousin、Genesis runtime、媒体输出或 publishability；也没有修改或
  重签已资格化的 `text2env.compile@1.0.0`。

## 2026-09-11 P10 audited resource delivery 与 external Codex diagnosis

- 新增 `harness.resource_access_receipt.v1` 与 SQLite append-only access journal。workflow artifact 的 MCP
  读取返回原 text/blob 和第二份 server-owned receipt；receipt 绑定 principal、workflow、完整 observed
  RunSnapshot、URI、ArtifactRef、delivered bytes/hash、representation 与 served time。读取与专用 receipt
  lookup 都不进入 shared turn，前者幂等、后者不递归记账；公共 schema snapshot 总数 63→64。
- 两个 exact MCP control tools `harness_advisory_record_v1` 与
  `harness_prompt_revision_record_v1` 已接到唯一 `GoldenRunHarness.submit_control` 权威；无 generic invoke。
  advisory 必须用 durable receipt 无条件精确覆盖 source prompt、全部 failure、observation receipt 与
  diagnostic sources。complete 接受；zero、3/4 partial 和 unrelated state-only 均拒绝，revision/turn/head
  与 control rows 不变。
- 真实 Codex CLI `0.153.4` 通过项目 stdio MCP 协商 `2025-06-18`。前两次启动失败后，raw wire 定位到
  MCP 2.2 新协议会容忍、Codex handshake-era serializer 会拒绝缺少根 `type: object` 的 output schema；
  `6e03144` 修复并以真实 `2025-06-18` initialize→initialized→tools/list 回归锁定 exact tools/no generic。
- 运行保留六次尝试：attempt 4 已读取四项但被 client approval policy 阻止 mutation；attempt 5 是互斥
  CLI flags；attempt 6 实际读取 source prompt、blocked receipt、controlled Genesis observation receipt 与
  diagnostic。首次 advisory 因 receipt URI 未排序被拒；Codex 保留同四份回执排序重提，提交 advisory
  revision 3，再提交显式 video-frame→image prompt revision 4。state SHA 始终
  `34f7a821…ed1d`，child runs 保持 2。
- 无 live application 的新 MCP server 重提两个 exact control，SQLite rows 保持
  starts/operations/accesses/controls/turns=`1/2/4/2/4`，CAS 保持 45 files/75,985 bytes；四份 access
  target 与四类 cited sources 集合精确相等。独立验收 complete/zero/partial/unrelated 四类均 PASS。
- 门禁：64 schema snapshots；MCP/control/resource focused 113 pass。仅用项目 `.venv` 的首次完整门
  得到 Harness `2942 passed, 20 skipped, 1 failed`、root `3955 passed, 20 skipped, 1 failed`，唯一失败
  都是该 venv 没有 `pip`，发生在 packaging wheel 构建启动前；系统 Python 单跑 packaging 为 2 pass。
  最终以系统 Python 的构建工具并通过 `PYTHONPATH` 复用项目 MCP 2.2 dependencies 重跑当前完整 root，
  得到 `3957 passed, 20 skipped`。Ruff check/format 对本切片文件与 diff check 通过。
- 证据：`docs/evidence/golden-external-codex-diagnosis-20260911.{md,json}`；外部原始 root 为
  `/home/jingxiang/bingsheng/golden-codex-diagnosis-20260911.jyB0nn`。
- 边界：observation/controls 仍明确 `simulator_executed=false` / `external_agent_executed=false`；delivery
  receipt 的 acknowledgement/understanding 标志均 false。该切片不证明 Genesis runtime、模型理解、
  physical validity、publishability、promotion、robot-policy，也没有让 prompt revision 被下一次 compile
  强制消费。

## 2026-09-11 P10 diagnosis/prompt revision control 纵切

- 新增 `AdvisoryRecordCommand`、`ExternalAgentAdvisoryReceipt`、`PromptRevisionCommand`、
  `PromptRevision`、`ControlCommitSnapshot` 五份 strict public schema，公共 snapshot 总数 58→63。
  advisory 固定 `authority=advisory_only`、`external_agent_executed=false`，并显式禁止授予 physical
  validity 或 publishability；prompt revision 只接受 full replacement，并把 base prompt、失败 receipt、
  predecessor package、advisory 与显式 fallback lineage 哈希绑定。
- `SQLiteGoldenRunStore` 新增共享 Skill/control turn ledger。Skill operation 与 control detail 都必须先
  占用同一 `(workflow_run_id, base_revision)`，因此不能在两个表中同时赢得同一个 revision；旧 operation-only
  数据库在事务内精确回填，active child 阻止 control，任何部分布局、双 detail 或次序破坏均 fail closed。
- `GoldenRunHarness.submit_control` 只接受两种 exact command。提交前重建 workflow reachability，验证
  blocked/failed receipt、diagnostic、Genesis observation citation、TTL、source prompt/package 和 video
  frame manifest；accepted control 只推进 revision/turn/head，state ref/hash 与 child runs 字节级不变。
  后续 qualified Skill 的 predecessor 可精确接受这两种 control receipt，mixed history 可重启重建。
- 固定 `e9d9fcb6337779c6bd0415dd6b8e451d53fe7496` 的三轴独立验收均 PASS：受控链完成
  observation succeeded → Skill blocked → advisory → prompt revision；CAS 41 objects、143 个递归
  ArtifactRef occurrences/39 unique identities 全闭合，frame index 7 的 video/frame/manifest lineage 与
  advisory/prompt 自哈希重算一致。TTL 60 秒闭区间上界接受、+1 秒拒绝；无 live application 重提返回
  同一 terminal，CAS/SQLite 零增长；CAS 篡改和未绑定 frame 均 fail closed。
- store/control/schema/aggregate 的验收分别覆盖 55、174、243 与较宽 331 个测试节点；新增 schema
  272 statements/72 branches 100%，新增 store diff 210 statements/62 branch arcs 100%。整个既有
  `golden_run.py` 在聚焦套件约 93%，未被误报为新模块 100%。带 platform/MCP/demo/dev extras 与
  构建工具的完整根套件为 `3929 passed, 20 skipped`。本切片 13 个 Python 文件的 Ruff
  check/format 与 diff check 通过；全仓 `ruff check .` 仍暴露 2,463 个既有 style violations，未在本
  功能提交中顺手改写无关文件。
- 边界：本纵切只证明 controlled S1 receipt/state/history，不证明真实 external Codex。当前没有 durable
  resource-access receipt、exact advisory/prompt MCP tools 或模型执行；server 交付资源也只能证明交付，
  不能证明模型理解。真实 Genesis capture、promotion 与 robot-policy 同样未完成。

## 2026-09-11 P10 fresh observation 受控纵切

- 冻结 exact `genesis.observe@1.0.0`，复用既有 Golden command envelope 与 S1
  `GoldenRunHarness.submit/read`；不新增通用 invoke 或旁路状态写接口。
- 受控纵切的完成条件是 `GenesisObservationReceipt`、`ToolResult.fresh_observations`、
  `TrustedToolReceipt.derived_fact_claims`、StateDelta mutations 与 resulting TrustedWorldState 对
  key/value/capture time/TTL 逐项闭合；inner receipt 与外层 trusted receipt 的 provenance 关系也必须
  精确。缺少、额外、重排或漂移均不得推进 revision。
- TTL 冻结为 `1..300` 秒闭区间 `[captured_at, valid_until]`，且
  `valid_until == captured_at + ttl_seconds`。过期 receipt 仍作为审计资源可读，但不能被后续操作作为
  fresh 输入；在 terminal commit 前已经过期的 observe 只能产生 blocker。
- 历史 replay PNG、旧截图和既有媒体 receipt 只能作为历史 supporting artifacts，重新贴标签不构成
  fresh capture。首轮 test adapter 必须记录 `simulator_executed=false`。
- 提交链 `9b0e86a` / `0cde87d` / `77a3510` / `0d38d42` / `69bc2db` / `bd00a1d` /
  `b13cef2` / `fe802df` / `2950e71` / `af9d6e8` / `dd3e161` / `6cc6482` / `55b28bd`
  依次冻结合同与 RED、增加 strict schema/controlled application，并把 receipt-bound fresh observations
  接入 Golden evidence policy seam。三个新公共 schema 把 environment package、replay workflow/runtime
  receipts、可选 checkpoint、capture plan、requested FactKeys、TTL、typed values、capture window 与
  payload CAS 精确闭合；公共 snapshot 总数 55→58。
- `ControlledGenesisObserveApplication` 不注册、不资格化 Skill；它复用 `SkillRegistry.evaluate_candidate`、
  SQLiteEventJournal、SQLiteRunStore 和共享 CAS。adapter 返回显式 observation value，不从图片摘要推断；
  PNG 完整解码后入 CAS，receipt/output 固定 `simulator_executed=false`、0 executed steps。输入 source CAS
  缺失、camera/key/time 不匹配、无效 PNG 与受控 video 请求均产生稳定 blocker。
- Golden submit 内核现从 invocation-bound evidence policy 取得 fresh observations，并验证每项都等于
  trusted receipt 的 upsert claim、source artifact 属于 supporting closure、存在有效 TTL；StateDelta
  仍以 trusted receipt 作为事实来源，同时保留 observation 的 observed/valid 时间。revision 2→3、
  terminal failure、receipt/projection/CAS 篡改、同实例重提和无 live application 重开均有受控用例。
- `ControlledGenesisObserveEvidencePolicy` 又从当前 workflow authorization context 逐项绑定
  principal/workflow/revision/state ref/receipt head，并在首次提交和重启恢复时用持久 Invocation 重算
  source package、replay child、runtime receipt、checkpoint、capture plan、requested keys 与 TTL；两个
  workflow 即使具有相同 state content hash 也不能混用 receipt。
- 首轮固定提交三轴验收发现 application receipt 与 Golden projection 虽分别通过，但尚未在同一个
  child execution 中相接，因此没有把该结果算作完整纵切。后续增加显式
  `create_controlled_genesis_observe_execution_binding`：只有 caller 提供已由同一 CAS 验证的受控
  descriptor 时才注册 test-controlled Skill，并使用 Golden 分配的 child UUID 进入 durable controlled
  intent；restart binding 只读 SQLite/CAS，不能安装 live qualification。它没有进入 production assembly、
  qualified exports 或 MCP catalog。
- 固定 `55b28bd482e0892989ec1caddfe4ef4483d2eb95` 的独立 archive 验收真实完成同一 workflow 的
  setup revision 1 → image replay revision 2 → controlled observe revision 3。61 个 CAS 对象/
  105,261 bytes、268 次递归 ArtifactRef/61 个 unique identity 全部闭合；Golden、fixture、observe
  SQLite 分别为 1 start/3 operations、2 intents/invocations/states/4 events、1 intent/invocation/state/
  3 events。无 live application 重开后重提返回相同 terminal，CAS 与所有表行数零增长。
- 验证：observation schema/application/policy/Golden projection focused 为 `57 passed`；schema/application 两模块
  331 statements / 96 branches 均为 100%；`python -m script.export_harness_schemas --check` 验证 58 份
  snapshot，controlled policy 为 153 statements / 70 branches 100%，Golden 相邻六模块合计
  1010 statements / 358 branches 100%；新增 application/binding 为 258 statements / 74 branches
  100%。最终根套件为 `3830 passed, 20 skipped`，Ruff/format/diff check 通过。本轮未运行 GPU。
- 边界：本切片实现的是受控 fresh-observation 证据与持久状态 seam，不是 Genesis runtime pass。
  已完成的 binding 仅接受 test-controlled qualification；production runtime binding、真实 sealed
  checkpoint capture、production qualification、Registry entry 和 `genesis_observe_v1_0_0` MCP
  exposure 均未完成。

## 2026-09-11 P5 真实 MCP compile→replay→validate

- 固定提交 `b6f779659606fb3b91b7a6a2c98ff6a03970c83c` 的正式 runner 使用官方
  `mcp==2.2.0` stdio Client/两个独立 server 进程，在资格绑定的 exact delegated scope 内完成
  raw text workflow create 与三个 exact Skills；协商协议 `2026-07-28`，目录只有四个 exact tools。
- 真实结果为 revision 0→1→2→3，compile/replay/validate 均 succeeded。B 进程重提同一 start 与
  validate command 后返回完全相同的 terminal operation；Golden SQLite 仍只有 1 start/3 operations，
  三个 child 各只有 1 intent、1 invocation、1 RunState，事件为 14/19/4，CAS 不增长。
- 首次真实运行保留在 `golden-mcp-real-20260911.BCRFXA/evidence`：它已到 revision 3 和媒体读取，
  但 runner 在 client context 关闭后访问协议属性，导致 summary/restart gate 未执行。`b6f7796` 修复后
  使用全新 root 重跑成功，没有覆盖或晋升失败目录。
- 成功 evidence root 为
  `/home/jingxiang/bingsheng/golden-mcp-real-20260911.3CZQqP/evidence`；summary SHA-256
  `b0bced244cad3192cc02ffd2c0b7a0fd96eaa2bc072595fabdb915b6d34f32f9`。独立三轴验收均 PASS：
  201 个 CAS 对象/61,836,772 bytes、3,928 个递归 ArtifactRef/189 个 unique digest 全闭合；四段
  receipt/state 与三段 delta 重算精确；compile/replay portable closure 为 13/50；validate 的 12 个输入
  来自 revision-2 durable lineage。
- replay 是真实 `0/900/120/120/12`，runtime report 37 checks pass，validate 重算 39 checks pass，
  fail/not-run 均 0；23 个资产成员/57,291,124 bytes 闭合。MP4 为 H.264 320×240/12fps/120 帧，
  114 decoded-unique，与 MCP Blob/CAS 字节一致。renderer stderr 的 1 条 OIDN CUDA unsupported、
  123 条 invalid-handle 与 1 条 PyTorch warning 作为非致命诊断保留，未被物理/媒体结果掩盖。
- 正式证据：`docs/evidence/golden-mcp-three-skill-real-20260911.{md,json}`。门禁：新 core
  919 statements/244 branches 100%，focused 238 pass，qualification 61 pass，root 3,782 pass/
  20 skip，Ruff/format/diff pass。Dashboard 三 URL 仍 HTTP 404，未声称同步。
- 边界：这是 legacy RoboTwin/SAPIEN 的真实 MCP transport 闭环，不是 external Codex planner、
  Genesis、promotion、robot-policy、image/video 或跨机 provisioning；scope 消失后再次 replay 前仍须
  重建同名 cgroup locator。

## 2026-09-11 P5 三 Skill MCP 受控全链与 production assembly

- 新增 `GoldenMCPServerSettings/create_golden_mcp_adapter`：从 operator-owned settings 重开
  Compile/Replay/Validate 三个 production application，共用 compile CAS 与 Golden SQLite；资格 CAS
  按内容导入，目录缺失、空 CAS、symlink/目录/错 digest member 均 fail closed。目录只列
  `harness_workflow_create_v1` 与三个 exact text2env tools，无 generic invoke。
- 首轮 RED 证明外部 MCP caller 在 compile revision 1 后只能得到 `state_ref`，不能构造 replay；新增
  workflow-scoped artifact resource，由 `GoldenRunHarness.read_workflow_artifact` 先完整重建当前历史，
  再只开放 typed workflow closure 中可达的 state/receipt/ToolResult/RunState/StateDelta/媒体引用。
  foreign principal、错误 workflow 与不可达 digest 统一不可见；裸 `artifact://` 不作为授权。
- 第二轮 RED 证明 validate v2 的 12 项 lineage 输入不能由 caller 从 OperationSnapshot 拼出；新增
  `Text2EnvValidateV2InputBuilder`，从 revision-2 receipt chain 与两个 durable child authority生成并深验
  compile/replay portable receipts、package、media verification 与 request provenance，再由只读
  `.../skill-inputs/text2env.validate/2.0.0` resource 返回 exact input。外部 caller 随后仍显式调用
  `text2env.validate@2.0.0`，没有新增通用执行工具。
- 官方 `mcp==2.2.0` Client 在受控 simulator boundary 下真实完成 inline text create revision 0、compile
  revision 1、900/120/120/12 replay revision 2、validate revision 3；validate 为 publishable/pass，重复
  validate 返回同一 operation 且 CAS 不增长。final receipt→ToolResult→StateDelta/TrustedReceipt 的字节
  SHA 全匹配，delta 只有 `environment.validation`，fresh observations 为空。MP4 经同一 workflow closure
  返回官方 BlobResourceContents，base64 解码后 length/SHA/CAS bytes 精确一致。
- 独立三 Skill tool snapshot 固定四工具的 name/skill/schema digest，文件 SHA-256 为
  `6ec3461f63ecc1a36740ff6a154aa22214005dfe39b06b8d68da4733bb7de4a5`。受控全链 1 pass；builder
  联同相邻 validate 路径 23 pass，81 statements/18 branches 100%；Golden workflow 213 pass，
  469/150 为 100%；MCP adapter focused 3 pass，246/46 为 100%；三 Skill assembly 89/22 为 100%。
- 边界：本节尚未执行真实 RoboTwin/SAPIEN MCP stdio replay，也未运行外部 Codex planner 回合；
  inline image/video 仍是不受信 claim，media ingest、poll/cancel、Genesis、promotion 与 robot-policy未完成。

## 2026-09-11 P5 raw text workflow create 纵切

- MCP 目录新增 exact control tool `harness_workflow_create_v1`（不是可选 `skill_ref` 的
  generic dispatcher）。caller 只提供 idempotency/workspace/profile 和 inline `request.*`
  user facts；principal、RegistrySnapshot、`source_kind=user_input`、evidence schema、CAS identity、
  workflow UUID/state/receipt 均由 server/Harness 注入。caller 不能提供路径、URI、摘要或
  outer ArtifactRef。
- adapter 先用 strict typed model 校验 inline facts，构造 canonical `WorldFactEvidence` 并写入与
  GoldenRunHarness 完全相同的 CAS，再按 content identity 排序 refs 并唯一调用
  `GoldenRunHarness.start`。重复 fact key、非 `request.*`、伪造 authority 字段和同幂等键不同
  request 都在受信 state 之前拒绝。
- 真实官方 MCP 2.2 进程内 Client 从 inline text 创建 revision 0，紧接 exact
  compile 到 revision 1。stdio A 进程也从空 Golden DB 完成 create→compile；B 进程重提
  start 返回当前 revision 1 authoritative head，再以原 command envelope 恢复同一 compile
  operation，SQLite/CAS 不增长。
- 该路径暴露并修复 S1 缺陷：旧 `GoldenRunHarness.start` 把已推进 workflow 的同请求
  retry 当成 revision-0 snapshot 验证，误报持久层冲突。现在 revision 0 仍校验 start
  receipt，已推进/有 active operation 则经公开 `read()` 完整重建历史并返回当前 head。
  GoldenRun 回归 213 passed，427 statements/140 branches 100%；三份 checked qualification
  门 61 passed，无需伪造重签。
- committed MCP tool snapshot 现精确包含 workflow-create 与 compile 两个工具的 name/
  Skill-boundary/input/output schema digest。边界：inline media 仅是不受信 JSON claim；尚无
  media ingest/receipt，不能把图像或视频引用写成已验真资产。

## 2026-09-11 P5 exact MCP stdio 与重启幂等

- 新增 strict local settings、compile-only production assembly 与
  `robot-harness-golden-mcp` stdio entry。每次进程启动均从同一 CAS、compile child
  SQLite、Golden SQLite 与冻结 RegistrySnapshot 重组真实 application/catalog/Harness，
  不从配置注入 handler、descriptor 或 qualification。
- 官方 MCP 2.2 stdio Client 真实连接 A/B 两个独立 Python 进程，两次均完成
  negotiate/list/call/read。A 执行 compile 后退出，B 用相同 command/idempotency 重提；
  OperationSnapshot/resource 完全一致，Golden/child SQLite 全表行数与 CAS path/bytes/SHA
  均不增长，operation/intent/invocation/run-state 各恰好 1 份。
- stdio context 内同时用 Python stdout redirect 包围 assembly 与 server run。故障注入的
  assembly/handler 普通 `print` sentinel 各只出现在 child stderr，官方 client 无
  `Failed to parse JSONRPC message`；这一真实 wire 验收同时证明 text/resource
  discriminator 没有被 `exclude_unset` 丢失。
- 补充失效模式：Registry/CAS 在已建立 adapter 后漂移，exact call 现返回
  `isError=true` + 稳定 `HARN_ARTIFACT_UNAVAILABLE`，不再泄漏为 MCP `-32603`。加入
  committed compile tool schema digest snapshot，绑定 exact name/Skill 与 input/output schema 字节身份；
  后续 workflow-create 更新见上方。
- Registry/Golden/schema 相邻回归含四份 MCP tracer 为 231 passed；adapter/server/assembly 三个
  核心模块合计 217 statements/38 branches 为 100%。stdio 入口另由真实双进程协议
  tracer 覆盖，没有用 mock transport 代替。
- 边界：此处是“预置 workflow + 预置 catalog”的 compile-only stdio 验收。尚无 MCP
  workflow create/catalog ingest、replay/validate production assembly、operation polling/cancel，不声称
  外部 Codex 已能从零启动三段链。

## 2026-09-11 P5 exact MCP 进程内纵切

- 新增 transport-neutral `HarnessMCPAdapter` 与官方 MCP 2.2 server bridge。工具目录只取
  adapter 冻结的 `RegistrySnapshot` 与当前 exact live `GoldenExecutionCatalog` 的交集，
  并再次对账 descriptor；不暴露 generic `skill.invoke`，也不允许 caller 替换 trust root。
- MCP 输入由类型化 `RunCommandRequest` envelope 与 exact Skill input schema 机械合成；
  `principal_id` 和 `skill_ref` 均由 server authority 注入。成功路径只调用
  `GoldenRunHarness.submit`，重复调用恢复同一 terminal operation；workflow resource 只通过
  `GoldenRunHarness.read` 读取。已知领域失败返回稳定结构化 code，unknown tool/resource
  保留协议层错误。
- 在独立环境安装真实 `mcp==2.2.0`/`mcp-types==2.2.0`，用官方进程内
  `Client` 完成 negotiate/list/call/read，并确认 `TextContent`/`ResourceLink` discriminator 在 wire
  上保留。最终相邻回归 229 tests 通过；两个新模块合计 183 statements/34 branches 为
  100%。项目增加 optional extra `mcp>=2.2,<3` 并在 CI 安装；base Python 未安装 MCP
  时 SDK 测试按可选依赖跳过。
- 边界：本纵切在当时只证明进程内 compile exact tool 与 workflow read；stdio、跨进程重启、
  workflow create、operation polling/cancel 与 MCP 上的 compile→replay→validate 尚未验收。

## 2026-09-10 integrated revision 3 最终真实闭环

- 固定实现 commit `eec1976bdd71578e43b606ef3b88f3bd7d5975e9`。官方 generator 在该
  implementation 上重新生成 replay/validate qualification，报告分别 8/8 与 6/6 pass；
  compile checked report 为 7/7 pass。同一 replay bundle 在 integration 与独立 archive 代码路径
  strict load pass，源码 manifest 是 50 个逻辑相对 exact-byte members。
- 独立 relocated checkout
  `/home/jingxiang/bingsheng/worktrees/golden-revision3-final-relocated-20260910` 在新 evidence
  root 真实完成 `text2env.compile@1.0.0` revision 0→1、`text2env.replay@1.0.0`
  revision 1→2 和 `text2env.validate@2.0.0` revision 2→3。三段共用同一 Harness 与
  RegistrySnapshot `f723d8be…aa8b`，StateDelta keys 分别只是
  `[assets.catalog,environment.package]`、`[replay.runtime_evidence]`、
  `[environment.validation]`，三段 `fresh_observations=[]`。
- 三个独立功能验收轴通过。终局 CAS 有 159 个对象/61,441,212 bytes；3,407 个递归
  ArtifactRef occurrence/152 个唯一内容身份的 URI、字节数、摘要和 CAS 对象全一致。
  compile package/catalog 精确成为 replay input，runtime evidence 精确成为 validate input；所有
  operation/trusted receipt/ToolResult/StateDelta/child receipt/world state 前件和重算链闭合。
- replay 原物理报告 `01e85c89…9d75` 与 validate 独立重算报告 `054bb519…713b`
  均为 pass/0 fail/0 not-run；23-member runtime assets 共 57,291,124 bytes 全闭包。MP4
  `a41d0536…12a4e` 独立完整解码为 H.264 320×240、12 fps、120 帧、114 个
  decoded-unique frames，8 份 view 媒体全部与 CAS 字节一致。
- 无 live application 的恢复 Harness 重提 validate command 返回同一 terminal operation；SQLite
  仍只有 1 个 workflow start、3 个 parent operations 与各 1 个 child invocation/terminal。最终 Harness
  `2743 passed, 20 skipped`，root `3756 passed, 20 skipped`，55-schema、变更 Python Ruff 与
  diff check 通过；validate 五模块 614 statements/188 branches 为 100%。
- 正式证据：`docs/evidence/golden-revision3-integration-20260910.{md,json}`。运行得出的
  `publishable=true` 仅属 legacy RoboTwin/SAPIEN profile；`genesis_profile_satisfied=false`、
  `promotion_executed=false`，不存在 robot-policy 事实。replay 源码身份可跨安装路径，但
  operator 仍为本机部署绑定；temporary delegated cgroup 消失后 deep verify 正确拒绝，
  运行前须显式 provision，未证明跨机器自动 provisioning。

## 2026-09-10 revision-3 integration replay 重新资格化

- 固定 integration implementation `b020be3182a7d0d264a633a91c3cbc7fdddc5212` 上重新探测 capability，
  官方 generator 在全新的外部 bundle/scratch/CAS 与 `golden-r3-q-h6ng8i.scope` 中真实执行 direct 与
  production-kernel 两条 `0/900/120/120/12`；物理、事件、23-member runtime assets 与顺序媒体均通过。
- 新 replay 三文档摘要为 manifest `f76ebd64…60b5`、qualification `3bafdfc90…7bf0`、report
  `64506f60…16e5`，implementation `2f8f2879…0974`，62-ref closure。将 66 个 generator CAS 对象逐摘要
  导入 fresh CAS 后得到 68 个对象；同一 bundle 在 integration root 和独立 archive 安装路径
  `/home/jingxiang/bingsheng/golden-r3-relocated-20260910.ZjLUfl/worktree` 均 strict verify/load pass，source
  manifest 不含任一本地绝对源码根。
- 随后先在 relocated source 以新 replay 资格真实执行 compile revision 1→replay revision 2，再以这条
  受信 lineage 运行官方 validate generator。validate 两次独立重算、完整 lineage、负向用例、candidate
  repeatability、source stability 与 generator 内 fresh-CAS reload 全部 pass；三文档摘要为 manifest
  `fa324e58…c8fe`、qualification `86aa8c4f…5842`、report `2f3ef5d8…18d6`，15-file implementation
  `a36fb7d9…a683`。将 108 个资格 CAS 对象逐摘要导入新的 CAS 后共 110 个对象；integration 与 relocated
  两个源码根的 public loader 均为 pass。新增 checked validate bundle gate 固定三文档、实现摘要和六个
  无排除通过的 qualification checks。
- 本条已完成最终树上的 replay/validate 重新资格化；随后的完整 relocated
  revision 0→1→2→3 已完成，见上述最终闭环。

## 2026-09-10 Golden portable replay

- 固定起点 `2bdeafd836d641b4f14aef316a21fbcd736f55e1`。首个公共 RED 把同一 qualification
  bundle 与 exact 源码 tree 复制到不同绝对路径；旧 loader/factory 因绝对 distribution root 拒绝。
  `0ef1eb17fd9e66126764436077400419c1d79266` 将资格源码改为逻辑 `replay_distribution` root，runner
  纳入 50-file implementation closure，module/runner 与 external operator 文件分账；当前根 mapping 上
  仍逐成员、逐目录链做三轮 nofollow 重走与最终 root 重开。字节漂移、缺/多成员、目录/链接异常、
  不同 tree、schema drift 与错误 external dependency 的负例继续拒绝。
- 第一次真实 relocated Golden 在 production factory 与 compile rev1 后精确暴露 capability 仍哈希绝对
  `PYTHONPATH`；没有复用该失败目录。`487bcfc189e31784461ae0dc43fae5955dbf0d19` 只在
  `PYTHONPATH == local mapped source root` 时把它归一到逻辑根，其他值仍按原字节哈希。capability +
  worker 契约 `197 passed`，`runtime_capability.py` 602 statements / 246 branches 为 100%；replay/P3
  focused `233 passed`。
- 在全新 bundle/scratch/CAS 与 `portable-replay-53vbg3.scope` 中重新探测 capability 并运行官方
  generator。direct 与 production kernel 各一次真实完成 0/900/120/120/12，物理均 pass，23-member
  runtime asset closure、完整事件流与 120/100/114 媒体通过。新 bundle 三文档为
  `9553bf10…041a` / `9315394c…cf03` / `feb70567…c681`，implementation 为
  `990b9dbf…5779`，62-ref closure 为 `92457468…9110`。
- 将同一 bundle 原样复制，并把资格 CAS 逐 digest 写入 68-object fresh CAS；A/B 两个绝对源码路径的
  strict verifier/loader 均 pass，source manifest 不含任一本地 root。随后从 B 路径构造 production
  `ReplayApplication`，在新的 Golden evidence root 真实执行 compile rev1→replay rev2；runtime
  evidence `ddb1cf26…673d` 为 pass。MP4 `a41d0536…12a4e` 独立解码为 120 帧、114 个 decoded
  unique、320×240、12 fps。无 live application 的新进程恢复 revision 2 并幂等返回同一 terminal。
- 正式证据：`docs/evidence/golden-portable-replay-20260910.{md,json}`。该范围仍不包含 validate Skill、
  Genesis native、`genesis.robot_policy@1`、promotion 或跨机器 provisioning；dashboard 三个状态 URL
  仍为 HTTP 404，未伪报同步。最终 focused `431 passed`、Harness `2671 passed, 20 skipped`、root
  `3684 passed, 20 skipped`、39-schema、变更 Python Ruff 与 diff check 通过；仓库级 Ruff 仍有
  2,463 个既有、范围外问题，未在本功能提交批量改写。

## 2026-09-10 P4 exact validate revision 3 真实闭环

- 固定实现 commit `63dfac7f0b84c4208bc3731cd4f6fca5cb0a4142` 的 production Golden Harness
  在 `/home/jingxiang/bingsheng/golden-p4-real-20260910.v8` 真实完成
  `compile revision 1 → replay revision 2 → validate revision 3`；三个 operation 均为 `succeeded`。
- validate 从 revision 2 的受信 compile/replay receipt、portable child receipt、package/catalog、runtime
  evidence/report/assets、media verification、event transcript 与 provenance 重建完整 lineage；独立重算
  报告 `054bb519…713b` 为 pass，decision `f5a36aaf…785c` 给出 `publishable=true`、零 blocker。
- validate ToolResult 的 `fresh_observations=[]`，StateDelta 只写 `environment.validation`；无 live
  applications 的重启重复提交恢复完全相同的 terminal validate operation，没有预留第四个 child。
- 单一 workflow CAS 有 187 个文件；逐文件摘要与路径复算通过，3,735 次递归 ArtifactRef occurrence、
  176 个 distinct digest 的 URI/bytes/SHA 全部闭合。资格证据先导入同一 CAS：replay 66 objects、
  validate 114 objects。
- 真 replay 参数为 `0/900/120/120/12`；MP4 `a41d0536…12a4e` 独立解码 120 帧、114 unique、
  320×240、12 fps。完整证据为
  `docs/evidence/golden-compile-replay-validate-p4-20260910.{md,json}`。
- 保留失效模式：错误环境触发 capability mismatch；未处于 delegated supervisor 时物理执行虽成功但
  media verifier 以 `sandbox_unavailable` 拒绝；替换新 operator root 又被资格 identity 拒绝。只有进程
  进入 exact qualified supervisor 后整链通过，未弱化任何门禁。
- 能力边界：这是 legacy RoboTwin/SAPIEN validation profile 的 publishability，不是 promotion，
  `genesis_profile_satisfied=false`，也不证明 Genesis-native、`genesis.robot_policy@1` 或跨机器 provisioning。
  该分支尚未整合 portable replay，最终 integrated commit 必须重新生成 replay/validate qualifications
  并再跑 relocated revision 3。
- 集中功能测试新增 36 个 public/production seam 场景，覆盖 durable pending/identity conflict、factory
  seal、真实 CAS/receipt/state lineage 反例、handler/dependency 与恢复 authority。覆盖诊断还识别出四条
  已被前置 exact invariant 蕴含的重复分支：重复 package/typed-output 比较、CAS put 后的非原子二次读取、
  固定 resolver 的恒定长度判断；删除这些重复判断后，上游 fail-closed 校验保持不变。用当前源码正式
  生成临时 validate qualification 并在隔离源码副本运行 98 个测试，五个新增模块合计 614 statements /
  188 branches 全部为 100%；Ruff 与 diff check 通过。分支内 checked qualification 仍保留原证据字节，
  最终 integration 才会进行正式重签。

## 2026-09-10 Image2Env / Video2Env 合同独立功能验收

- 固定合同提交 `71a19eddbf8a9567eda95f876fc3978a42827d1c` 与权威来源
  `/home/jingxiang/gujie/gen-env@eb0b710581fd7794bc01b447b0f77cb871c8a711` 分开验收。来源的媒体
  probe、单 rigid-link/no-joint URDF、SimFoundry import、Genesis load/build/step/contact/penetration、
  baseline/half-dt 候选入口均真实存在；但 full ordered frame hashes 未持久化，原 pipeline 内部调用模型，
  legacy absolute locator/status 不能成为 Harness 信任根。
- 现存 fresh-image output 的报告虽为 `scene_built`，exact `TaskOutput.verify()` 因 29 个 post-manifest
  physics 文件而失败；existing-reconstruction image output 的 170-file closure 与 legacy Genesis
  baseline 1000 steps、101-frame MP4 通过。后者仍没有 Harness CAS/receipt/qualification、video input、
  当前 dual-dt closure 或 robot-policy，只能作为 image-derived reuse candidate。
- 标准 Draft 2020-12 validator 首轮暴露 16 个新增 snapshot 中 11 个不能 standalone 消费；原因是根
  `$defs` 内子模型的 `$id/$schema` 改变 fragment resource scope。公共 schema 生成现在只保留文档根的
  resource identifiers，并递归移除嵌套定义的 identifiers；47 份受影响 snapshot 的机械比较只删除
  89 对嵌套字段，无其他语义差异。
- 最终新增 16 schema 的 Pydantic/standalone/catalog 验证均为 16/16，六 Skill 的 12 个 I/O 均为
  12/12；focused `40 passed`，相关 438 statements / 110 branches 为 100%，replay schema/qualification
  回归 `164 passed`，55-schema snapshot、Ruff 与 diff check 通过。Harness 全域为 `2694 passed,
  20 skipped, 1 failed`；唯一失败是 checked replay qualification 正确拒绝 schema/source 字节变化，
  没有在合同分支伪造重签，留待最终固定 integration commit 真实执行。

## 2026-09-10 P3 GoldenRun qualified compile → replay

- 公共 RED `17e535d` 先固定同一 actor-neutral workflow 的第二个 exact operation：rev0 经真实 qualified
  compile 到 rev1，再从该受信状态中的 EnvironmentPackage/asset catalog 和同一 compile receipt 关联
  构造 replay input，最后经 `GoldenRunHarness.submit` 到 rev2。P3a durable application 与两轮真实 replay
  重签完成后，P3b 功能提交为 `b51bf5783f9ea5cd8e0e4559bef020d934d15d30`。
- 新增 replay-owned `golden_replay_policy.py` 与 `golden_replay_execution.py`；generic aggregate/catalog
  没有新增 replay 分支。policy 只读 parameters 与 base trusted state，只从 terminal typed output 重建
  `replay.runtime_evidence` 一项 claim；媒体留作 supporting artifacts，`fresh_observations=()`。production
  adapter 在构造和每次访问前重验 factory seal，并把 application pending 映射到 actor-neutral pending。
- 用例覆盖 succeeded/blocked/failed、reservation/intent/invocation/terminal 四个恢复窗口、cross-workflow
  与 stale package 在 child intent 前拒绝、typed output/supporting CAS 关联、policy/application authority
  漂移、两个 binding 的顺序/重复、跨 exact Skill 共用 idempotency key，以及 replay 加入后 compile policy
  digest 与既有 compile operation 不变。focused `23 passed`，两个新增模块 125 statements / 34 branches
  均为 100%；compile/replay/qualification 邻接回归 `448 passed`。
- 真实运行第 1 次在任何 child 前因 objective 的 UTC 文本不是模型规范 JSON 而拒绝；第 2 次由类型化
  evidence 生成字节后，同一 `GoldenRunHarness` 真正提交 production compile 与 production
  RoboTwin/SAPIEN replay，revision 为 `0→1→2`。runtime evidence
  `ddb1cf26bb3d9ad0d904519dbe4f0b506918131b13ab1678854e537e68b7673d` 为 pass，900 steps、120-step
  contact window；MP4 `a41d05366050ba0c2af635e69299a12a4b2a0e78ed27490517d27b122fa12a4e`
  为 120/100/114 frames、12 fps。无 live application 的新 Harness 实例重读并重提交原 command，返回
  同一 terminal operation，没有再次执行运行时。
- 正式证据：`docs/evidence/golden-compile-replay-p3-20260910.{md,json}`；外部可观看媒体目录在该页记录。
  本切片不调用 validate Skill，不证明 Genesis native 环境、`genesis.robot_policy@1` 最终门、promotion
  或真机。
- 最终标准 root 为 `3676 passed, 20 skipped in 172.25s`；39 份 Harness schema snapshot、P3 Python
  Ruff/format 与 diff check 通过。全 root 带 coverage 的诊断运行中，两个既有 0.1 秒 capability 大输出
  用例在 coverage 开销下被分类为 timeout，完整结束为 `3674 passed, 20 skipped, 2 failed`；同一 4-case
  节点脱离 coverage 立即 `4 passed`，随后标准 root 全绿。P3 两个新增模块的独立 statement/branch
  coverage 均为 100%。统一平台脚本还在测试前因此独立 worktree 的 OpenReal2Sim 与 digital-cousins
  未初始化而返回 `ready=false`；没有把该环境前置写成 P3 功能通过。

## 2026-09-10 P3a durable production ReplayApplication

- 从固定起点 `7fe5c7e76fd9300014195c3af836f2d08bcd9c92` 建立独立 worktree。公共 Golden replay
  tracer 先以 `17e535d` 固定 compile revision 1 → replay revision 2 目标，但评审发现可直接构造的
  `ReplayApplication` 尚不能充当受信 child authority，因此保持该 tracer 为 RED，先拆出 P3a。
- P3a 的独立 RED `3d26088` 要求 production factory 才能签发 private seal；应用在每次公开读取与
  `invoke_typed` 前重验 exact Local CAS、同一 `harness.sqlite3` 的 journal/run store、Registry authority、
  evidence-invariant registration policy、唯一 replay v2 descriptor、同源 handler/resolver/PackageStore、
  operator roots 与 dependency/config identities。直接构造的 facade 不能通过受信断言。
- `invoke_typed(Text2EnvReplayInput, run_id=UUID4)` 复用 Registry 的 controlled intent → Invocation →
  terminal/event lifecycle；同输入、同 dependency 与同 descriptor identity 返回原 terminal，不再次调用
  handler。UUID 版本错误、ordinary-run 占用、不同输入、依赖漂移、pending lifecycle、异 CAS 或组装漂移
  都映射为稳定 application 错误。
- factory 现在只接受空 root，或只含 exact SQLite authority 与三个 replay-owned work directory 的合法
  非空 root；未知成员、符号链接与成员类型错误均拒绝。失败清理只删除本次新建成员，绝不清空既有
  authority。同 state root 的 factory 组装由 canonical sibling lock 线性化；确定性并发用例证明第二个
  factory 在 publication 失败时不会删除首个成功 factory 的数据库。损坏 SQLite 的底层异常统一封装为
  `ReplayApplicationConfigurationError` 并保留原 cause。测试在同实例、新 factory 实例和独立 Python
  进程中恢复同一 terminal，受控外部
  runtime/media fixture 总计只执行一次；这不冒充真实 RoboTwin/SAPIEN、Genesis 或真机运行。
- ReplayApplication 与资格组装专项为 `103 passed`；该模块 429 statements / 130 branches 均为 100%。
  compile application、Golden compile 与 compile qualification 回归为 `236 passed`。checked compile
  qualification 通过；checked replay qualification 唯一按预期以
  `implementation_file_mismatch: self_improving/harness/replay_application.py` 拒绝。P3a 未改 qualification
  三份文档或 expected hash。
- 基于固定实现 `8e45bd9d755ca68001f3372286cbaff265bbc461`，官方 generator 随后在全新外部
  bundle/scratch/CAS 与临时 delegated scope 中真实执行 direct/kernel 两条 0/900/120/120/12：两者
  均单 attempt succeeded，物理 `pass`、120/100/114 顺序媒体、23-member runtime assets。P3b tracer
  随后暴露并修复 typed output 的 CAS 身份重复，最终重新资格化 closure 为 63 refs；把 67 个 CAS 对象
  逐一按摘要复制到另一个全新 CAS 后，strict verifier/loader 均为 `pass`。
  三份 generator 原始文档及 49-file implementation identity 已独立安装；详见
  `docs/evidence/replay-qualification-refresh-p3a-20260910.md`。本结论仍不覆盖 validate、Genesis、
  promotion 或真机。

## 2026-09-10 P2 GoldenRun.submit compile-only 纵切

- 从固定绿色起点 `e67b656ea97044da5b09ee8323c48e752cd88c76` 建立独立 worktree，先以公共
  `GoldenRunHarness.submit` tracer 冻结 revision 0→1：真实调用 checked qualified
  `text2env.compile@1.0.0` 的 production `CompileApplication`，返回 terminal OperationSnapshot，随后
  从 CAS 重建 ToolResult、唯一 StateDelta、workflow receipt 与 resulting trusted state，并在重启后
  不重执行已提交 child。
- actor-neutral catalog kernel 与 compile-owned binding/adapter/policy 已分离。generic factory 不导入或
  硬编码 `CompileApplication`；compile evidence-policy digest 使用独立稳定源码闭包，所以后续新增
  replay binding 不会只因修改中央 catalog 而改变既有 compile operation 的 frozen policy identity。
  public binding 只暴露只读 Protocol，factory 返回非 dataclass 的 opaque concrete；catalog 消费时重验
  skill/policy/application/input model/live descriptor/child authority。policy 只规范化 parameters，完整
  `RunCommandRequest` 的 command/CAS 身份由 aggregate 原样保留。
- operation SQLite authority 显式持久化 `skill_ref`，lookup/唯一域改为
  `(workflow_run_id, skill_ref, idempotency_key)`，decode 再与 OperationSnapshot 交叉对账；同一 workflow
  的不同 exact Skill 可复用同一 key，同一 Skill/key 的不同 command 仍拒绝。
- `ToolResultV2.state_delta` 只保存 `ArtifactRef | None`，与 workflow receipt 指向同一 CAS delta；读路径
  从 ToolResult 的 ref 解析一次，重算 delta digest，再校验 base state、trusted receipt、有效时间、
  mutations 与 resulting state。发布顺序保持无环：command/qualified execution → child receipt → delta →
  resulting state → ToolResult → workflow receipt → SQLite 原子 terminal/head。
- 四个公开恢复窗口已固定：parent reservation 后、child intent 前可安全继续；intent-only 和
  invocation-only 因无法证明 handler 外部副作用边界而保守保持 running；child terminal 后、parent
  commit 前可只重建 CAS closure 并提交父 head，不再次调用 handler。这里只声明持久边界已证明的恢复
  语义，不扩大为跨任意外部副作用的 exactly-once。
- focused：`test_golden_run.py` + `test_golden_submit.py` + `test_schema_catalog.py` 为 `194 passed`；
  7 个相关模块共 1,386 statements / 402 branches，语句与分支均为 100%。从 repo root 以
  `PYTHONPATH=. python -m script.export_harness_schemas --check` 验证 39 份 Harness schema snapshot；
  direct-script 回归另行证明入口不会解析到其他 checkout。compile
  checked qualification、replay checked qualification 与 production compile application 三个固定身份
  节点为 `3 passed`。Harness 全套为 `2609 passed, 20 skipped`，完整 root 为
  `3622 passed, 20 skipped`。Dashboard 三个约定入口仍返回 HTTP 404，无法同步控制面。
- 明确边界：本切片没有接 replay/observe/validate dispatch、MCP、System 2 model loop、Genesis、
  promotion、VLM fallback 或真机；compile 的 succeeded 也不等于物理 validation 或 publishable。

## 2026-09-10 root demo 兼容失败修复

- 固定起点 `461b05d9efea11803ce5f35c3d7f2a089653d54f` 的完整 root 首轮稳定得到
  `3516 passed, 20 skipped, 2 failed`。失败节点为
  `test_submit_fails_closed_when_the_terminal_run_state_cannot_be_reloaded` 与
  `test_submit_fails_closed_when_committed_history_does_not_match_run_state`；秒级复现为
  `2 failed in 0.35s`。
- 两项测试在调用 `WorkbenchCompile.submit` 之前向 `harness_run_states` 安装 trigger。P2 的
  `SQLiteRunStore` exact-layout/zero-trigger 门会在写入 Invocation 和调用 handler 之前拒绝该布局，
  所以 observed exception 已提前为 `RunPersistenceError(RunStoreCorruptionError)`，demo 只会投影为
  unavailable；旧测试不再到达其名称声明的 terminal missing/history mismatch seam。
- 修复选择更新过时测试，不改生产代码：每个测试都先穿过真实 `CompileApplication.compile`，再在其
  返回 terminal state 后、Workbench 二次读取前删除该 run 的 terminal row 或最后一个 committed
  event。第一纵切形成 `1 passed, 1 failed`，第二纵切完成后为 `2 passed`；两项后置完整性语义仍由
  公共 submit seam 直接验证，P2 的 pre-Invocation layout 拒绝保持不变，既有 Registry/RunStore
  负向用例继续保护该边界。
- focused：`tests/demo/test_harness_compile.py` 为 `192 passed`；
  `demo/harness_compile.py` 为 531/531 statements、214/214 branches，均为 100%。Harness 全套为
  `2505 passed, 20 skipped`；完整 root 不排除节点为 `3518 passed, 20 skipped in 151.50s`。
- 本切片没有稳定生产行为变化，因此 reader-facing repo-docs 无需修改；compile/replay 资格文档、
  hash、签名、settings 和 evidence 均未触碰。

## 2026-09-10 当前集成树 replay qualification 独立重签

- 固定起点 `13f654e72e0204993355fa098e240698e1d8b085`。旧 replay bundle 先以
  `implementation_file_mismatch: self_improving/harness/__init__.py` RED；同次 preflight 中
  production import tracer 已随 compile 重签恢复。
- Python 3.10.20 在全新外部 bundle/scratch/CAS 与临时 delegated scope 中执行官方 fixed
  can-on-plate。candidate-direct 和 production-kernel candidate 均单 attempt succeeded，各有 14 个
  完整 runtime events，完成 `0/900/120/120/12`。
- 两次物理 validation 都为 pass、`fail=0/not_run=0`；视频均为 120 sequential frames、100 个
  source-unique frames、114 个 decoded-unique frames。23 个 runtime asset members 全部复核；
  evidence closure manifest 含 62 refs。
- 49-file implementation SHA-256 为
  `f6bc59e628bd72ff3b5195915a6bea53c7656154d6c71bd135eb6dd39347c604`，三阶段 source snapshot
  无变化；14 个 replay-bound model↔schema exact gate 通过。66 个新 CAS 对象逐摘要复制到另一个
  fresh CAS 后，严格 verifier/load 均 pass。
- 首次 scope 包装在生成器前因换行转义失败；首次 scope 外 reload 又按设计因 operator locator
  不存在而拒绝。两次均未改资格文档，最终在活动的同名 delegated scope 完成 strict reload。
- 三个原 deselect 节点不排除为 `3 passed`；P0 replay/loader/schema 加 checked bundle 为
  `172 passed`；完整 Harness 为 `2505 passed, 20 skipped`。32-schema、Ruff、replay 相关文件
  format 与 `git diff --check` 均通过；扩大到既有 `test_golden_run.py` 整文件的 format check 在
  HEAD 与工作树都返回 1，本切片未机械重排两个 expected digest 之外的基线代码。
- 完整 root 为 `3516 passed, 20 skipped, 2 failed`。两项失败是既有 demo 负向用例先安装 SQLite
  trigger，而 P2 RunStore 当前会在 Invocation 前拒绝 trigger-bearing authority；旧断言期待后续
  terminal reload/history mismatch。本 replay 切片不改实现、不弱化测试，按实际结果保留。
- 证据：
  `docs/evidence/replay-qualification-refresh-20260910.{md,json}`。该结果不扩大为 validate v2、
  MCP、Genesis、promotion、portable install 或真机资格。

## 2026-09-10 当前集成树 compile qualification 独立重签

- 固定 distribution 为干净
  `2841d0a17c9df12c434178969544dc7ed8e87fc9`。旧 checked-in gate 先真实 RED：
  `implementation_file_mismatch: self_improving/harness/qualification.py`。
- 用 `/home/jingxiang/miniconda3/bin/python`（Python 3.13.12）在全新外部
  bundle/scratch/CAS 运行官方 generator；固定 purple pedestal、seed 77、admission date
  2026-08-31 的三轮处置为 `admitted -> reused -> reused`。同一 asset id、package、ledger；
  第二/三轮 Invocation 与 typed output 相同，第一到第二轮只改变 `asset-library-state` dependency。
- 生成器报告绑定当前 11 个实现文件，implementation SHA-256 为
  `0394575ff26f8da601507416573656b3fb04d7ca06fc45ff3f8195005f470b7f`；执行前后 source
  snapshot 相同。只读 verifier 与全新外部 CAS strict loader 均 pass，生成的三件套逐字安装后
  checked-in loader gate 为 `1 passed`。
- compile qualification 两个专项文件为 `93 passed`；32 份 Harness schema snapshot、相关四个
  Python 文件的 Ruff/format 和 `git diff --check` 均通过。正式证据见
  `docs/evidence/compile-qualification-refresh-20260910.{md,json}`。
- ClawCross 与 dashboard 三个必读状态地址均返回 HTTP 404，无法同步控制面。本切片没有修改实现
  源码，没有启动 replay；static validation 仍 incomplete、资产仍 pending_settle，replay
  qualification 仍为下一独立工作。

## 2026-09-10 P0+P2 隔离集成

- 从主仓库固定 `ea26524cae7b4a18b5576150376eb6ec1ddddf7b` 新建隔离 worktree，依次合入
  `6570c82`、`b24c827`、`2ac283f`。主工作树的用户修改未被暂存、覆盖或提交；
  `docs/evidence/replay-production-qualification-20260902.md` 保持基线字节。
- 公共 root/schemas facade 保留 P0 的 lazy seam，并在 closure 外的全局映射中合并 P6 的
  `AssetSourceSnapshotManifest` / `AssetStageRequest` / `AssetStageResult` 三份 schema 与 stage 公开符号；
  P2 的 Registry/Application/RunStore/EventJournal/actor-neutral domain 实现保持不变。schema gate 为
  `verified 32 Harness schema snapshots`。
- P0 focused 在精确排除依赖 stale compile bundle 的 production import tracer 后为
  `170 passed, 1 deselected`；P2 focused 四文件为 `197 passed`。compile checked gate 按预期以
  `implementation_file_mismatch: self_improving/harness/qualification.py` 拒绝；production import
  tracer 在进入 trace 前因同一 compile qualification 原因拒绝；replay checked gate 按预期以
  `implementation_file_mismatch: self_improving/harness/__init__.py` 拒绝。
- Harness 在精确排除上述 3 个节点后为 `2502 passed, 20 skipped, 3 deselected`；
  `application.py`、`event_journal.py`、`registry.py`、`run_store.py`、`system2/domain.py`
  合计 1,447 statements / 478 branches，语句与分支覆盖均为 100%。fixed-range Ruff、format 与
  integration diff 完整性门通过。
- 无边界 `ruff check .` 仍报 2,463 个基线问题，主要位于本 fixed range 之外的历史
  `validation_evidence/`、`demo/` 与 `scene_gen/`；本集成的 28 个变更 Python 文件逐个通过
  `ruff check` 与 `ruff format --check`，未扩展修改基线文件。
- 本切片不重签、不执行真实 runtime。下一阶段必须在当前固定树上真实重新生成 compile
  与 900/120 replay 资格、严格 reload，然后不排除地跑通三个节点。当前结果不证明新的
  RoboTwin/SAPIEN、Genesis 或真机能力。

## 2026-09-10 P6 staged-loader 合入与未完成边界

- 早期固定候选 `bd61a9e1504b1462c1bb24799a38e76f5cdf26e4` 在独立 worktree 中完成 166 个 focused
  cases；三个相关生产模块合计 744 statements/260 branches，均为 100%。根套件显式排除既有 stale
  replay qualification 单项后为 `3293 passed, 20 skipped, 1 deselected`。该候选尚未合入。
- 从固定提交重建的 source/CAS stage 为 21 members、57,290,434 bytes、14 representation
  closures；真实 RoboTwin `create_actor`/SAPIEN 无渲染 probe 对 7 个模型各运行 900 steps，共 6,300
  steps，均记录 finite pose、contact 和 nonzero impulse。外部 report SHA-256 为
  `6e9ec1c6b294ab389c5579a4f9695c966dd2019ac2754130280b8ffd788cfb9e`；它明确没有执行 Genesis、
  runtime qualification、promotion 或 can-on-plate golden case。
- 主窗口在固定提交后独立复现两个 P1 阻断：`write_new_report` 的失败清理在 parent directory 被替换时
  会删除替换路径并遗留原 partial；deep verifier 可接受重新计算全部自洽 hash、但从 representation
  closure 中删去一个 GLB 引用 member 的伪造结果。两项均已冻结为下一轮 RED；修复并复审前，真实
  SAPIEN 运行只证明候选 loader/physics path 曾执行，不能升级为可信 stage gate。
- 一名 fixed-diff 只读审查 agent 被 OpenAI 安全系统拒绝。该具体审查已按仓库规则永久保留为
  blocked，没有重试、改写或重新委派；用户随后允许继续其他工作。此事件不是通过结论，也不改变
  上述两个主窗口阻断。
- 两个反例的独立 follow-up 固定提交为
  `8c1c94afae8292dc94ee1d2b08fb7fb08a94bc06`。focused 为 `183 passed, 1 skipped`，相关三个
  生产模块合计 780 statements/278 branches 均为 100%；根套件显式排除同一个 stale replay
  qualification 项后为 `3310 passed, 20 skipped, 1 deselected`。Ruff、31 份分支基线 schema snapshot 与
  diff check 通过。
- 从 `8c1c94a` 新建外部 evidence root 后重跑，stage result SHA-256 仍为
  `a2e786131bb33a3eacd7afd0403dca45a6353a2f6d54aebc100ecb19fda09f23`，stage binding 为
  `fcf73ec7851e42654a2264d818bfcc60d091ad3669429ecee7f5b7ccd7e0eb89`；真实 7×900-step report
  SHA-256 为 `c7a0ac85e1425b12041ab0d91833a0bfb15d57ba04f2b5549ff2fe9d1ecafc5d`。报告无已知本机
  absolute-path 泄漏，source/CAS/ledger 未变化。主窗口逐项复核新增 fixed diff 后未发现新的同级
  blocker，并按顺序合入为 `8bd0d33`、`5b5ae78`、`d9df27b`、`16eb232`、`52a21ac`；先前被安全
  系统拒绝的那一份审查仍不作为通过证据。
- 合并后的主分支 schema gate 为 32 snapshots；focused 为 `256 passed, 1 skipped`，四个核心模块
  845 statements/296 branches 全部 100%，Ruff 通过。完整根套件为
  `3360 passed, 20 skipped, 1 failed in 145.88s`；唯一失败仍是已登记的旧 replay qualification source
  identity，首个报错文件是用户修改的 `IMPLEMENTATION_LOG.md`，且 P0 最终仍需在新实现树上真实重签。
- 该切片仍明确不证明 can-on-plate、runtime qualification、Genesis 或 promotion；下一资产纵切是
  collision provenance/settle，不能由 exact bytes 与 ground contact 直接推断。

## 2026-09-10 P0 replay source-closure 候选

- 候选 `d664b046f7868ecce5e0ecd0fc6574d8dcc911da` 把 replay qualification 从整个 Harness 树改为
  显式实现闭包，并用 fresh-process 的真实 application/replay 装配与 qualification-generation 路径
  检查 import trace；旧 checked-in bundle 继续 fail closed，没有刷新 expected hash 或资格文档。
- 候选基于 `660ae49`，而 P1/P2 后续会继续修改 Registry/Application、schema catalog 与公共 façade，
  因此当前只作为待重放设计，不直接合入；最终必须在 P2 固定实现之后重新审查闭包并运行真实固定
  replay 后一次性重签。

## 2026-09-10 P3 同-run 链只读审计

- 第一条后续 tracer 已收敛为 `start → text2env.compile@1.0.0 → text2env.replay@1.0.0 → read`，同一
  workflow 依次推进 revision 0/1/2。replay 的 package 必须逐字来自 revision 1 的可信 state；两个
  child run 使用不同 identity，但 command、qualified execution、Invocation、RunState、ToolResult、
  StateDelta 与 operation receipt 必须形成一条连续 predecessor/CAS 链。
- P2 aggregate 内核必须以 `QualifiedApplicationAdapter` + per-Skill evidence policy 泛化；P2 可以只
  注册 compile，但不能在 submit/恢复/验证内硬编码 Text2Env 类型。P3 第一笔只增加真实 replay
  adapter，不复制旧 System2 dispatcher 分支，也不提前实现 MCP。
- fresh observation 可以与同 key 的 state upsert 共存，但只有 observation receipt、
  `ToolResult.fresh_observations` 与 StateDelta 中 `source_kind=fresh_observation` 的 key/value/source/time/
  TTL 全等时才能推进状态；仅移除“不允许重叠”规则不构成信任证明。
- 现有 portable run receipt 是证据可携带闭包，不是新机器可重新执行的完整环境包。最终 portability
  仍须屏蔽原资产根、从新随机根/新进程只用 CAS materialize 并重新执行。当前 replay v1 的 backend
  是 RoboTwin/SAPIEN，不能计作 Genesis 或 `genesis.robot_policy@1`。

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
- 当时的 20 份 Harness schema snapshot gate 全部通过；当前从 repo root 使用
  `PYTHONPATH=. python -m script.export_harness_schemas --check` 复核当前 checkout。
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

## 2026-09-09 Codex 自动化可用性预检

- 本机：`codex-cli 0.153.4`，提供 `codex mcp`、`codex exec --json` 和 `--output-schema`。
- 只读、无工具、`--ephemeral` 最小真实 Codex 调用 exit 0，在 6.22 秒内返回精确 JSON
  `{"codex_preflight":true}`；JSONL 包含 start/turn/agent-message/completion 事件。
- 此预检只证明当前身份可启动真实 Codex 非交互回合和取得 usage，不证明 MCP 工具调用、视觉理解、
  路由、Genesis 或 golden E2E。会话 identity 不写入公开进度或 dashboard。
- 官方 Codex MCP 文档确认 STDIO/Streamable HTTP、server instructions、项目级 config、tool timeout 和
  per-tool approval 配置；正式证据仍须由当前仓库 MCP server 的 negotiated clientInfo 与 tool-call
  receipts 对账。

## 2026-09-09 契约与实验设置确认

- 用户确认 S1–S6 公共 TDD seam、exact qualified Skill MCP 和 `genesis.robot_policy@1` 最终门。
- 用户允许新增 optional dependency `mcp>=2.2,<3`，但依赖只能在 P4 的正常 feature 切片中冻结；
  autoresearch 循环本身不安装或升级依赖。
- Autoresearch 已确认 36 个 routing cases、12 个 execution cases、primary
  `/metrics/closed_loop_success_rate`（higher is better），以及最多 30 次或 12 小时的首轮预算。
- 这表示允许开始 TDD，实现完成前仍不能声称 MCP、Genesis robot-policy 或 golden E2E 已通过。

## 2026-09-09 P6 只读资产修复计划 tracer

- public seam：S5 `AssetRepairApplication.plan(AssetRepairPlanRequest) -> AssetRepairPlan`；本切片不实现
  `stage` 或 `promote`。
- 独立双轴 review 先拒绝原实现：任意 caller CAS 可自报 exact、duplicate-key/non-canonical JSON 可通过、
  public plan 的 totals/disposition/next-step/followup 不自洽、Windows drive 与 `.` 可通过、missing 被错误
  导向 reacquire，且 v1 不能表达未来 full baseline。修正后的 focused RED 曾为 import error，随后在第一轮
  GREEN 后以 31 个失败暴露 canonical fixture 和旧错误预期，均保留在本次过程记录中。
- 冻结 canonical fixture：`tests/fixtures/asset_repair_tracer_inventory.json`，5,859 bytes，SHA-256
  `d19dc71323a7c94de129f5217efc158a9076fd70c762dece6b7c7395825d4afa`。两份 ledger 的 hash/bytes 与
  9/49 violation 已独立复算；主线程也对本机先前 replay materialization 的 14 份 visual/collision GLB
  重算 SHA-256/bytes，全部与 fixture 的 observed fields 一致。
- 证据限定：这些 GLB 没有 committed dataset revision，也尚未由本切片复制进 CAS；inventory 因而明确
  `byte_probe_source=local_unversioned_robotwin_assets`、`byte_probe_revision=null`。Application 只接受
  组装时显式给定的 trusted inventory ArtifactRef，并重验 exact `LocalArtifactStore`、CAS 与 canonical
  bytes；输出固定 `probe_bytes_in_cas=false`，不把本机观测冒充 portable recovery closure。
- tracer 结果：`003_plate` 为 9 条结构违规、2/2 observed digest matches；`071_can` 为 49 条结构违规、
  12/12 observed digest matches。合计 `planned_ledger_count=2`、`planned_violation_count=58`、
  `planned_representation_count=14`，下一步均为 `stage_and_rehash_observed_primary`。
- hash 相同但旧 size 错时进入 stage/rehash + remeasure；hash mismatch 或 missing 都只能
  `build_new_identity_or_retire_asset`。inventory 显式冻结来源总体 `162/4,082/1,101`，public plan 同时
  记录 source population、inventory sample、planned selection 三层 totals；只有 scope 为
  `full_baseline`、inventory totals 等于来源总体、且本次完整选择 inventory 时，才允许
  `full_baseline_evaluated=true`。把 2-entry tracer 改标签冒充全量、选择部分 inventory 或单字段篡改
  totals 都 fail closed。Pydantic document 只验证语法和本地可重算不变量；协调篡改整份 plan 仍可能
  形成内部一致文档，跨文档真实性必须由 exact trusted `AssetRepairApplication` 解析 `inventory_ref`
  重建，后续再由 S1 receipt 绑定，不能把裸 plan 当成独立证明。
- 其他攻击覆盖：untrusted/missing/non-canonical/duplicate-key inventory，非 CAS ref、错误 media/schema、
  未知/重复/乱序 asset、重复/乱序 debt detail、availability/observed identity 冲突、绝对/逃逸/反斜杠/
  Windows-drive/`.`/重复分隔符/尾随分隔符/C0/DEL/U+2028/U+2029 path，以及 line-separator 绕过
  dot-segment 检查；portable path runtime 与 JSON Schema 已用同一攻击样本对齐。另用 Node
  ECMA-262 `RegExp(..., "u")` 读取 committed schema，确认四个 Unicode line-separator 攻击均被拒绝。
- focused gate：
  `pytest -q tests/self_improving/harness/test_asset_repair.py tests/self_improving/harness/test_schema_catalog.py --cov=self_improving.harness.asset_repair --cov=self_improving.harness.schemas.asset_repair --cov-branch --cov-report=term-missing --cov-fail-under=100`
  为 `79 passed in 0.85s`，两个新增模块 statement/branch 均为 `100%`；28 份 schema snapshot、Ruff
  和 `git diff --check` 通过。Harness 全域排除已单独记录的 stale replay identity 门后为
  `2118 passed, 19 skipped in 56.89s`；根套件以同样的单项显式排除运行，为
  `3131 passed, 19 skipped in 155.31s`。本切片没有改 expected hash 或伪造重签。
- 主张边界：fixture 只覆盖 2/162 ledgers、58/4,082 violations、14/1,101 primary probes；它没有
  重放全量 scanner，也没有把资产字节入 CAS、写 ledger、枚举 loader closure、执行 settle/Genesis 或
  产生 qualification/promotion receipt。`162/4,082/1,101` 也只是 trusted inventory producer 声明，尚无
  scanner/manifest attestation，因此本切片只能算 E0 contract-tested + local-observation tracer。

## 2026-09-09 P0 readiness 修复与 clean baseline

- RED：删除 PEARL portal 后，公共 `python -m self_improving --json` 仍把它当 required module，退出 1。
- GREEN：保留历史 module identity 供旧消费者发现，但设为 `required=false`；其他 required modules
  不变。focused registry tests `3 passed`，当前共享 checkout 的真实 audit 为
  `ready=true/required_failures=[]`，portal 明确为 `missing`。
- Feature commit：`5ac9108 fix(platform): align readiness after portal removal`。
- Detached clean worktree 根套件：`3016 passed, 19 skipped, 1 failed in 143.26s`。唯一失败仍是
  `text2env.replay@1.0.0` 的 checked-in qualification source identity；clean tree 首个漂移文件是
  `self_improving/harness/__init__.py`，证明问题早于共享工作树的未提交 IMPLEMENTATION_LOG 变化。
- 原 2026-09-02 qualification settings、CAS、解释器、资产和媒体工具仍在本机，但其 delegated cgroup
  已不存在。不能复写三份资格文档或更新期望 hash；最终在实现树稳定后用新隔离 scope 跑真实固定案例
  并重签，或者显式撤销旧 Skill 资格。

## 2026-09-09 三方整合来源台账

- 新建 `docs/integration-provenance/`，以功能级 `integration_id` 分别记录 Bingsheng、Gujie、Yuxin
  的原始来源、第三方上游、Harness 整合责任、固定 commit、dirty snapshot、目标路径和验证状态。
- 初始台账只把当前 Harness/合同和 Yuxin 历史 active tree 标为 `integrated`；Gujie x2env/Genesis
  以及 Yuxin 面向新 Skills 的复用能力仍为 `candidate`。
- 当前没有任何新条目标为 `runtime_pass`：资产 ledger 债务、Codex/MCP 同-run 闭环和当前 Harness
  内的真实 Genesis 最终能力门均尚未完成。
- 根 `AGENTS.md` 已要求后续每个来源整合切片与台账更新同一提交，防止最终集中补写造成来源漂移。
- 三份人员档案已扩展为模块/功能矩阵：Bingsheng 细分 Text2Env 加固、Harness schema/CAS/Registry、
  compile/replay/validate、可信状态、Golden workflow、资产修复、Codex/MCP 与总体整合；Gujie 细分
  x2env/URDF/SimFoundry/Genesis adapter；Yuxin 细分检索、筛选、转换、ledger、catalog 与跨仿真验证。

## 2026-09-09 P1 S1 workflow-start 纵切

- 首个 RED：公共测试导入 `self_improving.harness.golden_run.GoldenRunHarness` 时得到
  `ModuleNotFoundError`；随后只实现 S1 的 `start()`，没有预先实现 submit/read/dispatch。
- 安全边界：调用方提交的是按 SHA-256 绑定的 `harness.world_fact_evidence.v1` 与 registry snapshot，
  不能直接提交或自称 `TrustedWorldState`；Harness 解析 canonical bytes、校验 `user_input` provenance
  和 `request.*` namespace 后自行构建可信状态。
- 输出同时保留逻辑 `state_sha256` 与状态 JSON 的 CAS `state_ref`，并产生不含 planner/provider/model
  身份字段的 `harness.workflow_start_receipt.v1` 及 revision 0 父快照。
- schema catalog RED：新增测试要求 23 个精确 schema 时为 `5 failed, 1 passed`；接线并导出 3 份新
  schema snapshot 后变绿。
- focused 验证：`19 passed in 0.53s`；`golden_run.py` 52/52 statements、8/8 branches，
  `schemas/workflow.py` 100/100 statements、14/14 branches，均为 100%。
- 攻击测试覆盖非 canonical JSON、伪装 fresh observation、错误 namespace、非 UTC clock、非 CAS/错
  digest URI、错误 media/schema、重复或乱序 initial evidence，以及非法 snapshot 时间序。
- 本切片只证明可信父 workflow 的启动证据链；尚不证明幂等恢复、compile/replay/validate 调度、MCP
  transport、Genesis 或最终晋升。

## 2026-09-09 P1 durable start/idempotency 纵切

- RED：跨 Harness 实例重复 `start()` 时原实现没有 durable idempotency authority，且公共
  `GoldenRunConflictError` 不存在；随后以 `(principal_id, idempotency_key)` 为唯一键，在 SQLite
  `BEGIN IMMEDIATE` 事务内执行 query-or-create。
- 相同 canonical request digest 返回原 snapshot，测试用会主动失败的 clock/UUID factory 证明 retry
  没有重新取时钟或分配 identity；同 key 异 request 在新 CAS/identity 前以
  `HARN_IDEMPOTENCY_CONFLICT` 拒绝。
- 恢复路径重验 canonical SQLite snapshot、registry/state/receipt/input CAS closure、逻辑 state digest
  与 start receipt/request binding；非 canonical DB bytes、跨记录 swap、CAS bytes 漂移和不一致 revision
  均 fail closed。workflow/operation identities 同步收紧为 UUID4。
- `GoldenRunHarness` 只接受 exact `LocalArtifactStore`，并已从 `self_improving.harness` 公共 façade 导出；
  protocol lookalike 不能绕过 CAS authority。
- focused 验证：workflow + schema catalog `29 passed in 0.68s`；`golden_run.py` 112/112 statements、
  24/24 branches，`schemas/workflow.py` 99/99 statements、14/14 branches，均为 100%；23 份 schema
  snapshot check 与 Ruff 通过。
- 审阅仍发现两个下一切片 blocker：RegistrySnapshot 目前只被 resolve、未 strict parse/资格闭包验证；
  start receipt 还没有 canonical request ArtifactRef，且 RunSnapshot receipt-head 仍锁死为 start receipt。
  因此本切片不声称完整可重放 workflow 或 exact qualified dispatch。

## 2026-09-09 P1 Registry/request 审计闭包纵切

- RED：把内容为非 JSON 的 CAS 对象伪标为 `harness.registry_snapshot.v1` 时，旧 `start()` 只做
  artifact resolve 便成功；同时 start receipt 只保存 request digest 而无 request ArtifactRef，且
  `RunSnapshot.receipt_head` 永久限定为 start receipt schema。后续契约复核又以 5 个 schema-catalog
  失败锁定 qualification report 已被 Registry 引用、却没有公共 schema identity/snapshot 的遗漏。
- GREEN：新增正式 `RegistrySnapshot`，只含按 MCP tool name 排序且 skill/tool 唯一的 qualified entries；
  每项 strict-canonical 解析 descriptor、qualification、qualification report，并交叉绑定 exact skill、
  MCP tool、implementation、case、regression command、status 与 schema catalog。缺 CAS、坏 canonical
  bytes、伪造版本绑定和未知 schema 分别给出稳定的 unavailable/input-invalid/unqualified/drift 错误。
- 当前真实 fixture 只列 `text2env.compile@1.0.0`；旧 replay qualification 有 source drift，目标
  text2env v2、image/video 和 Genesis Skills 尚未 qualification，因此没有被提前列入可用目录。
- canonical `RunStartRequest` 现在先入 CAS；start receipt 和 parent snapshot 都保存其 ArtifactRef，恢复时
  重算并核对 request、state、registry 与 receipt 全闭包。receipt head 接受精确
  `harness.workflow_<name>_receipt.v1` family，为后续 operation/transition 留出版本化演进空间。
- 验证：`48 passed in 0.94s`；`golden_run.py` 147/147 statements、28/28 branches，
  `schemas/registry_snapshot.py` 45/45 statements、12/12 branches，`schemas/workflow.py` 112/112
  statements、20/20 branches，公共 qualification-report schema projection 7/7 statements，均为
  100%；25 份 Harness schema snapshot、Ruff 与 `git diff --check` 全部通过。projection 没有修改
  已合格 compile 所绑定的 `qualification.py` 源码，compile qualification loader 仍通过。Harness 全域
  排除已单独记录的 stale replay qualification 身份门后为
  `2045 passed, 19 skipped, 1 deselected in 51.35s`。
- 限定：该闭包能拒绝不一致或损坏的调用方快照，但还没有与运行中 Registry/handler 的安装身份取交集；
  P2/P4 必须补这层生产 trust root 后，才可把 snapshot entry 暴露成 exact callable MCP Skill。

## 2026-09-09 P4 官方 MCP 2.2 隔离 spike

- 项目环境未安装 MCP；在隔离临时环境验证 `mcp==2.2.0`，没有修改项目依赖或 lock。官方 v2
  low-level `Server` 与 `Client` 的进程内和真实 stdio 两条最小路径均协商协议 `2026-07-28`，并完成
  exact tool list/call；这只证明 SDK seam，不证明项目 Harness MCP 已实现。
- 选择 low-level Server：adapter 只做 SDK type 与 S1 public type 映射，所有 schema/canonical/receipt/
  idempotency 校验仍由 Harness 负责；不提供 generic `skill.invoke`、alias、latest 或版本范围，也不在
  MCP server 内调用 Codex。
- SDK server 只广告 input schema，不替项目执行入参验证；timeout/cancel 后 durable operation 也不能
  假定回滚，caller 必须凭 command/idempotency identity 从 workflow resource 恢复。
- `uv lock --check` 当前已因历史 metadata 漂移失败，CI 的 editable pip 安装也没有消费 lock。P4 加入
  optional `mcp>=2.2,<3` 时必须同时更新 frozen runtime/CI lane，并记录 mcp、mcp-types 与 negotiated
  protocol 的精确身份；在 S1 `submit/read` 和真实 Registry trust root 完成前不写假 adapter。

## 2026-09-09 P1 durable workflow read/store 纵切

- public seam 保持 S1：新增 strict `RunReadRequest(schema_version, principal_id, workflow_run_id UUID4)`，
  `GoldenRunHarness.read(request) -> RunSnapshot`，以及调用方显式构造/注入的
  `SQLiteGoldenRunStore(path, busy_timeout_ms=5000)`。Harness 不再从 CAS root 隐式创建私有数据库；
  当前 `read` 明确只恢复 revision-0 start head；原有 `start`、跨实例幂等和 request/receipt/CAS
  复核语义保持通过，未来 operation head 不在本切片能力声明内。
- TDD RED 依次为：`RunReadRequest` import 不存在；公开 SQLite store module 不存在；Harness 不接受
  `workflow_store`；有效 64 位 SQLite start-request identity 被篡改后 `read` 仍静默成功；旧私有四列表
  无法由新 store 读取；旧 logical request digest 被迁移丢弃；只篡改 idempotency lookup key 能创建第二个
  workflow；两个线程并发 bootstrap 新库偶发 `database is locked`。这些 RED 分别由双 request identity
  binding、单一旧布局事务迁移、lookup metadata 反向绑定和无竞态初始化修复；receipt 中的
  domain-separated logical `request_sha256` 保持原定义。
- SQLite authority 不在并发 bootstrap 中切换 `journal_mode`；新库沿用 SQLite 默认 rollback journal，
  同时使用 `synchronous=FULL`、`BEGIN IMMEDIATE`、snapshot canonical bytes 与独立 payload checksum。
  `workflow_run_id` 为主键，`(principal_id, idempotency_key)` 与 canonical start-request digest 分别唯一。
  已知旧私有四列表可事务迁移并保留 logical digest；未知 layout、坏旧行、principal 不一致、合法 UUID
  碰撞、非 canonical/非 BLOB/不可解析 snapshot、无效或不匹配 checksum、row/snapshot identity swap、
  request/idempotency metadata 篡改和损坏数据库均 fail closed。
- 后续主审以同名七列表攻击发现：原构造器只比列名，会接受缺主键、缺任一 UNIQUE、可空列、错误
  declared type/affinity、默认值、隐藏生成列和附加索引；正确列/索引还可附带 `AFTER INSERT` trigger，
  让首个 `start` 返回内存成功而持久 authority 已被改写。新增 RED 覆盖上述结构，以及 duplicate/
  partial/expression/collation index、foreign key、STRICT/WITHOUT ROWID 和构造后插入/删除 trigger。
- 表级 `PRIMARY KEY/UNIQUE ... ON CONFLICT REPLACE` 在 PRAGMA 中与默认 ABORT 同形，却会静默替换
  authority；三类约束的 RED 均复现原构造器接受。布局检查现在忽略 SQL 引号/注释后拒绝显式
  `ON CONFLICT` policy（包括 `ON/**/CONFLICT`），生产 start 与 legacy migration 的写入同时固定为
  `INSERT OR ABORT`，避免表级 policy 改写冲突语义。
- GREEN 以 `table_xinfo` 精确验证 cid/name/type/notnull/default/pk/hidden，以 `index_list` +
  `index_xinfo` 验证 unique/origin/partial、顺序 key、collation、expression/extra key 与辅助 rowid；比较
  语义而不写死 SQLite autoindex 名。同时验证 `table_list` flags、零 foreign key/trigger；已知四列
  legacy 先验完整旧 layout，迁移后再验完整 current layout。INSERT 后在同一事务重读并对账全部
  authority metadata，因而晚注入 trigger 也不能造成首调假成功。
- `read` 只按 `(principal_id, workflow_run_id)` 返回，因此不存在与其他 principal 使用同 UUID 时的
  可观察差异；missing 与 wrong-principal 都给出同一个
  `GoldenRunNotFoundError(code=HARN_WORKFLOW_NOT_FOUND)`。恢复后重新解析 canonical start request，
  再复核 registry、trusted state、receipt、初始 evidence 与全部 CAS 内容闭包；删除 start-request CAS
  对象会给出 `HARN_PERSISTENCE_CONFLICT`，不会返回仅有 SQLite 的快照。
- 同一测试先用第二个 Harness/ArtifactStore/SQLite store 实例恢复，再启动真实 Python 子进程从相同
  SQLite 与 CAS 调用公开 `read`，两次结果都与起始快照完全相同；显式数据库位于 CAS 外，且确认
  Harness 没有在 CAS root 偷建 `golden-workflows.sqlite3`。另有 40 个 fresh database × 2 线程的有界
  并发 constructor 测试，以及两个 Harness 同时 start 同一 request 只落一个 aggregate 的测试。
- focused gate：
  `pytest -q tests/self_improving/harness/test_golden_run.py tests/self_improving/harness/test_schema_catalog.py --cov=self_improving.harness.golden_run --cov=self_improving.harness.golden_store --cov=self_improving.harness.schemas.workflow --cov-branch --cov-fail-under=100`
  为 `89 passed`；三个模块合计 440 statements、98 branches，均为 `100%`。29 份 Harness JSON
  Schema snapshot check 与 Ruff 通过。
- Harness 全域显式排除已登记的 `text2env.replay@1.0.0` qualification source-identity 单项后为
  `2141 passed, 19 skipped, 1 deselected in 53.37s`；本修复不改 expected hash 或伪造重签。
- 根套件保留同一个显式排除项后为 `3154 passed, 19 skipped, 1 deselected in 148.28s`；没有第二个
  回归失败。本结果不是 Genesis runtime 验证，也不替代最终资格重签。
- 早期 e1cc853 Standards/spec reviewer 曾在 Python 3.11.15 隔离环境复跑 focused `65 passed`、
  405 statements/86 branches 100%，并补做 40 个 fresh database × 2 独立进程 constructor 攻击；
  随后主审仍发现上述 layout/trigger blocker，因此旧 `NO BLOCKER` 不能作为最终审阅结论。当前修复由
  主线程复跑 focused 89 tests 并检查结构/trigger/post-insert 路径，未发现新的同级 blocker。
- 边界：本切片没有定义 `RunCommandRequest`、`OperationSnapshot`、`submit`、handler dispatch、MCP、
  Genesis 或 promotion，也没有把 caller 提供的 RegistrySnapshot 提升为生产 trust root；这些仍属于
  P1 后续与 P2/P4。纯持久化切片不产生真实仿真或真机能力主张。

## 2026-09-10 P2 controlled child-run identity 纵切

- 首轮 RED 固定 caller-reserved UUID4 的身份语义：相同 run id 但不同 canonical input、依赖树、
  descriptor、implementation 或 qualification identity 必须拒绝；只完成 preflight 的 intent 与已验证
  Invocation 使用不同 phase，且输入只 strict normalize 一次，避免可变 Mapping 在摘要与执行之间改变。
- Registry/Application 新增受控 child-run seam。validated intent、Invocation、terminal 和事件链由同一
  SQLite authority 绑定；同 intent 并发只允许一个 handler 执行，精确 terminal 可恢复，只有 invocation
  而没有 terminal 时返回稳定 pending，不把未知外部副作用冒充成可重试成功。普通 run UUID 再用于
  controlled 调用时稳定报 identity 冲突；只有在完整 ordinary lifecycle 已验证后才做该分类，损坏的
  metadata、payload anchor 或事件链仍报 persistence error。
- SQLiteRunStore 对 controlled intent、Invocation、terminal 采用一次只读事务的 lifecycle snapshot，
  并精确校验 current/known-legacy table、index、constraint、trigger、foreign-key、conflict policy、
  metadata 与 payload anchor。SQLiteEventJournal 同样在 publish 前后校验布局、插入后同事务重读 exact
  envelope/metadata，并确认前序链未改变；fresh-path 的 RunStore/EventJournal/双 Application 并发构造
  已有有界用例。
- 固定复审点 `cb3235f` 的 focused 四文件为 `192 passed`；Harness 明确排除 compile/replay 两份待统一
  重签的 qualification identity case 后为 `2294 passed, 19 skipped, 2 deselected`，五个受影响核心模块
  语句/分支为 100%。补齐普通生命周期占用与损坏不重分类用例后的本提交为 focused `197 passed`；
  相同 Harness 门为 `2299 passed, 19 skipped, 2 deselected`，五模块合计 1,592 statements、484 branches，
  均为 100%；Ruff、format 与 diff 完整性门均通过。
- 两份 qualification 尚未在本纵切重签；它们必须等待相关实现树稳定后用既有真实固定案例、settings
  与 CAS 统一生成，不能仅修改 expected hash。本结果也没有实现 `RunCommandRequest`、
  `OperationSnapshot`、`GoldenRunHarness.submit`、workflow operation journal、replay/validate dispatch、
  MCP transport、Genesis runtime 或 promotion，因此不声称 S1 submit 闭环或 golden E2E 已完成。
## 2026-09-10 P0 replay qualification 显式闭包修复

- RED：fresh-process import trace 原先只统计 `harness/`，遗漏 `self_improving/__init__.py` 实际加载的
  `self_improving/registry.py`；扩展到整个 `self_improving/` 后以唯一额外路径精确失败。该顶层 registry
  已进入 replay implementation closure，import-only trace 不再过滤它。
- RED：保持 distribution root 不变，在第二轮最后一个成员已打开时分别把 `self_improving/` 和
  `self_improving/harness/schemas/` 换成字节完全相同的完整副本，两轮实现仍返回旧摘要。GREEN 现在从
  绝对 root 独立打开三次，每次逐组件 nofollow 重走每条目录链并流式重读成员；逐项比较目录与文件
  identity、bytes、SHA-256，并最终再次重开 root。换根、中间目录替换、member rename/symlink、读取中
  原地变化、父级逃逸和非普通文件均 fail closed。
- fresh-process production tracer 不再替换 `_execute_fixed_case`：真实 compile application/handler、
  direct replay handler 与 Registry candidate 都执行，只替代外部 RoboTwin runtime、native sandbox 和
  media decoder 边界。实际加载的 `assets.py`、compile/replay handler、Harness Registry 均在显式
  implementation closure；若 ledger contract 动态 import，则必须落在独立 tree-hash 绑定的 ledger root。
- 14 个 replay 绑定 schema 的 `schema_id + model` 现由 frozen `REPLAY_SCHEMA_BINDINGS` 单表定义；
  implementation snapshot paths 与 exact model/snapshot gate 都从该表派生。测试保留独立 14-ID literal
  并要求模型集合、路径集合和 implementation closure 精确一致；qualification-report projection 仍是
  真实依赖。任一 snapshot 内容变化或缺失会在生成执行或 loader 解析前拒绝。
- package façade 兼容不变：root/handlers/schemas 为稳定惰性 seam；全局公开 export/schema family 目录
  仍在 replay closure 外；`SCHEMA_MODELS.copy()`、左右 `|` 与不可赋值行为保持。
- focused replay/loader/schema：`171 passed`；12 个变更模块合计 1,807 statements、372 branches，
  statement/branch coverage 均为 100%。29 份 Harness schema snapshot、Ruff、`git diff --check` 与
  checked compile qualification 均通过。
- root：`3206 passed, 19 skipped, 1 failed in 153.28s`。唯一失败仍是旧 checked replay bundle 对当前
  `harness/__init__.py` 的 `implementation_file_mismatch`；这是预期的 fail-closed 结果，不是重签。
- 在该独立 P0 候选上，reader-facing repo-docs 统一了时态和当时的 29-schema 计数：
  2026-09-02 bundle/真实 900/120 是固定旧
  源码下的历史证据；当前 loader 会拒绝，新的 current qualification 尚未执行。历史 evidence 页恢复
  原字节，当前失效和未重签状态记录在独立 `replay-qualification-status-20260910.md`。
- 边界：该独立候选未改 qualification 三份文档或 expected hash，未执行真实 replay
  requalification，未声称当前 simulator、Genesis 或真机能力。P2 当时尚会修改 closure 内
  `registry.py` / `application.py`；当前集成状态及后续重签门见本页顶部的 P0+P2 集成记录。
## 2026-09-10 Image2Env / Video2Env contract tranche

- Source audit：仓库内 `worktree/gujie@a25bc58853db1988aafafe88b544e0573eacbb3c` 是旧且
  prunable 的记录；实际多模态/Genesis 固定来源为独立仓库
  `/home/jingxiang/gujie/gen-env@eb0b710581fd7794bc01b447b0f77cb871c8a711`，重点祖先
  `9e8d28c` / `a8ced27`。来源工作树 dirty 项未计入固定 ref。
- Source behavior：已核 `probe_media` 的 input hash、image decode/dimensions、video ffprobe 全计帧、
  ffmpeg framemd5、FPS/duration/total/unique 和 15-frame sample；并核 `TaskOutput`、URDF/scene import、
  position/stabilization、Genesis step/contact/penetration、baseline/half-dt 的入口和来源测试/证据边界。
- RED 1：新 public contract 测试首次收集失败，`self_improving.harness.x2env_contracts` 不存在。
- GREEN 1：新增 Image/Video source identity、asset acquisition decision、candidate environment package、
  两组 compile/replay/validate strict models 和六项 guarded contract descriptor metadata；focused 首轮
  `13 passed`。
- RED 2：schema catalog 尚为 39 份，新增 16 个预期 `$id` 时 `6 failed, 2 passed`。
- GREEN 2：catalog 接入并导出 55 份 committed snapshots；补齐跨字段和跨模态攻击用例后，schema +
  contract focused 为 `24 passed`，新增两个模块 438 statements / 110 branches 均为 100%。
- Contract boundaries：compile 固定 `simulator_executed=false/publishable=false`；replay 要求真实
  collision/nonzero steps/顺序媒体，robot-policy 另要 probe+trajectory；validate 是唯一
  publishability seam。Gujie legacy 输出适配后仍固定 candidate，不导入旧 runtime/pass 状态。
- Artifacts：`docs/contracts/X2ENV_IMAGE_VIDEO_SKILL_CONTRACT_V1.md`、
  `docs/contracts/GUJIE_GENENV_ADAPTER_MAPPING_V1.md`、
  `X2ENV_IMAGE_VIDEO_IMPLEMENTATION_PLAN.md` 与两份 committed raw media mapping fixtures。
- Verification：55 份 committed schema snapshot check 通过；root 为
  `3692 passed, 20 skipped in 173.34s`；该 contract-only tranche 未改写既有资格包。
- 限定：该切片没有 handler/application、qualification bundle、Registry/System2/Golden executable entry、
  MCP tool、真实 Genesis replay/validate 或 promotion；runtime 状态仍为未实现。

# 2026-09-11 X2 typed exact-reuse compile evidence

- TDD 链：`327dd80`（12 个 typed schema RED）→ `33d0695`（schema GREEN）→ `fd3d0e9`
  （deep verifier RED）→ `314639c`（首版 GREEN）→ `c2c6f57`（完整 catalog/static 反例）→
  `7a992f9`（完整 CAS/static 修复）→ `f1aa527`（qualification 语义反例）→ `605f030`
  （最终 qualification 修复）。每个行为切片独立提交。
- 新增 proposal、portable catalog、representation、qualification、selection、SceneIR、TaskIR、scene
  closure、binding、Genesis runtime lock、static validation 和 compile receipt 共 12 个 public
  self-hashed schema；公共 snapshot 72→84，Draft 2020-12 standalone/catalog/exporter 全部一致。
- `ExactReuseCompileClosureVerifier` 只接受 image initial attempt + exact_only +
  `genesis.rigid_scene@1` + harness-native exact reuse。它纯读解引用完整 source/proposal/catalog/output
  graph；所有 available catalog entry（含未选项）的 representation、member、qualification 与 check
  evidence 都须在同 CAS 中存在，并绑定 trusted qualification allowlist、runtime lock、profile、issuer、
  subject 与 loader closure。qualification 恰含 `backend_load`/`collision_probe`，每项 evidence 均同时
  包含自身 representation 与 runtime lock。
- Static validation 必须恰含四项及其 exact evidence set：`asset_closure_complete={selection,closure}`、
  `binding_complete={binding}`、`runtime_lock_bound={runtime}`、
  `scene_task_consistent={SceneIR,TaskIR}`。noop、缺项、多项、错 evidence 与额外无关 evidence 均拒绝。
- 首轮集中验收真实发现两类 blocker：未选 catalog 坏 ref 可通过、noop/pass static validation 可通过；
  修复后第二轮又发现未选 qualification 的 noop checks 或正确 code+无关 evidence 可通过。上述失败均
  保留为 committed RED，没有从结果中删除。最终 focused 9 pass；核心 219 statements/52 branches
  100%；相邻 schema/x2env/image regression 79 pass；完整 root 为 4098 pass/20 skip。
- 最终两轴独立验收：catalog 矩阵 15 场景，双 available-entry 正向消费 2 representations/2
  qualifications，31 CAS files/51,900 bytes；14 个 missing/tamper/trust/runtime/profile/issuer/subject/
  check 反例全部拒绝且 verifier 零写。static 轴 20 CAS files/28,236 bytes，84 ArtifactRef occurrences/20
  unique 全部 bytes/SHA 可解，五种 static 反例拒绝且零写。catalog summary SHA-256
  `4d0ce6634bdef60873bf6644192a52211d0669d0dc615b3fbbb196482c3cc032`。
- 边界：没有 handler/application、production asset issuer/qualification、真实 Genesis/GPU、replay、
  publishability、MCP exposure 或 robot policy。测试 fixture 中的 qualification 只用于验证 typed/deep
  consumer，不能被称为真实资产物理资格。
