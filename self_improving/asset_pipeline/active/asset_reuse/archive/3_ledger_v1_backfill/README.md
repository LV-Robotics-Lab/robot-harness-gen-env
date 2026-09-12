# 已归档：ledger v1 backfill（2026-08-11）

`backfill_ledger_v1.py` 是把史前散装元数据（per-model bundle + fragment +
import_matrix）聚合成每资产一份 v1 账本的**一次性迁移工具**，2026-08-09 运行
一次后使命完成。2026-08-10 账本契约升级到 v2（lib/ledger.py 的 builder API
随之变更），本工具仍调 v1 API，如今运行会崩——它的 11 个测试因此长期红色。

- 全部 31 份账本已由 v2 迁移完成（迁移工具为当日 work/oneoff/migrate_ledger_v2.py，
  一次性脚本按惯例不入 git；迁移验证记录见 docs 与 nightwatch 日志）。
- 归档而非删除：保留 v1 时代数据形状的唯一可执行文档；git 历史亦可追溯。
- **不要基于此文件开发**：账本规范文本 = lib/ledger.py 的常量表。
