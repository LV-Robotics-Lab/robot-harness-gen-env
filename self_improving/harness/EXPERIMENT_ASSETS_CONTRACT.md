# 开发实验资产获取与修订

公共 seam 为 `acquire(AssetRequest, staging_root) -> AssetVersion` 与
`revise(AssetVersion, patch, staging_root) -> AssetVersion`。调用者是 Harness controller；本模块
不创建 workflow，不签发 qualification，不修改旧资产。结果是尚未物理验证的单刚体候选。

`AssetRequest.source` 是 `local/web/generated` 获取来源，`selection` 独立记录 `exact/cousin`
选用策略。selection 记录调用者的策略，并非本模块授予语义匹配资格。

- local 使用已有 `RoboTwinLocalProvider.search_phrases` 查询调用者配置的 catalog；当前接受
  usable 条目的自包含 GLB。来源 mesh 必须在条目的 asset root 内；不直接信任 usable 为物理证据。
- web 使用已有 GitHub tree provider、`download_candidate` 和 `stage_web_candidate`，仓库限定
  KhronosGroup/glTF-Sample-Assets。先把 main 解析为固定 commit，或接受调用者指定的完整 commit；
  同名 GLB 的逐模型许可只允许 CC0-1.0 / CC-BY-4.0，保留许可来源；下载上限20MB。
- generated 实际调用稳定 `ensure_assets_for_scene`，只接受 block/cube/cylinder/
  hexagonal_prism/octagonal_prism。其输出明确是 procedural proxy，不能替代任意语义物体重建。

共同规范化保留 visual GLB，计算碰撞凸包 OBJ、测量尺寸、写相对引用 URDF；坐标将几何底面放到
z=0，XY中心归零。scale 表示调用者明确的单位/尺度转换，范围[0.01,100]；归一化后各维须在
(0,5]m。质量(0,100]kg、摩擦[0,2]与包围盒惯量为估计。摩擦在 receipt 中声明，运行 adapter
必须显式应用；URDF 不替代运行时的摩擦设置。

不可变版本位于 `versions/<receipt digest>/`。receipt 绑定所有成员 SHA256/大小、来源、许可、
参数、尺寸与 checks；引用是包内相对路径。重复版本必须重新比较全部字节。GLB 的外部引用与
version symlink 均拒绝。`geometry/references=passed` 只证明该项静态检查，`physics=not_run`。

revise 仅允许 scale/mass_kg/friction，先重验旧 receipt/member，输出新 digest 和 parent_version，
原版本不变。每次 acquire/revise 保留独立 attempt 目录、结果/失败 JSON 和 wall_seconds；错误为
`AssetAcquisitionError(code, detail, partial_root)`。同问题至多2次的预算与停止规则由 controller
负责，模块不偷偷重试。

依赖是现有 scene_gen、已提交 asset pipeline/openxsim、NumPy、trimesh、SciPy。没有新增 vendor。
独立 Git worktree 的 provider import root 指向同一 worktree 的已提交源码。当前没有调用
SimFoundry/Gemini；其模型重建 adapter 尚待 Codex 化，不能据程序化生成宣称完成重建服务。

测试入口 `tests/harness/test_experiment_assets.py`；联网测试显式设置
`EXPERIMENT_ASSETS_WEB=1`。测试穿过真实生成器、local provider、下载/转换与可读版本，不注入
固定成功回执。全流程 Genesis 回放由集成调用者另行验证。
