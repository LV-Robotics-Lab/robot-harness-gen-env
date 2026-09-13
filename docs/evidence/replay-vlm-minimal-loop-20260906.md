# Replay → VLM 最小闭环实跑报告（2026-09-06）

## 结论

本次已把新 Harness 的一条成功 replay 正式接到本机 Qwen2.5-VL-3B：评估程序从 replay
持久账本恢复输入和终态，从同一 CAS 复核 resolved scene、runtime evidence 与四张图片，离线调用
模型一次，再把模型原始回答和结构化评估收据写回同一 CAS，并在同一 SQLite 事件流中留下五段真实
回调事件。

这条评估目前以 `text2env.replay_vlm@0.1.0` qualification candidate 运行，不是已晋升的 production
Skill。VLM 只提供视觉建议，`claims_physical_pass=false`；物理通过仍只能来自确定性 replay/runtime
门控。

## 输入 replay

- replay run ID：`0000f972-15e7-4ada-aec6-707e8973acde`
- replay 状态库：
  `/home/jingxiang/bingsheng/replay-qualification-run-17427bc/production/replay-cli-8ea46f8-2/harness.sqlite3`
- CAS 根目录：
  `/home/jingxiang/bingsheng/replay-qualification-run-17427bc/qualification/scratch-4/compile-application/cas`
- resolved scene 逻辑摘要：
  `96e995144468707f0f6e169341ce13e2330b1f42a8acaf73d9c126925e4be3ee`
- runtime evidence：
  `1c74e310fff651ef7ca562f8e57b66fdf6f4e2c7a0e14abc8d2c878435351f1f`

送入模型的四张图按固定顺序为：

1. `observer_start`：`738693faf957f83a895a50c2dedf880495c03bd3dc045c53f341ae13a9e3d32e`
2. `observer_mid`：`bb4cc281a42bcca937e9084b91030f19d04f2e1bf044e5ae4e879ca5e2045b97`
3. `observer_end`：`c93468cf0c1e381f573e0e3c7b8760146d94fe2b2c7907241fa184b47d2b17ce`
4. `preview_head`：`1f6a509d3dbbab9061106ca1fdf0fbd0b62063c4eb79cc01647b8d28033eaaca`

程序不信任文件名：每张图都必须同时存在于 replay typed output、terminal artifacts 和 CAS，且
bytes/SHA-256 复核一致。缺图或摘要漂移会在模型调用前阻断。

## 正式 VLM 运行

- assessment run ID：`1b0e6f80-c03f-4af4-b621-0568ce263ef4`
- terminal 状态：`succeeded`
- 运行状态文件：
  `/home/jingxiang/bingsheng/replay-vlm-minimal-loop-20260906/run-state-final-v2.json`
- 状态文件 SHA-256：
  `69bb8b50421537501375664b512c4072987d081429f1e791e7703b5f6a2b96c9`
- assessment receipt：
  `/home/jingxiang/bingsheng/replay-qualification-run-17427bc/qualification/scratch-4/compile-application/cas/sha256/ad/ad7e7dc3bbe6d4d775d348810843ddb015ccc9e4e87fc8ae428ac4fdd42a7f53`
- receipt SHA-256：
  `ad7e7dc3bbe6d4d775d348810843ddb015ccc9e4e87fc8ae428ac4fdd42a7f53`
- raw response：
  `/home/jingxiang/bingsheng/replay-qualification-run-17427bc/qualification/scratch-4/compile-application/cas/sha256/a1/a11b9542dc89651372b59fc7ddd89afdf0ddc067c53ba062a626b62c1cef94f8`
- raw response SHA-256：
  `a11b9542dc89651372b59fc7ddd89afdf0ddc067c53ba062a626b62c1cef94f8`

模型身份：

- model：`Qwen/Qwen2.5-VL-3B-Instruct`
- revision：`66285546d2b821cf421d4f5eb2576359d3770cd3`
- snapshot manifest：`b0f4cf794ec552f5e23de019107801e5adfec633d3b5c74564cdf6b65e9f1cad`
- model content manifest：`dd904e42c13f7a47296e1aff8ce8a78090a86451d24b5ccd0d2807d29e6e4cad`
- model roster：`e8c79978ae33176974b48eb567f4b3e6986720bb477da7675f2f592c523dc690`
- prompt：`replay_visible_advisory_v1`，SHA-256
  `98c8fa9886cb74aad32d78776c34e599ae5d4829bd5eb0279b834b7a4c480a88`
- provider 代码闭包依赖：
  `45a911d94dd9917f63ff15336f7c9f17535491b4ddfcb279a441745da321c81d`

资源收据：一次 visible VLM 调用，GPU 时间 3169 ms，峰值显存 7387 MiB，输入 1254 tokens，
输出 128 tokens，网络调用 0，付费远端调用 0，没有新 compile 或新 physical replay。

事件主账中的全局 event ID 为 34–38：

1. `qualification_candidate.preflight`
2. `qualification_candidate.vlm.replay_evidence.bound`
3. `qualification_candidate.vlm.inference.started`
4. `qualification_candidate.vlm.assessment.published`
5. `qualification_candidate.complete`

第 35 条事件引用 runtime evidence 和四张输入图；第 37、38 条引用 assessment receipt 和 raw
response，因而 UI/Workbench 后续可从正式事件流读取，而不需要扫描临时目录。

## 为什么结论是 `format_invalid`

模型确实看图并返回了内容。它识别到 can 与 plate，并对无法从视角确认的悬浮/穿透选择
`abstain`；但回答外包了 Markdown code fence，而且 `checks` 的内部形状没有遵守本次固定输出
schema。严格 consumer 因此没有猜测或清洗答案，而是保存原始 470 bytes，并把建议状态记为
`format_invalid`、`parsed_response=null`。

这不表示 replay 失败，也不覆盖 replay 已有的物理结论。它是下一步 prompt fallback 的真实、可重放
输入：fallback 应在有预算上限的情况下做一次格式修复，并继续禁止 VLM 产生物理 pass。

## 使用方法

入口是 `script/run_replay_vlm_assessment.py`。必需参数分四组：已有 replay 的数据库和 CAS、
replay run ID、隔离 GPU Python 与工作目录、固定模型的 snapshot/content/roster 摘要。命令退出码
0 表示 assessment run 自身完成，2 表示 Harness 明确 blocked/failed；`advisory_status` 必须另行读取，
不能把退出码 0 当成 VLM pass。

本次完整命令可从仓库根目录执行：

```bash
PYTHONPATH=. python script/run_replay_vlm_assessment.py \
  --database /home/jingxiang/bingsheng/replay-qualification-run-17427bc/production/replay-cli-8ea46f8-2/harness.sqlite3 \
  --artifact-root /home/jingxiang/bingsheng/replay-qualification-run-17427bc/qualification/scratch-4/compile-application/cas \
  --replay-run-id 0000f972-15e7-4ada-aec6-707e8973acde \
  --interpreter /home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python \
  --work-root /home/jingxiang/bingsheng/replay-vlm-minimal-loop-20260906/worker-next \
  --model-revision 66285546d2b821cf421d4f5eb2576359d3770cd3 \
  --model-snapshot /home/jingxiang/.cache/huggingface/hub/models--Qwen--Qwen2.5-VL-3B-Instruct/snapshots/66285546d2b821cf421d4f5eb2576359d3770cd3 \
  --snapshot-manifest-sha256 b0f4cf794ec552f5e23de019107801e5adfec633d3b5c74564cdf6b65e9f1cad \
  --model-content-manifest self_improving/studies/vlm_fallback_prompt_optimization/amendments/01/model_content_3b.json \
  --model-content-manifest-sha256 dd904e42c13f7a47296e1aff8ce8a78090a86451d24b5ccd0d2807d29e6e4cad \
  --model-roster-sha256 e8c79978ae33176974b48eb567f4b3e6986720bb477da7675f2f592c523dc690 \
  --output /tmp/replay-vlm-run-state.json
```

## 测试与已知红灯

- 新闭环及 schema 定向测试：15 passed。
- Harness + 冻结 VLM study 合集：2663 passed、19 skipped、3 failed。
- 三个红灯均不是本切片行为回归：一个来自工作区已有的
  `self_improving/harness/IMPLEMENTATION_LOG.md` 改动与旧 replay qualification manifest 不一致；
  两个来自按用户要求保留的 PEARL portal 删除，使冻结旧 study inventory 指向的图片不再存在。
- 没有恢复 PEARL 文件，也没有把旧 frozen study 改写成已执行实验。

## 下一步

最小 replay→VLM wiring 已完成；完整“校正闭环”尚未完成。下一个独立 feature 应增加一次有上限、
单一目的的 format-only fallback，分别保存初次回答、修复 prompt、第二次回答和费用，并以基线/实验
日志证明格式成功率是否提升。之后才讨论 qualification/promotion，不能直接把当前 candidate 注册为
production Skill。
