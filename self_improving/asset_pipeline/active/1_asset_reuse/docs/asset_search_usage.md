# 资产搜索使用指南（asset acquire / 线 D）

> 一句话：告诉系统"我要什么物体"，它按 **本地库 → NVIDIA → GitHub → Objaverse → GitHub 发现**
> 五级信任梯度检索、AI 看图验货、物理质检，通过后自动入库并可被文字场景立刻选中。
> 本文是操作指南；原理与阶段划分见 `../OVERVIEW.md`。

## 用法一：网页（单个需求，零配置）

打开 **http://100.64.0.9:8811**（Headscale 网内）：

1. 输入英文 prompt（如 `Place a lamp on the table`）→ 运行；
2. catalog 里没有的类别会自动触发检索引进，页面顶部 7 步进度条实时显示到哪一步；
3. 检索决策（每个候选为什么选/为什么淘汰）在第 ② 步"缺口引进"面板；
4. 类别已存在但想强制走一遍引进：把类别名填进「演示缺口」输入框。

## 用法二：命令行清单（批量 / 带属性约束 / 钉文件 / 收本地文件）

### 第 1 步 · 写清单

从模板复制（两个模板都在 `configs/templates/`）：

```bash
cp configs/templates/acquire_request.example.json /tmp/my_request.json
```

最简条目只需要类别；字段全解见 `configs/README.md`，四种条目形态：

| 形态 | 写法 | 含义 |
|---|---|---|
| searched | `{"category": "lamp", "aliases": ["lamp","desk lamp"]}` | 让系统自动检索 |
| searched+约束 | 加 `"colors": ["white"]`, `"materials": ["metal"]`, `"size_policy": "absolute:0.25"` | 颜色/材质进检索词并作为 AI 看图验货的硬门 |
| pinned | 加 `"pinned": {"prefix": "Assets/Isaac/...", "usd": "025_mug.usd"}` | 钉死 NVIDIA 服务器上的具体文件，跳过检索 |
| local | 加 `"local": {"path": "/绝对路径/model.glb"}` | 验收你已下载好的本地文件，跳过检索和下载 |

一份清单可混写多种形态；条目数不限；**同一类别写多条无效**（第二条起判"本地已有"直接复用）。

### 第 2 步 · 执行

```bash
A=/home/yuhang/workspace/robot-harness-gen-env/self_improving/asset_pipeline/active
PY=/home/yuhang/miniconda3/envs/env-gen-yuxin/bin/python
cd $A/1_asset_reuse
PYTHONPATH="$A/1_asset_reuse:$A/shared/openxsim/source/agenticsim:/home/yuhang/workspace/robot-harness-gen-env" \
OMNI_KIT_ACCEPT_EULA=YES \
$PY scripts/1_search/acquire_batch.py \
    --categories /tmp/my_request.json \
    --providers configs/providers.json \
    --dev-root "$A" \
    --out "$A/results/_test/acquire_$(date +%m%d_%H%M)"
```

⚠️ `PYTHONPATH` 三段缺一不可（缺 openxsim 段会在 import 处直接报 `agenticsim` 找不到）。

### 第 3 步 · 看结果

| 看什么 | 在哪 |
|---|---|
| 每条的检索决策链 | `<out>/selection_evidence.json` —— 每类别一段：`status`（`imported` 成功 / `reused_local` 本地已有 / `exhausted` 搜遍没找到）、咨询过的 `tiers_consulted`、每个候选的 `verdict` 与拒绝码（如 `validation_failed:tilt` 物理不稳、`outranked` 被更优者压过） |
| 新资产本体 | `$A/data/asset_library/<新编号>/`（模型 + `ledger.json` 账本 + `snapshots/` 快照） |
| 质检截图 | `<out>/shots/` |
| 引进台账（自动追加，勿手改） | `$A/data/acquired_manifest.json` |
| 网页里浏览 | http://100.64.0.9:8811/#library 「最近引进」和各统计下钻 |

## 行为要点（跑之前该知道的）

- **真实入库**：`imported` 的资产会写进生产 `data/asset_library/` 并重建 catalog——这不是演练。
  纯演练走隔离冒烟：`bash scripts/run_smoke.sh`（产物全落 `results/_test/`，生产数据只读）。
- **每条最多引进 1 个资产**；单条失败自动换候选（≤4 次），本层耗尽会继续向更深的 tier 找。
- **AI 看图验货**（VLM 身份门）：候选缩略图会被逐一判"这真是 lamp 吗 / 是白色吗"，
  不符者淘汰；全淘汰则该层判 MISS 继续下探。
- **调检索行为**：`configs/providers.json`——各 tier 开关、每类候选数 top_k、
  下载大小上限、许可门开关。
- **exhausted 怎么办**：先看 evidence 里各候选拒绝码；常见解法 = 加 `aliases` 同义词、
  放宽/去掉颜色材质约束、或直接用 pinned/local 形态人工指定。

## 从 prompt 端看（web / scene_acquire 的关系）

网页那条链路 = 系统解析 prompt 后**自动生成一份纯 searched 清单**再走本文同一套流程，
所以两种用法的证据格式、门禁、产物完全一致。
