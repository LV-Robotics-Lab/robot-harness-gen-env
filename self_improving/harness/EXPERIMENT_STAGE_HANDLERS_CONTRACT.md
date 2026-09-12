# 实验Skill阶段接线

公共 `execute_stage(stage, parameters, *, store, root, settings, backend=None)` 消费
`ExperimentSkillInput` 或对应dict，返回单阶段body。它不实现intent/visual/revise_scene模型动作，
也不创建另一套workflow状态。部署settings可为dict或属性对象；asset_library_root保持资产跨operation
复用，默认root.parent.parent/assets；runtime_roots（或scene_roots）、denied_roots提供明确运行环境。
catalog_path与source_ref仅从部署读取，模型不能通过proposal注入下载源或本地目录。

| stage | 必需dependency body | 真实实现与返回 |
|---|---|---|
| acquire | experiment.intent.intent | AssetRequest→acquire；version_id/root/receipt，physics not_run |
| revise_asset | experiment.asset | proposal仅scale/mass_kg/friction→revise；新不可变version与parent |
| compile | experiment.intent、experiment.asset | compile_scene；scene_ref/representation_ref/compile_receipt |
| replay | environment.scene.scene_ref | experiment_runtime.replay；双profile及CAS物理报告 |
| observe | environment.replay | experiment_runtime.observe；过期时真实新capture |
| validate | environment.scene/replay/observation、experiment.visual | 核验visual input/scene身份后独立validate |

dependencies值都是同一Harness提供的CAS ArtifactRef，body是该引用对应的JSON正文。
input身份在acquire/compile和visual消费处核对。控制器负责operation的状态、预算、修订和回执包装。

缺本地catalog返回blocked_external_resource与所需配置；已知AssetAcquisitionError保留partial_root及
JSON diagnostics的CAS引用。由依赖包缺失或URL/network错误引起的获取失败标blocked，其余标failed。
类型错误、未知stage、非法patch和绑定错误明确raise给宿主记录，不用宽泛异常catch隐藏编程问题。

静态公共测试真实调用procedural primitive生成、不可变资产登记、compile_scene和资产修订；没有用
stub模拟这些组件。另覆盖缺catalog、非法patch、生成失败诊断CAS保留。不授予Genesis运行通过；
replay/observe/validate真实执行由集成测试另记。
