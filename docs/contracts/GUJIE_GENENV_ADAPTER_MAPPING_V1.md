# Gujie `genenv.*` → Harness X2Env adapter mapping v1

- 状态：Mapping specified；adapter unimplemented
- 核对日期：2026-09-10
- 主集成基线：`2bdeafd836d641b4f14aef316a21fbcd736f55e1`
- 仓库内旧 ref：`worktree/gujie@a25bc58853db1988aafafe88b544e0573eacbb3c`
- 权威独立来源：`/home/jingxiang/gujie/gen-env@eb0b710581fd7794bc01b447b0f77cb871c8a711`
- 重点祖先：`9e8d28c24ca72ebf4001c2460dc4b174cef603c0`、
  `a8ced2732b48d3eb89e5c40d6cd7fd71b275f178`

本文只描述固定提交中真实存在的输入、输出和 adapter 映射。来源工作区当前还有 modified
`external/SimFoundry` 与 untracked `external/WorldComposer/` 等内容；这些 dirty 字节不属于上述 ref，
不进入本合同。提交记录中的 git authors 与团队所称 Gujie 责任名分别记录，不推断自然人映射。

## 1. 已核对入口和真实行为

| 固定来源入口 | 输入/输出 | 已存在行为 | Harness 结论 |
|---|---|---|---|
| `reconstruct_media.py:probe_media` | 图片或视频文件 → `genenv.media_reconstruction.v1` dict | regular/non-symlink 与扩展名检查；SHA-256/bytes；图片完整解码和尺寸；视频 ffprobe 完整计帧、ffmpeg framemd5、尺寸/FPS/时长、总/唯一帧数和 15 帧采样索引 | 可复用核验方法；原始 path 不入 CAS；video 全帧 hash 当前没有持久化，adapter 必须重新解码并写完整 frame-sequence manifest |
| `reconstruct_media.py:_normalize` | 输入媒体 → copied original / PNG | image 转 RGB PNG；video 保留原容器；单图 runner 另造 `source.MOV` 路径以兼容上游 | runner 兼容路径不是媒体 identity；Harness image identity 以原始 bytes + canonical PNG 两个 ArtifactRef 表达 |
| `reconstruct_media.py:task_config/run` | 媒体、CLIP index、VLM config、SimFoundry args → staged task | 绑定输入/config/实现/upstream hashes；调用 SimFoundry 和 native model；objects/scene/physics/final render 四阶段，可 resume | 不能直接成为 exact Skill handler：内部 model 调用违反 external-Codex seam；只可导入已经固定的 directory snapshot，或拆出无模型 deterministic adapter |
| `reconstruct_media.py:verify_sampled_frames` | SimFoundry frame dirs + media facts | 核对 frames_all 总数和 1/15 个 sampled frame 的索引与 bytes | 证明上游 sample 对应，不证明完整帧 sequence 已持久化；VideoSourceMedia 仍须 full manifest |
| `media_support.py` | observations/index/candidates → selected/materialized asset | geometry gate、候选排序/选择、颜色对比、footprint 物化与 support 加入 | 映射为每对象 AssetDecision；selection/render 不等于 asset qualification 或 runtime pass |
| `media_checkpoints.py` | stage/config/dependency → checkpoint | 对配置变化失效下游 stage，并校验 stage snapshot | 映射为 Harness operation receipt/idempotency；legacy checkpoint 不是 Harness trust root |
| `task_output.py:TaskOutput` | request → owned stage directory/manifests | request hash、安全目录名、owner、锁、stage files 和全任务 verify | 整个目录先做 immutable CAS snapshot；绝对 root 和 legacy status 丢弃/仅入 compatibility report |
| `standard_urdf.py` | URDF + referenced mesh closure | 单 link/no joint、mesh closure、mass/inertia/geometry 与 Genesis morph 辅助 | 作为 asset adapter/qualification 候选；第三方 bytes/许可和每成员 digest 必须重建 Harness closure |
| `import_simfoundry_scene.py` | SimFoundry environment/object/pose | 转换 ground、对象、pose、标准 URDF、scene layout/manifest，并可 Genesis preview | 转为 SceneIR/TaskIR/package；坐标系、pose format、ground 与 locator 必须显式规范化；preview 非 replay |
| `position_solver.py` / `scene_stabilization.py` | scene/layout → bounded placement / intervention | 有界布局求解与稳定化候选 | seed/attempt/失败原因入 receipt；intervention 状态不得作为自由 replay pass |
| `validate_imported_scene.py` / `physics_criteria.py` | native scene → trace/final state/physics report | Genesis collision、非零 step、接触/grounded force、位移/转动、穿透、支撑和数值判据 | 是 replay/validate production adapter 候选；旧 report 只能作为 legacy diagnostic，必须在当前 run 重跑 |
| `scene_physics_workflow.py` / `replay_text_half_dt.py` | package → position/stabilize/baseline/half-dt evidence | 分阶段 checkpoint、两种时间步自由回放、agreement/verify | 可作 rigid-scene qualification 保护门；不包含 robot-policy reset/action/observation/trajectory |

来源 tests 覆盖媒体 probe（示例视频 124 decoded/124 unique、15 sampled）、resume/config drift、task
output、URDF closure、scene import、位置求解、接触/穿透攻击、baseline/half-dt 和多项真实 Genesis 标记
测试。来源 `MEDIA_RECONSTRUCTION_EVIDENCE.md` 明确记录：已有单图复用资产后的真实物理通过案例，
但未重新运行全重建且不覆盖视频输入。这些结果尚未绑定当前 Harness workflow。

## 2. Runtime dependencies and upstream identity

- system：`ffmpeg`、`ffprobe`；
- Python：Pillow、NumPy、SciPy、trimesh、coacd、rtree、fast-simplification、OpenCV；可选 CLIP 路径
  另用 transformers/torch；
- SimFoundry gitlink：`9e34ebefcd020583fbb755a8b57268dce78eca26`；
- Genesis World gitlink：`0e74bf392781884ccad765c3f344419c86b872ca`；
- 来源代码还依赖 `scene_gen.llm_provider` 与 SimFoundry native model endpoint。目标 exact Skills 禁止
  在内部调用这些模型；外部 Codex proposal 与已冻结 reconstruction snapshot 是允许的输入 seam。

## 3. Field mapping

| Legacy field/artifact | Harness target | Mapping rule |
|---|---|---|
| `mode=image` + input bytes | `ImageSourceMedia.source` | bytes 重新写 CAS，SHA 必须等于 legacy sha；扩展名不作为 MIME 权威 |
| normalized `source.png` | `ImageSourceMedia.canonical_image` | 重新解码并以固定 encoder 产生 RGB/RGBA/L PNG，再写 CAS |
| image width/height/counts | `ImageSourceMedia` + `image_media_probe.v1` | 从 CAS 原图重新探测；固定 1/1，不信任 JSON 单独声明 |
| `mode=video` + input bytes | `VideoSourceMedia.source` | 容器 bytes 写 CAS并重算 hash/MIME |
| video width/height/duration/FPS/counts | `VideoSourceMedia` + `video_media_probe.v1` | 对 CAS 容器完整探测/解码；float seconds 转 checked integer milliseconds；FPS 解析为正整数分数 |
| ffmpeg per-frame hashes（来源函数内未持久化） | `video_frame_sequence.v1` + `frame_sequence_sha256` | adapter 必须重新完整解码、按顺序持久化每帧 identity，再计算 manifest digest；15 帧 sample 只作交叉检查 |
| `support_selection.json` | `X2EnvAssetDecision` | selected candidate 与拒绝原因进 decision evidence；exact/digital cousin 必须由新 qualification/equivalence gate 判定，不能从 similarity 分数推断 |
| generated/reconstructed object dirs | staged asset ArtifactRefs | 全目录逐成员入 CAS；标为 fresh generation candidate，不直接入权威 catalog |
| standard URDF + meshes | asset closure manifest | POSIX logical relative paths、每成员 hash、第三方来源与许可；拒绝绝对 locator、symlink、缺成员 |
| imported `scene_layout.json` / geometry / task metadata | SceneIR + TaskIR | 显式坐标系、transform、support/containment/attachment、collision、physical properties 与 task contract；未知字段进 compatibility blockers |
| legacy task manifest/checkpoints | legacy adapter receipt | 保存 source ref、directory snapshot、映射版本、ignored fields、conflicts 和输出 refs；不当作 current compile/replay receipt |
| legacy `physics_status`, trace, final render | legacy diagnostic refs | 绝不映射为 current runtime evidence、validation pass、simulator-ready 或 publishable；current replay 从新 root 重跑 |

## 4. Status and format translation

适配成功只产生：`representation_origin=legacy_genenv_adapted`、非空
`legacy_adapter_receipt`、`environment_format=harness.genesis_portable.v1`、
`simulator_ready=false`、`collision_qualified=false`。任何 unknown legacy schema、status 冲突、绝对路径
逃逸、缺 asset member、媒体事实与 CAS bytes 不一致都 fail closed，并进入 compatibility report/blocker。

只有后续 exact replay 在固定 Genesis/runtime/adapter identity 下真实 load/build/collision/nonzero-step，
再由 exact validate 从 portable CAS closure 重算，才可能得到 publishable。Legacy `passed`、final render、
process exit 0 或文件名中的 `genesis` 都不能跨越这两道门。

## 5. Committed mapping fixtures

- `tests/fixtures/legacy_genenv_image_media_manifest.json`
- `tests/fixtures/legacy_genenv_video_media_manifest.json`

fixture 复现固定来源 `probe_media` 的字段形状，不包含实际媒体 bytes 或私有输出。video fixture 刻意只含
legacy sampled facts、不含 full frame hash list，锁定 adapter 必须补做完整解码 manifest 的差距。未来
adapter RED 应从这两个 raw fixture + 单独 CAS media fixture 出发，不能把 fixture JSON 自己当运行证据。
