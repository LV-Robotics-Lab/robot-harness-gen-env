# 通用 Genesis 实验管线实施与验收

本轮有限单刚体实验版本已完成，暂停供审阅；不等于原长期 Golden E2E 全部完成。
进度事实源：[任务与结果账本](../../self_improving/golden_e2e_progress/README.md)。
旧 normal、中断 repair、正式库及用户主工作树没有改写。

## 唯一用户入口

用户只操作 Harness。现有 `LimitedImagePreviewController` 实验分支调用 `ExperimentService`，
复用 GoldenRunHarness、Registry、SQLite journal 和 CAS。内部 CodexBackend 负责解释、
视觉判断与修订建议；Harness 执行、校验、推进状态、限制重试。没有外部 Codex 主控的第二套流程。

在集成工作树及声明环境中提交新请求：

```bash
.venv/bin/python -m self_improving.harness.experiment_cli \
  --deployment /home/jingxiang/bingsheng/generic-experiment-v2-20260913.1QrGYD/deployment-v3.json \
  submit --text 'Generate one red block at the center of the table.' \
  --idempotency-key NEW_UNIQUE_KEY
```

支持 `--text-file`、可重复的 `--image ABSOLUTE_IMAGE`、`--video ABSOLUTE_VIDEO` 及组合。
后续命令为 `status --workflow-id UUID`、`package --workflow-id UUID --output NEW_DIRECTORY`。
配置决定可用资产来源；prompt 不得切换 release/development 权限。`resume` 不绕过源码身份或
未知活动 operation 保护；旧运行不能用新源码冒充原 authority。用户无需配置 Codex/MCP。

实验 Skill 为 `experiment.<stage>@1.0.0`，**尚未投影为旧 qualified MCP namespace**。
ToolResult、状态增量、receipt 在同一父 workflow 下提交。CLI `passed` 是阶段序列完成；
父 snapshot 仍 `active`、`parent_terminal=false`，没有晋升或发布。新命名空间 MCP、父终态收口
和正式资格没有被完成，本轮不创建伪资格。

## 每阶段合同与主要代码

| 阶段 | 契约/spec | 主要代码 |
| --- | --- | --- |
| 三模态 → 共享意图 | [输入合同](../../self_improving/harness/EXPERIMENT_INPUTS_CONTRACT.md) | [输入 adapter](../../self_improving/harness/experiment_inputs.py) |
| Codex 理解、视觉、布局建议 | [模型合同](../../self_improving/harness/EXPERIMENT_MODEL_STAGES_CONTRACT.md) | [模型阶段](../../self_improving/harness/experiment_model_stages.py)、[内部调用](../../self_improving/harness/codex_experiment_proposal.py) |
| local/web/generated、新版本 | [资产合同](../../self_improving/harness/EXPERIMENT_ASSETS_CONTRACT.md) | [获取与登记](../../self_improving/harness/experiment_assets.py) |
| 规范化、场景组装 | [编译合同](../../self_improving/harness/EXPERIMENT_SCENE_COMPILE_CONTRACT.md) | [共享编译](../../self_improving/harness/experiment_scene_compile.py) |
| replay/observe/validate | [运行合同](../../self_improving/harness/EXPERIMENT_RUNTIME_CONTRACT.md) | [运行 adapter](../../self_improving/harness/experiment_runtime.py) |
| 同 run 证据链与权限 | [服务合同](../../self_improving/harness/EXPERIMENT_SERVICE_CONTRACT.md) | [服务](../../self_improving/harness/experiment_service.py)、[policy](../../self_improving/harness/experiment_skill_policy.py)、[开发执行](../../self_improving/harness/development_execution.py) |
| 有界 fallback、用户入口 | [CLI 合同](../../self_improving/harness/EXPERIMENT_CLI_CONTRACT.md) | [控制器](../../self_improving/harness/experiment_controller.py)、[CLI](../../self_improving/harness/experiment_cli.py) |
| 全证据包与 loader | [包合同](../../self_improving/harness/EXPERIMENT_PACKAGE_CONTRACT.md) | [journal 导出](../../self_improving/harness/experiment_package.py)、[包入口](../../self_improving/harness/portable_genesis_package.py) |

按 TDD 纵向实现；codebase-design/domain-modeling 约束来源与选择策略、建议与权威、开发执行与资格
三个边界。固定 Gujie 来源、第三方和集成所有权分别记录于[来源台账](../../docs/integration-provenance/LEDGER.md)，
没有纳入其 dirty bytes。逐功能 Git 提交见台账，不将组件测试冒充真实仿真。

## 小型真实案例与失败

下表证据相对根为 `/home/jingxiang/bingsheng/generic-experiment-v2-20260913.1QrGYD`。
成功案例均实际调用内部 Codex 和 Genesis，最终物理 34/34、visual/validate passed。
首条耗时为 journal 跨度，其他为 submit wall；不含导出和后置 copy-run，不汇成一个成功率。

| 输入/来源/目的 | 固定源码 | workflow | 结果与耗时 | 证据 |
| --- | --- | --- | --- | --- |
| text/generated | `50eb2ec` | `6c84535b-7c0f-467a-a182-2bcffabd3cde` | 7 阶段；154.561299s journal 跨度 | `/home/jingxiang/bingsheng/generic-experiment-20260913.ZGPNcg/text-package` |
| image/local 首试 | `e2391bd` | `6e8b3c50-1db4-4392-aac5-bda05f2f97d8` | 8.8598s；catalog 零命中，未 compile | `controller-local/commands/2c7793f5-9706-48f8-ab42-46226572d9dc.json` |
| image/local 居中 | `536496e` | `eafcc7f0-3eb4-4401-9ddb-f8de1c178b2d` | 7 阶段；159.306762s | `image-local-centered-package`；`controller-local-v3/commands/5e60b94f-fa99-4832-8020-4a28df48114f.json` |
| video/generated 偏移 | `e2391bd` | `f6fe486a-68c1-4894-b210-6eba1359bf7f` | 480.881528s；两次修订仍视觉不符，budget exhausted | `controller/commands/ced4ee90-5672-4798-8901-48b8fb8436ba.json` |
| video/generated 居中 | `536496e` | `2d7703c6-46b8-41d6-b6e7-f182cef8df34` | 7 阶段；160.627255s | `newvideo-centered-package`；`controller-v3/commands/8e0b2f7b-f933-485b-a457-da755269ad84.json` |
| text/web | `e2391bd` | `1b4ed241-59db-4fc4-9e57-01b60e82910a` | 7 阶段；157.955860s | `web-package`；`controller/commands/0db7940d-3b24-45ff-bdbe-e70030e211cd.json` |
| text/generated/fallback A | `e2391bd` | `63968c2d-69d5-428d-80cd-7b156aa11790` | 12 阶段；308.774589s | `scene-repair-package`；`controller/commands/6f052943-d764-4557-891a-b6290d0e729e.json` |
| image+text/generated/fallback B | `536496e` | `4ef25a76-2e33-4d83-8df8-e71a62baa44c` | 12 阶段；321.707608s | `asset-repair-package`；`controller-v3/commands/fff78fb4-a81b-44b7-b909-6b33c999f4dd.json` |

本地首试模型只有 catalog 路径、没有库内容，误选 cube；修复为输入真实 catalog 类别/尺寸/哈希，
没有加虚假 alias。后续居中图片是新小例。偏移视频仍失败，不能以居中成功声称一般位姿理解已修好。
视频完整解码 41 帧，模型只观察 0/20/40；不证明全序列运动一致。居中视频来源是旧失败案例首次
真实居中回放，SHA `7400aa21cb455c6efb142d96d2f1632d5329cf759aeb01ba498ed39e21dc5df8`。

用户随后原样测试“打开的柜子＋柜子上的粉红鼠标”。真实模型错误压成单cabinet/web意图，遗漏
第二物体、颜色、开放状态和层级支撑；资产阶段`web_asset_not_found`，11.225584秒后停止，未产生
Genesis媒体。此结果是多对象语义完整性缺口，不是合理降级；详见[运行证据](../../docs/evidence/user-open-cabinet-pink-mouse-20260913.md)。

web 实际下载 Khronos/Cesium Box（CC-BY-4.0），固定来源提交
`90d7ede14c7e280af263824604b427a1ca02cb66`，源 GLB SHA
`ed52f7192b8311d700ac0ce80644e3852cd01537e4d62241b9acba023da3d54e`。
实际 Y-up→Z-up、统一缩放至 8cm；不是无界互联网语义检索。generated 是程序几何生成，
不是 SimFoundry/OpenReal2Sim 重建；local 是实际 catalog 检索复用。

## 两类 fallback 的实际改变

A：0.22×0.22×0.16m 块体 y=.375，两个 dt 的 support margin=.015m 低于原 .02m。
Codex 实际只把 y 改为 .369，第二轮 34/34。scene SHA 从
`a7e339cfc5663d75e16bc32ec646be3127ad9e23dd8041e7425c8e8d9dbad3eb` 变为
`09444e2e162f036a1b73437d70403e3ba1c31bd6aebf939ea8aaa9ea1eb762a6`，尺寸与门槛不变。

B：真实视觉模型判断相对参考过小，建议 `scale=3.5`。Harness 白名单生成新版本，
尺寸 .055×.055×.04m → 约 .1925×.1925×.14m。旧资产
`2c7f500479d7a7145e53d5df6358f43499069555c13c4c579802c4777e0522bb`，新资产
`4e7e58186d4d9d1bf0b32f3cd163e0adec89bec1e24bd68ee4044d3a679224bb`，保留 parent_version。
scene SHA 从 `20b4983b7baf6f1479887447ab54118fd33f3c38811153ea1dea82eceb9d6aa5`
变为 `86b598ddb3bb07e317240f3c061baf5b1e5796ef98eacb4799205211c0d2feb4`。
运行后主窗口逐成员复查两版本 SHA 均匹配原 receipt，旧资产未覆盖。
新 compile/replay/observe/visual/validate 通过；质量、摩擦仍标为估计值。

每类最多两次修订，依据绑定当前失败 receipt。旧偏移视频实际耗尽两次布局修订后停止，
三次双 dt 全为 34/34，但视觉始终不符，因此没有 validate/package。失败和部分媒体均保留。

## 复制后的可运行包

文本包首次独立运行：`/home/jingxiang/bingsheng/generic-text-package-copy-omub915k/package`，
1000 步、65.366340s、41 帧/4 unique；result SHA
`340eb42ebefdb9df21b5e8729f45ad9ff119949ca0ce45cb7662539a8473bfb5`。

最新完整视频包再次复制到 `/home/jingxiang/bingsheng/generic-video-package-copy-fxqpws2h/package`，
使用复制包自身代码真实执行 baseline 1000 步、66.166572s、41 帧/3 unique。
Landlock ABI8 实际拒绝原包、集成源码、主源码、整个 state-v3/CAS、资产库读取。
结果：[result.json](/home/jingxiang/bingsheng/generic-video-package-copy-fxqpws2h/execution/result.json)，
SHA `42807bd71bc4b4f128a4fb46e68f0caee58fc261f56701fb23616be2e0bb91af`。
包 manifest SHA `490d6c574dfbe7ea4bfc4ca31436ad237897f8044d4108513ffacbe9926bc7f5`。
新视频 SHA 与输入相同是实际确定性重跑结果，不是复制媒体。

[新图片](/home/jingxiang/bingsheng/generic-video-package-copy-fxqpws2h/execution/baseline/frames/step-1000.png) ·
[新连续视频](/home/jingxiang/bingsheng/generic-video-package-copy-fxqpws2h/execution/baseline/preview.mp4)

入口为 `python PACKAGE/run.py --runtime ROOTS_JSON --output NEW_DIRECTORY --deny-root ORIGINAL_ROOT
--profile baseline`。ROOTS_JSON 是运行环境根字典，不是整个 deployment。
声明外部环境 Python 3.12、Genesis 1.3.3、Genesis commit
`0e74bf392781884ccad765c3f344419c86b872ca`；Harness CLI 的 Python 3.13 与之分列。
包包含必要资产、相对引用、输入、模型日志、全部 journal refs、前后修订与媒体，不复制 8.5GB runtime。
导出动作 `physical_profile/relocated_execution=not_run` 与内含 workflow 报告、新 copy-run 结果
分别解释，不手改 manifest。copy-run 只 baseline，不冒称重新通过双 dt。

## 能力状态与剩余边界

| 环节 | 状态 | 范围或缺口 |
| --- | --- | --- |
| 三模态、组合输入与共享意图 | 真实通过 | 每模态一个小例；组合 image+text；单动态刚体 |
| 三来源、不可变登记 | 真实通过 | local catalog、固定许可 web、程序几何 |
| compile/replay/observe/validate | 真实通过 | 有限桌面、双 dt 34 项，视觉独立报告 |
| fallback A/B 与预算停止 | 真实通过 | 一次布局、一次资产修复；另有真实耗尽失败 |
| package-owned load/step/新媒体 | 真实通过 | 指定 Linux/Genesis 环境；不是跨 OS 认证 |
| 开发身份/正式库隔离 | 真实通过 | development_unqualified，不发布、不自动降级 |
| 新实验 MCP 投影、父终态收口 | 未实现 | 同 journal stage 已执行；父仍 active |
| cousin、多物体、独立数值修订 | 未实现 | 明确拒绝或限定，不以标签充作能力 |
| SimFoundry/OpenReal2Sim 通用重建 | 未实现 | 本轮 generated 仅程序几何 |
| release、全矩阵、autoresearch、历史债务 | 合同或设计 | 本阶段后置 |
| robot policy/data collection | 未实现 | 两项 evaluated=false |
| dashboard 同步 | 受阻 | 三入口 HTTP 404、无 task id；只更新仓库账本 |

## 测试与逐功能提交

最终限定回归：205 passed、4 skipped、1 failed（20.17s）；161 schema 快照检查通过。
唯一失败是旧 qualification manifest 拒绝已改变的 `registry.py`，本阶段未重建资格；
不称全仓测试通过。4 skips 为显式 opt-in 的 web/model 调用，真实运行在上文单独记账。
16 个实验/dev/portable 模块：语句 1465/1902=77.02%，分支 412/670=61.49%，综合 72.98%。
包含未被本次单测覆盖的真实 Codex proposal 和隔离子进程，不隐藏低覆盖模块。
[最终测试汇总](/home/jingxiang/bingsheng/generic-experiment-v2-20260913.1QrGYD/final-regression-summary.json) ·
[覆盖率](/home/jingxiang/bingsheng/generic-experiment-v2-20260913.1QrGYD/final-regression-coverage.json) ·
[JUnit](/home/jingxiang/bingsheng/generic-experiment-v2-20260913.1QrGYD/final-regression-junit.xml)。

独立功能分提交，固定真实案例源码与后续文档提交分列于台账。TDD 保留来源错误、开发资格隔离、
receipt 绑定、颜色不符、并发登记、坐标变换、幂等、修订预算和 loader 攻击测试。
后置格式提交 `22ffed5` 仅换行类型注解，5 项输入测试、161 schema 复查通过，10 个主要模块 lint
通过；没有再执行 Genesis，真实案例仍归属原固定提交。
不以 mock、人工最终 proposal 或文档箭头代替真实运行。
此处暂停供审阅，不继续正式 release、大规模实验、机器人接口或无关前端。
