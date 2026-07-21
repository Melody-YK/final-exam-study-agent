"""Private, short-lived filesystem boundaries for worker jobs."""

from __future__ import annotations

import asyncio
import os
import resource
import shutil
import signal
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class SandboxBoundaryError(RuntimeError):
    """The configured work root cannot provide an isolated job directory."""


class ProcessBoundaryError(RuntimeError):
    """A subprocess request violated a declared execution boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProcessTimeoutError(ProcessBoundaryError):
    def __init__(self) -> None:
        super().__init__("PROCESS_TIMEOUT")


class ProcessOutputLimitError(ProcessBoundaryError):
    def __init__(self) -> None:
        super().__init__("PROCESS_OUTPUT_LIMIT")


@dataclass(frozen=True, slots=True)
class Sandbox:
    """Paths owned by one claimed job and removed when processing finishes."""

    root: Path
    input_path: Path
    output_dir: Path


ArgvValidator = Callable[[tuple[str, ...]], bool]


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    """One statically named executable and its complete argv validation rule."""

    name: str
    executable: Path
    validate_args: ArgvValidator

    def __post_init__(self) -> None:
        if not self.name or any(
            character != "-" and character != "_" and not character.isalnum()
            for character in self.name
        ):
            raise ValueError("command policy names must be simple identifiers")
        if not self.executable.is_absolute():
            raise ValueError("command policy executables must be absolute paths")


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True, slots=True)
class ProcessResourceLimits:
    """Portable Unix resource ceilings applied in the child before exec."""

    cpu_seconds: int = 300
    address_space_bytes: int = 8 * 1024 * 1024 * 1024
    file_size_bytes: int = 512 * 1024 * 1024
    open_files: int = 256

    def __post_init__(self) -> None:
        if (
            min(
                self.cpu_seconds,
                self.address_space_bytes,
                self.file_size_bytes,
                self.open_files,
            )
            <= 0
        ):
            raise ValueError("process resource limits must be positive")

    def apply(self) -> None:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (self.cpu_seconds, self.cpu_seconds))
        # Darwin exposes RLIMIT_AS but rejects setting it in preexec_fn.
        # Linux production workers still receive the address-space ceiling.
        if sys.platform != "darwin":
            resource.setrlimit(
                resource.RLIMIT_AS,
                (self.address_space_bytes, self.address_space_bytes),
            )
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (self.file_size_bytes, self.file_size_bytes),
        )
        resource.setrlimit(resource.RLIMIT_NOFILE, (self.open_files, self.open_files))


class RestrictedProcessRunner:
    """Run only declared argv under a private cwd without a shell or inherited secrets."""

    _ALLOWED_ENVIRONMENT_KEYS = frozenset(
        {
            "FONTCONFIG_FILE",
            "FONTCONFIG_PATH",
            "LANG",
            "LC_ALL",
            "SAL_USE_VCLPLUGIN",
            "TZ",
        }
    )

    def __init__(
        self,
        policies: tuple[CommandPolicy, ...],
        *,
        max_output_bytes: int = 1024 * 1024,
        environment: Mapping[str, str] | None = None,
        resource_limits: ProcessResourceLimits | None = None,
    ) -> None:
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        self._max_output_bytes = max_output_bytes
        self._resource_limits = resource_limits or ProcessResourceLimits()
        self._environment = dict(environment or {})
        if any(key not in self._ALLOWED_ENVIRONMENT_KEYS for key in self._environment):
            raise ValueError("subprocess environment contains a non-allowlisted key")
        if any(
            "\x00" in key or "\x00" in value or "\n" in value or "\r" in value
            for key, value in self._environment.items()
        ):
            raise ValueError("subprocess environment contains invalid characters")
        self._policies: dict[str, CommandPolicy] = {}
        for policy in policies:
            if policy.name in self._policies:
                raise ValueError(f"duplicate command policy: {policy.name}")
            if policy.executable.is_symlink() or not policy.executable.is_file():
                raise ValueError(f"command executable is not a regular file: {policy.name}")
            if not os.access(policy.executable, os.X_OK):
                raise ValueError(f"command executable is not executable: {policy.name}")
            self._policies[policy.name] = policy

    async def run(
        self,
        command_name: str,
        args: tuple[str, ...],
        *,
        sandbox: Sandbox,
        timeout_seconds: float,
    ) -> ProcessResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        policy = self._policies.get(command_name)
        if policy is None:
            raise ProcessBoundaryError("COMMAND_NOT_ALLOWED")
        self._validate_argv(policy, args)
        cwd = sandbox.root.resolve(strict=True)
        environment = {
            "HOME": str(cwd),
            "TMPDIR": str(cwd),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            **self._environment,
        }
        try:
            process = await asyncio.create_subprocess_exec(
                str(policy.executable),
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=environment,
                start_new_session=True,
                close_fds=True,
                preexec_fn=self._resource_limits.apply,
            )
        except OSError:
            raise ProcessBoundaryError("PROCESS_START_FAILED") from None

        try:
            async with asyncio.timeout(timeout_seconds):
                stdout, stderr = await self._collect_output(process)
        except TimeoutError:
            await self._kill_process_group(process)
            raise ProcessTimeoutError from None
        except ProcessBoundaryError:
            await self._kill_process_group(process)
            raise
        except asyncio.CancelledError:
            await self._kill_process_group(process)
            raise
        return ProcessResult(
            returncode=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )

    def _validate_argv(self, policy: CommandPolicy, args: tuple[str, ...]) -> None:
        if len(args) > 128:
            raise ProcessBoundaryError("ARGV_NOT_ALLOWED")
        if any(
            len(argument) > 4096
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in argument)
            for argument in args
        ):
            raise ProcessBoundaryError("ARGV_NOT_ALLOWED")
        try:
            allowed = policy.validate_args(args)
        except Exception:
            raise ProcessBoundaryError("ARGV_NOT_ALLOWED") from None
        if not allowed:
            raise ProcessBoundaryError("ARGV_NOT_ALLOWED")

    async def _collect_output(self, process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
        if process.stdout is None or process.stderr is None:
            raise ProcessBoundaryError("PROCESS_PIPE_UNAVAILABLE")
        total = [0]
        stdout_task = asyncio.create_task(self._read_limited(process.stdout, total))
        stderr_task = asyncio.create_task(self._read_limited(process.stderr, total))
        wait_task = asyncio.create_task(process.wait())
        tasks = (stdout_task, stderr_task, wait_task)
        try:
            stdout, stderr, _ = await asyncio.gather(*tasks)
            return stdout, stderr
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _read_limited(
        self,
        stream: asyncio.StreamReader,
        total: list[int],
    ) -> bytes:
        output = bytearray()
        while block := await stream.read(64 * 1024):
            total[0] += len(block)
            if total[0] > self._max_output_bytes:
                raise ProcessOutputLimitError
            output.extend(block)
        return bytes(output)

    @staticmethod
    async def _kill_process_group(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            process.kill()
        await process.wait()


class SandboxManager:
    """Create mode-0700 job directories and track them for signal cleanup."""

    def __init__(self, work_root: Path) -> None:
        expanded = work_root.expanduser()
        if expanded.is_symlink():
            raise SandboxBoundaryError("worker sandbox root must not be a symlink")
        expanded.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not expanded.is_dir():
            raise SandboxBoundaryError("worker sandbox root must be a directory")
        self._work_root = expanded.resolve(strict=True)
        os.chmod(self._work_root, 0o700)
        self._active: set[Path] = set()

    @property
    def work_root(self) -> Path:
        return self._work_root

    @contextmanager
    def create(self) -> Iterator[Sandbox]:
        root = Path(tempfile.mkdtemp(prefix="job-", dir=self._work_root))
        os.chmod(root, 0o700)
        output_dir = root / "output"
        output_dir.mkdir(mode=0o700)
        self._active.add(root)
        try:
            yield Sandbox(
                root=root,
                input_path=root / "input.bin",
                output_dir=output_dir,
            )
        finally:
            self._remove(root)

    def cleanup_all(self) -> None:
        """Best-effort cleanup used both normally and during signal shutdown."""

        for root in tuple(self._active):
            self._remove(root)

    def _remove(self, root: Path) -> None:
        self._active.discard(root)
        try:
            root.relative_to(self._work_root)
        except ValueError as exc:
            raise SandboxBoundaryError("refusing to clean a path outside work root") from exc
        if root.is_symlink():
            root.unlink(missing_ok=True)
            return
        shutil.rmtree(root, ignore_errors=True)
