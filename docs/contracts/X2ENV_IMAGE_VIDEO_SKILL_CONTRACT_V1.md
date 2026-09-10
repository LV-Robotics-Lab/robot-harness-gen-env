# Image2Env / Video2Env exact Skill 合同 v1

- 状态：Contract implemented；runtime unimplemented；unqualified；unregistered
- 集成基线：`2bdeafd836d641b4f14aef316a21fbcd736f55e1`
- 合同日期：2026-09-10；compile attempt 修订：2026-09-11
- Pydantic：`self_improving/harness/schemas/x2env.py`
- exact namespace：`self_improving/harness/x2env_contracts.py`
- JSON Schema：`self_improving/harness/json_schemas/harness.{image2env,video2env}_*.schema.json`

本合同正式冻结 image-primary 与 video-primary 两组 compile/replay/validate seam。它只提供严格类型、
committed schema、exact descriptor metadata 和后续实现门；没有 handler、application 或 qualification
bundle，因此六个 Skill 当前不能注册或执行。

## 1. Exact descriptor namespace

| Exact Skill ref | MCP tool | input `$id` | output `$id` | repeatability | attempts |
|---|---|---|---|---|---:|
| `image2env.compile@1.0.0` | `image2env_compile_v1_0_0` | `harness.image2env_compile_input.v2` | `harness.image2env_compile_output.v1` | evidence invariant | 2 |
| `image2env.replay@1.0.0` | `image2env_replay_v1_0_0` | `harness.image2env_replay_input.v1` | `harness.image2env_replay_output.v1` | evidence invariant | 2 |
| `image2env.validate@1.0.0` | `image2env_validate_v1_0_0` | `harness.image2env_validate_input.v1` | `harness.image2env_validate_output.v1` | content bitwise | 1 |
| `video2env.compile@1.0.0` | `video2env_compile_v1_0_0` | `harness.video2env_compile_input.v2` | `harness.video2env_compile_output.v1` | evidence invariant | 2 |
| `video2env.replay@1.0.0` | `video2env_replay_v1_0_0` | `harness.video2env_replay_input.v1` | `harness.video2env_replay_output.v1` | evidence invariant | 2 |
| `video2env.validate@1.0.0` | `video2env_validate_v1_0_0` | `harness.video2env_validate_input.v1` | `harness.video2env_validate_output.v1` | content bitwise | 1 |

`X2ENV_SKILL_CONTRACTS` 是只读合同目录，不是 Registry catalog。每项都固定
`handler_registered=false`、`qualification_available=false`。只有调用方已经核实真实资格时，
`build_x2env_skill_descriptor(..., qualification_verified=True)` 才能组装 `SkillDescriptorV2`；该 helper
不安装 handler，也不把合同存在解释成资格通过。禁止 alias、`latest`、version range 和跨模态参数兼容。
两个 compile Skill 尚未资格化，因此在首次资格前把 executable descriptor input 切到 v2；原 v1 schema
仍保留为历史 parse-only 合同，不能用于新 operation。exact Skill ref 与 MCP tool name 没有变化。

## 2. Source media identity

### ImageSourceMedia

`harness.image_source_media.v1` 绑定原图 ArtifactRef SHA-256、规范 PNG ArtifactRef、probe ArtifactRef、
width、height、color mode，并固定 decoded/unique frame count 都为 1。三项 ArtifactRef 都必须使用
`artifact://sha256/<digest>`；原图 MIME 必须是 `image/*`，规范图必须是 `image/png`。

### VideoSourceMedia

`harness.video_source_media.v1` 绑定容器 ArtifactRef SHA-256、probe ArtifactRef、完整解码帧顺序 manifest、
width/height、duration milliseconds、FPS numerator/denominator、decoded/unique frame count 和
`frame_sequence_sha256`。`unique_frame_count <= decoded_frame_count`；抽帧集合不能替代完整帧顺序
manifest。Image schema 与 Video schema 以不同 `schema_version` 作 discriminator，两个 compile seam
不会互相接收。

媒体 schema 证明的是“哪些字节和解码事实被声明”；production ingest 仍须从 CAS 字节重新解码并核对，
不能信任调用方填写的尺寸、时长或帧数。

## 3. Compile

两种 compile input 都要求：模态专属 `source_media`、CAS 中的
`harness.scene_interpretation_proposal.v1`、目标 Genesis profile、资产目录、seed、可选 predecessor 和
repair evidence。`model_execution_allowed=false` 是常量：外部 Codex 可先生成 proposal，但 Skill 内部
不得再调用 planner/VLM，也不得把 proposal 当物理事实。

v2 另外要求 `attempt` discriminated union：

- `harness.initial_compile_attempt.v1` 只声明 `kind=initial`；
- `harness.prompt_revision_compile_attempt.v1` 必须携带 current `PromptRevision`、该 revision 的
  `effective_prompt`、非空排序唯一 failure receipts 与 predecessor package。

当前 workflow head 为 PromptRevision 时，Harness 还会在 command CAS/child allocation 前核对 target
exact Skill、workflow/principal/revision/state、revised prompt、完整失败 lineage、top-level package 与
video→image selected frame；initial attempt、旧 v1 或任何漂移都不能生成新 operation。

资产 acquisition policy 为 `auto`、`exact_only`、`digital_cousin_allowed` 或 `generate_on_miss`。
每个对象输出一个 `harness.x2env_asset_decision.v1`，lane 必须是：

- `environment_reuse`：完整环境候选复用，仍需与当前输入/profile 重核；
- `exact_asset_reuse`：已有资产 exact identity 与资格证据；
- `digital_cousin_reuse`：必须附 task-relevant 等价证据，不能称 exact；
- `fresh_asset_generation`：必须附 generation receipt 和 staged candidate，不能冒充既有 qualified asset。

compile output 包含 `X2EnvEnvironmentPackage`、SceneIR/TaskIR、资产决策、staged asset refs、static
validation 与 compile receipt。它固定 `simulator_executed=false`、`publishable=false`；fresh generation
lane 与 staged assets 必须同时出现或同时缺失。

## 4. Environment package

`harness.x2env_environment_package.v1` 固定：

- `route_id` 与 exact producer 必须分别匹配 `image2env.compile@1.0.0` 或
  `video2env.compile@1.0.0`；
- source media 模态必须与 route 匹配；
- format 为 `harness.genesis_portable.v1`，backend 为 `genesis`；
- SceneIR、TaskIR、asset closure、binding manifest、runtime lock 全部是 CAS ArtifactRef；
- `representation_origin` 明确为 `harness_native` 或 `legacy_genenv_adapted`；后者强制
  `harness.legacy_genenv_adapter_receipt.v1`，前者禁止附 legacy receipt；
- package 在 compile 阶段固定 `simulator_ready=false`、`collision_qualified=false`。

所以，Gujie 的 `genenv.*` 目录即使包含 URDF、Genesis preview 或旧 physics status，也只能先进入
legacy adapter；适配后的 package 仍是 candidate。只有同一 Harness workflow 的 replay/validate 才能
形成新的 runtime/publishability 证据。

## 5. Replay

replay input 要求同 route package、compile receipt、Genesis runtime attestation、seed 和完整 physics
plan。physics plan 固定 collision enabled，并要求正数 settle steps、observation steps、video frames 与
FPS。请求 `genesis.robot_policy@1` 时 robot probe steps 必须非零。

replay output 明示 `simulator_executed=true`、`collisions_enabled=true`、正数真实 steps、完整顺序视频
manifest、total/unique frames、runtime evidence、physics report、runtime receipt 和至少一个 fresh
observation。请求 robot-policy profile 时 robot probe report 与 trajectory 都必填。process exit、render
或稳定化 candidate 不能单独构造该 output；production handler 必须从新 materialization root 执行真实
Genesis load/build/step 后写入。

## 6. Validate / publishability

validate input 从同一 portable closure 重读 package、compile/replay receipts 与两项 qualification、
runtime evidence、physics report、media verification 和 fresh observations，并要求 requested profile 与
package 一致。validate output 固定 `portable_closure_reloaded=true`、`evidence_recomputed=true`。

只有 `validation_status=pass`、无 blockers 且 requested profile 位于 satisfied profiles 时，才可表达
`publishable=true`。其他结论必须 `publishable=false` 且至少一个 blocker。compile 或 replay 成功不拥有
publishability；本合同也没有实现后续 `environment.promote`。

## 7. External Codex boundary

Codex 是 Skill seam 外的 planner/视觉判断者：读取受信上下文，生成 interpretation proposal、route 或
advisory，再由 Harness 严格验证和执行。六个 Skill 不加载模型、不接收自由 tool name、不让 advisory
覆盖媒体、碰撞、物理或 profile 失败。video→image fallback 必须显式建立单帧 ArtifactRef 和新的
image2env operation；禁止把 VideoSourceMedia 原地当 ImageSourceMedia。

## 8. 当前可声明范围

当前完成的是 16 个新增严格 public schemas（12 个 Skill I/O、两类 source media、asset decision、
environment package）、55 份总 schema snapshot 和六项 contract-only descriptor metadata。没有新增
runtime handler、qualification bundle、Registry entry、System2 route、MCP tool、真实 Genesis run 或
publishable 结果。
