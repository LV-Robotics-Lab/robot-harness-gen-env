# Golden legacy mouse representation staging — 2026-09-11

固定实现：`2ddd5e619edf074b80b6563a134b549fba4cc97d`。

Gujie 历史 mouse archive 的 `asset.json`（SHA-256 `a7d20cc6...f21c`）声明 15 个成员，合计
5,326,332 bytes。`stage_legacy_genesis_asset_representation` 在任何 CAS 写入前验证 manifest schema、完整且
排序唯一的相对路径、每个 member 的 size/SHA、目录无缺项/额外项/symlink/特殊文件，并把已验证字节冻结到
受控 snapshot；CAS 只从 snapshot 发布，不再重读可变 source。

真实运行得到 16 个 CAS 对象、5,334,039 bytes：15 个 member 加一份 canonical
`AssetRepresentationV1`。representation self-hash 为
`ebf0f616e4336f4ad2272e6150c166a2686e60b3154b9cf50a3e403cc4693f2e`，CAS ref 为
`bf71acba05d383593b287ed1bce0efd31e8bbc13a46d35801c7e7e2189add1e9`，loader closure 为
`0c23bb38487aa48fa734734d97fbaa847c05b0976a40fb97eb0b6e76d9d50904`。同源重提零增长；不同绝对
source 路径与权限得到相同 representation 和 CAS tree。

独立验收 37 pass；新模块 184 statements / 64 branches 100%。验收在 verify→首次 CAS put 窗口分别修改
原 source member、manifest、增加 extra、替换 symlink，四例仍只发布预验证 snapshot，15-member identity
零漂移。静态 manifest/member/subject/symlink/extra/特殊文件负向用例均在首写前拒绝且 CAS 为空。

这份输出刻意没有 runtime lock、qualification、status/pass、simulator 或 publishability 字段；它只证明
可移植 byte representation。固定 Gujie 源码唯一现成 probe 会强制 Rasterizer/视频/EGL，且当前 editable
Genesis/Python 环境相对 lock 漂移。因此下一步是先冻结四层 typed asset-probe schema，再实现 Harness-owned、
无相机的 CPU load/contact issuer；旧 physics pass 仍不得迁移。
