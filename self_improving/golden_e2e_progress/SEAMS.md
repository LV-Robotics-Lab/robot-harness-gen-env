# canonical x2env 公共测试 seams

状态：accepted_matrix_v1_frozen。替代旧父子Golden/MCP/planner的active测试合同；
历史证据不升级为canonical资格。批准来源为实施计划第4、8–12节。

## 用户与能力seam

Harness.submit/status/resume/package是唯一用户接口；模型前持久化handle，同key同工作流。
最后revision验证通过、package物化可读后才原子succeeded。
状态仅active、succeeded、failed、blocked、cancelled，timed_out是cancelled原因。
意外child死亡保持可恢复active，resume检查准确owner及已提交操作，不重复submit。

公开Skill只有x2env.compile、x2env.replay、x2env.validate；asset.resolve、observe、revise为内部Tool。
CapabilityRegistry固定精确版本与digest；AssetRegistry只管理不可变资产。
强校验处为用户/媒体、模型建议、外部导入、跨进程、持久化恢复、package/资格；
已校验typed object不在进程内每层重复深验。

## 外部adapter seams

- CodexBackend.interpret / assess_asset_candidates / assess_and_diagnose只提供建议，不控制执行或物理通过。
- YuxinProviderAdapter调用原provider engine，带真实local/web和许可门。
- ReconstructionAdapter接受已验证媒体与SceneIR；Gujie原接口保留非默认，
  用户批准替代后端通过同一外部seam使用，要求固定来源、许可、真实新geometry。
- GenesisAdapter实际build/load/reset/step，输出连续轨迹/contact/joint/媒体。
- ArtifactPublisher只上传已被资格接受的制品，并实际下载核验。

## 冻结验收seam

[qualification-matrix-v1.json](qualification-matrix-v1.json)与
[physics-assertions-v1.json](physics-assertions-v1.json)是C02公开测试输入。
C03测试typed contracts/Registry，C04工作流，C06–C10 adapter，C11复制包，C12 CLI，
C13旧消费者删除，C14文档。仅在外部adapter、时钟、文件系统等seam使用受控替身，
不以mock计12例资格通过。总预算1800s，场景+资产最多2次修订，每provider一次；
fault注入不可预填模型建议或改变冻结断言。
