"""The dashboard summary: one cheap query set, no per-run JSON loading (D-023)."""
import json
import sqlite3
import threading

import pytest
from test_api import CASE, V1, Client

from regressagent.api import make_server
from regressagent.models import AssertionOperator, AssertionSpec, RegressionCase, RegressionResult
from regressagent.storage import Repository


def case(case_id, name=None):
    return RegressionCase(
        case_id=case_id, name=name or f"Case {case_id}", input={},
        assertions=[AssertionSpec(path="a", operator=AssertionOperator.EXISTS)],
    )  # fmt: skip


def record(repo, c, passed, label="l"):
    return repo.record_run(c, RegressionResult(case_id=c.case_id, passed=passed), label=label, runner_name="local")


def test_empty_database():
    with Repository() as repo:
        assert repo.overview() == {"counts": {"cases": 0, "traces": 0, "runs": 0}, "cases": [], "recent": []}


def test_counts_history_and_recent_runs():
    with Repository() as repo:
        a, b = case("a", "Alpha"), case("b")
        repo.save_case(a), repo.save_case(b)
        for flag in (True, False, True):
            record(repo, a, flag)
        record(repo, b, False)
        overview = repo.overview(history=2, recent=3)
    assert overview["counts"] == {"cases": 2, "traces": 0, "runs": 4}
    by_id = {c["case_id"]: c for c in overview["cases"]}
    assert by_id["a"]["name"] == "Alpha" and by_id["a"]["runs"] == 3 and by_id["a"]["passed_runs"] == 2
    assert by_id["a"]["history"] == [False, True]  # the last 2 runs, oldest first
    assert by_id["b"]["history"] == [False] and by_id["b"]["name"] == "Case b"
    assert [r["run_id"] for r in overview["recent"]] == [4, 3, 2]  # newest first, capped
    assert set(overview["recent"][0]) == {"run_id", "case_id", "label", "runner", "passed", "created_at"}


def test_cases_without_runs_and_corrupt_case_json_do_not_break_it(tmp_path):
    path = tmp_path / "o.db"
    with Repository(path) as repo:
        repo.save_case(case("ok"))
        repo.save_case(case("broken"))
    conn = sqlite3.connect(path)
    conn.execute("UPDATE cases SET case_json = 'not json' WHERE case_id = 'broken'")
    conn.commit()
    conn.close()
    with Repository(path) as repo:
        cases = {c["case_id"]: c for c in repo.overview()["cases"]}
    assert cases["ok"]["runs"] == 0 and cases["ok"]["history"] == []
    assert cases["broken"]["name"] == "broken"  # falls back to the id


def test_overview_caps_the_number_of_cases():
    with Repository() as repo:
        for i in range(7):
            repo.save_case(case(f"c{i}"))
        assert len(repo.overview(max_cases=5)["cases"]) == 5
        assert repo.overview(max_cases=5)["counts"]["cases"] == 7  # the real count is still reported


@pytest.fixture
def api(tmp_path):
    server, token = make_server(str(tmp_path / "t.db"), "127.0.0.1", 0)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True).start()
    yield Client(server.server_address[1], token)
    server.shutdown()
    server.server_close()


def test_overview_endpoint(api):
    assert api.call("GET", f"{V1}/overview", token=None)[0] == 401
    assert api.call("GET", f"{V1}/overview")[1]["counts"] == {"cases": 0, "traces": 0, "runs": 0}
    api.call("POST", f"{V1}/cases", CASE)
    api.call("POST", f"{V1}/cases/greet-ava/run", {"label": "x", "runner": "local-legacy"})
    api.call("POST", f"{V1}/cases/greet-ava/run", {"label": "y", "runner": "local"})
    body = api.call("GET", f"{V1}/overview")[1]
    assert body["counts"]["runs"] == 2 and body["cases"][0]["history"] == [False, True]
    assert json.dumps(body).count("Traceback") == 0
