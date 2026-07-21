"""Collect real local RC observations for the 2 GiB-equivalent preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.resources.preflight import (  # noqa: E402
    TWO_GIB_BYTES,
    ResourcePreflightObservation,
)
from study_agent.modules.retrieval.bm25_index import (  # noqa: E402
    Bm25IndexStore,
    LexicalDocument,
)
from study_agent.modules.retrieval.tokenizer import ChineseTokenizer  # noqa: E402

_COMPOSE = _ROOT / "infra" / "compose" / "compose.yml"


def _rss_bytes(pid: int) -> int:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,rss="],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(f"process is unavailable: {pid}")
    rows: dict[int, tuple[int, int]] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        try:
            process_id, parent_id, rss_kib = (int(field) for field in fields)
        except ValueError:
            continue
        rows[process_id] = (parent_id, rss_kib)
    if pid not in rows:
        raise RuntimeError(f"process is unavailable: {pid}")
    process_tree = {pid}
    changed = True
    while changed:
        changed = False
        for process_id, (parent_id, _rss_kib) in rows.items():
            if parent_id in process_tree and process_id not in process_tree:
                process_tree.add(process_id)
                changed = True
    return sum(rows[process_id][1] for process_id in process_tree) * 1024


def _timed(action: Callable[[], None]) -> float:
    started = time.perf_counter()
    action()
    return round((time.perf_counter() - started) * 1000, 3)


def _http_get(url: str) -> None:
    with urllib.request.urlopen(url, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected local HTTP status: {response.status}")
        response.read(1024)


def _postgres_query(sql: str) -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(_COMPOSE),
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "study_agent",
            "-d",
            "study_agent",
            "-v",
            "ON_ERROR_STOP=1",
            "-Atc",
            sql,
        ],
        capture_output=True,
        check=False,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError("local PostgreSQL query failed")


def _container_rss_bytes() -> int:
    container = subprocess.run(
        ["docker", "compose", "-f", str(_COMPOSE), "ps", "-q", "postgres"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    if not container:
        raise RuntimeError("local PostgreSQL container is unavailable")
    usage = (
        subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        .stdout.split("/", maxsplit=1)[0]
        .strip()
    )
    number = "".join(character for character in usage if character.isdigit() or character == ".")
    unit = usage[len(number) :].strip()
    factors = {
        "B": 1,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
        "kB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
    }
    if not number or unit not in factors:
        raise RuntimeError("Docker returned an unknown memory unit")
    return round(float(number) * factors[unit])


def _observation(
    component: str,
    *,
    latency_ms: float,
    rss_bytes: int,
) -> ResourcePreflightObservation:
    return ResourcePreflightObservation.model_validate(
        {
            "schema_version": "1.0",
            "component": component,
            "sample": 1,
            "latency_ms": latency_ms,
            "rss_bytes": rss_bytes,
            "outcome": "succeeded",
            "memory_limit_bytes": TWO_GIB_BYTES,
        }
    )


def _collect_static_web() -> ResourcePreflightObservation:
    distribution = _ROOT / "apps" / "web" / "dist"
    if not (distribution / "index.html").is_file():
        raise RuntimeError("static Web build is unavailable")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "http.server",
            "4174",
            "--bind",
            "127.0.0.1",
            "--directory",
            str(distribution),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        for _attempt in range(30):
            try:
                latency = _timed(lambda: _http_get("http://127.0.0.1:4174/"))
                return _observation(
                    "static-web",
                    latency_ms=latency,
                    rss_bytes=_rss_bytes(process.pid),
                )
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("static Web server did not become ready")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _collect_bm25() -> ResourcePreflightObservation:
    tokenizer = ChineseTokenizer(("信号量", "进程调度"))
    documents = [
        LexicalDocument(
            chunk_id=f"chunk-{index}",
            user_id="local-resource-user",
            course_id="local-resource-course",
            document_id=f"document-{index}",
            revision_id=f"revision-{index}",
            text=text,
            content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
        for index, text in enumerate(
            ("信号量用于同步并发任务", "进程调度决定任务运行顺序"),
            start=1,
        )
    ]
    with tempfile.TemporaryDirectory(dir=_ROOT / ".local") as temporary:
        store = Bm25IndexStore(Path(temporary), tokenizer)
        started = time.perf_counter()
        manifest = store.build(documents, version_id="resource-preflight")
        loaded = store.load(manifest)
        hits = loaded.search("并发同步", limit=2)
        latency = round((time.perf_counter() - started) * 1000, 3)
        if not hits:
            raise RuntimeError("BM25 mmap preflight returned no results")
        return _observation(
            "bm25-mmap",
            latency_ms=latency,
            rss_bytes=_rss_bytes(os.getpid()),
        )


def collect(api_pid: int, runner_pid: int) -> list[ResourcePreflightObservation]:
    postgres_rss = _container_rss_bytes()
    observations = [
        _collect_static_web(),
        _observation(
            "api-single-uvicorn",
            latency_ms=_timed(lambda: _http_get("http://127.0.0.1:8000/healthz")),
            rss_bytes=_rss_bytes(api_pid),
        ),
        _observation(
            "postgres-small-pool",
            latency_ms=_timed(lambda: _postgres_query("SELECT 1")),
            rss_bytes=postgres_rss,
        ),
        _observation(
            "index-runner",
            latency_ms=0,
            rss_bytes=_rss_bytes(runner_pid),
        ),
        _observation(
            "exact-pgvector",
            latency_ms=_timed(
                lambda: _postgres_query("SELECT '[1,2,3]'::vector <-> '[3,2,1]'::vector")
            ),
            rss_bytes=postgres_rss,
        ),
        _collect_bm25(),
    ]
    return observations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-pid", type=int, required=True)
    parser.add_argument("--runner-pid", type=int, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".local/evals/resource-observations.json"),
    )
    arguments = parser.parse_args()
    observations = collect(arguments.api_pid, arguments.runner_pid)
    target = arguments.output.expanduser().absolute()
    if target.is_symlink():
        raise ValueError("output must not be a symlink")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            [item.model_dump(mode="json") for item in observations],
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    os.chmod(target, 0o600)
    print("resource_observations=collected")
    print("production_capacity_verified=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
