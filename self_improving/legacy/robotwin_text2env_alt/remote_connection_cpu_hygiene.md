# Remote Connection CPU Hygiene

这条规则用于之后所有远程机器 / VS Code Remote / SSH 项目操作：避免因为打开超大目录、文件监听、搜索索引、Python analysis 扫描数据集而把远程 CPU 拉满。

## 默认原则

1. VS Code Remote 不打开超大根目录，只打开具体项目子目录。
2. 搜索不要跟随软链接：`search.followSymlinks: false`。
3. 把数据集、输出、checkpoint、日志、venv 等目录加入 exclude。
4. 我在远程机器上用 CLI 搜索时，也要优先做有边界的查找，并 prune 大目录。
5. 除非明确需要，不从 `/data`、`/data/sdb`、home 根目录或磁盘根目录做无边界递归扫描。

## 建议写入 VS Code Remote 的 `.vscode/settings.json`

```json
{
  "search.followSymlinks": false,
  "search.useIgnoreFiles": true,
  "search.useGlobalIgnoreFiles": true,
  "search.exclude": {
    "**/.git": true,
    "**/.venv": true,
    "**/venv": true,
    "**/node_modules": true,
    "**/__pycache__": true,
    "**/outputs": true,
    "**/checkpoints": true,
    "**/datasets": true,
    "**/data": true,
    "**/logs": true,
    "**/wandb": true
  },
  "files.watcherExclude": {
    "**/.git/**": true,
    "**/.venv/**": true,
    "**/venv/**": true,
    "**/node_modules/**": true,
    "**/__pycache__/**": true,
    "**/outputs/**": true,
    "**/checkpoints/**": true,
    "**/datasets/**": true,
    "**/data/**": true,
    "**/logs/**": true,
    "**/wandb/**": true
  },
  "python.analysis.exclude": [
    "**/.venv",
    "**/venv",
    "**/data",
    "**/datasets",
    "**/outputs",
    "**/checkpoints",
    "**/logs",
    "**/wandb"
  ]
}
```

## 远程命令习惯

查找仓库或文件时，优先使用类似下面这种带 prune 的命令：

```bash
find /data/sdb/zhengye \
  \( -path "*/miniconda3/*" -o -path "*/.cache/*" -o -path "*/.local/share/*" -o -path "*/data/*" -o -path "*/datasets/*" -o -path "*/outputs/*" -o -path "*/checkpoints/*" -o -path "*/logs/*" -o -path "*/wandb/*" \) -prune \
  -o -maxdepth 5 -type d -iname "*robotwin*" -print
```

