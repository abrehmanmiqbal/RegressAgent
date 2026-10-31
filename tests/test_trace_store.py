"""Raw trace persistence (schema v3). Stored traces are untrusted data and are redacted BEFORE
they touch the disk (same policy as stored run results)."""
import json
import sqlite3

import pytest

from regressagent.cli import main
from regressagent.storage import NotFoundError, Repository, StorageError, TraceExistsError
from regressagent.trace import parse_trace

TOKEN = "github_pat_" + "A1b2C3d4E5" * 3  # fake, matches the token pattern


def make(output=None, trace_id="t1", input_=None):
    return parse_trace(
        json.dumps(
            {"trace_id": trace_id, "input": input_ or {"q": "hi"}, "output": output or {"a": 1}}
        ).encode()
    )


# ---------------------------------------------------------------- repository
def test_save_get_list_delete_roundtrip():
    with Repository() as repo:
        digest, redacted = repo.save_trace(make())
        assert len(digest) == 64 and redacted is False
        assert repo.get_trace("t1") == make()
        repo.save_trace(make(trace_id="a0"))
        assert repo.list_trace_ids() == ["a0", "t1"]
        repo.delete_trace("a0")
        assert repo.list_trace_ids() == ["t1"]


def test_hash_is_stable_and_depends_on_content():
    with Repository() as repo:
        h1, _ = repo.save_trace(make(output={"a": 1, "b": 2}))
        h2, _ = repo.save_trace(make(output={"b": 2, "a": 1}), overwrite=True)  # key order only
        h3, _ = repo.save_trace(make(output={"a": 2, "b": 2}), overwrite=True)
        assert h1 == h2 and h1 != h3


def test_duplicate_is_refused_unless_overwrite():
    with Repository() as repo:
        repo.save_trace(make())
        with pytest.raises(TraceExistsError):
            repo.save_trace(make(output={"a": 2}))
        assert repo.get_trace("t1").output == {"a": 1}
        repo.save_trace(make(output={"a": 2}), overwrite=True)
        assert repo.get_trace("t1").output == {"a": 2}


def test_missing_trace_errors_are_clean():
    with Repository() as repo:
        with pytest.raises(NotFoundError):
            repo.get_trace("nope")
        with pytest.raises(NotFoundError):
            repo.delete_trace("nope")
    assert issubclass(TraceExistsError, StorageError)


def test_secrets_are_redacted_before_they_reach_the_disk(tmp_path):
    path = tmp_path / "t.db"
    trace = make(
        output={"api_key": "sk-live-abcdef1234567890", "msg": f"token {TOKEN}", "ok": "fine"},
        input_={"q": f"use {TOKEN}"},
    )
    with Repository(path) as repo:
        _, redacted = repo.save_trace(trace)
        stored = repo.get_trace("t1")
    assert redacted is True
    raw = path.read_bytes()
    assert TOKEN.encode() not in raw and b"sk-live-abcdef1234567890" not in raw
    assert stored.output["ok"] == "fine" and "[REDACTED" in stored.output["api_key"]
    row = sqlite3.connect(path).execute("SELECT redacted FROM traces").fetchone()
    assert row == (1,)


def test_stored_row_that_was_tampered_with_is_reported_not_trusted(tmp_path):
    path = tmp_path / "t.db"
    with Repository(path) as repo:
        repo.save_trace(make())
    conn = sqlite3.connect(path)
    conn.execute("UPDATE traces SET trace_json = '{\"trace_id\": 5}'")
    conn.commit()
    conn.close()
    with Repository(path) as repo, pytest.raises(StorageError):
        repo.get_trace("t1")


# ---------------------------------------------------------------- CLI
def cli(tmp_path, *argv):
    return main(["--db", str(tmp_path / "t.db"), *argv])


def write_trace(tmp_path, output, name="tr.json", trace_id="cli-1", input_=None):
    f = tmp_path / name
    f.write_text(json.dumps({"trace_id": trace_id, "input": input_ or {"q": "x"}, "output": output}))
    return str(f)


def test_cli_trace_add_list_show_delete(tmp_path, capsys):
    f = write_trace(tmp_path, {"msg": "hi", "n": 2})
    assert cli(tmp_path, "trace", "add", f) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["saved"] is True and out["trace_id"] == "cli-1" and out["redacted"] is False
    assert cli(tmp_path, "trace", "add", f) == 2  # exists
    assert "--overwrite" in capsys.readouterr().err
    assert cli(tmp_path, "trace", "add", f, "--overwrite") == 0
    capsys.readouterr()
    cli(tmp_path, "trace", "list")
    assert json.loads(capsys.readouterr().out) == ["cli-1"]
    cli(tmp_path, "trace", "show", "cli-1")
    assert json.loads(capsys.readouterr().out)["output"] == {"msg": "hi", "n": 2}
    assert cli(tmp_path, "trace", "delete", "cli-1") == 2  # needs --yes
    assert "--yes" in capsys.readouterr().err
    assert cli(tmp_path, "trace", "delete", "cli-1", "--yes") == 0
    capsys.readouterr()
    cli(tmp_path, "trace", "list")
    assert json.loads(capsys.readouterr().out) == []
    assert cli(tmp_path, "trace", "show", "cli-1") == 2


def test_cli_bad_trace_file_is_not_stored(tmp_path, capsys):
    f = tmp_path / "bad.json"
    f.write_text("nope")
    assert cli(tmp_path, "trace", "add", str(f)) == 2
    assert capsys.readouterr().err.startswith("error:")
    cli(tmp_path, "trace", "list")
    assert json.loads(capsys.readouterr().out) == []


def test_cli_add_reports_redaction_and_stores_no_secret(tmp_path, capsys):
    f = write_trace(tmp_path, {"api_key": "sk-live-abcdef1234567890", "ok": 1})
    assert cli(tmp_path, "trace", "add", f) == 0
    assert json.loads(capsys.readouterr().out)["redacted"] is True
    assert b"sk-live-abcdef1234567890" not in (tmp_path / "t.db").read_bytes()


def test_case_from_stored_trace_skips_redacted_values(tmp_path, capsys):
    f = write_trace(
        tmp_path,
        {"api_key": "sk-live-abcdef1234567890", "msg": f"token {TOKEN}", "ok": "fine", "n": 3},
    )
    cli(tmp_path, "trace", "add", f)
    capsys.readouterr()
    assert cli(tmp_path, "case", "from-trace", "cli-1", "--stored") == 0
    captured = capsys.readouterr()
    case = json.loads(captured.out)
    assert [a["path"] for a in case["assertions"]] == ["n", "ok"]  # stored keys are sorted
    assert "api_key" in captured.err and "msg" in captured.err and "redacted" in captured.err
    assert "sk-live" not in captured.out + captured.err


def test_case_from_stored_trace_save_and_run_loop(tmp_path, capsys):
    from regressagent.runner import DeterministicLocalRunner

    request = {"action": "greet", "name": "Ava"}
    output = DeterministicLocalRunner().run(request)
    f = write_trace(tmp_path, output, trace_id="greet-ava", input_=request)
    assert cli(tmp_path, "trace", "add", f) == 0
    assert cli(tmp_path, "case", "from-trace", "greet-ava", "--stored", "--save") == 0
    assert cli(tmp_path, "run", "greet-ava", "--label", "good", "--runner", "local") == 0
    assert cli(tmp_path, "run", "greet-ava", "--label", "bad", "--runner", "local-legacy") == 1


def test_stored_flag_on_unknown_trace_and_file_mode_unchanged(tmp_path, capsys):
    assert cli(tmp_path, "case", "from-trace", "ghost", "--stored") == 2
    assert "not found" in capsys.readouterr().err
    f = write_trace(tmp_path, {"a": 1})
    assert cli(tmp_path, "case", "from-trace", f) == 0  # plain file mode still works
