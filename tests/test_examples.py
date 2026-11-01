"""The shipped examples/ must stay valid: they are documentation that is executed."""
from pathlib import Path

import pytest

from regressagent.cli import load_case_file, main
from regressagent.models import AssertionOperator
from regressagent.regression import RegressionEngine
from regressagent.runner import RUNNERS

CASES_DIR = Path(__file__).resolve().parent.parent / "examples" / "cases"
FILES = sorted(CASES_DIR.glob("*.json"))


def test_examples_exist():
    assert len(FILES) >= 3


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_each_example_loads_and_passes_with_local_runner(path):
    case = load_case_file(str(path))
    result = RegressionEngine(RUNNERS["local"]()).run_case(case)
    assert result.passed, [a.message for a in result.assertions if not a.passed]


def test_greet_example_fails_with_legacy_runner():
    case = load_case_file(str(CASES_DIR / "greet.json"))
    assert RegressionEngine(RUNNERS["local-legacy"]()).run_case(case).passed is False


def test_examples_demonstrate_every_operator():
    used = {a.operator for f in FILES for a in load_case_file(str(f)).assertions}
    assert used == set(AssertionOperator)


def test_examples_work_through_the_cli(tmp_path):
    db = str(tmp_path / "ex.db")
    for f in FILES:
        assert main(["--db", db, "case", "add", str(f)]) == 0
    assert main(["--db", db, "run-all", "--label", "ex", "--runner", "local"]) == 0


def test_example_trace_converts_to_a_case_that_passes_with_the_local_runner():
    from regressagent.trace import case_from_trace, parse_trace

    raw = (CASES_DIR.parent / "traces" / "greet-ava.json").read_bytes()
    case, notes = case_from_trace(parse_trace(raw))
    assert notes == [] and len(case.assertions) >= 2
    assert RegressionEngine(RUNNERS["local"]()).run_case(case).passed
    assert not RegressionEngine(RUNNERS["local-legacy"]()).run_case(case).passed


def test_example_config_is_valid(tmp_path):
    import shutil

    from regressagent.config import HttpAdapterConfig, load_config

    target = tmp_path / "regressagent.toml"
    shutil.copy(CASES_DIR.parent / "regressagent.example.toml", target)
    target.chmod(0o600)
    adapters = load_config(target).adapters
    assert set(adapters) == {"my-cmd-agent", "my-http-agent"}
    assert isinstance(adapters["my-http-agent"], HttpAdapterConfig)


def test_support_example_catches_the_skipped_tool_call():
    case = load_case_file(str(CASES_DIR / "support.json"))
    assert RegressionEngine(RUNNERS["local"]()).run_case(case).passed
    legacy = RegressionEngine(RUNNERS["local-legacy"]()).run_case(case)
    failed = {a.path + ":" + a.operator.value for a in legacy.assertions if not a.passed}
    assert "steps:tool_called" in failed and "steps:tool_order" in failed
    assert "message:equals" not in failed  # same words, different behaviour


def test_example_trace_with_steps_converts_and_round_trips():
    from regressagent.trace import case_from_trace, parse_trace

    raw = (CASES_DIR.parent / "traces" / "support-order.json").read_bytes()
    case, notes = case_from_trace(parse_trace(raw))
    assert notes == []
    assert RegressionEngine(RUNNERS["local"]()).run_case(case).passed
    assert not RegressionEngine(RUNNERS["local-legacy"]()).run_case(case).passed
