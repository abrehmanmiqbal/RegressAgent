import os
import sqlite3
import stat

import pytest

from regressagent.models import AssertionOperator, AssertionSpec, RegressionCase
from regressagent.regression import RegressionEngine
from regressagent.runner import DeterministicLocalRunner
from regressagent.storage import (
    SCHEMA_VERSION,
    CaseExistsError,
    NotFoundError,
    PayloadTooLargeError,
    Repository,
    SchemaTooNewError,
    StorageError,
    case_hash,
)


def make_case(case_id="c1", expected="Hello, Ava!"):
    return RegressionCase(
        case_id=case_id,
        name="greet",
        input={"action": "greet", "name": "Ava"},
        assertions=[AssertionSpec(path="message", operator=AssertionOperator.EQUALS, expected=expected)],
    )


def run(case):
    return RegressionEngine(DeterministicLocalRunner()).run_case(case)


def test_new_database_is_migrated_to_current_version():
    with Repository() as repo:
        assert repo.schema_version == SCHEMA_VERSION == 3


def test_case_roundtrip_and_hash_is_stable():
    case = make_case()
    with Repository() as repo:
        digest = repo.save_case(case)
        assert digest == case_hash(case)
        assert repo.get_case("c1") == case
        assert repo.list_case_ids() == ["c1"]


def test_duplicate_case_refused_unless_overwrite():
    with Repository() as repo:
        repo.save_case(make_case())
        with pytest.raises(CaseExistsError):
            repo.save_case(make_case())
        new_digest = repo.save_case(make_case(expected="Hi"), overwrite=True)
        assert new_digest != case_hash(make_case())
        assert repo.get_case("c1").assertions[0].expected == "Hi"


def test_missing_case_and_run_raise_not_found():
    with Repository() as repo:
        with pytest.raises(NotFoundError):
            repo.get_case("nope")
        with pytest.raises(NotFoundError):
            repo.get_run(99)
        with pytest.raises(NotFoundError):
            repo.delete_case("nope")


def test_run_roundtrip_ties_result_to_case_hash():
    case = make_case()
    with Repository() as repo:
        repo.save_case(case)
        run_id = repo.record_run(case, run(case), label="baseline", runner_name="local")
        stored = repo.get_run(run_id)
        assert stored.case_hash == case_hash(case)
        assert stored.label == "baseline"
        assert stored.result.passed is True
        assert repo.list_runs("c1")[0].run_id == run_id


def test_editing_case_does_not_rewrite_run_history():
    case = make_case()
    with Repository() as repo:
        repo.save_case(case)
        old = repo.record_run(case, run(case), label="baseline", runner_name="local")
        repo.save_case(make_case(expected="Changed"), overwrite=True)
        assert repo.get_run(old).case_hash == case_hash(case)


def test_runner_error_result_persists():
    class Boom:
        def run(self, i):
            raise ValueError("x")

    case = make_case()
    result = RegressionEngine(Boom()).run_case(case)
    with Repository() as repo:
        repo.save_case(case)
        rid = repo.record_run(case, result, label="candidate", runner_name="boom")
        assert "ValueError" in repo.get_run(rid).result.error


def test_record_run_requires_saved_case_and_valid_label_and_matching_id():
    case = make_case()
    with Repository() as repo:
        with pytest.raises(NotFoundError):
            repo.record_run(case, run(case), label="a", runner_name="r")
        repo.save_case(case)
        with pytest.raises(StorageError):
            repo.record_run(case, run(case), label="", runner_name="r")
        with pytest.raises(StorageError):
            repo.record_run(case, run(case), label="x" * 65, runner_name="r")
        other = make_case("other")
        with pytest.raises(StorageError):
            repo.record_run(case, run(other), label="a", runner_name="r")


def test_delete_case_cascades_runs():
    case = make_case()
    with Repository() as repo:
        repo.save_case(case)
        repo.record_run(case, run(case), label="a", runner_name="r")
        repo.delete_case("c1")
        assert repo.list_runs("c1") == []


def test_oversized_case_rejected():
    big = RegressionCase(
        case_id="big", name="big", input={"blob": "x" * 1_100_000},
        assertions=[AssertionSpec(path="a", operator=AssertionOperator.EXISTS)],
    )
    with Repository() as repo:
        with pytest.raises(PayloadTooLargeError):
            repo.save_case(big)
        assert repo.list_case_ids() == []


def test_sql_injection_like_ids_are_stored_as_data():
    evil = "x'; DROP TABLE cases; --"
    case = make_case(evil)
    with Repository() as repo:
        repo.save_case(case)
        assert repo.get_case(evil).case_id == evil
        assert repo.list_case_ids() == [evil]


def test_file_database_persists_across_connections(tmp_path):
    path = tmp_path / "ap.db"
    case = make_case()
    with Repository(path) as repo:
        repo.save_case(case)
        repo.record_run(case, run(case), label="baseline", runner_name="local")
    with Repository(path) as repo:
        assert repo.get_case("c1") == case
        assert len(repo.list_runs("c1")) == 1


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
def test_new_file_database_has_owner_only_permissions_even_with_permissive_umask(tmp_path):
    path = tmp_path / "private.db"
    old_umask = os.umask(0)
    try:
        with Repository(path) as repo:
            assert repo.schema_version == SCHEMA_VERSION
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
def test_opening_existing_file_database_restricts_its_permissions(tmp_path):
    path = tmp_path / "existing.db"
    with Repository(path):
        pass
    path.chmod(0o644)

    with Repository(path):
        pass

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
def test_rollback_journal_has_owner_only_permissions(tmp_path):
    path = tmp_path / "journal.db"
    with Repository(path) as repo:
        repo._conn.execute("CREATE TABLE scratch (value INTEGER)")
        repo._conn.commit()
        repo._conn.execute("INSERT INTO scratch VALUES (1)")
        journal = path.with_name(path.name + "-journal")
        assert journal.exists()
        assert stat.S_IMODE(journal.stat().st_mode) == 0o600
        repo._conn.rollback()


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "mkfifo"), reason="POSIX FIFO support")
def test_special_file_database_path_is_refused(tmp_path):
    fifo = tmp_path / "database.fifo"
    os.mkfifo(fifo)

    with pytest.raises(StorageError, match="regular file"):
        Repository(fifo)


def test_database_from_newer_version_is_refused(tmp_path):
    path = tmp_path / "future.db"
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaTooNewError):
        Repository(path)


def test_corrupt_database_file_raises_storage_error_not_raw_sqlite(tmp_path):
    path = tmp_path / "bad.db"
    path.write_text("this is not a database")
    with pytest.raises(StorageError):
        Repository(path)
