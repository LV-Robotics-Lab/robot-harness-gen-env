# Asset ledger v3 integrity evidence — 2026-08-31

最后复核：2026-09-01。

这份记录区分两件事：账本代码和活动写入口已经在下述协作式运行边界内按 v3 内容契约
fail closed；仓库里既有的 162 份账本则尚未因此自动变成合格数据。没有通过改
`schema_version`、猜 stable pose 或把 catalog 来源字符串伪装成物理 run id 来消除债务。

## 现在由什么构成一份有效账本

- `representations[].files` 是规范排序、无重复的实际依赖闭包，不是“作者愿意列出的几个文件”。
  validator 会解析 URDF、OBJ/MTL、glTF/GLB、DAE 和 USDA 的下游引用，并逐文件复核 SHA-256 与
  bytes；越根引用、网络/绝对引用、缺文件、格式混淆、文件或任一父目录 symlink 都拒绝。
- collision-bearing representation 必须有明确 `collision_meta`。v3 已删除的 `size_bytes`、
  `runtime_default_kg` 和 `runtime_default_basis` 不再因为文档自称 v3 而被放过。
- stable pose 必须带 `measured_against`；verification receipt 必须是结构化的 backend/check/verdict、
  canonical timestamp、非空 run id，并绑定当前 `asset-representation-set.v2` 摘要。这个摘要覆盖目标
  backend 的全部非 snapshot representation、完整 files、几何/坐标系/collision metadata，而不是只
  哈希一个顶层 mesh。
- `ledger_writes.py` 是活动写入口共享的提交边界：先构造完整闭包，再用 `check_files=True` 验证，
  最后原子替换 ledger 或追加 digest-bound receipt。materialize、runtime sweep、articulated
  validation、backfill、migration、fragment、retire、relativize、settle repair 和 writeback 都在
  发布前使用同一严格契约。`rescale_backfill --apply` 仍明确禁用，不能算作已接通的写入口；
  `migrate_v3 --apply` 只允许单 ledger，多 ledger 会在首写前拒绝。
- 锁和发布同样属于证据边界：lock final component 用 `O_NOFOLLOW` 打开；read/validate/compare/write
  在同一锁内完成。空 backend 不能生成 representation digest，同一 producer identity 的相异 receipt
  是冲突而不是幂等。backfill 的 manifest+ledger 对使用 pinned dirfd、双锁和 durable journal，崩溃后
  可确定恢复；retire 有条件回滚。路径段、父目录 symlink、generated admission 的复用/发布换靶与
  gate 后可观察字节漂移均有攻击测试。writeback 当前只有封闭的 SAPIEN issuer，没有 Isaac issuer。

generated admission 仍把确定性生成器的 `generation_qc` 作为 analytic provenance receipt，而不是
SAPIEN settle；admission report 继续写 `physical_qualification=pending_settle`。后续真实 settle 或
runtime receipt 可以保留并且不妨碍同一生成资产复用，但原始 generation-QC、输入 provenance、目录
manifest 或非 verification 内容只要变化，复用就会拒绝。`ledger.lock` 只是锁文件，不进入 payload
身份。

## 适用的信任边界

- runtime evidence 使用 `immutable_snapshot_pre_post.v1`：执行前后对同一 cooperative runtime 可见的
  路径做 samefile/hash 复验，但不声称证明 loader 真正打开的 FD，也不防执行中瞬时 swap 后复原。
- capability 绑定当前 Python 与已加载的 Python/extension-module 文件；它不枚举 ELF `DT_NEEDED`、
  任意 `dlopen`、驱动、`LD_PRELOAD` 或 loader cache。因此这不是同一用户任意代码面前的 OS
  防伪边界，公开 evidence helper 也不能代替受控生产入口本身。
- ledger 的原子替换只覆盖 ledger 文件。representation 是独立的可变文件；同一主机上的敌对写者若在
  最终 gate 与替换之间改文件，post-check 会拒绝并尽力回滚最终 ledger，但不能承诺任何不加锁的 raw
  path reader 从未看见过瞬时字节。可信 consumer 必须重新执行 `validate_ledger(...,
  check_files=True)`，不能把 `load_asset_ledger` 的 raw 结果当作资格。
- 实现依赖 POSIX/Linux 的 `fcntl`、dirfd、`O_NOFOLLOW` 与 `/proc/self/fd`。asset key 使用受限的
  portable namespace，不表示整个包可跨平台运行。

## 代码回归

复核入口（`<sapien-python>`、`<scratch>` 和本地 RoboTwin shadow/catalog 由运行机提供）：

```bash
PYTHONPATH=self_improving/asset_pipeline/active/1_asset_reuse:self_improving/asset_pipeline/active/shared/openxsim/source/agenticsim \
  pytest -q self_improving/asset_pipeline/active/1_asset_reuse/tests
<sapien-python> -m pytest -q \
  self_improving/asset_pipeline/active/1_asset_reuse/tests/test_s13b_validate_articulated.py
<sapien-python> self_improving/asset_pipeline/active/1_asset_reuse/scripts/4_validate/s13b_validate_articulated.py \
  --instance-dir <scratch>/library/<asset>/0 --source-usd <scratch>/source.usd \
  --out <scratch>/s13-out --library-dir <scratch>/library
<sapien-python> self_improving/asset_pipeline/active/1_asset_reuse/scripts/4_validate/s11_runtime_load_sweep.py \
  --shadow <scratch>/shadow --catalog <scratch>/catalog.json \
  --out <scratch>/s11-report.json --library-dir <scratch>/library
```

资产子项目需要显式包含其本地 OpenXSim source root；使用这个真实入口运行完整测试得到：

```text
902 passed, 2 skipped
```

两个 skip 是基础 Python 未安装 SAPIEN 时，`test_s13b_validate_articulated.py` 的成功写盘与
violation/no-write 路径；在真实 `env-gen-yuxin` SAPIEN 环境中又单独运行这两个节点，结果为
`2 passed`。synthetic/file-backed runtime 只用于确定性攻击覆盖，不冒充这次真实 loader 验收。
在最终格式化后的脚本字节上，公共 S13b→S11 链又以 SAPIEN 3.0.0b1、真实 RoboTwin
`envs.utils.create_actor` 和真实 `pysapien` native modules 完成一次最小 articulated asset 回放：
S13b `joint_sweep` 与 S11 `runtime_load` receipt 均为 current，ledger 复核为 0 violation；S11 输出
`SWEEP 1/1`、`late_drift_m=0`、finite/settled/no-penetration。对应 evidence SHA-256 分别为
`ae122f2eb645241d949113f5f25aab88dfee59a0878b8edd7a2216bd1b9cf33a` 与
`6a1541d6a18455dd2f59b8f2791fe8917d6ba2d5f67bea201ce65debfc80fe0a`；篡改 catalog stable
orientation 后同一公共 S11 在 loader 前明确拒绝。SAPIEN 3 的 rigid settle 也真实执行通过，证明新的
collision-component tight-AABB 路径不再调用不存在的 `Entity.get_global_aabb()`；这次只验证函数和
in-memory pose promotion，没有写入 rigid ledger/evidence。上述是最小集成资产回放，不代表 162 份
既有 ledger 或完整生产资产库已经逐项回放。
完整 asset-reuse 套件下，`ledger.py`、`ledger_writes.py` 与 `writer_paths.py` 合计 2,391 statements /
1,148 branches，综合覆盖 94%（分模块 94% / 90% / 100%），不写成全覆盖。Harness 的 generated
admission、compile qualification、application、CLI 与 handler 五组独立回归为 `164 passed`。攻击测试覆盖闭包
缺失/篡改/symlink/opaque 格式、stale/冲突/空-backend receipt、共享依赖删除、路径逃逸、lock symlink、
并发 CAS、gate 后变更、backfill recovery、materialize loser ownership、generated 目录复用/发布换靶、
后续合法 receipt 复用、原始 generation-QC 篡改和无物理证据等反例；测试支持还锁住 synthetic
runtime module 不得泄漏到后续用例。

## 既有数据的 RED 基线

对活动 asset library 65 份和 upstream ledgers 97 份做只读审计：`162/162` 均被新门拒绝，共
`4,082` 条 violation：

| 类别 | 数量 |
| --- | ---: |
| deleted field | 1,915 |
| required field missing | 1,757 |
| stable pose `measured_against` missing | 410 |

字段层包括：1,101 个旧 `size_bytes`、1,101 个缺 `files`、563 个 collision representation 缺
`collision_meta`、407+407 个旧 runtime-default 字段、93 个缺 `external_ids.env_gen`，以及 410 个
pose provenance 缺失。完整 audit JSON 因体量不提交；upstream report 为 679,892 bytes / SHA-256
`881d2f8485f15be48be8ceef0a06337359b76a98377b7a107aa283b3437b8a98`，library report 为
133,108 bytes / SHA-256 `52592a69f173017acd6bd0b4a040ae266c5db21754b0fa345bae2a47cc2dea37`。

本切片修改既有 ledger 数据文件数为 0。能从现有 bytes 无损证明的字段可以由 migrator 补；不能
证明闭包或真实 settle 的项目会输出 typed debt 并拒绝写盘。剩余 pose debt 必须关联已有可信
receipt 或执行真实 replay，不能用迁移日期、source commit 或任意字符串补齐。

因此状态是 `implementation_pass_data_debt_open`：新写入和复用不会再制造表面 v3，旧数据也不会被
错误宣告健康。
