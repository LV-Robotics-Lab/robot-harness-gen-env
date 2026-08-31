# Replay media verifier qualification — 2026-08-31

这份证据只回答一个问题：Harness 在把 replay 的 PNG/MP4 晋升为类型化制品前，能否在一个
受资源限制、无路径读取能力的本机沙箱中真实解码媒体，并验证帧数、帧率、尺寸、采样宽高比与
解码后互异帧。它不证明 900/120 物理门已经通过，也不赋予整个 `text2env.replay` 发布资格。

## 为什么不能只信扩展名或 runtime JSON

初始 handler 的测试替身曾把 `b"image:<filename>"` 当作 `.png`/`.mp4`，旧实现仍会按扩展名
发布为 `image/png` 或 `video/mp4`。这意味着 worker 即使声称 120 帧、100 个互异帧，也不能证明
CAS 中真有一段可解码录像。新边界先把输出保存为不受信任的 octet-stream，再由本 verifier
完整解码；只有解码事实与 replay contract 一致时才晋升媒体类型。

## 失败过的方案与保留决策

1. **动态 FFmpeg + 进程超时**：仅绑定 `/usr/bin/ffmpeg` ELF 不能绑定动态 loader、214 个 DSO、
   ld cache/preload 与宿主 CPU 行为；timeout 也不能防瞬时 OOM。此方案被拒绝。
2. **主进程 Pillow 解码**：临时改写 Pillow 全局 decompression-bomb hook 会影响其它线程；主进程
   也没有独立内存/pid/CPU 边界。此方案被拒绝。
3. **从普通 session scope 迁移子进程**：即使目标 cgroup 归当前用户所有，从 root-owned source
   scope 写 `cgroup.procs` 仍实测 `Permission denied`。生产前置因此固定为已经委派的
   supervisor/jobs topology，缺失时返回 `cgroup_unavailable`，不降级。
4. **通用静态 imageio FFmpeg**：在 Landlock 只允许 executable、seccomp 拒绝文件打开与随机源时，
   该 binary 因 `std::random_device` 失败而退出。没有把 `/dev/urandom` 放宽进信任面，而是重新构建
   不依赖随机设备的最小静态 FFmpeg。
5. **把 framemd5 叫做 yuv420p**：真实 full-range H.264 反例说明 raw frame 大小只能证明 8-bit
   4:2:0 sample layout，不能区分 yuv420p/nv12/yuvj420p。最终证据只声明 `8bit-420`。

## 固定工具链与执行边界

- FFmpeg source：`https://ffmpeg.org/releases/ffmpeg-7.0.2.tar.xz`
- source tarball SHA-256：
  `8646515b638a3ad303e23af6a3587734447cb8fc0a0c064ecdb8e95c4fd8b389`
- compiler/linker：Ubuntu GCC `13.3.0-6ubuntu2~24.04.1`、GNU ld `2.42`
- qualified static FFmpeg：4,337,384 bytes，SHA-256
  `fe08d0f51873874056abe5be5eb1d1047a1cc3eb3da0c41907047be5581ef02e`
- native launcher：790,240 bytes，SHA-256
  `092597d14624759011d8c3f53d4f203ea6a345541bac1e2075b263247694d7ec`
- launcher source：9,524 bytes，SHA-256
  `e819aaf36ade98ae01f42e3581205392bde55649fff7132f61b41d7ea776e54c`
- sandbox identity：
  `dc03f7db9d671cfe23ea23e78698a05a56867f72eeb0941393c42350345fd223`
- verifier identity：
  `aca09b208fb2bddab566b90d1327a6ffc4d029dec6f65e31018081b60d1bd699`

FFmpeg 是 `statically linked` ELF，且无 `PT_INTERP`/dynamic section。构建只启用 fd/pipe、MOV、
PNG、H.264、rawvideo、framemd5 与必要 scale/format/zlib；禁用网络、设备、自动探测和其它 codec。
媒体通过父进程持有的 seekable regular-file FD 传入，不把路径交给 decoder。native launcher 在
release 前进入 per-run cgroup，再施加 Landlock、seccomp、`no_new_privs`、rlimit；seccomp 拒绝
open/openat/openat2/creat、getrandom 和网络系统调用。每次运行记录 memory/pids/CPU 与 OOM 事件。

本机 delegated smoke 使用下面的 supervisor/jobs 拓扑；普通未委派会话按设计 fail closed：

```bash
systemd-run --user --scope -p Delegate=yes --expand-environment=no bash -c '
set -eu
IFS=: read -r hierarchy empty relative < /proc/self/cgroup
root=/sys/fs/cgroup${relative}
mkdir "${root}/supervisor" "${root}/jobs"
echo $$ > "${root}/supervisor/cgroup.procs"
echo "+cpu +memory +pids" > "${root}/cgroup.subtree_control"
echo "+cpu +memory +pids" > "${root}/jobs/cgroup.subtree_control"
export MEDIA_SANDBOX_DELEGATED_ROOT="${root}"
export MEDIA_SANDBOX_STATIC_FFMPEG=/tmp/media-ffmpeg-qualified-v7.0.2/ffmpeg
cd /home/jingxiang/bingsheng/robot-harness-gen-env
exec python -m pytest -q \
  tests/self_improving/harness/test_media_sandbox.py \
  tests/self_improving/harness/test_media_verifier.py \
  --cov=self_improving.harness.media_sandbox \
  --cov=self_improving.harness.media_verifier \
  --cov-branch --cov-fail-under=100 --cov-report=term-missing
'
```

## 真实历史录像结果

输入是 committed study 下既有 can-on-plate 900/120 evidence 的 7 张 PNG 与 MP4；输入 aggregate
digest 为 `2146ba8ab8830d42eeb702913d45dcde7a71595cd281cd78e9fff81436503761`。

| 观测 | 结果 |
| --- | ---: |
| 解码帧数 | 120 |
| 解码后互异帧 | 114 |
| 帧率 | 12 fps |
| 尺寸 | 320 × 240 |
| SAR | `0/1`，按 FFmpeg 语义记录为未声明、默认方形 |
| 可证明 pixel fact | `8bit-420` |
| 8 次 sandbox invocation 峰值内存 | 11,485,184 bytes |
| 峰值 pids | 22 |
| CPU usage 合计 | 113,549 µs |
| OOM / OOM-kill / pids.max | 0 / 0 / 0 |

攻击样本稳定拒绝 AVI 改名、MPEG4-in-MP4、yuv444、SAR 2:1、额外 stream、结构 bitflip、超时、
输出洪泛、OOM、pids、CPU、SIGSYS 与输入 TOCTOU。full-range/yuvj420p 被接受，但只报告上表中可
证明的 `8bit-420`。未委派环境分别返回 sandbox `cgroup_unavailable`、verifier
`sandbox_unavailable`；没有退回主进程解码。

## 回归与边界

- Python 3.13 delegated：`211 passed`，两个模块 statement/branch 均 `100%`。
- Python 3.11 delegated：`211 passed`。
- wheel + isolated install：`2 passed`；native launcher source 由 `native/*.c` package-data 携带并
  逐字节复核。

静态 FFmpeg binary 与 source tree 没有提交进仓库；生产装配必须由 operator 提供与 qualification
摘要完全一致的 binary 和 delegated cgroup root。这里完成的是可供 replay handler 使用的可信媒体
解码边界；handler/dependency 接线、正式 900/120 receipt、独立 validate 与 promotion evidence
仍需后续切片完成。
