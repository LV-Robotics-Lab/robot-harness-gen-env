# 双盲标注工作流

这套工具只完成预注册第 2 步的材料准备、回收和密封。它不运行 VLM、不启动仿真器，也不会把
test gold 自动交给实验运行器。

## 1. 准备私钥

在仓库和模型可见目录之外生成一个至少 32 字节、权限为 `0600` 的文件。这个文件只交给标注
管理员，不能交给两位标注者或模型进程。工具会把自身源码 SHA、冻结 spec SHA、公开 assignment
SHA 和私有 mapping HMAC 一起写入回执；中途换工具源码或换私钥都会失败关闭。

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
打开 test gold 的 `visible_gold_seal.json`。

test payload **只是权限隔离，不是加密文件**。在 A1 prompt SHA 和模型 / processor 回执写入
append-only run log 之前，必须把整个密封目录保留在仓库、provider readable paths 和模型可见目录
之外。候选 manifest 需要人工复核后，通过一个单独可审计提交替换当前 pending manifest，并同步
runner 中的固定 SHA；不能由本工具自动改源码或自动解封。

## 当前状态

工具和真实 39 样本导出烟测已完成；两位真实独立标注者和第三位仲裁者尚未执行。因此当前
`sealed_test_annotation_manifest.json` 仍必须保持 `pending_blinded_annotation`，昂贵推理仍不得开始。
