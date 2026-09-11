# Gujie 场景接入与软件渲染双时间步实跑

本次为 M1 的真实场景运行切片，尚不是完整图像 Golden line 或生产场景资格。
固定 Gujie 来源 `eb0b710581fd7794bc01b447b0f77cb871c8a711` 的坐标转换、单刚体几何、
有限支撑布局与物理判据已适配到 Harness；运行不使用旧 pass 报告作为成功依据。

运行目录：`/home/jingxiang/bingsheng/golden-scene-software-20260911.gu2pwrnc`。
真实输入为 Gujie mouse 场景、15-member representation、24,573 个完整 visual/collision 顶点，
以及有限 Box 桌面。保持来源 XY 与姿态，将底部置于桌面上方 1 mm。

| 实际输出 | baseline | half_dt |
| --- | --- | --- |
| dt / steps | .004 s / 1,000 | .002 s / 2,000 |
| 含初态连续行 | 1,001 | 2,001 |
| PNG / decoded unique | 41 / 24 | 41 / 4 |
| 无损视频逐帧 RGB 与 PNG 一致 | true | true |
| 最大穿透 | .43596 mm | .37623 mm |
| 末段支撑比例 | 1 | 1 |

两条末态差 0.02369 mm / 0.00801°。保存的 `physics-report.json` 是当时的 32-check 格式，
全部通过，authority 为 `trace_consistency_only`，其内 `media=[]`；媒体独立保存在两份
`media.json`。后续已冻结格式增加判据和媒体闭包，必须另跑生成新报告，不能回填本记录。

| 原始文件 | SHA256 |
| --- | --- |
| physics-report.json | `500edbbe29ac6311d62159792c878f4ee8714fafdbecf959a7ce1b1f9735c89d` |
| baseline/media.json | `fd85fb88628fd757cd58bc3abf160863d49c6513fbee5f97a32e3223adba7ced` |
| half_dt/media.json | `dc0e9f2f0e789fdf794a28e10f2f7d666173d805c3349481d69dd94204dee1e3` |
| baseline/preview.mp4 | `f3da017b543923d1a7494107ba7778812d02af514fc60300f899c0e6f425bd5b` |
| half_dt/preview.mp4 | `a7f1bad14830e6c59fa874ccfe33087599b76ca4285da065577107e9d91fad5a` |

软件 renderer 实测 Mesa llvmpipe / LLVM 20.1.2 / Mesa 25.1.7，观测设备仅 `/dev/null`。
Harness 先创建真实 4×4 framebuffer 并确认完整后启用渲染，未改动上游 checkout。
初次 EGL 使用 NVIDIA、强制 Mesa EGL 的 exit 139、OSMesa 初次 framebuffer 失败均保留在
`/home/jingxiang/bingsheng/golden-scene-render-smoke-20260911.cgwjhx5u` 的对应日志。

运行测试见 `tests/self_improving/harness/test_genesis_scene_cpu_backend.py`，原始执行记录为
上述运行目录的 `baseline-test.log` 与 `half-dt-test.log`。合同和来源公式映射位于
`self_improving/harness/GUJIE_SCENE_ADAPTER_CONTRACT.md` 与 `GENESIS_SCENE_REPLAY_CONTRACT.md`。

尚缺新 renderer/native runtime manifest、受约束场景执行来源、生产 image compile 与完整
compile→replay→observe→validate 绑定。本次不签发 scene execution authority，不继承旧资产
lock 的资格，不宣称 M1、环境发布、MCP 图像路径或机器人验证完成。
