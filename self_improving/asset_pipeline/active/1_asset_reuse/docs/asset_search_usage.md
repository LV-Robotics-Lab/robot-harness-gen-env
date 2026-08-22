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

**输出逻辑**（一次批量运行的收尾顺序）：

1. 逐条处理产出一条**决策记录**（单条抛异常也会被记成 `entry_error`，不影响其余条目）；
2. 只要本批**有 ≥1 条成功引进**，就触发一次全局重建：从资产池**全部** ledger 重新派生
   overrides（`<out>/overrides_ext_all.yml`，不是只合并本批的——账本是唯一真源），再调
   s9 重建影子根 + 扩展 catalog（此后新资产就能被文字场景选中）；
3. 全部决策记录连同**providers 配置快照**和**输入清单回显**写入
   `<out>/selection_evidence.json`（可复现：当时用什么源、什么参数、请求了什么，全部冻结在证据里）；
4. stdout 逐条打 `MATCH <类别> grade=…` 与 `PASS/FAIL <类别> status=…`，最后一行
   `SUMMARY PASS|FAIL imported=N exact=A similar=B none=C`；**退出码**：每条拿到
   可复用资产（exact/similar）才返回 0，出现任一 none/entry_error 返回 1。

**匹配三档（每条请求必有 `match` 块）**：

| grade | 含义 | `asset` 字段 |
|---|---|---|
| `exact` | 找到资产：类别命中且你声明的**全部**属性（颜色/材质）被确认满足；未声明属性=类别命中即 exact | 可直接复用的资产本体（编号/路径/账本/已知属性） |
| `similar` | 找到相似资产：类别对但至少一项属性 `mismatch`（明确不符）或 `unverified`（库内未标注、无法确认），差距逐条列在 `unmet` | 最接近的资产本体——**照样返回给你复用**，将就与否你定 |
| `none` | 完全没找到：本地与全部外部梯队均无类别级命中 | `null` |

行为要点：带属性约束的请求会**先全梯队找 exact**（本地同类属性不符不再当场复用——
按旧行为"库里有球就算找到"会把蓝球请求错报成功）；exact 落空才落 similar：
优先本地同类（零成本），其次把「类别对、属性不符」的最优外部候选走完整引进链
兜底（条目级开关 `"allow_similar": false` 可关掉，严格模式宁可 none）。
`match` 还带 `candidates[]`——**所有**同类候选按匹配度排序的完整列表（不只 top-1），
每个带 `attr_score`（属性满足度 0–1）与 `visual_sim`（CLIP 文本-图像相似分，
探针标定 AUC 0.949；条目级 `similar_min_visual` 门限默认 0.18，属性全符者豁免；
缺缩略图的资产分记 null 不误杀）。blocker 的 `nearest_local` 同样带 `candidates[]`。
跨来源同类会**另开资产位**（如 NVIDIA 的 330_ball 与 web 引进的 366_ball 并存——
资产级 profile 承诺不同，不互相追加模型）。
stdout 逐条打 `MATCH <类别> grade=… asset=…` 可直接 grep；退出码：
exact/similar 都算成功（拿到了可复用资产），仅 none/entry_error 计失败。

**selection_evidence.json 结构**：

| 字段 | 含义 |
|---|---|
| `run_id` / `providers_snapshot` / `categories_input` | 本次运行名 / 当时的检索源完整配置 / 你提交的清单原文 |
| `categories[]` | 每条请求一段决策记录，字段如下 |
| ├ `query` | 实际检索的类别与别名 |
| ├ `entry_mode` | `searched` / `pinned` / `local` / `error` |
| ├ `status` | **四种结局**：`imported` 引进成功 · `reused_local` 本地已有直接复用 · `exhausted` 搜遍所有层没有合格候选 · `entry_error` 该条目本身出错 |
| ├ `tiers_consulted` | 实际问过哪几层（如 `[0,1,2,3]`＝一路搜到 Objaverse） |
| ├ `attempts` | 实际尝试导入的候选数 |
| ├ `candidates[]` | 每个候选的 `verdict` 与拒绝码：`selected` 选中 / `outranked` 被更优者压过 / `validation_failed:<门>`（如 `:tilt` 物理不稳）/ 许可、超大小、VLM 判非同类等 |
| └ `selected` | 胜出候选的来源、URL、许可证、落到的资产编号 |

**过程产物**（都在 `--out` 下，是证据不是最终品，可整目录删）：

| 目录/文件 | 是什么 |
|---|---|
| `webcache/<hash>/` | 下载缓存 + 来源溯源（provenance.json） |
| `staging_<资产>_m<N>/` | 转换暂存区 + staging_manifest.json（两阶段交接单） |
| `physcheck/` | 物理质检用的碰撞/视觉网格 |
| `shots/` | SAPIEN 质检截图（web 第④步展示的就是它） |
| `bundles/` `fragments/` `rows/` | 逐模型的账本条目、catalog 片段、导入行记录 |
| `import_matrix.json` | 逐模型 通过/淘汰 总表 |
| `overrides_ext_all.yml` | 全池派生的 catalog 重建输入（有引进才生成） |

**最终品（副作用，不在 --out 里）**：`data/asset_library/<新编号>/`（模型 + ledger.json +
snapshots）、`data/acquired_manifest.json` 追加一条、`data/scene_gen_ext/` catalog 重建、
`data/robotwin_shadow/` 影子根更新。网页 http://100.64.0.9:8811/#library 的
「最近引进」与统计会随之变化。

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
