# canonical x2env Skill 合同

状态：accepted_matrix_v1_frozen。旧text/image/video三乘三、experiment、limited preview和MCP
不作为canonical active Registry接口；历史实现与证据保持原义。

## 三个公开Skill

| 名称 | 输入 | 输出与权威 |
|---|---|---|
| x2env.compile | 接受SceneIR revision、ResolvedAssetSet、seed、Genesis profile | CompiledScene、静态诊断、receipt；不授物理通过 |
| x2env.replay | CompiledScene、runtime lock/profile、总deadline | 连续trajectory/contact/joint/checkpoint/帧MP4，失败也保留 |
| x2env.validate | 当前compiled/replay/fresh observation、Codex advisory、固定assertions | 物理与视觉分列；模型不改阈值 |

SemVer精确版本/digest固定；Python typed models是schema唯一编辑源，生成descriptor/API字段表
不得手工漂移。Skill handler不改变workflow终态。

## 内部Tools与来源

asset.resolve消费实体语义、sources、exact/cousin、seed和media，返回不可变版本、
候选provenance及miss/blocked；observe消费当前replay/checkpoint/scene revision，
返回TTL有界新观测；revise消费ScenePatch/AssetPatch/NumericsPatch、base revision、
failure/diagnosis refs，产生新版本。

local/web/reconstruction是source，exact/cousin是selection；默认local exact→verified cousin
→licensed web→reconstruction。foreground许可只接受CC0-1.0/CC-BY-4.0并保留归属。
table/worktop/counter/ground是compile-owned structural support，不扩张到cabinet/basket/plate。
真实重建必须新geometry；Gujie原接口保留非默认，替代backend只按批准从同一外部seam接入，
语义权威仍是managed Codex，不运行第二套Gemini/Qwen控制器。

## 生命周期与失败

Harness.submit/status/resume/package唯一用户入口。状态只有active、succeeded、failed、
blocked、cancelled；package可读后才succeeded，timed_out只是cancelled原因。
用户或模型不能切换development_candidate/qualified；候选不可发布，资格接纳也不改历史身份。
Codex无业务SQLite/CAS源码编辑与阈值权限。外部adapter无第二套checkpoint/retry。
缺资源blocked_external_resource，缺物理metadata为missing_physical_metadata，
不支持关系unsupported_relation。scene+asset最多2修订，重复fingerprint停止，每provider一次。
保留全部部分产物，不覆写旧asset或证据。

冻结case见[qualification-matrix-v1.json](qualification-matrix-v1.json)，
固定判据见[physics-assertions-v1.json](physics-assertions-v1.json)，
总门见[ACCEPTANCE.md](ACCEPTANCE.md)。接口合同存在不表示实现或资格通过。
