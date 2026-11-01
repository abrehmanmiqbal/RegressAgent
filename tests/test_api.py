"""Local HTTP API (D-020). Binds to loopback only, needs a random bearer token, rejects foreign
Host/Origin headers (DNS rebinding / CSRF), caps request sizes and never leaks tracebacks."""
import http.client
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from regressagent.api import ApiError, make_server

CASE = {
    "case_id": "greet-ava",
    "name": "greet",
    "input": {"action": "greet", "name": "Ava"},
    "assertions": [{"path": "message", "operator": "equals", "expected": "Hello, Ava!"}],
}
SECRET_TRACE = {
    "trace_id": "t1",
    "input": {"q": "x"},
    "output": {"message": "hi", "api_key": "sk-live-abcdef1234567890", "n": 2},
}


class Client:
    def __init__(self, port, token):
        self.port, self.token = port, token

    def call(self, method, path, body=None, *, token="default", headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = dict(headers or {})
        use = self.token if token == "default" else token
        if use is not None:
            hdrs.setdefault("Authorization", f"Bearer {use}")
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        out = (resp.status, json.loads(payload) if payload else None, dict(resp.getheaders()))
        conn.close()
        return out


@pytest.fixture
def api(tmp_path):
    server, token = make_server(str(tmp_path / "t.db"), "127.0.0.1", 0)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True).start()
    yield Client(server.server_address[1], token)
    server.shutdown()
    server.server_close()


V1 = "/api/v1"


# ---------------------------------------------------------------- binding and token
@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com", "", "::"])
def test_only_loopback_hosts_can_be_bound(tmp_path, host):
    with pytest.raises(ValueError, match="loopback"):
        make_server(str(tmp_path / "t.db"), host, 0)


def test_tokens_are_random_and_long(tmp_path):
    s1, t1 = make_server(str(tmp_path / "a.db"), "127.0.0.1", 0)
    s2, t2 = make_server(str(tmp_path / "b.db"), "localhost", 0)
    s1.server_close(), s2.server_close()
    assert t1 != t2 and len(t1) >= 32 and len(t2) >= 32


def test_every_api_route_needs_the_token(api):
    for path in (f"{V1}/health", f"{V1}/cases", f"{V1}/traces", f"{V1}/compare?before=1&after=2"):
        assert api.call("GET", path, token=None)[0] == 401
        assert api.call("GET", path, token="wrong")[0] == 401
        assert api.call("GET", path, token="")[0] == 401
    _, _, headers = api.call("GET", f"{V1}/cases", token=None)
    assert headers.get("WWW-Authenticate", "").startswith("Bearer")
    assert api.call("GET", f"{V1}/health")[0] == 200


def test_token_in_the_query_string_is_not_accepted(api):
    assert api.call("GET", f"{V1}/cases?token={api.token}", token=None)[0] == 401


# ---------------------------------------------------------------- host / origin / methods
def raw_request(api, lines, body=b""):
    import socket

    with socket.create_connection(("127.0.0.1", api.port), timeout=10) as sock:
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), head.decode(), rest


@pytest.mark.parametrize("host", ["evil.example", "evil.example:80", "127.0.0.1.evil.example", "localhost.evil.com:1"])
def test_foreign_host_headers_are_rejected(api, host):
    lines = ["GET /api/v1/health HTTP/1.1", f"Host: {host}", f"Authorization: Bearer {api.token}", "Connection: close"]
    assert raw_request(api, lines)[0] == 403


def test_missing_host_header_is_rejected(api):
    lines = ["GET /api/v1/health HTTP/1.0", f"Authorization: Bearer {api.token}"]
    assert raw_request(api, lines)[0] == 403


@pytest.mark.parametrize("origin", ["http://evil.example", "null", "https://127.0.0.1", "http://127.0.0.1:1"])
def test_foreign_origins_are_rejected(api, origin):
    assert api.call("GET", f"{V1}/health", headers={"Origin": origin})[0] == 403


def test_same_origin_requests_are_allowed(api):
    assert api.call("GET", f"{V1}/health", headers={"Origin": f"http://127.0.0.1:{api.port}"})[0] == 200
    assert api.call("GET", f"{V1}/health", headers={"Sec-Fetch-Site": "same-origin"})[0] == 200
    assert api.call("GET", f"{V1}/health", headers={"Sec-Fetch-Site": "none"})[0] == 200


def test_cross_site_fetches_are_rejected(api):
    for value in ("cross-site", "same-site"):
        assert api.call("GET", f"{V1}/health", headers={"Sec-Fetch-Site": value})[0] == 403


@pytest.mark.parametrize("method", ["PUT", "PATCH", "OPTIONS", "TRACE", "HEAD"])
def test_other_methods_are_not_allowed_and_no_cors_headers_ever(api, method):
    status, _, headers = api.call(method, f"{V1}/cases")
    assert status == 405
    assert not any(k.lower().startswith("access-control-") for k in headers)


def test_responses_carry_safe_headers_and_no_cors(api):
    _, _, headers = api.call("GET", f"{V1}/health")
    low = {k.lower(): v for k, v in headers.items()}
    assert low["content-type"].startswith("application/json")
    assert low["cache-control"] == "no-store" and low["x-content-type-options"] == "nosniff"
    assert not any(k.startswith("access-control-") for k in low)


# ---------------------------------------------------------------- request body limits
def test_oversized_bodies_are_refused_without_reading_them(api):
    lines = ["POST /api/v1/cases HTTP/1.1", f"Host: 127.0.0.1:{api.port}", f"Authorization: Bearer {api.token}",
             "Content-Type: application/json", "Content-Length: 5000000", "Connection: close"]  # fmt: skip
    assert raw_request(api, lines)[0] == 413


def test_chunked_bodies_and_missing_length_are_refused(api):
    base = ["POST /api/v1/cases HTTP/1.1", f"Host: 127.0.0.1:{api.port}", f"Authorization: Bearer {api.token}",
            "Content-Type: application/json", "Connection: close"]  # fmt: skip
    assert raw_request(api, base + ["Transfer-Encoding: chunked"], b"0\r\n\r\n")[0] in (400, 411, 501)
    assert raw_request(api, base)[0] == 411


def test_body_must_be_a_json_object_with_the_right_content_type(api):
    assert api.call("POST", f"{V1}/cases", raw=b"{}", headers={"Content-Type": "text/plain"})[0] == 415
    assert api.call("POST", f"{V1}/cases", raw=b"not json")[0] == 400
    assert api.call("POST", f"{V1}/cases", raw=b"[1]")[0] == 400
    assert api.call("POST", f"{V1}/cases", raw=b'{"a": NaN}')[0] == 400
    assert api.call("POST", f"{V1}/cases", raw=b"\xff\xfe")[0] == 400
    assert api.call("POST", f"{V1}/cases", raw=b"[" * 100000 + b"]" * 100000)[0] == 400


def test_errors_never_leak_tracebacks_or_paths(api):
    for path in (f"{V1}/cases/nope", f"{V1}/runs/abc", f"{V1}/nothing", f"{V1}/compare?before=x&after=y"):
        status, body, _ = api.call("GET", path)
        assert status in (400, 404)
        text = json.dumps(body)
        assert "Traceback" not in text and "/home/" not in text and ".py" not in text


def test_unexpected_exceptions_become_a_generic_500(api, monkeypatch):
    import regressagent.api as api_module

    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail /home/x/y.py")

    monkeypatch.setattr(api_module.Api, "_dispatch", boom)
    status, body, _ = api.call("GET", f"{V1}/health")
    assert status == 500 and "secret" not in json.dumps(body) and body["error"] == "internal error"


def test_odd_ids_cannot_escape_or_crash(api):
    for cid in ("..%2F..%2Fetc%2Fpasswd", "%00", "a%20b", "x" * 300, "%E2%82%AC"):
        status, body, _ = api.call("GET", f"{V1}/cases/{cid}")
        assert status in (400, 404) and "error" in body


# ---------------------------------------------------------------- cases
def test_case_lifecycle(api):
    assert api.call("GET", f"{V1}/cases")[1] == []
    status, body, _ = api.call("POST", f"{V1}/cases", CASE)
    assert status == 201 and body["case_id"] == "greet-ava" and len(body["case_hash"]) == 64
    assert api.call("POST", f"{V1}/cases", CASE)[0] == 409  # exists
    assert api.call("POST", f"{V1}/cases?overwrite=1", CASE)[0] == 201
    assert api.call("GET", f"{V1}/cases")[1] == ["greet-ava"]
    assert api.call("GET", f"{V1}/cases/greet-ava")[1]["case_id"] == "greet-ava"
    assert api.call("DELETE", f"{V1}/cases/greet-ava")[0] == 400  # needs confirm=yes
    assert api.call("DELETE", f"{V1}/cases/greet-ava?confirm=yes")[0] == 200
    assert api.call("GET", f"{V1}/cases/greet-ava")[0] == 404
    assert api.call("DELETE", f"{V1}/cases/greet-ava?confirm=yes")[0] == 404


def test_invalid_cases_are_422_with_safe_details(api):
    bad = dict(CASE, assertions=[{"path": "a..b", "operator": "regex", "expected": "(a+)+b"}])
    status, body, _ = api.call("POST", f"{V1}/cases", bad)
    assert status == 422 and body["error"] and isinstance(body["details"], list)
    assert api.call("POST", f"{V1}/cases", dict(CASE, surprise=1))[0] == 422


# ---------------------------------------------------------------- runs, compare, report
def test_run_compare_report_flow_with_demo_runners(api):
    api.call("POST", f"{V1}/cases", CASE)
    status, body, _ = api.call("POST", f"{V1}/cases/greet-ava/run", {"label": "before", "runner": "local-legacy"})
    assert status == 200 and body["passed"] is False and body["run_ids"] == [1]
    status, body, _ = api.call("POST", f"{V1}/cases/greet-ava/run", {"label": "after", "runner": "local"})
    assert status == 200 and body["passed"] is True
    runs = api.call("GET", f"{V1}/cases/greet-ava/runs")[1]
    assert [r["label"] for r in runs] == ["before", "after"] and runs[1]["runner"] == "local"
    assert api.call("GET", f"{V1}/runs/2")[1]["result"]["passed"] is True
    comparison = api.call("GET", f"{V1}/compare?before=1&after=2")[1]
    assert comparison["verdict"] == "improved"
    md = api.call("GET", f"{V1}/report?before=1&after=2")[1]
    assert md["format"] == "markdown" and "RegressAgent report" in md["text"]
    js = api.call("GET", f"{V1}/report?before=1&after=2&format=json")[1]
    assert js["format"] == "json" and json.loads(js["text"])["verdict"] == "improved"
    assert api.call("GET", f"{V1}/report?before=1&after=2&format=html")[0] == 400


def test_run_endpoint_only_runs_demo_runners_and_validates_input(api):
    api.call("POST", f"{V1}/cases", CASE)
    run = f"{V1}/cases/greet-ava/run"
    assert api.call("POST", run, {"label": "x", "runner": "nope"})[0] == 400
    status, body, _ = api.call("POST", run, {"label": "x", "adapter": "my-agent"})
    assert status == 400 and "adapter" in body["error"]  # commands never run through the API
    assert api.call("POST", run, {"label": "x", "repeat": 0})[0] == 400
    assert api.call("POST", run, {"label": "x", "repeat": 21})[0] == 400
    assert api.call("POST", run, {"label": "x", "repeat": "2"})[0] == 400
    assert api.call("POST", run, {"label": 5})[0] == 400
    assert api.call("POST", run, {"label": "x", "surprise": 1})[0] == 400
    assert api.call("POST", f"{V1}/cases/ghost/run", {"label": "x"})[0] == 404
    status, body, _ = api.call("POST", run, {"label": "r", "repeat": 3})
    assert status == 200 and len(body["run_ids"]) == 3 and body["stable"] is True


def test_compare_validates_query_numbers(api):
    for q in ("", "before=1", "before=1&after=x", "before=-1&after=2", "before=1&after=999999999999999999999"):
        assert api.call("GET", f"{V1}/compare?{q}")[0] in (400, 404)
    assert api.call("GET", f"{V1}/compare?before=1&after=2")[0] == 404  # no such runs


# ---------------------------------------------------------------- traces
def test_trace_store_and_case_generation(api, tmp_path):
    status, body, _ = api.call("POST", f"{V1}/traces", SECRET_TRACE)
    assert status == 201 and body["redacted"] is True
    assert api.call("POST", f"{V1}/traces", SECRET_TRACE)[0] == 409
    assert api.call("GET", f"{V1}/traces")[1] == ["t1"]
    stored = api.call("GET", f"{V1}/traces/t1")[1]
    assert stored["output"]["message"] == "hi" and "sk-live" not in json.dumps(stored)
    assert b"sk-live-abcdef1234567890" not in (tmp_path / "t.db").read_bytes()
    status, case, _ = api.call("POST", f"{V1}/traces/t1/case", {})
    assert status == 200 and case["saved"] is False
    assert [a["path"] for a in case["case"]["assertions"]] == ["message", "n"]
    assert any("api_key" in n for n in case["notes"])
    status, saved, _ = api.call("POST", f"{V1}/traces/t1/case", {"case_id": "from-t1", "save": True, "paths": ["message"]})
    assert status == 201 and saved["saved"] is True
    assert api.call("GET", f"{V1}/cases/from-t1")[1]["assertions"][0]["path"] == "message"
    assert api.call("POST", f"{V1}/traces/t1/case", {"paths": ["nope"]})[0] == 400
    assert api.call("POST", f"{V1}/traces/ghost/case", {})[0] == 404
    assert api.call("DELETE", f"{V1}/traces/t1")[0] == 400
    assert api.call("DELETE", f"{V1}/traces/t1?confirm=yes")[0] == 200
    assert api.call("GET", f"{V1}/traces/t1")[0] == 404


def test_bad_traces_are_rejected(api):
    assert api.call("POST", f"{V1}/traces", {"trace_id": "x"})[0] == 400
    assert api.call("POST", f"{V1}/traces", dict(SECRET_TRACE, extra=1))[0] == 400


# ---------------------------------------------------------------- concurrency
def test_parallel_requests_work(api):
    api.call("POST", f"{V1}/cases", CASE)

    def run(i):
        return api.call("POST", f"{V1}/cases/greet-ava/run", {"label": f"p{i}", "runner": "local"})[0]

    with ThreadPoolExecutor(8) as pool:
        assert set(pool.map(run, range(8))) == {200}
        assert set(pool.map(lambda _: api.call("GET", f"{V1}/health")[0], range(20))) == {200}
    assert len(api.call("GET", f"{V1}/cases/greet-ava/runs")[1]) == 8


def test_api_error_carries_status_and_message():
    err = ApiError(418, "teapot")
    assert (err.status, err.message) == (418, "teapot")
