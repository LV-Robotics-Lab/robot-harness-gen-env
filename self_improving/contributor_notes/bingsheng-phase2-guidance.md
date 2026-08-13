# Phase 2 Environment Generation Guidance

## 分工

- **4.1 Harness：主责**——搭建统一的 Skill/MCP 注册、运行、记录和门禁框架。
- **4.3 Text2Env：**——把已验收的 RoboTwin SceneGen Stage 0–5 接入 Harness，不重做 V1。
- **4.7 Transfer：**——把环境迁移封装为 Harness 中可复用、可验证的路线。

工作主线：

```text
4.3 Text2Env（入口与门禁）
        ↓ EnvironmentPackage
4.1 Harness（统一枢纽）
        ↓ versioned exchange package
4.7 Transfer（跨模拟器迁移）
```

## 当前工作区基线

| 目录 | 用途 | 当前定位 |
| --- | --- | --- |
| `RoboTwin/` | RoboTwin/SAPIEN 仿真器、资产与任务环境 | 运行和回放后端 |
| `robot-harness-gen-env/` | 文本到场景的确定性编译、打包与物理验证 | 4.3 的 V1 基线；当前版本 `0.1.0` |

现有 Text2Env 主链路为：

```text
中英文文本 → SceneSpec → 资产匹配 → ResolvedSceneSpec
→ RoboTwin 场景包 → SAPIEN 回放 → 物理/视频门禁
```

现有场景包已保存请求、类型化场景、解析后场景、回放入口和 SHA-256 manifest；运行时报告与解析后场景保持哈希绑定。现有仓库只负责 `/gen-env`，暂不包含通用 Harness 和跨模拟器迁移实现。

## 4.3 Text2Env：先形成可靠入口

**输入：** 中英文文本、配置、种子和资产目录版本。

**输出：** `SceneSpec`、`ResolvedSceneSpec`、`EnvironmentPackage`、验证报告或结构化 `Blocker`。

简单要求：

- 复用现有 Stage 0–5；不让语言模型直接提供代码、资产路径、最终位置或四元数。
- 将编译、验证、回放注册为带版本的能力。
- 固定输入、配置和种子时可复现，并保留来源、哈希、求解尝试与失败原因。
- Schema、物理、回放或回归门禁失败时，不向 Harness 发布环境包。

## 4.1 Harness：统一枢纽

Harness 负责连接所有环境生成路线，而不把具体生成逻辑写死在框架中。

最小公共能力：

- `CapabilityRegistry`：发现、匹配、参数补全、生成、编译、验证和迁移。
- 公共契约：`EnvironmentPackage`、`RunState`、`Event`、`ArtifactManifest`、`Blocker`。
- 记录：工具/适配器版本、依赖、种子、来源、哈希、候选淘汰原因、验证结果和错误码。
- 控制：优先复用已有方案、有限重试、失败回滚、结构化 blocker、发布门禁。
- 路线接入：Text2Env、Anchor2Env、互联网资产导入和 Transfer。

每条路线都应表现为带版本号的 Skill/MCP 调用，并至少有一个确定性测试样例。

## 4.7 Transfer：从统一包迁移

**输入：** Text2Env、Anchor2Env 或外部导入产生的 `EnvironmentPackage`。

**过程：** 先编译为带版本的中间交换包，再交给源/目标模拟器适配器。

**输出：** 目标模拟器环境包、迁移验证报告、迁移损失清单或结构化 `Blocker`。

简单要求：

- 显式转换几何、坐标系、关节、碰撞、材质、质量/惯量、相机、物理参数、重置条件与任务判定。
- 记录适配器版本与哈希；不支持或有损内容不得静默降级。
- 对刚体、关节物体和相机敏感案例量化迁移损失。
- 只有接触、控制、渲染和任务结果达到既定容差，才声明迁移前后等价。

## 统一发布门禁

一次运行只有同时满足以下条件才可发布：

1. Schema 和包完整性校验通过。
2. 来源、版本、种子和哈希可追溯。
3. 物理、碰撞、稳定性、可见性和回放检查通过。
4. Transfer 场景的迁移损失在容差内。
5. 回归测试通过；失败时生成明确错误码和 blocker。

## 实施顺序

1. 将现有 `robot-harness-gen-env` 包装成 Text2Env v1 能力，明确输入、输出和门禁。
2. 建立 Harness 的最小注册表、公共契约、运行状态和产物记录。
3. 用 Text2Env 作为首条端到端标准路线验证 Harness。
4. 定义中间交换包和适配器接口，接入 Transfer。
5. 增加迁移损失报告与回归门禁，再接入其余环境生成路线。

## 范围边界

- 本阶段不重新证明 V1 场景生成效果，重点是工程化封装与复用。
- Harness 不负责机器人策略训练、数据采集或模型评估。
- 未知参数保留为结构化未知值，不猜测；任何降级、修复和重试都必须可审计。
