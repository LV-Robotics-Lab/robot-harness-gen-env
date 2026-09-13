# C08/C09 多实体设计与动态支撑增量契约

状态：合同或设计。来源：已批准canonical计划C08/C09及冻结M03/H02/H03；不是修改矩阵或物理阈值。
基线：`dfda113`。S01真实单物体闭环已通过，但不证明本合同能力。沿用唯一Harness/三个Skills。

## 分片及不变项

1. 实际加载拓扑证据：先增强现有Genesis child的几何审计，仍拒绝未经证明的动态目标支撑。
2. 实测支撑面与新碰撞版本：证明面域及孔洞，不以统一凸包填平内凹结构。
3. 单支撑DAG编译与物理检查：允许刚体→刚体→结构支撑，每动态实体恰好一个on目标。
4. Design Grounding v2：逐实体资产锚定、显式生成布局授权和完成门独立重验。
5. 小型真实双profile验证，再运行冻结案例；不把fixture替身或新JSON字段算接通。

首片不实现inside、关节、多支撑；这些冻结分支仍记录失败，不改成成功。全部前景保持dynamic。
原结构支撑/单物体v1回执继续按原授权验证；不得让新模式追认旧证据。

## 实际几何拓扑

固定Genesis `0e74bf392781884ccad765c3f344419c86b872ca`：
`rigid_geom.py`公开get_verts/init_faces和get_vverts/init_vfaces；rigid_solver.py初始化实际使用
geom.init_faces + geom.vert_start。每geom保留局部顶点/三角索引及entity/link/geom归属，不能拼接
顶点后仍使用未偏移的局部索引。世界顶点经实体实际逆位姿变为URDF-local。

沿用已有顶点距离审计，增加面拓扑绑定与独立URDF mesh核对；必须识别相同顶点但连接不同的非等价面。
允许顶点/面重排，但不得无证据接受新增/缺失表面。明确处理独立sdf_mesh；未经审计的替代表面拒绝。
记录不等于物理通过。对应公开seam仍为run_scene/实际child执行及包内运行，不新增公共Skill。

## 有效支撑面

支撑面证据绑定不可变资产及视觉/碰撞成员、单位、URDF-local水平面高度、原面索引、二维域和孔洞。
来源必须是实际几何，不能由plate类别、最高顶点或外AABB推断。视觉与碰撞表面的共同有效域才可支持。
现有normalize_mesh整体convex_hull不能授予凹盘支撑；需要新的不可变多凸块碰撞版本。
固定Genesis支持一个link的多个collision；已声明runtime存在CoACD，但尚未验证其native执行。
分解必须有有界seed/参数/预算并重核支撑域；返回mesh不等于保真或仿真通过。

## 碰撞新版本的最小纵切

公开`decompose_collision(parent_version, *, registry, store, backend, policy, approval,
output_root, timeout)`只处理已登记单link静态几何的碰撞表示；不放入颜色patch，不建立第二workflow。
controller approval绑定parent、明确policy和approved；模型不能自己授予执行权限。
输入从parent的visual GLB及URDF/node完整变换读取，不能从已填凹的旧collision凸包再分解。
首片只接受闭合有限正体积三角网格；没有合格输入就返回结构化失败，不能根据plate类别补几何。

- 外部backend是明确部署的CoACD CPU子进程；固定解释器、库版本/二进制身份、seed和全部参数。
  preprocess_mode=off；不启用extrude/decimate/PCA，不改源visual、质量/摩擦/惯量与许可。
- 新collision/part-N.obj必须有限、合法索引、闭合、正体积且凸；预算和块数/三角数均有界。
  块写新目录、URDF单link引用全部块；旧collision不作为新版本必要成员，旧parent字节不改。
- timeout只中断准确owner进程组，先SIGINT留日志、再有界收尾；错误/取消/部分输出有结构化记录。
  backend返回候选不授面域或物理通过，禁止固定成功回执或从fixture替身授真实能力。
- 新版本记录parent_version、来源/原许可、approval、原visual及新碰撞hash、参数/进程/候选校验。
  惯量仍是原supplied convex-hull近似，准确记录，不偷偷改成新几何实测；必要时后续独立修订。
- 登记只是不可变候选；随后由measure_support_surfaces测新visual/collision共同域，派生proof不回写版本。
  没有共同有效面或分解填洞仍失败，保留新候选和原因；只有真实load/双dt/profile消费后才授适用能力。
- AssetPreviewRenderer改为核实际URDF引用闭包：根asset.urdf/physics.json、受控visual GLB及
  collision OBJ相对成员；拒绝多余/缺失/逃逸/外部材质，不再以恰好四文件限制多凸块。

本纵切只授权组件实现与小型真实CPU分解验证，后续controller资产修复路由须另片接入并记录预算。
旧默认normalize_mesh整体凸包行为不变；没有新资格、真实重建或矩阵通过主张。

首片的succeeded仅指合法碰撞候选生成/登记与共同支撑面可用。必须另记
`collision_preservation_status=not_run`和`physical_evaluated=false`：visual与collision的交集保孔，
并不证明collision自身没有填孔。若无共同面则失败且保candidate；若有面也不能授整体保真性。
完整保真及具体支撑适用性仍由后续独立几何/实际加载/接触/穿透门判定，不能用共同域存在制造pass。

## 版本化动态支撑运行载荷

旧RuntimeScene v1字段、序列化和静态矩形判据保持原样；增加独立RuntimeSceneV2，
schema_version=x2env.runtime_scene.v2，support_profile=measured_single_support_dag.v1。
统一parse_runtime_scene按明确schema_version分派，不能向旧载荷补默认字段造成历史digest漂移。
CompiledScene用判别union；只有实际动态目标on边才生成v2，旧compile receipt不改。

RuntimeSupportBinding逐on绑定source_id/target_id及kind(structural_top|measured_surface)，
动态目标携target_version_sha256/target_asset_record_path/surface_path/surface_sha256/
selection_receipt_path。所有相对路径必须被RuntimeMember的hash/size覆盖，结构目标相应项为null。
资产record复用完整AssetVersion；surface复用MeasuredSupportSurface，不重复编造平面高度/域。
首片selection仅unique_feasible；多可行面无授权选择即ambiguous，不选最高或首面。

共享纯measure_support_surfaces_from_members(asset_record,read_member(path)->bytes)与
read_asset_geometry_from_members读取完整变换几何/bounds/visual中心，先核成员hash/size。
Registry wrapper只inspect再传入；包内消费者读取已核验相对成员，不构造假Registry/依赖原CAS。
此片先接编译/可加载包与独立几何核验，动态physical消费者另片接；不能只因v2可解析就授物理通过。

## 动态支撑的模型补全纵切

保留scene_grounding.v2的直接结构支撑路径及历史重算。实际动态目标使用明确
scene_grounding.v3/ generated_layout_context.v3与controller的design_authorization.v3；仍属同一ground操作。
模型只返回`MeasuredLayoutValues.choices`：每项entity_id、path、有限value；path仅允许结构
dimensions[0]/dimensions[1]、pose.position[0]/pose.position[1]、pose.yaw_degrees。
允许集合来自真实缺失且basis=simulation_design_choice的规则；必须恰好覆盖一次，不能提交已知轴、
资产尺寸、frame、关系或Z。范围继续原显式GeneratedLayoutPolicy，不改变成功阈值。

`build_measured_candidate`按原proposal/资产绑定/规则及选择构造候选SceneIR，仅补允许XY/yaw/结构尺寸
与已有deployment/asset固定值；Z未知保留。候选写当前ground操作的CAS后，调用与compile共享的
`resolve_measured_layout(scene_ref,assets,*,registry,store,policy,seed)`纯求解器，推唯一可行面和真实底面Z。
solver不写文件/CAS、不登记、不执行compile；没有可行面或歧义保留失败，不让模型猜Z或覆盖已知Z。
GroundingResult只在独立重验后推进父state；完成门从原authorization/proposal/真实choices/context与候选ref
重建同一结果。新receipt不追认历史v2，不复制第二套workflow或几何求解器。

首片没有模型选面；多面歧义明确阻断。该合同仍未授任何真实动态案例、视觉或physical资格。

## 坐标、图与动态物理

- 保留SceneIR前景几何中心、结构上表面中心、normalized URDF XY中心/Z底面约定。
- 支撑图拓扑排序，拒绝环、自边、多目标及未解析frame；重复类别不合并ID。
- 未知源Z只能由对应目标实测支撑面和源实际最低几何求解；显式XYZ/yaw不得覆盖。
- 编译输出完整世界位姿；运行器不改变布局。未知XY不能默认为零。
- 每帧把源实际visual+collision完整footprint转换到目标当帧局部坐标，检查带孔支撑域及边界余量。
  不只检查顶点；跨孔边和覆盖孔洞的内部也要拒绝。目标域不能取凸包填洞。
- 每on边独立检查对应接触对的有效向上力；罐碰桌不替代罐碰盘。所有动态物体均执行原双dt、稳定性、
  全轨迹穿透和媒体门，冻结2cm支撑余量不变。超出水平支撑profile则明确拒绝。
- 完成门及复制包包含面证据、加载拓扑、场景和实际轨迹；相同消费者重算，而非信任自报passed。

## Design Grounding v2

显式部署选择generated simulation layout，默认不开启；仅授权模型为生成仿真选择未知结构尺寸、
XY/yaw，不声称从媒体恢复真实尺度。范围上限8实体；数值设计范围由部署记录，不改物理成功阈值。
未知项原行/critical不改写，未解决conflict拒绝；用户已知数字、实体、关系、frame始终不可改。

逐字段区分input_fixed、asset_geometry、deployment_default、support_geometry_derived、
simulation_design_choice、media_layout。每前景的尺寸只绑定自己的version/normalization；不得使用
第一资产尺寸代替全部。动态目标高度由上述实测支撑面决定，不由模型编造。

模型仅给逐实体数值，Harness检查ID集合、已知轴、资产尺寸、图、边界和可量化关系；保留真实媒体来源。
新receipt明确v2版本，含全部资产绑定、原proposal、授权、规则、固定值、设计选择及真实model evidence。
completion按版本从已提交输入重建规则并重验；不能仅以出现字段猜版本或用回执自称授权。
不另建controller。旧v1仍沿用原two-entity/单anchor合同。

### 首个纵切固定字段与可执行子集

- 保留原SceneDesignPolicy及其dump；新增GeneratedLayoutPolicy，mode=generated_layout、enabled默认false。
  保留同名structural_defaults_enabled/world_anchor_xy/world_anchor_yaw_degrees显式授权字段。
- 新设计范围字段support_extent_range_m=(0.05,5.0)，有限正数且下界小于上界；
  position_abs_max_m=5.0，有限正数。范围仅检查模型新选值，已有值不clamp也不改写。
- Deployment沿用scene_design_policy字段，v1/v2明确mode的联合；旧缺mode配置仍选择原v1，
  不能因新增discriminator使旧配置失效。模型prompt无权开启新模式。
- 首片允许1..8实体和多个world-frame结构支撑；每前景恰好一个直接结构on目标。动态目标先返回
  unsupported_dynamic_support_geometry，等待实测面片接入，不以v2绕过runtime关系门。
- 未知前景Z仅在frame等于唯一结构target时用该资产halfheight推导；world-frame未知Z首片拒绝，
  已知worldpose保留。未知结构厚度/高度仅用显式StructuralPolicy，不产生无依据的生成Z。
- 无图生成可选择未知结构长宽、各实体XY/yaw；原媒体关系及字段出处仍须保持，不能用新模式忽略参考图。
- receipt明确schema_version=x2env.scene_grounding.v2；保留bundle_ref/proposal_ref/assets_ref/policy/
  structural_policy，新增逐实体asset_bindings、plan、fixed_values、design_choices，并保原unknowns、
  changes、输出SceneIR及真实evidence。GroundingValuesV2为1..8实体且ID集合不变，只含数值建议。
- completion精确按receipt版本分派；从已提交原proposal、全部Registry版本和编译policy重建规则/固定值，
  校验原模型transport、raw values、设计范围和最终SceneIR，不信任receipt自报字段授权。

## TDD与真实验收

首片纯几何模块为`measured_support.py`，公开`measure_support_surfaces`返回全部有证据的水平候选面，
不以类别/最高点代选；面证据作为version派生记录，避免写回自身版本产生循环。
读取URDF/node完整变换后分别并合朝上共面三角片、扣上方遮挡投影，再取visual/collision共同面；
Polygon/MultiPolygon保留所有interior rings，不使用buffer(0)/make_valid填洞或修补无效输入。
`evaluate_support_footprint`对源全部三角投影并集F和目标当帧域D检查`D.covers(F)`以及
`distance(F, D.boundary)>=0.02m`；不是仅顶点、凸包或minimum_clearance。
本片是authored几何证明，后续仍需实际loaded parts重算一致才能授物理能力。
二维布尔运算依赖Shapely2.1.2，独立固定安装，不改变sealed Genesis环境；官方接口依据为
[Polygon](https://shapely.readthedocs.io/en/2.1.2/reference/shapely.Polygon.html)与
[用户手册](https://shapely.readthedocs.io/en/2.1.2/manual.html)。

既有公开compile_scene、ground_scene/Harness、evaluate_physics/assess_scene、verify_package和
materialize_completion作为测试seam。先写错误接受的RED，再最小实现。
攻击至少包括：同顶点异面、错误geom索引、凸包填洞/错层、源跨孔、动态图初始位姿误用、错接触对、
环/多支撑、逐实体尺寸错绑、无授权text设计、改已知轴、伪造v2授权/未提交证据、包缺面资源。
每测试组/真实case/copy-run总预算≤1800秒；真实生成/仿真失败保产物，不改阈值或预填proposal。
本合同尚未授予动态支撑、生成布局或任何新增矩阵成功。
