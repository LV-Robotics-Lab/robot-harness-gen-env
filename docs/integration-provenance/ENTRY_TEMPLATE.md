# 整合条目模板

复制以下段落到 [LEDGER.md](LEDGER.md)，并删除占位说明。

## INT-XX-NNN — 功能名称

- `lifecycle`：`reference_only | candidate | integrating | integrated | retired`
- `verification`：`not_run | contract_pass | runtime_pass | blocked | superseded`
- `origin_owner`：原始能力或候选实现责任人
- `upstream_owner`：第三方项目；没有则写 `none`
- `integration_owner`：当前分支适配、验证和交付责任人
- `source_repository`：源仓库绝对身份或公开 URL
- `source_ref`：不可移动 commit/tag；不得只写分支名
- `dirty_snapshot`：`none`，或能唯一核对未提交字节的 manifest/hash/证据
- `source_paths`：具体文件或目录
- `target_paths`：当前仓库正式落点
- `integration_method`：直接复用、移植、anti-corruption adapter、重写或仅参考
- `contract_spec`：对应合同、schema 或 ADR
- `verification_evidence`：测试命令、报告、receipt、媒体或 commit
- `golden_run`：Golden run/operation ID；尚无则写 `none`
- `known_gaps`：尚未完成或不能宣称的能力
- `last_verified`：`YYYY-MM-DD`

### 核对结论

用一段话说明哪些字节/设计来自源实现、哪些由 Harness 新增，以及当前证据允许作出的最强主张。
