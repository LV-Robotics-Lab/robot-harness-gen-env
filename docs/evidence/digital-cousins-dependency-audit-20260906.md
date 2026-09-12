# Digital Cousins 依赖可用性审计（2026-09-06）

## 结论

`external/digital-cousins` **可以作为本项目 Digital Cousin 能力的上游源码依赖**，而且主仓已经用
Git submodule 固定了它的代码版本。但它 **现在不能直接作为现有 `text2env.compile` 的即插即用
依赖**，也不能把它的 BEHAVIOR 资产直接拷进 RoboTwin catalog。

最合适的心智模型是：

```text
external/digital-cousins
    = “看一张真实图片，挑 OmniGibson 里的近似物体，并拼出 OmniGibson 场景”的上游引擎

当前 text2env.compile
    = “读一段文字和 RoboTwin catalog，求解并打包 RoboTwin/SAPIEN 场景”的编译器
```

两者可以协作，但中间还缺一个正式适配层，并且要保留 OmniGibson 数据许可边界。

| 检查项 | 当前判断 | 简单解释 |
| --- | --- | --- |
| 上游源码能否作为依赖 | **可以** | 子模块完整、版本固定、代码许可证为 Apache-2.0 |
| 本机运行环境是否可立即执行 | **不可以** | `acdc` 环境的 editable 安装仍指向已删除的旧工作区，核心包无法导入 |
| 模型和数据是否齐全 | **不齐** | 有部分依赖、检查点和一组历史运行产物，但 BEHAVIOR/ACDC 资产链接已失效 |
| ACDC 输出能否直接喂给现有 compile | **不能** | ACDC 输出 OmniGibson 的 `category/model/pose`；compile 只接受 `robotwin.asset_catalog.v1` |
| 能否做成真正的 Digital Cousin 路线 | **可以** | 推荐新增 OmniGibson 原生 provider/package/replay，而不是复制受限资产 |

## 1. 为什么源码依赖本身是成立的

主仓已把它声明为外部子模块，地址是 `LV-Robotics-Lab/digital-cousins`，并记录预期的集成分支：
[`.gitmodules`](../../.gitmodules#L5-L8)。主仓 gitlink 与来源清单都固定在
`b1aa90a8dec7273e5c5a4a60450dc829083ce0a0`：
[`source_inventory.json`](../../self_improving/source_inventory.json#L187-L192)。本地子模块工作区干净，
当前提交也与主仓 gitlink 一致。

平台注册表已经把它作为必需外部模块登记，而不是历史资料：
[`registry.py`](../../self_improving/registry.py#L54-L64)。平台 README 对它的责任描述也是
“Digital Cousin discovery and generation”：
[`self_improving/README.md`](../../self_improving/README.md#L30-L34)。

代码本身的许可证是 Apache-2.0：
[`LICENSE`](../../external/digital-cousins/LICENSE#L1-L5)。因此，从代码组织和源码许可证看，正式写
adapter 调用这个子模块是合理的。

有一个不影响当前 commit 身份、但接入前应修正的维护问题：`.gitmodules` 写的是
`integration/self-improving`，本地及当前远端跟踪分支实际是 `main`。应选择一个真实存在的稳定分支，
或者明确只以 gitlink commit 为准，避免将来更新子模块时找不到配置中的分支。

## 2. 它实际做什么，与当前 compile 有什么不同

### Digital Cousins / ACDC

它的官方入口是 **单张 RGB 图片**，不是文字提示词。README 明确称其为从单张 RGB 图自动生成
可交互场景的代码库，并要求 Linux、Conda、CUDA 和建议 24 GB 以上显存：
[`README.md`](../../external/digital-cousins/README.md#L13-L21)。本机有 32 GB RTX 5090，因此硬件
表面条件满足，但这不代表软件和数据已经就绪。

ACDC 的三步是：

1. 从图片提取真实世界结构；
2. 给每个物体匹配 Digital Cousin；
3. 生成 OmniGibson 场景。

这三步和输入接口可直接见
[`acdc.py`](../../external/digital-cousins/digital_cousins/pipeline/acdc.py#L35-L79)。匹配阶段会在
OmniGibson 数据集中选 category、model 和 pose：
[`matching.py`](../../external/digital-cousins/digital_cousins/pipeline/matching.py#L88-L128)。生成阶段用
OmniGibson `DatasetObject(category=..., model=...)` 加载所选模型：
[`generation.py`](../../external/digital-cousins/digital_cousins/pipeline/generation.py#L219-L266)。

最终输出是 `scene_<n>_info.json` 和 `step_3_output_info.json`。对象记录的是 category、model、scale、
包围盒和相对相机位姿，而不是一份自带 OBJ/URDF 的独立资产包：
[`generation.py`](../../external/digital-cousins/digital_cousins/pipeline/generation.py#L281-L322)、
[`generation.py`](../../external/digital-cousins/digital_cousins/pipeline/generation.py#L630-L651)。重放时仍
需要本机 OmniGibson 数据集按 category/model 找回资产：
[`generation.py`](../../external/digital-cousins/digital_cousins/pipeline/generation.py#L673-L716)。

### 当前 `text2env.compile`

当前 compile 输入只有文字、随机种子、`robotwin.asset_catalog.v1` 和是否生成缺失资产的开关：
[`text2env.py`](../../self_improving/harness/schemas/text2env.py#L90-L105)。它没有 RGB 图片或视频输入，
所以不能直接调用 ACDC 的图片链路。

compile 的 catalog 条目必须提供 RoboTwin/SAPIEN 能读的 rigid 或 URDF 表示、尺寸、稳定姿态、碰撞、
质量/摩擦等字段：[`catalog.py`](../../scene_gen/catalog.py#L28-L85)。求解结果还把每个对象固定为
`asset_id + model_id + load_type + source_files`，并把 catalog 摘要绑定进场景：
[`schema.py`](../../scene_gen/schema.py#L323-L369)、
[`schema.py`](../../scene_gen/schema.py#L473-L495)。

最后生成的包入口是 `scene_gen.envs.generated_scene:load_resolved_scene`，manifest 类型是
`robotwin.generated_scene_package.v1`：[`builder.py`](../../scene_gen/builder.py#L39-L70)。Harness 外层又
要求 `EnvironmentPackage` 的 catalog 和 package manifest 分别是 RoboTwin 的两个固定 schema：
[`text2env.py`](../../self_improving/harness/schemas/text2env.py#L59-L87)。

因此，ACDC 的 `scene_info.json` 不能改个文件名就当作现有 `EnvironmentPackage`。当前仓库也没有
ACDC → RoboTwin catalog/ledger 的 production adapter；现有验收脚本只会预检子模块、导入状态和
是否存在 step-2/step-3/catalog 文件：
[`run_compile_acceptance.py`](../../script/run_compile_acceptance.py#L700-L758)。

## 3. 本机目前为什么还跑不起来

本机确实有名为 `acdc` 的 Python 3.10 环境，且 `pip show` 还能看到 `digital-cousins`、OmniGibson、
GroundingDINO 和 SAM2 的安装元数据。但这些是失效的 editable 安装：它们仍指向
`/home/jingxiang/gujie/digital-cousins/...`，而这个旧工作区已经不存在。实际探测结果为：

```text
digital_cousins  false
omnigibson       false
groundingdino    false
sam2             false
dinov2           false
depth_anything_v2 false
```

直接 `import digital_cousins` 和 `import omnigibson` 都得到 `ModuleNotFoundError`。最近一次 compile 验收
也保留了同样的机器可读结论；它在子模块和共享依赖目录这两个预检根目录中没有发现可用 catalog 或
step-2/step-3 输出：
[`digital_cousins_preflight.json`](../../data/compile_environment_acceptance_20260906_v3/digital_cousins_preflight.json)。

另一个共享目录
`/home/jingxiang/workspace/robot-harness-gen-env/data/external/digital-cousins` 约 6.2 GB，已有四个主要模型
检查点、两个策略检查点，以及 GroundingDINO、SAM2、DINOv2、robomimic 的部分源码。这能减少恢复
成本，但还不等于完整安装：

- `assets` 是一个断开的符号链接，目标位于当前不存在的 `/media/...`；
- 没有找到 BEHAVIOR/OmniGibson 对象数据；
- 这个共享依赖目录本身没有 ACDC 的 step-2/step-3 输出或 compile catalog；
- PerspectiveFields、Depth-Anything-V2 等源码依赖也没有形成当前可导入环境。

子模块自己的目录只有约 11 MB 源码和两张示例图片，不包含 assets、checkpoints、deps 或训练结果。
这是正常的源码仓形态；README 本来就要求另外安装依赖和数据：
[`README.md`](../../external/digital-cousins/README.md#L37-L137)、
[`README.md`](../../external/digital-cousins/README.md#L140-L166)。

### 重要更正：有历史执行证据，但当前不能重放

更宽范围检查找到了此前学生工作区归档中的一组约 37 MB ACDC 产物：

`/home/jingxiang/workspace/robot-harness-gen-env/data/student_workspaces/gujie/acdc_output`

这不是空目录，也不是只有说明文档。它包含 step-1 的深度、分割和检测结果，step-2 的候选图与选择
JSON，以及 step-3 的三个场景 JSON 和三张完整场景可视化图。step-2 汇总明确记录 `3` 个对象、每个
对象 `2` 个 cousins，并给出水瓶、桌子和笔记本对应的六个 BEHAVIOR model id：
[`step_2_output_info.json`](/home/jingxiang/workspace/robot-harness-gen-env/data/student_workspaces/gujie/acdc_output/step_2_output/step_2_output_info.json#L1-L64)。
step-3 汇总包含 `scene_0`、`scene_1`、`scene_2` 三个 1600×900 场景及各自的 category、model、scale、
相机位姿和对象位姿：
[`step_3_output_info.json`](/home/jingxiang/workspace/robot-harness-gen-env/data/student_workspaces/gujie/acdc_output/step_3_output/step_3_output_info.json#L1-L32)、
[`step_3_output_info.json`](/home/jingxiang/workspace/robot-harness-gen-env/data/student_workspaces/gujie/acdc_output/step_3_output/step_3_output_info.json#L156-L174)、
[`step_3_output_info.json`](/home/jingxiang/workspace/robot-harness-gen-env/data/student_workspaces/gujie/acdc_output/step_3_output/step_3_output_info.json#L310-L334)。

所以准确说法是：**已经有 ACDC 曾成功执行到场景输出的历史证据，但没有当前可重放的依赖闭包。**
阻断点有三类：

- step-1/step-3 JSON 内仍写着已删除的 `/home/jingxiang/gujie/...` 绝对路径；例如输入 RGB 和分割目录
  仍引用旧位置：
  [`step_1_output_info.json`](/home/jingxiang/workspace/robot-harness-gen-env/data/student_workspaces/gujie/acdc_output/step_1_output/step_1_output_info.json#L19-L20)、
  [`step_1_output_info.json`](/home/jingxiang/workspace/robot-harness-gen-env/data/student_workspaces/gujie/acdc_output/step_1_output/step_1_output_info.json#L57-L58)。归档中实际保留了
  `bottle_resize.jpg` 和中间 PNG，但 JSON 没有被重定位。
- step-2 所选的六个 BEHAVIOR 模型（`lojipo`、`qaceen`、`puapey`、`wdbwhc`、`sngiih`、`ongghk`）
  仍通过旧绝对路径引用资产快照：
  [`step_2_output_info.json`](/home/jingxiang/workspace/robot-harness-gen-env/data/student_workspaces/gujie/acdc_output/step_2_output/step_2_output_info.json#L7-L60)。在当前可访问目录中没有找到这些模型本体。
- ACDC 的 loader 会按 category/model 从 OmniGibson 数据集重新取模型，而不是从历史 JSON 或 PNG
  恢复网格。因此已有可视化图能证明当时跑过，不能替代当前 runtime、数据和资产闭包。

这组历史产物很有价值：正式恢复时可以先把它用作“冻结输入 + 已知选择结果”的回归 fixture，优先
尝试重定位和 native replay；但在六个模型资产与数据许可环境恢复前，不能把它记为当前可用的
Digital Cousin compile 输出。

## 4. 代码许可证与数据许可证必须分开看

Digital Cousins 源码是 Apache-2.0，但它依赖的 BEHAVIOR 数据不是 Apache-2.0。ACDC README 要求使用
`--accept_license` 下载 OmniGibson/BEHAVIOR 数据：
[`README.md`](../../external/digital-cousins/README.md#L140-L147)。本机保留的 OmniGibson 一方源码把
数据条款写得很明确：

- 仅限非商业学术研究；
- 禁止从 OmniGibson 中提取数据或逆向；
- 数据只能在 OmniGibson 内使用；
- 不能重新分发密钥或数据的全部或部分。

原文在
[`asset_utils.py`](/home/jingxiang/workspace/robot-harness-gen-env/data/external/digital-cousins/deps/GroundingDINO/OmniGibson/omnigibson/utils/asset_utils.py#L438-L448)，下载逻辑在没有密钥时会要求用户明确同意：
[`asset_utils.py`](/home/jingxiang/workspace/robot-harness-gen-env/data/external/digital-cousins/deps/GroundingDINO/OmniGibson/omnigibson/utils/asset_utils.py#L526-L552)。

这意味着：可以在 OmniGibson 内按 model id 使用官方 Digital Cousin，但不能把 BEHAVIOR 网格解密后
复制到 RoboTwin、主 CAS 或可移植交付包。源码依赖可以自动化；接受数据条款必须由有权的人明确
决定，不能由 compile 隐式完成。

## 5. 推荐接法

### 推荐：把它接成独立的 OmniGibson Digital Cousin provider

这是对源码和数据许可最自然的接法：

```text
RGB 观测 ArtifactRef
  → DigitalCousinProvider（独立 Python 3.10 / OmniGibson 进程）
  → ACDC step 1：图像、深度、分割、相机/场景结构
  → ACDC step 2：category/model/pose 候选与选择收据
  → ACDC step 3：OmniGibson scene_info
  → OmniGibsonEnvironmentPackage（只引用数据集版本和 model id）
  → OmniGibson replay / validate
```

不要直接修改现有 `harness.environment_package.v1` 的含义。更安全的是新增一个明确的
`digital_cousin.compile@1.0.0`，或者新增支持 backend 的 package v2，并为 OmniGibson 单独定义 replay
adapter。这样现有 RoboTwin/SAPIEN compile 的信任边界不会被悄悄改变。

要注意：ACDC 不是纯文本检索器。若入口仍只有随机文字提示，必须先明确增加一种输入来源：

1. 用户或真实运行提供的 RGB 观测；或
2. 由另一个已审计生成器产生、并保存来源摘要的目标参考图。

不能在没有图片的情况下记录“ACDC 已执行”。如果只是根据文字在本地 catalog 中挑同类资产，那是
文字资产检索，不是这套 ACDC 图片到 Digital Cousin 的方法。

### 不推荐：把官方 BEHAVIOR 资产转换成 RoboTwin 资产

按本机保存的 BEHAVIOR 条款，官方数据只能留在 OmniGibson 内，因此不能把它们转成 OBJ/URDF 后
入库到现有 RoboTwin compile。只有在输入资产另有清晰、允许转换和分发的许可证，或者资产完全由
用户拥有时，才可以实现这一分支。

如果以后使用这类可转换资产，适配器也不能只产一个 catalog JSON。它至少要产出：

- 真实 visual/collision/URDF 或 rigid 表示及其文件摘要；
- 尺寸、坐标轴、原点、稳定姿态、质量、摩擦、碰撞和关节信息；
- ACDC 输入图、候选列表、最终 category/model id 和匹配方法的选择收据；
- 来源、许可证、代码 commit、数据版本、checkpoint/config 摘要；
- `asset_ledger.v3`，并通过 SAPIEN load/settle/admission 后再写
  `robotwin.asset_catalog.v1`。

现有 ledger 契约已经有这些归属位置：它区分 retrieved/generated 来源，要求保留不可恢复的许可证或
生成参数，并区分 sapien/isaacsim/mujoco/portable 表示：
[`ledger.py`](../../self_improving/asset_pipeline/active/1_asset_reuse/lib/ledger.py#L28-L31)、
[`ledger.py`](../../self_improving/asset_pipeline/active/1_asset_reuse/lib/ledger.py#L73-L119)、
[`ledger.py`](../../self_improving/asset_pipeline/active/1_asset_reuse/lib/ledger.py#L174-L253)。

## 6. 精确的接入任务清单

1. **先做许可决定**：由用户确认项目用途是否满足“非商业学术研究”，并由有权的人显式接受；Harness
   只记录许可状态，不代替用户点击同意。
2. **修复而不是复用失效环境**：建立受版本控制说明的独立 ACDC runtime，把 editable 路径指向当前
   子模块和完整依赖；固定 Python、CUDA、OmniGibson、依赖 commit 和 checkpoint SHA-256。
3. **恢复数据路径**：让 ACDC assets、BEHAVIOR/OmniGibson dataset 和 key 指向有效、受访问控制的
   外部数据目录；不得进入 Git 或通用 CAS。
4. **定义输入契约**：给 Digital Cousin 路线增加 RGB `ArtifactRef`。若从文字开始，还要明确参考图
   的生成者、seed、digest 和用途，不能省略 text → image 这一步。
5. **实现 provider 隔离层**：主 Harness 用 subprocess/worker 调用 ACDC Python 3.10，不把 Isaac Sim/
   OmniGibson 依赖导入 Python 3.11 的 `scene_gen` 核心。
6. **保存可审计收据**：固定输入图、config、step-1/2/3 JSON、候选及所选 model id、代码/data/checkpoint
   身份、日志和媒体。受限资产本体只保存受控引用，不复制。
7. **新增 backend package 与 replay**：包描述应能在另一台已获许可、安装同版本数据的机器上重建，
   但要诚实标为“可移植配方”，不能声称是自包含资产包。
8. **做真实运行门**：当前方便加载脚本显式使用 `visual_only=True`：
   [`load_scene.py`](../../external/digital-cousins/digital_cousins/scripts/load_scene.py#L16-L42)。因此第一轮
   图片只能证明场景可见；正式 replay 必须在允许物理的模式下加载并验证碰撞、沉降、支撑、关节和
   连续帧，再由 validate 决定是否通过。
9. **最后才接 compile 编排**：当上述 provider/package/replay 已有资格报告后，compile 才能把
   “无精确复用资产 → 请求 Digital Cousin”作为一个真实分支；依赖不齐或许可未知时必须明确阻断，
   不能退回程序几何体后仍标记为 Digital Cousin。

## 7. 对当前 5 条 Digital Cousin 验收意味着什么

当前验收最后五条仍只能算“本地任务等价资产复用”，因为它们的收据明确写着
`upstream_acdc_image_pipeline_executed=false`：
[`run_compile_acceptance.py`](../../script/run_compile_acceptance.py#L1074-L1098)。

将本子模块登记为 provider 依赖，是把 `0/5` 往真实 ACDC 推进的正确第一步；但只有在真实 RGB 输入、
ACDC step 1–3、OmniGibson package、原生 replay 及选择收据都完成后，才能把这五条改记为“官方
Digital Cousin 已使用”。

## 审计边界

本次只读取本地一手源码、Git 元数据、环境和数据路径，没有安装或下载任何内容，没有接受数据许可，
没有运行 GPU 模型，也没有改动 compile 或 PEARL。Dashboard 三个规定状态地址仍返回 HTTP 404，
因此没有同步远端任务状态。
