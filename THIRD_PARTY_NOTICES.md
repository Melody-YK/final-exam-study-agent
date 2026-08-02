# Third-Party Notices

本文件记录当前直接依赖和公开 fixture 的许可入口，不替代各上游项目随包发布的完整许可文本。精确版本和传递依赖以 `uv.lock`、`services/worker/profiles/paddle/uv.lock` 和 `package-lock.json` 为准。

## Public Fixtures

| Artifact | License | Source |
|---|---|---|
| Generated PDF/PPTX parser fixtures | `CC0-1.0` | Self-authored in this repository |
| `rag-seed-v1.jsonl` | `CC0-1.0` | Self-authored in this repository |

Hash、用途和 attribution 由 `evals/manifests/public.json` 管理。fixture 不含私有课程材料或参考答案正文。

## Python Runtime

| Dependency | License family |
|---|---|
| Alembic, FastAPI, SQLAlchemy, Pydantic Settings | MIT |
| asyncpg | Apache-2.0 |
| BM25S, Jieba, filetype | MIT |
| HTTPX | BSD-3-Clause |
| pgvector | PostgreSQL License |
| structlog | Apache-2.0 |
| Uvicorn | BSD-3-Clause |
| lxml | BSD-3-Clause |
| pdfplumber, python-pptx, Typer | MIT |
| Pillow | HPND |
| pypdf | BSD-3-Clause |

## Optional PDF Text Repair

Poppler `pdftotext` 使用 `GPL-2.0-only OR GPL-3.0-only`。它作为宿主机可选外部可执行文件调用，本仓库不内置或分发 Poppler；缺失时解析器会沿用已有质量门和 OCR 回退路径。

## Web Runtime

| Dependency | License family |
|---|---|
| React / React DOM | MIT |
| React Router | MIT |
| TanStack Query | MIT |
| Lucide React | ISC |
| PDF.js (`pdfjs-dist`) | Apache-2.0 |
| Vite | MIT |

开发工具及传递依赖的许可字段保存在 `package-lock.json`。`scripts/dependency_check.py` 离线验证 lock source、integrity 和 license metadata；它不查询漏洞公告数据库。

## Isolated OCR Profile

`paddleocr` 与 `paddlepaddle` 使用 Apache-2.0，且只存在于 `services/worker/profiles/paddle/` 的独立锁定 profile。默认 API/Worker 不安装它们，capability probe 不下载模型。模型权重可能有独立条款，必须在本地获取前另行核查；本仓库不分发权重。

## Verification Boundary

许可名称来自上游包 metadata 或项目许可声明。发布前仍需用组织认可的 SBOM/许可和漏洞数据库工具复核；该外部审计在当前本地实现范围中未执行。
