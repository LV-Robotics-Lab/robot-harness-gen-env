# Gujie 来源档案

## 责任边界

Gujie 的候选贡献集中在 x2env 媒体输入、`genenv.*` 任务输出、标准 URDF closure、SimFoundry
asset/scene import、Genesis 场景构建、布局求解、物理验证、回放和媒体门禁。Genesis World、
SimFoundry 以及输入资产仍属于各自第三方上游；本档案只把 Gujie 编写的 adapter/workflow 归于 Gujie。

## 当前固定锚点

- 权威独立仓库：`/home/jingxiang/gujie/gen-env`
- 当前 committed HEAD：`eb0b710581fd7794bc01b447b0f77cb871c8a711`
- 重点祖先：`9e8d28c24ca72ebf4001c2460dc4b174cef603c0`、
  `a8ced2732b48d3eb89e5c40d6cd7fd71b275f178`
- Genesis World committed gitlink：`0e74bf392781884ccad765c3f344419c86b872ca`
- SimFoundry committed gitlink：`9e34ebefcd020583fbb755a8b57268dce78eca26`

当前仓库的本地 ref `worktree/gujie@a25bc58853db1988aafafe88b544e0573eacbb3c` 已落后，不能作为
唯一来源。独立仓库当前还含 modified `external/SimFoundry` 与 untracked `external/WorldComposer/`；
除非后续生成内容 manifest/hash，这些未提交字节不属于上述 HEAD。

## 候选源区

- `self_improving/sim_adapters/genesis/reconstruct_*`
- `self_improving/sim_adapters/genesis/media_*`
- `self_improving/sim_adapters/genesis/task_output*`
- `self_improving/sim_adapters/genesis/standard_urdf*`
- `self_improving/sim_adapters/genesis/import_simfoundry_assets*`
- `self_improving/sim_adapters/genesis/import_simfoundry_scene*`
- `self_improving/sim_adapters/genesis/position_solver*`
- `self_improving/sim_adapters/genesis/scene_stabilization*`
- `self_improving/sim_adapters/genesis/scene_physics_workflow*`
- `self_improving/sim_adapters/genesis/validate_imported_scene*`
- `self_improving/sim_adapters/genesis/replay_text_half_dt*`
- `self_improving/sim_adapters/genesis/physics_criteria*`

## 接入原则

通过 anti-corruption adapter 把 `genenv.*` 映射为 Harness schema、CAS、receipt 和 promotion；不直接
把 Gujie 的内部格式提升为 Harness 权威状态。真实 Genesis 证据必须在当前 Harness run 中重建，源
工作区的测试和产物只能作为候选实现证据。
