# Production generated-asset portability evidence — 2026-09-01

A043 把 production compile 的生成资产从单次运行 `state_root` 分离到项目资产库。提交数据前的最终
审查发现，旧的未跟踪样例仍把本机绝对路径写进 ledger，并把已经消失的 pytest staging 路径写进
`generation_provenance.json`。它从未进入 Git；本次先隔离旧目录，再只通过公开
`compile_scene(..., asset_admitter=GeneratedAssetAdmitter(...))` 重新生成项目样例。

## RED → GREEN

- RED 1：公开 admission 成功后，四个 provenance `files[*].path` 仍是 staging 绝对路径。
- RED 2：v1 provenance 允许额外的 `files.debug`，未绑定 locator 会原样进入发布目录。
- RED 3：既有 file identity 还允许额外 `staging_path` 字段，同样可隐藏未绑定 locator。
- RED 4：初始输入门通过后、private staging 快照前改写 visual payload，旧实现仍会继续发布。
- RED 5：既有资产把 provenance 改为绝对 staging locator、同步重建自洽 ledger 后，旧 reuse 只比较
  路径尾部，仍返回 `reused`。
- RED 6：v1 provenance 顶层加入 `staging_path`，旧实现仍会原样发布。
- RED 7：private staging 的 provenance 在改写后的第二次快照期间被替换，旧实现未重新读取对账。
- RED 8：同一窗口加入 `unexpected.bin`，旧实现未要求 staging tree 是精确闭包。
- RED 9：derived provenance 的 `compatibility` 对象可增加未绑定 locator。
- RED 10：允许字段 `requested_material` 可被换成嵌套 `{"staging_path":"/tmp/..."}` 对象并发布。
- RED 11：derived provenance 的字符串列表（例如 `aliases`）可原样携带绝对 staging locator。
- RED 12：derived provenance 的 `semantic_name` 可原样携带绝对 staging locator。
- GREEN：generated source 先逐文件复制并对账；随后只在 private `.incoming` inode 内把四个已验证
  locator 改成 asset-relative POSIX path，写盘并 fsync，再重新计算完整 staging manifest、构造 ledger
  和执行 `check_files=True`；归一化后的 provenance 摘要还会与实际 staged payload 再对账。
  `files` 必须精确是 visual/collision/material/metadata，且每项只能含 `path` 与 `sha256`；procedural
  与 derived v1 的顶层字段、标量、三维向量、字符串列表和 compatibility 子对象也使用精确内建类型
  与冻结值域；derived 的语义名称和字符串列表也必须是 locator-free，不能在合法字段内再藏对象或
  路径。

十二个攻击结果均从公开 `compile_scene(..., asset_admitter=...)` 或 `GeneratedAssetAdmitter.admit()`
入口观察。RED 4、7、8 只 monkeypatch private manifest seam 注入确定时序漂移；RED 5 的 private
ledger helper 只准备一份自洽 legacy reuse 输入，最终接受/拒绝仍由公开入口决定。

## 项目资产库结果

- 固定 asset id：`900_gen_hexagonal_pedestal_5695f4e5`。
- 首次公开 admission 为 `admitted`，第二次独立生成相同内容为 `reused`。
- `ledger.json` SHA-256：
  `fe52c79a5107aa83fb6750ba56a71b48dc3a2b8a78b96bf1c8f6532c35b41ae2`。
- `generation_provenance.json` SHA-256：
  `47055f5969416dd06b5d19020ccb35d8892a5568821e0255886f599fda718116`。
- ledger 有 3 个完整 file records；`validate_ledger(check_files=True)` 为 0 violation，当前
  `sapien/generation_qc` receipt 的 report 与 representation digest 均匹配。
- ledger URI 相对 active asset tree，provenance locator 相对资产根；提交目录与 checked-in compile
  qualification 三文档均不含 `/home`、`/tmp` 或 generated-staging locator。资格报告仍按契约记录
  可复现的 pytest regression command；它不是资产 locator。

## Compile qualification 联动

`assets.py` 的行为字节变化先让旧 checked-in bundle 以 `implementation_file_mismatch` RED。格式化
最终源码后，在全新 scratch/library 中再次执行固定三轮 generator，仍得到
`admitted -> reused -> reused`，七项资格门全部 pass；production loader 重读实现文件、`scene_gen`
与 ledger contract tree 后通过。

- implementation SHA-256：
  `07501791e56e555e3b70c6a93dfd2e24ba00945fc3a27395e09b4faf17cb5e2d`。
- report SHA-256：
  `eb3282cb671a134574b7ae1a565573b86a03e4fe9dd13310b52e930da5b8fdb9`。
- bundle raw SHA-256：manifest `893586fb…5a1bb`、qualification `41d3ff98…2118e`、report
  `eb3282cb…8fdb9`。
- admission、qualification/generator、application/CLI/packaging 六个文件合计 `223 passed`。
- descriptor/replay qualification 回归另为 `240 passed`。

## 边界

该样例仍只取得 deterministic generation QC：`physical_qualification=pending_settle`，mass 与 friction
仍 unknown，static validation 仍 `incomplete`。相对路径、严格 ledger closure 与稳定 reuse 不等于
SAPIEN settle、接触/稳定性证据、replay qualification 或发布资格。
