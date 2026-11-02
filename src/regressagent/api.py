"""Local HTTP API (D-020): a small JSON API for the UI and scripts, stdlib only.

Threat model: anything on the machine (and any web page open in a browser) can try to talk to a
local port. So the server (1) binds to loopback only, (2) requires a random bearer token that is
printed once at start-up, (3) rejects requests whose Host header is not the loopback address it
listens on (DNS rebinding), (4) rejects foreign Origin / cross-site Sec-Fetch-Site (CSRF), sends no
CORS headers, (5) checks all of that BEFORE reading a request body, (6) caps bodies at 1 MB and
refuses chunked bodies, (7) never returns tracebacks, and (8) can only run the built-in demo
runners: adapters (commands, HTTP endpoints) are never run through the API.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import re
import secrets
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import ValidationError

from . import __version__
from .cli import MAX_REPEAT, _repeat_summary, _run_repeated
from .comparison import compare_runs
from .models import RegressionCase
from .report import render_json, render_markdown
from .runner import RUNNERS
from .storage import (
    CaseExistsError,
    NotFoundError,
    PayloadTooLargeError,
    Repository,
    StorageError,
    TraceExistsError,
)
from .trace import MAX_TRACE_BYTES, TraceError, _too_deep, case_from_trace, parse_trace

MAX_BODY_BYTES = 1_000_000
_ID_MAX = 128
_INT = re.compile(r"[0-9]{1,18}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f/\\]")
_TRUE = {"1", "true", "yes"}


_UI_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
UI_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


class ApiError(Exception):
    def __init__(self, status: int, message: str, details: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details


def _reject_constant(name: str) -> Any:
    raise ValueError(name)


def _problems(exc: ValidationError) -> list[dict[str, str]]:
    return [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()[:10]]


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Api:
    def __init__(self, db_path: str, token: str, port: int) -> None:
        self._db = db_path
        self._token = token
        self._hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        ui = resources.files("regressagent") / "ui"
        self._ui = {path: ((ui / name).read_bytes(), ctype) for path, (name, ctype) in _UI_FILES.items()}

    def static(
        self, method: str, target: str, headers: Mapping[str, str]
    ) -> tuple[int, bytes, dict[str, str]] | None:
        """The UI files (no token needed: they contain no data). None if ``target`` is not one."""
        entry = self._ui.get(urlsplit(target).path)
        if entry is None or method not in ("GET", "HEAD"):
            return None
        try:
            self._check_origin(headers)
        except ApiError as exc:
            return exc.status, json.dumps({"error": exc.message}).encode(), {"Content-Type": "application/json"}
        return 200, entry[0], {"Content-Type": entry[1], "Content-Security-Policy": UI_CSP}

    # ------------------------------------------------------------------ security + routing
    def handle(
        self,
        method: str,
        target: str,
        headers: Mapping[str, str],
        read_body: Callable[[], bytes],
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        extra: dict[str, str] = {}
        try:
            self._check_origin(headers)
            if method not in ("GET", "POST", "DELETE"):
                extra["Allow"] = "GET, POST, DELETE"
                raise ApiError(405, "method not allowed")
            if not urlsplit(target).path.startswith("/api/"):
                raise ApiError(404, "not found")
            self._check_token(headers.get("Authorization", ""), extra)
            status, body = self._guarded(method, target, headers, read_body)
            return status, body, extra
        except ApiError as exc:
            body: dict[str, Any] = {"error": exc.message}
            if exc.details:
                body["details"] = exc.details
            return exc.status, body, extra
        except Exception:  # noqa: BLE001 - never leak internals to the client
            return 500, {"error": "internal error"}, extra

    def _check_origin(self, headers: Mapping[str, str]) -> None:
        if headers.get("Host", "").lower() not in self._hosts:
            raise ApiError(403, "forbidden host")
        origin = headers.get("Origin")
        if origin is not None and origin not in {f"http://{h}" for h in self._hosts}:
            raise ApiError(403, "forbidden origin")
        if headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            raise ApiError(403, "cross-site requests are not allowed")

    def _check_token(self, value: str, extra: dict[str, str]) -> None:
        scheme, _, given = value.partition(" ")
        ok = scheme == "Bearer" and hmac.compare_digest(given.encode(), self._token.encode())
        if not ok:
            extra["WWW-Authenticate"] = "Bearer"
            raise ApiError(401, "missing or invalid token")

    # ------------------------------------------------------------------ error mapping
    def _guarded(self, method, target, headers, read_body) -> tuple[int, dict[str, Any]]:
        try:
            return self._dispatch(method, target, headers, read_body)
        except ApiError:
            raise
        except NotFoundError as exc:
            raise ApiError(404, str(exc)) from None
        except (CaseExistsError, TraceExistsError) as exc:
            raise ApiError(409, f"{exc}; pass overwrite=1 to replace it") from None
        except PayloadTooLargeError:
            raise ApiError(413, "payload too large") from None
        except StorageError:
            raise ApiError(500, "storage error") from None
        except ValidationError as exc:
            raise ApiError(422, "validation failed", _problems(exc)) from None
        except TraceError as exc:
            raise ApiError(400, str(exc)) from None
        except ValueError:
            raise ApiError(400, "invalid request") from None

    # ------------------------------------------------------------------ routing
    def _dispatch(self, method, target, headers, read_body) -> tuple[int, dict[str, Any]]:
        parts = urlsplit(target)
        query = parse_qs(parts.query, keep_blank_values=True, max_num_fields=20)
        segs = [unquote(x) for x in parts.path.split("/")[1:]]
        if segs[:2] != ["api", "v1"] or len(segs) < 3:
            raise ApiError(404, "not found")
        rest = segs[2:]
        n = len(rest)

        def body() -> dict[str, Any]:
            return self._json_body(headers, read_body)

        if method == "GET" and rest == ["health"]:
            return 200, {"ok": True, "version": __version__}

        with Repository(self._db) as repo:
            if rest[0] == "cases":
                return self._cases(method, rest, query, body, repo)
            if rest[0] == "traces":
                return self._traces(method, rest, query, body, repo)
            if method == "GET" and rest[0] == "runs" and n == 2:
                run = repo.get_run(self._int(rest[1], "run id"))
                return 200, run.model_dump(mode="json")
            if method == "GET" and rest in (["compare"], ["report"]):
                comparison = compare_runs(
                    repo.get_run(self._query_int(query, "before")),
                    repo.get_run(self._query_int(query, "after")),
                )
                if rest[0] == "compare":
                    return 200, comparison.model_dump(mode="json")
                fmt = self._query_one(query, "format") or "markdown"
                if fmt not in ("markdown", "json"):
                    raise ApiError(400, "format must be markdown or json")
                render = render_json if fmt == "json" else render_markdown
                return 200, {"format": fmt, "verdict": comparison.verdict.value, "text": render(comparison)}
        raise ApiError(404, "not found")

    def _cases(self, method, rest, query, body, repo) -> tuple[int, dict[str, Any]]:
        n = len(rest)
        if n == 1 and method == "GET":
            return 200, repo.list_case_ids()  # type: ignore[return-value]
        if n == 1 and method == "POST":
            case = RegressionCase.model_validate(body())
            self._id(case.case_id)
            digest = repo.save_case(case, overwrite=self._flag(query, "overwrite"))
            return 201, {"case_id": case.case_id, "case_hash": digest}
        case_id = self._id(rest[1]) if n >= 2 else ""
        if n == 2 and method == "GET":
            return 200, repo.get_case(case_id).model_dump(mode="json")
        if n == 2 and method == "DELETE":
            self._confirm(query)
            repo.get_case(case_id)
            repo.delete_case(case_id)
            return 200, {"deleted": case_id}
        if n == 3 and rest[2] == "runs" and method == "GET":
            repo.get_case(case_id)
            return 200, [  # type: ignore[return-value]
                {
                    "run_id": r.run_id,
                    "label": r.label,
                    "runner": r.runner_name,
                    "passed": r.result.passed,
                    "created_at": r.created_at,
                }
                for r in repo.list_runs(case_id)
            ]
        if n == 3 and rest[2] == "run" and method == "POST":
            return 200, self._run(repo, case_id, body())
        raise ApiError(404, "not found")

    def _run(self, repo: Repository, case_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if "adapter" in data or "config" in data:
            raise ApiError(400, "adapters cannot be run through the API; use the CLI")
        if set(data) - {"label", "runner", "repeat"}:
            raise ApiError(400, "unknown field(s); allowed: label, runner, repeat")
        label = data.get("label", "api")
        runner = data.get("runner", "local")
        repeat = data.get("repeat", 1)
        if not isinstance(label, str) or not 1 <= len(label) <= 128:
            raise ApiError(400, "label must be a string of 1-128 characters")
        if not isinstance(runner, str) or runner not in RUNNERS:
            raise ApiError(400, f"runner must be one of: {', '.join(sorted(RUNNERS))}")
        if isinstance(repeat, bool) or not isinstance(repeat, int) or not 1 <= repeat <= MAX_REPEAT:
            raise ApiError(400, f"repeat must be an integer 1-{MAX_REPEAT}")
        case = repo.get_case(case_id)
        results, run_ids = _run_repeated(repo, case, (runner, RUNNERS[runner]), label, repeat)
        summary = _repeat_summary(results, run_ids)
        return {
            "run_ids": run_ids,
            "passed": all(r.passed for r in results),
            "passed_runs": summary["passed"],
            "failed_runs": summary["failed"],
            "stable": summary["stable"],
        }

    def _traces(self, method, rest, query, body, repo) -> tuple[int, dict[str, Any]]:
        n = len(rest)
        if n == 1 and method == "GET":
            return 200, repo.list_trace_ids()  # type: ignore[return-value]
        if n == 1 and method == "POST":
            trace = parse_trace(json.dumps(body()).encode("utf-8"))
            self._id(trace.trace_id)
            digest, redacted = repo.save_trace(trace, overwrite=self._flag(query, "overwrite"))
            return 201, {"saved": True, "trace_id": trace.trace_id, "trace_hash": digest, "redacted": redacted}
        trace_id = self._id(rest[1]) if n >= 2 else ""
        if n == 2 and method == "GET":
            return 200, repo.get_trace(trace_id).model_dump(mode="json")
        if n == 2 and method == "DELETE":
            self._confirm(query)
            repo.get_trace(trace_id)
            repo.delete_trace(trace_id)
            return 200, {"deleted": trace_id}
        if n == 3 and rest[2] == "case" and method == "POST":
            data = body()
            if set(data) - {"case_id", "name", "paths", "save"}:
                raise ApiError(400, "unknown field(s); allowed: case_id, name, paths, save")
            paths = data.get("paths")
            if paths is not None and not (isinstance(paths, list) and all(isinstance(p, str) for p in paths)):
                raise ApiError(400, "paths must be a list of strings")
            for key in ("case_id", "name"):
                if data.get(key) is not None and not isinstance(data[key], str):
                    raise ApiError(400, f"{key} must be a string")
            case, notes = case_from_trace(
                repo.get_trace(trace_id), case_id=data.get("case_id"), name=data.get("name"), paths=paths
            )
            if data.get("save") is True:
                self._id(case.case_id)
                digest = repo.save_case(case, overwrite=self._flag(query, "overwrite"))
                return 201, {"saved": True, "case_hash": digest, "case": case.model_dump(mode="json"), "notes": notes}
            return 200, {"saved": False, "case": case.model_dump(mode="json"), "notes": notes}
        raise ApiError(404, "not found")

    # ------------------------------------------------------------------ small helpers
    @staticmethod
    def _id(value: str) -> str:
        if not 1 <= len(value) <= _ID_MAX or _CONTROL.search(value):
            raise ApiError(400, "invalid id")
        return value

    @staticmethod
    def _int(value: str, what: str) -> int:
        if not _INT.fullmatch(value):
            raise ApiError(400, f"{what} must be a positive integer")
        return int(value)

    @staticmethod
    def _query_one(query: dict[str, list[str]], name: str) -> str | None:
        values = query.get(name)
        if values is None:
            return None
        if len(values) != 1:
            raise ApiError(400, f"query parameter '{name}' must appear once")
        return values[0]

    def _query_int(self, query: dict[str, list[str]], name: str) -> int:
        value = self._query_one(query, name)
        if value is None:
            raise ApiError(400, f"query parameter '{name}' is required")
        return self._int(value, name)

    def _flag(self, query: dict[str, list[str]], name: str) -> bool:
        return (self._query_one(query, name) or "").lower() in _TRUE

    def _confirm(self, query: dict[str, list[str]]) -> None:
        if self._query_one(query, "confirm") != "yes":
            raise ApiError(400, "deleting needs confirm=yes")

    @staticmethod
    def _json_body(headers: Mapping[str, str], read_body: Callable[[], bytes]) -> dict[str, Any]:
        raw = read_body()
        if raw and not headers.get("Content-Type", "").lower().startswith("application/json"):
            raise ApiError(415, "Content-Type must be application/json")
        try:
            data = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise ApiError(400, "body is not valid JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "body must be a JSON object")
        if _too_deep(data):
            raise ApiError(400, "body is nested too deeply")
        return data


# ---------------------------------------------------------------------- the HTTP server
_SAFE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


class _Handler(BaseHTTPRequestHandler):
    server_version = "regressagent"
    sys_version = ""
    protocol_version = "HTTP/1.0"
    timeout = 15  # seconds per socket operation: a stalled client cannot hold a thread forever

    def log_message(self, *args: Any) -> None:  # keep the terminal quiet
        pass

    def _read_body(self) -> bytes:
        if "Transfer-Encoding" in self.headers:
            raise ApiError(411, "Content-Length is required (chunked bodies are not supported)")
        length = self.headers.get("Content-Length")
        if length is None:
            raise ApiError(411, "Content-Length is required")
        if not length.isdigit():
            raise ApiError(400, "invalid Content-Length")
        if int(length) > MAX_BODY_BYTES:
            raise ApiError(413, f"body exceeds {MAX_BODY_BYTES} bytes")
        return self.rfile.read(int(length))

    def _serve(self) -> None:
        api: Api = self.server.api  # type: ignore[attr-defined]
        served = api.static(self.command, self.path, self.headers)
        if served is not None:
            status, payload, extra = served
        else:
            status, body, extra = api.handle(self.command, self.path, self.headers, self._read_body)
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            extra = {"Content-Type": "application/json; charset=utf-8", **extra}
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        for key, value in {**_SAFE_HEADERS, **extra}.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)


for _method in ("GET", "POST", "DELETE", "PUT", "PATCH", "OPTIONS", "HEAD", "TRACE"):
    setattr(_Handler, f"do_{_method}", _Handler._serve)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    api: Api


class _Server6(_Server):
    import socket as _socket

    address_family = _socket.AF_INET6


def make_server(
    db_path: str, host: str = "127.0.0.1", port: int = 0, token: str | None = None
) -> tuple[_Server, str]:
    """Create (but do not start) the API server. Only loopback hosts are accepted."""
    if not _loopback(host):
        raise ValueError("the API only binds to a loopback address (127.0.0.1, ::1 or localhost)")
    token = token or secrets.token_urlsafe(32)
    with Repository(db_path):  # create/migrate once, so concurrent first requests cannot race on it
        pass
    server = (_Server6 if ":" in host else _Server)((host, port), _Handler)
    server.api = Api(db_path, token, server.server_address[1])
    return server, token


__all__ = ["MAX_BODY_BYTES", "MAX_TRACE_BYTES", "Api", "ApiError", "make_server"]
