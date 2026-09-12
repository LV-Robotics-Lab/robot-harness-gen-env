# Robot Harness / x2env Context

本上下文定义多模态请求到可验证仿真环境的领域语言。意图、真实物理证据与发布资格互不替代。

## Language

**Harness（环境编排者）**:
接收环境请求、管理生命周期和修订、交付环境或失败证据的唯一权威。
_Avoid_: 外部Codex主控、并列父子工作流

**Workflow（环境工作流）**:
一次请求及其输入修订、操作、资产、场景和交付结果的统一身份。
_Avoid_: chat session、single Skill invocation

**Managed Codex Backend（受管理 Codex 后端）**:
Harness使用的理解、候选视觉评估、场景判断与诊断建议者；建议不等于已接受意图或物理事实。
_Avoid_: 外部agent主控、独立VLM权威

**Scene Intent（场景意图）**:
用户期望的实体、属性和关系，以及理解这些内容的媒体与文本来源。
_Avoid_: 已验证场景、模型成功声明

**SceneIR（受接受场景表示）**:
经过检查的命名实体与关系图，保留关键属性的来源和修订身份。
_Avoid_: free-form prompt、隐式丢实体

**Pending Intent（待补全意图）**:
语义已识别但关键参数尚未解决的建议，可用于查找候选资产，不可直接编译或视为接受场景。
_Avoid_: accepted SceneIR、悄悄忽略 critical unknown

**Simulation Design Scale（仿真设计尺度）**:
为生成场景选择的尺寸依据，可锚定已测资产并保留设计来源；不声称恢复了媒体中真实世界的绝对尺度。
_Avoid_: 图像米制测量、真实重建尺度证明

**Structural Support（结构支撑）**:
承载前景对象的桌面、工作台或地面，不是重建得到的前景资产。
_Avoid_: cabinet、basket、任意primitive替代物

**Asset Version（资产版本）**:
含来源、许可、几何、物理元数据与完整依赖的不可变对象表示；修订产生关联的新版本。
_Avoid_: 可覆盖目录、仅文件名身份

**Asset Source（资产来源）**:
资产的获取来路，包括本地库、许可允许的网络来源或真实重建。
_Avoid_: exact、digital cousin

**Asset Selection（资产选用）**:
判断候选能否代表目标的策略，精确复用与经验证的数字近亲是不同策略。
_Avoid_: source provider、重命名资产

**Digital Cousin（数字近亲）**:
并非目标本身，但在任务相关属性上经验证可替代的资产。
_Avoid_: 未验证外观相似、exact match

**Reconstruction（真实重建）**:
从用户媒体产生运行前不存在的新几何，并保留输入、算法及资产来源关系。
_Avoid_: primitive selection、旧资产复制、仅图片生成

**Compilation（场景编译）**:
把接受意图与解析资产组装为确定的仿真场景；编译本身不是物理证据。
_Avoid_: replay、physical proof

**Replay Evidence（回放证据）**:
真实仿真中的连续状态、接触、关节和媒体，与执行的场景修订一致。
_Avoid_: 单帧渲染、首尾截图、进程退出码

**Fresh Observation（新鲜观测）**:
从当前场景回放状态实际取得、带明确时效与修订身份的观测。
_Avoid_: 缓存截图、其他运行观测

**Physical Assessment（物理评估）**:
按预先冻结判据重算真实轨迹、接触、支撑、包含、穿透和稳定性的结论。
_Avoid_: 视觉判断、可读JSON

**Visual Intent Assessment（视觉意图评估）**:
实际观测与用户外观、布局、关系期望是否一致的视觉判断；不能替代物理评估。
_Avoid_: physics pass、任意主观成功

**Revision（有界修订）**:
针对记录的失败，对场景、资产或允许数值配置作出的新版本改变。
_Avoid_: 覆盖历史、修改阈值、无限重试

**Environment Package（环境包）**:
含可加载场景、必要资产、可迁移引用、入口、运行声明和证据的交付物。
_Avoid_: 仅报告集合、原目录绝对路径集合

**Failure Bundle（失败证据包）**:
失败或阻断请求的输入、原因、已完成产物与诊断证据的不可变集合。
_Avoid_: 丢弃异常、伪造终态

**Sim-ready（仿真就绪）**:
在明确运行环境和物理范围内真实加载、步进并通过验证的能力，不包含未执行的策略或真机能力。
_Avoid_: robot-policy ready、真机部署

**Qualification（资格）**:
固定实现与固定矩阵全部必要门通过后取得的执行与制品发布资格。
_Avoid_: 单例通过、candidate execution、回填历史成功
