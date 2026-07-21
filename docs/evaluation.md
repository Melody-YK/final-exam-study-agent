# 评测协议

## 数据边界

公开 parser fixture 由 `tests/fixtures/build_documents.py` 确定性生成；公开 RAG seed 位于 `evals/fixtures/public/rag-seed-v1.jsonl`。两者均为自制内容并按 `CC0-1.0` 发布，权威哈希和许可记录见 `evals/manifests/public.json`。

私有课程材料不得复制入仓库。使用以下命令只生成包含绝对路径、大小和哈希的本地 manifest，输出必须留在 `.local/`：

```bash
uv run python scripts/build_private_eval_manifest.py /absolute/private/source
```

## OCR

`evals/ocr/run_benchmark.py` 读取本地 Observation 和人工 gold，输出不含文本、文件路径或对象 key的报告。报告记录 parser/dependency 版本、CER、阅读顺序、bbox、表格/公式计数、RSS、P50/P95 和失败页。没有真实模型 observation 时，不能声明 live OCR verified。

隔离 profile 已安装并由操作员完成 General warmup 时，可使用自制中英 gold 和可选私有授权目录运行真实基准：

```bash
services/worker/profiles/paddle/.venv/bin/study-agent-paddle-profile warmup \
  --backend general --cache-root "$PWD/.local/models/paddlex"
uv run python scripts/run_live_ocr_benchmark.py \
  --private-source-root /absolute/private/source
```

`warmup` 只有在真实自制页推理通过严格 Adapter 后才写 readiness marker。PP-StructureV3、MinerU 和付费 OCR 不会因 General 成功而自动启用；每个后端必须独立通过 capability 与资源门。

## RAG、引用与拒答

RAG 报告严格拆分：

| 模式 | 外部调用 | 可证明范围 |
|---|---:|---|
| `test-double` | 否 | seed、Dense/BM25/RRF/Rerank、引用/拒答报告协议 |
| `no-provider` | 否 | Provider 缺失时明确拒答/不可用的报告边界 |
| `live-provider` | 是 | 仅在显式 live gate、轮换凭据和本地 observation 同时存在时可生成 |

```bash
uv run python -m evals.rag.run_benchmark --mode test-double
uv run python -m evals.rag.run_benchmark --mode no-provider
uv run python -m evals.rag.ablation \
  evals/fixtures/public/rag-seed-v1.jsonl \
  --output .local/evals/rag/ablation.json
```

真实 Provider 使用轮换后的 `0600` Secret 文件在同一 shell 中显式双门执行；live smoke 只使用自制合成 Evidence，并输出脱敏 receipt：

```bash
set -a
source .local/secrets/provider.env
set +a
./scripts/run_live_provider_probe.sh -q
uv run python scripts/run_live_rag_smoke.py
```

报告只保存 seed 哈希、case key、Evidence ID 计数、Recall@K、MRR、Citation support/coverage、拒答准确率、延迟和失败码，不保存问题、答案或课程原文。预计算 seed 的满分结果只证明协议自洽，不是应用 E2E、真实模型质量或阈值通过证据。

## 资源

资源 observation 必须覆盖 `static-web`、`api-single-uvicorn`、`postgres-small-pool`、`index-runner`、`exact-pgvector` 和 `bm25-mmap`，并声明精确的 `2147483648` 字节限制。没有 `.local/evals/resource-observations.json` 时：

```bash
uv run python scripts/run_resource_preflight.py
# exit 77: external-blocked
```

报告包含 RSS、P50/P95、timeout/OOM/error；无论结果如何都不能写成生产容量验证。

`./scripts/run_local_rc.sh --smoke` 会启动 Compose PostgreSQL 和宿主 API/Web/Runner/Worker，收集真实 observation，并保留 PostgreSQL container/volume；退出时只清理本轮宿主进程。

## 依赖公告

```bash
uv run python scripts/run_advisory_audit.py
```

该命令查询基础 Python、隔离 Paddle Python 与 npm production 的在线公告库，把脱敏报告写入 `.local/evidence/`，并把摘要绑定到当前 `uv.lock`、Paddle `uv.lock` 和 `package-lock.json` 哈希。网络不可用返回 `77`，findings 返回 `1`；锁文件变化后必须重跑。

## 质量判定

当前代码提供指标和失败分析协议，但没有经过 Review 确认的最终质量阈值。任何不足必须保留在报告 failure code 中，不允许用平均分掩盖漏检、错误引用、错误拒答或 OOM。

生成的原始 observation、报告和实现证据均写入 `.local/` 或 `evals/reports/generated/`，这些路径被 Git 忽略。
