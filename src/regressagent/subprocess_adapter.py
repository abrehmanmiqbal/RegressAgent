"""Tier A subprocess adapter (D-010, docs/ADAPTER_ISOLATION.md section 3).

**Tier A is NOT a sandbox.** It runs a command the user configured, as the user, with the
user's files and network reachable. It adds guard rails against *accidents and sloppy agents*
(hangs, floods, leftover children, leaked environment variables), not against a malicious
program. Use it for trusted code only.

What it does:
- ``shell=False``; argv comes from the validated local config, never from case data;
- executable resolved with ``realpath``, must be a regular, executable file that is not
  group/world writable; its SHA-256 is recorded and, when pinned, must match;
- empty environment plus a fixed ``PATH`` plus the allow-listed variable *names* (values read
  from the parent at run time, never stored);
- fresh private temp directory as cwd (removed afterwards), stdin is a pipe;
- own session / process group; on timeout or cap violation the whole group gets SIGTERM, a short
  grace, then SIGKILL, and the group is also killed after a normal exit so no children linger
  (a child that calls ``setsid`` escapes this: Tier A cannot prevent that);
- single-threaded ``selectors`` loop: wall-clock deadline, byte caps on stdout (kill) and stderr
  (truncate, kill on a flood), input written without deadlocking on a child that never reads;
- POSIX rlimits (CPU, address space, file size, open files, core=0, tasks) applied by a tiny
  launcher before ``exec`` (no ``preexec_fn``, so it is safe in threaded hosts); if a limit
  cannot be applied the run fails (fail closed);
- stdout must be exit code 0, UTF-8, one JSON object of bounded depth (no NaN/Infinity).

Every failure raises ``AdapterError``; ``RegressionEngine`` turns that into a failed recorded
result. ``last_metadata`` describes the most recent run (set even when it failed).

Known limits (tested or documented, none hidden): RLIMIT_NPROC is not enforced for root and is
per-user, not per-tree; a hash is checked just before the launch, so a file swapped in that
window is not detected; descendants that leave the process group survive; Linux only.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from typing import Any

from .config import AdapterConfig
from .models import RunMetadata

FIXED_PATH = "/usr/local/bin:/usr/bin:/bin"
MAX_FILE_BYTES = 16 * 1024 * 1024  # RLIMIT_FSIZE
MAX_OPEN_FILES = 64  # RLIMIT_NOFILE
TASK_SLACK = 256  # RLIMIT_NPROC = tasks the user already has + this
STDERR_HARD_LIMIT = 10 * 1024 * 1024  # drained bytes after which a stderr flood is killed
MAX_OUTPUT_DEPTH = 64
TERM_GRACE_SECONDS = 0.5
DRAIN_GRACE_SECONDS = 0.3
SNIPPET_BYTES = 200
_CHUNK = 65536

# Runs in the child (python -I -S): apply limits, then exec. Any problem exits 125 with a marker.
# argv: limits-json, names-to-drop-json, executable, argv...  The names to drop are variables that
# only the launcher itself needed (LD_LIBRARY_PATH for a shared-libpython interpreter); values
# never appear on a command line.
_LAUNCHER = r"""
import json, os, resource, sys
try:
    for name in json.loads(sys.argv[2]):
        os.environ.pop(name, None)
    for name, value in json.loads(sys.argv[1]).items():
        which = getattr(resource, name)
        hard = resource.getrlimit(which)[1]
        if hard != resource.RLIM_INFINITY:
            value = min(value, hard)
        resource.setrlimit(which, (value, value))
    os.execve(sys.argv[3], sys.argv[4:], dict(os.environ))
except BaseException:
    try:
        os.write(2, b"REGRESSAGENT_LAUNCHER_FAILED\n")
    finally:
        os._exit(125)
"""
_LAUNCHER_MARKER = b"REGRESSAGENT_LAUNCHER_FAILED"


_SANDBOX_ROOTS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")
_SANDBOX_CACHE: dict[str, str | None] = {}
_USERNS_FLAG_CACHE: dict[str, bool] = {}


def _find_bwrap() -> str | None:
    found = shutil.which("bwrap", path=FIXED_PATH)
    return os.path.realpath(found) if found else None


def _supports_disable_userns(bwrap: str) -> bool:
    if bwrap not in _USERNS_FLAG_CACHE:
        try:
            help_text = subprocess.run(
                [bwrap, "--help"], capture_output=True, text=True, timeout=10, check=False
            )
            _USERNS_FLAG_CACHE[bwrap] = "--disable-userns" in (help_text.stdout + help_text.stderr)
        except (OSError, subprocess.SubprocessError):
            _USERNS_FLAG_CACHE[bwrap] = False
    return _USERNS_FLAG_CACHE[bwrap]


def _sandbox_flags(bwrap: str) -> list[str]:
    """The fixed sandbox skeleton: private namespaces (incl. NO network), read-only system dirs,
    tmpfs /tmp and /work, no capabilities. Extra host paths come only from ``ro_binds``."""
    flags = ["--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL"]
    if _supports_disable_userns(bwrap):
        flags += ["--unshare-user", "--disable-userns"]  # no nested user namespaces inside
    for name in _SANDBOX_ROOTS:
        path = "/" + name.lstrip("/")
        if os.path.islink(path):
            flags += ["--symlink", os.readlink(path), path]
        elif os.path.isdir(path):
            flags += ["--ro-bind", path, path]
    flags += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/work", "--chdir", "/work"]
    if os.path.exists("/etc/ld.so.cache"):
        flags += ["--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache"]
    return flags


def _sandbox_problem(bwrap: str) -> str | None:
    """None if bubblewrap can build the sandbox on this machine, else a short reason (cached)."""
    if bwrap not in _SANDBOX_CACHE:
        try:
            done = subprocess.run(
                [bwrap, *_sandbox_flags(bwrap), "/usr/bin/true"],
                capture_output=True, timeout=15, check=False,
            )  # fmt: skip
            reason = done.stderr.decode("utf-8", errors="replace").strip().splitlines()
            _SANDBOX_CACHE[bwrap] = None if done.returncode == 0 else (reason[0][:160] if reason else "failed")
        except (OSError, subprocess.SubprocessError) as exc:
            _SANDBOX_CACHE[bwrap] = f"could not run bwrap: {type(exc).__name__}"
    return _SANDBOX_CACHE[bwrap]


def _sandbox_command(cfg: AdapterConfig, executable: str) -> tuple[str, list[str]]:
    """The (program, argv) that runs the agent inside bubblewrap; refuses instead of degrading."""
    bwrap = _find_bwrap()
    if bwrap is None:
        raise AdapterError(
            "tier B needs bubblewrap (bwrap), which was not found (for example: apt install "
            "bubblewrap); refusing to run unsandboxed"
        )
    problem = _sandbox_problem(bwrap)
    if problem:
        raise AdapterError(
            f"bubblewrap cannot create a sandbox here ({problem}); refusing to run unsandboxed"
        )
    visible = (*_SANDBOX_ROOTS, *cfg.ro_binds)
    internal = ("/tmp", "/work", "/proc", "/dev")  # exist inside the sandbox on their own

    def inside(path: str, roots: tuple[str, ...]) -> bool:
        return any(path == root or path.startswith(root.rstrip("/") + "/") for root in roots)

    if not inside(executable, visible):
        raise AdapterError(
            "the executable is not visible inside the sandbox; add its directory to ro_binds"
        )
    for arg in cfg.argv[1:]:  # a script/data path that exists on the host but is not mounted
        if arg.startswith("/") and os.path.exists(arg) and not inside(os.path.realpath(arg), (*visible, *internal)):
            raise AdapterError(
                "an argument path exists on the host but is not visible inside the sandbox; "
                "add its directory to ro_binds"
            )
    binds: list[str] = []
    for path in cfg.ro_binds:
        binds += ["--ro-bind", path, path]
    return bwrap, ["bwrap", *_sandbox_flags(bwrap), *binds, "--", executable, *cfg.argv[1:]]


class AdapterError(Exception):
    """The adapter run failed; the message is short, single-line and safe to store."""


class SubprocessAdapter:
    """Implements the ``AgentRunner`` protocol by running a configured command once per input."""

    def __init__(self, config: AdapterConfig, environ: Mapping[str, str] | None = None) -> None:
        self._config = config
        self._environ: Mapping[str, str] = os.environ if environ is None else environ
        self.last_metadata: RunMetadata | None = None

    # ------------------------------------------------------------------ public
    def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        cfg = self._config
        limits = self._limits_or_none()
        meta: dict[str, Any] = {
            "adapter_name": cfg.name,
            "adapter_tier": cfg.tier,
            "argv": list(cfg.argv),
            "env_names": list(cfg.env_allow),
            "timeout_seconds": float(cfg.timeout_seconds),
            "limits": dict(limits or {}),
        }
        self.last_metadata = RunMetadata(**meta)
        try:
            return self._run(input_data, meta, limits)
        finally:
            self.last_metadata = RunMetadata(**meta)

    # ------------------------------------------------------------------ internals
    def _limits_or_none(self) -> dict[str, int] | None:
        cfg = self._config
        try:
            tasks = _count_user_tasks()
        except OSError:
            return None
        return {
            "RLIMIT_CPU": math.ceil(cfg.timeout_seconds) + 1,
            "RLIMIT_AS": cfg.memory_limit_bytes,
            "RLIMIT_FSIZE": MAX_FILE_BYTES,
            "RLIMIT_NOFILE": MAX_OPEN_FILES,
            "RLIMIT_CORE": 0,
            "RLIMIT_NPROC": tasks + TASK_SLACK,
        }

    def _run(
        self, input_data: dict[str, Any], meta: dict[str, Any], limits: dict[str, int] | None
    ) -> dict[str, Any]:
        cfg = self._config
        if not sys.platform.startswith("linux"):
            raise AdapterError("adapters are only supported on Linux (refusing to run unprotected)")
        if limits is None:
            raise AdapterError("cannot read /proc to size the process limit; refusing to run")
        if not sys.executable:
            raise AdapterError("cannot locate the Python interpreter for the launcher")
        executable = _check_executable(cfg, meta)
        try:
            payload = json.dumps(input_data, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            raise AdapterError("input is not JSON serializable") from None

        env = {"PATH": FIXED_PATH}
        for name in cfg.env_allow:
            if name in self._environ:
                env[name] = self._environ[name]

        # The launcher is OUR interpreter: it may need the loader path to start (setup-python and
        # pyenv builds use a shared libpython). The agent never gets it unless allow-listed.
        launcher_env = dict(env)
        launcher_only: list[str] = []
        loader_path = os.environ.get("LD_LIBRARY_PATH")
        if loader_path is not None and "LD_LIBRARY_PATH" not in env:
            launcher_env["LD_LIBRARY_PATH"] = loader_path
            launcher_only.append("LD_LIBRARY_PATH")

        workdir = tempfile.mkdtemp(prefix="regressagent-")
        try:
            exec_path, exec_argv = executable, list(cfg.argv)
            if cfg.tier == "B":
                exec_path, exec_argv = _sandbox_command(cfg, executable)
            args = [
                sys.executable, "-I", "-S", "-c", _LAUNCHER,
                json.dumps(limits), json.dumps(launcher_only), exec_path, *exec_argv,
            ]
            started = time.monotonic()
            try:
                proc = subprocess.Popen(
                    args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    cwd=workdir, env=launcher_env, close_fds=True, start_new_session=True,
                    shell=False, bufsize=0,
                )
            except OSError as exc:
                raise AdapterError(f"could not start the process ({exc.strerror})") from None
            outcome = _Supervisor(proc, payload, cfg).supervise(started)
            meta["duration_ms"] = int((time.monotonic() - started) * 1000)
            meta["exit_code"] = proc.returncode if proc.returncode >= 0 else None
            meta["stdout_truncated"] = outcome.stdout_truncated
            meta["stderr_truncated"] = outcome.stderr_truncated
        finally:
            _remove_tree(workdir)

        if outcome.failure:
            raise AdapterError(_with_stderr(outcome.failure, outcome.stderr))
        code = proc.returncode
        if code == 125 and outcome.stderr.startswith(_LAUNCHER_MARKER):
            raise AdapterError("could not start the executable (limits or exec failed)")
        if code < 0:
            raise AdapterError(_with_stderr(f"terminated by signal {_signal_name(-code)}", outcome.stderr))
        if code != 0:
            raise AdapterError(_with_stderr(f"exited with code {code}", outcome.stderr))
        return _parse_output(outcome.stdout)


class _Outcome:
    def __init__(self) -> None:
        self.stdout = b""
        self.stderr = b""
        self.stdout_truncated = False
        self.stderr_truncated = False
        self.failure: str | None = None


class _Supervisor:
    """Owns the child until it is reaped: I/O loop, deadline, caps, process-group cleanup."""

    def __init__(self, proc: subprocess.Popen[bytes], payload: bytes, cfg: AdapterConfig) -> None:
        self._proc = proc
        self._payload = payload
        self._cfg = cfg
        self._pgid = proc.pid  # start_new_session=True: the child leads its own group

    def supervise(self, started: float) -> _Outcome:
        out = _Outcome()
        try:
            self._io_loop(out, started + self._cfg.timeout_seconds)
        finally:
            self._cleanup(out)
        return out

    # -- I/O -----------------------------------------------------------------
    def _io_loop(self, out: _Outcome, deadline: float) -> None:
        proc, cfg = self._proc, self._cfg
        stdout_buf, stderr_buf = bytearray(), bytearray()
        stderr_total = 0
        offset = 0
        sel = selectors.DefaultSelector()
        streams = {"out": proc.stdout, "err": proc.stderr, "in": proc.stdin}
        for stream in streams.values():
            os.set_blocking(stream.fileno(), False)  # type: ignore[union-attr]
        sel.register(proc.stdout, selectors.EVENT_READ, "out")  # type: ignore[arg-type]
        sel.register(proc.stderr, selectors.EVENT_READ, "err")  # type: ignore[arg-type]
        if self._payload:
            sel.register(proc.stdin, selectors.EVENT_WRITE, "in")  # type: ignore[arg-type]
        else:
            self._close_stdin()
        exited_at: float | None = None
        try:
            while True:
                now = time.monotonic()
                if now >= deadline:
                    out.failure = f"timed out after {cfg.timeout_seconds:g}s"
                    break
                pipes_open = bool({k.data for k in sel.get_map().values()} & {"out", "err"})
                if exited_at is None and self._leader_exited():
                    exited_at = now
                if exited_at is not None and (
                    not pipes_open or now - exited_at > DRAIN_GRACE_SECONDS
                ):
                    break  # leader gone and drained (or a child still holds the pipes open)
                if not sel.get_map():
                    time.sleep(0.01)  # nothing to select on: wait for the leader to exit
                    continue
                for key, _ in sel.select(min(deadline - now, 0.05)):
                    kind = key.data
                    if kind == "in":
                        chunk = self._payload[offset: offset + _CHUNK]
                        try:
                            offset += os.write(proc.stdin.fileno(), chunk)  # type: ignore[union-attr]
                        except BlockingIOError:
                            continue
                        except OSError:  # child closed its stdin / exited: not our problem
                            offset = len(self._payload)
                        if offset >= len(self._payload):
                            sel.unregister(proc.stdin)  # type: ignore[arg-type]
                            self._close_stdin()
                        continue
                    try:
                        data = os.read(key.fd, _CHUNK)
                    except BlockingIOError:
                        continue
                    if not data:
                        sel.unregister(key.fileobj)
                    elif kind == "out":
                        stdout_buf += data
                        if len(stdout_buf) > cfg.max_stdout_bytes:
                            out.stdout_truncated = True
                            out.failure = f"stdout exceeded {cfg.max_stdout_bytes} bytes"
                    else:
                        stderr_total += len(data)
                        room = cfg.max_stderr_bytes - len(stderr_buf)
                        if room > 0:
                            stderr_buf += data[:room]
                        if len(data) > max(room, 0):
                            out.stderr_truncated = True
                        if stderr_total > STDERR_HARD_LIMIT:
                            out.failure = "stderr flood (output was cut off)"
                if out.failure:
                    break
        finally:
            sel.close()
            self._close_stdin()
        out.stdout = bytes(stdout_buf)
        out.stderr = bytes(stderr_buf)

    def _close_stdin(self) -> None:
        with contextlib.suppress(OSError):
            self._proc.stdin.close()  # type: ignore[union-attr]

    # -- process control -------------------------------------------------------
    def _leader_exited(self) -> bool:
        """True once the leader has exited. Uses WNOWAIT so the group id stays reserved by the
        zombie until we have killed the group (no signalling a recycled id)."""
        try:
            return os.waitid(os.P_PID, self._proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:
            return True

    def _signal_group(self, sig: int) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self._pgid, sig)

    def _cleanup(self, out: _Outcome) -> None:
        """Always: end the whole group, reap the leader, close pipes."""
        proc = self._proc
        try:
            if out.failure or not self._leader_exited():
                self._signal_group(signal.SIGTERM)
                end = time.monotonic() + TERM_GRACE_SECONDS
                while time.monotonic() < end and not self._leader_exited():
                    time.sleep(0.01)
            self._signal_group(signal.SIGKILL)  # stragglers, before the leader is reaped
            proc.wait()
        finally:
            for stream in (proc.stdout, proc.stderr, proc.stdin):
                if stream is not None:
                    with contextlib.suppress(OSError):
                        stream.close()


# ---------------------------------------------------------------------- helpers
def _count_user_tasks() -> int:
    """Threads and processes owned by this user (what RLIMIT_NPROC counts)."""
    uid = os.geteuid()
    total = 0
    with os.scandir("/proc") as entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat().st_uid == uid:
                    total += len(os.listdir(f"/proc/{entry.name}/task"))
            except OSError:
                continue  # process vanished
    if total == 0:
        raise OSError("no processes visible in /proc")
    return total


def _check_executable(cfg: AdapterConfig, meta: dict[str, Any]) -> str:
    resolved = os.path.realpath(cfg.argv[0])
    try:
        info = os.stat(resolved)
    except OSError:
        raise AdapterError("executable not found") from None
    if not stat.S_ISREG(info.st_mode) or not os.access(resolved, os.X_OK):
        raise AdapterError("executable is not a regular executable file")
    if info.st_mode & 0o022:
        raise AdapterError("executable is writable by group or others; refusing to run it")
    digest = hashlib.sha256()
    try:
        with open(resolved, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        raise AdapterError("cannot read the executable to hash it") from None
    meta["executable_path"] = resolved
    meta["executable_sha256"] = digest.hexdigest()
    if cfg.executable_sha256 is not None and cfg.executable_sha256 != digest.hexdigest():
        raise AdapterError("executable sha256 does not match the pinned value; refusing to run")
    return resolved


def _remove_tree(path: str) -> None:
    """Delete the temp cwd even if the child chmod-ed things; never follows symlinks."""
    for dirpath, dirnames, _files in os.walk(path):
        for name in dirnames:
            full = os.path.join(dirpath, name)
            if not os.path.islink(full):
                with contextlib.suppress(OSError):
                    os.chmod(full, 0o700)
    shutil.rmtree(path, ignore_errors=True)


def _signal_name(number: int) -> str:
    try:
        return signal.Signals(number).name
    except ValueError:
        return str(number)


def _with_stderr(message: str, stderr: bytes) -> str:
    snippet = "".join(
        chr(b) if 32 <= b < 127 else "?" for b in stderr[:SNIPPET_BYTES]
    ).strip()
    return f"{message}; stderr: {snippet}" if snippet else message


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name}")


def _depth_ok(value: Any) -> bool:
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_OUTPUT_DEPTH:
            return False
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
    return True


def _parse_output(raw: bytes, what: str = "stdout") -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AdapterError(f"{what} is not valid UTF-8") from None
    try:
        data = json.loads(text, parse_constant=_reject_constant)
    except RecursionError:
        raise AdapterError(f"{what} is not valid JSON (nested too deeply)") from None
    except ValueError:
        raise AdapterError(f"{what} is not valid JSON") from None
    if not isinstance(data, dict):
        raise AdapterError(f"{what} JSON must be an object, got {type(data).__name__}")
    if not _depth_ok(data):
        raise AdapterError(f"{what} JSON is nested too deeply (limit {MAX_OUTPUT_DEPTH})")
    return data


__all__ = ["AdapterError", "SubprocessAdapter"]
