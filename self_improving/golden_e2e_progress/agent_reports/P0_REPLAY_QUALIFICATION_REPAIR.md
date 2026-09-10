# P0 Replay Qualification Identity Repair

状态：`standalone_implementation_ready; integrated_with_p2; real_requalification_pending`

- 基线：`660ae4900eed4fba0fce59fb0d26fe0610735df8`
- 实现提交：本报告所在的单一 superseding feature commit；固定 SHA 由主审回执记录。
- Dashboard：portfolio、tasks、project 三个读取入口均返回 HTTP 404；未声称同步成功。

## RED

旧实现把整个 `self_improving/harness/` 当作 replay 资格身份，导致无关 System 2、image/video、
asset 或 VLM family 的公开导出变化也使资格失效。初版显式列表又遗漏了 package/schema 聚合器的
实际执行依赖。新增负向用例与完整性校验覆盖：

- fresh-process import trace 必须统计整个 `self_improving/` 并落在显式 implementation closure 或独立
  tree-hash 绑定的 ledger contract root；顶层 `self_improving/registry.py` 不得被过滤；production tracer
  必须真实调用 `_execute_fixed_case`，不得以整函数替身冒充执行期 import 证据；
- 四个全局 facade/catalog 目录增加无关 family 时 replay tree identity 不变，而每一个 closure 成员
  byte 漂移都会改变 identity；
- 旧 `resolve -> open` 实现可在三次扫描中稳定哈希替代 symlink；distribution ancestor symlink、
  open 后 rename、流式读取中原地变更、相对 root 父级逃逸和 closure `..` 成员均须 fail closed；
- 惰性 `SCHEMA_MODELS` 必须保留既有 `.copy()`、左/右 `|` 的 dict 行为；
- distribution root 在打开后被整体 rename/replaced 时，即使旧 root FD 中成员稳定，也必须拒绝；
- distribution root 不变但在第二轮最后成员打开后将 `self_improving/` 或 `harness/schemas/` 替换为
  identical-byte 完整副本时，不能只比较摘要，必须由第三轮重走按目录链 identity 拒绝；
- 14 份 replay 绑定 schema 中任一模型 snapshot 内容漂移或缺失，都必须在执行或 bundle 解析前拒绝；
  其中包含 `harness.skill_qualification_report.v1` projection。

## GREEN

- replay identity 改为显式、可执行的 Text2Env compile/replay closure，包含稳定 package 边界、生产
  handler/application/registry/runtime code、实际 project helpers（包括顶层 `self_improving/registry.py`）、
  14 份 schema snapshot 与原生媒体 sandbox 源码。
- `harness`、`handlers`、`schemas` package init 只保留稳定 PEP 562 seam；可扩展公开导出目录移到
  closure 外的 `_public_exports.py`。Text2Env 使用 closure 内、不可变且明确冻结到 legacy v1 的
  `_qualified_exports.py` resolver；validate v1 及未来 Text2Env v2/v3 不进入 replay@1 seam；
  全局 schema family 目录只有调用 public catalog 时才加载。
- schema catalog 仍是不可赋值的 `Mapping`，并兼容原 mapping proxy 的 `copy`、`|`、反向 `|`；
  Text2Env qualified IDs 不加载全局 family 目录。
- `_snapshot_harness_source` 从文件系统锚点起逐组件 `openat`，directory/file 均使用 `O_NOFOLLOW`；
  每个普通文件只通过同一 FD 流式哈希，并用前后 `fstat` 及最终 nofollow `stat` 锁定
  device/inode/mode/link-count/size/mtime/ctime；从三个独立打开的 root FD 对每一条目录链和成员完整
  重走、重读并逐项对账，再次重开绝对 root 比较 `(device, inode, mode)`。
- fresh-process production tracer 真实执行 `_execute_fixed_case` 的 compile application/handler、direct
  replay handler 与 Registry candidate；仅外部 RoboTwin runtime、native sandbox 和 media decoder 使用
  确定性替身。执行期第一方 imports 必须属于显式 implementation closure，ledger 动态 imports 则必须
  属于由 `ledger_contract_tree_sha256` 独立绑定的 contract tree。
- qualification generation、bundle loader 与 in-memory document loader 在各自执行/解析前，对 14 个
  模型生成的 canonical JSON Schema 与 committed snapshot 做 exact-byte 检查；模型与 snapshot 路径
  都派生自单一 frozen binding 表，并与测试的独立 14-ID literal 做集合一致性校验。

## 验证

- focused replay/loader/schema：`171 passed`。
- 变更模块 statement + branch：`1807 statements, 372 branches, 100%`。
- Harness：`2193 passed, 19 skipped, 1 failed`；唯一失败是未重签的旧 checked replay bundle 对当前
  `harness/__init__.py` 身份不匹配。
- root：`3206 passed, 19 skipped, 1 failed`；唯一失败同上，证明旧 bundle 继续 fail closed。
- checked compile qualification：`1 passed`，本切片未使 compile 资格失效。
- `python -m script.export_harness_schemas --check`：29 份 snapshot 通过。
- Ruff 与 `git diff --check`：通过。

## 边界与下一步

本切片没有刷新 expected hashes、没有改 checked qualification bundle，也没有把旧资格标成 current。
该独立候选当时预期 P2 将修改 closure 内的 `registry.py` 与 `application.py`；P2 现已进入隔离集成树。
真实 compile 与 900/120 replay、bundle regeneration、严格 reload、验证和统一重签必须基于当前
集成树（以及届时所有其他 closure 依赖变化）执行。若真实运行受
GPU、delegated cgroup、runtime 或资产 closure 阻塞，继续保持旧资格失效，不以 hash-only 更新绕过
执行证据。

Repo-docs sync gate 已同步：四个 reader-facing 入口把 2026-09-02 bundle/900/120 明确标成历史证据，
注明当前源码会严格拒绝，并把该独立候选的 committed schema 数更正为 29；集成已在主线的
3 份 asset staging schema 后，当前总数为 32。append-only 的 2026-09-02 evidence
保持原字节；失效原因、闭包修复和未重签边界写入独立 2026-09-10 状态页。本切片尚未产生新的 current
资格，最终 P2 后真实重签时再补新的运行 evidence。
