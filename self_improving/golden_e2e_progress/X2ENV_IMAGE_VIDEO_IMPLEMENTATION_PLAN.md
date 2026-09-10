# Image2Env / Video2Env 实施计划

状态：合同完成；runtime 待实现

本计划从 `2bdeafd836d641b4f14aef316a21fbcd736f55e1` 的真实 Harness 基线和
`/home/jingxiang/gujie/gen-env@eb0b710581fd7794bc01b447b0f77cb871c8a711` 的固定来源出发。
公共 seam 是 S1 `GoldenRunHarness`、S3 `GenesisRuntime` 和 S4 `LegacyGenenvImporter`。每个纵切按
RED → minimal GREEN → focused 100% statement/branch → full regression → 必要真实 runtime → 独立
qualification/feature commit 执行。本文不把合同模块当成 runtime 实现。

## X0 — 已完成的合同切片

- 12 个 exact Skill I/O schema；Image/Video source identity、asset decision、environment package 四个
  shared schemas；55 份 committed Harness schema snapshots。
- 六项 contract-only descriptor metadata；未注册 handler，未生成 qualification bundle。
- Gujie legacy mapping spec + image/video raw manifest fixtures。
- compile 固定 non-runtime/non-publishable；replay 固定真实 simulator/collision/steps/media 断言；validate
  是唯一 publishability seam。

## X1 — Shared ingest 与 legacy anti-corruption adapter

1. Image ingest：从 CAS 原图重新解码，生成 canonical PNG、probe 与 `ImageSourceMedia`；测试伪 MIME、
   错 SHA、解码失败、超限尺寸、动画图冒充单图。
2. Video ingest：完整解码容器，生成 ordered frame manifest、FPS fraction、duration ms、total/unique 和
   sequence digest；测试空视频、计数分歧、重复帧、乱序、只有 15 帧 sample。
3. `LegacyGenenvImporter.import_snapshot`：只收 CAS directory snapshot；按 mapping spec 转 SceneIR、
   TaskIR、asset closure 和 candidate package，保存 compatibility report。绝对 locator、missing member、
   unknown schema/status conflict 全部 fail closed。
4. Production adapter 只复用 fixed commit 中可分离的 deterministic media/URDF/scene mapping；来源当前
   内置模型调用不进入 Skill。若必须消费其结果，只能消费预先冻结的 reconstruction directory snapshot。

资格门：两个 ingest 分别有 fixed media cases 和 consumer-side full decode；legacy importer 有 relocation、
missing/changed member 与 status-conflict attack cases。此阶段仍不运行 Genesis。

## X2 — `image2env.compile@1.0.0`

实现：typed handler/application + dependency resolver；消费 ImageSourceMedia、外部 Codex proposal、固定
asset catalog 与 policy，输出 native 或 legacy-adapted candidate package。模型调用不在 handler 内。

三条 compile golden line：

| lane | 必须真实经过 | compile 验收 |
|---|---|---|
| exact reuse | image observation → exact asset identity → current Genesis asset qualification | AssetDecision=exact；closure 完整；static pass/incomplete 可解释；simulator/publishable=false |
| digital cousin | image evidence → task-relevant affordance/geometry/physics threshold → explicit differences | AssetDecision=digital cousin；equivalence ref 非空；不能标 exact |
| generation | reuse exhaustion → run-owned generator adapter → geometry/closure QC → staged CAS | AssetDecision=fresh；generation receipt + staged candidate；不写权威 catalog |

qualification：每 lane 至少一个固定图像；fresh process/CAS；package relocation；descriptor implementation、
dependency/runtime lock、schema snapshot 与 source closure 全绑定。compile qualification 不要求把 package
标为 simulator-ready。

## X3 — `image2env.replay@1.0.0`

每条 X2 compile line 都分别进入 replay，不共用旧 runtime evidence：

1. 从新 materialization root 重验 package、URDF/mesh/texture/collision closure；
2. 调 Gujie-derived production Genesis adapter 的 native load/build；
3. collision enabled，执行非零 settle/observation steps；记录逐 step 时间、state/contact/penetration；
4. 生成顺序视频、full frame manifest、total/unique、fresh observation；
5. `genesis.robot_policy@1` 另执行 reset + bounded nonzero action + typed observation/termination + trajectory。

资格门：三 lane 分别过 rigid scene；最终用例还须过 robot-policy。baseline/half-dt 可作保护性对照，
但 intervention/stabilization state 不充当自由 replay pass。旧 Gujie physics report 只做诊断对照。

## X4 — `image2env.validate@1.0.0`

每条 replay line 在全新进程只从 portable CAS 重读 package、compile/replay receipts、两项 qualification、
runtime/physics/media/fresh-observation bytes，并重算 profile satisfaction。缺闭包、stale qualification、
route/source mismatch、forged pass、媒体不连续、无 collision/nonzero steps 都返回 non-publishable blocker。

只有 pass + no blocker + requested profile satisfied 可输出 publishable。成功执行但
`publishable=false` 是合法 Skill output。promotion 不在本 Skill 内。

## X5 — `video2env.compile@1.0.0`

实现与 X2 共用 deep compiler/asset modules，但入口只接受 VideoSourceMedia。三条 golden line 与 X2 同
样逐条运行，并增加：

- 必须消费完整 ordered frame manifest，而不是 15 帧 sample；
- scene interpretation 引用具体 frame/time，保留跨帧 unknown/conflict；
- 容器 hash、decoded/unique、FPS、duration、sequence digest 全写 compile receipt；
- dynamic observation 不可被单帧静态猜测覆盖。

Gujie `probe_media` 的完整解码/framemd5 方法可移植；因它不持久化全 frame hashes，目标 adapter 必须
新增 manifest writer/reader。其 SimFoundry/native model 编排不能直接作为 handler。

## X6 — `video2env.replay@1.0.0`

三条 compile lane 各自重跑 X3 的 Genesis runtime 门，同时将 replay 视频与输入视频严格分开：前者是
当前仿真产生的 evidence，后者是 source identity。输出必须包含真实顺序 replay frame manifest、正数
steps 和 fresh observations；input total/unique 不能代替 replay total/unique。

资格另含：重复输入帧、乱序 manifest、duration/FPS drift、只复制输入视频当 replay、静态图片改 MIME
冒充视频等 fail-closed cases。最终 robot-policy case 必须产生 trajectory。

## X7 — `video2env.validate@1.0.0`

与 X4 使用同一 deterministic validation kernel，但 codec 强制 video route/package/source identity；三条
资产 lane 各有 portable fresh-process validate。除通用 blockers 外，input/replay frame identities 混淆、
source/replay manifest swap、sample-only evidence 都必须 non-publishable。

## X8 — System2 / Golden catalog 路由条件

合同目录永远不直接进入 executable catalog。每个 exact Skill 只有同时满足以下交集才可路由：

1. public schema `$id` 与 committed snapshot byte-exact；
2. live `SkillDescriptorV2` 精确匹配 skill ref/tool/input/output；
3. handler/application 已安装，input/output codec 是对应模态类型；
4. qualification bundle 由当前 implementation/source/dependency/runtime identity 验证通过；
5. RegistrySnapshot entry 与 live Registry/handler/qualification 交叉绑定；
6. caller workflow state 的 source modality、route、package lineage、requested profile 和允许 transition 匹配；
7. Golden execution policy 只晋升该 Skill 自己拥有的 typed facts。

System2 只可选择上下文列出的 exact ref。image-primary 只能走 image compile，video-primary 只能走 video
compile。video→image fallback 必须先产出带 source video/frame lineage 的新 image ArtifactRef，再提交新的
`image2env.compile@1.0.0` operation；旧 video failure/history 保留。禁止 alias/latest、自动类型转换、
由 planner 参数自报 qualification 或 publishability。

Golden catalog 的加入顺序：compile 资格 → replay 资格 → validate 资格；某一 namespace 三项未齐时可
用于受限阶段测试，但不能进入三段 closed-loop/promotion route。image 与 video 分别完成三 lane 后，
才允许统一路由 benchmark 计为可执行能力。

## X9 — 真实 runtime 与组合资格总门

- Adapter：固定 Gujie source ref、SimFoundry/Genesis gitlink、依赖 lock、source closure；dirty bytes 禁用。
- Asset：exact/digital/generated 分别有来源、许可、geometry、collision representation 与 backend 资格。
- Rigid scene：portable relocation、native Genesis load/build、collision、baseline + nonzero steps、
  contact/support/penetration/stability、顺序媒体。
- Robot policy：固定 robot profile、reset digest、bounded action、RGB/proprio/contact observation、termination、
  trajectory。
- Validate：fresh process/CAS 重新读取；compile/replay qualification 与 receipt identity 不漂移。
- Combined：image/video × 3 asset lanes 都有 compile→replay→validate；component pass 不推出组合 pass。
- Promotion：仅消费 current publishable validate decision；本计划前八阶段不实现或声称 promotion。

## X10 — 提交与验证顺序

建议独立提交：ingest → legacy importer → image compile/replay/validate → video compile/replay/validate →
catalog routing → combined qualification。每一项先写 public-seam RED；新模块 statement/branch 100%；
执行 schema `--check`、focused tests、root `pytest -q`、ruff 和 `git diff --check`。涉及 Genesis loader、
collision 或 validate 行为的提交必须另附真实 runtime evidence；测试 adapter 必须明确
`simulator_executed=false`，不能用于资格签署。
