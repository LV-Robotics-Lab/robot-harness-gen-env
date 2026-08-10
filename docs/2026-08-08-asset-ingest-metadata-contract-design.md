# 资产入库 metadata 契约（asset_ledger.v1）· 设计 spec

> **日期**：2026-08-08（r2，含外部评审修订） · **状态**：设计定稿（待实现） · **所属**：1_asset_reuse 入库管线（写方）+ 2_sim_migration / openxsim（读方），两阶段共用
> **一句话**：以资产账本为**唯一权威入库记录**（账本即契约），收编散在 `external_overrides_fragment.yml` / 验证报告里的元数据，新增 `schema_version` 与 `verification` 组；fragment 降级为账本的**派生视图**（generator 自动生成）。
> **名词**：**账本（ledger）**＝每资产一份的 `ledger.json`（含 `models[]`）；**fragment**＝`external_overrides_fragment.yml`（s9 构建扩展 catalog 的输入）；**池层/视图层**＝资产池只进不出，catalog 是池上的可逆过滤视图。
> **r2 修订记录**（2026-08-08 外部评审：ACDC 论文对照 + 2025-2026 文献扫描 + 对抗评审）：①verification 语义修复（verified_digest/run_id/latest 读取语义）；②账本纳入 git + 并发锁；③物性 status 加 estimated 档 + runtime_default_basis；④账本粒度改每资产一份含 models[]（owner 决定）；⑤stable pose 改列表形态（owner 决定）；⑥入库顺手渲快照（owner 决定）；⑦license gate 开关机制（owner 决定 B+）。方向性外部佐证：NVIDIA SimReady Foundation（Requirement→Capability→Profile+CI 验证器）是"账本即契约"的工业同构物。

## 1. 目标与范围

**问题**：同一资产的元数据摊在 4 处（账本 JSON、fragment、`model_data0.json`、`import_matrix.json`），无 schema 版本、无统一 validator；双后端验证结果躺在 `results/` 不回填账本，迁移时无法程序化判断「该资产在目标后端验过没有」。

| 在本任务 ✅ | 不在本任务 ❌ |
|---|---|
| `asset_ledger.v1` schema 定义 + validator（入库门禁末尾） | 上游 / s9 输入接口改动（fragment 格式不变，只改生成方式） |
| fragment generator（账本 → YAML，替代手拼）含 license-gate 开关 | s14 准入语义改动（仍视图层、可逆） |
| 验证状态回填账本（`verification[]`，含内容锚定与并发锁） | license 实际核查（**待议**，见 §8；本任务只保证「unknown 必须显式」+ gate 机制就位） |
| backfill 脚本：现存入库资产聚合升 v1 | 线 D 语义检索增强（embedding 等） |
| openxsim 侧拆包适配 `to_ir_bundles`（在我方 lib，openxsim 本体零改动） | openxsim IR 结构改动 |

## 2. 字段确定方法（判据；将来加字段也按此）

metadata 不是「描述资产的一切属性」，而是**下游消费者输入合同的并集**。每个字段过三个测试：

1. **有真实读者**——代码里哪一行读它；没人读不进 schema。
2. **缺失有明确失败模式**——最好能被门禁检测（先例：缺 stable orientation → 资产躺着生成；缺 isaacsim 表示 → transfer 报 blocker）。
3. **能自动获得，或显式结构化 unknown**——`{"value": null, "status": "unknown", ...}`，不编造。

新字段提案必答三问：**谁读它、缺了怎么失败、拿不到怎么办**。答不上不进 schema。

**第四问（r2 新增，豁免通道）**：*现在不抓，将来还抓得到吗？*——入库时近零成本**且**事后不可恢复的信息（典型：源许可条款页面快照），允许在无读者时以证据文件形式存档进 `_source/`（不进 schema 结构体）。源网格镜像保留在 `_source/` 是本判据成立的前提（embedding/快照/网格统计均可事后从镜像重算，故不属此类），此保留政策自 r2 起为显式约束。

外部实证注记：ACDC（CoRL 2024）B.3 把检索失败归因于类目命名抽象层级不匹配（cup vs coffee_cup）——正是 `aliases` 必填要防的失败模式。

四类消费者（字段的「读者」来源）：① 复用-检索/选中（grounding：category/aliases/colors/materials/尺寸）；② 场景编译+物理验证（solver + SAPIEN settle：摆放惯例组）；③ 迁移编译（openxsim transfer：representations/articulation/physical）；④ 治理/可信（溯源/许可/哈希/验证状态）。

## 3. Schema：`asset_ledger.v1` 字段总表

权威单位 = **每资产一份 `ledger.json`，模型变体进 `models[]`**（r2 owner 决定：资产级字段只写一次、改一处生效；结构对齐上游 catalog 的 asset+models[]）。openxsim 侧经 `to_ir_bundles(ledger)` 拆包适配——每个 model 摊平为一个 IR AssetBundle dict（`asset_id` = `<资产asset_id>_m<model_id>`，与 resolved_scene / 现存 IR 用法一致），**openxsim 本体零改动**。

每张表的「含义 · 目的」列格式：*含义——**目的**（谁读它 / 防什么失败）*。

### 3.1 资产级顶层

| 字段 | 含义 · 目的 | 类型 / 约束 | 必填 | 现状 |
|---|---|---|---|---|
| `schema_version` | 账本格式版本号——**目的**：validator 据此决定校验规则；旧账本被明确识别并引导 backfill，杜绝对无版本文件的语义猜测 | const `"asset_ledger.v1"` | ✅ | 新增 |
| `asset_id` | 资产唯一标识——**目的**：全链路对齐同一资产；须为 IR 合法标识符（如 `external_315_shears`），且与所在目录名对应（`315_shears`），validator 交叉校验 | str | ✅ | 语义调整（不再带 `_m<N>` 后缀，后缀由拆包适配器合成） |
| `category` | 资产类目——**目的**：grounding 按类目选中；惯例继承与尺寸参照按同类先例查找 | str 非空 | ✅ | 已有 |
| `semantic_name` | 人类可读语义名——**目的**：对齐上游 catalog 同名字段，内外资产条目同构 | str | ✅ | 新增 |
| `kind` | 刚体还是关节体（资产级：同资产各 model 同 kind，对齐上游 `load_type` 层级）——**目的**：validator 按 kind 切换 models[] 必填表；管线选验证路径 | `rigid` \| `articulated` | ✅ | 新增 |
| `tags` | 自由标记——**目的**：按批次/来源过滤统计 | list[str] | ✅ | 已有 |
| `semantics` | 见 §3.2 | object | ✅ | 收编 |
| `models` | 模型变体列表，见 §3.3——**目的**：一资产多 model 是 RoboTwin 惯例（如 302_can 有 4 个）；`model_id` 全列表唯一，validator 校验 | list，≥1 | ✅ | 结构调整 |

### 3.2 `semantics`（资产级一次；读者：grounding / 线 D 检索）

| 字段 | 含义 · 目的 | 类型 | 必填 | 现状 |
|---|---|---|---|---|
| `aliases` | 文字请求中可能指代该资产的同义词——**目的**：grounding 命中率。**缺失失败模式：文字场景永远选不到它**（入了库等于白入；外部实证见 §2 ACDC 注记） | list[str]，≥1 | ✅ | 从 fragment 收编 |
| `colors` | 外观颜色词——**目的**：带颜色 prompt 过滤；s10 e2e 靠它 ground 到 301_cup | list[str] | 可空 | 从 fragment 收编 |
| `materials` | 材质词——**目的**：带材质 prompt 过滤；另为将来「材质→物性表派生」预留中间键（2025-2026 主流做法：存材质类，density/friction 查表派生，比直接猜 mass 可审计） | list[str] | 可空 | 新增 |

（评审决定：不设 `description`——线 D 现行检索为词表匹配 + grounding LLM 语义层，无当前读者；按「无读者不进 schema」不占位。）

### 3.3 `models[]` 条目结构总览

每条 = `{model_id, physical, representations[], articulation, source, verification[]}`。`model_id` 为 int；以下 §3.4–§3.8 均为 models[] 条目内字段。

### 3.4 `models[].physical`（读者：solver 摆放 + 迁移编译）

| 字段 | 含义 · 目的 | 类型 / 约束 | 必填 | 现状 |
|---|---|---|---|---|
| `mesh_bbox_m` | 规范化后网格实测包围盒（米）——**目的**：oversize 门禁、solver 间距、迁移时作 `dimensions_m` 入 IR；实测防转换器单位假设 | [x,y,z] | ✅ | 已有 |
| `mesh_up_axis` | 网格自身 up 轴——**目的**：决定规范化旋转与 stable pose 取值；几何语义由此字段驱动，防启发式猜轴 | `Y` \| `Z` | ✅ | 已有 |
| `origin_convention` | 网格原点约定——**目的**：z_policy 正确落桌的前提 | str（如 `bottom-center`） | ✅ | 新增 |
| `scale_applied` | 入库统一缩放系数——**目的**：几何变换可复现，比例失调可追责 | float | ✅ | 已有 |
| `size_resolution` | 尺寸策略决策记录——**目的**：回答「为什么缩放成这样」；无先例显式 `no_precedent` | `{mode, actual_max_dim_m, scale, reference_max_dim_m, reference_assets[], verdict}` | ✅ | 已有 |
| `conventions.is_static` / `z_policy` / `footprint_shape` | 静态性 / 落桌策略 / 占地形状——**目的**：solver 与运行时行为；类目语义可继承 | bool / str 枚举 / `circle`\|`box` | ✅ | 已有 |
| `conventions.stable_poses` | **稳定姿态列表**（r2 改形）`[{pose_id, orientation_wxyz, is_default}]`——**目的**：solver 取 `is_default` 条与 yaw 合成朝向（今天行为不变）；多稳定姿态（瓶立/躺）是物理事实，容器形态现在定死，将来加躺姿是 append 而非破坏性改形。**缺 default 姿态的失败模式（已发生）：资产躺着生成** | 非空；恰一条 `is_default:true`；逐条单位四元数（模长容差 1e-6） | ✅ | 从 fragment 收编+改形（原 `stable_pose_id`+`stable_orientation_wxyz` 标量） |
| `conventions.support_margin_m` / `support_spawn_clearance_m` | 支撑边距 / 生成净空——**目的**：堆叠不悬边、不穿透 | float | 可选 | 新增 |
| `conventions.inherited_from` | 惯例参照资产（null=无先例）——**目的**：区分验证过的与抄来的惯例 | str \| null | ✅ | 已有（改名） |
| `mass_kg` | 质量——**目的**：迁移后端消费；三态 status 使「估计值」有合法落位（r2：2025-2026 主流是估计→材质表派生→仿真验证，`known\|unknown` 二值堵死该路线）；`estimated` 必带 `estimator`（如 `material_table:steel` / `vlm:<model>@<date>`）保持可审计 | `{value, status: known\|estimated\|unknown, estimator?, runtime_default_kg, runtime_default_basis}` | ✅ | 已有+r2 扩展 |
| `mass_kg.runtime_default_basis` | 运行时缺省的**依据**——**目的**：把「管线全局常量」与「资产事实」显式分离（0.1kg 写进每份账本≠每个资产 0.1kg 重）；关节体 basis=`urdf_inertial`（引擎按 URDF 惯量算），不吃全局常量 | `global_constant` \| `category_typical` \| `urdf_inertial` \| `none` | ✅ | r2 新增 |
| `friction` | 摩擦——**目的**：同 mass；跨后端物理不等价的诚实记录 | 同 mass 结构（runtime_default 可 null=引擎默认材质） | ✅ | 新增 |

### 3.5 `models[].representations[]`（读者：openxsim transfer / enrich；迁移核心）

| 字段 | 含义 · 目的 | 类型 / 约束 | 必填 | 现状 |
|---|---|---|---|---|
| `format` / `backend` / `role` / `uri` / `sha256` / `size_bytes` | 同 r1：格式取用、后端判据（**缺 isaacsim 表示→如实 blocker**）、视觉碰撞分取、加载入口、内容锚定 | role 枚举 r2 扩为 `visual` \| `collision` \| `visual_and_collision` \| **`snapshot`** | ✅（snapshot 除外） | 已有 |
| `role: "snapshot"` 条目 | 渲染快照（正面/多 yaw PNG）——**目的**：人工 QA 画廊 + 将来视觉检索物料（ACDC 实证：快照属资产侧、入库一次生成可缓存；r2 owner 决定入库顺手渲）。**管线产出物，validator 不强制**（缺快照不算 violation） | metadata 必记 `{yaw_deg, camera, renderer}`——快照/特征是渲染参数×版本的函数，防静默漂移 | 可选 | r2 新增 |
| `metadata.derived_from` / `converter` / `conversion_params` | 转换血缘三件套——**目的**：追源、复现、圈定转换器 bug 波及面；`converter` 取不到时标 `unknown (pre-v1 import)` 不编造 | str / str / dict | 转换产物必填 | 已有+固化 |

约束：每 model **至少 1 个 sapien 表示**；isaacsim 缺失合法。

### 3.6 `models[].articulation`（kind=articulated 必填，rigid 为空对象）

同 r1：`joint_names/joint_types/limits`（与 URDF 核对，迁移关节保真）、`closed_qpos/open_qpos`（任务语义+验证驱动）、`balance_gate{free_joints_allowed, measured_equilibrium}`（豁免显式留痕）。

### 3.7 `models[].source`（读者：治理 / 追溯 / 发布合规；**per-model**——同资产不同 model 可来自不同源文件，如 302_can 四个 model 各一个源 USD）

| 字段 | 含义 · 目的 | 类型 | 必填 | 现状 |
|---|---|---|---|---|
| `library` / `group` / `file` / `url` | 来源坐标——**目的**：定位原始出处；url 是重新获取与许可核查入口 | str | ✅（url 有则填） | 已有+新增 |
| `license` | 许可结构化记录——**目的**：发布合规程序化判据。`status` 语义（r2 明确）：`unknown`＝**来源清楚但条款未核查**（不是来源不明——来源不明进不了池），`declared`＝已核查并记录适用范围。unknown 合法但必须显式 | `{spdx: str\|null, status: declared\|unknown, terms_note}` | ✅ | 已有→结构化 |
| `retrieved_at` / `source_manifest_path` | 获取日期 / 源镜像哈希清单——**目的**：来源快照锚定；转换链两端有哈希。`retrieved_at` 格式（H1 硬化 5 收紧）：仅接受规范日期形 `YYYY-MM-DD`（10 字符，无时间部分），带时间的完整 datetime 字符串视为格式错误——见 §3.8 timestamp 的收紧理由，对称适用 | ISO 日期（`YYYY-MM-DD`）/ str | ✅ | 新增/入账 |
| `selection_evidence_path` / `import_matrix_path` | 检索决策链 / 导入判定记录——**目的**：「为什么引进它」可追溯；挂链接防漂移 | str \| null | 有则填 | 新增 |

### 3.8 `models[].verification[]`（读者：迁移前可信度判断 + 治理；r2 语义修复）

每条记录：

| 字段 | 含义 · 目的 | 类型 / 取值 |
|---|---|---|
| `backend` / `check` / `verdict` | 在哪验、验什么、过没过——**目的**：跨后端验证不等价，逐后端记账；check 枚举为 L0-L4 预留 | `sapien`\|`isaacsim`\|… / `settle`\|`joint_sweep`\|`runtime_load`\|`e2e`\|`admission_report` / `pass`\|`fail` |
| `run_id` | 本次验证所属运行标识（如 results 运行目录名）——**目的**：追溯到完整运行上下文；同 run 重复条目去重依据（r2） | str |
| `timestamp` | 验证时刻，**秒级 ISO**——**目的**：同日多次验证可排序；latest 语义的排序键（r2，原 `date` 只到日不可判序）。格式（H1 硬化 5 收紧）：仅接受规范 T 形 `YYYY-MM-DDTHH:MM:SS`（19 字符、`T` 分隔、秒级精度）；拒绝 `Z` 后缀、空格分隔形、紧凑无横杠形、纯日期形——理由：裸 `datetime.fromisoformat` 能接受的集合在 py3.10/py3.11 间不一致（本项目双跑两个 env），且空格分隔形与规范 T 形混用时按字符串比较会颠倒 `latest_verification` 的排序语义 | ISO datetime（`YYYY-MM-DDTHH:MM:SS`） |
| `verified_digest` | **验证时该 model 同 backend 全部 representations 的内容摘要**（各 sha256 排序连接后再 sha256）——**目的**：钉住「验的是哪个内容」；文件更换后 digest 不再匹配 → 该记录自动失效。没有它，「资产改了要重验」无任何可执行机制（r2 必改#1） | str（64 hex） |
| `report_path` / `thresholds` | 完整报告路径 / 当次门限——**目的**：按内容判定落点；pass 可解释 | str / dict 可选 |

**读取语义（规范，r2）**：「当前验证状态」= 每 (backend, check) 取 `timestamp` 最新一条，**且** `verified_digest` 与当前 representations 摘要一致；不一致视为未验证（如实报，不静默沿用旧 pass）。历史条目仅作审计。`any(pass)` 式读取是错误实现——重验 fail 后旧 pass 不得复活。

约定：append-only（历史即审计日志）；写入经 **fcntl 文件锁**（多写入方并发保护，r2 必改#2）；同 (backend, check, run_id, verified_digest) 重复条目跳过。`admission_report` 只记 s14 报告路径并标注「视图层、可逆」。

### 3.9 派生字段（validator 推导，**禁止手写**）

per-model `usable: bool` + `missing: list[str]`——按必填表推导，与上游 catalog 同名机制同构；「说自己可用」的账本不可能撒谎。

**治理注记（r2 必改#2）**：账本文件**纳入 git**（`.gitignore` 对 `data/asset_library/*/ledger.json` 开豁免；全库几百 KB）——「唯一权威」的最低要求是历史可查、误写可回滚；verification 追加允许批量 commit，不要求逐条。

## 4. 数据流与组件

**账本权威位置**：`data/asset_library/<asset>/ledger.json`（账本随资产走；r2 定稿）。`results/<run>/bundles/` 保留**运行快照**（per-model 摊平形态，与 `to_ir_bundles` 输出同构）。读者一律读权威位置。

```
import_materialize / s8b / s13b ──唯一写入方（upsert model 进资产账本）──> ledger.json（写后即跑 validator）
s4 / s11 / s13b 验证步 ──append（fcntl 锁）──> models[].verification[]
ledger.json ──gen_fragment（latest-settle-pass 过滤 + default pose 投影 ± --license-gate）──> fragment ──s9（不变）──> 扩展 catalog
ledger.json ──to_ir_bundles 拆包──> openxsim transfer / enrich / s5（openxsim 本体零改动）
```

| 组件 | 位置 | 职责 |
|---|---|---|
| `ledger.py`（新） | `1_asset_reuse/lib/` | schema 常量 + `validate_ledger` + `new_model_entry`/`upsert_model` builder + `append_verification`（锁）+ `reps_digest` + `to_ir_bundles` + `latest_verification`；纯 stdlib 双环境可 import |
| `gen_fragment.py`（新） | `1_asset_reuse/scripts/` | 账本→fragment：模型过滤条件=latest (sapien, settle)=pass 且 digest 一致；`stable_poses` 取 `is_default` 条投影为 fragment 的 `stable_pose_id`/`stable_orientation_wxyz` 标量（fragment 格式冻结）；**`--license-gate`**（默认关；开启后 `status != declared` 的 model 不进视图）；**无论开关，每次运行打印 unknown license 计数警告**（r2 owner 决定 B+：欠账常显不静默） |
| `backfill_ledger_v1.py`（新，一次性） | `1_asset_reuse/scripts/` | 现存 per-model 旧 bundle + fragment + import_matrix **聚合**为每资产 ledger.json；validator 清零后归档旧件 |
| `import_materialize` / `s8b` / `s13b`（改） | 现位置 | 落账走 builder+upsert；settle 后顺手渲快照（role=snapshot，复用 s3 渲染写法）；violation 即该 model FAIL（`schema_violation:<code>` 进 import_matrix） |
| `s4` / `s11`（改） | 现位置 | 验证结果 append 进对应 model 的 verification[]（带 run_id/timestamp/verified_digest） |

**`model_data0.json` 不是派生视图**——RoboTwin 资产本体的一部分，账本在 representation 层记其哈希。

## 5. 兼容与迁移路径

1. `ledger.py`（schema+validator+拆包）→ backfill 聚合升 v1、validator 清零（现存不一致全部显式化）。
2. `gen_fragment` 产出与现 merged fragment **语义 diff=0** 后切换 s9 输入；手拼停用。
3. 批量管线切 v1 落账；旧账本 validator 报 `needs_backfill` 不猜语义。
4. 池层只进不出不变：验证 fail 的资产账本仍在（verification 记 fail），淘汰只在视图层。
5. openxsim/s5 等原 per-model bundle 读者改经 `to_ir_bundles`；运行快照保持摊平形态，向后兼容。

## 6. 错误处理与边界

| 情形 | 处理 |
|---|---|
| validator 发现账本 ↔ fragment ↔ 磁盘不一致 | 报错列 diff，不静默取任一方 |
| 字段查不到 | 结构化 unknown + runtime_default(+basis)，不编造 |
| sha256 与磁盘不符 / uri 不存在 | 硬失败 |
| 旧账本（无 schema_version） | 报「需 backfill」 |
| **验证记录 digest 与当前文件不符** | **该记录失效，视为未验证——如实报，不沿用旧 pass**（r2） |
| **多进程并发 append verification** | **fcntl 锁串行化**（r2） |
| upsert 时资产级字段与已有账本不一致 | 硬失败（同资产第二个 model 落账即比对——写时抓漂移） |
| openxsim 读账本 | 经 `to_ir_bundles` 拆包；IR `AssetBundle.from_dict` 按键选择性读取（`ir.py:136-147`），拆包输出与旧 bundle 同构 |
| `stable_poses` 为空 / 无 default / 非单位四元数 | validator 拒绝 |
| license unknown | 入池合法；gen_fragment 常显警告；`--license-gate` 开启时不进视图（发布纪律见 §8） |

### 6.1 validator 违规码全集（实现新增部分）

上表按「情形」组织；以下三个 violation code 是实现里存在、但上表未逐一点名的判据，补记含义与引入原因（§8 末行原则同样适用于本节：**`ledger.py` 常量表为规范文本，本节为文档视图**——如两者不一致，以 `ledger.py` 为准）：

| code | 含义 | 引入原因 |
|---|---|---|
| `bad_type` | 字段存在但类型不符（如 `models` 不是 list） | 与「missing（缺失）」区分：值本身存在，只是形状错——presence-only 检查看不出这类错误，需要单独判据 |
| `bad_timestamp` | `verification.timestamp` / `source.retrieved_at` 不满足各自的规范格式 | 防止目录名（如 `batch_v3`）被误切片拼出语法合法、语义垃圾的时间戳字符串静默通过（T8 真实事故，见 §3.8） |
| `bad_sha256` | `representations[].sha256` 不是 64 位十六进制字符串 | 契约外码：`check_files=False`（不碰磁盘）时也生效，独立于需要磁盘存在才能判定的 `file_missing`/`sha256_mismatch` |

## 7. 测试与验收

1. **单测**（`test_ledger.py`）：validator 正反例（每条必填/枚举/形状规则一个反例，含 stable_poses 三规则、mass status 三态、digest 格式）；`to_ir_bundles` 拆包输出等价旧 bundle 形状且过 IR `AssetBundle.from_dict`；`append_verification` 锁+去重+append-only；`latest_verification` 语义（新 fail 压过旧 pass；digest 不符即失效）。
2. **generator**：latest-settle 过滤、default pose 投影、license-gate 开关行为、unknown 警告输出、与现 merged fragment 语义 diff=0。
3. **回归**：1_asset_reuse 既有 42 测试 + openxsim 49 测试不破；`run_smoke.sh` s10/s12 四连判定照过。
4. **验收**：全量现存资产 backfill 后 validator 0 error；任选一资产走 transfer，能从账本程序化读出「目标后端当前有效验证状态」（含 digest 一致性判断）。

## 8. 范围外 / 依赖 / 待议

- **license 实际核查（audit）——待议（owner 标记 2026-08-08）**：半自动方案已讨论未立项（扫池列去重许可来源→人工判定填 decisions（spdx/适用范围/证据链接/判定人）→按来源批量回写账本→条款页面快照存 `_source/`）。池内 17 外部资产仅 2 个许可来源（NVIDIA EULA / YCB terms），核查是 O(来源) 小时级。**发布纪律（现行有效）：对外发布前必须开启 `--license-gate` 并使 unknown 归零。**
- **迁移一致性验证口径 L0-L4**：`verification[].check` 枚举预留扩展位。
- **线 D 语义检索增强**：不预留占位字段；快照（role=snapshot）与源镜像使 embedding 可事后批量重算，决策可逆。
- **verification 长期形态**：例行 sweep 上线时切换为「摘要进账本 + JSONL 历史归档」，届时定规则（对抗评审 C 项）。
- 上游只读不变；fragment 格式与 s9 接口零改动。
- validator 的规范文本 = `ledger.py` 内常量表；本文件 §3 为文档视图（消灭双真相，r2）。

## 9. 上游资产账本（r3 追加，2026-08-10）

**动机**：迁移消费者需对场景中出现的**任何**资产回答「目标引擎拿什么加载、验过没有」——上游 RoboTwin 资产（约 130 条 catalog 条目）此前不在账本范围，现纳入。

**架构：派生核心 + 增量层**（不改上游一行、不产生会腐烂的复制品）：

| 层 | 字段 | 权威 | 再生成语义 |
|---|---|---|---|
| 派生核心 | 身份/语义/几何/摆放（由上游 asset_catalog.json 条目映射） | **上游 catalog**（我们的账本禁手改这些字段） | 每次重跑 backfill_upstream **覆盖刷新**（上游 pull 后重跑一次即同步） |
| 增量层 | representations 的非 sapien 条目（如 isaacsim USD 登记）、verification[]、license（人工判定后） | **本项目** | 再生成时**保留合并** |

**约定**：
- 位置 `data/upstream_ledgers/<asset>/ledger.json`（与外部池分目录——gen_fragment 只扫外部池，上游资产走上游 catalog 入场景，不经我们的视图层）；asset_id 前缀 `robotwin_`（builder 的 asset_id_prefix 参数）。
- `source_manifest_path` 指向 backfill 生成的逐文件哈希清单（存于账本同目录，兼作上游资产漂移检测锚——上游文件变了哈希对不上即知过时）；`retrieved_at` 取上游资产目录内最新文件 mtime（获取时刻不可考，basis 记录于报告）。
- catalog 标 `usable:false` 的 model 如实跳过不入账（报告记录），不硬凑。
- license 初始 unknown（RoboTwin 资产来源混合，条款核查是独立后续 audit）。
- 消费端：迁移侧（usd_enrich）按资产名先查账本（上游+外部两区）的 isaacsim 表示；查不到行为不变（如实报缺）。
