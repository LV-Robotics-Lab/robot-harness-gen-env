# 通用 Genesis 实验管线实施记录

状态：实施中。进度事实源是 [`golden_e2e_progress`](../../self_improving/golden_e2e_progress/README.md)。
本页承接2026-09-12交接，不改变旧normal/repair记录；正式资格、历史债务、autoresearch与机器人接口后置。

## 唯一控制入口

用户通过 `python -m self_improving.harness.experiment_cli --deployment ... submit` 提交文本、
图片、视频或组合。CLI创建现有`LimitedImagePreviewController`的实验分支；
`ExperimentService`使用原GoldenRunHarness和SQLite journal，内部模型只提供结构化建议。
用户不用操作Codex或MCP；实验版本尚未将开发descriptor投影为旧qualified MCP工具。

## 逐阶段契约与源码

| 阶段 | 契约 | 主要代码 | 当前已证明范围 |
| --- | --- | --- | --- |
| 输入、共享意图 | [输入合同](../../self_improving/harness/EXPERIMENT_INPUTS_CONTRACT.md) | `experiment_inputs.py` | 文本/图片/完整视频解码组件；组合输入保留来源 |
| Codex解释、视觉、布局建议 | [模型合同](../../self_improving/harness/EXPERIMENT_MODEL_STAGES_CONTRACT.md) | `experiment_model_stages.py`, `codex_experiment_proposal.py` | 真实文本解释；视觉/修订待本轮真实案例 |
| 三来源资产 | [资产合同](../../self_improving/harness/EXPERIMENT_ASSETS_CONTRACT.md) | `experiment_assets.py` | local/web/generated真实组件；生成只限procedural proxy |
| 场景编译 | [编译合同](../../self_improving/harness/EXPERIMENT_SCENE_COMPILE_CONTRACT.md) | `experiment_scene_compile.py` | 完整顶点、相对资产引用、实测尺寸、XY/yaw改变scene bytes |
| 回放与物理验证 | [运行合同](../../self_improving/harness/EXPERIMENT_RUNTIME_CONTRACT.md) | `experiment_runtime.py` | 保留既有双dt判据；结果见下方案例 |
| 服务、证据链 | [服务合同](../../self_improving/harness/EXPERIMENT_SERVICE_CONTRACT.md) | `experiment_service.py`, `experiment_skill_policy.py` | 真实模型→资产→compile在同journal revision0→3 |
| 有界调度 | [CLI合同](../../self_improving/harness/EXPERIMENT_CLI_CONTRACT.md) | `experiment_controller.py`, `limited_image_preview_controller.py` | 14调度协议测试；两类真实修复待验 |
| 包与可迁移入口 | [包合同](../../self_improving/harness/EXPERIMENT_PACKAGE_CONTRACT.md) | `experiment_package.py`, `portable_genesis_package.py` | 历史鼠标包真实隔离copy-run；新通用包待验 |

新实验Skill使用`experiment.<stage>@1.0.0`开发命名空间。它复用同一执行内核，不替代旧正式
text2env/image2env/video2env资格；没有资格时不偷偷降级release。开发模式显式记录声明源码与部署，
资产和结果隔离正式库。运行完成、物理检查通过和发布资格分别报告。

## 当前真实案例

- 固定`50eb2ec`，文本请求“生成桌面红色block，XY=[0.1,-0.05]、yaw=20°、保留生成尺寸”，
  从Harness CLI启动；输入建议来自一次9.235秒的真实内部Codex回合。workflow
  `6c84535b-7c0f-467a-a182-2bcffabd3cde`已在同journal提交7阶段，34/34、visual和validate通过。
  journal起止跨度154.561299秒，不称精确CLI计时。父snapshot仍active，CLI阶段结果passed。
- 已导出132-member包并复制到`/home/jingxiang/bingsheng/generic-text-package-copy-omub915k/package`。
  新目录包内入口真实运行1000步/65.366秒，41帧/4unique，原包、两处源码、原state/CAS及assets
  全部read_denied。新末帧由主窗口查看；本次copy-run仅baseline，不冒称新双dt。
- 案例根目录：`/home/jingxiang/bingsheng/generic-experiment-20260913.ZGPNcg`。

场景修复A固定e2391bd，workflow`63968c2d-69d5-428d-80cd-7b156aa11790`。输入明确初始
0.22×0.22×0.16m块体、y=.375并允许物理失败后向内修正。原双dt均测得余量.015<.02m；
Codex实际建议并修订y=.369，尺寸不变，scene SHA改变，第二轮34/34与validate通过。
全命令308.774588622秒，12个stage commit；失败和成功一同留在
`/home/jingxiang/bingsheng/generic-experiment-v2-20260913.1QrGYD/scene-repair-package`。

## 已修复、仍受限

已保留攻击测试：开发receipt不能通过qualified reader；颜色意图不符不能仅改报告；
8线程并发登记同一资产必须复验后复用；联网Y-up资产必须实际旋转为Z-up；未实现cousin必须拒绝。
三来源是获取来源，exact/cousin是选择策略，目前cousin为`未实现`。

包内bytes完整性和图片不是物理通过。文本包独立copy/load/step已真实通过；图像、视频、联网
完整workflow与两类fallback尚待真实验证。后续包补充完整journal refs，不改旧包。
robot_policy_evaluated=false、data_collection_evaluated=false。Dashboard三入口持续404，
没有task id，因此只更新仓库账本，不伪造控制面同步。
