"""The UI is three static files served by the API server. They must not need (or allow) inline
code, must not touch persistent storage, and must never receive the token from the server."""
import re
import threading
from html.parser import HTMLParser
from importlib import resources

import pytest
from test_api import Client

from regressagent.api import make_server

UI = resources.files("regressagent") / "ui"


@pytest.fixture
def web(tmp_path):
    server, token = make_server(str(tmp_path / "t.db"), "127.0.0.1", 0)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True).start()
    yield Client(server.server_address[1], token)
    server.shutdown()
    server.server_close()


def fetch(web, method, path, headers=None):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", web.port, timeout=10)
    conn.request(method, path, headers=headers or {})
    resp = conn.getresponse()
    body = resp.read()
    out = (resp.status, body, {k.lower(): v for k, v in resp.getheaders()})
    conn.close()
    return out


@pytest.mark.parametrize(
    ("path", "ctype"),
    [("/", "text/html"), ("/index.html", "text/html"), ("/app.js", "text/javascript"), ("/app.css", "text/css")],
)
def test_ui_files_are_served_without_a_token_with_a_strict_csp(web, path, ctype):
    status, body, headers = fetch(web, "GET", path)
    assert status == 200 and headers["content-type"].startswith(ctype) and body
    csp = headers["content-security-policy"]
    for needed in ("default-src 'none'", "script-src 'self'", "style-src 'self'", "connect-src 'self'",
                   "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'"):  # fmt: skip
        assert needed in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and "*" not in csp
    assert headers["x-content-type-options"] == "nosniff" and headers["cache-control"] == "no-store"
    assert not any(k.startswith("access-control-") for k in headers)


def test_the_token_is_never_present_in_static_responses(web):
    for path in ("/", "/app.js", "/app.css"):
        assert web.token.encode() not in fetch(web, "GET", path)[1]


def test_query_is_ignored_but_other_paths_and_methods_are_not_served(web):
    assert fetch(web, "GET", "/app.js?x=1")[0] == 200
    for path in ("/ui/app.js", "/app.js/", "/../etc/passwd", "/%2e%2e/etc/passwd", "/favicon.ico", "/nope"):
        assert fetch(web, "GET", path)[0] == 404
    assert fetch(web, "POST", "/")[0] in (404, 405, 401)
    assert fetch(web, "HEAD", "/app.js")[1] == b""


def test_host_and_origin_checks_apply_to_the_ui_files_too(web):
    assert fetch(web, "GET", "/", {"Host": "evil.example"})[0] == 403
    assert fetch(web, "GET", "/app.js", {"Origin": "http://evil.example"})[0] == 403


class _Audit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.problems = []

    def handle_starttag(self, tag, attrs):
        names = {k for k, _ in attrs}
        if tag == "script" and "src" not in names:
            self.problems.append("inline script")
        if tag == "style":
            self.problems.append("inline style element")
        for k, v in attrs:
            if k.startswith("on") or k == "style":
                self.problems.append(f"{tag}[{k}]")
            if k in ("src", "href") and v and not v.startswith("/") and not v.startswith("#"):
                self.problems.append(f"external reference {v}")


def test_index_html_has_no_inline_code_and_no_external_resources():
    audit = _Audit()
    audit.feed((UI / "index.html").read_text(encoding="utf-8"))
    assert audit.problems == []


@pytest.mark.parametrize(
    "forbidden",
    ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
     "localStorage", "sessionStorage", "document.cookie", "indexedDB", "http://", "https://", "importScripts", "postMessage"],
)  # fmt: skip
def test_app_js_avoids_dangerous_apis(forbidden):
    assert forbidden not in (UI / "app.js").read_text(encoding="utf-8")


def test_app_js_takes_the_token_from_the_fragment_and_removes_it_from_the_address_bar():
    js = (UI / "app.js").read_text(encoding="utf-8")
    assert "location.hash" in js and "replaceState" in js
    assert re.search(r"Authorization", js)
