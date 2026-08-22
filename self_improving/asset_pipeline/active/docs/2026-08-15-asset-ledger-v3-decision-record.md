# asset_ledger.v3 合成决策表（四路辩证 + 实验裁决）

日期：2026-08-15 凌晨。输入：检索怀疑者/迁移怀疑者/冗余审计员/外部对标员四份实证报告 + E1/E2 裁决实验（进行中）。
版本事实：代码现行契约为 asset_ledger.v2（scale_applied 已删），本次定稿为 **v3**。
库存事实：外部账本 63 份 + 上游 16 份 = 79 份（87 model）；catalog 189 条目 155 可用。

## 一、删除（9 项，冗余审计零决策消费者证明）

| 字段 | 证据 | 处置 |
|---|---|---|
| semantic_name | 79/79 恒等于 category；账本→catalog 通路不存在（catalog 的 semantic_name 另有来源） | 删；投影时按需派生 |
| tags | 全词表 7 词均为 kind/source 复制品；openxsim 决策读取 0 命中 | 删；ir.py 容缺省 |
| mass_kg.runtime_default_kg / runtime_default_basis | "runtime" 是谎言：真实运行时质量走上游 catalog 标量通路；唯一读者是 archive 死代码；87 份里 86 份抄同一全局常量 0.1 | 删；常量归 runtime_config/编译器侧 |
| friction.runtime_default / runtime_default_basis | 同上 | 删 |
| physical.mesh_up_axis | 与 kind 100% 互锁（rigid→Y, articulated→Z）；消费面 0；且 E2 证明它是 per-model 字段却描述不了 per-representation 的真实帧（302_can 账本 Y、USD 实测 Z） | 删；帧信息下沉到 representations[].frame（见新增） |
| physical.origin_convention | 与 kind 100% 互锁；消费面 0 | 删 |
| representations[].size_bytes | v2 已降可选；零决策消费者，身份由 sha256 承载 | 写入方停写，字段移出契约 |

## 二、降级/收缩（5 项）

| 字段 | 处置 | 理由 |
|---|---|---|
| size_resolution | scale + actual_max_dim_m 保留必填（单位漂移校验和，历史抓过 12/39 真实失败）；mode/reference_*/verdict 降为可选决策留痕 | 后四项入库后零读者 |
| verification[].report_path | 降可选 | 必填的审计装饰；无存在性检查 |
| source.source_manifest_path | 降可选；文档声明按 `_source/<group>/SOURCE_MANIFEST.json` 约定派生 | 70/87 可派生；17 例外中 16 个是指向已删 checkout 的死路径（负资产） |
| inertial | 收缩为 {status, basis, com_m?, inertia_diagonal_kgm2?, principal_axes_wxyz?}——三个数值字段全部可选 | 73 份携带者全 null；无编译器读者；但外部对标确认业界最小集含惯量主轴 → 留可选位而非删除 |
| license.evidence_url/checked_date/checked_by | 维持可选，文档明示"不可恢复捕获，无读者" | 契约铁律：license 事实删了就找不回 |

## 三、新增（迁移侧，E2 实证支撑）

| 字段 | 服务 | 出处 |
|---|---|---|
| `external_ids: {env_gen: "302_can", ...}` 资产级 | 三套命名（账本/上游目录/IR）的映射目前全靠代码约定，账本零记录——ledger-backed Isaac 链在第一个真实资产上断裂（skipped_no_ledger 实跑复现） | 迁移 A |
| `representations[].frame: {up_axis}` | 同一 model 的 GLB(Y-up 已烘 scale) 与 USD(Z-up 未烘) 是几何上不同的物体，现契约无字段区分 | 迁移 C、E2 |
| `representations[].geometry_state: {scale_baked: bool, origin: str}` | 30/71 model scale≠1 只烘在 GLB 侧；enrich 按"USD 已烘"假设归一 → Isaac 尺寸错 1.8-2.9×（E2 量化） | 迁移 C、E2 |
| `representations[].files[]`（多文件，各带 sha256） | 单文件 sha 对 USD payload/variant 失明（sektion 缺 payload 实测不可察觉）；MuJoCo 需 OBJ+分解碰撞多文件 | 迁移 F |
| `representations[].role` 增 `collision`；`collision_meta: {approximation, decomposer, params, hull_count}` | 碰撞语义现藏在 conversion_params 无一等地位；业界四规范交集（UsdPhysics approximation / KHR geometry / CoACD 参数决定保真度）；本周 basket 楔入事故的根因维度 | 对标 + 迁移 C |
| BACKENDS 枚举 += `mujoco` | 现枚举 (sapien,isaacsim,portable) 装不下第三后端 | 迁移 F |
| `stable_poses[].measured_against: {backend, run_id}`；placement 测量同 | 同库两种互斥姿态语义并存（上游 identity vs 外部 X90，无字段判别）；本周"同网格换加载器停留差 9mm"实证测量-栈耦合 | 迁移 D（最硬一条）+ 对标确认业界无此先例但我们场景必需 |
| `physical.restitution`（三态）；friction 拆 static_friction/dynamic_friction | 业界最小物理集四规范交集；SAPIEN 三参数材质模型原生支持 | 对标 |
| `physical.placement: {interior_dims_m, interior_floor_z_offset_m, support_surface_*, measured_against}` | 可放置性几何（probe 实测）现只活在 s9 生成层与旁路 JSON，账本无位置——权威性漂移 #6；容器检索三层断裂的 metadata 层 | 审计漂移 + 检索 6 |
| `models[].appearance: {colors_measured[], color_fractions, method, measured_against}` | 508 份模型级实测色无契约位置（漂移 #1）；资产级 colors 导致多色资产色盲 + 诚实惩罚悖论（发布者被淘汰、未测者通吃）；E1 量化挽回幅度 | 检索 4/5 + E1 |
| `semantics.category_anchor: {lvis?, wikidata?}`（可选） | 外部可对齐类目锚点；SimReady 用 Q-code、Objaverse 用 LVIS 的业界惯例 | 对标 |
| `source.license.attribution`（可选） | CC-BY 法律要求署名；打包 NOTICE 的读者 | 对标 |

## 四、修复义务（不是字段，是管线契约条款）

1. **materials 投影缺失 bug**：gen_fragment 只投影 colors 不投影 materials → 材质查询永远命中不了外部资产（上游 grounding 明明会消费）。修 gen_fragment。
2. **验证回写义务**：Isaac E2E/转换完成必须回写 verification[]（现全库 isaacsim 记录 0 条，含打通过的 001_bottle）；runtime_revocations 必须回写（现在视图层悄悄推翻账本，账本不知情）。"不写回 = 未完成"。
3. **junction 修复**：usd_enrich 改用 AssetBundle.source 的 asset_id/model_id（importer 已保真传递），不再靠前缀剥离猜。
4. **URI 基准**：representations[].uri 定契约基准=相对账本所在目录；绝对路径判 audit 违规（现 278 条绝对路径绑死本机）。
5. **check 受控词表**：verification[].check 收敛为枚举（settle/joint_sweep/runtime_load/…），跨后端可比。

## 五、明确不加（对标 + 审计一致意见）

- CLIP/OpenShape embedding 向量：随模型漂移，进契约即耦合 schema 与检索模型 → sidecar 向量库，keyed by asset_id
- 接触求解器参数（solref/solimp/kp/kd）：引擎调参非物体属性，业界一致不进资产元数据 → 后端 profile
- 场景级状态（初速度/kinematic/gravityFactor）、combine mode、collision filter：场景装配关切
- grasp/affordance 标注：量级+gripper 依赖 → 伴生数据集 keyed by (asset_id, scale)
- 形状/尺寸的派生几何量（h/w 比等）：可从 mesh_bbox_m 投影时派生，存储即冗余 → 投影层+上游对齐清单（parser 形状词表、grounding 尺寸量化匹配）

## 六、上游对齐清单新增（本轮发现、动不了上游只能记）

- parser：形状修饰语词表缺失（shallow/deep/tall/mini 被静默丢弃或熔成假类目 tall_pitcher）；情态动词 "can" 误配可乐罐；修饰语+词表外名词的去修饰语回退
- grounding：dimensions_m 只做存在性加分不做数值匹配（"large cup" 实测选中全池最小杯）；模型级颜色选型（E1 补丁为证）；interior 适配度评分
- CatalogEntry.colors 资产级 schema 限制（模型级色盲的上游半边）
- 索引侧：LVIS 同义词层（"trash bin"→0 命中）、Objaverse tags/categories/faceCount 未入索引、per-category 截断无质量排序
- settled 判据 0.5°/关节角度单位（deg vs rad）/upstream 其余既有条目

## 七、v3 迁移方案

79 份账本自动迁移：删除段直接剥离；appearance 从 asset_attributes.json 回填（有测量的 model）；placement 从 top_support_survey.json + overrides 回填（有测量的）；measured_against 统一补 {backend: "sapien", run_id: 迁移批次}（诚实：这些数字都是 SAPIEN 栈测的）；external_ids 从 asset_id 前缀规则生成；frame/geometry_state 按已知事实回填（GLB: Y-up+scale_baked=true；USD _source: Z-up+scale_baked=false——E2 证实后）；validator 同步更新；audit 全清 + 265 测试 + openxsim 56 测试全绿为收工线。

## 八、实验裁决结果（2026-08-15 实跑回填）

- **E1 模型级颜色**：44 条属性矩阵重跑——颜色真命中 9→23、错配 23→9、全绿 43/44 保持。模型级 appearance 由此从提案升级为 v3 正式字段；逐行结果见 [`validation_evidence/ledger_v3_20260815/matrix/`](../../../validation_evidence/ledger_v3_20260815/matrix/)。
- **E2 Isaac 迁移**：①身份修复（剥 asset_ 前缀/改走 source 键）后 skipped_no_ledger 清空、compile partial→compiled；②尺寸漂移 = 精确 1/scale（cracker box 2.8527×），30 model 命中——scale_baked 实证必需；③Isaac runtime 舞台实测同样 2.8527×（尺寸异常非仅编译期）；静态引用下 reset/step 通过是空洞证据——运行时验证须记录物理绑定状态。isaac-smoke 环境新机可用（OMNI_KIT_ACCEPT_EULA=YES 前提）。
- **冗余回归**：v3 删除/降级全部落地后双套件 262+57 全绿、79 账本迁移零失败、目录 156 可用、B11 e2e fail=0；最终验证报告见 [`validation_evidence/ledger_v3_20260815/b11/`](../../../validation_evidence/ledger_v3_20260815/b11/)。

## 九、定稿状态

hyx 分支 0df4fb4（90 文件）；镜像 329e0db。账本例外规则已补进实验室仓库 .gitignore（79 份账本首次获得该仓库的 git 历史）。
