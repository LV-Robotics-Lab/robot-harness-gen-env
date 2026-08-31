# 双盲标注工作流

这套工具只完成预注册第 2 步的材料准备、回收和密封。它不运行 VLM、不启动仿真器，也不会把
test gold 自动交给实验运行器。

## 1. 准备私钥

在仓库和模型可见目录之外生成一个至少 32 字节、权限为 `0600` 的文件。这个文件只交给标注
管理员，不能交给两位标注者或模型进程。工具会分别绑定 `annotations.py`、`protocol.py` 的源码
SHA，并计算组合 implementation SHA；冻结 spec SHA、公开 assignment SHA 和私有 mapping HMAC 也会
进入同一承诺链。中途换任一实现源码或私钥都会失败关闭。

## 2. 导出两份盲标包

```bash
python -m self_improving.studies.vlm_fallback_prompt_optimization.annotations export \
  --spec self_improving/studies/vlm_fallback_prompt_optimization/experiment_spec.json \
  --repo-root . \
  --public-root /path/to/share/visible-annotation \
  --private-map /secure/path/assignment-map.json \
  --blind-key-file /secure/path/blind.key
```

`visible-annotation/rater-1` 和 `rater-2` 分别交给两位标注者。每份包只有匿名 item id、任务意图、
图片和八项检查，不含 case id、split、group、源路径、runtime report、arm、模型输出或另一人的答案。
两人的 item id 与排序不同。标注者填写各自的 `ratings.template.json`，每项只能是：

- `pass`
- `fail`
- `not_applicable`
- `insufficient_view`

不要把 `assignment-map.json` 或 blind key 放进公开包。

## 3. 只仲裁分歧

```bash
python -m self_improving.studies.vlm_fallback_prompt_optimization.annotations adjudicate \
  --private-map /secure/path/assignment-map.json \
  --public-root /path/to/share/visible-annotation \
  --rater-1-ratings /secure/path/rater-1-ratings.json \
  --rater-2-ratings /secure/path/rater-2-ratings.json \
  --adjudication-root /path/to/share/adjudication \
  --adjudication-map /secure/path/adjudication-map.json \
  --blind-key-file /secure/path/blind.key
```

输出中的 `disagreement_count` 大于 0 时，把 adjudication 包交给第三位标注者。第三人只看到有分歧
的检查和原图，看不到前两人的选择。如果计数为 0，密封时不要传任何 adjudication 参数。

## 4. 密封

有分歧时：

```bash
python -m self_improving.studies.vlm_fallback_prompt_optimization.annotations seal \
  --private-map /secure/path/assignment-map.json \
  --public-root /path/to/share/visible-annotation \
  --rater-1-ratings /secure/path/rater-1-ratings.json \
  --rater-2-ratings /secure/path/rater-2-ratings.json \
  --adjudication-root /path/to/share/adjudication \
  --adjudication-map /secure/path/adjudication-map.json \
  --adjudication-ratings /secure/path/adjudication-ratings.json \
  --output-root /secure/path/sealed-gold \
  --blind-key-file /secure/path/blind.key
```

密封目录权限为 `0700`，其中 gold 文件为 `0600`。工具会输出 train、dev、test 三个不同载荷，
逐 check Cohen's kappa、raw agreement、分歧数、三份回答 SHA、候选 annotation manifest 和尚未
打开 test gold 的 `visible_gold_seal.json`。当 Cohen's kappa 因期望一致率为 1 而没有定义时，值为
`null`，并在 `cohen_kappa_undefined_reason_per_check` 中记录原因，不能解释为 1.0。test payload
显式绑定 train gold SHA；候选 manifest 再绑定 test payload SHA，因此 train gold 也处在最终承诺链中。

工具在接受每一份回答前会重新读取并核对 assignment 中的每个媒体视图，并在发布边界再次完成同样
检查（包括仲裁包）。assignment、私有 mapping 和 test payload 的 v2 schema 固定了新增的实现身份与
摘要链字段；旧 v1 文档不会被静默当作 v2 接受。`--output-root` 必须同时与公开 rater assignment 树和
adjudication assignment 树隔离：不能等于、位于或经符号链接进入任一共享树，即使路径随后再逸出也会
拒绝。输出父目录必须由当前进程用户拥有且权限仅限 owner；每一级祖先必须由 root 或当前进程用户
拥有，且不能对 group/other 可写（由上述受信任主体拥有的标准 sticky 临时目录除外）。同一系统身份
是受信任的单写者，在 seal 期间不得并发改名或移动该父目录及其祖先。所有文件先写到目标同一文件
系统上的私有 staging 目录，
全部检查通过后才用 no-replace 原子改名发布；工具会在提交后再次核对 pinned 父目录，发现漂移时回滚。
在上述单写者边界内，写入、复验或发布失败会清理 staging，目标目录不会以半成品状态发布，可安全
重试。若越界的同身份并发移动与回滚 I/O 失败同时发生，工具会明确报告 `quarantine required` 并且
不会签发 SealReceipt；操作员必须先隔离错误中指出的目录，不能把该次调用当作普通可重试失败。

test payload **只是权限隔离，不是加密文件**。在 A1 prompt SHA 和模型 / processor 回执写入
append-only run log 之前，必须把整个密封目录保留在仓库、provider readable paths 和模型可见目录
之外。候选 manifest 需要人工复核后，通过一个单独可审计提交替换当前 pending manifest，并同步
runner 中的固定 SHA；不能由本工具自动改源码或自动解封。

## 当前状态

旧版流程曾完成真实 39 样本导出；当前 v2 contract 的 CPU/攻击测试已完成，但真实 39 样本 v2 重跑
因工作树缺少一份冻结的 portal 图像而尚未完成，不能沿用旧烟测冒充 v2 证据。两位真实独立标注者和
第三位仲裁者也尚未执行。因此当前 `sealed_test_annotation_manifest.json` 仍必须保持
`pending_blinded_annotation`，昂贵推理仍不得开始。
