# Yuxin 来源档案

## 责任边界

Yuxin 主要负责资产检索与复用，包括分层 provider、选择门、网络获取、覆盖率、批量/场景 acquisition、
格式转换、物化、ledger、shadow root、catalog admission 以及 SAPIEN/Isaac 验证历史。Yuxin、HYX、
huyuxinn 是同一来源域的历史名称。

## 权威来源清单与固定锚点

原工作区 `/home/jingxiang/yuxin/env-gen-dev` 已在整合后移除，不能再假定其存在。权威索引是
`self_improving/source_inventory.json`，其中固定：

- source main：`ecea99faaec4e082e1fdbc3716ee498cea8dbf87`
- web studio v2：`ec477bc96e8f7bcae0d28a6dfe3d1ed40b1592e1`
- working-tree snapshot：`0b87fed8a2903be8a13f146b9a2b549658a79b74`
- history merge：`899c649179608d950f7ac776a645d21734af2d20`
- 本仓库历史 ref：`worktree/hyx@e5ad817348dbf2e0bfb6753013181fbcc34a1e36`
- alias screening archive：`324dcae1ef0f8875dccc44e227fd4f5a38be1b32`
- asset sources archive：`8c2f058215b4d69d10f15c269148397a9f14ccd2`
- asset reuse ABC archive：`4982cc3660d82743b849fd0ba9cbdfd04933e895`

## 当前落点

规范整合树为 `self_improving/asset_pipeline/active/`。branch overlay、archive 和 source-only workbench
用于追溯，不应被误当成多个独立当前实现。历史概览还可查
`self_improving/contributor_notes/yuxin-gen-env-overview-legacy.md`。

## 接入原则

- 复用检索、筛选、转换、物化与入库能力，同时保留源 URL、许可和内容 hash；
- 不继承历史 ledger 的缺失证据：新 Golden line 必须重新生成 schema-clean receipt；
- exact reuse、digital cousin 和 fresh generation 必须在 ToolResult 中分型，不能只写“找到资产”；
- 当前 162 份历史 ledger 的 4,082 条 v3 违规是明确阻塞，代码已整合不代表资产已获得运行时资格。
