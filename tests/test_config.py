"""Tests for the regressagent.toml loader. The loader only parses and validates; it never
executes anything and never touches the executables it names (D-010, section 8 step 2)."""
import os
from pathlib import Path

import pytest

from regressagent.config import (
    DEFAULT_MAX_STDOUT_BYTES,
    DEFAULT_MEMORY_LIMIT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_CONFIG_BYTES,
    ConfigError,
    load_config,
)

GOOD = """
[adapters.my-agent]
tier = "A"
argv = ["/usr/bin/python3", "/opt/agent/main.py", "--json"]
acknowledge_not_sandboxed = true
"""


def write(tmp_path: Path, text: str | bytes, name: str = "regressagent.toml") -> Path:
    path = tmp_path / name
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def adapter_toml(**overrides: str) -> str:
    fields = {
        "tier": '"A"',
        "argv": '["/usr/bin/python3", "agent.py"]',
        "acknowledge_not_sandboxed": "true",
    }
    fields.update(overrides)
    body = "\n".join(f"{k} = {v}" for k, v in fields.items() if v is not None)
    return f"[adapters.a]\n{body}\n"


def test_valid_config_loads_with_defaults(tmp_path):
    cfg = load_config(write(tmp_path, GOOD))
    adapter = cfg.adapters["my-agent"]
    assert adapter.tier == "A"
    assert adapter.argv == ("/usr/bin/python3", "/opt/agent/main.py", "--json")
    assert adapter.timeout_seconds == DEFAULT_TIMEOUT_SECONDS == 30
    assert adapter.max_stdout_bytes == DEFAULT_MAX_STDOUT_BYTES == 1_000_000
    assert adapter.memory_limit_bytes == DEFAULT_MEMORY_LIMIT_BYTES == 512 * 1024 * 1024
    assert adapter.env_allow == ()
    assert adapter.executable_sha256 is None


def test_empty_config_has_no_adapters(tmp_path):
    assert load_config(write(tmp_path, "")).adapters == {}


def test_missing_file_is_a_clean_error(tmp_path):
    with pytest.raises(ConfigError, match="not found|cannot read"):
        load_config(tmp_path / "nope.toml")


def test_directory_is_refused(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_oversized_file_is_refused_before_parsing(tmp_path):
    big = write(tmp_path, "# " + "x" * MAX_CONFIG_BYTES + "\n")
    with pytest.raises(ConfigError, match="too large"):
        load_config(big)


@pytest.mark.parametrize(
    "raw",
    [b"\xff\xfe\x00bad", b"[adapters.a\n", b"x = = 1", b"a = 1\na = 2\n"],
    ids=["invalid-utf8", "unterminated-table", "syntax", "duplicate-key"],
)
def test_malformed_toml_is_a_clean_error(tmp_path, raw):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, raw))


def test_unknown_top_level_key_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="unknown"):
        load_config(write(tmp_path, GOOD + '\n[runners.x]\ncommand = "rm -rf /"\n'))


def test_unknown_adapter_key_is_refused(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, adapter_toml(shell="true")))


def test_trust_acknowledgement_is_required(tmp_path):
    with pytest.raises(ConfigError, match="acknowledge_not_sandboxed"):
        load_config(write(tmp_path, adapter_toml(acknowledge_not_sandboxed=None)))


@pytest.mark.parametrize("value", ["false", '"true"', '"yes"', "1", '"trusted"'])
def test_trust_acknowledgement_must_be_literal_true(tmp_path, value):
    with pytest.raises(ConfigError, match="acknowledge_not_sandboxed"):
        load_config(write(tmp_path, adapter_toml(acknowledge_not_sandboxed=value)))


def test_tier_b_is_accepted_since_the_sandbox_exists(tmp_path):
    cfg = next(iter(load_config(write(tmp_path, adapter_toml(tier='"B"'))).adapters.values()))
    assert cfg.tier == "B" and cfg.ro_binds == ()


@pytest.mark.parametrize("value", ['"a"', "1", "true"])
def test_unknown_tier_is_refused(tmp_path, value):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, adapter_toml(tier=value)))


@pytest.mark.parametrize(
    "argv",
    [
        '"python3 agent.py"',  # string instead of list: would invite shell parsing
        "[]",
        '["python3", "agent.py"]',  # not absolute
        '["./agent"]',
        '["/usr/bin/../bin/python3"]',  # traversal segment
        '["/usr/bin/python3", 1]',
        '["/usr/bin/python3", "a\\u0000b"]',
        '["/usr/bin/python3", ""]',
        '["/usr/bin/python3\\n"]',
        '["/bin/sh -c rm"]',  # whitespace in executable
        "[" + ", ".join(['"/x"'] * 70) + "]",
        '["/' + "a" * 5000 + '"]',
    ],
)
def test_bad_argv_is_refused(tmp_path, argv):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, adapter_toml(argv=argv)))


@pytest.mark.parametrize("name", ["", "A", "has space", "../x", "a/b", "x" * 65, "-x", "local"])
def test_bad_adapter_names_are_refused(tmp_path, name):
    text = adapter_toml().replace("[adapters.a]", f'[adapters."{name}"]')
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, text))


def test_adapter_name_cannot_shadow_builtin_runner(tmp_path):
    with pytest.raises(ConfigError, match="built-in"):
        load_config(write(tmp_path, adapter_toml().replace("adapters.a", "adapters.local-legacy")))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("timeout_seconds", "0"),
        ("timeout_seconds", "-1"),
        ("timeout_seconds", "301"),
        ("timeout_seconds", '"30"'),
        ("timeout_seconds", "nan"),
        ("timeout_seconds", "inf"),
        ("timeout_seconds", "true"),
        ("max_stdout_bytes", "0"),
        ("max_stdout_bytes", "1000001"),
        ("max_stderr_bytes", "-5"),
        ("max_stderr_bytes", "1000001"),
        ("memory_limit_bytes", "1"),
        ("memory_limit_bytes", str(64 * 1024**3)),
        ("memory_limit_bytes", "1.5"),
    ],
)
def test_limits_outside_hard_maxima_are_refused(tmp_path, key, value):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, adapter_toml(**{key: value})))


def test_limits_can_be_overridden_within_bounds(tmp_path):
    text = adapter_toml(
        timeout_seconds="300", max_stdout_bytes="1000", memory_limit_bytes="1073741824"
    )
    adapter = load_config(write(tmp_path, text)).adapters["a"]
    assert (adapter.timeout_seconds, adapter.max_stdout_bytes) == (300, 1000)
    assert adapter.memory_limit_bytes == 1024**3


@pytest.mark.parametrize(
    "value",
    ['["FOO=bar"]', '["a b"]', '[""]', '["1X"]', '["A\\u0000"]', '"FOO"', "[1]", '["PATH"]',
     '["LD_PRELOAD"]', '["LD_AUDIT"]', '["PYTHONPATH"]', '["PYTHONSTARTUP"]'],
)
def test_env_allow_must_be_plain_safe_names(tmp_path, value):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, adapter_toml(env_allow=value)))


def test_env_allow_accepts_names_and_deduplicates_refusal(tmp_path):
    adapter = load_config(write(tmp_path, adapter_toml(env_allow='["HOME_DIR", "MY_AGENT_MODE"]')))
    assert adapter.adapters["a"].env_allow == ("HOME_DIR", "MY_AGENT_MODE")
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(write(tmp_path, adapter_toml(env_allow='["A", "A"]')))


def test_ld_library_path_may_be_allowed_explicitly(tmp_path):
    # Needed by interpreters built with a shared libpython; LD_PRELOAD and friends stay refused.
    adapter = load_config(write(tmp_path, adapter_toml(env_allow='["LD_LIBRARY_PATH"]')))
    assert adapter.adapters["a"].env_allow == ("LD_LIBRARY_PATH",)


def test_sha256_pin_must_be_lowercase_hex(tmp_path):
    good = "ab" * 32
    ok = load_config(write(tmp_path, adapter_toml(executable_sha256=f'"{good}"')))
    assert ok.adapters["a"].executable_sha256 == good
    for bad in ['"AB"', f'"{good.upper()}"', f'"{good}0"', "1"]:
        with pytest.raises(ConfigError):
            load_config(write(tmp_path, adapter_toml(executable_sha256=bad)))


def test_world_writable_config_is_refused(tmp_path):
    path = write(tmp_path, GOOD)
    path.chmod(0o666)
    with pytest.raises(ConfigError, match="writable"):
        load_config(path)


def test_group_writable_config_is_refused(tmp_path):
    path = write(tmp_path, GOOD)
    path.chmod(0o620)
    with pytest.raises(ConfigError, match="writable"):
        load_config(path)


def test_config_that_is_a_symlink_is_refused(tmp_path):
    real = write(tmp_path, GOOD, "real.toml")
    link = tmp_path / "link.toml"
    link.symlink_to(real)
    with pytest.raises(ConfigError, match="symlink"):
        load_config(link)


def test_loading_never_executes_or_touches_the_executable(tmp_path):
    marker = tmp_path / "ran"
    script = tmp_path / "agent.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    before = script.stat().st_atime_ns
    load_config(write(tmp_path, adapter_toml(argv=f'["{script}"]')))
    assert not marker.exists()
    assert script.stat().st_atime_ns == before


def test_nonexistent_executable_is_not_a_load_error(tmp_path):
    # Existence and hash are checked at run time by the adapter, not by the parser.
    load_config(write(tmp_path, adapter_toml(argv='["/definitely/not/here"]')))


def test_config_object_is_immutable(tmp_path):
    cfg = load_config(write(tmp_path, GOOD))
    with pytest.raises(TypeError):
        cfg.adapters["x"] = cfg.adapters["my-agent"]  # type: ignore[index]
    with pytest.raises(Exception):  # noqa: B017 - pydantic frozen error type
        cfg.adapters["my-agent"].tier = "B"  # type: ignore[misc]


def test_error_messages_never_echo_argv_values_that_may_be_secret(tmp_path):
    secret = "sk-" + "a" * 40
    text = adapter_toml(argv=f'["relative-{secret}"]')
    with pytest.raises(ConfigError) as info:
        load_config(write(tmp_path, text))
    assert secret not in str(info.value)


def test_umask_default_files_are_checked_realistically(tmp_path):
    # A file created with the process umask (typically 0o644) must be accepted.
    path = tmp_path / "plain.toml"
    path.write_text(GOOD)
    path.chmod(0o644)
    assert os.stat(path).st_mode & 0o022 == 0
    load_config(path)
