# 编排摆放生成流程

当需要把 Designer 输出、Critic review 和 validation result 合并成下一步 pipeline artifact 时使用这个 skill。在 agent 流程中，它对应 Orchestrator 能力。

## 规则

- 如果 Critic reject，停止并输出 blocked decision。
- 如果需要 repair，输出具体修复指令。
- 如果通过，写出 stage 为 `final_static_for_smoke` 的最终 `robotwin.tabletop_placement.v0` artifact。
- 保留有效的 object id、asset id、model id 和 pose。
- 把 simulator 和 render 检查项带入 validation plan。
- 没有实际看过 RoboTwin preview evidence 前，不要声称已经完成 visual verification。
- 如果没有 human / VLM / Codex-visual report，smoke 后的合理状态是 `pending_visual_review`。
- 只有 static validation、smoke 和明确 visual review report 都通过时，完整 pipeline 才能标记为 pass。
- 每次新 prompt run 后，都要把新发现的失败模式、资产姿态要求、加载要求回写到相应 skill 和 asset catalog。

## 输出

返回 final placement JSON 和 validation plan。

## 运行经验

- 资产专属修复应放进 asset catalog 的 `placement_defaults`，不要在 harness 里写一次性 prompt 特判。
- 视觉审核失败时，应 repair placement 并重新 smoke，而不是强行覆盖 review 结果。
- 必要时保留 pending 和 pass 两类 run：pending 证明视觉门控生效，pass 证明最终场景经过审核。
