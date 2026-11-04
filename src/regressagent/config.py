"""Loader for the local, user-authored ``regressagent.toml`` (D-010, ADAPTER_ISOLATION.md s2/s8).

This module only *parses and validates*. It never executes anything, never opens or hashes the
executables it names, and never contacts the network: existence, realpath and SHA-256 checks
belong to the adapter at run time. Commands live here and nowhere else: case files, traces and
the database never contain commands, URLs or environment values.

Safety properties:
- the file is opened without following symlinks, must be a regular file owned by the current
  user or root, must not be group/world writable, and is size-capped before it is parsed;
- unknown keys are errors (no silent typos, no smuggled options);
- ``argv`` is an explicit list (never a string a shell could split) with an absolute,
  normalised executable path;
- every adapter entry must carry ``acknowledge_not_sandboxed = true`` (Tier A is NOT a sandbox);
- limits cannot exceed hard maxima; environment allow-list holds plain *names* only, and names
  that change how programs load code (``LD_*`` except an explicit ``LD_LIBRARY_PATH``, ``PYTHON*``, ``PATH`` ...) are refused;
- error messages never echo argv values (they may contain secrets).
"""
from __future__ import annotations

import errno
import ipaddress
import math
import os
import posixpath
import re
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from .runner import RUNNERS

MAX_CONFIG_BYTES = 64 * 1024
MAX_ADAPTERS = 64
MAX_ARGV_ITEMS = 64
MAX_ARGV_ITEM_LENGTH = 4096
MAX_ENV_ALLOW = 32
MAX_RO_BINDS = 16
MAX_BIND_PATH = 4096
MAX_URL_LENGTH = 2048
MAX_ALLOW_HOSTS = 32
MAX_HEADERS = 16
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 1_000_000  # matches the storage cap per stored result

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300
DEFAULT_MAX_STDOUT_BYTES = 1_000_000
MAX_STDOUT_BYTES = 1_000_000  # matches the storage cap per stored result
DEFAULT_MAX_STDERR_BYTES = 64 * 1024
MAX_STDERR_BYTES = 1_000_000
DEFAULT_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
MIN_MEMORY_LIMIT_BYTES = 16 * 1024 * 1024
MAX_MEMORY_LIMIT_BYTES = 16 * 1024**3

_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOP_KEYS = {"adapters"}
_HTTP_KEYS = {
    "type", "url", "timeout_seconds", "max_response_bytes", "allow_hosts",
    "allow_insecure_http", "headers_env",
}
_HOSTNAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
_HEADER_NAME = re.compile(r"[A-Za-z0-9-]{1,64}")
_FORBIDDEN_HEADERS = {
    "host", "content-length", "content-type", "transfer-encoding", "connection", "accept",
    "user-agent", "expect", "upgrade", "te", "trailer", "keep-alive",
}
_ADAPTER_KEYS = {
    "type", "tier", "argv", "acknowledge_not_sandboxed", "ro_binds", "timeout_seconds", "max_stdout_bytes",
    "max_stderr_bytes", "memory_limit_bytes", "env_allow", "executable_sha256",
}
_FORBIDDEN_ENV_NAMES = {
    "PATH", "IFS", "ENV", "BASH_ENV", "PERL5OPT", "PERL5LIB", "RUBYOPT", "RUBYLIB",
    "NODE_OPTIONS", "NODE_PATH", "CLASSPATH", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS",
}
_FORBIDDEN_ENV_PREFIXES = ("LD_", "DYLD_", "PYTHON")


class ConfigError(Exception):
    """A predictable, user-facing configuration problem (CLI maps it to exit code 2)."""


@dataclass(frozen=True)
class AdapterConfig:
    name: str
    tier: str
    argv: tuple[str, ...]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES
    memory_limit_bytes: int = DEFAULT_MEMORY_LIMIT_BYTES
    env_allow: tuple[str, ...] = ()
    executable_sha256: str | None = None
    ro_binds: tuple[str, ...] = ()  # tier B only: extra host paths mounted read-only in the sandbox


@dataclass(frozen=True)
class HttpAdapterConfig:
    """An HTTP endpoint that receives the case input as a JSON POST (ADAPTER_ISOLATION.md s4)."""

    name: str
    url: str  # normalised, no credentials/query/fragment
    scheme: str
    host: str  # lowercase hostname or IP literal (no brackets)
    port: int
    path: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    allow_hosts: tuple[str, ...] = ()
    allow_insecure_http: bool = False
    headers_env: tuple[tuple[str, str], ...] = ()  # (header name, env var NAME holding the value)


@dataclass(frozen=True)
class Config:
    adapters: Mapping[str, AdapterConfig | HttpAdapterConfig]


def load_config(path: str | os.PathLike[str]) -> Config:
    """Read, parse and validate a config file. Raises ``ConfigError`` on any problem."""
    text = _read_checked(os.fspath(path))
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from None
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise ConfigError(f"unknown top-level key(s): {_names(unknown)}")
    table = data.get("adapters", {})
    if not isinstance(table, dict):
        raise ConfigError("'adapters' must be a table of adapter entries")
    if len(table) > MAX_ADAPTERS:
        raise ConfigError(f"at most {MAX_ADAPTERS} adapters are allowed")
    adapters = {name: _parse_adapter(name, entry) for name, entry in table.items()}
    return Config(adapters=MappingProxyType(adapters))


def _names(keys: set[str]) -> str:
    return ", ".join(repr(k[:40]) for k in sorted(keys)[:5])


def _read_checked(path: str) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise ConfigError("config file not found") from None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ConfigError("config file is a symlink; refusing to follow it") from None
        raise ConfigError(f"cannot read config file ({exc.strerror})") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ConfigError("config path is not a regular file")
        if info.st_mode & 0o022:
            raise ConfigError("config file must not be writable by group or others")
        if hasattr(os, "geteuid") and info.st_uid not in (0, os.geteuid()):
            raise ConfigError("config file must be owned by you or root")
        if info.st_size > MAX_CONFIG_BYTES:
            raise ConfigError(f"config file is too large (limit {MAX_CONFIG_BYTES} bytes)")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(MAX_CONFIG_BYTES + 1)
    except OSError as exc:
        raise ConfigError(f"cannot read config file ({exc.strerror})") from None
    finally:
        os.close(fd)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigError(f"config file is too large (limit {MAX_CONFIG_BYTES} bytes)")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError("config file is not valid UTF-8") from None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _bounded_int(entry: dict[str, Any], key: str, default: int, low: int, high: int, who: str) -> int:
    value = entry.get(key, default)
    if not _is_int(value) or not low <= value <= high:
        raise ConfigError(f"adapter {who}: {key} must be an integer from {low} to {high}")
    return value


def _parse_adapter(name: str, entry: Any) -> AdapterConfig | HttpAdapterConfig:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ConfigError(
            f"invalid adapter name {name[:40]!r}: use 1-64 chars of a-z, 0-9, '_' or '-', "
            "starting with a letter or digit"
        )
    if name in RUNNERS:
        raise ConfigError(f"adapter name {name!r} would shadow a built-in runner")
    if not isinstance(entry, dict):
        raise ConfigError(f"adapter {name}: entry must be a table")
    kind = entry.get("type", "subprocess")
    if kind == "http":
        return _parse_http(name, entry)
    if kind != "subprocess":
        raise ConfigError(f"adapter {name}: type must be \"subprocess\" or \"http\"")
    unknown = set(entry) - _ADAPTER_KEYS
    if unknown:
        raise ConfigError(f"adapter {name}: unknown key(s): {_names(unknown)}")

    tier = entry.get("tier")
    if tier not in ("A", "B"):
        raise ConfigError(f"adapter {name}: tier must be \"A\" (no sandbox) or \"B\" (bubblewrap sandbox)")
    if tier == "A":
        if entry.get("acknowledge_not_sandboxed") is not True:
            raise ConfigError(
                f"adapter {name}: acknowledge_not_sandboxed = true is required. Tier A adapters run "
                "the command as you, with your files and network; use them for trusted code only "
                "(or use tier B)"
            )
        if "ro_binds" in entry:
            raise ConfigError(f"adapter {name}: ro_binds is only used with tier B")
    ro_binds = _parse_ro_binds(name, entry.get("ro_binds", [])) if tier == "B" else ()

    argv = _parse_argv(name, entry.get("argv"))

    timeout = entry.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int | float)
        or not math.isfinite(timeout)
        or not 0 < timeout <= MAX_TIMEOUT_SECONDS
    ):
        raise ConfigError(
            f"adapter {name}: timeout_seconds must be a number above 0 and at most "
            f"{MAX_TIMEOUT_SECONDS}"
        )

    return AdapterConfig(
        name=name,
        tier=tier,
        argv=argv,
        timeout_seconds=timeout,
        max_stdout_bytes=_bounded_int(
            entry, "max_stdout_bytes", DEFAULT_MAX_STDOUT_BYTES, 1, MAX_STDOUT_BYTES, name
        ),
        max_stderr_bytes=_bounded_int(
            entry, "max_stderr_bytes", DEFAULT_MAX_STDERR_BYTES, 1, MAX_STDERR_BYTES, name
        ),
        memory_limit_bytes=_bounded_int(
            entry, "memory_limit_bytes", DEFAULT_MEMORY_LIMIT_BYTES,
            MIN_MEMORY_LIMIT_BYTES, MAX_MEMORY_LIMIT_BYTES, name,
        ),
        env_allow=_parse_env_allow(name, entry.get("env_allow", [])),
        executable_sha256=_parse_sha(name, entry.get("executable_sha256")),
        ro_binds=ro_binds,
    )


def _parse_argv(name: str, argv: Any) -> tuple[str, ...]:
    if not isinstance(argv, list) or not 1 <= len(argv) <= MAX_ARGV_ITEMS:
        raise ConfigError(
            f"adapter {name}: argv must be a list of 1-{MAX_ARGV_ITEMS} strings "
            "(a single command string is never accepted: no shell)"
        )
    for index, item in enumerate(argv):
        if not isinstance(item, str) or not 1 <= len(item) <= MAX_ARGV_ITEM_LENGTH:
            raise ConfigError(
                f"adapter {name}: argv[{index}] must be a non-empty string of at most "
                f"{MAX_ARGV_ITEM_LENGTH} characters"
            )
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in item):
            raise ConfigError(f"adapter {name}: argv[{index}] contains a control character")
    exe = argv[0]
    if (
        not exe.startswith("/")
        or any(ch.isspace() for ch in exe)
        or posixpath.normpath(exe) != exe
    ):
        raise ConfigError(
            f"adapter {name}: argv[0] must be an absolute, normalised path without spaces "
            "(no '..', no '//', no trailing '/')"
        )
    return tuple(argv)


def _parse_ro_binds(name: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_RO_BINDS:
        raise ConfigError(f"adapter {name}: ro_binds must be a list of at most {MAX_RO_BINDS} paths")
    paths: list[str] = []
    for item in value:
        problem = None
        if not isinstance(item, str) or not item or len(item) > MAX_BIND_PATH:
            problem = f"each ro_binds entry must be a path of 1-{MAX_BIND_PATH} characters"
        elif any(ord(c) < 32 or ord(c) == 127 for c in item):
            problem = "ro_binds paths may not contain control characters"
        elif not item.startswith("/") or os.path.normpath(item) != item or item == "/":
            problem = "ro_binds paths must be absolute, normalised (no '..', '//' or trailing '/') and not '/'"
        elif any(item == root or item.startswith(root + "/") for root in ("/proc", "/dev", "/sys")):
            problem = "ro_binds may not expose /proc, /dev or /sys"
        elif not os.path.exists(item):
            problem = f"ro_binds path does not exist: {item}"
        elif item in paths:
            problem = f"duplicate ro_binds path: {item}"
        if problem:
            raise ConfigError(f"adapter {name}: {problem}")
        paths.append(item)
    return tuple(paths)


def _parse_timeout(name: str, entry: dict[str, Any]) -> float:
    timeout = entry.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int | float)
        or not math.isfinite(timeout)
        or not 0 < timeout <= MAX_TIMEOUT_SECONDS
    ):
        raise ConfigError(
            f"adapter {name}: timeout_seconds must be a number above 0 and at most "
            f"{MAX_TIMEOUT_SECONDS}"
        )
    return timeout


def _host_or_none(value: Any) -> str | None:
    """A lowercase hostname or IP literal, else None."""
    if not isinstance(value, str):
        return None
    if _HOSTNAME.fullmatch(value):
        return value
    try:
        return str(ipaddress.ip_address(value)) if "%" not in value else None
    except ValueError:
        return None


def _parse_http(name: str, entry: dict[str, Any]) -> HttpAdapterConfig:
    unknown = set(entry) - _HTTP_KEYS
    if unknown:
        raise ConfigError(f"adapter {name}: unknown key(s) for an http adapter: {_names(unknown)}")

    allow_hosts = entry.get("allow_hosts", [])
    if not isinstance(allow_hosts, list) or len(allow_hosts) > MAX_ALLOW_HOSTS:
        raise ConfigError(f"adapter {name}: allow_hosts must be a list of at most {MAX_ALLOW_HOSTS} hosts")
    hosts: list[str] = []
    for item in allow_hosts:
        host = _host_or_none(item)
        if host is None or host != item or host in hosts:
            raise ConfigError(
                f"adapter {name}: allow_hosts entries must be distinct lowercase hostnames or IPs "
                "(no scheme, port or path)"
            )
        hosts.append(host)
    insecure = entry.get("allow_insecure_http", False)
    if not isinstance(insecure, bool):
        raise ConfigError(f"adapter {name}: allow_insecure_http must be true or false")

    url, scheme, host, port, path = _parse_url(name, entry.get("url"))
    loopback = host == "localhost" or _is_loopback_ip(host)
    if not loopback and host not in hosts:
        raise ConfigError(
            f"adapter {name}: host {host!r} is not a loopback address and is not listed in allow_hosts"
        )
    if scheme == "http" and not loopback and not insecure:
        raise ConfigError(
            f"adapter {name}: plain http to {host!r} sends data unencrypted; use https or set "
            "allow_insecure_http = true"
        )
    return HttpAdapterConfig(
        name=name,
        url=url,
        scheme=scheme,
        host=host,
        port=port,
        path=path,
        timeout_seconds=_parse_timeout(name, entry),
        max_response_bytes=_bounded_int(
            entry, "max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES, 1, MAX_RESPONSE_BYTES, name
        ),
        allow_hosts=tuple(hosts),
        allow_insecure_http=insecure,
        headers_env=_parse_headers_env(name, entry.get("headers_env", {})),
    )


def _is_loopback_ip(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _parse_url(name: str, value: Any) -> tuple[str, str, str, int, str]:
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        raise ConfigError(f"adapter {name}: url must be a string of at most {MAX_URL_LENGTH} characters")
    if any(not 32 < ord(c) < 127 for c in value):
        raise ConfigError(f"adapter {name}: url may only contain printable ASCII without spaces")
    try:
        parts = urlsplit(value)
        port = parts.port
        hostname = parts.hostname
    except ValueError:
        raise ConfigError(f"adapter {name}: url is not valid") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ConfigError(f"adapter {name}: url scheme must be http or https")
    if "@" in parts.netloc:
        raise ConfigError(f"adapter {name}: url must not contain credentials; use headers_env")
    if parts.query or parts.fragment or "?" in value or "#" in value:
        raise ConfigError(
            f"adapter {name}: url must not contain a query or fragment; use headers_env for credentials"
        )
    host = _host_or_none(hostname)
    if host is None:
        raise ConfigError(f"adapter {name}: url has no valid host")
    default_port = 443 if scheme == "https" else 80
    port = default_port if port is None else port
    if not 1 <= port <= 65535:
        raise ConfigError(f"adapter {name}: url port must be 1-65535")
    path = parts.path or "/"
    shown = f"[{host}]" if ":" in host else host
    netloc = shown if port == default_port else f"{shown}:{port}"
    return f"{scheme}://{netloc}{path}", scheme, host, port, path


def _parse_headers_env(name: str, value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict) or len(value) > MAX_HEADERS:
        raise ConfigError(f"adapter {name}: headers_env must be a table of at most {MAX_HEADERS} entries")
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for header, env in value.items():
        if not _HEADER_NAME.fullmatch(header) or header.lower() in _FORBIDDEN_HEADERS:
            raise ConfigError(f"adapter {name}: headers_env header name {header[:40]!r} is not allowed")
        if header.lower() in seen:
            raise ConfigError(f"adapter {name}: duplicate header {header!r}")
        seen.add(header.lower())
        upper = env.upper() if isinstance(env, str) else ""
        if (
            not isinstance(env, str)
            or not _ENV_NAME.fullmatch(env)
            or upper in _FORBIDDEN_ENV_NAMES
            or upper.startswith(_FORBIDDEN_ENV_PREFIXES)
        ):
            raise ConfigError(
                f"adapter {name}: headers_env values must be plain environment variable NAMES "
                "(the secret itself is read from the environment at run time)"
            )
        pairs.append((header, env))
    return tuple(pairs)


def _parse_env_allow(name: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_ENV_ALLOW:
        raise ConfigError(f"adapter {name}: env_allow must be a list of at most {MAX_ENV_ALLOW} names")
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not _ENV_NAME.fullmatch(item):
            raise ConfigError(f"adapter {name}: env_allow entries must be plain variable names")
        upper = item.upper()
        if upper != "LD_LIBRARY_PATH" and (
            upper in _FORBIDDEN_ENV_NAMES or upper.startswith(_FORBIDDEN_ENV_PREFIXES)
        ):
            raise ConfigError(
                f"adapter {name}: env_allow may not include {item!r} (it changes how programs "
                "find or load code)"
            )
        if item in seen:
            raise ConfigError(f"adapter {name}: duplicate env_allow entry {item!r}")
        seen.add(item)
    return tuple(value)


def _parse_sha(name: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ConfigError(f"adapter {name}: executable_sha256 must be 64 lowercase hex characters")
    return value
