# Gujie x2env 与当前 Harness 接入审计（2026-09-11）

## 结论先行

用一句通俗的话概括 Gujie 的工作：

> 他做了一套“把文字、图片或视频逐步变成 Genesis 能加载的刚体场景，并用真实物理回放检查它”的
> 候选流水线；其中图片/视频的三维重建主体由上游 SimFoundry 提供，Gujie 的主要工作是输入封装、
> 资产/URDF 转换、场景导入、布局、物理判据、回放、失败诊断和阶段化输出。[GJ-S1][GJ-S2][GJ-S3]

它不是一个已经满足本项目 Golden E2E 要求的完整产品。更准确的表述是：

- 它确实直接调用了固定版本 Genesis 的原生 Python API，能真实 `load/build/step`，因此不是“假 Genesis”
  或纯渲染演示。[GJ-S6]
- 它定义的最终包是自有 `genenv.*` JSON + 自包含 URDF/mesh，而不是 Genesis 官方定义的一种通用场景
  序列化格式；“Genesis 原生”指执行引擎和加载 API 是 Genesis，不代表文件格式是 Genesis 官方标准。
  [GJ-S3][GJ-S5]
- 个别已有资产/刚体场景完成过真实 Genesis 双时间步验收，但 fresh image/video 从输入开始的完整链路
  没有形成已通过、可移植、可重放的 Golden run；视频输入没有完成 Genesis 验收。[GJ-E1][GJ-E2]
- 它没有机器人 `reset/action/observation/reward/policy` 合同，也没有 Harness 的 CAS、Registry、ToolResult、
  StateDelta、MCP、同-run Codex 诊断和 promotion，因此不能整体称为 policy-ready 或 Harness sim-ready。
  [GJ-S1][H-S8]

当前项目的接入也要分两层看：

1. **目标分支 `worktree/bingsheng@ea26524`**：尚未包含新 image/video x2env 实现；Gujie 三组能力在
   provenance 中仍是 `candidate/not_run`。本次审计用 `git branch --contains` 核对，下面列出的新增
   x2env/Genesis 适配提交均只在候选分支。
2. **本地候选 `codex/golden-diagnosis-20260911@7e145274`**：已经接入了合同、媒体 ingest、受控
   image exact-reuse compile、Gujie mouse 字节 staging，以及一条真实 Genesis 单资产 CPU probe；但这些
   还未合回目标分支，也没有组成完整 image2env/video2env Golden line。[H-S8][H-E1]

## 1. 审计口径和固定来源

### 1.1 哪个才是 Gujie 的有效实现

本仓库登记的 `worktree/gujie@a25bc58853db1988aafafe88b544e0573eacbb3c` 是落后的旧工作树，且
Git 已标记为 prunable。真正包含 x2env/Genesis 工作的固定来源是独立仓库：

- 仓库：`/home/jingxiang/gujie/gen-env`
- committed HEAD：`eb0b710581fd7794bc01b447b0f77cb871c8a711`
- 两个主要祖先：
  `9e8d28c24ca72ebf4001c2460dc4b174cef603c0`、
  `a8ced2732b48d3eb89e5c40d6cd7fd71b275f178`
- Genesis gitlink：`0e74bf392781884ccad765c3f344419c86b872ca`
- SimFoundry gitlink：`9e34ebefcd020583fbb755a8b57268dce78eca26`

独立仓库当前有 modified `external/SimFoundry`、untracked `external/WorldComposer/` 以及其他未提交目录。
本报告不把这些 dirty 字节计入 Gujie 已提交能力。[P-S1][P-S2]

### 1.2 归属边界

固定三个提交的 git author identity 分别包括：

- `9e8d28c`、`a8ced27`：`yuhang5090 <1205492990@qq.com>`
- `eb0b710`：`LV-Robotics Lab <lv.robotics.lab@gmail.com>`

“Gujie 工作”是团队对来源域的责任称呼；仓库证据不足以把上述 git identity 与某个自然人一一对应，
本报告不做这种推断。SimFoundry 重建算法、Genesis 引擎及第三方资产也继续归各自上游所有。[P-S1]

## 2. 用通俗语言理解 Gujie 的整套工作

可以把这套系统想成四个车间：

```text
输入车间                 资产车间                   场景车间                  验收车间

文字 ──提取物体/关系──┐
                       ├─> 找现有资产或重建资产 ─> URDF/mesh + 位姿 ─> Genesis build/step
图片 ──SimFoundry─────┤                                 │                    │
视频 ──SimFoundry─────┘                                 └─ 初始预览           └─ 轨迹/接触/穿透/视频
```

“初始预览”和“物理通过”在其代码里是分开的；物理没有通过时，正式 `04_final_render` 必须保持为空。
[GJ-S4]

### 2.1 Text2Env：文字先变成物体清单，再摆成场景

输入主要是：

- 一段自然语言 `--request`；
- 本地 CLIP 资产索引；
- LLM/VLM 配置；
- seed、planner 类型和输出目录。[GJ-S7]

主要步骤是：

1. 模型从文字中提取物体和关系。
2. 每个物体用 CLIP 找候选，再由 VLM 选择具体资产。
3. `build_scene.py` 用 LLM 或规则产生布局计划，再由确定性几何逻辑求坐标。
4. 生成初始 Genesis 预览。
5. 物理验收是另一个阶段；构建成功本身明确写为 `physics_steps=0`、`physics_status=not_run`。
   [GJ-S7][GJ-S8]

输出不是单个 `.scene` 文件，而是一个任务目录，核心四层是：

- `01_obj`：解析后的物体、关系、候选和选中资产；
- `02_scene`：场景图、布局、资产包和预览；
- `03_physics`：物理输入、逐步轨迹、检查和诊断；
- `04_final_render`：只有物理通过后才允许放最终展示媒体。[GJ-S4]

这条 text 流程与本项目原有稳定 `text2env` 不是同一实现：当前 Harness 的 production text2env 仍以
RoboTwin/SAPIEN 为主；Gujie 的 text 流程是另一套 Genesis 候选实现，尚未被替换成 production
`text2env.*` Skills。[H-S7][H-S8]

### 2.2 Image2Env / Video2Env：先重建，再导入 Genesis

统一入口 `reconstruct_media.py` 接受三种互斥输入：一张图片、一个视频，或者一份已经完成的
SimFoundry reconstruction 目录。[GJ-S2]

对图片，它会：

- 校验普通非 symlink 文件和扩展名；
- 完整解码并记录 SHA-256、bytes、宽高；
- 转成 RGB PNG；
- 让 SimFoundry 按单图模式工作，只用一个采样帧。[GJ-S2]

对视频，它会：

- 用 ffprobe 得到尺寸、FPS、时长和完整解码帧数；
- 用 ffmpeg `framemd5` 对所有解码帧计算摘要并统计唯一帧；
- 选最多 15 帧给上游重建步骤；
- 回读 SimFoundry 的 `frames_all` 与 sampled frames，核对数量、索引和字节。[GJ-S2]

一个重要限制是：旧流程虽然在运行时算过所有帧摘要，但持久化 schema 只保存总数、唯一数和 15 帧
采样事实，没有保存完整、可逐帧重放的 ordered frame manifest。这正是当前 Harness 选择重新实现
`VideoSourceMediaV2` 的原因。[GJ-S2][H-S3]

SimFoundry 输出后，Gujie 的代码继续完成：

1. 把重建出的前景物体转换成自包含的单刚体 URDF package；
2. 保存源场景中的对象身份、世界位姿、地面配置；
3. 转成 `genenv.asset_scene.v2` 的 `scene_graph.json`、`scene_layout.json`、
   `native_geometry.json`、asset packages 和逐文件 manifest；
4. 加载进 Genesis 做预览或物理流程。[GJ-S3][GJ-S5]

支撑面有两种模式：

- `upstream`：保留 SimFoundry 的原始平面和物体位姿。当前固定源码在支撑拓扑足够时会做一次自由
  replay；没有明确支撑关系时报告 `not_run`，不能伪造通过。
- `retrieved`：从资产索引中再选一个有限桌面，测量 footprint，把桌面和前景组合后进入完整位置求解、
  稳定化、baseline/half-dt 流程。[GJ-S2][GJ-S9]

### 2.3 资产与场景格式：可搬运 URDF，但不是 Harness CAS

`standard_urdf.py` 对每个资产做独立审计：

- 当前只接受一个 rigid link、零 joint；
- 要求 authored mass/inertia、visual 和 collision geometry；
- 解析 URDF 引用的所有 mesh，检查依赖闭包；
- Genesis 加载后再核对几何、质量、质心、惯量、摩擦和 6 DOF。[GJ-S5]

`import_simfoundry_scene.py` 会把这些 package 复制到场景目录内，用相对路径和逐文件哈希形成自包含包；
删除原 SimFoundry 目录后仍可以验证和预览。但其 `genenv.*` 文件会记录绝对 `scene_package`、
`source_root` 或运行位置等部署信息，而且没有 Harness ArtifactRef、CAS、Registry issuer 和 runtime
qualification，因此“能搬走一些文件”还不等于 Harness 所要求的可移植证据闭包。[GJ-S3][GJ-S4][H-S6]

### 2.4 真实物理验收：不是只看截图

Gujie 的 `validate_imported_scene.py` 确实执行以下真实 Genesis 行为：

- 校验本机 Genesis commit 必须等于固定 gitlink；
- `gs.init(backend=gs.cpu)`；
- 建 `gs.Scene`，开启碰撞，使用 Newton solver；
- 用 `gs.morphs.URDF` 加载对象、用 `gs.morphs.Plane` 加载地面；
- `scene.build()` 后连续 `scene.step()`；
- 每步保存物体位置、姿态、线/角速度、接触点、穿透和接触力；
- 持久轨迹重新计算结论，并核对视频真实帧数和唯一帧数。[GJ-S6]

完整 scene physics workflow 不是“让物体掉一下”这么简单，而是：

1. 验证支撑图；
2. 有界 position solve；
3. 做一次干预式 stabilization，只产生候选位姿；
4. 在候选位姿上独立跑 baseline；
5. 再独立跑相同物理时长的 half-dt；
6. 两次都通过且终态足够一致，才算 pass。[GJ-S9]

判据包括接触、通向固定根的向上力、支撑足迹、穿透上限、末段位移/旋转、末态线速度和角速度。
“稳定化成功”“视频看起来没动”“进程 exit 0”都不能替代这些门。[GJ-S6][GJ-S9]

## 3. 它到底算不算 Genesis 原生、sim-ready？

### 3.1 Genesis 原生：执行层面是，文件标准层面不是

答案应拆开说：

- **是 Genesis 原生执行**：它直接导入固定 Genesis 源码并调用 `gs.init`、`gs.Scene`、
  `gs.morphs.URDF`、`scene.build/step`，不是通过截图或另一个仿真器假装 Genesis。[GJ-S6]
- **不是 Genesis 官方场景格式**：它自己定义 `genenv.asset_scene.v2`、`genenv.*` task/report，并用
  URDF + JSON 做 anti-corruption layer。Genesis 在这里是 runtime backend，不是 package schema 的所有者。
  [GJ-S3][GJ-S5]
- **SimFoundry 阶段不是 Genesis**：SimFoundry reconstruction 和其 OmniGibson/PyBullet 中间步骤只是
  上游重建；只有后面的导入和 replay 才是 Genesis 证据。[GJ-S1][GJ-S3]

### 3.2 Sim-ready：局部 case 可以，整体 pipeline 不能这样宣称

固定来源确实有较强的局部证据：复用已经重建的 mouse 资产，经过位置求解后，baseline 与 half-dt
均通过；另有 mouse/crowded/stack 三个已有场景图完成真实物理和终态展示。[GJ-E1]

但不能把它扩大成“x2env 已经 sim-ready”，原因是：

- fresh mouse image 首轮完整重建在桌面选择/后续环节失败；后续成功物理 case 复用了已有资产，不是
  fresh checkout 从图片一路跑完。[GJ-E1]
- 视频只证明 124/124 帧完整解码和 15 帧采样，未完成视频到 Genesis 的完整验收。[GJ-E1]
- 原有 text 四资产场景存在明确物理失败/资产准备失败，不是所有 text 输入都能通过。[GJ-E2]
- 图片的深度、隐藏几何、尺度、质量和摩擦含推断或模型估计，不是真实测量。[GJ-S2][H-E1]
- 没有机器人任务 API、policy trajectory、数据采集协议、Registry qualification 或 promotion。
  [H-S8]

因此更诚实的能力等级是：**Gujie 已提供 Genesis-loadable rigid-scene 和真实物理验证底座，并有若干
独立 pass case；尚没有 policy-ready、可统一路由、可晋升的 Golden X2Env。**

## 4. 我们目前已经接进来了什么

下面的“已接入”主要指本地候选 `codex/golden-diagnosis-20260911@7e145274`；这些文件和提交尚未进入
`worktree/bingsheng@ea26524`。

| 能力 | 当前接入方式 | 当前最强证据 | 还不能声称什么 |
|---|---|---|---|
| Gujie 来源固定与字段映射 | **合同/审计**，没有复制 runtime | 固定 `eb0b710`、两 upstream gitlink、legacy→Harness 字段映射 | 不代表 adapter 已完整实现 |
| image2env/video2env 六个 Skills | **Harness 新合同** | compile/replay/validate 各三项 exact namespace；strict Pydantic/JSON Schema | 默认仍 unqualified/unregistered |
| 图片输入 | **Harness 重写** | 原图 CAS 全量解码、canonical PNG、typed probe、pure-read 重验 | 未调用 Gujie/SimFoundry；未证明 scene compile |
| 视频输入 | **Harness 重写** | 完整 ordered decoded-frame manifest、时间基、FPS、总/唯一帧、pure-read 全量重解码 | 仍是受控 decoder seam；未接 production decoder/compile |
| exact-reuse compile evidence | **Harness 重写** | SceneIR/TaskIR/catalog/qualification/selection/closure/binding/static/package/receipt 深验 | 仅 image 单物体 `on_table` controlled candidate |
| Gujie mouse URDF | **直接复用资产字节，重新入 CAS** | legacy manifest + 15 members → portable unqualified `AssetRepresentationV1` | 没复用旧 pass、旧 runtime lock 或旧 qualification |
| Genesis 单资产 probe | **参考语义后由 Harness 重新实现 runtime** | 固定 Genesis CPU/Newton、1000 steps、真实 contact/penetration/settling、relocated CAS 重验 | 只是 asset-level probe，不是 environment replay/validate |
| MCP/System 2 | **尚未接入 image/video** | production assembly 仍只装 text2env compile/replay/validate | image/video Skills、observe、promotion 尚未 exposure |

### 4.1 六个 x2env Skill 合同

候选分支已经定义：

- `image2env.compile@1.0.0`
- `image2env.replay@1.0.0`
- `image2env.validate@1.0.0`
- `video2env.compile@1.0.0`
- `video2env.replay@1.0.0`
- `video2env.validate@1.0.0`

合同 helper 只有在调用方已经验证 qualification 时才允许组装 Registry descriptor；单有合同不会自动
注册 handler。[H-S1]

### 4.2 Image ingest：吸收了完整性思想，但代码是 Harness 自己写的

当前 `ImageMediaIngestor` 从 CAS 读原始图片，完整解码 JPEG/PNG/WEBP，处理 EXIF，生成不带元数据的
canonical PNG 和 strict probe。`ImageMediaConsumerVerifier` 再从 source bytes 重算 canonical image、
probe 和全部镜像字段。[H-S2]

这不是直接搬 Gujie 的 `_normalize/probe_media`；它保留了“原始输入 hash + 解码事实 + canonical image”
的意图，同时改造成 Harness ArtifactRef/CAS 信任模型。[H-S2][H-S8]

### 4.3 Video ingest：补上了 Gujie 旧输出最关键的证据缺口

Harness 的 `VideoSourceMediaV2` 要求保存每一帧的 index、DTS、PTS、duration、raw-frame SHA 和 byte count，
并计算完整顺序摘要；probe 还必须声明 `full_decode=true`。[H-S3]

这比 Gujie 的“运行时算全帧 hash、持久化 15 帧 sample”更适合 replay 审计。但目前 production
`Video2EnvCompileV2Input` 仍使用旧 `VideoSourceMedia` v1 类型，新的 `VideoSourceMediaV2` 尚未真正接到
compile，因此这仍是独立 ingest 组件，不是一条 video2env 路径。[H-S3][H-S8]

### 4.4 Image exact-reuse compile：有受控候选，没有生产资格

`Image2EnvExactReuseCompileHandler` 和 deep verifier 已能：

- 重新读取 image media、interpretation proposal、runtime/catalog/representation/qualification；
- 只允许一个 dynamic object、一个 `on_table` 关系；
- case-sensitive exact/alias 唯一匹配；
- 生成并重验 SceneIR、TaskIR、asset selection、closure、binding、static validation、environment package
  和 compile receipt；
- 固定 `simulator_executed=false`、`publishable=false`。[H-S4]

但它使用 controlled Registry/qualification，是“证明合同和证据链能闭合”的 candidate，不是 production
image2env。它没有调用 Gujie 的重建 runtime，也没有加载 Genesis。[H-S4][H-S8]

### 4.5 Gujie mouse 资产：只接了字节，不接旧结论

当前找到的最近 Gujie mouse archive 含一个 URDF、8 个 collision OBJ、visual OBJ/MTL/纹理和
`physics.json`，共 15 members。Harness 会：

- 重算 manifest 和每个 member 的 size/SHA；
- 拒绝 symlink、绝对路径、缺/多文件、权限和 verify→put 期间的 source mutation；
- 把 15 个 bytes 写进新 CAS，再产生一份 self-hashed `AssetRepresentationV1`。[H-S5]

它刻意不迁移 Gujie 历史 `physics_status=passed`、旧 runtime module SHA、绝对 locator 或 qualification。
这是目前唯一称得上“直接复用 Gujie 产物”的部分，而且复用的是资产字节，不是信任结论。[H-S5][H-S8]

### 4.6 真实 Genesis 单资产 probe：已经跑了，但仍不是完整环境

候选 HEAD `7e145274` 已有 Harness-owned `GenesisCpuAssetProbeBackend`：固定 Genesis commit、CPU、
Newton、dt=0.004、1000 steps、10 mm release，不创建 camera/renderer。它把 staged mouse 用
`gs.morphs.URDF` 加载，记录真实 load shape、质量/惯量/摩擦、每步接触/力/穿透和末态速度。[H-S6]

当前工作树的运行证据报告：

- 1,001 rows；
- 989 个 contact steps，4,040 contacts；
- 最大穿透约 0.487 mm；
- 末态线速度约 `6.85e-7 m/s`、角速度约 `2.56e-5 rad/s`；
- 23-object CAS 复制到第二绝对路径后可在不加载 Genesis、也不需要 Gujie source root 的进程里
  strict verify。[H-E1]

需要注意：这份 evidence Markdown/JSON 当前还是候选工作树的未提交文件；代码固定在 `7e145274`，但证据
还没有形成同一个 clean feature commit。并且 runtime lock 尚未封闭 Python、Torch、Quadrants、NumPy 和
native libraries，所以没有签发 production `GenesisAssetQualificationV1`，也没有进入 Registry/MCP。
[H-E1][H-S8]

## 5. 哪些还完全没有接进来

以下 Gujie 能力仍只停留在参考/候选状态：

1. **SimFoundry fresh reconstruction runtime**：没有 production handler 从一张新图片或新视频实际调用
   SimFoundry、导入结果并写入 Harness CAS。
2. **完整 legacy `genenv.*` adapter**：现有合同有 mapping，但没有把整个 TaskOutput/scene package 安全地
   转成 Harness SceneIR/TaskIR/package/compatibility receipt。
3. **`standard_urdf.py` 通用 importer**：只 staging 了一个固定 mouse 的 15-member bytes，没有接通
   Gujie 的通用 SimFoundry asset importer、许可闭包或 articulated asset。
4. **SimFoundry scene import**：ground、对象世界位姿、pose format、source snapshot 到 Harness SceneIR 的
   production 转换器尚不存在。
5. **position solver 与 scene stabilization**：没有进入 Harness operation stages；现有 image compile 只有
   静态单物体关系，没有求解真实多物体布局。
6. **baseline + half-dt environment replay**：Gujie 的双时间步、agreement 和支撑图验证尚未接成
   `image2env.replay`/`video2env.replay`。
7. **物理判据到 production validate/promotion**：单资产 probe 不是 environment validate，尚无 image/video
   publishability 或 promotion。
8. **最终可观看媒体**：Harness 尚未为 image/video Golden line 生成与物理轨迹绑定的 final image/video；
   Gujie 的旧媒体不能直接充当新 run 证据。
9. **video2env compile/replay/validate**：除合同和 ingest 外，三个 runtime Skills 都未实现。
10. **三类资产 lane**：fresh generation、digital cousin、environment reuse 尚未跑通；目前只有一个受控
    exact-reuse image compile candidate。
11. **System 2/Codex 多模态路由**：production MCP assembly 仍只绑定 text2env 三项；没有 image/video
    tool exposure、路由 benchmark、fallback 成功率或 autoresearch 结果。[H-S7][H-S8]
12. **robot policy**：Gujie 和当前接入都没有完成 `genesis.robot_policy@1` 的 robot load、reset、action、
    observation、trajectory 和数据采集门。

## 6. 最容易混淆的四件事

### “Gujie 已有真实 Genesis pass”不等于“我们已经接入该 pass”

旧 pass 属于 Gujie 的独立目录、旧代码和旧 runtime。当前 Harness 正确做法是只复用 bytes/算法思想，
在新的 CAS、runtime lock 和 run 中重跑；不能把字符串 `passed` 搬过来。[H-S5][H-S8]

### “Image/video ingest 通过”不等于“Image/video2env compile 通过”

ingest 只证明媒体是谁、怎样解码、帧有没有漏。它还没有识别场景、选资产、生成布局或运行 Genesis。
[H-S2][H-S3]

### “单资产 Genesis probe 通过”不等于“场景 sim-ready”

当前 probe 证明这个 mouse URDF 在指定参数下能被 Genesis 加载、落地、接触并静止；它没有证明桌子、
对象关系、机器人空间、task semantics 或整个环境 replay。[H-E1]

### “候选分支已有代码”不等于“bingsheng 已交付”

`dacfee6`（六 Skill 合同）、`ae05517`（image ingest）、`13e8f01`（video ingest）、`c817ebf`
（controlled image compile）、`2ddd5e6`（mouse staging）和 `7e14527`（Genesis probe 修正）经
`git branch --contains` 核对均只在 `codex/golden-diagnosis-20260911`。目标
`worktree/bingsheng@ea26524` 尚未包含这些提交。

## 7. 当前准确的交付表述

截至本审计时，建议对外只做如下陈述：

> 我们已经完成 Gujie x2env 固定来源审计和 anti-corruption mapping；在未合入的本地候选分支上，已经
> 独立实现 image/video 媒体完整性 ingest、六个 x2env Skill 合同、受控 image exact-reuse compile、
> Gujie mouse 资产 byte staging，并对该 mouse 完成一次 Harness-owned 的真实 Genesis CPU 单资产
> load/contact/settling probe。我们尚未接入 Gujie 的完整 SimFoundry reconstruction、scene importer、
> position/stabilization/dual-dt environment workflow，也没有任何一条 image/video Genesis Golden E2E、
> production Registry/MCP qualification、robot policy 或 promotion。

## 8. Primary source 索引

以下所有 Gujie 源码引用均固定到
`/home/jingxiang/gujie/gen-env@eb0b710581fd7794bc01b447b0f77cb871c8a711`；Harness 引用固定到
`/home/jingxiang/bingsheng/worktrees/golden-diagnosis-agent@7e145274a10e8d80f3966b491e05a512294588fc`，
除非条目明确说明是当前未提交 evidence。

### Gujie 固定源码

- **[GJ-S1]** `README.md`，L1–26、L128–199、L200–320：总体 text/media pipeline、阶段边界和依赖。
- **[GJ-S2]** `self_improving/sim_adapters/genesis/reconstruct_media.py`，L33–115、L260–343、
  L440–758、L826–915、L918–1009：媒体 probe、SimFoundry 调用、两种支撑模式、物理与 CLI。
- **[GJ-S3]** `self_improving/sim_adapters/genesis/import_simfoundry_scene.py`，L1–32、L54–150、
  L190–380：源 pose/ground 到 `genenv.asset_scene.v2` 与自包含 package 的转换。
- **[GJ-S4]** `self_improving/sim_adapters/genesis/task_output.py`，L1–145、L148–280、L282–305：
  四阶段目录、owner/lock/manifest、physics→final-render gate。
- **[GJ-S5]** `self_improving/sim_adapters/genesis/standard_urdf.py`，L1–149、L162–214：
  单刚体 URDF closure 与 Genesis load audit。
- **[GJ-S6]** `self_improving/sim_adapters/genesis/validate_imported_scene.py`，L212–503、L506–548：
  Genesis commit 锁、原生 Scene/load/build/step、trace/media 和 pure-read verifier。
- **[GJ-S7]** `self_improving/sim_adapters/genesis/extract_assets.py`，L62–188、L191–205：
  text 输入、模型提取、CLIP/VLM 选择和 scene build 调用。
- **[GJ-S8]** `self_improving/sim_adapters/genesis/build_scene.py`，文件说明及 scene report 构建段：
  初始可视场景，无物理验收。
- **[GJ-S9]** `self_improving/sim_adapters/genesis/scene_physics_workflow.py`，L112–261、L272–348：
  position/stabilization/baseline/half-dt/agreement/verify。

### Gujie 固定证据

- **[GJ-E1]** `self_improving/sim_adapters/genesis/MEDIA_RECONSTRUCTION_EVIDENCE.md`，L14–60、
  L80–162、L181–259：fresh image/video 失败、mouse 后续物理诊断与 pass 的准确边界。
- **[GJ-E2]** `self_improving/sim_adapters/genesis/PHYSICS_VALIDATION_EVIDENCE.md`，L65–145：
  text 四资产场景的设置、失败项、正反校准与真实测试范围。

### 当前 Harness 候选源码与状态

- **[H-S1]** `self_improving/harness/x2env_contracts.py`，L26–142：六个 exact Skill 合同与资格门。
- **[H-S2]** `self_improving/harness/image_ingest.py`，L37–193、L234–310：CAS image ingest 和重验。
- **[H-S3]** `self_improving/harness/schemas/video_ingest.py`，L30–257；
  `self_improving/harness/video_ingest.py`：完整 ordered frame-sequence identity。
- **[H-S4]** `self_improving/harness/x2env_compile_closure.py`，L266–439、L479–918；
  `self_improving/harness/image2env_compile.py`：受控 exact-reuse pre/postflight 和派生输出。
- **[H-S5]** `self_improving/harness/legacy_genesis_asset_staging.py`，L25–165、L168–260：
  Gujie legacy URDF byte closure → unqualified representation。
- **[H-S6]** `self_improving/harness/genesis_cpu_backend.py`，L25–35、L72–239、L241–351；
  `self_improving/harness/genesis_asset_probe.py`，L594–760：真实 CPU probe 和证据重算。
- **[H-S7]** `self_improving/harness/mcp_assembly.py`，L151–177：production MCP assembly 仍只绑定
  text2env compile/replay/validate。
- **[H-S8]** `docs/integration-provenance/LEDGER.md`，L254–336；
  `self_improving/golden_e2e_progress/TASKS.md`，L61–103；
  `self_improving/golden_e2e_progress/TODO.md`，L5–68：接入生命周期、验证等级与未完成项。
- **[H-E1]** 当前未提交 evidence：`docs/evidence/golden-genesis-cpu-asset-probe-20260911.md`，L1–39，
  及同名 JSON，L1–84。固定实现 commit 为 `7e145274`，但 evidence 文件本身尚未提交。

### Provenance 与 Git

- **[P-S1]** `docs/integration-provenance/sources/gujie.md`；
  `docs/integration-provenance/LEDGER.md` 的 `INT-GJ-001..004`。
- **[P-S2]** `git status --short --branch`、`git submodule status --recursive` 于
  `/home/jingxiang/gujie/gen-env@eb0b710`；dirty 工作区不进入固定来源。
