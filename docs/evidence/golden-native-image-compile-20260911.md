# 首条 native image exact-reuse compile 实跑

本次实际消费 Gujie 同一 mouse 的历史仿真 overview 图像、人工固定 proposal、seed 17、
已签发资产资格的可信 SQLite 与完整 CAS，生成有限桌面 Genesis scene。输入类型明确为
simulated-image exact reuse，不代表照片重建、fresh generation 或模型视觉理解。
top / side 两个视角请求已准备，本记录中尚未执行。

新公共入口 `Image2EnvGenesisCompiler.compile(request_ref)` 与 `read(receipt_ref)` 使用版本化
native request/receipt。旧 controlled application 和 v2 输入语义未改写；compile 不拥有新的
工作流状态库，资格来自既有资产应用的 qualified 终态。显式有限桌面与完整几何取代旧固定高度。
实现身份固定源码集合，不包含会继续更新的 provenance 账本。

运行根：`/home/jingxiang/bingsheng/golden-native-image-compile-20260911`。

| 样本 | 结果 |
| --- | --- |
| red.log | 真实资格读取后因 compile 未实现失败，1 failed / 17.69 s |
| green-attempt1.log | 同一真实输入 3 passed / 35.46 s |
| focused.log | 输入、回执及场景漂移等 12 passed / 145.81 s |
| 后续纯 schema 反例 | 6 passed；与前述覆盖合并 |

`compile-result.json` SHA256：
`3a735e2168ac6c6f4203eb6f603b7a43b63ef405cf5b20aed3c2ecda0d87874d`。

- compile receipt：`2777c9a2cad308a997c749d6c2945a45e910ca72192d6fb40516e2edbaf1d988`，6,114 bytes。
- scene：`e543ef65e21d380fc5965ff8f8d4f7fb69e2e2408f7a946f4377624c11f98e00`，898,939 bytes。
- focused.log：`c274cddb0687c3af40deff28ae6fc5d14f806f272a5a3099d45cb7a4b2cd34d4`。
- coverage.json：`cbecedc0abb38f16525ecaec67e7a424f5cd412404d330ef3cc8e1521bc1bd5d`。

schema 的 51 statements / 10 branches 为 100%；compile core 的 77 statements / 22 branches
合并覆盖为 98%。源码在计算身份后、发布其 CAS 期间发生变化的防御拒绝分支尚未覆盖，不能宣称
新 core 全部 100%。未通过 mock Harness 核心或调用 private helper 填充覆盖。

结果权限为 `static_scene_candidate`。与之前实跑相同的 scene 内容仅说明确定性，不能把旧 trace
挂到这份新 compile 回执后冒充本次执行。后续必须从此回执发起 fresh scene replay，并完成生产
Skill 资格、持久应用、观察和验证；本记录不证明 M1 已完成或 Registry/MCP 已注册。
