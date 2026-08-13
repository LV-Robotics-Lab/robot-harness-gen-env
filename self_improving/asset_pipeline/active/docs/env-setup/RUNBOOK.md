# RunBook — 复现 `robot-harness-gen-env`（LV Robotics Lab）

> 目标读者：想在 **lv-5090 工作站**（或类似 RTX 5090 / Blackwell Linux 机器）上，从零把公开仓库
> `github.com/LV-Robotics-Lab/robot-harness-gen-env` 的 **text → 场景 → 物理验证** 流程端到端跑通的人。
>
> "边复现边记录"，命令可直接照抄。已在 lv-5090 实测通过（2026-07-26，HEAD `60a2597`）。

---

## 0. 背景与结论速览

- 本仓库是更大 "Robot Harness" 的 `/gen-env` 子系统：把一句话（中/英）编译成**物理已验证、可复现（哈希绑定）的 RoboTwin 场景包**。
- **核心 pipeline 不依赖 LLM**（规则解析 + 求解器 + SAPIEN 物理验证）；仅可选的"渲染批判"用一个本地 VLM（`Qwen2.5-VL-3B`）。
- 复现两层：**逻辑层**（解析/求解/打包/静态验证，纯 Python，`pytest` 即验证）+ **物理层**（SAPIEN 回放，需 Linux+NVIDIA GPU+RoboTwin）。
- **一句话流程**：`text → SceneSpec → 资产落地 → 支撑/容纳求解 → ResolvedSceneSpec 包(哈希) → SAPIEN 回放 → 多重验证门`。

---

## 1. 前置条件（lv-5090 实况）

| 项 | 要求 / 实况 |
|---|---|
| OS | Ubuntu 24.04，内核 6.17 |
| GPU | RTX 5090 32GB（Blackwell），驱动 580.159.03 |
| CUDA | **必须 cu12.8+**（Blackwell 不兼容 RoboTwin 官方钉的 12.1）；本机 torch 2.11.0+cu128 已解决 |
| 仿真栈 | SAPIEN 3.0.0b1（在 `robotwin-5090` conda 环境；本 RunBook 从它克隆） |
| RoboTwin | `/home/jingxiang/workspace/alchedata-self-improving-agents/external/RoboTwin`（20G，assets/objects 4.4G/134 物体，git HEAD `c3ddfa8`） |
| VLM 缓存 | `Qwen/Qwen2.5-VL-3B-Instruct` 已在 `~/.cache/huggingface`（可选批判用，无需重下） |
| 磁盘 | 单分区，可用约 122G（91% 已用，注意别挤爆） |

> **踩坑 #1（最大）**：用 RoboTwin 官方安装（钉 CUDA 12.1）在 5090 上会 `torch.cuda.is_available()==False`（Blackwell 不兼容）。
> 规避：用 **cu128 的 torch + sapien 3.x**。直接复用 / 克隆已就绪的 `robotwin-5090` 环境即可。

---

## 2. 环境准备（隔离，避免破坏共享 `stage05`）

> **踩坑 #2（共享账号）**：`robotwin-5090` 里 `scene_gen` 是**可编辑安装**且指向 jingxiang 的 `stage05`。
> 若直接对新克隆 `pip install -e`，会把该 editable **重指到新目录，破坏 stage05**。
> 规避：**克隆专属 conda 环境**，只在其中安装。

```bash
export PATH=$HOME/miniconda3/bin:$PATH

# 代码
mkdir -p /home/jingxiang/yuxin && cd /home/jingxiang/yuxin
git clone https://github.com/LV-Robotics-Lab/robot-harness-gen-env env-gen-github

# 专属环境（从已验证的 robotwin-5090 克隆，继承 cu128/sapien3 栈；用硬链接，约几秒）
conda create -y --clone robotwin-5090 -n env-gen-yuxin
source activate env-gen-yuxin
python -c "import torch,sapien; print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), sapien.__version__)"
# 预期: 2.11.0+cu128 12.8 True 3.0.0b1
```

### 方案 A（本次实测走的路）：clone `robotwin-5090`
最快、最稳（继承已验证的 cu128/sapien3 栈），代价是依赖机器上已有那个环境；见上方命令。

### 方案 B（自包含 pip 环境，不依赖别人的 env）★推荐给外部读者
核心依赖**几乎全是普通 pip 包**，不必 clone 任何人的环境。分两层：

- **逻辑层（pytest/编译/静态验证）完全自包含，且 Python 3.11 即可、无需 GPU/RoboTwin：**
  ```bash
  python3.11 -m venv .venv && source .venv/bin/activate
  pip install -e '.[dev,demo]'          # 只拉 pydantic/pyyaml/pillow/flask 等
  python -m pytest -q                    # 62 passed
  ```
- **物理层（SAPIEN 回放）——⚠️ 无法做成"短 pip 清单式"自包含（已实测证明）：**
  从零建 3.11 环境跑物理回放，会**逐层撞出 RoboTwin 自己的依赖长尾**（实测顺序）：
  `imageio` → `open3d` → `h5py` → …最终撞上 **`curobo`（nvidia_curobo）硬墙**。
  原因：`run_scene_runtime.py` `import` 了 RoboTwin 的 `Base_Task`，而它**加载时无条件** `from .planner import CuroboPlanner`
  （`planner.py` 用 try/except 包着，curobo 缺失就不定义该名字 → 硬 `ImportError`）。**gen-env 并不用机器人规划器，却被强行拽进 curobo 这个重编译 CUDA 库。**
  → 结论：**物理层的依赖 = RoboTwin 的完整 env（含 curobo），不是一条短 pip 命令能搞定。**
- **物理层正确做法（二选一）**：
  1. **方案 A（推荐）**：clone 一个已装好的工作环境（如 `robotwin-5090`）——curobo/pytorch3d 都已 vendor 安装好。
  2. **RoboTwin 官方安装**：跑 `RoboTwin/script/_install.sh`（会 build curobo），**但要覆盖它的 torch/sapien**（见踩坑 #6）。
- **可复现 lock（本次跑通环境导出，可靠）**：`/home/jingxiang/yuxin/env-gen-yuxin.{environment.yml, requirements-lock.txt}`。

> **踩坑 #6（RoboTwin requirements 会毁掉 Blackwell）**：`RoboTwin/script/requirements.txt` 钉死 **`torch==2.4.1` + `sapien==3.0.0b1`**——这正是 CUDA 12.1 那套，**在 5090 上 `cuda.is_available()==False`**。用官方安装后**必须**把 torch/torchvision/sapien 覆盖成 `torch==2.11.0+cu128` / `sapien 3.0.x`（其余依赖如 open3d/h5py/gymnasium/curobo 保留）。

> **已验证 vs 未验证（诚实标注，2026-07-26 实测）**：
> - ✅ **逻辑层**从零 py3.11 自包含：`venv + pip install -e '.[dev,demo]'`（**无需** `--ignore-requires-python`）→ **62 passed**。
> - ✅ `torch 2.11+cu128` / `sapien 3.0.3 稳定版` 纯 pip 装成功、`cuda.is_available()==True`。
> - ⚠️ **sapien 3.0.3 稳定版能否端到端跑物理，未能验证**——被 curobo import 墙挡在真正执行 sapien 之前。要验证需先装好 curobo。

---

## 3. 阶段 1 — 逻辑层复现（无需 GPU/RoboTwin）

```bash
cd /home/jingxiang/yuxin/env-gen-github
pip install -e '.[dev,demo]' --ignore-requires-python      # 注意末尾 flag，见踩坑#3
python -m pytest -q
# 预期: 62 passed in <1s
python -c "import scene_gen, os; print(os.path.dirname(scene_gen.__file__))"
# 预期: /home/jingxiang/yuxin/env-gen-github/scene_gen  （确认指向新仓库，未串到 stage05）
```

> **踩坑 #3（Python 版本，仅方案 A 需要）**：`robotwin-5090` 是 **Python 3.10**（对齐 RoboTwin 官方），而仓库
> pyproject 要求 `>=3.11` → clone 后 `pip install -e` 报 `requires a different Python: 3.10.x not in '>=3.11'`。
> **代码实测在 3.10 完全能跑（62 测试全过）**，故加 `--ignore-requires-python` 绕过即可。
> **说明**：Blackwell 兼容只取决于 **cu128 的 wheel**，与 Python 3.10/3.11 无关（torch/sapien 都有 3.11 wheel）。
> 若走方案 B 从零建 **3.11** 环境，通常直接满足 `>=3.11`、连这个 flag 都不用（该路径尚未端到端实测，见方案 B 注意③）。

**验证点**：`pytest` 全绿 = 解析/求解/打包/静态验证逻辑复现成功（靠仓库自带 fixtures，不碰 RoboTwin）。

---

## 4. 阶段 2 — 建资产目录（扫 RoboTwin）

```bash
RT=/home/jingxiang/workspace/alchedata-self-improving-agents/external/RoboTwin
SRC=$(git -C "$RT" rev-parse HEAD 2>/dev/null || echo unknown-nongit)
mkdir -p data/scene_gen
python -m scene_gen.catalog \
  --robotwin-root "$RT" \
  --overrides scene_gen/asset_overrides.yml \
  --source-commit "$SRC" \
  --out data/scene_gen/asset_catalog.json \
  --missing-out data/scene_gen/missing_assets.json
# 预期: PASS entries=130 available=18 sha256=...   （missing=6）
```

**验证点**：产出 `asset_catalog.json`（130 条，18 available）+ `missing_assets.json`（6 条），并有 sha256。

---

## 5. 阶段 3 — 编译场景（静态，无物理）

```bash
python script/generate_scene.py \
  --prompt "Place a can on top of a plate." \
  --seed 42 \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --out-root data/generated_scenes
# 预期: PASS scene_id=place_a_can_on_top_of_a_plate_xxxx validation=incomplete
```

产出目录 `data/generated_scenes/<scene_id>/`：`request.txt` / `scene_spec.json` / `resolved_scene.json` /
`generated_scene.py` / `package_manifest.json` / `validation_report.json`。

> **说明（不是坑）**：静态验证结果是 `incomplete`（`fail=0, not_run=1`）是**正常**——`incomplete` 只表示"物理那一
> 项还没跑"（`generate_scene.py` 默认 `require_runtime=False`）。只要 `fail=0` 即静态全过。

**验证点**：`validation_report.json` 的 `fail_count=0`；`resolved_scene.json` 里物体已落地到真实资产
（本例 `can_1=071_can`, `plate_1=003_plate`）。

---

## 6. 阶段 4 — 物理回放（GPU + SAPIEN，无头）★关键步

```bash
REPO=/home/jingxiang/yuxin/env-gen-github
RT=/home/jingxiang/workspace/alchedata-self-improving-agents/external/RoboTwin
SCENE=place_a_can_on_top_of_a_plate_fe0b76e316     # 用阶段3打印的 scene_id
mkdir -p $REPO/data/runtime/$SCENE
cd $REPO
python script/run_scene_runtime.py \
  --robotwin-root "$RT" \
  --resolved-scene "$REPO/data/generated_scenes/$SCENE/resolved_scene.json" \
  --asset-catalog  "$REPO/data/scene_gen/asset_catalog.json" \
  --out-dir        "$REPO/data/runtime/$SCENE" \
  --settle-steps 900 --contact-window-steps 120 --video-frames 120 --fps 12
# 预期: PASS scene=... fail=0 video_frames=120
```

产出：`preview_head.png` / `preview_segmentation.png` / `preview_world_left|right.png` /
`observer_start|mid|end.png` / `observer_runtime.mp4` / `runtime_evidence.json` / `runtime_validation_report.json`。

> **踩坑 #4（路径）**：`run_scene_runtime.py` 会 `os.chdir(robotwin_root)`，所以 `--resolved-scene / --asset-catalog / --out-dir` **必须用绝对路径**，否则会写到 RoboTwin 目录里 / 找不到文件。
>
> **踩坑 #5（吓人但无害的日志）**：会刷一堆 `[svulkan2] [error] OIDN Error: invalid handle`（光追降噪器 OIDN 报错）——**不影响结果**，最终仍 `PASS`。另有 `missing pytorch3d`、Warp DeprecationWarning，均**非致命**、不阻断。

**验证点**：结尾 `PASS ... fail=0`；`runtime_evidence.json` 的 `status=pass`。

---

## 7. 阶段 5 — 全量验证（接入 runtime 证据）

```bash
python -m scene_gen.validator \
  --resolved-scene "data/generated_scenes/$SCENE/resolved_scene.json" \
  --asset-catalog  "data/scene_gen/asset_catalog.json" \
  --package-root   "data/generated_scenes/$SCENE" \
  --runtime-evidence "data/runtime/$SCENE/runtime_evidence.json" \
  --require-runtime \
  --out "data/runtime/$SCENE/validation_full.json"
# 预期: PASS fail=0 not_run=0
```

**验证点（端到端成功标志）**：`validation_full.json` 的 `status=pass, fail=0, not_run=0`。
本例物理指标：`can_1`/`plate_1` 均 settled、penetration=0、support_contact=True(frac=1.0)、可见像素 934/3843。

---

## 8. 阶段 6/7（可选，待续）

- **阶段 6 · prompt matrix（对齐 README 的 33/33 证据）**
  ```bash
  python script/run_prompt_matrix.py \
    --matrix tests/fixtures/prompt_matrix.json \
    --asset-catalog data/scene_gen/asset_catalog.json \
    --generated-objects-root "$RT/assets/objects" \
    --out-root data/prompt_matrix --report data/prompt_matrix/report.json \
    --runtime --robotwin-root "$RT"
  # 目标: 33/33 compile + runtime pass
  ```
- **阶段 7 · VLM 渲染批判（可选）**：`pip install -e '.[vlm]' --ignore-requires-python` 后跑
  `script/run_rendered_critic.py`，用已缓存的 `Qwen2.5-VL-3B`。

---

## 9. 验证清单（一眼判断是否成功）

| 阶段 | 命令看到 | 文件确认 |
|---|---|---|
| 1 | `62 passed` | `scene_gen.__file__` 指向新仓库 |
| 2 | `PASS entries=130` | `asset_catalog.json` 存在 |
| 3 | `PASS ... validation=incomplete` | `validation_report.json` `fail_count=0` |
| 4 | `PASS ... fail=0 video_frames=120` | `runtime_evidence.json` `status=pass` |
| 5 | `PASS fail=0 not_run=0` | `validation_full.json` `status=pass` |

---

## 10. 踩坑速查（TL;DR）

1. **Blackwell（5090）必须 cu128** —— 别用 RoboTwin 官方的 CUDA 12.1，否则 GPU 用不上。
2. **别污染共享环境** —— 克隆专属 conda env，别对共享环境 `pip install -e`（会重指 editable 破坏别人）。
3. **Python 3.10 vs 仓库要求 3.11**（仅方案 A/clone 路径）—— 加 `--ignore-requires-python`，代码在 3.10 实测能跑；Blackwell 只看 cu128 wheel，与 py 版本无关；方案 B 从零可直接用 3.11。
4. **物理回放用绝对路径** —— 脚本会 `chdir` 到 RoboTwin 根目录。
5. **OIDN / pytorch3d 报错无害** —— 只要结尾 `PASS fail=0` 即成功。
6. **ssh 走 sg-relay 易掉线** —— 长任务建议 `nohup ... &` detached + 轮询 log；ControlMaster 断了要在本地终端重连一次。
7. **磁盘 91%** —— 共享盘紧张，产出别堆太多；用完清 `data/runtime` 大文件。

---

## 附：本次实测环境指纹

- 仓库 HEAD：`60a2597 Use qualified settling window in demo`
- conda env：`env-gen-yuxin`（clone 自 `robotwin-5090`；Python 3.10.20 / torch 2.11.0+cu128 / sapien 3.0.0b1）
- RoboTwin HEAD：`c3ddfa8b97d5519efa828b075999bd0006778e5e`
- 资产目录 sha256：`15872390a857160867f88d4ae5296d418d9e34ce41ff4dad79b8fd9666c7fbbd`
- 端到端样例场景：`place_a_can_on_top_of_a_plate_fe0b76e316` → `validation_full.json: pass`
