# Compile 端到端链路验收（2026-09-06）

## 一句话结论

本次结果是 **部分通过**：全新资产生成 `10/10`、已有资产直接复用 `5/5`、可移植运行收据
`20/20` 均通过；Digital Cousins 资产接入 `0/5`，五次真实 compile 调用都在 solve 阶段以
`T2E_ASSET_UNAVAILABLE` 阻断。它证明当前 compile 的生成、CAS、catalog、入库、再选择链路可用，
但不能证明 Digital Cousins 已接入，也没有运行 SAPIEN replay 或真机验证。

## 验收对象与复现方式

- 分支：`worktree/bingsheng`
- 源码基点：`dd3c675d73b2770aa8238c32e5cf6e09299b5c8d`
- Python：`3.13.12`
- 批次随机种子：`20260906`
- 命令：

  ```bash
  python script/run_compile_acceptance.py \
    --out-root data/compile_acceptance_20260906_deliverable \
    --seed 20260906
  ```

所有大体积运行产物位于被 Git 忽略的目录：

`/home/jingxiang/bingsheng/robot-harness-gen-env/data/compile_acceptance_20260906_deliverable`

输入清单是 `inputs.json`，SHA-256 为
`c409d341ee4987a2ea2335f944171be506e8849ba7fa568fe4cf7af4730ede37`。固定随机种子使这 20 条
输入可重复生成，不依赖临时人工挑选。

## 1. 全新生成：10/10 通过

每条输入从空 catalog 开始并设置 `generate_missing_assets=true`。compile 实际完成：解析输入、
生成 OBJ/MTL/metadata/provenance、静态求解与验证、发布有效 catalog、写 CAS、写 v3 ledger、
把资产放入批次独立的 `asset-library/generated/`。十份 admission 均为 `admitted`；十份 ledger
以 `check_files=true` 回读均为零错误。

| 用例 | 随机输入 | 入库资产编号 |
| --- | --- | --- |
| fresh-01 | blue ceramic slender bollard | `900_gen_slender_bollard_0ea95028` |
| fresh-02 | pink metal rounded caddy | `900_gen_rounded_caddy_7e5d9572` |
| fresh-03 | purple glass ribbed obelisk | `900_gen_ribbed_obelisk_5b266bdd` |
| fresh-04 | green ceramic low riser | `900_gen_low_riser_b48ecdc5` |
| fresh-05 | pink plastic square marker | `900_gen_square_marker_58006ccb` |
| fresh-06 | white glass octagonal plinth | `900_gen_octagonal_plinth_3c70a118` |
| fresh-07 | red plastic hexagonal pedestal | `900_gen_hexagonal_pedestal_2b168a85` |
| fresh-08 | yellow metal stout tower | `900_gen_stout_tower_6ee2c88e` |
| fresh-09 | blue wooden grooved puck | `900_gen_grooved_puck_9dccac6e` |
| fresh-10 | white glass tapered stand | `900_gen_tapered_stand_50e257c4` |

关键证据：

- 每条运行：`runs/fresh-XX/summary.json`、`run_state.json`、`invocation.json`、`events.json`、
  `typed_output.json`、`portable_receipt.json`
- CAS：`state/cas/sha256/`
- 入库资产与 ledger：`asset-library/generated/<asset_id>/`
- 汇总图：`media/fresh-contact-sheet.png`
- 每条资产：`media/fresh-XX/preview.png`、36 帧 `turntable.mp4` 与原始 frames

边界：这些是确定性程序生成的 proxy 资产，其中识别到 hexagonal/octagonal 的用对应棱柱，其他
不认识的语义退化为有界 box proxy。admission 中的物理资格仍明确写着 `pending_settle`，不能把
“生成成功”理解成高保真资产或物理验证通过。

## 2. 已有资产复用：5/5 通过

先把 fresh-01 至 fresh-05 已入库的五个资产组成新的有效 catalog，再把
`generate_missing_assets=false` 固定后执行五次 compile。五次结果均满足：

1. 选出的资产编号与对应 fresh 用例完全相同；
2. 输入 catalog SHA-256 均为
   `3ff26a056c9bee48aec5fa03b3e2f35b423df8bfcd01586e600ad343dfae3f49`；
3. 没有 asset generation artifact；
4. 没有 asset admission artifact；
5. resolved scene 直接引用既有资产库路径。

这说明复用不是“重新生成后给相同名字”，而是 catalog 直接选择先前已入库的同一内容身份。
逐条证据在 `runs/reuse-XX/summary.json` 的 `reuse_verification`；复用 catalog 在
`catalogs/existing-reuse.catalog.json`；图片和视频在 `media/reuse-XX/`。

这五条的“已有”指本批次第一阶段刚刚生成并完成入库的资产，证明的是同一条资产库的二次使用。
它不是对历史 RoboTwin 大资产库的抽样验收。

## 3. Digital Cousins：0/5，真实阻断

五条输入分别请求 cabinet、basket、tray、bowl、microwave，并真实调用 production compile，且关闭
缺失资产生成，防止程序用普通 proxy 冒充 Digital Cousins 资产。五次均返回：

- `status=blocked`
- `code=T2E_ASSET_UNAVAILABLE`
- `stage=solve`
- 无 selected asset id、无生成报告、无入库报告

预检 `digital_cousins_preflight.json` 记录了当前事实：

- 子模块存在，commit 为 `b1aa90a8dec7273e5c5a4a60450dc829083ce0a0`；
- 子模块只有两张示例输入图，没有可供 compile 使用的 catalog / step-2 / step-3 资产输出；
- `acdc` 环境中 `digital_cousins`、`omnigibson`、`groundingdino`、`sam2` 四项导入均不可用；
- 因此 `usable_for_compile=false`。

这五条没有视频，因为没有 Digital Cousins 资产可渲染。`media/digital-cousins-XX/blocked.png` 是
阻断状态卡，不是资产图片；`media/digital-cousins-blocked-sheet.png` 是五条失败的汇总视图。

## 收据、CAS 与媒体完整性

- 20 条终态都发布了 `harness.portable_run_receipt.v1`；随后只使用目标
  `portable-cas/` 独立加载收据、资格、Invocation、RunState、事件 transcript 和所有输入/输出
  artifact，`20/20` 成功。
- production CAS 有 130 个对象、303,639 bytes；portable CAS 有 210 个对象、925,685 bytes。
- 15 个成功场景各有一个 36 帧 MP4；FFprobe 全部读到 36 帧，各用例的 36 张源帧摘要均不同。
- 媒体由已入库 OBJ 文件渲染的转台预览生成，不是物理 replay，不证明接触、沉降、漂移或可见性门。
- `evidence_manifest.json` 记录 1,305 个文件；独立回算无摘要不一致。清单 SHA-256 为
  `0f65093cf688093e917bf34225625864b337d88c334a6c9c570b63b5b60a6f9a`。
- 结构化总表 `campaign_summary.json` SHA-256 为
  `387a8b07db0ca430383c2a99355769da0c4df6827338ffd96c91195ebf779abb`。

## 代码层发现并修正的问题

批量验收最初暴露出 portable receipt 对 production compile 的两个真实兼容性问题：正式资格 JSON
保留可读排版时被错误当作内容不可信；同一 catalog 内容在输入、输出使用不同显示名时被错误当作
CAS closure 不一致。修正后以内容 SHA、bytes、media type、schema 绑定身份，同时继续拒绝内容缺失、
哈希不符或元数据冲突；相关专项测试为 `45 passed`。

## 下一步

要让本报告从“部分通过”变成“全部通过”，还需要：

1. 恢复并锁定 Digital Cousins 的完整环境与数据许可，真实生成至少五份 step-2/step-3 输出；
2. 写 Digital Cousins → RoboTwin catalog/ledger 适配器，记录来源编号、模型文件摘要、尺寸和许可；
3. 把五份资产入库后，用 `generate_missing_assets=false` 重跑同一批 Digital Cousins compile；
4. 对 20 个成功 EnvironmentPackage 再跑 qualified replay 与 validate，单独交付 SAPIEN 视频、
   运行时证据和 publishability 结论；在这一步完成前不宣称物理仿真通过。

## 测试记录

- compile application、compile handler、portable receipts 与本批次计划的定向回归：`105 passed`。
- Harness + validate snapshot：`2,092 passed, 19 skipped, 1 failed`，语句与分支覆盖均为 `100%`。
  唯一失败是工作区中原有未提交的 `IMPLEMENTATION_LOG.md` 与 checked-in replay qualification 的
  源码清单摘要不一致，不是本批次 compile 结果失败。
- `python script/export_harness_schemas.py --check`：17 份 schema snapshot 通过。
- 统一 `script/run_self_improving_tests.sh` 在进入 pytest 前被 PEARL portal 删除后的旧
  `source_inventory.json` 挡住：清单仍把 `apps/pearl_evidence_portal` 标为 required。按本次要求没有
  恢复 portal；该删除的清单/测试收尾应作为独立任务处理。
