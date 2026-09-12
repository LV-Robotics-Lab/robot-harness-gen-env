# 共享意图到开发 Genesis 场景

`compile_scene(store, input_ref, intent, asset) -> CompiledExperimentScene` 返回
`scene_ref`、`representation_ref`、`receipt_ref`。store 使用既有 CAS；不创建第二个 workflow。
本模块不签发资产或 Skill qualification，所有物理检查为 not_run。

intent 精确接受 category/color/source/selection/dimensions_m/xy_m/yaw_deg/support/
object_count/input_sha256/unknowns。输入摘要必须绑定真实 CAS 输入；support=table、object_count=1、
unknowns=[]。source/selection/category 必须与已获取资产一致；颜色意图保留供后续视觉判断，
静态编译不声称颜色匹配。

输入 AssetVersion 逐成员重新检查 SHA256。将自包含 visual.glb 转成 OBJ+材质成员，同时保留真实
collision OBJ；写入现有 AssetRepresentationV1，完整 loader closure 由既有 digest 函数绑定。
入口为 asset/urdf/asset.urdf，物性为 asset/physics.json，来源 receipt 也进入同一 closure。

dimensions_m=null 保留实测尺寸。显式尺寸只允许统一比例缩放；三轴比例不一致拒绝。统一缩放
同步 visual/collision、质心和惯量（二次方），保持请求质量，记录 requested_uniform_estimate。
不改变已登记 AssetVersion；representation 是显式可追溯场景派生物。

场景实际调用已有 adapt_gujie_tabletop_scene：重新解析全部 visual/collision OBJ 顶点，应用
xy/yaw，桌面中心(0,0,0.35)、尺寸(1.2,1,0.1)、摩擦0.6。Z由旋转后全顶点最低点加桌面高度与
0.001m间隙得到；完整 footprint 必须在桌缘0.01m内。read_gujie_tabletop_scene 可以独立重算。

source document 是 Harness 从当前意图创建的原生记录，使用已有 adapter 输入格式；并非 Gujie
历史重建或人工 image proposal。Gujie eb0b710 的几何/桌面语义归属继续保留；此处新增转换和
controller-facing seam 属 Harness 整合。输入、意图、资产版本、representation、scene和缩放在
compile receipt 中共同绑定。布局变化会改变 source/geometry/scene/receipt bytes。

测试位于 tests/harness/test_experiment_scene_compile.py，覆盖真实primitive静态编译、布局变化、
统一缩放、非均匀缩放拒绝、完整footprint拒绝和未知控制字段拒绝。Genesis执行与媒体验证由
工作流调用者执行，不能把本静态测试当作sim-ready。
