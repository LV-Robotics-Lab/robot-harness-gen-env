# Gujie 工作与来源档案

## 角色与归属口径

Gujie 的工作集中在 x2env 多模态输入、`genenv.*` 任务包、SimFoundry/URDF 导入和 Genesis 真实物理
工作流。下表中的“Gujie 工作”是其编写的 adapter、转换、求解、验证和编排逻辑；Genesis World、
SimFoundry、WorldComposer、媒体工具以及输入资产仍归各自上游。

团队把该来源域称为 “Gujie 分支/工作”。固定提交的 git author 实际包含
`yuhang5090 <1205492990@qq.com>` 和 `LV-Robotics Lab <lv.robotics.lab@gmail.com>`；现有证据不足以
把这些 git identity 与自然人 Gujie 做一一等同，因此最终核对同时保留团队责任名和实际 author。
`9e8d28c`、`a8ced27` 记录为 `yuhang5090`，后续物理验收加固 `eb0b710` 记录为 LV-Robotics Lab。

## 具体模块与功能

| Gujie 模块 | Gujie 完成的具体功能 | Harness 拟复用/改造方式 | 当前状态 |
|---|---|---|---|
| `reconstruct_media.py` | `a8ced27` 建立图片/视频文件 hash、media probe、输入规范化、外部 reconstruction/SimFoundry 编排、抽帧复核和上游重建导入；`eb0b710` 又加固物理判据/有界修复相关路径 | 把输入、逐帧事实、外部命令身份和产物转成 CAS ArtifactRef/ToolResult；外部命令不能直接推进 Harness 状态 | candidate |
| `media_support.py` | 提取场景观测；执行 mesh geometry gate；对资产候选 rank/choose；用颜色差异辅助匹配；按 footprint 物化并把支撑物加入场景 | 作为 image/video asset-resolution 内部实现；候选、拒绝原因、selected asset 与视觉证据分别入 receipt | candidate |
| `media_checkpoints.py` | 对媒体流程各阶段保存 snapshot/config；按配置变化失效下游 checkpoint；判断上游结果能否复用 | 映射为 durable operation checkpoint，配置和输入 digest 进入 Harness idempotency/receipt | candidate |
| `task_output.py` | 由 request hash 生成任务身份和安全输出目录；`TaskOutput` 管理阶段产物、manifest 与 `verify()` | `genenv.*` 保留为外部格式，anti-corruption adapter 转为 Harness RunSnapshot/ArtifactRef，不直接成为信任根 | candidate |
| `standard_urdf.py` | `9e8d28c` 建立标准 URDF 的 link/joint/mesh closure、portable package 检查、Genesis morph 及 collision/friction/预览审计；`eb0b710` 后续加固 | 用于资产 closure 检查和 Genesis loader adapter；Harness 重新绑定每个文件 hash、许可与 representation identity | candidate |
| `import_simfoundry_assets.py` | 将 SimFoundry 资产库转换为可消费资产目录并生成 previews | 作为第三方 asset importer；保留 SimFoundry gitlink、原资产来源和许可，不能把第三方字节记为 Gujie 原创 | candidate |
| `import_simfoundry_scene.py` | 读取环境/对象/pose，转换 scene package，复核转换结果，并生成 reference/orbit preview | 将 `genenv.*` scene 映射到完整 SceneIR/portable bundle；pose、坐标系和绝对 locator 需要显式规范化 | candidate |
| `position_solver.py` | 用 polygon/half-plane/closest-point 约束做 bounded position solve；从 scene package 生成布局解 | 可复用为 Genesis 布局后端，但必须把 seed、输入约束、尝试次数、失败原因和最终位姿写入 Harness evidence | candidate |
| `scene_stabilization.py` | 判断数值配置是否具有区分力；基于布局/几何/轨迹评估稳定性；运行并复核 stabilization evidence | 作为 replay 的中间观测，明确“已稳定化”不等于 validation pass | candidate |
| `validate_imported_scene.py` | `a8ced27` 建立真实 Genesis baseline/numerics/friction 验证；`eb0b710` 加入判据分类、有界修复搜索和固定地面根处理；按 grounded force、接触、位移、旋转、穿透和支撑 SDF 形成 checks | 作为 `genesis.rigid_scene@1` 验证实现候选，结果必须绑定当前 Harness input/asset/runtime identity | candidate |
| `physics_criteria.py` | 分类 physics failure、区分可调与不可调项、计算 penetration budget 并给 checks 加结构化注释 | 错误映射为稳定 Blocker/diagnosis facts；不能让 prompt fallback 绕过硬物理门 | candidate |
| `scene_physics_workflow.py` | 串联 derive、SDF、规划 margin、分阶段 physics config、agreement 与 verify | 拆成 Harness operation stages，每阶段出 receipt 和 fresh observation，支持失败后受限续跑 | candidate |
| `replay_text_half_dt.py` | 从 baseline evidence 生成 half-dt 输入；检测输入漂移；自由重放并可选录制；比较 baseline 与 half-dt | 作为数值稳定/可复现攻击测试，不能用 half-dt “碰巧通过”替代 baseline 通过 | candidate |

## 固定来源

- 权威独立仓库：`/home/jingxiang/gujie/gen-env`
- 当前 committed HEAD：`eb0b710581fd7794bc01b447b0f77cb871c8a711`
- 重点祖先：`9e8d28c24ca72ebf4001c2460dc4b174cef603c0`、
  `a8ced2732b48d3eb89e5c40d6cd7fd71b275f178`
- Genesis World committed gitlink：`0e74bf392781884ccad765c3f344419c86b872ca`
- SimFoundry committed gitlink：`9e34ebefcd020583fbb755a8b57268dce78eca26`

当前仓库的 `worktree/gujie@a25bc58853db1988aafafe88b544e0573eacbb3c` 已落后。独立仓库还含
modified `external/SimFoundry` 与 untracked `external/WorldComposer/`；这些未提交字节只有生成内容
manifest/hash 后才能成为可整合来源。

## Gujie 与 Bingsheng 的接力边界

Gujie 提供上述 x2env/Genesis 候选实现和独立工作区运行证据；Bingsheng 负责把它们映射到 Harness
schema、CAS、Registry qualification、ToolResult、状态增量、MCP、promotion 和统一测试。接入前的
Gujie 产物只能证明候选实现做过某次运行，不能代替当前 Harness Golden run。
