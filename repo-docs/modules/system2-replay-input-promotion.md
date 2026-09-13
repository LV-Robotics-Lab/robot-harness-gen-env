# System 2 Replay-Input Package-Only CAS Promotion

`self_improving.system2_replay_input_promotion.System2ReplayInputPromoter.promote` 是把一份已准备好的
System 2 replay input 的 package closure 从 source CAS 复制到独立 replay CAS 的唯一 public execution
seam。它解决“目标 CAS 能否只靠自己的 package bytes 重物化 replay 输入”这一件事，不把 package 复制
写成 replay、validate 或发布资格。

## 一条成功路径

```text
System2CompileTurnResult + supplied PreparedSystem2Replay
  -> 在 source CAS 重跑 System2ReplayHandoff
  -> verified Prepared exact-match supplied Prepared
  -> materialize package + republish through source PackageStore
  -> 重验 canonical EnvironmentPackage、catalog 与 exact manifest
  -> 在独立 scratch 中形成稳定 staging
  -> destination 写 package、catalog、最后写 EnvironmentPackage JSON
  -> destination 重读 JSON/catalog + 按 manifest 重物化 package
  -> PromotedSystem2ReplayInput
```

重跑 handoff 意味着调用方不能只交一份外形正确的 `PreparedSystem2Replay`：compile result、history、trusted
receipt、终态、request provenance 和 package closure 会先在 source 重新交叉绑定，重建结果必须与 supplied
Prepared 完全相同。package 随后从 source 物化并重发；重发的 manifest ref、canonical environment bytes
与 catalog digest 都必须保持原值。全部 source 检查与 staging 完成前，destination 没有首次写入。

## 七个 logical refs 与 commit-last

destination 复制的精确集合是：canonical `EnvironmentPackage` JSON、asset catalog、package manifest，
以及 manifest 的四个 members：`request.txt`、`scene_spec.json`、`resolved_scene.json`、
`generated_scene.py`。

package 和 catalog 先写，environment-package JSON 最后写。destination 返回的每个 ref 都要与 source
exact-match；写后还会重读 environment/catalog、重建 typed replay input，并从 destination 再物化 package。
所以成功返回后即使 source CAS 不再可用，目标 package closure 仍完整。

重复推广相同输入是幂等的，CAS 内容只做 additive 写入。若中途失败，已经写入的 immutable prefix 可以保留，
但调用方得不到成功结果或部分 authority。模块不回滚这些对象，因为按 digest 共享的 CAS object 可能也被其他
ref 使用。

## Root 身份与文件系统边界

promoter 在构造时固定 source/destination canonical roots，并建立只使用这些 roots 的私有 stores；caller
持有的 store handle 会在各阶段边界复核。观察到 root 漂移、source/destination overlap，或与两者重叠、
含 symlink component 的 scratch root 时都会 fail closed。

这里信任 same-host local filesystem。实现没有跨整个操作持有 directory fd 或 filesystem lease，因此 active
same-UID actor 仍可能在一次 check 与下一次 path use 之间并发替换路径；阶段性 root 复核不声称消除这个
TOCTOU 边界。

## Package-only 边界

目标 CAS 不会得到 request provenance、compile receipt、history authority、qualification、完整 authority
closure 或 runtime assets。catalog locator 仍须由实际 replay 的 allowed asset roots 约束，因此七个 refs
齐全不等于 runtime-asset portability。

这个 seam 不执行 `ReplayApplication`、simulator 或 validate，也不产生 physical pass 或 `publishable`。
未来 validate v2 仍需要正式 provenance 与 portable receipt closure。

精确源码摘要、47 项定向测试、169 statements / 36 branches 的 100% coverage、77 项 System 2 邻接回归、
89 项 CAS/PackageStore/replay-dependency 回归与攻击边界见
[`docs/evidence/system2-replay-input-promotion-20260902.md`](../../docs/evidence/system2-replay-input-promotion-20260902.md)。

证据状态：2026-09-02 已验证 trusted same-host filesystem 上的 package-only local CAS promotion；replay
执行、runtime assets、validate、完整 portable authority 与发布资格仍未由本切片验证。
