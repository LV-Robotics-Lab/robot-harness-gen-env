# canonical x2env 验收合同

状态：accepted_matrix_v1_frozen。权威为已批准实施计划和用户允许替代重建后端的补充。

## 固定输入与计分

[qualification-matrix-v1.json](qualification-matrix-v1.json)包含12个唯一ID，simple/medium/hard各4。
prompt移除Markdown引用前缀，同一引用自然段物理行无分隔连接，空prompt为null。
媒体按固定bytes/SHA识别，路径只是staging locator；输入pack仍需MIME/尺寸/完整帧与上传授权。
四份输入已轻量核对不等于重建通过。

共同G与case附加断言都通过才计1分；blocked/failed/cancelled/timed_out均0分。
必须simple4/4、总分≥8/12，text/image/video/multimodal与local/licensed web/真实重建各有成功；
digital cousin可not_run。M01不是额外硬门，但成功必须真实articulation。
所有12例均交付环境或不可变失败包。
Gujie原接口保留非默认，用户批准的固定替代后端通过同一reconstruction seam接入，
不能用primitive/旧资产复制代替新geometry，不能弱化M04或其他重建来源硬门。

## 冻结物理与视觉

[physics-assertions-v1.json](physics-assertions-v1.json)是数值权威。
baseline .004s×1000、half-dt .002s×2000，均4s独立零初速。
每动态实体末.5s translation≤.001m、rotation≤.5°、drift≤.002m/s、
angular rate≤1°/s、excursion≤.001m。有效速度max(|v|,radius*|w|)≥1.5*g*dt连续行数<5。
支撑末段dropout≤.05、contact中向上力>1e-6N比例≥.8；
全轨迹penetration≤.001m、最低顶点≥-.001m；完整旋转visual+collision footprint margin≥.02m。
inside必须测量内腔，clearance≥.005m、距内部底面≤.003m，同样contact/support/penetration门；
多支撑每target有效contact fraction≥.5。跨dt终态position≤.01m、rotation≤5°。
open revolute span≥30°，距closed≥max(15°,20%span)，初始处于20%–80%span；
4s drift≤2°、末段speed≤1°/s、全程越limit≤.5°。
缺内腔/关节metadata返回missing_physical_metadata，不估计宽松阈值。物理与视觉必须分别通过。

## 补充、复制和执行门

source-fallback、fallback-A、fallback-B、bounded-stop、dead-owner-resume、
clarification-resume、idempotency独立通过；fault只通过qualification外部adapter seam，
不进入public request/Registry，不预填Codex proposal。scene+asset合计最多2修订，
重复error+SceneIR/asset+evidence fingerprint立即停止；provider每来源一次。
完整case总1800s，1770s精确owner SIGINT留30s收口，超时cancelled/timed_out。
不立即换seed、缩小场景或删断言补跑。

每成功包静态闭包验证；三个不同包按matrix固定顺序复制：S01、首个新重建包、
排除前两者的首个丰富场景。OS拒绝原workspace/CAS/资产/包，
真实version check/load/reset/step/新观测contact/stability与新图/连续MP4。
只复用声明外部runtime，不复制8.5GB运行环境。

C15 clean HEAD固定schema/capability/code；任何源码fixture阈值变化重新冻结与资格。
canonical core语句/分支100%，外部adapter边界和真实运行验收，不用exclude掩盖业务。
所有active CI、schema、ruff、文档链接与diff gate通过。最终报告入evidence prerelease，
候选身份不可回填qualified；默认qualified部署另做有界验收，远端下载verify。
正常push origin/worktree/bingsheng后停止，不开main PR。
robot_policy_evaluated/data_collection_evaluated均false。
