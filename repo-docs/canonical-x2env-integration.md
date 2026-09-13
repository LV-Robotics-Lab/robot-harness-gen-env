# Canonical x2env 部署与接入指南

本文面向部署者和 adapter 开发者。用户提交命令见[用户指南](walkthroughs/canonical-x2env-user-guide.md)；
本文解释如何给同一个 Harness 配置模型、Genesis 和资产来源，以及如何登记资产、接入外部实现。
字段权威是 [deployment.py](../self_improving/harness/x2env/deployment.py) 和对应 Python 类型。
示例中的 `/absolute/...`、SHA、来源声明必须替换为实际内容；它们不是已经部署的资源。

截至 matrix v2，四种输入、local 来源、颜色资产修订及有限 Genesis 桌面场景真实通过；
web 和 Gujie reconstruction 的 adapter 已接入路由。后续P1已配置本机外部资源并运行案例，
已证明真实下载及新几何/登记/Genesis预览子集，完整E2E仍有失败；见
[P1记录](../docs/evidence/canonical-p0-p1-20260913.md)。
详细证据见[本轮审计](../self_improving/golden_e2e_progress/CANONICAL_MATRIX_V2_PUSH_AUDIT_20260913.md)。
安装本 Python 包不等于部署了全部来源，也不授予通用重建、机器人策略或数据采集能力。

## 1. 部署入口与目录

优先由 `load_deployment()` 读取部署 JSON，再由 `build_harness()` 组装。
公共 CLI 已使用这条路径；应用嵌入时也可直接调用：

```python
from self_improving.harness.x2env.deployment import build_harness, load_deployment

deployment = load_deployment("/absolute/private/deployment.json")
harness = build_harness(deployment)
print(harness.describe_capabilities())
```

`build_harness` 提供依赖，不启动第二套 workflow。一个 `state_dir` 对应 `harness.sqlite` 和 `cas/`；
workflow、幂等 key、资产不可变版本都依赖这个 Store。操作目录及输出包另有实际路径，
以 CLI 的结果和 workflow 引用为准。更换 `state_dir` 会得到另一份数据库和资产索引，
不是切换同一个部署的展示目录。

部署 JSON、`state_dir`、配置文件必须是作用域明确的绝对路径，避免符号链接。
`PinnedFile` 用 `path` 和 `sha256` 固定配置字节，文件上限 2 MiB。
修改 provider/runtime 配置后必须同步更新 SHA；不要仅凭文件名或修改时间当作身份。
部署文件整体也有 2 MiB 限制。凭据、CAS、输入资产和运行输出均放 Git 之外。

`timeout_seconds` 当前默认 1770，允许 1–1770 秒；这是正常执行预算，清理也应留时间。
matrix v2 使用 1170 秒并为退出留 30 秒，总上限 20 分钟。
常规核验继续遵守用户规定：一次核验含清理不超过 30 分钟，超时保留现场，
未作重大修改或时间优化前不要原样重跑。

## 2. 受管理 Codex

`codex` 配置包含 `executable`、`sha256`、`model`、可选 `router`。
可执行文件必须是绝对路径，SHA 是所执行文件的实际 SHA-256。
当前类型只接受 `gpt-6-astra` 或 `openai/gpt-5.6-terra`，默认前者；
Terra 必须同时配置当前实现明确支持的私有 router，其他模型名不能直接填入绕过类型。

部署私有 router 时，`api_key_file` 必须属于运行用户、权限为 `0600`、大小 1–4096 bytes，
内容为不含空白的单个密钥，文件和父路径不能是符号链接。adapter 只将密钥传入模型子进程环境。
当前 router 地址在类型中固定，并非任意 OpenAI-compatible 地址的通用插件。
不要在日志、示例、Git 或命令参数中复制密钥。

模型子进程由 [CodexBackend](../self_improving/harness/x2env/codex.py) 启动：
`exec --json --ephemeral --ignore-user-config --ignore-rules --sandbox read-only`，
固定请求 `model_reasoning_effort="max"`，并传入本轮输出 schema。
shell tool 和模型自行生成子 agent 的能力关闭。回执区分请求的 effort 与服务端实际值；
`server_effective_effort_verified=false` 不能改写成服务端已证实 max。

这里的模型负责 SceneIntent、设计补全、候选资产视觉判断及诊断建议；controller 负责调用能力、
保存状态和判定最终结果。模型没有任意修改 SQLite、CAS 或项目源码的权限。
无需部署外部 Codex 主控流程，也无需用户配置 MCP。
不配置 `codex` 时仍可以组装 Harness、读取既有状态；需要模型的执行阶段会报告资源缺失。

## 3. Genesis 与设计政策

`genesis.runtime_roots` 必须声明 `interpreter`、`stdlib`、`distributions`、`native`、`genesis`
五个根路径。它们组成运行允许读取的外部依赖范围，不是五个可随意填写的标签。
`denied_roots` 用于隔离原资产、workspace 或 CAS；不要把运行环境或当前包本身放进拒读路径。
环境需要实际提供 Genesis、相应 Python/native 依赖和媒体工具；项目基础依赖安装不负责下载这些资源。

`compile_policy` 是 `StructuralPolicy`，由部署者明确给出结构表面高度、厚度、摩擦等策略。
`scene_design_policy` 默认采用严格设计边界；若使用用户指南中的 `generated_layout` 配置，
需要显式启用并声明世界锚点、结构默认值政策。所补尺寸和姿态是仿真设计，不能写成从图片测得。
已知输入约束不能被设计默认值覆盖。

[GenesisReplayExecutor](../self_improving/harness/x2env/replay.py) 在同一预算中运行 `baseline` 和
`half_dt`，保留子进程身份、场景、轨迹、真实 PNG/MP4 和错误。回放执行成功还需要后续物理及视觉判断。
部署后的首次小场景应核查最终 workflow、双 profile、连续帧数与物理报告；复制包测试还要证明原目录拒读。
不能只用 `import genesis` 成功或一张截图表示环境包可运行。

## 4. 本地资产：先规范化登记，再供检索

默认 local 查询的是当前 Store 中已经登记的 `AssetVersion`，由
[RegistryLocalCatalog](../self_improving/harness/x2env/local_catalog.py) 投影给 Yuxin 原有本地检索实现。
它不会自动扫描历史 RoboTwin 文件夹、读取任意 URDF 目录或导入旧 ledger。
当前索引按准确 `category` 查询，没有自动同义词合并；例如 `mouse` 与其他类别名应由上游明确归一。
投影默认最多 128 个版本、256 MiB 完整证据预算；超限返回明确错误。

目前没有 `x2env asset-import` 子命令。登记是部署/资产准备阶段的 Python 接口，随后用户照常 submit。
最小过程如下：

1. 确认来源、许可及作者，保留原始来源和许可证据；仅接受 `CC0-1.0` 或 `CC-BY-4.0`，后者必须有署名。
2. 对静态单文件 mesh 调用 `normalize_mesh`，明确目标 XYZ 尺寸、Y/Z up、质量和摩擦。
3. 将规范化报告、实际许可和来源记录写入同一 Store 的 CAS，再调用 `AssetRegistry.register`。
4. 保存返回的 `version_sha256`；`inspect()` 可重读并检查。随后提交小请求，让真实预览、回放和 validate 决定可用性。

下面片段适用于已有来源证明的单刚体资产，不是自动生成授权或物理验收的脚本：

```python
from pathlib import Path
from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
from self_improving.harness.x2env.normalization import normalize_mesh
from self_improving.harness.x2env.store import Store

store = Store(Path("/absolute/private/state"))  # 与 deployment.state_dir 相同
source = Path("/absolute/assets/source.glb")
out = Path("/absolute/assets/new-normalized-version")  # 新目录
report = normalize_mesh(
    source, out, dimensions_m=(0.12, 0.08, 0.04), up_axis="Z",
    mass_kg=0.15, friction=0.5,
)
normalization_ref = store.write_artifact(
    (out / "normalization.json").read_bytes(), "application/json"
)
# 这些文件必须包含实际查证的来源/许可/准备过程，不能预填虚假的通过报告。
license_ref = store.write_artifact(
    Path("/absolute/evidence/original-license.txt").read_bytes(), "text/plain"
)
source_ref = store.write_artifact(
    Path("/absolute/evidence/source-record.json").read_bytes(), "application/json"
)
receipt_ref = store.write_artifact(
    Path("/absolute/evidence/preparation-record.json").read_bytes(), "application/json"
)
version = AssetRegistry(store).register(
    asset_id="my-mouse", category="mouse", normalized_root=out,
    entrypoint=report["entrypoint"],
    files=tuple(member["path"] for member in report["files"]),
    normalization_report=normalization_ref,
    license=AssetLicense(
        spdx="CC-BY-4.0", attribution="实际作者与作品署名",
        source_url="https://实际来源页面", evidence=license_ref,
    ),
    source=AssetSource(
        kind="local", provider="operator-import",
        source_ref=str(source), evidence=source_ref,
    ),
    receipt=receipt_ref,
)
print(version.version_sha256)
```

`dimensions_m` 是归一化后目标 AABB，当前实现可分别缩放三个轴；`mass_kg`、`friction` 是供应参数，
不是实测值。默认碰撞体为凸包，会填平凹陷。因此篮子、盘子等即使外观看起来正确，
也不能借此授予 inside/containment 能力。静态 `.glb/.obj/.stl/.ply` 是这条 helper 的输入范围；
带皮肤、动画、关节、复杂外部材质引用需要专门 adapter，不能悄悄压成单刚体。

登记会绑定 URDF、mesh、材质引用和全部文件 bytes；归一化报告必须恰好覆盖声明的依赖。
已经登记的资产不可原地修改，修复登记新版本并通过 `parent_version` 关联原版本。
`AssetVersion.physical_evaluated` 和 `sim_ready` 固定为 false：登记是资产完整性事实，
环境级物理能力来自后续运行证据。

## 5. 接入 Yuxin web providers

当前唯一实现路径为
[`self_improving/asset_pipeline/active/asset_reuse/`](../self_improving/asset_pipeline/active/asset_reuse/)。
早期 `active/1_asset_reuse/` 路径属于历史组织；新接入请使用可导入的 `asset_reuse` 模块。
canonical [YuxinProviderAdapter](../self_improving/harness/x2env/adapters/yuxin.py)
直接复用 `load_providers`、`tiered_search` 和下载实现，不复制 provider 引擎。

部署中的 `web` 结构为：

```json
{
  "web": {
    "provider_config": {
      "path": "/absolute/private/providers.json",
      "sha256": "替换为文件实际64位小写SHA256"
    }
  }
}
```

这是合入完整 deployment 的片段，含占位符时不能通过校验。
从仓库 [providers.json](../self_improving/asset_pipeline/active/asset_reuse/configs/providers.json)
选取实际可用 provider，固定配置路径/内容；索引、缓存和 token 等资源由部署者准备。
仓库样例包含 robotwin_local、NVIDIA、GitHub、Objaverse 等配置，默认启用不表示资源可用。
相对索引路径会受运行工作目录影响，独立部署宜改成明确的资源路径再计算 pin。
GitHub 分支和联网元数据仍可能变化：配置 pin 固定的是配置字节，下载回执固定的是实际获取内容，二者不同。

canonical web 搜索排除 `robotwin_local`，本地来源由上节登记库独立处理。
provider 返回候选后还须满足 HTTPS、支持格式以及逐资产许可检查。
即使原 provider 配置 `globals.license_gate=false`，canonical adapter 的 `_permitted()` 仍检查许可证据；
不能用该开关绕过 `CC0-1.0`/`CC-BY-4.0` 限制。仓库/平台许可证不自动等于每个资产的许可证。
`license_records` 是可选 pinned 文件，主要供 adapter 的本地 ledger 读取；
它不是给任意 web URL 强行填写许可证的白名单。

web 结果还会进入模型准备建议、规范化、Registry、真实资产预览和视觉选择；检索命中不等于环境成功。
GLB 准备建议中的 `up_axis` 表示源文件轴，固定为格式规定的 Y；`dimensions_m` 则是目标 Z-up
场景的 XYZ 尺寸。两者不可混用。其他格式仍由准备建议给出源轴并保存依据。
定位失败应依次读 search receipt 的 `tiers_consulted`、`provider_errors`、`provider_stats`、
候选许可、fetch receipt、规范化/视觉结果。当前 matrix v2 没有执行 web 来源成功案例，
部署完成后应独立用一个许可明确的小资产验证下载到入库全过程，记录真实网络错误和耗时。

## 6. 接入 Gujie reconstruction

当前 adapter 调用固定外部 checkout 中两条窄接口：
`self_improving.sim_adapters.simfoundry.controlled_segmentation.segment_image` 与
`self_improving.sim_adapters.simfoundry.reconstruction_only`。
Harness 管理模型提出分割点/框，外部实现执行分割和重建；不启动 Gujie 旧的完整任务控制器。
应从[来源账本](../docs/integration-provenance/LEDGER.md)选择包含这两条接口的固定提交；
不能仅因为 checkout 是某个历史 Gujie commit 就假定接口存在。

`ReconstructionConfig` 必需字段是 `source_root`、40 位 `source_commit`、`python`、`python_sha256`、
`segmentation_runtime`、`reconstruction_runtime`、`model_refs`；
`derivation_authorization` 类型上可省略，但实际 controller 会在缺失时返回
`blocked_derivation_authorization`。

部署前准备：

1. 外部源码固定 HEAD 且 clean。adapter 会核对 Git HEAD 与 `git status --porcelain`，包含未跟踪修改也会拒绝。
2. 固定解释器及 SHA；两个 runtime JSON 分别用 `PinnedFile` 绑定，其 `python` 字段都必须等于所配置解释器。
3. 按固定外部源码准备分割/重建依赖、GPU、模型权重及缓存。runtime JSON 的内部 schema 归外部实现所有，
   canonical 不提供能替代真实权重的万能模板。
4. `model_refs` 必须非空，键为实际输出 provenance 的点分路径，例如从 `segmentation` 或 `reconstruction`
   起始逐级定位。值必须与 backend 实际报告一致，不能填写未经核验的模型名称来制造匹配。
5. 把输入派生授权作为真实证据写入部署同一 CAS，配置返回的 `ArtifactRef`。
   授权内容检查 `input_sha256`、`allow_derivative=true`、`output_spdx`、`source_url`，CC-BY 还检查 `attribution`。
   输出许可不能从模型或代码许可自动推断。

当前图像选择优先第一张 canonical 输入图片；无图片时从视频完整序列证据核对并取 frame 0。
这不是多视角融合、任意视频全场景重建或按每个物体自动挑最佳帧。
text-only 请求缺少重建图片会返回 `missing_reconstruction_image`。
由于派生授权目前是部署中单个输入绑定引用，更换输入必须准备对应授权；
不能把一个输入的授权当成全部用户媒体的通行证。

外部命令实际接收 masked RGBA、输出目录、runtime、质量、摩擦和 seed；
重建准备可为SceneIR指定颜色提供RGBA估计及准确色名，规范化时显式均匀色覆盖并记录来源。
这是颜色设计，不是从图像恢复纹理；原始生成GLB保持，新规范化资产单独登记。
adapter 核对输入/分割 RGBA/新 `geometry.glb` 的哈希与后端身份，随后仍需规范化、入库和预览。
模型权重/代码许可单独保存，不代表资产物理检查通过。
adapter 子进程只继承有限环境变量（如 PATH、库路径、locale、CUDA 可见设备），
不要假定任意服务 token 会自动传入。缺文件通常返回 `blocked_external_resource`，
身份不符返回 `deployment_identity_mismatch`，后端或输出证据错误有独立日志和 receipt。

历史 matrix v2 没有配置并验证该路径。后续P1已真实执行分割和TRELLIS新几何、规范化登记与
Genesis资产预览；第一次完整工作流因大JSON证据遍历受阻，修复及后续结果见P1记录。
这不能称为任意图像/视频的通用场景重建通过。
首次接入应先证实固定后端可执行，再跑一个新输入的 Harness 全链路，保留全部资源/失败记录。

## 7. 扩展能力与应用嵌入

当前公开 Skill 固定为 `x2env.compile`、`x2env.replay`、`x2env.validate`；
`asset.resolve`、`observe`、`revise` 是内部工具名称。
[CapabilityRegistry](../self_improving/harness/x2env/capabilities.py) 只接受这些名称、精确 SemVer、
输入/输出类型和 schema digest；当前不是自动发现任意插件的系统。
各能力是否实际绑定以 `harness.describe_capabilities()` 和
[build_capabilities](../self_improving/harness/x2env/skill_execution.py) 为准，常量中出现名称不等于已有公开调用端点。

应用应使用同一 `Harness.submit/status/resume/package` 生命周期。
需要替换外部依赖时，优先由 `build_harness` 的依赖组装处接入，或在测试中使用现有
`backend_factory`、`resolver_factory`/`contextual_resolver_factory`、`replay_factory`、`asset_preview_factory`。
两个 resolver factory 互斥；工厂是 Python 构造 seam，不是部署 JSON 中可填任意代码的配置项。

新增 provider 在唯一 asset-reuse 引擎中实现查询/下载，再由 canonical adapter 处理证据和接纳；
新增重建后端实现窄算法接口，保持 controller 状态权威；新增 Skill 行为要同步类型、schema 导出、绑定和测试。
所有 adapter 返回结构化结果和已有产物；不能直接把 workflow 改成功。
保存来源固定提交及许可证，并在同一 feature commit 更新 provenance。

验收接入至少覆盖：正确输入/输出、缺资源、超时退出、坏证据拒绝、幂等、部分产物保留，
再用一次有界真实案例区分“组件实现”“集成接通”“真实通过”。
服务包装 REST、外部 MCP、verified cousin、任意 articulated 重建与机器人接口均不能凭接入文档授予能力；
后续方向另见项目 roadmap。
