# Golden image2env exact-reuse candidate — 2026-09-11

固定实现：`c817ebf7353d62b7e6fd5df8a65575e93adaa374`。

## 实际执行

独立验收从固定提交创建隔离源码副本与 fresh CAS，通过真实
`SkillRegistry.evaluate_candidate` 连续执行两次 `image2env.compile@1.0.0` 候选。第一次 CAS 从 12 个
输入对象增至 21 个：一份 canonical compile input 与 8 份派生文档。第二次使用不同 run UUID，输出与
ArtifactRef 序列逐项相同，CAS 保持 21 个对象。

v2 receipt 的 `compile_input` SHA-256 为
`c71d1e207e66bb2a4217f25ad590eec99ac97f5506f73732cf08511d16bb5244`；package id 为
`059219fcf496e2f631474c6f07801aec6f54ed5acd7816923d6318b085080f62`；receipt SHA-256 为
`71167565b28fd90b30c7cbb4c57f4617112931d9215c88eface3397114ae7029`。递归得到的 21 个
ArtifactRef 与 RunState artifacts 精确相等，全部 CAS URI、bytes、文件名摘要和内容摘要一致。输入 PNG
实际解码为 3×2、RGB、单帧。

## 完整性校验

missing/ambiguous category、missing source、missing catalog、missing representation member 和 missing
qualification 六个独立用例全部拒绝，且调用前后 CAS 对象数不变。category 失败保留底层稳定原因
`HARN_X2ENV_EXACT_ASSET_NOT_FOUND` / `HARN_X2ENV_EXACT_ASSET_AMBIGUOUS`。

定向门禁为 99 pass；`image2env_compile.py` 与 `x2env_compile_closure.py` 合计 375 statements / 92
branches，全部覆盖；87 份 schema snapshot 通过 exporter。隔离 `.venv` 的完整根套件只有 packaging
节点因没有 pip 失败，精确结果为 4108 pass、20 skip、1 fail；同一 packaging 节点使用系统 Python为
2 pass。

独立原始 summary SHA-256：
`6e2191b5ee887925705b856cb68b8f3c851d69e09b5e6d7abf6d6c261efd52b5`。

## 边界

这是 controlled candidate，不是 production qualification。没有运行 Genesis 或 GPU，没有 replay、
validate、MCP image tool 或 robot policy。所有 runtime、collision、sim-ready 与 publishability 标志均为
false；固定 placement 只是 implementation-bound candidate default，不是图像或物理推断。
