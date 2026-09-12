# 内部Codex实验建议阶段

`execute_model_stage(stage, parameters, *, store, root, settings, backend)` 是唯一公共seam，调用既有
`CodexBackend.propose`，不创建workflow、操作业务DB或执行模型建议之外的工具。

- interpret从CAS ExperimentInputBundle消费文本、规范图像和视频。视频真实FFmpeg提取首/中/末
  帧，返回各frame_index/source_video/decoded_sequence/image引用；这只是视觉采样，已有完整解码
  ingest仍是视频身份。输出必须通过SceneIntentV1与input hash核验，source还受部署allowed_sources限制。
  本地catalog配置不再仅给模型路径：interpret先读取至多1MB/256 entries的实际catalog快照，
  给出类别和usable记录，并把原始catalog SHA/CAS引用写入catalog_context。缺失、损坏或超限的
  已配置catalog返回blocked_external_resource，模型不调用。未配置catalog且默认非local时明确
  available=false。只有自哈希不可变asset receipt及完整成员字节检查相符时才附上已记录尺寸，
  连同receipt CAS身份；不测量猜造尺寸，不把可用库条目视为视觉匹配资格。
  模型仍自行比较原图与候选语义；只在确实匹配时采用实际category，不能增设cube别名或强制选block。
- visual以最后附图明确标识实际Genesis observation，前面的图片是用户输入；输出status/visual_status/reason、
  input_sha256/scene_sha256、repair_kind（none/scene/asset/numeric）和nullable asset_patch。
  patch只可有scale/mass_kg/friction；passed必须none，非asset不能有patch，asset必须非空。
  程序化physical report作为独立诊断给模型，不把视觉判断变成物理通过。scene数字仅传必要位姿/桌面，
  不把大规模geometry顶点堆进prompt。
  visual_status只描述可见意图；status是联合诊断，实际物理failed由Harness强制保持failed。
  成功执行但物理失败的replay仍可进入观察/诊断；最终validate继续独立重算，不能由模型覆盖。
- revise_scene消费现有intent和dependencies，并读取由service核准的proposal.failure_receipt正文。
  返回完整新intent，但只允许xy_m/yaw_deg实际改变，其余字段完全一致。base_failure_receipt和
  base_dependencies显式记录。修订预算和最近失败验证归controller/service。

每次模型输出带model_evidence CAS引用；model_proposal_receipt指向实际backend result.json，控制器
可据此核对失败视觉建议。模型失败返回failed/model_proposal_failed；结构/身份/非法修订错误明确抛出
给宿主。真实模型日志原目录保留，不将失败变成默认建议。生成器只支持当前primitive子集；该合同
不授予通用三维重建或多物体支持。

2026-09-13小型真实text interpretation在
`/home/jingxiang/bingsheng/generic-real-interpret-e77y9kmo/operation/model/`完成：9.439秒，真实
CodexBackend返回red block/generated/exact、XY[.1,-.05]、yaw20，与用户输入相符。input SHA
`c11a69b489ca5e1c58e8e6c7648684589077b71fbf47ef58f2475d8d6f32616f`；transcript SHA
`26534673dc9fbb383d1e194764a90ca5e0e3fe8bc846dac469d4309cd60e6d80`。这只证明interpret模型
阶段真实通过，不是Genesis或完整工作流。后续visual repair字段由离线合同测试覆盖，未伪称真实visual通过。

公共测试覆盖input替换拒绝、非法source修订拒绝、真实FFmpeg六帧输入的[0,3,5]索引采样，以及
显式模型asset_patch字段消费。受控backend fixture明确external_agent_executed=false。
