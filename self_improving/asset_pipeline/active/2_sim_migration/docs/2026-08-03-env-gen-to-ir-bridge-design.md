# env-gen → openxsim IR 桥接 · 设计 spec

> **日期**：2026-08-03 · **状态**：设计定稿（待实现） · **所属**：Phase 2 · 4.7 Transfer 的 env-gen 上游 importer
> **名词**：**env-gen** = `robot-harness-gen-env`（文字→物理已验证的 RoboTwin 场景）；**IR** = openxsim `EnvironmentPackage`（后端中立中间交换包）；**openxsim** = 跨模拟器迁移引擎（在 `shared/openxsim/`）。

## 1. 目标与范围

给 openxsim 增加一路**"env-gen → IR"的一等 importer**，让 env-gen 产出的场景能进入 openxsim 现成的 `transfer` 流水线，端到端跑通到目标后端。方案取 **B（直接写 importer）**——相比"翻译成 `sapien_scene.v1` 再复用"，B 全保真（关节/关系/来源不丢），代价仅多写一个与 `import_sapien_scene`（~68 行）同量级的文件。

**范围边界：**

| | 在本任务 ✅ | 不在本任务 ❌ |
|---|---|---|
| 场景语义 | `resolved_scene` → 合法 IR（位姿/资产/关节/关系/来源） | — |
| 任务缺口 | 复用 openxsim 的 unbound-task 占位 + 标记 | 合成可判定 task |
| 资产 | 把 SAPIEN 表示（OBJ/URDF）写进 IR | **OBJ/URDF → USD 转换**（资产管线 / asset reuse） |
| 后端 | 注册进 dispatcher，能被 `transfer` 消费 | 后端选型（Onboarding+团队）、Isaac 网格编译 |

**Definition of Done**：真实 env-gen 场景（如 can-on-plate）→ `import_env_gen` → `EnvironmentPackage.validate()` 通过 → 经 openxsim `transfer` **跑通到同源后端（RoboTwin/SAPIEN）**；去 Isaac/MuJoCo 对缺 USD 的物体**如实报 blocker、不崩、不静默降级**。

## 2. 数据流

```
env-gen resolved_scene.json
   │  ① import_env_gen（本任务新写）
   ▼
openxsim EnvironmentPackage (IR)   —— env / assets(带 SAPIEN 表示+articulation) / task(unbound)
   │  ② openxsim 现成 transfer / backends（不改）
   ▼
RoboTwin/SAPIEN（同源，顺）   |   Isaac/MuJoCo（缺 USD 的物体如实报 blocker）
```

## 3. 组件与落位

importer 放进 **openxsim 包内**（与 `import_sapien_scene`/`import_mjcf`/`import_metasim` 平级），**不放 `2_sim_migration/lib/`**——因为 `transfer` 靠 openxsim 内部 `import_environment` 分派器找 importer，放包内则 `transfer --source-backend env_gen` 直接可用、依赖方向干净。openxsim 是本项目要开发的引擎（非只读上游），改它属正常。

| 组件 | 位置 | 职责 |
|---|---|---|
| `import_env_gen(path) -> EnvironmentPackage`（新） | `openxsim/env_gen.py`（新文件） | 读 `resolved_scene.json`（**当纯 dict 解析，不 import scene_gen**）→ 映射 → 组装 IR → `validate()` |
| 分派钩子（改 1 处） | `openxsim/importers.py::import_environment` | 识别 env-gen（靠独有字段 `compiler_version`，或显式 `--source-backend env_gen`）→ 调 `import_env_gen` |
| 复用帮手 | `importers.py` | `_native_package` / `_task_from_contract` / `_valid_identifier` |
| 测试 + fixture | `openxsim/tests/test_env_gen_import.py` + 样例 `resolved_scene.json` | 单测 + 回归固定件 |
| 文档更新 | `2_sim_migration/README.md`、`docs/design.md` | 注明 importer 落 openxsim 内；`lib/` 改作 env-gen 专属胶水预留 |

**取舍**：不 import env-gen 的 `scene_gen`（当 dict 读，保持 openxsim 自洽、与现有 importer 风格一致）；核心就一个新文件 + 分派器一行，可独立测。

## 4. 字段映射（`resolved_scene` → IR）

两边**位姿同约定（米 + wxyz 四元数）**，主体直搬。

**① 包级 / 环境级**
| IR | ← env-gen |
|---|---|
| `package_id` / `env.name` | `scene_id` |
| `env.workspace_bounds_m`(6元组) | `workspace.x_bounds_m`+`y_bounds_m`+`table_height_m`（z_min=`table_height_m`，z_max=`table_height_m + 0.5`m 固定默认；实现时可改为按最高物体 AABB+余量推算） |
| `env.gravity_mps2` | 默认 `(0,0,-9.81)`（env-gen 不存重力=SAPIEN 默认） |
| `env.metadata` | `request`/`seed`/`compiler_version`/`source_scene_spec_sha256`/`asset_catalog_sha256` |
| `env.metadata["relations"]` | env-gen `relations`（on_top_of/inside/left_of…）整组带过来 |
| `source` / `target_backends` | `{mode: env_gen_import, …}` / 初始 `("robotwin",)` |

**② 每个物体 → `SceneObject`**
| IR | ← env-gen |
|---|---|
| `instance_id` / `asset_id` | `object_id` / `asset_id`(+`model_id` 保唯一) |
| `pose.position` / `pose.orientation_wxyz` | `pose.position_m` / `pose.orientation_wxyz`（直搬） |
| `static` / `scale` | `is_static` / `mesh_scale` |
| `metadata` | `category/color/material/support_relation/support_target/grounding_score` |

**③ 每个资产 → `AssetBundle`**
| IR | ← env-gen |
|---|---|
| `asset_id` / `category` | 同物体 / `category` |
| `representations` | 由 `source_files` 建：`load_type=urdf`→(format=urdf,uri=urdf路径)；`rigid`→(format=obj,uri=网格路径)，**backend=sapien**；`sha256`/`size` 有则填 |
| `articulation` | `articulation_joint_names/limits/qpos/state`（关节保真） |
| `physical` | `dimensions_m` 带上；**`mass`/`inertia`/`friction` 标 `{"status":"unknown"}`，不编造** |
| `source` / `tags` | `asset_provenance`+`source_files`+`derived_from…`（全血缘） / color、material |

**④ 任务（场景无任务）→ unbound 占位**
- 复用 `_task_from_contract(None)`：`success=[{type:unbound,requires_binding:True}]`，`limitations=["task_semantics_unbound"]`；
- `instruction ← request`（保住 prompt），`intent="env_gen_scene_import"`；`relations` 已在 `env.metadata`，留作将来合成 success 的料，**本任务不硬合成**。

**关键取舍**：①未知物理不编造（标 unknown）；②全血缘入 IR（可追溯）；③关节保真（B 相对 A 的核心收益）；④relations 先当数据带着。

## 5. 错误处理与边界

| 情形 | 处理 |
|---|---|
| `resolved_scene.json` 解析失败 | 抛 `EnvironmentImportError` |
| 缺必需字段 / IR 契约违反 | 返回前 `package.validate()`，违反即抛 `IRValidationError` |
| 资产文件(mesh/urdf)不存在 | **硬失败**抛错（同 `import_sapien_scene`）——缺资产=坏场景 |
| 无任务 | 非错误——记 `limitations=["task_semantics_unbound"]`（显式标记） |
| 缺 USD 去 Isaac | 非本 importer——openxsim 编译器如实报 `no existing USD` blocker |
| **id 数字开头**（`071_can`/`003_plate`） | IR 要求字母开头 → `_valid_identifier` 消毒（→`asset_071_can`），**object↔asset 引用同步消毒保持一致** |
| mass/inertia/friction 缺 | 标 `{"status":"unknown"}`，不编造 |

## 6. 测试与验收

1. **单元测试** `openxsim/tests/test_env_gen_import.py` + fixture（复现得到的 can-on-plate `resolved_scene.json`，另加一个关节场景 cabinet、一个 proxy 场景）：
   - `import_env_gen(fixture)` → `validate()` 通过；物体/资产数、位姿、**关节保真**、血缘、unbound-task(`instruction=request`)、未知标记 全断言；
   - 分派器 `import_environment(fixture)` 能识别并路由；
   - 缺资产→抛错；数字开头 id→消毒且引用一致；
   - **确定性**：同输入→同 `package.digest()`。
2. **端到端（DoD）**：真实 env-gen 场景 → `import_env_gen` → 经 openxsim `transfer` 跑到 RoboTwin；Isaac/MuJoCo → 如实报缺-USD blocker、不崩。
3. **回归固定件**：提交 fixture + 预期 digest + 一个失败案例（缺资产）。挂进 openxsim 现有 pytest（36 passed，之上加新的）。

## 7. 范围外 / 依赖

- **OBJ/URDF → USD 资产转换**：Isaac 网格编译的前提，属资产管线 / asset reuse（Hu 另一任务），不在本桥。
- **后端选型 / 第二适配器目标**：Onboarding + 团队待确认。
- **relations → 可判定 task 合成**：留将来（本任务只把 relations 当数据带入 IR）。
- **上游只读**：不改 `external/env-gen-github`；`resolved_scene.json` 当外部数据读。

## 8. 实测里程碑（2026-08-03）
- **env-gen → IR → Isaac 端到端跑通**：补上 `2_sim_migration/lib/usd_enrich.py`（接头：把资产管线转好的 USD 登记进 IR 的 isaacsim 表示）后，env-gen bottle 场景经 `import_env_gen` → `enrich_isaac_usd` → `IsaacSimCompiler` **干净编译出 `scene.usda`（status=compiled，无 missing-USD blocker，scene.usda 引用 bottle.usd）**。测试 `shared/openxsim/tests/test_env_gen_isaac_enrich_e2e.py`（+全量 49 passed）。
- **分工**：GLB→USD 转换是资产管线（1_asset_reuse，isaac-smoke/omni.kit.asset_converter）的活；本桥负责 IR + 用 enrich 登记 USD 表示。env-gen→RoboTwin 为原生直达、不经 IR（该 round-trip 从 DoD 移除）。
