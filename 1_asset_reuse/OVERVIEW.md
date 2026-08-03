# OVERVIEW — 资产复用管线（asset_spike）

## 1. 这是什么

**让 RoboTwin 和 Isaac/USD 生态两边的资产可以互通，并且都能被 env-gen 的
"文字 → 场景 → 物理验证"全流程复用的一条资产管线**，当前为打样规模，三条线均已端到端验证：

- **线 A（正向转换）**：RoboTwin 资产（GLB/URDF）→ Isaac Sim 可直接加载的 USD；
- **线 B（反向引入）**：NVIDIA 资产服务器上的 USD 物体 → RoboTwin 布局的 GLB 资产；
- **线 C（全流程复用）**：外部资产注册进 env-gen 的 asset catalog，被文字请求的
  grounding 真实选中、通过 SAPIEN 物理回放与全量验证。

打样样本：001_bottle（刚体）、036_cabinet（关节体）、YCB 025_mug（外部引入，红色马克杯，
注册为 `301_cup`）。批量线已按清单导入 NVIDIA 资产 25 个模型：**17 通过入库**（9 个资产
类目，catalog available 18→27），8 个带原因淘汰（见 `import_matrix.json`）。所有转换以
"双后端物理验证 + 哈希账本"证明没走样。

## 2. 整体 输入 → 输出

```
输入（只读）                                   输出
├─ RoboTwin 资产库（134 物体，jingxiang 处）    ├─ AssetBundle 账本 JSON（每资产一份，双后端表示+哈希+结构化未知）
├─ NVIDIA Isaac 资产服务器（HTTP，逐 prop 拉）  ├─ 线A: bottle.usd / cabinet.usd（Isaac 可加载）
├─ openxsim IR 库（AssetBundle 数据结构）       ├─ 线B: data/asset_library/301_cup/（RoboTwin 布局 GLB 资产）
└─ env-gen 上游（catalog/场景编译/验证器）      ├─ 线C: data/robotwin_shadow/（影子根）+ 扩展 catalog（131 条）
                                               └─ 双后端验证报告 / e2e 场景包 / 回放视频截图
```

**成功标志**：线 A = openxsim `representation_for("isaacsim")` 命中（Transfer 阻塞消除）；
线 B = 外部资产在 SAPIEN 静置验证 pass；线 C = 文字 prompt 生成的场景 `resolved_scene.json`
中 asset_id 为外部资产，且全量验证 `fail=0 not_run=0`。

## 3. 阶段总览

全部代码在 `work/asset_spike/`（试验夹，不入 git）；总入口 `run_smoke.sh`（11 步）：

| 线 | 功能阶段 | 脚本 |
|---|---|---|
| A | ① 资产读取登记（RoboTwin→账本） | `robotwin_asset.py` |
| A | ② 环境自检（一次性） | `s0_verify_isaac.py` |
| A | ③ 刚体 GLB→USD + 物理装配 | `s1_convert_rigid.py` |
| A | ④ 关节体 URDF→USD + 关节核对 | `s2_convert_articulated.py` |
| A | ⑤ 双后端静置验证 | `s3_validate_sapien.py` `s4_validate_isaac.py` |
| A | ⑥ IR 注册 + 内容判定 | `s5_check_ir.py` `s6_verdict.py` |
| B | ⑦ 探测（反向转换可行性+选品，一次性） | `s7_probe_reverse.py` |
| B | ⑧ 拉取源 USD + 反向转换 GLB | `s8a_fetch_convert_usd.py` |
| B | ⑨ 物化 RoboTwin 布局 + SAPIEN 验证 + 账本 | `s8b_materialize_validate.py` |
| C | ⑩ 影子根 + 扩展 overrides + 上游 catalog 扫描 | `s9_build_shadow_root.py` |
| C | ⑪ e2e：文字→场景选中外部资产→回放→全量验证 | `s10_e2e_scene.sh` |
| B批量 | ⑫ 清单驱动批量拉取+转换（一次 App 会话转全部） | `import_fetch_convert.py` + `configs/external_manifest.json` |
| B批量 | ⑬ 批量物化+逐模型验证（进程隔离防原生崩溃；淘汰模型物理隔离出资产池） | `import_materialize.py` |
| 防护 | ⑭ 惯例继承+尺寸策略（类别语义抄先例、几何语义按规范化 kind） | `lib/conventions.py` |
| 防护 | ⑮ 目录准入（视图层单资产编译检查，池层永不拒收） | `s14_catalog_admission.py` |

```
线A: RoboTwin GLB/URDF ──转换──> USD ──双后端验证──> 账本(isaacsim 表示) ──> Transfer 可消费
线B: NVIDIA USD ──反向转换──> RoboTwin 布局 GLB ──SAPIEN 验证──> 账本 + data/asset_library
线C: asset_library ──影子根注入──> 扩展 catalog ──grounding──> 场景 ──SAPIEN 回放──> 全量验证 PASS
```

## 4. 分阶段详解

（线 A 各阶段说明见前六阶段，与此前一致：读取登记生成账本、自校准缩放装配、URDF 规范化
副本导入、贴地静置双后端验证、`representation_for` 命中检查、按产物内容总判定。参数详情
见各脚本 argparse 与同目录 `README.md`。以下详解新增的 B/C 线。）

### ⑧ 拉取与反向转换 — `s8a_fetch_convert_usd.py`（isaac-smoke）

**输入→输出**：NVIDIA 资产服务器 URL → 源镜像 `data/asset_library/_source/ycb_025_mug/`
（USD + 贴图 + SOURCE_MANIFEST.json 哈希清单 + 源 upAxis 元数据）+ `mug_visual.glb`。
用 S3 列举 API 精确拉取单个 prop（几 MB，非几十 G 资产包）；asset_converter 反向导出
GLB（探测确认 GLB/GLTF/OBJ 三格式均支持）。

| 参数 | 含义 |
|---|---|
| `--source-dir` | 源镜像目录（含哈希清单） |
| `--out` | GLB 输出目录 |

### ⑨ 物化与验证 — `s8b_materialize_validate.py`（env-gen-yuxin）

**输入→输出**：GLB + 源清单 → `data/asset_library/301_cup/{visual,collision,model_data0.json}`
+ SAPIEN 验证报告 + AssetBundle。三个规范化步骤把任意来源网格对齐 RoboTwin 惯例：
① 按源 USD 声明的 upAxis 决定 Z-up→Y-up 旋转（不用启发式猜）；② 原点平移到底部中心
（配合 z_policy origin_on_table）；③ 实测包围盒写入 model_data（extents/scale=1，接触点
等标结构化空值）。碰撞暂用视觉网格副本（加载时凸分解；容器类任务需 CoACD 精化，账本已注明）。

### ⑩ 影子根与扩展 catalog — `s9_build_shadow_root.py`（env-gen-yuxin）

**输入→输出**：真 RoboTwin + `data/asset_library/` → `data/robotwin_shadow/`（symlink 全镜像
+ 注入外部资产；上游零写入）+ `data/scene_gen_ext/{asset_overrides_ext.yml, asset_catalog.json}`。
扩展 overrides = 上游文件全文 + 外部资产段（category/aliases/colors/**stable_orientation_wxyz**
——Y-up 网格立正的关键字段 + z_policy/footprint）。catalog 由**上游扫描器原样**跑在影子根上
（131 条 = 130 RoboTwin + 1 外部），保证条目结构与上游完全一致。

### ⑪ 端到端验收 — `s10_e2e_scene.sh`（env-gen-yuxin）

**输入→输出**：prompt "Place a red mug on the table." + 扩展 catalog → 场景包 + 回放证据
+ `validation_full`。四个连续判定：场景编译 PASS → `resolved_scene.json` 中 cup_1 的
asset_id **必须是 301_cup**（红色限定词让外部资产以 108 分胜过 RoboTwin cup 的 103 分，
grounding 打分确定性可复算）→ 回放 `fail=0` → 全量验证 `fail=0 not_run=0`。

**一键运行**：`export OMNI_KIT_ACCEPT_EULA=YES && bash run_smoke.sh`（步骤 ②⑦为一次性，
不在其中）。产物落 `results/_test/20260802_smoke_bottle_cabinet_glb2usd/`（线 A）与
`results/_test/20260803_smoke_usd2envgen/`（线 B/C）。

## 5. 关键概念 / 术语

- **AssetBundle / representation**：账本结构——一个资产多个后端表示（sapien=GLB、
  isaacsim=USD），各后端按格式取用；两方向转换都往同一结构里填。
- **影子根（shadow root）**：symlink 出的 RoboTwin 目录镜像 + 注入的外部资产，让"按名字+
  目录约定加载"的上游运行时无感知地看到扩展资产池；真目录零改动。
- **自校准缩放**：实测转换产物包围盒对齐目标米制尺寸，不假设转换器单位约定。
- **规范化**：外部网格对齐 RoboTwin 惯例（Y-up、原点=底部中心、model_data 布局），由源
  USD 元数据驱动，非启发式。
- **stable_orientation_wxyz**：catalog 中"网格坐标→世界直立姿态"的四元数；Y-up 网格为
  X+90（[0.7071, 0.7071, 0, 0]），求解器以 yaw∘此值合成最终朝向——缺它资产会躺着生成。
- **结构化未知**：查不到的参数（质量/许可证/接触点）记 unknown+运行默认，不编造。
- **按内容判定**：成败以验证 JSON 与账本注册为准（Kit 会吞退出码）。

## 6. 依赖 / 来历

| 依赖 | 性质 | 位置 |
|---|---|---|
| RoboTwin 资产库 + envs 运行时 | **外部只读**（影子根引用） | `/home/jingxiang/workspace/.../external/RoboTwin` |
| NVIDIA Isaac Assets 5.1（YCB 等） | 外部 HTTP 源，逐文件哈希入账；许可证标 unknown 待查 | `omniverse-content-production.s3-us-west-2.amazonaws.com` |
| env-gen 上游（catalog/编译/验证器） | **外部只读**，全部经 CLI 参数消费 | `env-gen-dev/external/env-gen-github` |
| openxsim（AssetBundle IR） | 项目内 vendored | `env-gen-dev/shared/openxsim/` |
| Isaac Sim 5.1.0.0（pip 版） | 双向转换 + Isaac 侧验证 | conda `isaac-smoke`（py3.11） |
| SAPIEN 3.0.0b1 + trimesh | SAPIEN 侧验证与网格处理 | conda `env-gen-yuxin`（py3.10） |

任务上下文：Phase 2 · 4.5 Asset reuse；线 A 产物同时为 4.7 Transfer 解除资产侧阻塞。
局部细节见同目录 `README.md`。
