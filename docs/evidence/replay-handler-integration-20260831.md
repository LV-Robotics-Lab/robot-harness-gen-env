# Text2Env replay handler integration evidence — 2026-08-31

这份证据只回答一个问题：当前候选 `text2env.replay` adapter 是否已经把 Invocation 依赖、一次
RoboTwin 执行、诊断保留、runtime evidence 复核和媒体晋升接成一条可测试的 consumer 路径。答案是
“候选竖切已接线”；它不是 production qualification，也没有新增一轮 900 settle / 120 contact / 120
frames 的真实 handler 回放。

## 已闭合的候选路径

`Text2EnvReplayDependencyResolver` 先从 CAS 重物化 EnvironmentPackage，重新解析 scene/resolved/catalog
绑定，并为这次公开输入独立计算 runtime asset snapshot。它只返回下面五项、按名字排序的
`DependencyRef`；缺项、多项、重复名或任一摘要不一致都会拒绝：

| Invocation dependency | 绑定的事实 |
| --- | --- |
| `text2env.replay.capability` | 预期 runtime capability 摘要 |
| `text2env.replay.executor` | interpreter、runner 与监督策略的 canonical identity |
| `text2env.replay.handler_config` | allowed roots、task、阈值、capability、media verifier 等 handler 配置 |
| `text2env.replay.media_verifier` | 完整解码器及沙箱策略的 canonical identity |
| `text2env.replay.runtime_assets` | 当前输入选中 loader tree 的 CAS snapshot manifest |

handler 不复用 resolver 的内存对象或可变 side channel。它再次重物化 package、再次计算 asset
snapshot，并把这五项与 `RunContext.dependencies` 精确对账后才允许 executor 启动；executor 返回后、
解释结果前再对账一次。每个调用只提交一个 `RuntimeJob`、调用 executor 一次。失败或崩溃留下的
capability、事件 transcript、stdout/stderr、probe 和安全的 partial outputs 只作为诊断制品保留。

成功执行的 runtime JSON、PNG 和 MP4 也先以 `application/octet-stream` 进入 CAS。handler 随后复核：

- capability 前后身份、严格事件生命周期、checkpoint 数列和 allowlisted 输出集合；
- scene/package/catalog/runtime-asset 摘要与 runtime evidence 的配置和时间线；
- validation report 的 scene/resolved 身份、check 状态和计数自洽；
- PNG/MP4 的完整解码结果，其中视频必须是 `iso-bmff/mp4`、`h264`、`8bit-420`，SAR 只能为
  `1/1`，或 `0/1` 且明确记录“未声明、默认方形”；120 帧配置下仍须至少 30 个 decoded unique
  frames。

只有全部 consumer-side 检查通过，runtime JSON 与媒体才以公开 schema/media type 重新晋升，并写
path-free replay receipt。物理 validation 为 `fail` 仍可构成一次成功的 replay evidence acquisition；
是否发布由独立 validate/promotion gate 决定，handler 不把 acquisition success 改写成物理通过。

## 专项回归

```bash
pytest -q \
  tests/self_improving/harness/test_replay_dependencies.py \
  tests/self_improving/harness/test_text2env_replay_handler.py \
  --cov=self_improving.harness.handlers.text2env_replay \
  --cov=self_improving.harness.replay_dependencies \
  --cov-branch --cov-fail-under=100 --cov-report=term-missing
```

记录结果为 `185 passed`。`text2env_replay.py` 为 663 statements / 240 branches 全覆盖，
`replay_dependencies.py` 为 157 statements / 46 branches 全覆盖。攻击面包含 package/catalog 绑定、
五依赖缺失/额外/重复/漂移、source asset 漂移、executor/capability 漂移、事件和输出协议、诊断保留、
CAS 二次对账、runtime JSON、validation report，以及 format/codec/pixel/SAR/default flag 等媒体元数据。

## 尚未取得的证据

- 尚无 replay qualification generator、固定 qualification bundle 或 production application；当前
  factory 只组装 handler/resolver 候选。
- 尚未用当前 handler 经 direct candidate 与 Registry 两条路径各执行固定 900/120 资格案例；因此
  没有新的 handler receipt、CAS 回读或独立 validate/promotion 证据。
- A035 的真实 2-step RoboTwin/SAPIEN smoke 只证明 executor、事件与 immutable asset substrate；它的
  validation 按设计失败，不能倒推本 handler 已完成长程资格化。
- 公开 descriptor 仍声明 `deterministic=True`，而真实物理运行、媒体字节与资源指标可能变化。正式
  qualification 前必须先给这种可复验但未必 bitwise-identical 的执行定义版本化语义，不能靠测试
  double 的内容稳定性替代证明。

因此本记录的状态是 `candidate_vertical_slice`：代码和单元攻击门已闭合，production replay 与物理
晋升仍保持 fail closed。
