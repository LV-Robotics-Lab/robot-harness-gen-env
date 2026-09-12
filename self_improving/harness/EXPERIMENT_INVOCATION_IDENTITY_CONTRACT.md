# 实验调用身份：私有诊断记录

`capture_invocation_identity(workdir, configuration)` 由公共CLI在调用开始时执行，返回普通JSON。
workdir必须为Git worktree根；configuration为JSON配置文件路径或JSON兼容mapping。文件配置
哈希绑定原始字节，mapping哈希绑定排序紧凑JSON；不返回配置原文或Git diff正文。

记录HEAD、porcelain路径状态、`git diff --binary HEAD`哈希、未追踪Python源码与pyproject.toml
字节哈希、实际当前Python解释器/版本及runtime_roots声明。禁用Git外部diff/textconv与子模块
dirty递归检查。未追踪资产/输出/数据/依赖/秘密目录不读取；符号链接与超过2MiB源码只记录跳过。
已追踪diff流式哈希，不执行声明runtime、不扫描runtime闭包、不访问submodule内部状态。

runtime declaration_sha256只绑定声明路径，**不是**runtime字节身份或资格证据；明确
`declared_not_deep_verified`。记录是私有operator诊断，不应发布dashboard。不替代现有源码
authority，不能授权Skill或正式发布。快照非原子；并发编辑仍由正式调用的authority校验处理。
Git/config读取失败抛错，由调用者记录失败，不制造默认身份。

测试使用微型真实Git仓库，覆盖tracked diff变化、未追踪源码哈希、配置原始字节哈希、秘密值
不出现在结果、符号链接拒绝。它们不构成新runtime运行或深验通过。
