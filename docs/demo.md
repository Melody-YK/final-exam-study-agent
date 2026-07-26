# 本地演示脚本

## 演示前检查

```bash
make sync
./scripts/run_local_rc.sh --check
```

`--check` 返回 `77` 表示 Docker 或某个本地进程不可用。它不会自动修复或启动环境。需要显式启动一次性 smoke 时运行：

```bash
./scripts/run_local_rc.sh --smoke
```

脚本不会执行浏览器自动化、调用真实 Provider、下载 Paddle 模型、登录服务器、执行 Compose teardown 或删除 volume。若本机已有通过 warmup 的隔离 Paddle profile/cache，Worker 会通过 capability probe 上报 General OCR；否则安全降级为 native-only。

## 演示顺序

1. 打开 `http://127.0.0.1:5173`，创建课程并上传自制 PDF 或 Markdown；图片可用于资料入库，PPT/PPTX、DOCX、TIFF 请先转换格式。
2. 展示 ParseJob、不可变 preview Revision、页/幻灯片定位和 Chunk。
3. 未配置 Embedding 时展示 `parsed_index_blocked` / `index_blocked_provider`，说明不会生成伪向量。
4. 在已配置且已获授权的本地环境中恢复 IndexJob，展示 Dense、BM25、RRF 和 active manifest；本仓库自动化不会执行该外部调用。
5. 展示有 Evidence 的回答、Citation 回源和非法 Citation 被拒绝。
6. 提问 seed 未覆盖的网络问题，展示证据不足拒答。
7. 展示笔记绑定 Revision；删除来源后引用立即失效并进入幂等清理。
8. 打开 `.local/evals/` 与 `.local/evidence/` 中的脱敏报告，明确区分 `test-double`、`no-provider`、`live-provider`、`live-model` 与生产未评估边界。

## 讲解边界

- 不展示真实课程原文、对象 key、Prompt、Secret、私有路径或完整日志。
- 不把预计算 RAG 报告称为 E2E。
- 不把本地 2 GiB preflight 称为服务器容量或生产就绪。
- 不把 `Deploy Plan`、本地 Compose 或一次 smoke 称为已经部署。

## 收集实现证据

```bash
CHECK_ALL_QUICK=1 ./scripts/check_all.sh
```

`.local/evidence/implementation-manifest.json` 只记录 gate 状态、退出码、耗时、输出哈希和公开 artifact 哈希。它不保存命令输出、环境值或绝对工作区路径。
