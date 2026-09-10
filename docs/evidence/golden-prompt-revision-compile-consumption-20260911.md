# PromptRevision → exact image compile consumption（2026-09-11）

## 固定实现

- implementation commit: `9239ba3ae7134ecdc7196c90a01aa63195d2c4f2`
- schema tranche: `5e6ef4eee54d54dd1869ba12c03655bcfffd1393`
- public exports: `869ba200ebc769e735c778bf18fd162c400603c4`
- public MCP RED: `828d643` + identity fix `d20e72f`
- fail-closed legacy barrier: `d9552ce`

## 实际受控链

官方 `mcp==2.2.0` Client 在独立 `git archive` 与新 evidence root 中执行：

```text
controlled observation
→ blocked Skill failure
→ 4 audited resource reads
→ advisory revision 3
→ PromptRevision revision 4
→ exact image2env.compile@1.0.0 with v2 prompt_revision attempt
→ controlled blocked terminal revision 5
```

正确请求只调用一次 tracking application、只分配一个 child；revision/turn `4→5`，新 workflow operation
receipt 的 predecessor 精确等于 PromptRevision receipt，trusted state ref/hash 不变。该 blocked terminal 是
测试边界主动停止，不是 compile 成功或环境生成证据。

## 完整性与重启

- CAS：55 objects，逐文件摘要 0 mismatch。
- workflow 可达闭包：49 unique ArtifactRef identities，全部可解析且 bytes/hash 一致。
- workflow SQLite 与 child SQLite：`PRAGMA integrity_check=ok`。
- 无 live application 重开后，使用相同 durable Harness command 得到同一 terminal operation；两库所有
  row counts 与 CAS manifest 均零增长。
- 独立验收 root：`/tmp/golden-prompt-consumption-9239ba3.RjqmXH/evidence`。
- `acceptance-summary.json` SHA-256：
  `2e324dd48b8b0fcb0c89528522b22d5845e6cc501032bec955757c42e4e6347c`。
- `pytest.xml` SHA-256：
  `f1c19b0cba1cd1ab7d0200d265ebbeb9f60938f111b7ad56f12fd40909157087`。
- 第二轴用另一个 `git archive` 复核 68 个 schema/catalog、x2env 未资格/未生产曝光边界和既有 text
  compile v1 qualification。后者仍为 11 个 implementation files、7 个 checks、pass，implementation
  identity 为 `0394575ff26f8da601507416573656b3fb04d7ca06fc45ff3f8195005f470b7f`；没有被本切片重签。

## 失效模式

以下六类 v2 lineage 漂移都返回 `HARN_HISTORY_MISMATCH`，并保持 application calls、child allocations、
workflow revision/state、两库 rows 与 CAS manifest 不变：

1. target exact Skill 漂移；
2. PromptRevision ArtifactRef 漂移；
3. effective revised prompt 漂移；
4. failure receipt tuple 漂移；
5. predecessor package 漂移；
6. video→image selected frame identity 漂移。

旧 `harness.image2env_compile_input.v1` 返回 `HARN_INPUT_SCHEMA_INVALID`，同样零副作用。新
`prompt_revision_consumption.py` 的永久测试为 37/37 statements、26/26 branches，100%。

## 门禁

- 68/68 committed schema snapshots verified。
- schema/x2env focused：45 passed。
- PromptRevision consumption + branch gate：20 passed，100%。
- 第二轴独立重跑同一 20-test coverage gate：20 passed，37/37 statements、26/26 branches，100%。
- 相邻 schema/Golden/MCP：337 passed。
- 完整 root：3984 passed，20 skipped。
- Dashboard portfolio/tasks/project 三个 URL 仍为 HTTP 404；没有声称同步成功。

## 不可扩张边界

本证据只证明 strict contracts、Golden aggregate、官方 MCP transport 与受控 application preflight：

- `Image2Env/Video2Env` 尚无 production handler、qualification 或 production Registry exposure；
- 没有运行 Genesis/GPU，没有资产生成、精确复用或 digital-cousin 路径；
- 没有产生 environment package、媒体、physical pass、publishability、promotion 或 robot-policy；
- 没有修改或重签已资格化的 `text2env.compile@1.0.0`；未来 text prompt retry 必须使用新的 exact v2 Skill。
