# canonical x2env 公共测试 seams

P0/P1 使用既有 CLI/Deployment 与外部 adapter seam；公开 `check_deployment`/CLI `check`
只读报告配置存在性和 pinned JSON 完整性，不创建 Store 或执行模型。与执行相同的 PinnedFile
读取规则必须拒绝符号链接、过大文件和漂移；不把 path present 宣称为 backend 已验证。

状态：accepted_matrix_v1_frozen。替代旧父子Golden/MCP/planner的active测试合同；
历史证据不升级为canonical资格。批准来源为实施计划第4、8–12节。

## 用户与能力seam

v3父推进前`audit_pending_measured_grounding`为已批准只读seam：真实active snapshot/公开Store状态、
controller授权、返回GroundingResult与controller显式policy。共同内容核验与最终完成门复用，但不
伪造committed operation/compiled scene。Harness公开测试须证明坏返回不采用scene/不compile且保错误证据。

Harness.submit/status/resume/package是唯一用户接口；模型前持久化handle，同key同工作流。
最后revision验证通过、package物化可读后才原子succeeded。
状态仅active、succeeded、failed、blocked、cancelled，timed_out是cancelled原因。
意外child死亡保持可恢复active，resume检查准确owner及已提交操作，不重复submit。

公开Skill只有x2env.compile、x2env.replay、x2env.validate；asset.resolve、observe、revise为内部Tool。
CapabilityRegistry固定精确版本与digest；AssetRegistry只管理不可变资产。
强校验处为用户/媒体、模型建议、外部导入、跨进程、持久化恢复、package/资格；
已校验typed object不在进程内每层重复深验。

## 外部adapter seams

预览来源身份与运行拒读目录分开：GenesisConfig.source_identity 仅由部署选择 Git 或 installed
策略，预览固定核自身五个执行源成员。Git 的显式 root/commit 失败不得退到 installed；installed
核当前 distribution 的成员/原始 RECORD/METADATA，可固定 RECORD 摘要，但不把自描述 RECORD
当作原 wheel 签名。所有记录仅 development provenance；denied_roots 独立显式传给两条预览装配，
不得把推算出的 site-packages 整体当成工作区拒读，也不据此授予 runtime 或 release 资格。
自动 Git 必须对应实际 canonical 源目录，安装在仓库内的虚拟环境仍核 distribution/RECORD；
显式 Git 的不匹配继续拒绝，canonical 源位置的损坏Git不切模式。

编译seam消费固定SceneIR artifact、ResolvedAssetSet、seed和显式StructuralPolicy，不获取资产。
SceneIR坐标约定：foreground为几何中心，结构支撑frame为上表面中心；normalized URDF原点为
XY中心/Z底面，编译器做显式偏移。仅未给出的结构厚度/世界原点可使用部署policy，并记录defaults；
长宽必须来自SceneIR，foreground未知高度只由明确on关系与已测资产半高解析。
不得修改用户已给轴/朝向，尺寸冲突拒绝；没有解析的关节/inside不静默简化。
foreground未知X/Y不得用支撑中心替代。控制器与完成门共用needs_design_grounding检查实际
缺失的必要设计值，不以unknown.critical为唯一门；未解决conflict即使非critical也须阻断。
显式StructuralPolicy默认、Registry实测尺寸与合法on-Z推导仍沿用原compile合同，不一律强制模型补全。
classify_design_unknowns保留原unknown行与标记；规则有内容时原unknown索引可为空，完成门重算并核对。

- CodexBackend.interpret / assess_asset_candidates / assess_and_diagnose只提供建议，不控制执行或物理通过。
- Store.asset_categories提供有界排序命名索引，部署取构造时快照交给CodexBackend；每次interpret留证。
  该上下文不是候选、实时catalog、许可或匹配证明；原whole-phrase/category门不变，不自动别名。
  明确超限只使可选命名上下文unavailable，不伪称空库；其它无效数据错误不得吞掉。
- interpret的请求级transport schema把SceneIR请求SHA/revision固定为已知值，出处SHA限制为
  本InputBundle原text/image/video来源集合；不使用派生帧PNG身份代替原来源。静态合同不变，
  模型仍选择内容和出处，后置来源类型/索引/帧/SHA关联检查保留；错误输出不得自动重写。
- 所有模型角色共用的 transport 固定显式请求 max 并记实际argv；拒绝原样失败，不重试降级，服务端有效力度未验证。
- interpret的JSON语法错误独立于语义/schema失败：最多一次带精确错误的模型重生成，原输入和schema
  不变、共享原deadline、两份raw/log保留；非语法错误和第二次失败停止，不做本地JSON内容修补。
- CodexBackend.ground_scene 补全显式部署允许的生成设计未知项；pending intent 只供查资产，
  测得的不可变资产尺寸与原媒体共同形成设计依据，不是图像绝对尺度测量，未通过检查不得 compile。
- YuxinProviderAdapter调用原provider engine，带真实local/web和许可门。
- ReconstructionAdapter接受已验证媒体与SceneIR；Gujie原接口保留非默认，
  用户批准替代后端通过同一外部seam使用，要求固定来源、许可、真实新geometry。
- GenesisAdapter实际build/load/reset/step，输出连续轨迹/contact/joint/媒体。
- ArtifactPublisher只上传已被资格接受的制品，并实际下载核验。

## 冻结验收seam

动态grounding继续公开ground_scene/Harness/completion seam；新增纯`build_measured_candidate`
按原规则检查MeasuredLayoutValues.choices，禁止模型提交Z/已知轴/非授权值，供producer与独立consumer复用。
纯resolve_measured_layout与compile共享求解，不在模型回合里偷偷执行第二compile。

动态包纯几何seam为`measure_support_surfaces_from_members(asset_record,read_member)`和
`read_asset_geometry_from_members(asset_record,read_member)`；Registry与包共享同一测量逻辑，
不mock内部测量或制造Registry。测试成员篡改/完整变换/源底面，v1字节不变及v2歧义/图/选面拒绝。

C09碰撞新版本纵切增加`decompose_collision(parent_version, *, registry, store, backend, policy,
approval, output_root, timeout)`，仅新不可变候选，不授物理能力；外部CPU分解使用显式backend seam。
测试真实Registry/CAS、完整visual变换、父字节不变、审批绑定、错误块拒绝、失败日志及有界超时；
AssetPreviewRenderer.render另片验证多collision闭包。细则见增量契约，不以类型门删除冒充动态支撑。

C08/C09首个纯几何纵切增加明确公共seam：
`measure_support_surfaces(version_sha256, *, registry, store)`及
`evaluate_support_footprint(surface, *, source_geometry_parts, source_pose, target_pose)`。
前者只产不可变资产authored几何派生证据，不称actual loaded；后者按当帧target逆姿态检完整三角投影，
保留孔洞，要求完整覆盖且到全部域边界距离不少于冻结0.02m。首片不开放runtime关系门。
测试含四角均合法但内部跨洞、错层/错版本、完整边/内域越界与target移动；实际加载拓扑绑定另片完成。
Shapely2.1.2在独立目录固定安装/记录wheel与GEOS身份，不改sealed运行环境；无效面不静默修复。

C08/C09多实体支撑增量按[增量契约](CANONICAL_MULTI_ENTITY_SUPPORT_SPEC.md)在既有compile/
ground/Harness/assessment/package公开seam执行TDD；固定Genesis实际拓扑先验证，不能只删类型门。

[qualification-matrix-v1.json](qualification-matrix-v1.json)与
[physics-assertions-v1.json](physics-assertions-v1.json)是C02公开测试输入。
C03测试typed contracts/Registry，C04工作流，C06–C10 adapter，C11复制包，C12 CLI，
C13旧消费者删除，C14文档。仅在外部adapter、时钟、文件系统等seam使用受控替身，
不以mock计12例资格通过。总预算1800s，场景+资产最多2次修订，每provider一次；
fault注入不可预填模型建议或改变冻结断言。
