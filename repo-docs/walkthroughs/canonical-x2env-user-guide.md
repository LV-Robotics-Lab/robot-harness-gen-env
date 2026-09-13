# Canonical x2env 用户操作指南

本指南描述唯一 `x2env` CLI：用户提交输入，Harness 管理模型、资产获取、编译、
Genesis 回放、观测、诊断与输出。用户不直接操作 Codex CLI/MCP，也没有第二套外部 agent 工作流。
这是开发实验入口，不是已认证的机器人策略测试或数据采集服务。

## 先确认版本和资源

以下命令以 Linux、Python 3.13 的独立环境为例。项目元数据允许 Python >=3.11，
但实际依赖组合及 Genesis 运行环境必须另行核验；安装 Python 包不会自动下载模型或部署仿真器。
从要审阅的固定提交安装，记录 commit，不在已有 sealed runtime 或用户共享环境里直接升级依赖：

```bash
python3.13 -m venv /absolute/new-x2env-env
/absolute/new-x2env-env/bin/python -m pip install '.[platform,dev]'
/absolute/new-x2env-env/bin/x2env --help
```

上述安装命令须在固定源码根执行；wheel 安装包含已有 Yuxin/OpenXSim Python 包，
不代表这些 provider 的外部资源或凭据已可用。`platform` 包含 Shapely 2.1.2。
Genesis、渲染原生库、FFmpeg、受管理模型可执行文件和可选重建后端，仍由部署方明确配置。
不要通过伪造 catalog、预填模型 proposal 或修改许可证让缺依赖分支通过。

当前能力及逐次结果分别看 [canonical 模块说明](../modules/canonical-x2env.md) 和
[进度入口](../../self_improving/golden_e2e_progress/README.md)。下面的命令配方本身不是成功证据。

## 部署配置与私密模型路由

`--deployment` 必须是绝对路径、非符号链接的 JSON 文件。配置字段严格校验，不接受任意额外字段。
最新授权模型路由使用 `openai/gpt-5.6-terra`；必须同时显式配置固定私有 router。
历史 `gpt-6-astra` 配置仍可解析，但不能把历史运行标为新的 Terra 运行。

密钥文件放在本地 `.x2env-private/` 或仓库外的受控目录；仓库根 `.x2env-private/` 已被忽略。
密钥由部署方通过安全渠道放入，不写在 JSON、命令参数、Git 或日志里。
文件必须属于执行用户、权限恰为 `0600`、非符号链接，内容为单个非空 token；
Harness 仅向受管理模型子进程传递环境变量。不要把环境变量完整打印出来排查问题。

```bash
chmod 600 /absolute/private/router-key
git check-ignore .x2env-private/router-key
```

第二条只检查仓库内约定路径，不能证明仓库外任意路径受保护。
以下为配置模板；`REPLACE_*` 必须由部署方替换，尤其 SHA 必须来自实际文件，不能原样运行：

```json
{
  "schema_version": "x2env.deployment.v1",
  "state_dir": "/absolute/new-experiment/state",
  "timeout_seconds": 1170,
  "codex": {
    "executable": "/absolute/approved/codex-real",
    "sha256": "REPLACE_WITH_ACTUAL_EXECUTABLE_SHA256",
    "model": "openai/gpt-5.6-terra",
    "router": {
      "base_url": "http://100.64.0.1:8324/v1",
      "api_key_file": "/absolute/private/router-key"
    }
  },
  "compile_policy": {
    "thickness_m": 0.04,
    "surface_height_m": 0.75,
    "friction": 0.5
  },
  "scene_design_policy": {
    "mode": "generated_layout",
    "enabled": true
  },
  "local_enabled": true,
  "genesis": {
    "runtime_roots": {
      "interpreter": "/absolute/declared/python",
      "stdlib": "/absolute/declared/stdlib",
      "distributions": "/absolute/declared/distributions",
      "native": "/absolute/declared/native",
      "genesis": "/absolute/declared/genesis"
    },
    "denied_roots": ["/absolute/forbidden/original-assets"]
  }
}
```

表面高度、厚度、摩擦以及允许的生成布局是明确的仿真设计政策，不是从图像量出的真实物理量。
`generated_layout` 默认并未开启，只有上述显式授权才允许补未知设计字段；已知输入不得覆盖。
不要为运行方便把 deny root 写为 `/`，也不能与必须读取的运行环境、当前包目录重叠。

模板没有配置 web 或 reconstruction，因此不能据此宣称三种来源均已部署。
web 需要 `provider_config: {path, sha256}`，以及相应许可证据；重建需要固定外部源码提交、
解释器 SHA、分割/重建 runtime 配置 pin、模型身份和派生授权。字段以
[deployment.py](../../self_improving/harness/x2env/deployment.py) 为准。
缺资源会留下结构化阻断，不会静默制造资产。已有本地资产须经过不可变 Registry 登记、
许可和完整依赖闭包检查；不是把任意目录路径当作已登记版本。

## 四种提交方式

用安装环境中的 `x2env`，或等价的 `python -m self_improving.harness.x2env.cli`。
每次新请求指定一个新输出目录和稳定、唯一的幂等 key。以下省略 `--source`，
按默认 `local → web → reconstruction` 顺序尝试部署可用的 adapter；不是为某案例强制指定来源。

```bash
x2env submit --deployment /absolute/deployment.json \
  --text '在桌面放一个粉红色鼠标。' --seed 11 \
  --idempotency-key experiment-text-1 --output /absolute/outputs/text-1

x2env submit --deployment /absolute/deployment.json \
  --image /absolute/inputs/reference.png --seed 23 \
  --idempotency-key experiment-image-1 --output /absolute/outputs/image-1

x2env submit --deployment /absolute/deployment.json \
  --video /absolute/inputs/reference.mp4 --seed 37 \
  --idempotency-key experiment-video-1 --output /absolute/outputs/video-1

x2env submit --deployment /absolute/deployment.json \
  --image /absolute/inputs/reference.png \
  --text '参考图片，在桌面放一个蓝色方块，颜色以文字要求为准。' --seed 41 \
  --idempotency-key experiment-multimodal-1 --output /absolute/outputs/multimodal-1
```

纯图像和纯视频请求不要暗中附加文本；视频输入不是机器人动作复演能力的授权。
可重复 `--image` 提交多张图片；视频参数至多一个。长文本用 `--text-file`，
不能同时使用 `--text`；文本文件限制为 128 KiB。
非矩阵实验可显式重复 `--source local --source web` 缩小允许来源。
`--allow-cousin` 当前会明确报告 `digital_cousin_selection` 未实现，不能当成可用功能开关。

本次 [matrix v2](../../self_improving/golden_e2e_progress/qualification-matrix-v2.json)
固定了原媒体 SHA、四条输入、seed 和默认来源。上面的示意图片路径不能替代矩阵原媒体。
本轮每例包括清理最多 20 分钟：配置 `timeout_seconds=1170`，预留 30 秒退出；
整体窗口还受 matrix v2 的固定截止时间约束，失败不重置窗口、不自动原样重试。
CLI 旧默认仍为 1770 秒，故本轮必须显式采用 1170，不应误称默认已经变为 20 分钟。

## 查看状态、取包、恢复和取消

CLI 输出 JSON，保留 `workflow_id`、真实 snapshot 和 `command_wall_seconds`。
`submit` 前景只调用一次 Harness `resume`，不是不断重提的轮询器。
完成后 `delivery` 给出环境包或失败包入口；返回状态为 blocked 不等于没有已有产物。

```bash
x2env status --deployment /absolute/deployment.json --workflow-id WORKFLOW_ID
x2env package --deployment /absolute/deployment.json --workflow-id WORKFLOW_ID \
  --output /absolute/outputs/new-review-copy
x2env resume --deployment /absolute/deployment.json --workflow-id WORKFLOW_ID
```

`status` 是读取，不会重跑模型；退出码 0 只表示命令成功，不代表 workflow 成功。
`submit/resume` 的成功为退出码 0，blocked/active 为 2，失败或取消为 1。
`package` 可导出失败/阻断/取消的已有现场，其退出码 0 同样不授成功环境能力。
成功交付包含 `environment/`、`result.json`、`workflow.json`；图片、视频和日志从结果的实际路径读取。
当前导出结果仍明确 `copy_run=not_run`、`release_qualified=false`、`sim_ready=false`；
另一次真实复制运行的证据不能靠修改这些原导出字段来补写。

幂等重提须保持同一 key 和同一完整请求，包括输出目录；相同请求复用已有 workflow，
不意味着重跑失败步骤。`submit` 对已有同一交付只读核验后复用，不覆盖；单独 `package`
要求新目录。目的成员有漂移就拒绝，不能删除失败目录后伪装第一次执行。

`resume` 只针对已有 workflow：成功、失败、取消终态不会重新执行。
非终态能否继续由 Store/controller 的 owner、操作日志和恢复规则决定。
资产修订 owner 丢失而没有可提交结果的情况可能返回 `color_repair_recovery_requires_audit`，
不会自动再次改资产。资源已补齐也不保证所有历史阻断都可恢复；先读 error、required resources
和最近操作，不编辑 SQLite、不改 receipt、不重复 submit 同一 key 来绕过门禁。

没有单独的 `cancel` 子命令。前景执行时用 Ctrl-C；远程管理时只向准确识别的本次 owner
发 SIGINT，让日志和状态完成收尾。不要按名称杀全部 Python/Codex/Genesis 进程。
取消响应保留 workflow id；随后可 `status` 和 `package` 查看现场。
包导出本身失败会返回 `package_failed`，不能把它掩盖成 workflow 成功。

## 如何判断结果、定位失败

- 先读顶层 `status/error_code/stage/workflow_id`，再读 snapshot 的停止原因、所需资源和操作结果。
- `blocked_external_resource`：检查部署文件、模型/资源可用性、许可与源 pin；不要读取或传播密钥值。
- `model_authentication_required`：受管理模型认证失效，父状态列出`managed_codex_authentication`；
  由部署方恢复凭据，不消耗模型重试，不把“本机缓存已登录”当作服务端授权有效的证明。
- `command_timeout` / `command_interrupted`：保留日志、已完成资产和状态；不要把一次超时推断为某种网络故障。
- `incomplete_dual_profile` / 缺新鲜图像：物理或观测仍是 `not_run`，不能拿一张预览图替代。
- `no_feasible_support_surface` / `footprint_outside_support_domain`：查看实测面、完整投影、接触和布局；
  不加静态标记、不填孔、不改冻结物理阈值制造通过。
- 视觉判断只作独立意图证据。物理、视觉、copy-run、发布状态必须分别看，不能合成一个含糊的“sim-ready”。

真实可迁移性需要在新目录复制后、禁止读取原 workspace/CAS/资产路径的环境中执行包内 loader，
产生新帧和连续视频。只有 JSON manifest 校验通过不等于这一步已经运行。
机器人策略和数据采集能力按实际报告分别标记，未运行就是 false/not_run。

当前覆盖率政策为测量和报告缺口，不再因不足精确 100% 阻断；所有 active 测试、lint、
源码身份和测量完整性仍是门。旧 matrix v1 及历史失败不回填、不改称本轮成功。

## 实现依据

[CLI](../../self_improving/harness/x2env/cli.py)、
[部署组装](../../self_improving/harness/x2env/deployment.py)、
[Harness 生命周期](../../self_improving/harness/x2env/harness.py)、
[交付校验](../../self_improving/harness/x2env/delivery.py)、
[测试与覆盖报告门](../../script/x2env_test_groups.py)。
当前真实结果以进度台账和具体 run 的原始证据为准，本指南不替未完成案例授予能力。
