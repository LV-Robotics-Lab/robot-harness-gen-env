# 通用实验输入与共享场景意图

本切片属于 2026-09-12 通用实验管线，沿用唯一 Harness workflow。输入是非空文本、一张或多张
图片、一个视频及其组合。`ExperimentInputs.ingest` 将原字节写入 CAS，图像复用 ImageMediaIngestor，
视频复用 VideoMediaIngestor 的完整解码合同；记录实际路由 text/image/video/multimodal。
视频的模型视觉样本明确只是样本，完整 source identity 不降格为图片。

`accept_intent` 只接受模型提出且绑定该输入的 SceneIntent；它没有物理或资格权威。首个可执行
profile 是单个动态刚体、水平有限桌面，明确拒绝多物体/关节/未识别支撑。文本约束由 Codex 与
全部媒体一起解释，冲突保留在 unknowns 并拒绝编译。资产来源 local/web/generated 和 exact/cousin
选用分别表达。位置、朝向、尺寸必须有限且有界；不得携带代码、路径、资格或成功结论。

输入/意图成功只表示组件完成，不表示 Genesis 或端到端通过。原始失败和部分产物由现有 workflow
journal 调用者保留。场景修订最多两次，需引用 base intent 与失败证据，不修改原输入。
