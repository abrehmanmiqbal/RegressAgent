"""Real schema-migration tests: a genuine v1 database is built from the frozen v1 DDL
(copied here on purpose; it must never change) and upgraded by the current code."""
import json
import sqlite3

import pytest
from pydantic import ValidationError

from regressagent import storage
from regressagent.models import AssertionOperator, AssertionSpec, RegressionCase, RunMetadata
from regressagent.regression import RegressionEngine
from regressagent.runner import DeterministicLocalRunner
from regressagent.storage import (
    SCHEMA_VERSION,
    Repository,
    SchemaTooNewError,
    StorageError,
    case_hash,
)

# Frozen snapshot of the shipped v1 schema. Do not edit.
V1_DDL = """
CREATE TABLE cases (
    case_id     TEXT PRIMARY KEY,
    case_json   TEXT NOT NULL,
    case_hash   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    case_hash   TEXT NOT NULL,
    label       TEXT NOT NULL,
    runner_name TEXT NOT NULL,
    passed      INTEGER NOT NULL CHECK (passed IN (0, 1)),
    result_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_runs_case ON runs(case_id, run_id);
"""


def make_case(case_id="c1"):
    return RegressionCase(
        case_id=case_id,
        name="greet",
        input={"action": "greet", "name": "Ava"},
        assertions=[
            AssertionSpec(path="message", operator=AssertionOperator.EQUALS, expected="Hello, Ava!")
        ],
    )


def build_v1_db(path):
    """Create a real v1 database containing one case and two runs, via raw SQL."""
    case = make_case()
    result = RegressionEngine(DeterministicLocalRunner()).run_case(case)
    case_json = storage._canonical_json(case)
    result_json = storage._canonical_json(result)
    conn = sqlite3.connect(path)
    conn.executescript(V1_DDL)
    conn.execute(
        "INSERT INTO cases VALUES (?,?,?,?,?)",
        (case.case_id, case_json, case_hash(case), "2026-01-01T00:00:00+00:00",
         "2026-01-01T00:00:00+00:00"),
    )
    for label in ("before", "after"):
        conn.execute(
            "INSERT INTO runs(case_id, case_hash, label, runner_name, passed, result_json,"
            " created_at) VALUES (?,?,?,?,?,?,?)",
            (case.case_id, case_hash(case), label, "local", 1, result_json,
             "2026-01-01T00:00:01+00:00"),
        )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    return case, result


def user_version(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def table_columns(path, table):
    conn = sqlite3.connect(path)
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def test_current_schema_is_v3():
    assert SCHEMA_VERSION == 3
    with Repository() as repo:
        assert repo.schema_version == 3


def test_v1_database_is_upgraded_and_data_preserved(tmp_path):
    path = tmp_path / "v1.db"
    case, result = build_v1_db(path)
    assert user_version(path) == 1

    with Repository(path) as repo:
        assert repo.schema_version == 3
        assert repo.get_case("c1") == case
        runs = repo.list_runs("c1")
        assert [r.run_id for r in runs] == [1, 2]
        assert [r.label for r in runs] == ["before", "after"]
        assert all(r.case_hash == case_hash(case) for r in runs)
        assert all(r.result == result for r in runs)
        assert all(r.created_at == "2026-01-01T00:00:01+00:00" for r in runs)
        # Old runs have no adapter metadata; that must read back as None, not a made-up value.
        assert all(r.metadata is None for r in runs)

    assert user_version(path) == 3
    assert "trace_json" in table_columns(path, "traces")
    cols = table_columns(path, "runs")
    for name in ("adapter_name", "executable_sha256", "argv_json", "env_names_json", "exit_code",
                 "duration_ms", "stdout_truncated", "stderr_truncated"):
        assert name in cols
    # Original columns and index survive.
    assert "result_json" in cols
    conn = sqlite3.connect(path)
    try:
        indexes = [r[1] for r in conn.execute("PRAGMA index_list(runs)")]
    finally:
        conn.close()
    assert "idx_runs_case" in indexes


def test_upgraded_database_accepts_new_runs_with_metadata_and_keeps_old_ones(tmp_path):
    path = tmp_path / "v1.db"
    case, result = build_v1_db(path)
    meta = RunMetadata(
        adapter_name="demo",
        adapter_tier="A",
        executable_path="/usr/bin/python3",
        executable_sha256="a" * 64,
        argv=["/usr/bin/python3", "agent.py"],
        env_names=["HOME_DIR"],
        timeout_seconds=30,
        limits={"rlimit_as": 536870912},
        exit_code=0,
        duration_ms=12,
        stdout_truncated=False,
        stderr_truncated=True,
    )
    with Repository(path) as repo:
        new_id = repo.record_run(case, result, label="new", runner_name="demo", metadata=meta)
        assert new_id == 3  # autoincrement continues after the v1 rows
        assert repo.get_run(new_id).metadata == meta
        assert repo.get_run(1).metadata is None
    with Repository(path) as repo:  # reopening an already-v2 file is a no-op
        assert repo.schema_version == 3
        assert repo.get_run(3).metadata == meta
        assert len(repo.list_runs("c1")) == 3


def test_run_without_metadata_roundtrips_as_none():
    case = make_case()
    result = RegressionEngine(DeterministicLocalRunner()).run_case(case)
    with Repository() as repo:
        repo.save_case(case)
        rid = repo.record_run(case, result, label="a", runner_name="local")
        assert repo.get_run(rid).metadata is None


def test_database_newer_than_supported_is_refused_and_left_untouched(tmp_path):
    path = tmp_path / "future.db"
    build_v1_db(path)
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    before = path.read_bytes()
    with pytest.raises(SchemaTooNewError):
        Repository(path)
    assert path.read_bytes() == before
    assert user_version(path) == SCHEMA_VERSION + 1


def test_negative_user_version_is_refused_not_misapplied(tmp_path):
    """user_version = -1 used to index MIGRATIONS[-1] (the last migration) by accident."""
    path = tmp_path / "neg.db"
    build_v1_db(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = -1")
    conn.commit()
    conn.close()
    before = path.read_bytes()
    with pytest.raises(StorageError):
        Repository(path)
    assert path.read_bytes() == before


def test_corrupt_files_are_clean_storage_errors(tmp_path):
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a database" * 100)
    with pytest.raises(StorageError):
        Repository(garbage)

    truncated = tmp_path / "trunc.db"
    build_v1_db(truncated)
    data = truncated.read_bytes()
    truncated.write_bytes(data[: len(data) // 2])
    with pytest.raises(StorageError):
        Repository(truncated)


def test_v1_marked_database_missing_its_tables_fails_cleanly_and_unchanged(tmp_path):
    path = tmp_path / "hollow.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE unrelated(x)")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    before = path.read_bytes()
    with pytest.raises(StorageError):
        Repository(path)
    assert path.read_bytes() == before
    assert user_version(path) == 1


def test_foreign_database_with_version_zero_is_not_clobbered(tmp_path):
    """A pre-existing foreign DB (user_version 0) that already has a `cases` table."""
    path = tmp_path / "foreign.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE cases(mine TEXT)")
    conn.execute("INSERT INTO cases VALUES ('precious')")
    conn.commit()
    conn.close()
    with pytest.raises(StorageError):
        Repository(path)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT mine FROM cases").fetchall() == [("precious",)]
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        conn.close()


def test_failing_migration_rolls_back_completely_including_version(tmp_path, monkeypatch):
    path = tmp_path / "v1.db"
    build_v1_db(path)
    bad = list(storage.MIGRATIONS)
    # Valid first statement, then a failing one: nothing may remain half-applied.
    bad[1] = "ALTER TABLE runs ADD COLUMN adapter_name TEXT; SELECT * FROM no_such_table;"
    monkeypatch.setattr(storage, "MIGRATIONS", bad)
    with pytest.raises(StorageError):
        Repository(path)
    assert user_version(path) == 1
    assert "adapter_name" not in table_columns(path, "runs")


def test_runmetadata_rejects_secret_looking_values_and_bad_shapes():
    base = {"adapter_name": "demo", "adapter_tier": "A"}
    # env_names must be names, not NAME=value pairs (values must never be stored).
    with pytest.raises(ValidationError):
        RunMetadata(**base, env_names=["TOKEN=abc123"])
    with pytest.raises(ValidationError):
        RunMetadata(**base, env_names=["has space"])
    with pytest.raises(ValidationError):
        RunMetadata(**base, executable_sha256="not-a-hash")
    with pytest.raises(ValidationError):
        RunMetadata(**base, argv=["x"] * 300)
    with pytest.raises(ValidationError):
        RunMetadata(**base, argv=["y" * 5000])
    with pytest.raises(ValidationError):
        RunMetadata(adapter_name="demo", adapter_tier="Z")
    with pytest.raises(ValidationError):
        RunMetadata(**base, unexpected_field=1)
    with pytest.raises(ValidationError):
        RunMetadata(**base, duration_ms=-1)


def test_metadata_is_stored_as_json_arrays_not_executable_text(tmp_path):
    path = tmp_path / "m.db"
    case = make_case()
    result = RegressionEngine(DeterministicLocalRunner()).run_case(case)
    evil = ["/bin/true", "'; DROP TABLE runs; --", "$(rm -rf /)"]
    meta = RunMetadata(adapter_name="demo", adapter_tier="A", argv=evil)
    with Repository(path) as repo:
        repo.save_case(case)
        rid = repo.record_run(case, result, label="a", runner_name="demo", metadata=meta)
        assert repo.get_run(rid).metadata.argv == evil
    conn = sqlite3.connect(path)
    try:
        raw = conn.execute("SELECT argv_json FROM runs WHERE run_id = ?", (rid,)).fetchone()[0]
        assert json.loads(raw) == evil
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize(
    "column,value",
    [
        ("argv_json", "{not json"),
        ("argv_json", '"a string, not a list"'),
        ("env_names_json", '["TOKEN=secret"]'),
        ("executable_sha256", "zzz"),
        ("adapter_tier", "Z"),
        ("limits_json", '{"a": "not-int"}'),
    ],
)
def test_tampered_metadata_in_the_database_is_a_clean_storage_error(tmp_path, column, value):
    path = tmp_path / "t.db"
    case = make_case()
    result = RegressionEngine(DeterministicLocalRunner()).run_case(case)
    meta = RunMetadata(adapter_name="demo", adapter_tier="A", argv=["/bin/true"])
    with Repository(path) as repo:
        repo.save_case(case)
        rid = repo.record_run(case, result, label="a", runner_name="demo", metadata=meta)
    conn = sqlite3.connect(path)
    conn.execute(f"UPDATE runs SET {column} = ? WHERE run_id = ?", (value, rid))
    conn.commit()
    conn.close()
    with Repository(path) as repo, pytest.raises(StorageError):
        repo.get_run(rid)


def test_version_bump_happens_inside_the_migration_transaction(tmp_path, monkeypatch):
    """A crash between COMMIT and the version bump would leave schema v2 labelled v1 and make
    the next start re-run ALTER TABLE. So the bump must execute before COMMIT."""
    path = tmp_path / "v1.db"
    build_v1_db(path)
    statements: list[str] = []
    real_connect = sqlite3.connect

    def tracing_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(storage.sqlite3, "connect", tracing_connect)
    with Repository(path) as repo:
        assert repo.schema_version == 3
    flat = [s.strip().rstrip(";").strip().upper() for s in statements]
    bump = next(i for i, s in enumerate(flat) if s.startswith("PRAGMA USER_VERSION = 2"))
    begin = next(i for i, s in enumerate(flat) if s == "BEGIN")
    commit = next(i for i, s in enumerate(flat) if s == "COMMIT")
    assert begin < bump < commit


def test_v2_database_is_upgraded_to_v3_and_data_preserved(tmp_path):
    from regressagent.storage import MIGRATIONS

    path = tmp_path / "v2.db"
    case, _result = build_v1_db(path)  # a v1 file with one case and two runs
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[1])  # -> v2 (adapter metadata columns)
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()
    assert user_version(path) == 2
    assert "traces" not in {r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master")}

    with Repository(path) as repo:
        assert repo.schema_version == 3
        assert repo.get_case("c1") == case
        assert [r.run_id for r in repo.list_runs("c1")] == [1, 2]
        assert repo.list_trace_ids() == []
    assert user_version(path) == 3
