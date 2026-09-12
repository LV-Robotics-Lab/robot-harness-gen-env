# 可迁移 Genesis 实验包

公共 seam：`export_package(store, *, scene_ref, output, provenance=None, reports=(), media=())`、
`load_package(root)`、`run_package(root, *, runtime_roots, output, denied_roots=(), timeout=600,
profile="baseline")`。包内 `run.py --runtime ROOT_MAPPING.json --output NEW_DIRECTORY
--deny-root OLD_DIRECTORY [--profile baseline|half_dt]` 无需安装 Harness 或读取原 CAS。

范围为已有 `harness.genesis_scene_spec.v1` 的单动态刚体、有限桌面、ground。导出保持 representation
完整 logical paths，包括 runner 读取的 `urdf/../physics.json`。包拥有 scene、assets、可选原始报告与
媒体、provenance、runtime 依赖说明及小型 Harness runner 源码闭包。导出不授予物理通过或发布资格；
asset scene 静态正确性仍由上游 compiler 负责。成员校验、URDF/OBJ/MTL引用校验只负责此处搬迁边界。

`environment.json` 是未签名的完整性索引，不是 qualification。成员拒绝路径穿越、symlink、损坏或
遗漏的资源引用。旧 CAS identities 可以存在于 provenance/scene 中；实际 runner 不解析这些身份，
只消费包内 scene 数值与资产。Genesis 1.3.3/Python 3.12/Mesa 等运行依赖从部署显式提供，不复制
8.5 GB runtime，也不在每次包执行时复验整个运行时内容清单。

执行复用当前 `run_genesis_scene_cpu_v2`，原 baseline 1000步/.004秒、half_dt 2000步/.002秒，
各自产生41张真实帧与视频；不修改原阈值。一次 profile 完成只证明 load/step/media，
`dual_dt_physical_profile=not_run`。上层可调用两次并独立消费轨迹进行物理评估。
`robot_policy_evaluated=false`、`data_collection_evaluated=false` 固定保留。

子进程复用 Landlock/网络限制，运行根只包含复制包、声明的外部依赖和新输出目录。`denied_roots`
与允许根有祖先/后代重叠时拒绝；限制生效后实际 listdir 原根须返回 PermissionError。
任务输出保留 job、stdout/stderr、result；子进程失败不会产生假的成功回执。不存在的 runtime 返回
`missing_runtime_dependency`，非固定 profile 返回 `unsupported_replay_profile`。

## 来源与小型真实验收

沿用 `9460a4b` 的 Harness 自有 `genesis_scene_cpu_backend.py`、`_genesis_cpu_child.py`、
`_genesis_scene_child.py` 和 `GENESIS_SCENE_ENVIRONMENT`，不引入 Gujie dirty 内容。
Gujie/Genesis 来源关系继续遵守 `GENESIS_SCENE_REPLAY_CONTRACT.md`。

2026-09-12 使用原 normal 的 scene `e543ef65e21d380fc5965ff8f8d4f7fb69e2e2408f7a946f4377624c11f98e00`
和该 representation 所需资产，导出6.1 MB包后复制新目录。固定导出源码字节也在包内成员索引。
真实目录 `/home/jingxiang/bingsheng/generic-portable-copy-nisz5oyp/`：

- `execution/result.json`：68.155秒，1000步，41帧/24 unique，Landlock ABI8。
- `execution/baseline/preview.mp4`、`simulation.mkv`、`frames/step-0000.png` 和 `trace.ndjson`
  是本次新生成输出。
- 原 built、CAS、集成树、开发树、用户主树五个目录读取全部被实际拒绝。
- 双dt物理检查未重做；该证据只授予复制后独立load/step/media。

三次启动失败现场依次为 `generic-portable-copy-fkuzidnn`（0.466s）、
`generic-portable-copy-7nmt0qk0`（0.466s）、`generic-portable-copy-nyqn5avq`（2.320s），均位于上述
父目录。前两次为stdlib路径未在启动环境映射，第三次为GLFW尝试非声明子进程。修复为复用已有完整
运行环境配方；均未完成场景load/step，不计成功。后续成员/引用检查和双profile选择改动通过离线测试，
未为这些改动重复昂贵回放；真正执行过的确切包内源码保留在成功目录，不能混称最终commit已重跑。

离线测试通过公共 seam 验证路径、symlink、损坏、缺依赖、非固定profile、搬迁后无CAS解析以及
外部URDF资源拒绝。原normal和repair证据未修改。
