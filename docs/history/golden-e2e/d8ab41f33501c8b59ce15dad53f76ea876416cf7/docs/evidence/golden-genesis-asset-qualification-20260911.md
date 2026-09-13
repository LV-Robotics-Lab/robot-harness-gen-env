# Durable Genesis 单资产资格

固定实现`5f78e62`，issuer源码SHA256：
`b0da423a3f9dc304b0adac98d81cf1c0f9c1cd0c80df3c0f9dfde9ab06c07ef2`。
本切片已经真实签发给定runtime与物性参数下的单资产资格，不是完整环境或Skill资格。

## 执行与恢复

应用使用[manifest驱动的固定CPU执行](golden-runtime-execution-20260911.md)，只在收到该producer的
live capability后建立可信`executed`记录。直接`invoke`完成执行和签发；显式`execute`可以停在已
执行状态，让新的应用实例在没有runtime roots的情况下重验同一证据并恢复签发。

caller UUID绑定canonical请求、批准manifest和issuer身份。pending不暗中补执行；qualified重试
只读；SQLite配置/规范记录/关联和CAS损坏拒绝。issuer字节先写入CAS，qualification最后写入，随后
只提交SQLite终态。两个检查固定为`backend_load`与`collision_probe`，关联representation、runtime
lock、manifest、request、execution receipt、observation和全部probe证据。

合同与主要实现：
[公共合同](../../self_improving/harness/GENESIS_ASSET_QUALIFICATION_APPLICATION_CONTRACT.md)、
[application](../../self_improving/harness/genesis_asset_qualification_application.py)、
[公共测试](../../tests/self_improving/harness/test_genesis_asset_qualification_application.py)。旧qualification
v1语义未修改；公开schema仍为97份。

## 实测证据

- 真实RED252.56s：fresh Producer成功，按预期在尚未实现的资格提交处失败；原始日志和executed
  SQLite保留，不能改记为通过。
- 正式GREEN：3 passed/865.35s，覆盖坏XML完整失败闭包、fresh live签发/无live app重启/同UUID
  幂等、真实executed断点新app恢复。最后一项恢复不提供运行roots，不再次调用Genesis。
- 第二CAS和可信SQLite备份纯读通过，61.60s，进程中Genesis不可导入且没有启动执行。两UUID
  得到相同qualification：`2800480e74027a963190bd32c7c986bcd790ec53ac600f2b11842996084e34f2`，
  8,694bytes。资格晚于所有check evidence，且是源CAS最后新增对象。终态引用名称漂移被拒绝。
- [第二路径机读摘要](golden-genesis-asset-qualification-relocated-20260911.json)包含精确refs、路径、
  复制对象和拒绝结果。原始RED/GREEN日志位于
  `/home/jingxiang/bingsheng/golden-runtime-bound-20260911.ndky_vr0/qualification-{red,green}.log`。
- [固定提交独立功能验收](golden-genesis-asset-qualification-acceptance-20260911.json)通过：两检查
  各10/13项evidence精确绑定，issuer、真实执行和可信恢复一致。
- 新真实execute保存checkpoint后并发恢复通过：一个应用返回资格，另一个拒绝已变化的journal；
  同UUID重试返回同ref。收口组首轮1 failed/1 passed、401.84s；唯一失败为坏字节测试误预期长度
  错误（实际按SHA拒绝），原日志保留。只重跑修正后的纯读坏字节用例1 passed/21.54s，无新Genesis。
- [完整运行小摘要](golden-genesis-asset-qualification-run-20260911.json)记录每轮失败/通过、refs与最终
  覆盖：application为217/220 statements、57/60 branches，未排除任何行。三个剩余防御分支逐项
  说明，原始100%目标未达到；最终全套测试结果另在进度账本记录。

## 边界

资格固定于这一representation和runtime lock。质量与摩擦来自SimFoundry估计，物理probe证明给定
参数下的load/contact/settling，不证明现实物性测量。CAS纯读无live执行权威；恢复权威来自可信应用
状态及完整证据复核。该资产资格没有注册production image/video Skill，也未暴露MCP工具；完整
Genesis environment replay、robot-policy、promotion和可观看媒体仍是后续工作。
