"""End-to-end: the shipped example agents run through the real adapters and the real CLI.
This is the README story (good run, regression, compare) with an actual agent instead of a demo
runner."""
import json
import sys
import threading
from pathlib import Path

import pytest
from test_subprocess_adapter import PYTHON  # sibling test module (tests/ is on sys.path)

from regressagent.cli import main
from regressagent.storage import Repository

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
AGENT = EXAMPLES / "agents" / "greeter_agent.py"
CASE = EXAMPLES / "cases" / "greet.json"

pytestmark = [
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Tier A adapters are Linux only"),
    pytest.mark.skipif(not PYTHON, reason="no usable interpreter for the example agent"),
]


def cli(tmp_path, *argv):
    return main(["--db", str(tmp_path / "t.db"), *argv])


def write_config(tmp_path, env_allow=True, url=None):
    lines = []
    if url is None:
        lines += ["[adapters.greeter]", 'tier = "A"', f"argv = [{json.dumps(PYTHON)}, {json.dumps(str(AGENT))}]"]
        lines += ["acknowledge_not_sandboxed = true", "timeout_seconds = 10"]
        if env_allow:
            lines.append('env_allow = ["GREETER_BUG"]')
    else:
        lines += ["[adapters.greeter]", 'type = "http"', f'url = "{url}"', "timeout_seconds = 10"]
    cfg = tmp_path / "regressagent.toml"
    cfg.write_text("\n".join(lines) + "\n")
    cfg.chmod(0o600)
    return str(cfg)


def run(tmp_path, cfg, label):
    return cli(tmp_path, "run", "greet-ava", "--label", label, "--adapter", "greeter", "--config", cfg)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("GREETER_BUG", raising=False)


def test_command_agent_good_then_regression_is_caught_and_compared(tmp_path, monkeypatch, capsys):
    assert cli(tmp_path, "case", "add", str(CASE)) == 0
    cfg = write_config(tmp_path)
    assert run(tmp_path, cfg, "good") == 0
    monkeypatch.setenv("GREETER_BUG", "1")  # the "deploy" that introduces a regression
    assert run(tmp_path, cfg, "buggy") == 1
    capsys.readouterr()
    assert cli(tmp_path, "compare", "1", "2") == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "regressed"
    meta = Repository(str(tmp_path / "t.db")).get_run(2).metadata
    assert meta.adapter_tier == "A" and meta.env_names == ["GREETER_BUG"] and meta.exit_code == 0


def test_the_child_only_sees_variables_listed_in_env_allow(tmp_path, monkeypatch):
    assert cli(tmp_path, "case", "add", str(CASE)) == 0
    monkeypatch.setenv("GREETER_BUG", "1")
    # Not listed -> not passed -> the agent behaves correctly even though the parent has it set.
    assert run(tmp_path, write_config(tmp_path, env_allow=False), "clean-env") == 0


def start_http_agent(bug):
    sys.path.insert(0, str(EXAMPLES / "agents"))
    try:
        import greeter_http

        httpd = greeter_http.make_server("127.0.0.1", 0, bug=bug)
    finally:
        sys.path.pop(0)
    threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True).start()
    return httpd


def test_http_agent_good_then_regression_is_caught(tmp_path, capsys):
    assert cli(tmp_path, "case", "add", str(CASE)) == 0
    results = []
    for bug in (False, True):
        httpd = start_http_agent(bug)
        try:
            url = f"http://127.0.0.1:{httpd.server_address[1]}/agent"
            results.append(run(tmp_path, write_config(tmp_path, url=url), f"bug={bug}"))
        finally:
            httpd.shutdown()
            httpd.server_close()
    assert results == [0, 1]
    capsys.readouterr()
    assert cli(tmp_path, "compare", "1", "2") == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "regressed"
    assert Repository(str(tmp_path / "t.db")).get_run(1).metadata.adapter_tier == "H"


def test_example_agents_are_dependency_free_and_tiny():
    for path in (EXAMPLES / "agents").glob("*.py"):
        text = path.read_text()
        assert len(text.splitlines()) < 80, path.name
        assert "import requests" not in text and "import regressagent" not in text
