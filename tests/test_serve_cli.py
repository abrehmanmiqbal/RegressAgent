"""`regressagent serve`: starts the local API as a real process, prints the URL and a one-time token."""
import http.client
import json
import re
import signal
import subprocess
import sys

import pytest

from regressagent.cli import main

START = [sys.executable, "-c", "from regressagent.cli import main; raise SystemExit(main())"]


def test_serve_starts_answers_and_stops_cleanly_on_ctrl_c(tmp_path):
    proc = subprocess.Popen(
        [*START, "--db", str(tmp_path / "s.db"), "serve", "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        banner = ""
        while "Token" not in banner:
            line = proc.stderr.readline()
            assert line, f"server exited early: {proc.poll()}"
            banner += line
        port = int(re.search(r"http://127\.0\.0\.1:(\d+)/", banner).group(1))
        token = re.search(r"Token[^:]*:\s*(\S+)", banner).group(1)
        assert len(token) >= 32
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/v1/health", headers={"Authorization": f"Bearer {token}"})
        resp = conn.getresponse()
        assert resp.status == 200 and json.loads(resp.read())["ok"] is True
        conn.request("GET", "/api/v1/health")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 401
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.stderr.close()
    assert (tmp_path / "s.db").exists()


def test_serve_refuses_non_loopback_hosts(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "s.db"), "serve", "--host", "0.0.0.0", "--port", "0"]) == 2
    assert "loopback" in capsys.readouterr().err


def test_serve_reports_a_busy_port_cleanly(tmp_path, capsys):
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        assert main(["--db", str(tmp_path / "s.db"), "serve", "--port", str(port)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "Traceback" not in err


@pytest.mark.parametrize("port", ["-1", "70000", "abc"])
def test_serve_validates_the_port(tmp_path, port):
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(tmp_path / "s.db"), "serve", "--port", port])
    assert exc.value.code == 2
