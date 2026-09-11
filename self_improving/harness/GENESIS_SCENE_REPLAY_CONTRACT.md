# 有限桌面场景双时间步回放证据

本合同将 Gujie 固定 `eb0b710581fd7794bc01b447b0f77cb871c8a711` 的场景验收语义接入
Harness。来源为 `physics_criteria.py` 的失败分类、`validate_imported_scene.py` 的连续对象状态、
接触相位与固定根支撑、`validate_single_asset.py` / `asset_physics.py` 的末段稳定判据，以及
`scene_physics_graph.py` / `scene_physics_workflow.py` 的完整几何 footprint 与双 dt agreement。
实现归 Harness 适配层；不复制历史 pass，也不沿用上游绝对运行路径。

## 公共输入与边界

`GenesisSceneSpecV1` 包含 Gujie source、portable representation、完整 geometry 三个 CAS 输入、
测得的 visual + collision 顶点、dynamic `object` 的初始位姿、有限 axis-aligned `table` Box 的
中心/尺寸/摩擦，以及 z=0 的 `ground` Plane。只有桌面与 ground 可以固定。原始 source 与 placement
派生规则由 `read_gujie_tabletop_scene` 重算；桌面不能暗中换成无限 ground。

`profile_settings(profile)` 固定 baseline 为 dt=.004、1000 steps，half_dt 为 dt=.002、2000 steps；
两者均为 4 秒、同一初始场景、零初速、独立自由回放。函数拒绝自定义 profile。runner 必须真实创建
动态 URDF、有限静态 Box 和 ground，并保存其实际 dof/collision/fixed observations。

`GenesisSceneTraceRowV1` 要求每步完整保存三个对象的 position / wxyz quaternion / velocity /
angular_velocity，以及 contact 的对象双方、位置、单位 normal、penetration、双方 force。
step 0 是 initial_detection，force 必须 unavailable；后续是 solved_step，双方力须等量反向。
有限值、对象全集、连续 step/time、原始初态与固定 root 在所有行上重验。

## 纯读数学门

`evaluate_scene_replay(scene, baseline_rows, half_dt_rows)` 重算两条轨迹，不产生真实执行权威。
每条末段窗口为 .5 秒，继承以下 Gujie 阈值：

| 判据 | 门 | 分类 |
| --- | --- | --- |
| translation / rotation | <= .001 m / .5 deg | scene_truth |
| drift / rotation rate | <= .002 m/s / 1 deg/s | scene_truth |
| excursion | <= .001 m | scene_truth |
| sustained effective speed | `max(|v|, radius*|w|)` >= `1.5*g*dt` 的连续行数 < 5 | scene_truth |
| contact dropout | 末段未接触比例 <= .05 | numerics_coupled |
| support fraction | 接触样本中 table 对 object 向上力 > 1e-6 N 的比例 >= .8 | scene_truth |
| penetration | 全轨迹所有接触 <= .001 m | numerics_coupled |
| finite support geometry | 完整旋转后 visual + collision footprint 的 target-local margin >= .02 m | scene_truth |
| below ground / unexpected contact | 最低顶点 >= -.001 m；禁止 object-ground 替代声明的 object-table 接触 | scene_truth |

support geometry 保留 Gujie 的 1e-8 m 数值比较容差。weighted speed 最大值、末态线/角速度与
resting penetration budget 比例只作 diagnostic，不把孤立速度采样或 solver compliance 当成场景缺陷。
末段几何变化与支撑失败不可通过调数值参数隐藏；`numerics_tunable` 仅在存在失败、且所有失败均属于
numerics_coupled 时为 true。

两个 profile 均须独立通过；最终位姿之间还须 <= .01 m / 5 deg。profile 失败为 failed；两者通过但
agreement 不通过为 inconclusive；全部通过才为 passed。四元数角度使用归一化点积，允许已通过单位
四元数容差门的 float32 表示误差，不凭非单位点积制造虚假旋转。

## CAS 和连续媒体

`read_scene_replay(store, replay_ref)` 纯读：重验 Gujie scene adapter、完整 runtime manifest、两份
raw loaded_scene、canonical NDJSON、41 PNG 与各自 lossless 视频。raw load 必须绑定 profile、
representation、实际桌面中心/尺寸、object 6 DOF、两个 fixed root 与三者非零 collision geoms。

每 .1 秒保存一帧，包括 t=0 与 t=4，共 41 帧；PNG 必须同尺寸 RGB。视频可以是 lossless FFV1/MKV
或 lossless RGB MP4。纯读通过 ffprobe 重验 41 帧的 PTS/尺寸，再通过 ffmpeg 完整解码 RGB，并与
PNG 原始像素逐帧比对顺序和内容。报告保存总帧数、真实唯一帧数、尺寸、10 fps 与最后采样时刻 4 秒。
这不是只验一个视频文件 SHA，也不把静态预览当完整视频；最后一帧有正常显示时长，播放时长可为 4.1 秒。

## 权威与失败

报告 authority 固定 `trace_consistency_only`。CAS-only reader 可证明字节、字段、数学、媒体的一致性，
不能单独证明真实 Genesis 执行，也不提供 Live capability 或 qualification issuer。
生产环境资格仍须新的固定 scene producer 将批准的 runtime manifest、compile/native scene、raw load、
双 dt、媒体和实际进程身份封闭。加入 renderer 后的新 runtime manifest 不自动继承旧单资产 lock 的
资格；需要新锁时须 fresh 签发，不回填旧 run。

结构、依赖或媒体不完整时 raise；真实物理不合格时保留 failed/inconclusive report。分析用 trace fixture
只验证数学，真实 runner、CAS 运行摘要与失败记录另由 Golden progress 和 provenance 账本记载。
