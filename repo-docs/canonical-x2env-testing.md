# Canonical x2env 测试与验证手册

本页面向修改 Harness、接入 adapter 和复核交付的开发者。普通用户提交输入请先读
[用户指南](walkthroughs/canonical-x2env-user-guide.md)。测试分为可离线运行的公共接口测试、
源码与文档门、真实模型/Genesis 案例和复制包运行；它们回答不同问题，不能互相替代。
本文提供操作配方，不表示在本次文档更新中重新执行过昂贵仿真。

部署排错先运行 `python -m self_improving.harness.x2env.cli check --deployment /absolute/deployment.json`。
轻量检查不加载外部模型、不创建状态；`not_configured` 不表示该来源可执行。
检查契约测试为 `tests/self_improving/harness/x2env/test_preflight.py`，包含配置漂移及符号链接误报攻击。

## 安装测试环境

在仓库根、独立虚拟环境中执行。`dev` 包含 pytest、pytest-cov、ruff、markdown-it-py；
`demo` 为保留的 Flask 测试提供依赖，`platform` 为几何和规范化测试提供依赖。

```bash
python3.13 -m venv /absolute/new-x2env-test-env
/absolute/new-x2env-test-env/bin/python -m pip install -e '.[dev,demo,platform]'
export PYTHON_BIN=/absolute/new-x2env-test-env/bin/python
"$PYTHON_BIN" -m pip check
git rev-parse HEAD
git status --short
```

以上使用已有本机实验的 Python 3.13 版本作为配方；不是项目最低版本声明。
[pyproject.toml](../pyproject.toml) 声明 Python >=3.11，
[CI](../.github/workflows/ci.yml) 配置 Python 3.11、3.12 两组环境；
各环境仍须实际安装验证，尤其 `platform` 的 `scipy>=1.17,<2` 会受可用 Python/wheel 版本限制。
不能因为 CI YAML 列出版本就认定该版本最新依赖安装已通过。

运行前检查几何依赖实际可导入，例如 `"$PYTHON_BIN" -c 'import shapely, trimesh, scipy'`。
`pip check` 不能替代此项：未安装某个 extra 时，它不一定报告该 extra 缺失。
2026-09-13 文档复核所用旧虚拟环境缺少 `shapely`，根测试出现大量关联失败；
因此不要把历史环境路径当作依赖已齐全的保证。

迁移测试还断言旧 `self_improving/asset_pipeline/active/1_asset_reuse` 路径不存在。
旧 checkout 即使 `git status --short` 干净，也可能残留被忽略的 `.coverage`、`__pycache__`，
使该断言失败。用 `git status --short --ignored self_improving/asset_pipeline/active/1_asset_reuse`
只读确认；优先在完整的新 checkout 中验证，或由目录所有者确认后归档残留，勿直接清理未知数据。

可离线测试使用 committed fixture、临时目录和外部端口替身，不要求下载 Genesis 权重、
运行真实 Codex 或访问 RoboTwin 资产。真实 runtime 测试需要另行配置声明的环境。
测试源码依赖目录应完整；完整 checkout/CI 使用递归子模块，缺失时先查看
`git submodule status --recursive`，不要把依赖缺失当作业务断言失败。

## 单次核验的时限与证据

当前采用每次核验最多 20 分钟：执行预算 1170 秒，退出清理预留 30 秒。
超时保留本次日志、退出码和产物，标记 `timed_out`；在大幅代码修改、时间优化或新的用户指示前，
不得换输出目录原样重跑。多组测试可以分别受限运行，但不能把一个已超时任务任意拆成重试来绕过限制。

直接定向测试可以使用 GNU `timeout`。下面创建新的仓库外证据目录，保留真实退出码：

```bash
test_evidence=$(mktemp -d /var/tmp/x2env-targeted.XXXXXX)
timeout --signal=INT --kill-after=30s 1170s \
  "$PYTHON_BIN" -m pytest -q \
  tests/self_improving/harness/x2env/test_harness_lifecycle.py \
  tests/self_improving/harness/x2env/test_operation_recovery.py \
  --junitxml="$test_evidence/junit.xml" \
  >"$test_evidence/log.txt" 2>&1
test_exit=$?
printf '%s\n' "$test_exit" >"$test_evidence/exit-code.txt"
printf 'Evidence: %s; exit: %s\n' "$test_evidence" "$test_exit"
```

退出码 0 表示该命令通过，124/137 表示 timeout/强制清理路径，其余应读取 pytest 或工具错误。
如果 shell 已开启 `set -e`，请将命令放入显式处理非零退出的脚本后再记录退出码，避免错误提前退出。
不要只保存终端最后一行；测试条数、失败栈和环境/源码身份都影响解释。

## 选择最小相关测试

Canonical 测试位于 [tests/self_improving/harness/x2env](../tests/self_improving/harness/x2env)。
将上面的文件参数替换为对应文件即可，其余计时和证据收集保留。

- 输入和模型字段：`test_input.py`、`test_contracts.py`、`test_structured_schema.py`、`test_codex.py`。
- 生命周期和幂等：`test_harness_lifecycle.py`、`test_operation_recovery.py`、`test_pipeline.py`。
- 诊断与预算：`test_diagnosis.py`、`test_harness_repair_budget.py`、`test_repair_reservations.py`、`test_revision.py`。
- 本地/联网/重建：`test_local_catalog.py`、`test_yuxin_adapter.py`、`test_web_resolver.py`、
  `test_reconstruction_adapter.py`、`test_source_router.py`、`test_resolver.py`。
- 颜色资产修订：`test_local_color_execution.py`、`test_local_color_harness.py`、`test_asset_revision.py`。
- 编译/几何/回放/物理：`test_compile.py`、`test_measured_support.py`、`test_genesis_runtime.py`、
  `test_replay.py`、`test_observation.py`、`test_assessment.py`。
- CLI/交付/包隔离：`test_cli.py`、`test_deployment.py`、`test_delivery.py`、`test_package.py`、`test_failure_bundle.py`。

需要单个行为时使用 pytest 标准选择器，例如 `-k 'recovery'`，或
`文件路径::实际测试函数名`；先看文件内已有命名，不凭空假设测试名称。
增加 canonical 测试文件后必须同步下节的分类，否则 runner 明确拒绝
`canonical_test_classification_drift`。测试中模拟的模型/下载/渲染成功不算真实来源通过。

## 根测试、六组测试和 coverage merge

[统一脚本](../script/run_self_improving_tests.sh) 委托
[x2env_test_groups.py](../script/x2env_test_groups.py)，支持 `1` 至 `6`、`root`、`merge`、`all`。
`PYTHON_BIN` 控制 shell 入口解释器。当前脚本内部默认时限仍为 **1770+30 秒**，
CLI 没有 `--timeout` 参数；本轮 20 分钟要求不能靠传一个不存在的参数实现。

各组职责如下，精确文件清单以 `GROUPS`/`ACTIVE` 为准：

1. 请求/SceneIR/Capability/Skill 合同及 schema 快照。
2. managed Codex、设计/grounding、controller、恢复、修订与持久状态。
3. 资产来源、规范化、Registry、不可变修订、Yuxin 和重建 adapter。
4. Genesis 编译、运行时合同、实测支撑面、观测和物理评估。
5. CLI、部署、完成门、导出包、源码身份、publisher；同时检查读者文档和其远端链接。
6. 保留的非 canonical 根测试及 stage5、alchedata、agenticsim、asset reuse/web/openxsim 测试；
   同时运行 active lint 与平台 inventory。

`root` 在仓库根执行 `pytest -q`，收集配置 `testpaths=["tests"]` 下的测试；
它没有覆盖所有位于 `self_improving/**/tests` 的嵌套套件，因此不能取代第 6 组。
六组 coverage merge 不消费 root 测量，但 CI 将 root 当作独立必要 job。

下面沿用现有 runner 的 `run_bounded`、worker、receipt 和 merge，仅显式传入当前 20 分钟预算。
在仓库根运行，不建立第二个测试实现。`--worker` 是内部入口，由此配方在同一版本脚本内调用，
不要把它当作稳定对外 API。

```bash
export X2ENV_TEST_EVIDENCE=$(mktemp -d /var/tmp/x2env-groups.XXXXXX)
"$PYTHON_BIN" - <<'PY'
import os
import sys
from pathlib import Path
from script.x2env_test_groups import run_bounded, source_identity

root = Path.cwd().resolve()
output = Path(os.environ["X2ENV_TEST_EVIDENCE"]).resolve()
script = root / "script/x2env_test_groups.py"
failed = False
for group in ("1", "2", "3", "4", "5", "6", "root", "merge"):
    if group == "merge" and failed:
        print("merge not_run: a required test group did not pass")
        break
    receipt = run_bounded(
        [sys.executable, str(script), group, "--worker", "--output",
         str(output if group == "merge" else output / group)],
        output=output / group,
        cwd=root,
        seconds=1170,
        cleanup_seconds=30,
        identity=source_identity(root),
    )
    print(group, receipt["status"], receipt["wall_seconds"], flush=True)
    failed |= receipt["status"] != "passed"
print("Evidence:", output)
raise SystemExit(int(failed))
PY
```

该配方每个子目录必须尚不存在；一轮固定源码运行全部组，过程中不要编辑、提交或改变依赖。
要只运行一组，将 tuple 改为 `("2",)`；需要 merge 时应已具备同一根目录下 1–6 的完整成功收据。
不要重用旧测量拼成新提交通过。源码身份比较包含 HEAD、dirty、diff SHA 和选定源码内容 SHA，
即使只改文档，也可能使旧六组无法 merge 到当前源码。

每组输出 `log.txt`、`result.json`、`junit-*.xml`、隐藏 `.coverage.*`；第 5/6 组还有
`reader-docs.json`/`ruff.json`。merge 输出 `coverage.json`、`coverage.xml`、
`coverage-gate.json`、`.coverage.combined`。上述显式预算配方不生成 CLI 外层的 `pending-gates.json`；
它不参与 `merge_groups()` 的六组成功与测量核对。

覆盖率当前为 `report_only`：保留缺失语句、缺失分支及默认排除的明细，不要求精确 100%。
merge 仍拒绝任一组失败、缺收据/coverage/JUnit、源码漂移、缺分支测量、核心模块完全没有测量。
三个外部执行排除为 `adapters/reconstruction.py`、`adapters/yuxin.py`、`genesis_child.py`；
这是 coverage 核心完整性边界，不表示无需测试或真实运行证据。

CI 当前直接执行旧默认预算的统一脚本，每个 Python 版本分别上传六组/root 制品，再在 coverage job
合并该版本制品。本地 20 分钟操作政策与 CI 脚本默认应如实区分；本文未修改 CI 行为。

## Schema、文档与 lint 的快速核对

从仓库根使用同一解释器。必要时按前述 `timeout` 配方包住每条命令并保存独立日志：

```bash
"$PYTHON_BIN" script/export_x2env_schemas.py --check
"$PYTHON_BIN" script/check_reader_docs.py --root . --output /absolute/new-doc-check.json
"$PYTHON_BIN" -m ruff check self_improving/harness/x2env tests/self_improving/harness/x2env
git diff --check
```

`--check` 只核对生成快照。修改 Python 类型模型后，可先生成到新临时目录比较，确认后再运行
`script/export_x2env_schemas.py` 更新默认 committed schema 与 `api-fields.md`；不要手工改派生字段表。
读者文档默认核对本地链接及历史归档；完整第 5 组额外使用 `--check-remote`，
会访问文档引用的远端，网络不可达应保留具体失败，不把“未测”改为通过。
定向 ruff 只覆盖列出的目录，不能当作第 6 组全部 active lint 已通过。

## 真实四模态案例的运行与判定

当前已接受证据为 [matrix v2](../self_improving/golden_e2e_progress/qualification-matrix-v2.json)
的四个简单例：文本粉红鼠标、纯图像红方块、纯视频红方块、图片加蓝色文字覆写。
该历史窗口已结束；新的回归应生成新的运行记录，不能把新结果写进冻结旧窗口。
旧 12 例 v1 矩阵和其失败继续保留，不宣称 v2 四例证明全部复杂能力。

运行方法使用[用户指南的四条 submit](walkthroughs/canonical-x2env-user-guide.md)，
固定该次源码/部署/解释器、输入文件 SHA、原始 prompt、seed 和新幂等 key。
部署 `timeout_seconds` 显式设为 1170，外层进程保留至多 30 秒清理；顺序运行以免抢占仿真资源。
纯图像、纯视频不得暗添文本；`purpose` 是测试说明而非额外用户输入。

每次记录 workflow ID、最终 status、operation 序列、模型原始输出、源选择/许可证、资产版本、
SceneIR 修订、compile/replay/observe/validate 报告和总耗时。
成功必须有真实动态轨迹、连续视频、视觉意图结果与物理结果；失败记录停止阶段、机器错误、
人类可读原因、所需资源及部分产物的绝对路径。`status` 或 `package` 命令返回 0 只表明命令成功，
不能单凭退出码判定原 workflow 成功。

已发生的结果及可查看资产/图片/视频/日志路径见
[本轮 walkthrough](../self_improving/golden_e2e_progress/CANONICAL_MATRIX_V2_PUSH_AUDIT_20260913.md)
和 [机读结果](../self_improving/golden_e2e_progress/qualification-matrix-v2-results.json)。
当前四例真实通过来源为 local；web/reconstruction 未在该部署配置并实跑，digital cousin 未实现。
场景布局 fallback 的组件测试、真实颜色新版本 fallback 和通用重建不是同一份证明。

## 复制包的真实 load/step 检查

成功导出的 `environment/` 自带 `package_loader.py`、场景和必要资产。
必须从复制后的包运行 loader；直接运行源码目录的 `package_loader.py` 会把源码目录当包根。
准备声明的外部 Genesis 环境及纯 `runtime_roots` JSON，后者只包含
`interpreter`、`stdlib`、`distributions`、`native`、`genesis`，不是整个 deployment JSON。
可依据部署文件的 `genesis.runtime_roots` 配置导出这五项，不能自行填写不实路径。

```bash
copy_check=$(mktemp -d /var/tmp/x2env-copy-check.XXXXXX)
cp -a /absolute/successful-delivery/environment "$copy_check/package"
timeout --signal=INT --kill-after=30s 1170s \
  "$PYTHON_BIN" "$copy_check/package/package_loader.py" \
  --runtime /absolute/runtime-roots.json \
  --output "$copy_check/baseline" --profile baseline \
  --deny-root /absolute/original-workspace \
  --deny-root /absolute/original-state \
  --deny-root /absolute/original-assets
```

另一次核验用同一复制包、全新输出 `$copy_check/half_dt` 和 `--profile half_dt`，
保留相同原路径拒读规则。命令本身需像前述配方保存 stdout、stderr 和退出码。
loader CLI 当前每次内部执行默认超时 600 秒，支持 baseline、half_dt、load_step_smoke；
没有 `--timeout` CLI 参数。`load_step_smoke` 只证明有限加载步进，不能替代双 dt 物理门。

输出目录必须是新目录；声明 runtime/包/output 不能与 deny roots 重叠。
Linux 的实际 Landlock 和声明运行目录是隔离证明的一部分；缺隔离能力时不得仅删掉 deny roots 继续宣称
可迁移通过。当前 `_environment()` 还依赖具体 Linux 原生库及 Python 3.12 site-packages 布局，
不能承诺任意 Linux/macOS/Windows 或任意 Genesis 安装直接可用。

检查新 `process.json`、stdout/stderr、实际轨迹、frames、preview.mp4 以及返回 status。
两个 loader 命令通过后，还需消费这两份实际证据运行程序化物理评估；单一子进程正常退出不等于
全部接触、穿透、稳定性、媒体或视觉检查通过。公开 workflow 已负责该编排；独立 copy-run
使用 `assess_scene(scene, *, package_root, scene_ir_bytes, input_sha256, profiles, visual_status="not_run")`。
其中 `profiles` 必须同时有 `baseline`/`half_dt`，每份按 `ProfileEvidence` 提供绝对 `root`
及逐文件 `path/size_bytes/sha256`；SceneIR 使用包内原字节，不能重序列化改写或借用旧 replay 输出。
实现核对必需成员、实际 load/trace/媒体、来源绑定和物理阈值，详见
[assessment.py](../self_improving/harness/x2env/assessment.py)。
原包 manifest 保持不变，不回填 `sim_ready`、`release_qualified` 或 `copy_run` 字段制造资格。

已有 S01 copy-run 的 baseline/half_dt、新视频、26/26 物理检查与拒读路径见上述 walkthrough。
其独立复制视觉复评及 post-step reset 未验证；robot policy、数据采集仍未运行。

## 改动后应交付的测试说明

记录改动对应公共行为、执行的测试命令、固定提交/环境、通过/失败/超时条数、测量缺口和证据路径。
文档修改完成本地链接/schema 相关检查即可按实际需要扩大验证，不应为文档重跑仿真。
涉及 support、containment、loader 或 validator 的行为修改，除定向攻击测试，还需真实对应 runtime 回放；
稳定 `scene_gen`/RoboTwin 契约受影响时按仓库规则补 RoboTwin/SAPIEN 验证，Genesis 回放不能替代它。
测试和证据不足以支持的能力明确写 `not_run` 或具体阻断，并进入后续开发计划。
