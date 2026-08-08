# 资产入库 metadata 契约（asset_ledger.v1）· 设计 spec

> **日期**：2026-08-08 · **状态**：设计定稿（待实现） · **所属**：1_asset_reuse 入库管线（写方）+ 2_sim_migration / openxsim（读方），两阶段共用
> **一句话**：以 AssetBundle 账本为**唯一权威入库记录**（账本即契约），收编散在 `external_overrides_fragment.yml` / 验证报告里的元数据，新增 `schema_version` 与 `verification` 组；fragment 降级为账本的**派生视图**（generator 自动生成）。
> **名词**：**账本（ledger）**＝每 (asset, model) 一份的 AssetBundle JSON；**fragment**＝`external_overrides_fragment.yml`（s9 构建扩展 catalog 的输入）；**池层/视图层**＝资产池只进不出，catalog 是池上的可逆过滤视图。

## 1. 目标与范围

**问题**：同一资产的元数据摊在 4 处（账本 JSON、fragment、`model_data0.json`、`import_matrix.json`），无 schema 版本、无统一 validator；双后端验证结果躺在 `results/` 不回填账本，迁移时无法程序化判断「该资产在目标后端验过没有」。

| 在本任务 ✅ | 不在本任务 ❌ |
|---|---|
| `asset_ledger.v1` schema 定义 + validator（入库门禁末尾） | 上游 / s9 输入接口改动（fragment 格式不变，只改生成方式） |
| fragment generator（账本 → YAML，替代手拼） | s14 准入语义改动（仍视图层、可逆） |
| 验证状态回填账本（`verification[]`） | license 实际核查（本任务只结构化记录现状） |
| backfill 脚本：现存入库资产账本升 v1 | 线 D 语义检索增强（embedding 等） |
| | openxsim IR 结构改动（新键对 `from_dict` 透明，见 §6） |

## 2. 字段确定方法（判据；将来加字段也按此）

metadata 不是「描述资产的一切属性」，而是**下游消费者输入合同的并集**。每个字段过三个测试：

1. **有真实读者**——代码里哪一行读它；没人读不进 schema。
2. **缺失有明确失败模式**——最好能被门禁检测（先例：缺 `stable_orientation_wxyz` → 资产躺着生成；缺 isaacsim 表示 → transfer 报 blocker）。
3. **能自动获得，或显式结构化 unknown**——`{"value": null, "status": "unknown", "runtime_default": …}`，不编造。

新字段提案必答三问：**谁读它、缺了怎么失败、拿不到怎么办**。答不上不进 schema。

四类消费者（字段的「读者」来源）：① 复用-检索/选中（grounding：category/aliases/colors/materials/尺寸）；② 场景编译+物理验证（solver + SAPIEN settle：摆放惯例组）；③ 迁移编译（openxsim transfer：representations/articulation/physical）；④ 治理/可信（溯源/许可/哈希/验证状态）。

## 3. Schema：`asset_ledger.v1` 字段总表

权威单位 = **每 (asset_id, model_id) 一份账本 JSON**（沿用现状）。在现有 AssetBundle 结构上扩展，不推翻；「现状」列标明字段来源。

每张表的「含义 · 目的」列格式：*含义——**目的**（谁读它 / 防什么失败）*。

### 3.1 顶层

| 字段 | 含义 · 目的 | 类型 / 约束 | 必填 | 现状 |
|---|---|---|---|---|
| `schema_version` | 账本格式版本号——**目的**：validator 据此决定校验规则；旧账本被明确识别并引导 backfill，杜绝对无版本文件的语义猜测 | const `"asset_ledger.v1"` | ✅ | 新增 |
| `asset_id` | 资产在库内与 IR 中的唯一标识——**目的**：账本→catalog→`resolved_scene`→IR 全链路靠它对齐同一资产 | str，IR 合法标识符（现状形如 `external_315_shears_m0`，不重构 id 体系） | ✅ | 已有 |
| `model_id` | 同一资产下第几个模型变体（RoboTwin 一资产多 model 惯例）——**目的**：与上游 catalog `models[]` 对齐；fragment 逐 model 字段生成时定位，读者不必解析 asset_id 字符串（id 保持不透明标识符）。validator 交叉校验 asset_id 后缀 ↔ model_id 一致，冗余变一致性检查 | int | ✅ | 新增（现在只隐含在 asset_id 后缀里） |
| `category` | 资产类目（cup/can/…）——**目的**：grounding 按类目选中；惯例继承与尺寸参照按同类先例查找 | str 非空 | ✅ | 已有 |
| `semantic_name` | 人类可读语义名——**目的**：对齐上游 catalog 同名字段，保持内外资产条目同构 | str | ✅ | 新增 |
| `kind` | 刚体还是关节体——**目的**：validator 按 kind 切换必填表（articulation 组是否必填）；管线据此选验证路径（settle vs 关节扫掠） | `rigid` \| `articulated` | ✅ | 新增（现在隐含在 tags 里） |
| `tags` | 自由标记（rigid/external/batch…）——**目的**：按批次/来源快速过滤统计；openxsim IR 现有字段，保留 | list[str] | ✅ | 已有 |

### 3.2 `semantics`（读者：grounding / 线 D 检索）

| 字段 | 含义 · 目的 | 类型 | 必填 | 现状 |
|---|---|---|---|---|
| `aliases` | 文字请求中可能指代该资产的同义词——**目的**：grounding 命中率；prompt 说 "mug" 也能选中 cup 类资产。**缺失失败模式：文字场景永远选不到它**（入了库等于白入） | list[str]，≥1 | ✅ | 从 fragment 收编 |
| `colors` | 外观颜色词——**目的**：带颜色的 prompt（"red mug"）过滤；s10 e2e 正是靠它 ground 到 301_cup | list[str] | 可空 | 从 fragment 收编 |
| `materials` | 材质词——**目的**：带材质 prompt 过滤；上游 catalog 有此字段，外部资产补齐保持同构 | list[str] | 可空 | 新增（上游 catalog 有，外部资产缺） |

（评审决定：不设 `description` 字段——线 D 现行检索为词表匹配，语义检索无当前读者；按「无读者不进 schema」判据不预留占位，将来立项时按真实需求加字段，schema_version 支持演进。）

### 3.3 `physical`（读者：solver 摆放 + 迁移编译；现有组扩展）

| 字段 | 含义 · 目的 | 类型 / 约束 | 必填 | 现状 |
|---|---|---|---|---|
| `mesh_bbox_m` | 规范化后网格的实测包围盒（米）——**目的**：oversize 门禁、solver 摆放间距、迁移时作为 `dimensions_m` 带入 IR；「实测」而非源文件声明，防转换器单位假设 | [x,y,z] 米 | ✅ | 已有 |
| `mesh_up_axis` | 网格自身的 up 轴约定——**目的**：决定规范化旋转与 `stable_orientation` 取值（Y-up→X+90）；几何语义不继承、由此字段驱动，防启发式猜轴 | `Y` \| `Z` | ✅ | 已有 |
| `origin_convention` | 网格原点约定（如底部中心）——**目的**：`z_policy` 正确落桌的前提；跨后端摆放一致的锚点 | str（如 `bottom-center`） | ✅ | 新增（现散在 representation.metadata.origin） |
| `scale_applied` | 入库时施加的统一缩放系数——**目的**：源网格→库内网格的几何变换可复现；比例失调时可追责到这一步 | float | ✅ | 已有 |
| `size_resolution` | 尺寸策略完整决策记录（模式/实测/参照资产/结论）——**目的**：回答「为什么缩放成这样」；无先例时显式 `no_precedent`，不装作验证过 | `{mode, actual_max_dim_m, scale, reference_max_dim_m, reference_assets[], verdict}` | ✅ | 已有 |
| `conventions.is_static` | 是否静态物（不参与动力学）——**目的**：solver 与运行时行为分支；从同类先例继承（类目语义可继承原则） | bool | ✅ | 已有 |
| `conventions.z_policy` | 落桌高度策略——**目的**：物体不悬空、不穿桌；上游 solver 直接消费 | str 枚举（上游取值） | ✅ | 已有 |
| `conventions.footprint_shape` | 占地形状——**目的**：solver 支撑面/间距计算；堆叠关系的碰撞近似 | `circle` \| `box` | ✅ | 已有 |
| `conventions.stable_pose_id` | 稳定姿态命名（upright 等）——**目的**：标识 orientation 对应哪个姿态，与上游 stable pose 机制对齐 | str | ✅ | **从 fragment 收编** |
| `conventions.stable_orientation_wxyz` | 网格坐标→世界直立姿态的四元数——**目的**：solver 以 yaw∘此值合成最终朝向。**缺失失败模式（已发生）：资产躺着生成** | [w,x,y,z]，单位四元数（validator 查模长） | ✅ | **从 fragment 收编** |
| `conventions.support_margin_m` / `support_spawn_clearance_m` | 支撑面边距 / 生成净空——**目的**：`on_top_of` 堆叠时不悬边、初始不穿透 | float | 可选（默认继承上游惯例） | 新增 |
| `conventions.inherited_from` | 惯例抄自哪个参照资产（null=无先例）——**目的**：区分「验证过的惯例」与「抄来的惯例」；惯例出错可追溯到先例资产 | str \| null | ✅ | 已有（`precedent`/`note` 规范化改名） |
| `mass_kg` | 质量，结构化未知——**目的**：迁移到需要质量的后端时不编造；`runtime_default_kg` 显式声明实际生效值，「默认值」与「实测值」永不混淆 | `{value: float\|null, status: known\|unknown, runtime_default_kg}` | ✅ | 已有 |
| `friction` | 摩擦系数，结构化未知——**目的**：同上；跨后端物理不等价的诚实记录（桥接设计表③要求） | 同 `mass_kg` 结构 | ✅ | 新增 |

### 3.4 `representations[]`（读者：openxsim transfer / enrich；迁移核心）

| 字段 | 含义 · 目的 | 类型 / 约束 | 必填 | 现状 |
|---|---|---|---|---|
| `format` | 文件格式——**目的**：后端编译器按格式取用（`representation_for` 的 formats 过滤） | str（glb/obj/urdf/usd…） | ✅ | 已有 |
| `backend` | 该表示服务哪个后端——**目的**：transfer 判断目标后端有无可用表示的字面判据。**缺失失败模式（已验证）：缺 isaacsim 表示 → Isaac 编译如实报 blocker** | `sapien` \| `isaacsim` \| `portable` | ✅ | 已有 |
| `role` | 视觉 / 碰撞 / 两用——**目的**：装配时视觉与碰撞网格分开取用（SAPIEN visual+collision 分离惯例） | `visual` \| `collision` \| `visual_and_collision` | ✅ | 已有 |
| `uri` | 文件绝对路径——**目的**：加载入口；validator 查存在性（坏路径=坏账本，硬失败） | 存在的文件路径 | ✅ | 已有 |
| `sha256` / `size_bytes` | 内容哈希与大小——**目的**：证明「转换没走样、文件没被换」；validator 与磁盘核对，账本与实物永远一致 | 64 hex / int≥0 | ✅ | 已有（size 偶缺，补齐） |
| `metadata.derived_from` | 转换血缘：源文件——**目的**：从库内任一文件一步追回原始来源 | str | 转换产物必填 | 已有（自由键固化为约定名） |
| `metadata.converter` | 转换器及版本——**目的**：复现转换；转换器出 bug 时能圈定波及面（哪些资产是它转的） | str（如 `omni.kit.asset_converter@isaac-5.1`） | 转换产物必填 | 新增 |
| `metadata.conversion_params` | 转换参数（旋转/缩放/凸分解方式等）——**目的**：复现 + 解释产物与源的几何差异 | dict | 转换产物必填 | 部分已有，收拢 |

约束：每资产**至少 1 个 sapien 表示**；isaacsim 表示缺失**合法**（迁移时如实报 blocker，不静默降级）。

### 3.5 `articulation`（kind=articulated 必填，rigid 为空对象）

| 字段 | 含义 · 目的 | 类型 | 现状 |
|---|---|---|---|
| `joint_names` / `joint_types` / `limits` | 关节名 / 类型（revolute/prismatic/fixed）/ 限位——**目的**：迁移关节保真（桥接方案 B 的核心收益）；s13b 拿它与 URDF 声明核对，防「转出来的关节和源不一致」 | list（s13a 已产出） | 已有 |
| `closed_qpos` / `open_qpos` | 关节闭合 / 张开位形——**目的**：任务语义（开抽屉）与验证驱动目标位；对齐上游 catalog 同名字段 | list[float] | 已有 |
| `balance_gate` | 平衡门结果：是否豁免自由关节 + 实测平衡位——**目的**：记录「该关节体静置会不会漂」；豁免必须显式留痕，防静默放行漂移资产 | `{free_joints_allowed: bool, measured_equilibrium: list\|null}` | 新增（s13b `--allow-free-joints` 落账） |

### 3.6 `source`（读者：治理 / 追溯 / 发布合规）

| 字段 | 含义 · 目的 | 类型 | 必填 | 现状 |
|---|---|---|---|---|
| `library` / `group` / `file` | 来源库 / 组（服务器 prefix）/ 源文件名——**目的**：定位原始出处的最小坐标 | str | ✅ | 已有 |
| `url` | 来源 URL——**目的**：重新获取与许可核查的入口 | str | 有则填 | 新增 |
| `license` | 许可证结构化记录——**目的**：发布合规的程序化判据；unknown **合法但必须显式**（`terms_note` 留核查线索），不能装作没问题。发布门禁将来只需查 `status` | `{spdx: str\|null, status: declared\|unknown, terms_note: str}` | ✅ | 已有（自由文本 → 结构化） |
| `retrieved_at` | 获取日期——**目的**：来源快照时间点；上游资产服务器内容会变，出争议时锚定「当时拿到的是什么」 | ISO 日期 | ✅ | 新增 |
| `source_manifest_path` | 源镜像逐文件哈希清单路径——**目的**：证明「库内产物确实来自这些源文件」，转换链条两端都有哈希锚 | str | ✅ | 已有实践，入账 |
| `selection_evidence_path` / `import_matrix_path` | 检索决策链 / 批量导入判定记录路径——**目的**：回答「为什么引进它、同批其他为什么淘汰」；挂链接不复制内容，防两处记录漂移 | str \| null | 有则填 | 新增 |

### 3.7 `verification[]`（读者：迁移前可信度判断 + 治理；**本设计最大新增**）

每条记录：

| 字段 | 含义 · 目的 | 类型 / 取值 |
|---|---|---|
| `backend` | 在哪个后端验的——**目的**：「物理验证跨后端不等价」（design.md §6），每个后端单独记账，SAPIEN 的 pass 不冒充 Isaac 的 pass | `sapien` \| `isaacsim` \| … |
| `check` | 验证类型——**目的**：区分不同强度的验证（静置 ≠ 运行时加载 ≠ e2e）；枚举为 L0-L4 口径预留扩展位 | `settle` \| `joint_sweep` \| `runtime_load` \| `e2e` \| `admission_report` |
| `verdict` | 通过与否——**目的**：迁移前程序化判断「该资产在目标后端可信吗」的入口；**本组解决的核心缺口：验证报告躺在 results/ 无法程序化查询** | `pass` \| `fail` |
| `date` | 验证日期——**目的**：判断验证是否过期（资产文件变了要重验，配合 sha256 判断） | ISO 日期 |
| `report_path` | 完整验证报告路径——**目的**：账本只存结论，完整证据可一步追到；按内容判定原则的落点 | str（results/ 下的验证 JSON） |
| `thresholds` | 当次验证门限（位移/倾角等）——**目的**：让 pass 可解释——「位移<2mm 的 pass」≠「无门限的 pass」 | dict，可选 |

约定：**append-only**，重跑追加不覆盖（验证历史即审计日志）；`admission_report` 只记 s14 报告路径并标注「视图层、可逆」，准入结果不改变池层任何字段。

### 3.8 派生字段（validator 推导，**禁止手写**）

`usable: bool` + `missing: list[str]`——该 model 当前是否可用、缺了什么。**目的**：与上游 catalog 同名机制同构，内外资产从此同一套可用性判据；由 validator 从必填表推导、禁止手写，保证「说自己可用」的账本不可能撒谎。

## 4. 数据流与组件

```
import_materialize / s8b / s13b ──唯一写入方──> 账本 v1（写后即跑 validate_ingest）
s4 / s11 / s13b 验证步 ──append──> 账本 verification[]
账本 ──gen_fragment──> external_overrides_fragment.yml ──s9（不变）──> 扩展 catalog
账本 ──openxsim AssetBundle.from_dict 直读──> transfer / enrich（新键透明，见 §6）
```

| 组件 | 位置 | 职责 |
|---|---|---|
| `ledger.py`（新） | `1_asset_reuse/lib/` | schema 常量 + `validate_ingest(bundle_path) -> list[Violation]`；纯 stdlib、双 conda 环境可 import（同 `conventions.py` 先例） |
| `gen_fragment.py`（新） | `1_asset_reuse/scripts/` | 全量账本 → fragment YAML；过滤条件：至少 1 条 `sapien settle pass` 才进视图（池层照收）。**聚合规则**：fragment 的资产级字段（category/aliases/colors）取同资产各 model 账本的一致值，不一致 = validator 错误（不静默取并集） |
| `backfill_ledger_v1.py`（新，一次性） | `1_asset_reuse/scripts/` | 现存入库资产账本升 v1：从 fragment / 验证报告 / SOURCE_MANIFEST 反向回填，跑 validator 清零后归档旧账本 |
| `import_materialize` / `s8b` / `s13b`（改） | 现位置 | 落账改写 v1 结构；物化尾部调 `validate_ingest`，violation 即该模型 FAIL（进 import_matrix，带 `schema_violation` 淘汰码） |
| `s4` / `s11`（改） | 现位置 | 验证完成后把结果 append 进对应账本 `verification[]` |

**`model_data0.json` 不是派生视图**——它是 RoboTwin 资产本体的一部分（运行时布局约定），物化时照常生成；账本只在 representation 层记它的存在与哈希。

## 5. 兼容与迁移路径

1. 先落 `ledger.py`（schema + validator）→ 跑 backfill，把现存资产升 v1、validator 清零（顺带把现存不一致全部扫出，逐条修或显式标 unknown）。
2. `gen_fragment.py` 产出与现 `external_overrides_fragment_merged.yml` diff 一致后，切换 s9 输入为生成版；手拼 fragment 停用。
3. 批量管线（import_* / s13）切 v1 落账；旧版账本读到时 validator 报 `schema_version missing` 引导 backfill。
4. 池层「只进不出」不变：验证失败资产账本仍在（`verification.verdict=fail`），淘汰只发生在视图层与 import_matrix 记录。

## 6. 错误处理与边界

| 情形 | 处理 |
|---|---|
| validator 发现账本 ↔ fragment ↔ 磁盘不一致 | 报错并列 diff，**不静默取任一方** |
| 字段查不到（mass/friction/license…） | 结构化 unknown + runtime_default，不编造 |
| sha256 与磁盘不符 / uri 不存在 | 硬失败（坏账本=坏资产） |
| 旧账本（无 schema_version） | validator 明确报「需 backfill」，不猜测语义 |
| openxsim 读 v1 账本 | 安全：`AssetBundle.from_dict` 按键选择性读取、忽略未知顶层键（`ir.py:136-147`，s5 亦走此路径），新增组对 IR 透明 |
| `stable_orientation_wxyz` 非单位四元数 | validator 拒绝（模长容差 1e-6） |

## 7. 测试与验收

1. **单测**（`1_asset_reuse/tests/test_ledger.py`）：validator 正反例（每条必填规则一个反例）；四元数模长；sha256 核对；asset_id 后缀 ↔ model_id 交叉校验；unknown 结构合法性；旧版账本报 backfill。
2. **generator 幂等**：`gen_fragment` 输出与现 merged fragment 语义 diff = 0（键序无关比较）。
3. **回归**：openxsim 全量测试（49 passed）不破；`run_smoke.sh` s10/s12 四连判定照过。
4. **验收**：全量现存入库资产 `validate_ingest` 0 error；任选一资产走 transfer，能从账本 `verification[]` 程序化读出目标后端验证状态。

## 8. 范围外 / 依赖

- **license 实际核查**（NVIDIA EULA / YCB terms 逐条确认）：治理后续任务，本契约只保证「unknown 必须显式」。
- **迁移一致性验证口径 L0-L4**（`docs/design.md` ⏳ 项）：`verification[].check` 枚举为其预留扩展位，口径定稿后追加取值即可，不改结构。
- **线 D 语义检索增强**：本任务不涉及，schema 亦不预留占位字段；将来立项时按真实需求加字段（schema_version 支持演进）。
- 上游只读约束不变：不改 `external/env-gen-github`；fragment 格式与 s9 接口零改动。
