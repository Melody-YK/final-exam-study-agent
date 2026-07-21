from __future__ import annotations

import os
import signal
import stat
import sys
from pathlib import Path

import pytest

from study_worker.sandbox import (
    CommandPolicy,
    ProcessBoundaryError,
    ProcessOutputLimitError,
    ProcessResourceLimits,
    ProcessTimeoutError,
    RestrictedProcessRunner,
    SandboxBoundaryError,
    SandboxManager,
)


def test_sandbox_is_private_and_removed_after_context(tmp_path: Path) -> None:
    manager = SandboxManager(tmp_path / "worker")

    with manager.create() as sandbox:
        root = sandbox.root
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(sandbox.output_dir.stat().st_mode) == 0o700
        sandbox.input_path.write_bytes(b"private")

    assert not root.exists()


def test_cleanup_all_removes_active_sandboxes(tmp_path: Path) -> None:
    manager = SandboxManager(tmp_path / "worker")
    context = manager.create()
    sandbox = context.__enter__()

    manager.cleanup_all()

    assert not sandbox.root.exists()
    context.__exit__(None, None, None)


def test_symlink_work_root_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "worker"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(SandboxBoundaryError, match="symlink"):
        SandboxManager(link)


@pytest.mark.asyncio
async def test_restricted_runner_uses_exec_argv_and_does_not_inherit_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = Path(sys.executable).resolve()
    code = (
        "import os,sys;sys.stdout.write(sys.argv[1] + '|' + os.getenv('WORKER_TOKEN', 'missing'))"
    )
    marker = tmp_path / "must-not-exist"
    literal_argument = f"; touch {marker}"
    expected_args = ("-c", code, literal_argument)
    runner = RestrictedProcessRunner(
        (
            CommandPolicy(
                name="literal",
                executable=executable,
                validate_args=lambda args: args == expected_args,
            ),
        )
    )
    monkeypatch.setenv("WORKER_TOKEN", "host-secret")
    manager = SandboxManager(tmp_path / "worker")

    with manager.create() as sandbox:
        result = await runner.run(
            "literal",
            expected_args,
            sandbox=sandbox,
            timeout_seconds=2,
        )

    assert result.returncode == 0
    assert result.stdout.decode() == f"{literal_argument}|missing"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_restricted_runner_rejects_unknown_command_and_argv(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    runner = RestrictedProcessRunner(
        (
            CommandPolicy(
                name="version",
                executable=executable,
                validate_args=lambda args: args == ("--version",),
            ),
        )
    )
    manager = SandboxManager(tmp_path / "worker")

    with manager.create() as sandbox:
        with pytest.raises(ProcessBoundaryError, match="COMMAND_NOT_ALLOWED"):
            await runner.run("shell", (), sandbox=sandbox, timeout_seconds=1)
        with pytest.raises(ProcessBoundaryError, match="ARGV_NOT_ALLOWED"):
            await runner.run(
                "version",
                ("-c", "print('not allowed')"),
                sandbox=sandbox,
                timeout_seconds=1,
            )


@pytest.mark.asyncio
async def test_restricted_runner_timeout_kills_the_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = Path(sys.executable).resolve()
    args = ("-c", "import time; time.sleep(60)")
    runner = RestrictedProcessRunner(
        (
            CommandPolicy(
                name="slow",
                executable=executable,
                validate_args=lambda candidate: candidate == args,
            ),
        )
    )
    kill_calls: list[tuple[int, int]] = []
    real_killpg = os.killpg

    def recording_killpg(process_group: int, sent_signal: int) -> None:
        kill_calls.append((process_group, sent_signal))
        real_killpg(process_group, sent_signal)

    monkeypatch.setattr(os, "killpg", recording_killpg)
    manager = SandboxManager(tmp_path / "worker")

    with (
        manager.create() as sandbox,
        pytest.raises(ProcessTimeoutError, match="PROCESS_TIMEOUT"),
    ):
        await runner.run("slow", args, sandbox=sandbox, timeout_seconds=0.05)

    assert kill_calls
    assert kill_calls[0][1] == signal.SIGKILL


@pytest.mark.asyncio
async def test_restricted_runner_enforces_combined_output_limit(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    args = ("-c", "import sys; sys.stdout.write('x' * 128)")
    runner = RestrictedProcessRunner(
        (
            CommandPolicy(
                name="noisy",
                executable=executable,
                validate_args=lambda candidate: candidate == args,
            ),
        ),
        max_output_bytes=32,
    )
    manager = SandboxManager(tmp_path / "worker")

    with (
        manager.create() as sandbox,
        pytest.raises(ProcessOutputLimitError, match="PROCESS_OUTPUT_LIMIT"),
    ):
        await runner.run("noisy", args, sandbox=sandbox, timeout_seconds=2)


@pytest.mark.asyncio
async def test_restricted_runner_applies_child_file_size_limit(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    args = (
        "-c",
        "import os; f=open('oversized.bin','wb'); f.write(b'x'*4096); "
        "f.flush(); os.fsync(f.fileno())",
    )
    runner = RestrictedProcessRunner(
        (
            CommandPolicy(
                name="writer",
                executable=executable,
                validate_args=lambda candidate: candidate == args,
            ),
        ),
        resource_limits=ProcessResourceLimits(file_size_bytes=1024),
    )
    manager = SandboxManager(tmp_path / "worker")

    with manager.create() as sandbox:
        result = await runner.run("writer", args, sandbox=sandbox, timeout_seconds=2)

    assert result.returncode != 0
