# 通用实验运行时 adapters

本模块不拥有业务生命周期、数据库或模型；由现有 Harness controller/Skill executor 调用。
所有输入 ArtifactRef 可为 pydantic 实例或JSON字典；返回值为带schema_version和CAS receipt字典。

- `replay(store, scene_ref, output_root, runtime_roots, denied_roots=())` 导出可迁移包，分别执行
  原 baseline/half_dt profile，把轨迹、loaded、capture metadata、真实帧/视频、stdout/stderr/result
  入CAS。原 `evaluate_scene_replay` 从两条轨迹计算物理判据，没有新成功阈值。失败保留已完成
  profiles、错误和包目录。runtime cache/home/tmp不是用户产物，不整体入CAS。
  `status=passed` 只表示两种profile执行成功、完整轨迹可解析且物理报告已计算，允许后续
  observe/模型诊断消费失败现场，不表示sim-ready。`physical_status` 独立保留原报告的
  passed/failed/inconclusive；执行或轨迹不完整仍status=failed、physical_status=not_run。
  新增字段只扩展当前实验回执，不修改既有回执、正式资格或物理判据。
- `observe(store, replay_result, *, ttl_seconds=300, output_root=None, runtime_roots=None,
  denied_roots=())` 先消费最后一个实际camera event的captured_at。过期且没有新执行资源时返回
  `fresh_capture_required`；参数齐全时在新目录真实再跑half_dt并使用新camera event。新回放只更新
  observation来源，不覆盖旧replay、轨迹或报告。逐像素SHA与原event比对，TTL仍复用1至300秒门。
- `validate(store, *, input_ref, scene_ref, replay_result, visual_advisory=None, observation=None)`
  从不可变receipt读取scene和profiles，独立重算物理报告，分别返回physical_status/visual_status。
  总passed必须replay和physical通过、visual advisory为passed、observation同scene/replay且仍新鲜。
  视觉建议不会授权物理通过；input identity被显式记录，上层compile/controller仍负责input到scene
  的因果绑定。本模块不伪造旧qualified Skill receipt或发布资格。

`replay_result` 可以传完整replay返回字典或receipt ArtifactRef；完整字典若与CAS回执不一致会拒绝。
`visual_advisory` 为Codex结构化建议，包含 `status: passed|failed|not_run` 联合诊断状态；
独立视觉结果优先读取 `visual_status`，旧建议缺此字段时兼容读取status。联合诊断失败不会
把visual_status=passed伪报成视觉失败，也不会覆盖独立重算的物理失败。其余字段原样保留。
`observation` 传observe返回字典或receipt引用。所有成功权威仅为development_unqualified有限刚体
实验；robot_policy_evaluated/data_collection_evaluated仍false。

该实现复用现有Harness runner、物理判据和freshness门，沿用
`GENESIS_SCENE_REPLAY_CONTRACT.md` 的Gujie固定来源/Genesis上游归属。此切片只执行公共seam
离线测试，不运行新Genesis案例；真实端到端接线由集成controller案例单独记录。测试覆盖无效场景
失败回执、视觉不能掩盖缺失物理、过期时间不能重贴、scene替换与物理报告篡改拒绝。
执行/评估分流测试使用分析轨迹和runner边界替身，覆盖物理失败仍完成执行、截断轨迹及
runner失败不获执行通过、联合失败与视觉通过分离；这些测试不是新的真实Genesis运行证据。
