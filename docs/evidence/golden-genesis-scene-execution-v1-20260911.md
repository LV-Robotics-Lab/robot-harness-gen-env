# Genesis 有限桌面场景执行 v1

本次固定 Gujie mouse exact-reuse candidate，实际执行受约束的 Genesis baseline / half-dt，
并将运行进程、完整轨迹、PNG、无损视频和 34 项环境检查绑定到 CAS。该运行用于证明低层
scene producer 与 renderer runtime，尚未签发最终 `image2env.replay@2.0.0` Skill 资格。

运行根：`/home/jingxiang/bingsheng/golden-scene-constrained-20260911.f92x5_zn`。
小型摘要为 `v1-scene-acceptance-summary.json`。runtime manifest
`e37a013bd95e9c9b46914477df5da07d66ebfb555d3323e17d8b02f15ae95828`
声明 165,812 members / 8,569,494,006 bytes / 19 个固定 backend 文件，并已从 CAS 深读和
部署目录逐 bytes 重验。

## 保留的失败

首次正式 candidate 运行 123.85 秒后以 `runtime member inventory or bytes differ` 失败，
run UUID `bbd7a12d-ca7e-4d4a-be4b-644f8144da4c` 保持 failed terminal，未生成执行回执或资格。
秒级差异只发现 8 个额外 `cpython-313.pyc`；19 个固定源码、missing members 和另外五个 roots
均无漂移。因果复现证明是一次宿主负向诊断漏用 `-B` 写入缓存。8 个文件带 SHA/bytes/mtime
移入 `quarantine-formal-1` 保留；同诊断加 `-B` 后 0.465 秒通过，完整 165,812-member 部署门
又在 56.317 秒通过。manifest 与请求均未重签或改写。

- `scene-candidate-formal-1.log` SHA256：
  `3b1a3931cc2c3bfc52f7b3b7e123eee8b2dc9717294689afa3ee34c9c234b893`
- `formal-1-inventory-diff.json` SHA256：
  `4edbdb126f6f9cf3c7bc5eaba2e36ca61d4f24e53651b1fb56ba4193b707780f`
- `scene-manifest-summary.json` SHA256：
  `4a3e6f7e5729488af7528b664518cdcd295360cb1debe9a6bafb099e58715dd7`

## 正式成功

新 run UUID `f0698892-d75c-4409-b4ed-6b8d211a82a0` 在同一 manifest 与请求上完成：

| profile | rows | PNG | unique | modules | native | confinement |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| baseline | 1,001 | 41 | 24 | 3,186 | 299 | Landlock 8；network/write 拒绝 |
| half_dt | 2,001 | 41 | 4 | 3,184 | 299 | Landlock 8；network/write 拒绝 |

外部 native 数均为 0。两条轨迹的无损 MKV 与 41 PNG 逐像素一致，34 checks 全部通过。
正式 pytest 为 712.26 秒，应用执行为 711.949 秒。

- execution receipt：`5816ab222ee27b39998ad1d9f990838d543d0621f07144130c56fd32b9cce192`
- replay：`551f475c598521e1f79cd76948a7816d6d258cddddd15e775789273fd53c2bcb`
- report：`4d28c0a2875c396eb03732d84eac015e266bfa11fdb5bc5a1507188e175bff14`
- launch result：`601480bda34fa8d33896aa069e90773abe67a7571c6d0fe05b93c9254fdc015e`
- `scene-candidate-formal-2.log` SHA256：
  `bddad44ec8b2e74e17666ff6aa3a0808c58b41e6363d49d24199c98201d063ca`
- `formal-2-review-pointers.json` SHA256：
  `d9c756fefa6266c8c51e677464925ade967030231ba2f43e83348ee616f4be14`

v1 的 MP4 preview 在本地和 retained CAS 索引中可观看，但 launch result 没有直接引用它；
因此权威闭包只声明 PNG + 无损 MKV，不能把 MP4 当 v1 可搬迁输出。v1 也没有精确的实际
frame capture wall-clock event，不能签发 fresh observation。下一 runtime v2 将一次加入可达
preview、retained index 和真实 capture event，并用生产 compile 的可信终态重新执行。

该运行消费早期 native compile receipt `2777c9a2…`，只用于下层执行资格证据；M1 最终链必须
从已注册 production compile 的 run UUID 和终态发起 v2 replay。当前结果不表示完整 M1、
observe/validate、MCP、promotion 或 robot policy 已完成。
