"""Tier B (D-022): the command runs inside bubblewrap: private mount/pid/ipc/uts/net namespaces,
read-only system dirs plus explicit ro_binds only, no network, no capabilities, tmpfs cwd. If the
sandbox cannot be created the run is REFUSED (never silently unsandboxed)."""
import json
import os
import shutil
import socket
import threading
from pathlib import Path

import pytest

import regressagent.subprocess_adapter as sa
from regressagent.cli import main
from regressagent.config import ConfigError, load_config
from regressagent.storage import Repository
from regressagent.subprocess_adapter import AdapterError, SubprocessAdapter

# Inside the sandbox only /usr, /bin, /lib* and ro_binds exist, so the test agent must run on the
# system interpreter (a venv/uv/toolcache interpreter elsewhere is correctly refused).
PYTHON = os.path.realpath("/usr/bin/python3")
PROBE = Path(__file__).with_name("tier_b_probe.py")
TESTS_DIR = str(PROBE.parent)


def toml_for(tier="B", binds=None, extra="", name="sb"):
    binds = [TESTS_DIR] if binds is None else binds
    lines = [f"[adapters.{name}]", f'tier = "{tier}"', f"argv = [{json.dumps(PYTHON)}, {json.dumps(str(PROBE))}]",
             "timeout_seconds = 20"]  # fmt: skip
    if tier == "A":
        lines.append("acknowledge_not_sandboxed = true")
    if binds is not False and tier == "B":
        lines.append(f"ro_binds = {json.dumps(binds)}")
    return "\n".join(lines) + "\n" + extra


def load(tmp_path, text, name="sb"):
    f = tmp_path / "regressagent.toml"
    f.write_text(text, encoding="utf-8")
    f.chmod(0o600)
    return load_config(f).adapters[name]


# ---------------------------------------------------------------- config (no bwrap needed)
def test_tier_b_config_parses_and_needs_no_unsandboxed_acknowledgement(tmp_path):
    cfg = load(tmp_path, toml_for())
    assert cfg.tier == "B" and cfg.ro_binds == (TESTS_DIR,)
    assert load(tmp_path, toml_for(binds=[])).ro_binds == ()


def test_tier_a_still_requires_the_acknowledgement_and_rejects_ro_binds(tmp_path):
    with pytest.raises(ConfigError, match="acknowledge_not_sandboxed"):
        load(tmp_path, toml_for("A").replace("acknowledge_not_sandboxed = true\n", ""))
    with pytest.raises(ConfigError, match="tier B"):
        load(tmp_path, toml_for("A", extra=f'ro_binds = ["{TESTS_DIR}"]\n'))
    with pytest.raises(ConfigError, match="tier"):
        load(tmp_path, toml_for("C"))


@pytest.mark.parametrize(
    "binds",
    ["notalist", [5], [""], ["relative/dir"], ["/"], ["/proc"], ["/proc/1"], ["/dev"], ["/dev/shm"], ["/sys/kernel"],
     ["/definitely/not/here"], [TESTS_DIR + "/../tests"], [TESTS_DIR, TESTS_DIR], [TESTS_DIR + "\n"], ["/tmp/" + "x" * 5000],
     [TESTS_DIR] * 17, ["/usr//bin"]],
)  # fmt: skip
def test_bad_ro_binds_are_rejected(tmp_path, binds):
    with pytest.raises(ConfigError):
        load(tmp_path, toml_for(binds=binds))


# ---------------------------------------------------------------- availability
def _sandbox_works() -> bool:
    exe = shutil.which("bwrap")
    if exe is None:
        return False
    try:
        return sa._sandbox_problem(exe) is None
    except Exception:  # noqa: BLE001
        return False


needs_sandbox = pytest.mark.skipif(not (PYTHON.startswith("/usr/") and _sandbox_works()), reason="bubblewrap not installed or cannot create a sandbox here")


def run_probe(tmp_path, request, *, binds=None, tier="B", environ=None, extra=""):
    cfg = load(tmp_path, toml_for(tier, binds, extra))
    return SubprocessAdapter(cfg, environ=environ or {}).run(request)


# ---------------------------------------------------------------- behaviour
@needs_sandbox
def test_the_agent_runs_inside_the_sandbox_and_metadata_says_tier_b(tmp_path):
    cfg = load(tmp_path, toml_for())
    adapter = SubprocessAdapter(cfg, environ={})
    assert adapter.run({"mode": "hello"}) == {"status": "ok", "cwd": "/work"}
    assert adapter.last_metadata.adapter_tier == "B" and adapter.last_metadata.exit_code == 0


@needs_sandbox
def test_network_is_blocked_but_not_in_tier_a(tmp_path):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    port = server.getsockname()[1]
    threading.Thread(target=lambda: [server.accept() for _ in range(3)], daemon=True).start()
    try:
        ask = {"mode": "net", "host": "127.0.0.1", "port": port}
        assert run_probe(tmp_path, ask, tier="A")["ok"] is True  # control: Tier A can reach the host loopback
        assert run_probe(tmp_path, ask)["ok"] is False  # Tier B cannot
    finally:
        server.close()


@needs_sandbox
def test_host_files_outside_the_allow_list_are_invisible_but_readable_in_tier_a(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    ask = {"mode": "read", "path": str(secret)}
    assert run_probe(tmp_path, ask, tier="A")["ok"] is True  # control
    result = run_probe(tmp_path, ask)
    assert result["ok"] is False and result["error"] == "FileNotFoundError"


@needs_sandbox
def test_ro_binds_are_read_only(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "data.txt").write_text("hello", encoding="utf-8")
    binds = [TESTS_DIR, str(shared)]
    assert run_probe(tmp_path, {"mode": "read", "path": str(shared / "data.txt")}, binds=binds) == {"ok": True, "value": 5}
    result = run_probe(tmp_path, {"mode": "write", "path": str(shared / "new.txt")}, binds=binds)
    assert result["ok"] is False and not (shared / "new.txt").exists()
    result = run_probe(tmp_path, {"mode": "write", "path": str(shared / "data.txt")}, binds=binds)
    assert result["ok"] is False and (shared / "data.txt").read_text() == "hello"


@needs_sandbox
def test_tmp_is_private_and_discarded(tmp_path):
    assert run_probe(tmp_path, {"mode": "tmp"}) == {"exists": True}
    assert not Path("/tmp/probe-file").exists()


@needs_sandbox
def test_environment_pid_namespace_and_capabilities(tmp_path):
    keys = run_probe(tmp_path, {"mode": "env"}, environ={"SECRET_TOKEN": "x", "ALLOWED": "y"}, extra='env_allow = ["ALLOWED"]\n')["keys"]
    assert "SECRET_TOKEN" not in keys and "ALLOWED" in keys and set(keys) <= {"PATH", "ALLOWED", "LC_CTYPE", "PWD"}
    pids = run_probe(tmp_path, {"mode": "pids"})
    assert pids["count"] <= 4 and pids["pid"] <= 10  # a private pid namespace
    assert run_probe(tmp_path, {"mode": "caps"})["cap_eff"] == "0000000000000000"


@needs_sandbox
def test_only_the_expected_top_level_directories_exist(tmp_path):
    top = set(run_probe(tmp_path, {"mode": "ls", "path": "/"})["value"])
    assert {"usr", "proc", "dev", "tmp", "work"} <= top
    assert not top & {"root", "mnt", "var", "srv", "opt", "boot", "media", "run", "sys"}


@needs_sandbox
def test_executable_outside_the_visible_tree_is_refused_with_a_hint(tmp_path):
    adapter = SubprocessAdapter(load(tmp_path, toml_for(binds=[])), environ={})  # the probe script is not bound
    with pytest.raises(AdapterError, match="ro_binds"):
        adapter.run({"mode": "hello"})


# ---------------------------------------------------------------- fail closed
def test_missing_bubblewrap_refuses_to_run_unsandboxed(tmp_path, monkeypatch):
    monkeypatch.setattr(sa, "_find_bwrap", lambda: None)
    adapter = SubprocessAdapter(load(tmp_path, toml_for()), environ={})
    with pytest.raises(AdapterError, match="bubblewrap.*refusing|refusing.*bubblewrap"):
        adapter.run({"mode": "hello"})
    assert adapter.last_metadata is not None


def test_a_sandbox_that_cannot_be_created_refuses_to_run(tmp_path, monkeypatch):
    monkeypatch.setattr(sa, "_find_bwrap", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(sa, "_sandbox_problem", lambda exe: "setting up uid map: Permission denied")
    adapter = SubprocessAdapter(load(tmp_path, toml_for()), environ={})
    with pytest.raises(AdapterError, match="cannot create a sandbox.*refusing|refusing"):
        adapter.run({"mode": "hello"})


# ---------------------------------------------------------------- through the CLI
@needs_sandbox
def test_cli_runs_a_case_through_a_tier_b_adapter(tmp_path, capsys):
    case = {"case_id": "sb-case", "name": "sandboxed", "input": {"mode": "hello"},
            "assertions": [{"path": "status", "operator": "equals", "expected": "ok"}]}  # fmt: skip
    (tmp_path / "c.json").write_text(json.dumps(case))
    cfg = tmp_path / "regressagent.toml"
    cfg.write_text(toml_for())
    cfg.chmod(0o600)
    db = str(tmp_path / "t.db")
    assert main(["--db", db, "case", "add", str(tmp_path / "c.json")]) == 0
    capsys.readouterr()
    assert main(["--db", db, "run", "sb-case", "--label", "b", "--adapter", "sb", "--config", str(cfg)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert Repository(db).get_run(out["run_id"]).metadata.adapter_tier == "B"


@needs_sandbox
def test_the_executable_itself_must_be_visible_inside_the_sandbox(tmp_path):
    script = tmp_path / "agent.sh"
    script.write_text("#!/bin/sh\necho '{}'\n")
    script.chmod(0o755)
    cfg = load(tmp_path, f'[adapters.sb]\ntier = "B"\nargv = [{json.dumps(str(script))}]\n')
    with pytest.raises(AdapterError, match="executable is not visible"):
        SubprocessAdapter(cfg, environ={}).run({})


@needs_sandbox
def test_nested_user_namespaces_are_blocked_when_bubblewrap_supports_it(tmp_path):
    if not sa._supports_disable_userns(sa._find_bwrap()):
        pytest.skip("this bubblewrap has no --disable-userns")
    result = run_probe(tmp_path, {"mode": "userns"})
    assert result["ok"] is False or result["value"] is False
