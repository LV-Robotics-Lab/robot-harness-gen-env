# Genesis 单资产资格持久应用合同

本模块只签发指定 representation / runtime lock 的单资产资格，不授予 environment replay、robot
policy、Skill Registry 或 MCP 资格。字段继续使用冻结的 `GenesisAssetQualificationV1`；精确门由
应用实现补齐。

## 公共 seam

`GenesisAssetQualificationApplication(state_path, artifact_store, approved_manifest_ref)` 固定 operator
批准的 runtime manifest 与独立 issuer implementation digest。SQLite 部署路径不进入内容身份。

- `reserve(request_ref, run_id=UUID4)` 只持久化 intent。已存在 intent 不获得执行权限。
- `execute(request_ref, run_id=UUID4, roots=...)` 只对新 UUID 执行固定 producer，并持久化 executed；
  不签发资格。已有 executed / qualified 只读返回，pending / failed 不重新执行。
- `invoke(request_ref, run_id=UUID4, roots=...)` 只允许新 intent 调用固定 producer 一次。同 UUID 的
  不同输入拒绝；pending 不暗中补执行；终态重试只读。
- `read(run_id)` 读取并重验持久状态与其证据闭包；缺失的 UUID 返回 `None`，损坏不冒充缺失。

## 状态与恢复

状态顺序为 `pending → executed → qualified`，执行失败为 `pending → failed`。
`executed` 只能由本应用收到固定 producer 的 `LiveGenesisRuntimeExecution` 后写入。
CAS-only verifier 不会生成 live capability，也不能单独创建 executed journal。

重启若只见 pending 必须停止；若可信 executed journal 已提交，则可以深验同一 execution receipt 后
继续资格发布，不再次执行 Genesis。qualified / failed 的重复调用不执行模拟。SQLite 配置、数量、
规范记录摘要、UUID 与请求身份，以及 terminal CAS 均须重验。

## 两个精确 checks

- `backend_load` 绑定 issuer bytes、manifest、request、execution receipt、observation、representation、
  runtime lock、probe input、raw load observation 与 load evidence。
- `collision_probe` 在这些共同身份上，再绑定连续 rows、contact trace 和 probe report。

资格的 subject、representation digest、loader closure、runtime、issuer 与两组 evidence 都须从同一
已核闭包重算。issuer helper 不提供 `issue(arbitrary_report)` 公共入口。

## 写入顺序

issuer 源码先作为 CAS evidence 发布；qualification 必须是本应用最后一个权威 CAS 写入。
随后仅提交 SQLite terminal。若最后 CAS 写入后 SQLite 尚未提交，可能存在未授权孤立对象；应用须从
可信 executed journal 确定性收口，不能凭猜中的 CAS 地址或孤立 qualification 对象授予权限。
failed journal 绑定固定 producer 保存的 diagnostic 及原始输出流，临时目录清理不切断失败证据。

## 验收边界

离线用例可覆盖持久意图、拒绝、损坏和没有真实执行权威的读取。成功签发必须实际调用批准 runtime
的固定 producer；禁止用 controlled probe 或伪造 live capability 填补该路径。当前实现与实测状态
以 Golden progress 的结果账本为准。
