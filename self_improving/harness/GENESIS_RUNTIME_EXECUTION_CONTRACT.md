# Manifest 驱动的 Genesis CPU execution 合同

本合同实现 Golden S3 的单资产运行前置，不是 environment replay、机器人策略门或 asset qualification。
既有 `GenesisRuntimeLockV1` 和 asset-probe v1 的物理合同不变；新增版本化 execution records 将其与
runtime manifest 关联。公共模型以 `schemas/genesis_runtime_execution.py` 为字段级规范。

## 公共 interface

- `GenesisRuntimeExecutionProducer(store, approved_manifest_ref).produce(request_ref, roots=...)`
  使用operator固定的manifest批准配置。请求只能引用该manifest，不能替换launcher或提交一个报告
  冒充执行。成功返回不可序列化的 `LiveGenesisRuntimeExecution`。
- `GenesisRuntimeExecutionVerifier(store).verify_request(request_ref)` 重验请求、manifest与runtime lock。
- `verify_observation(request_ref, observation_ref)` 和 `read_receipt(receipt_ref)` 只重算CAS证据，返回
  `VerifiedGenesisRuntimeClosure`，其 `execution_authority=False`；它不是issuer可用的live capability。

## 输入身份

request必须引用同一CAS中的manifest、representation和runtime lock，并选择manifest中固定路径的
Harness runner。lock的既有dependency列表必须含精确manifest identity，backend implementation digest
必须由manifest的完整backend成员集合计算。这种新增依赖不会改变旧v1记录的含义；新lock必须实际
驱动fresh probe，因此不能沿用绑定旧lock的report。

部署映射与逻辑身份分离。launcher会核对运行入口与受信Harness安装源码；manifest本身是operator
批准的运行配置，不由任意请求提供的自声明字段自动获得上游来源或production资格。

## 执行与闭包

固定launcher使用声明的解释器、归档源码和进程配置启动child，并在执行前后核对部署。归档执行
不借用旁边Git checkout。运行根只读，输出有独立可写目录；必要native部署别名逐文件绑定声明
字节。运行约束、依赖或环境不满足时必须失败，不能悄悄放宽目录或退回普通进程。

CPU设备元数据来自实际kernel facts；读取这些事实所需的精确intrinsic对象或预开descriptor不是
普通、预固定的manifest成员。observation记录CPU名称、内存、kernel release/machine和预开maps
事实。Genesis初始化期间的metadata adapter须恢复原函数，不改变asset load、solver或物性合同。

执行observation绑定request、实际process identity、Torch线程、module/native成员、运行约束、输出
流，以及probe input、raw load、load evidence、rows、trace、report。deep verifier重算完整probe闭包，
所有引用必须与同一个representation/runtime lock相符。producer完成这些步骤后才最后发布pass
execution receipt；失败留下diagnostic而不发布pass receipt。

## 后续资格门

CAS完整性与真实执行来源分账。只有固定producer成功执行能产生live capability；pure-read verifier
不能把任意自洽JSON升级成这个类型。durable qualification application还需caller UUID/输入幂等、
可信intent/terminal重建、pending不二次执行、损坏拒绝，以及资格作为最后权威CAS写入。
本合同不授予MCP曝光、publishability或promotion。

## 验证目标

公共测试覆盖请求/manifest/runner/lock漂移、实际观测与probe关联、纯读无执行权威、失败与commit-last。
真实验收必须调用producer跑完initial observation及1,000 CPU/Newton steps，并在第二部署位置重验
完整执行回执；不能通过fake launcher或历史probe填补live成功路径。
