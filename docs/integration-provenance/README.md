# 整合来源核对档案

本目录是 Golden E2E 工作的功能级来源台账，用于在实现过程中和最终验收时回答四个问题：

1. 某项能力最初来自哪里、固定在哪个版本？
2. 原始实现、第三方上游与 Harness 整合分别由谁负责？
3. 哪些内容只是参考或候选，哪些已经进入当前分支？
4. 当前结论通过了合同测试、真实运行门，还是仍被某个已知问题阻塞？

这里记录的是可审计的整合事实，不是以目录归属推断个人原创。三类责任必须分开：

- `origin_owner`：原始能力或候选实现的来源责任人；
- `upstream_owner`：Genesis、SimFoundry、RoboTwin 等第三方项目；
- `integration_owner`：把能力映射到 Harness schema、CAS、receipt、测试和文档的人，当前总体由
  Bingsheng 负责。

## 核对入口

- [功能级整合台账](LEDGER.md)：最终核对的权威清单。
- [条目模板](ENTRY_TEMPLATE.md)：新增或更新一项整合时必须填写的字段。
- [Bingsheng 来源档案](sources/bingsheng.md)：Harness 与总体整合。
- [Gujie 来源档案](sources/gujie.md)：x2env、Genesis/SimFoundry adapter 候选实现。
- [Yuxin 来源档案](sources/yuxin.md)：资产检索、复用、入库与历史 ledger。

## 状态词汇

生命周期 `lifecycle`：

- `reference_only`：只用于研究或设计，没有计划直接整合代码；
- `candidate`：已固定来源，正在评估或等待整合；
- `integrating`：正在映射、测试或修复；
- `integrated`：已进入当前分支的正式路径；
- `retired`：已被替代，保留历史记录。

验证状态 `verification`：

- `not_run`：尚未执行相应验证；
- `contract_pass`：类型、单元或离线合同通过，但不代表真实仿真通过；
- `runtime_pass`：已通过条目声明的真实运行门；
- `blocked`：存在明确、已记录的阻塞；
- `superseded`：验证证据已被新版本替代。

`integrated` 与 `runtime_pass` 是两个独立维度。代码已经合并，不等于资产干净、Genesis sim-ready
或 Golden E2E 已完成。

## 更新规则

每个整合切片应在同一个功能提交中完成以下操作：

1. 在 `LEDGER.md` 增加或更新唯一 `integration_id`；
2. 固定源仓库、ref/commit、源路径和第三方依赖 ref，禁止只写可移动分支名；
3. 写明目标路径、适配或重写方式，以及没有复用的部分；
4. 绑定 contract/spec、测试、真实运行证据、Golden run/receipt（若已有）；
5. 如源工作树含未提交内容，单列 dirty snapshot 身份，不能把 HEAD 冒充为全部来源；
6. 同步 `self_improving/golden_e2e_progress/` 中相应任务和结果。

最终核对只有在每个宣称交付的条目都具备固定来源、目标实现、验证证据和明确责任边界后才完成。
未达到真实运行门的条目必须保留 `contract_pass` 或 `blocked`，不得写成 sim-ready。
