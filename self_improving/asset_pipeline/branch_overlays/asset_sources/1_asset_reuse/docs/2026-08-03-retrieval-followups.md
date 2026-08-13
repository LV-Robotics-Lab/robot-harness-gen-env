# 检索层 v1 · 终审后续事项（post-merge follow-ups）

终审结论：MERGE-READY（must-fix 已修，commit 0b8554a）。以下为已裁定的非阻塞后续项，按优先级：

1. [done] scene_acquire 陈旧 PASS 防护：runner 返回码 + resolved_scene 新鲜度检查（复用 --out 目录时的误报风险）
2. [done] acquire_batch 批循环逐条 try/except：单条坏条目不应中断整批并丢失 evidence
3. [done] 证据保真包：write_evidence 补清单哈希；GitHub 通路区分 fetch_failed/convert_failed；validation_failed:<gate> 提取真实门禁名；no_token_match 统计
4. [done] coverage_report 增 "acquired" 状态（区别于复查后的 covered）
5. [done] tier-0 catalog 与 coverage catalog 统一或写明配置纪律；文档化颜色限定缺口的 v1 局限
6. [done 2026-08-08] 真实回退演练：scissors 拒收 validation_failed:tilt → 自动落第二名 foam_brick 入库（attempts=2，evidence: results/_test/20260808_fallback_drill；演练资产已清理，catalog 恢复 141）；[done] local 分支去重简化；[done] acquired_manifest 增 source 字段；[done] github_discovery token 配置面

（2026-08-08 hardening pass：1–5 全部完成 + 6 的 source 字段/token 配置面两个代码点；6 剩余两项按裁定不在本轮范围，留待后续。）
