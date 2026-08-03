# PDF 解析链配置

PDF 上传提供两条独立链路。默认的“智能回退”按页执行快速原生解析、Docling
标准结构化回退和 Docling VLM 多模态兜底；用户也可以显式选择自建 MinerU 服务。
非 PDF 文件始终使用现有原生链路。

## 路由与结果来源

智能回退先保留通过质量门的原生页，只对文字覆盖不足、文字层损坏或表格、图片、
公式结构未解析的页面升级处理。Docling 标准结果仍不完整或执行失败时，才尝试 VLM。
每页最终选择质量最好的候选，因此同一文档可以包含不同后端的页面。显式选择
MinerU 时不会暗中切换到智能回退；MinerU 失败会保留为可重试失败，用户可重新上传
或改选智能回退。

解析产物记录以下来源值：

| 路径 | `parser_profile` | `source_backend` |
|---|---|---|
| 快速原生解析 | `native-v1` | `pdf-native` |
| Docling 标准 | `native-v1` | `docling-standard` |
| Docling VLM | `native-v1` | `docling-vlm` |
| 混合页面汇总 | `mixed` | `mixed` |
| MinerU pipeline | `mineru-v1` | `mineru-pipeline` |

页和块的 metadata 还会记录 `parser_route`、`parser_route_reason` 和尝试次数。

## Docling 准备

Docling 位于独立锁定环境，不会被安装进基础 Worker。模型下载只能通过操作员显式
执行 warmup；Worker 启动、能力探测和正式解析均不会自动下载模型。

```bash
uv sync --project services/worker/profiles/docling --locked
mkdir -p .local/models/docling
uv run --project services/worker/profiles/docling study-agent-docling-profile warmup \
  --backend standard --artifacts-root "$PWD/.local/models/docling"
uv run --project services/worker/profiles/docling study-agent-docling-profile warmup \
  --backend vlm --artifacts-root "$PWD/.local/models/docling"
```

`standard` 必须先于 `vlm` 完成 warmup。warmup 可能访问 Hugging Face；如操作员
主动设置了 `HF_HUB_OFFLINE=1` 或 `TRANSFORMERS_OFFLINE=1`，需在首次 warmup 前解除。
正式 `run` 命令会固定设置这两个变量为 `1`，并只从配置的 artifacts 目录读取。

在 `.env` 中启用已预热的 profile：

```dotenv
WORKER_DOCLING_PROFILE_BIN=/absolute/path/to/services/worker/profiles/docling/.venv/bin/study-agent-docling-profile
WORKER_DOCLING_ARTIFACTS_ROOT=/absolute/path/to/.local/models/docling
```

检查能力：

```bash
uv run study-agent-worker docling-capabilities
```

只有 standard marker 有效时才启用结构化回退，只有 VLM marker 也有效时才启用
多模态兜底。版本变化、目录不一致或 marker 缺失都会关闭相应能力。

## MinerU 服务

本仓库对接 MinerU 3.4.4 的 `GET /health` 和同步 `POST /file_parse`，固定使用
`backend=pipeline` 与 `content_list`。MinerU 是独立第三方服务，不与本项目 API 共用
端口。开发环境可让本项目 API 继续使用 `8000`，MinerU 使用 `8001`。

MinerU 的安装、模型预取和硬件参数应以其对应版本的官方部署文档为准。一个最小的
独立 Python 环境示例如下；这些命令不属于本项目启动流程：

```bash
uv venv --python 3.12 .local/mineru/.venv
uv pip install --python .local/mineru/.venv/bin/python 'mineru[pipeline]==3.4.4'
.local/mineru/.venv/bin/mineru-models-download
.local/mineru/.venv/bin/mineru-api --host 127.0.0.1 --port 8001
```

MinerU 上游首次运行可能自动下载模型，生产环境应先显式预取并在无外网条件下做一次
解析 smoke。不要把未加鉴权的 MinerU API 直接暴露到公网；跨主机部署时放在内部网络
或带鉴权的反向代理后。`WORKER_MINERU_TOKEN` 只用于向这种代理发送 Bearer token。

在 `.env` 中配置 Worker：

```dotenv
WORKER_MINERU_BASE_URL=http://127.0.0.1:8001
WORKER_MINERU_TOKEN=
WORKER_MINERU_BACKEND=pipeline
```

先验证上游，再验证 Worker：

```bash
curl -fsS http://127.0.0.1:8001/health
uv run study-agent-worker mineru-capabilities
```

重启 Worker 后，健康能力会随心跳上报；上传 PDF 时 MinerU 选项才会启用。界面中的
“由自建 MinerU 服务解析”同时履行当前 MinerU 许可证要求的服务标识。部署公开云服务
前仍须复核实际版本的 `LICENSE.md`、模型权重及字体许可。

## 资源和失败边界

默认 Worker 限制为单文件 100 MiB、最多 2000 页、单个外部解析进程 180 秒。可通过
`WORKER_MAX_INPUT_BYTES`、`WORKER_MAX_PAGES` 和
`WORKER_EXTERNAL_PROCESS_TIMEOUT_SECONDS` 调整。Docling 子进程还受 CPU、文件大小、
打开文件数及 Linux 8 GiB 地址空间限制；VLM 通常需要明显更多内存和首次预热时间。

MinerU 的并发、显存、CPU、任务保留和输出空间由 MinerU 服务自身控制，本项目只在
Worker 侧限制输入页数、响应体大小和超时。云端容量不能按“服务器内存除以单请求
内存”静态推断，应分别对快速链、Docling standard、Docling VLM 和 MinerU 做真实文档
压测，并限制每种重型后端的并发。

## 验证边界

仓库自动测试覆盖选择策略、能力门、混合页来源、回退顺序、MinerU 页码映射、响应
边界和 Docling 运行期离线模式。自动测试不证明本机已安装模型，也不证明具体教材的
版面质量。发布前至少用自制的纯文本 PDF、扫描 PDF、表格/公式 PDF 和图示密集 PDF
分别跑两条链路，核对页码、标题层级、表格、公式、学习单元和最终出题证据。
