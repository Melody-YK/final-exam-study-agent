# 期末复习智能体

面向大学生期末周的课程资料学习工作区。系统把 PDF、Markdown 和图片转成可追踪的课程知识库，提供带原页或章节引用的问答、证据不足拒答、章节笔记和检索工程证据。新上传支持 PDF、Markdown、JPG/JPEG 和 PNG，Markdown 单文件上限为 5 MB；PPT/PPTX、DOCX、TIFF 等请先转换为 PDF 或 Markdown。

## 当前状态

仓库已具备本地课程上传、原生解析、版本化索引、混合检索、证据约束问答、拒答、引用和笔记的可测试实现。当前交付边界是“本地代码与自动化可验证”，不是生产就绪声明；生产认证、部署、容量、回滚和观测仍不在本仓库的已验证范围内。

## 本地要求

- Python 3.12
- Node.js 24+
- uv
- Docker Desktop / Docker Compose
- Poppler `pdftotext`（建议安装；低内存修复部分 PDF 的损坏中文文字层）

macOS 可用 `brew install poppler`，Ubuntu/Debian 可用 `sudo apt-get install poppler-utils`；通过 `pdftotext -v` 检查是否可用。

## 开发命令

```bash
make sync
make compose-up
make check
make api
make web
```

API 默认监听 `http://127.0.0.1:8000`，Web 默认监听 `http://127.0.0.1:5173`。

## 本地 RC

`scripts/run_local_rc.sh --check` 只读检查现有 PostgreSQL、API、Index Runner、Worker 和 Web，不会启动、停止或删除容器。服务未运行或 Docker 不健康时返回 `77`，含义是 `external-blocked`，不是测试通过。

`scripts/run_local_rc.sh --smoke` 是显式 opt-in：它只启动 Compose 中的 PostgreSQL，随后在宿主机启动 API、单并发 Index Runner、原生 Worker 和静态开发 Web。脚本退出时只终止自己创建的宿主进程，不执行 `compose down`，不删除 volume。该 smoke 不含浏览器、真实 Provider 或 Paddle 模型验证。

```bash
./scripts/run_local_rc.sh --check
./scripts/run_local_rc.sh --smoke
```

## 评测与证据

公开 seed 位于 `evals/fixtures/public/`，其哈希、许可、用途和答案污染声明由 `evals/manifests/public.json` 管理。私有资料只能通过工作区外绝对路径建立本地 manifest，原文件不会复制入仓库。

```bash
uv run python -m evals.rag.run_benchmark --mode test-double
uv run python -m evals.rag.run_benchmark --mode no-provider
uv run python scripts/run_resource_preflight.py
CHECK_ALL_QUICK=1 ./scripts/check_all.sh
```

评测报告和实现证据写入忽略目录 `.local/`，权限设为 `0600`。`test-double` 报告只验证预计算排名和报告协议；没有真实本地观察时，资源预检返回 `77`，不会推断 2 GiB 服务器或生产容量。

详细说明：

- [架构与运行边界](docs/architecture.md)
- [评测协议](docs/evaluation.md)
- [演示脚本](docs/demo.md)
- [第三方与 fixture 许可](THIRD_PARTY_NOTICES.md)

## 数据与凭据边界

- 内部课程资料不得复制进仓库、CI 或公开 Demo。
- `.env.example` 只包含非敏感契约；真实 Key 只通过运行时环境/Secret 注入。
- 运行时不提供假模型。Provider 未配置时，资料浏览可用，问答和新笔记生成明确不可用。
- 已经在聊天或日志中暴露的凭据必须先撤销轮换，禁止继续使用。
- `scripts/check_private_data.py` 同时校验私有路径、常见凭据形态、公开 fixture 哈希、许可、重复内容、悬空 Evidence ID 和答案污染。
