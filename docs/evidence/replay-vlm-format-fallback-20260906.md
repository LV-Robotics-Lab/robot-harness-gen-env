# Replay → VLM 最多一次格式修复实跑报告（2026-09-06）

## 结论

`text2env.replay_vlm` 已从 `0.1.0` 升到 `0.2.0`，并接通“最多一次”的格式修复：首答只有在
`format_invalid` 时才会触发第二次 VLM 调用；第二次之后无论成功与否都会停止。首答、修复提示、
第二答、逐次资源用量和总资源用量都进入同一份 CAS 收据。

本次真实 can-on-plate 运行证明重试上限和失败留痕都生效，但模型没有修好格式：首答带 Markdown
fence 且 checks 是嵌套对象；第二答仍保留了嵌套 checks。程序没有继续第三次，也没有猜测性清洗，
最终建议为 `format_invalid`。assessment run 自身为 `succeeded`，只表示完整收据已落账，不表示 VLM
建议通过，更不表示物理通过。

## 这次新增的门

1. 正常首答符合严格 JSON schema 时，只调用一次。
2. 只有首答为 `format_invalid` 才生成 `replay_visible_format_repair_v1` 提示并调用第二次。
3. 修复提示包含首答 SHA-256、首答原文以及确定性归一化出的唯一允许 JSON；第二次 attempt 的
   `repair_of_sha256` 必须等于首答原文摘要。
4. 父进程重新解析两次原文、复算 prompt/response 摘要、逐次资源和汇总资源，拒绝第三次 attempt、
   错误绑定、伪造 parsed response、联网调用或资源计数漂移。
5. `replay_vlm.format_only_preservation.v1` 以保守规则检查“只改格式”：嵌套状态只有全部一致才可折叠，
   缺失检查只能补 `abstain`，overall 与 explanation 不得改义。无法证明保持时第二答仍记为
   `format_invalid`，并保留不匹配字段。
6. `claims_physical_pass` 仍固定为 false；VLM 没有物理 gate 权限。

## 正式输入

- source replay run ID：`0000f972-15e7-4ada-aec6-707e8973acde`
- replay SQLite：
  `/home/jingxiang/bingsheng/replay-qualification-run-17427bc/production/replay-cli-8ea46f8-2/harness.sqlite3`
- CAS：
  `/home/jingxiang/bingsheng/replay-qualification-run-17427bc/qualification/scratch-4/compile-application/cas`
- resolved scene SHA-256：
  `96e995144468707f0f6e169341ce13e2330b1f42a8acaf73d9c126925e4be3ee`
- runtime evidence SHA-256：
  `1c74e310fff651ef7ca562f8e57b66fdf6f4e2c7a0e14abc8d2c878435351f1f`
- 四张图仍按 `observer_start`、`observer_mid`、`observer_end`、`preview_head` 固定顺序读取，摘要分别为
  `738693fa…`、`bb4cc281…`、`c93468cf…`、`1f6a509d…`。

模型为本机离线 `Qwen/Qwen2.5-VL-3B-Instruct`，revision
`66285546d2b821cf421d4f5eb2576359d3770cd3`。snapshot manifest、model content manifest 与 roster
摘要沿用上一轮已核验身份；网络和远端付费调用均禁用。

## 正式运行证据

- assessment run ID：`095fc25b-32f0-4c67-aeed-64083ce741fb`
- skill：`text2env.replay_vlm@0.2.0`
- run 状态：`succeeded`
- 最终 advisory：`format_invalid`
- run state：
  `/home/jingxiang/bingsheng/replay-vlm-format-fallback-20260906/accepted-final-run-state.json`
- run state SHA-256：
  `2b0ad3fa2eff4725e49510153853428d22addc9d463269b06b53c295947416a0`
- worker request：
  `/home/jingxiang/bingsheng/replay-vlm-format-fallback-20260906/accepted-final-worker/095fc25b-32f0-4c67-aeed-64083ce741fb/request.json`
- worker response：
  `/home/jingxiang/bingsheng/replay-vlm-format-fallback-20260906/accepted-final-worker/095fc25b-32f0-4c67-aeed-64083ce741fb/response.json`
- worker request / response SHA-256：
  `645ee32ecb88ef0bb40a78fbdd4bd51bbc7078229c06c17d79ec9f6d906d3c2b` /
  `9821b0ce6aacba870c7fbdbb9548bf1421a4fa4b52d8c143af5987a9cb1b5b76`
- v2 assessment receipt：
  `/home/jingxiang/bingsheng/replay-qualification-run-17427bc/qualification/scratch-4/compile-application/cas/sha256/14/14e372c4465c85881e7b4b84f4303971b8b7a5931b90fffc95c9a41e1cbd3546`
- receipt SHA-256：
  `14e372c4465c85881e7b4b84f4303971b8b7a5931b90fffc95c9a41e1cbd3546`

逐次证据：

- 首答原文：SHA-256
  `a11b9542dc89651372b59fc7ddd89afdf0ddc067c53ba062a626b62c1cef94f8`，470 bytes，
  `format_invalid`。
- 格式修复提示：SHA-256
  `f1a7a4531d0f42ae04739beefefcf7d0821f9e240f276210ebbc782e8d66101c`，1799 bytes；
  同时是第二次 attempt 的 `prompt_sha256`。
- 第二答原文：SHA-256
  `90b2c0a99f53685a8ca16a2b0f629d3c1574e22a2af2b834fc74e0fec189a1a6`，434 bytes，
  仍为 `format_invalid`。
- 第二次的 `repair_of_sha256` 精确等于首答摘要。preservation receipt 显示首答 JSON 可恢复，但
  第二答依旧不是严格 schema，`mismatched_fields=["repaired_response.invalid"]`、`preserved=false`。

总资源收据为 2 次 visible VLM invocation、GPU 时间 4719 ms、峰值显存 7388 MiB、输入 2835
tokens、输出 241 tokens、网络调用 0、远端付费调用 0。峰值显存取两次调用的最大值，不作无意义相加。

事件流新增 `qualification_candidate.vlm.format_fallback.completed`，它绑定首答、第二答和修复提示；
随后 `vlm.assessment.published` 再绑定完整 v2 receipt。这样工作台以后可以按真实回调展示 fallback，
不需要猜目录。

## 测试

- assessment / worker / subprocess provider 专项：13 passed。
- 公共 schema 已增加 `harness.replay_vlm_assessment_output.v2`，20 份 snapshot 校验通过。
- 仓库主套件：3015 passed、19 skipped、1 failed。唯一失败是工作区已有
  `self_improving/harness/IMPLEMENTATION_LOG.md` 与旧 replay qualification manifest 不一致，不是本切片
  新增回归。
- 冻结 VLM study：665 passed、2 failed。两项仍因按用户要求保留的 PEARL 删除导致旧 inventory 缺图；
  本切片没有恢复 PEARL，也没有改写冻结实验。

## 当前边界

“最多一次 fallback”已经实现并由真实 GPU 运行证明；“这台 3B 模型能稳定修好格式”没有被证明，
本次恰好失败。下一步若要提高成功率，应把它作为 prompt 实验做多样本基线和量化，而不是增加第三次
重试或在 consumer 里偷偷清洗答案。
