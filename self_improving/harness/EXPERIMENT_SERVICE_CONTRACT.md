# 通用开发实验服务

`ExperimentService`使用已有GoldenRunHarness、SQLiteGoldenRunStore、SkillRegistry与child journal。
它不建立第二业务状态机。用户输入固定为一个CAS bundle；每个阶段通过版本化
`experiment.<stage>@1.0.0`开发descriptor和policy调用，真实ToolResult/receipt推动父revision。
此命名空间是开发实验，不替代旧text2env/image2env/video2env qualified版本，也不自动发布。

阶段输入是`ExperimentSkillInput`，只能引用当前受信input/dependencies；输出是
`ExperimentSkillOutput`。completed execution不等于物理通过：失败body进入诊断，不生成成功fact。
实际模型是内部CodexBackend.propose，无shell/MCP工具权限；由服务选择和执行Skill，而非模型外控。
开发执行显式需要配置授权，固定本次声明的源码文件和配置身份；旧run在声明源码变化后拒绝恢复。

幂等key绑定原输入或阶段/提议；每次成功或失败执行均落现有journal。修订最多场景2次、资产2次；
必须引用最新失败，asset patch必须等于已记录模型建议，且下游再检白名单。不允许发布阶段。
更多停止条件由现有controller的实验分支消费完整history决定。

阶段及媒体完整性不是资格。当前服务纵切真实证明text→内部模型→生成资产→compile；
replay/observation/visual/validate和包入口是否真实通过，以进度RESULTS的逐次记录为准。
