# Canonical x2env API 与 CLI 参考

结构支撑的SceneEntity新增可选`surface_rgba`（4个0..1数值）。模型保留原`color`文本及其provenance，
同时提供均匀表面颜色估计，编译器直接传给Genesis；不是纹理恢复。foreground必须为null/省略，
资产材质继续由资产工具处理。旧记录缺字段保持原序列化及旧颜色解析行为。

本页按当前 `self_improving.harness.x2env` 源码说明调用行为。首次安装、部署配置实例及用户输入
配方见[用户指南](walkthroughs/canonical-x2env-user-guide.md)。当前真实验收范围见
[matrix v2 审计](../self_improving/golden_e2e_progress/CANONICAL_MATRIX_V2_PUSH_AUDIT_20260913.md)。
API 支持某种输入格式，不表示该格式下任意场景、资产来源和物理关系都已验证成功。

公开 CLI 新增 `check --deployment /absolute/deployment.json`，返回
`x2env.deployment_check.v1`：各组件的 `configured/not_configured/misconfigured`、检查项和
`missing` 字段路径。退出码 0 表示已配置项的轻量检查通过，1 表示配置无效或检查失败。
可选来源未配置不会使 `ok` 变 false；因此必须同时查看 `components`，不能把 `ok` 当作 E2E。
该命令不创建 Store，不读密钥内容，不运行模型/Genesis。仅 pinned JSON 执行大小、链接和 SHA
检查；模型/Genesis 路径只检查存在性，重建 Git/模型/解释器身份仍由实际 adapter 执行时核验。

## Python 调用入口

使用具体模块导入；包的 `__init__.py` 当前没有重导出 `Harness` 或 `X2EnvRequest`。
推荐由受信部署配置组装 Harness，这会绑定模型、资产解析器、编译政策和 Genesis 执行器。

```python
from pathlib import Path

from self_improving.harness.x2env.contracts import InputMedia, X2EnvRequest
from self_improving.harness.x2env.deployment import build_harness, load_deployment

config = load_deployment(Path("/absolute/deployment.json"))
harness = build_harness(config)
request = X2EnvRequest(
    text="在桌面中央放一个易拉罐。",
    images=(),  # 可替换为 (InputMedia(path="/absolute/input.png"),)
    seed=42,
    idempotency_key="user-can-42-v1",
    output_dir="/absolute/outputs/user-can-42-v1",
)
handle = harness.submit(request)
snapshot = harness.resume(handle.workflow_id, timeout=1170)
print(snapshot.model_dump_json(indent=2))
if snapshot.status in {"succeeded", "failed", "blocked", "cancelled"}:
    delivery = harness.package(
        handle.workflow_id,
        output=Path(request.output_dir),
    )
    print(delivery)
```

这些 `/absolute/...` 是待替换路径；实例依赖已部署资源和已登记资产。Python `submit` 只登记
持久请求并返回 handle，**不会执行模型或仿真**；Python 调用者须继续 `resume`。
CLI `submit` 已封装 `submit → resume → package`。这一区别源于
[Harness](../self_improving/harness/x2env/harness.py) 与
[CLI](../self_improving/harness/x2env/cli.py) 的真实调用关系。

### 方法与返回值

- `Harness.submit(request: X2EnvRequest) -> WorkflowHandle`：写入请求；返回含 `workflow_id` 的
  冻结模型。新 workflow 为 `active`、revision 0。当前请求模型由调用者在边界构造。
- `Harness.status(workflow_id: str) -> WorkflowSnapshot`：读取持久 snapshot，不推进操作。
  找不到 workflow 时抛出 `KeyError`。应使用同一 deployment 的 state 目录查询。
- `Harness.resume(workflow_id: str, *, timeout: int = 1770) -> WorkflowSnapshot`：前台领取并执行
  可继续的步骤。`timeout` 必须是整数 1–1770；控制器将剩余预算传给后端。Python 层是控制器
  的截止预算，CLI 还额外安装命令级 `SIGALRM`；调用自定义阻塞 adapter 时不能只靠这个 Python
  参数保证操作系统硬中断。`succeeded/failed/cancelled` 直接返回；`blocked` 只有满足实现的恢复
  条件才继续。它不是“重试一切错误”的接口，也没有接受用户补充 clarification 的参数。
- `Harness.package(workflow_id: str, *, output: Path | None = None, reuse_existing=False)`：返回
  Python `dict`。成功 workflow 导出已提交环境包；`output=None` 时返回原包的位置和清单信息。
  `failed/blocked/cancelled` 必须提供输出目录，导出失败证据。`active` 不可导出。
  此调用不运行 Genesis、不重新诊断、不提升资格、不自动上传。
- `Harness.describe_capabilities()`：返回 `tuple[CapabilityDescriptor, ...]`，每项具有
  `name/version/schema_sha256`；可用 `dataclasses.asdict()` 序列化。

直接构造 `Harness(state_dir: Path, *, backend_factory=None, resolver_factory=None,
compile_policy=None, replay_factory=None, contextual_resolver_factory=None,
scene_design_policy=None, asset_preview_factory=None)` 是开发集成 seam。
同时传 `resolver_factory` 和 `contextual_resolver_factory` 会被拒绝。仅传 state 目录可查询和
登记请求，但执行会因缺后端或执行器而停止；生产调用应优先使用 `build_harness`。

### 幂等、恢复与并发

同一 state 内，相同 `idempotency_key` 与完全相同序列化请求返回同一 workflow；任意请求字段
改变，包括 `output_dir`、路径或来源元组顺序，会导致 `ValueError`。新的意图或新的试验使用新 key。
这不是“相同 prompt 必然相同文件”的承诺。请求指纹包含媒体路径，内容绑定在后续 ingest 时完成；
登记到 ingest 期间应保持输入文件不变。证据来源见
[Store.submit](../self_improving/harness/x2env/store.py) 和
[输入采集](../self_improving/harness/x2env/input.py)。

workflow 使用 owner 与 revision 控制领取及原子状态推进；死亡 owner 的恢复由 Store/Harness
处理。不要手改 SQLite、删除 CAS 或使用新 key 假装恢复旧 workflow。超时或中断会保留已完成
证据；当前 `cancelled` 不会被普通 `resume` 再执行。按照项目验证规则，超时核验应等待重大修改、
时间优化或新授权后再开展，不能无限循环调用 resume。

## CLI 完整参数

安装入口为 `x2env`，等价模块入口为 `python -m self_improving.harness.x2env.cli`。
支持 Linux 的 `SIGALRM/ITIMER_REAL`；当前不是通用 Windows 命令实现。

```bash
x2env --deployment /absolute/deployment.json submit \
  --text "在桌面中央放一个易拉罐。" \
  --seed 42 --idempotency-key user-can-42-v1 \
  --output /absolute/outputs/user-can-42-v1

x2env status --deployment /absolute/deployment.json --workflow-id WORKFLOW_ID
x2env resume --deployment /absolute/deployment.json --workflow-id WORKFLOW_ID
x2env package --deployment /absolute/deployment.json --workflow-id WORKFLOW_ID \
  --output /absolute/new-export-directory
```

`--deployment` 必填，可位于子命令前或后。其它参数属于各子命令。`--help` 和各子命令的
`--help` 由 argparse 提供。

`submit` 参数：

- `--text TEXT` 或 `--text-file PATH`：互斥，均可省略；文本文件最大 128 KiB，读取后的文本仍受
  请求模型 32768 字符限制。省略文本时必须给图片或视频。
- `--image PATH`：可重复，最多八张，顺序保留；默认空列表。CLI 转成绝对路径。
- `--video PATH`：最多一个，默认不传。可与文本、图片同时给出，CLI 转成绝对路径。
- `--seed INT`：必填，0–2147483647。
- `--source local|web|reconstruction`：可重复指定允许来源；默认三者全部允许。不允许重复项。
  这是允许来源筛选，执行选择顺序仍由控制器的来源路由负责。
- `--allow-cousin`：默认 false；目前只是请求约束。传入后命令 JSON 明确追加
  `not_implemented: ["digital_cousin_selection"]`，不能据此宣称 cousin 已启用。
- `--idempotency-key STRING`：必填，1–255 字符。
- `--output PATH`：必填，转绝对路径；指定本次交付目录，建议尚不存在。

`status/resume/package` 都必须传 `--workflow-id`。只有 `package` 额外要求 `--output`；
`resume` 没有输出参数，也不会自动执行交付导出，执行后用单独 `package` 命令取得新的交付目录。
CLI 没有 `--timeout`、`--model`、`--publish` 或覆盖成功阈值参数；时间预算及模型由 deployment 控制。

### stdout、退出码与异常

正常命令向 stdout 输出一个 JSON 对象，包括 `workflow_id/status/snapshot/command_wall_seconds`。
`submit` 在停止状态及显式 `package` 命令还包含 `delivery`。stdout 是机器可读 JSON；若要人类
阅读，可自行格式化，不能假定程序打印多段中文说明。

- 退出码 `0`：`submit/resume` 的 workflow 成功；或者 `status/package` 命令本身成功。
  **查询或导出失败证据的退出码也可以是 0**，必须同时读 `status`。
- 退出码 `2`：正常返回的 `submit/resume` workflow 为 `blocked` 或 `active`。
- 退出码 `1`：正常返回的 `submit/resume` 为 `failed/cancelled`；或参数、资源、导出、超时、
  中断被 CLI 的异常处理捕获。参数解析错误也采用 JSON + 1，并非默认 argparse 的 2。

已捕获异常 JSON 包含 `status="failed"`、`error_code`、`message`（最多 2048 字符）、`stage`、
`workflow_id` 与 `command_wall_seconds`；如果已有 snapshot，可能包含 `workflow_status`。
命令状态失败不意味着持久 workflow 已改成 failed，例如中断的 workflow 可能是 cancelled。

命令级错误码有 `command_timeout`、`command_interrupted`、`package_failed`、
`blocked_external_resource`、`invalid_request`。当前 CLI 只捕获源码列举的
`ValueError/OSError/KeyError/ImportError/KeyboardInterrupt`，不要假定任何编程异常都包装成 JSON。
工作流内部错误另外见 `stop_reason`、operation 的 `ToolResult.error_code/message` 和失败包。

## 请求与模型合同

[contracts.py](../self_improving/harness/x2env/contracts.py) 中 `Model` 采用 Pydantic strict、
frozen、`extra="forbid"`、禁止 NaN/Infinity。Python 构造时使用元组和真实整数；读取 JSON 使用
`X2EnvRequest.model_validate_json(...)`，不要把 JSON 的列表直接当成 strict Python 元组。

`X2EnvRequest` 包含前述文本、images、video、seed、allowed_sources、constraints、
idempotency_key、output_dir。Python 中媒体和 output 路径必须已经绝对化；至少一种非空输入。
`RequestConstraints` 当前只有 `allow_cousin=False`，没有可自由附加的物理阈值或 arbitrary options。
`InputMedia` 只有 `path` 字段，不接受 URL、base64 或预填 SHA。

输入采集默认每个媒体源不超过 64 MiB，单帧解码不超过 1600 万像素；视频最长 60 秒、600 帧，
累计解码预算 1 GiB，解码命令预算 60 秒。图片须能确定性解码，动画多帧图片不按静态图片接受。
视频通过 FFmpeg/FFprobe 解码并记录逐帧证据；文件后缀不会替代实际解码。以上来自 `IngestLimits`，
当前不是 CLI 自定义参数。

### SceneIR 与结构化建议

SceneIR 是 Harness 接受并保存的共享场景描述，revision 与原始输入 SHA 绑定。模型返回的
`SceneIntentProposal` 是建议，经过控制器接受后才能成为可信 SceneIR。输入调用者提交 prompt/media，
不应预填最终 proposal 绕过理解步骤。

SceneIR 有 1–8 个 entities（包含结构支撑），实体 id 唯一且不能为 `world`。实体字段为 category、
role、color、dimensions、material、pose、articulation_state、逐字段 provenance。结构支撑 role
只允许 `table/worktop/counter`；一般物体使用 foreground。尺寸与坐标以米计，yaw 以度计
（-180 到 180）；`pose.frame` 只能是 `world` 或另一实体 id。未知坐标/尺寸分量可以为 null，
进入编译前须完成允许范围内的 grounding。

关系支持 `on/inside/left_of/right_of/front_of/behind/near`，引用已知实体，不允许自指或支撑循环。
字段来源区分 text/image/video、explicit/inferred/override，并绑定媒体 hash/索引/帧。
这些是表达合同；当前 compiler 会拒绝 articulation profile，不能因 schema 能写
`articulation_state="open"` 就认为打开柜门已能仿真。

生成布局需要 deployment 显式启用 scene_design_policy。默认政策 disabled，不会自动把未知
物理尺寸当成已测量值。生成式补全与真实尺度恢复是不同结论，详见
[grounding](../self_improving/harness/x2env/grounding.py)、
[生成布局](../self_improving/harness/x2env/design_grounding_v2.py) 与
[compiler](../self_improving/harness/x2env/compile.py)。

### Snapshot、ToolResult 与三个 Skill

`WorkflowSnapshot` 除请求、workflow_id、status、revision 外，含 owner、stop_reason、
required_resources、operations，以及 input_bundle/proposal/scene_ir/pending_scene_ir/grounding/
asset_resolution/resolved_assets/compiled_scene/replay_result/observation/diagnosis/validation/
environment_package/revisions 的 ArtifactRef。未完成阶段对应字段可以为空。

workflow 状态只有 `active/succeeded/failed/blocked/cancelled`。Operation 另外允许 `running`，
有 capability/version、起止时间、可选 result 和 repair reservation。workflow revision 是持久
推进次数，不能解释为模型修订次数或 SceneIR revision。

`ArtifactRef` 仅有 sha256、size_bytes、media_type。它不是任意远端 URL，也不内含本地路径。
`ToolResult` 有 operation_id、status、outputs、error_code、message；成功时 error_code 必须为空，
其它状态必须有 error_code。

默认[执行绑定](../self_improving/harness/x2env/skill_execution.py)实际注册：

- `x2env.compile@1.0.0`：`CompileCall(scene_ir, assets, output_root, seed) → CompiledScene`。
- `x2env.replay@1.0.0`：`ReplayCall(scene, package_root, output_root, timeout) → ReplayResult`。
- `x2env.validate@1.0.0`：`ValidateCall(scene_ir, observation, diagnosis) → ValidationResult`。

控制器负责选择和调用。`asset.resolve/observe/revise` 是内部工具名，默认
`describe_capabilities()` 只返回上述三个已绑定 Skill，不能把允许的名字当作已注册接口。
`CapabilityRegistry.invoke(name, version, value)` 要求精确版本和精确输入模型类型；其返回值也进行
类型核验。没有对用户暴露 REST 路由或 MCP 服务。这些底层调用缺少 workflow 编排，不推荐用户绕过
Harness 直接把 invoke 当成 submit。

## Deployment 的字段边界

`load_deployment(path)` 读取绝对、非符号链接 JSON，最大 2 MiB。`Deployment` schema_version
为 `x2env.deployment.v1`；`state_dir` 必填，其余可为空并在执行到相应环节时产生缺资源结果。

- `timeout_seconds` 默认 1770，范围 1–1770。项目当前核验预算可能比模型上限更短，以部署和
  当前验收规则的较小值为准。
- `codex`：绝对 executable、sha256、model、可选 router。代码 schema 默认 `gpt-6-astra`；
  当前用户指南中的授权部署使用 `openai/gpt-5.6-terra`，并要求配套 private router。
  这两者不要混淆为自动模型切换。密钥文件和固定路由细节见用户指南。
- `compile_policy`：thickness_m、surface_height_m 都大于 0，friction 大于等于 0；无内置默认值。
- `scene_design_policy`：默认关闭的 `asset_anchored_simulation`，也可显式选择 `generated_layout`。
  结构默认开启时必须声明 world anchor，不能由 prompt 改部署政策。
- `local_enabled`：默认 true；没有因此自动导入任何目录为资产库。
- `genesis`：runtime_roots 的 interpreter/stdlib/distributions/native/genesis，及 denied_roots、
  可选 source_identity；资源由部署者准备。
- `web`：provider_config 是 `{path, sha256}` pin；license_records 可选，同样 pin。允许来源
  列表中有 web 不代表已配置 provider。
- `reconstruction`：source_root/source_commit、python/python_sha256、segmentation_runtime/
  reconstruction_runtime pins、model_refs 和可选 derivation_authorization；模型权重、许可与外部
  运行环境不会由 request 自行提供。

字段、额外验证与默认值以[deployment.py](../self_improving/harness/x2env/deployment.py)为准。
配置模板属于运行资源说明；不要把占位 SHA 当成有效配置提交执行。

## 交付文件、证据与能力标记

成功 `package(..., output=...)` 生成 `OUTPUT/result.json`、`OUTPUT/workflow.json` 和
`OUTPUT/environment/`。后者包含 manifest、包内场景入口、资产、loader、runtime 说明与 evidence。
delivery 字典列出 environment_package、manifest、manifest_sha256、images、videos、logs 的绝对路径，
以及 workflow/revision 和各项结论。通过这些数组展示媒体，比猜测文件名可靠。

成功交付核验已提交 completion、成员和证据闭包，但此 API 返回的 `copy_run="not_run"`、
`release_qualified=false`、`sim_ready=false`、robot_policy_evaluated=false、
data_collection_evaluated=false。物理/视觉 passed 与复制运行、正式资格是独立结果。
后续某个包的独立 copy-run 证据不会自动改写历史 delivery 字典。

失败交付生成 `OUTPUT/result.json` 和 `OUTPUT/failure/`，含 manifest.json、workflow.json、
error.json、human-readable.md、可用的 scene-ir.json 和 partial/ 证据。environment_package 为 null；
图片视频可能是输入或部分结果，不能当成成功仿真输出。当前 human-readable.md 是控制器生成的
英文摘要，详细模型诊断若已产生会保留在 evidence 中；并不保证每个失败都有模型生成的中文解释。

输出目录默认必须不存在。`reuse_existing=True` 是已有导出逐字节核对，不是覆盖开关；CLI submit
使用此选项处理重复提交，独立 CLI package 则要求新目录。导出失败保留部分目录供检查。
对失败包重复导出并不自动清理或修复额外文件；交付完整性以对应 manifest 和导出检查为准。
实现见[成功交付](../self_improving/harness/x2env/delivery.py)与
[失败包](../self_improving/harness/x2env/failure_bundle.py)。

内部 state 固定为 `STATE/harness.sqlite` 与 `STATE/cas/`。CAS blob 位于
`STATE/cas/<sha256前两位>/<sha256>`；用户展示与下载优先使用导出目录，避免把内部 CAS 地址作为
永久外部 API。模型执行目录常为 `STATE/attempts/<workflow_id>/<operation_id>/`；完整证据索引
应从 snapshot 的 ArtifactRef 和交付 manifest 追踪，不应遍历旧工作区猜测文件。

## Schema 导出与接口升级

Python 类型是可编辑合同源；生成快照位于
[json_schemas](../self_improving/harness/x2env/json_schemas/)，含
[API 字段索引](../self_improving/harness/x2env/json_schemas/api-fields.md)。该导出器涵盖
contracts 模型及指定 grounding 模型，不等于 deployment/Skill 输入所有模型都已有单独快照。
其它类型以所在 Python 模块的 `model_json_schema()` 为准。

```bash
python script/export_x2env_schemas.py --check
```

此只读比对成功退出 0，漂移打印 `schema drift: ...` 并退出 1。修改合同时可运行无 `--check`
的命令生成快照，再审阅差异、补边界测试并提交。不要手工同步 JSON 字段，也不要把历史 namespace
静默映射到 canonical 接口。开发集成和测试流程分别参考后续接入与测试指南。
