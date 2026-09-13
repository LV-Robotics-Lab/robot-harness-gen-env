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

## Design Grounding v2（实施前再固定字段schema）

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

## TDD与真实验收

既有公开compile_scene、ground_scene/Harness、evaluate_physics/assess_scene、verify_package和
materialize_completion作为测试seam。先写错误接受的RED，再最小实现。
攻击至少包括：同顶点异面、错误geom索引、凸包填洞/错层、源跨孔、动态图初始位姿误用、错接触对、
环/多支撑、逐实体尺寸错绑、无授权text设计、改已知轴、伪造v2授权/未提交证据、包缺面资源。
每测试组/真实case/copy-run总预算≤1800秒；真实生成/仿真失败保产物，不改阈值或预填proposal。
本合同尚未授予动态支撑、生成布局或任何新增矩阵成功。
