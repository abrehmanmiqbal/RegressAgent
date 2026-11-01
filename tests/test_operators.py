"""Tests for numeric (gt/lt) and regex assertion operators, incl. ReDoS guards."""
import json
import math
import time

import pytest
from pydantic import ValidationError

from regressagent.assertions import evaluate_assertion
from regressagent.cli import main
from regressagent.models import AssertionOperator as Op
from regressagent.models import AssertionSpec
from regressagent.regex_safety import MAX_TEXT_LENGTH

GT, LT, RX = Op("gt"), Op("lt"), Op("regex")


def spec(path, op, expected=None):
    return AssertionSpec(path=path, operator=op, expected=expected)


def run(output, path, op, expected):
    return evaluate_assertion(output, spec(path, op, expected))


# ---------------------------------------------------------------- numeric
def test_gt_and_lt_basic():
    assert run({"n": 5}, "n", GT, 3).passed is True
    assert run({"n": 3}, "n", GT, 3).passed is False
    assert run({"n": 2}, "n", LT, 3).passed is True
    assert run({"n": 3}, "n", LT, 3).passed is False


def test_numeric_int_float_mix():
    assert run({"n": 2.5}, "n", GT, 2).passed is True
    assert run({"n": 2}, "n", LT, 2.5).passed is True


def test_numeric_negative_and_zero():
    assert run({"n": 0}, "n", GT, -1).passed is True
    assert run({"n": -5}, "n", LT, 0).passed is True


@pytest.mark.parametrize("actual", [True, False, "5", None, [5], {"a": 5}])
def test_numeric_rejects_non_number_actual(actual):
    for op in (GT, LT):
        result = run({"n": actual}, "n", op, 0 if op == GT else 10)
        assert result.passed is False
        assert "not a number" in result.message


def test_numeric_missing_path_fails():
    assert run({}, "n", GT, 0).passed is False


def test_numeric_nan_actual_never_passes():
    out = {"n": math.nan}
    assert run(out, "n", GT, 0).passed is False
    assert run(out, "n", LT, 0).passed is False


@pytest.mark.parametrize("bad", ["5", True, None, [1], {"a": 1}, math.nan, math.inf, -math.inf])
def test_numeric_expected_must_be_finite_number(bad):
    for op in (GT, LT):
        with pytest.raises(ValidationError):
            spec("n", op, bad)


# ---------------------------------------------------------------- regex basics
def test_regex_uses_search_semantics_and_anchors():
    assert run({"m": "Hello, Ava!"}, "m", RX, r"Ava").passed is True
    assert run({"m": "Hello, Ava!"}, "m", RX, r"^Ava").passed is False
    assert run({"m": "Hello, Ava!"}, "m", RX, r"^Hello, \w+!$").passed is True


@pytest.mark.parametrize("actual", [5, None, True, ["a"], {"a": "b"}])
def test_regex_non_string_actual_fails(actual):
    result = run({"m": actual}, "m", RX, r"a")
    assert result.passed is False
    assert "not a string" in result.message


def test_regex_missing_path_fails():
    assert run({}, "m", RX, r"a").passed is False


@pytest.mark.parametrize(
    "pattern",
    [r"^Hello, \w+!$", r"\d{4}-\d{2}-\d{2}", r"order #\d+", r"(?i)error", r"colou?r", r"^\d{1,3}(\.\d{1,3}){3}$"],
)
def test_typical_patterns_are_accepted(pattern):
    spec("m", RX, pattern)


# ---------------------------------------------------------------- regex validation
@pytest.mark.parametrize("bad", [None, 5, True, ["a"], "", "("])
def test_regex_expected_must_be_valid_non_empty_string(bad):
    with pytest.raises(ValidationError):
        spec("m", RX, bad)


def test_regex_pattern_length_limit():
    spec("m", RX, "a" * 200)
    with pytest.raises(ValidationError):
        spec("m", RX, "a" * 201)


@pytest.mark.parametrize(
    "pattern",
    [
        r"(a+)+b",  # classic nested quantifier
        r"(a*)*b",
        r"(a|a)*b",  # ambiguous alternation in a repeat
        r"(foo|bar)+",  # alternation in a repeat (rejected conservatively)
        r"(.*a){20}",  # unbounded repeat multiplied by an outer fixed repeat
        r"(a{1,2}){1,50}",  # variable repeat nested in variable repeat
        r"a*a*b",  # two unbounded repeats: O(n^3) in practice
        r"\w+@\w+\.\w+",  # three unbounded repeats
        r"a{0,100}a{0,100}a{0,100}b",  # bounded repeats whose product explodes
        r"(a)\1",  # backreference
        r"(a)?(?(1)b|c)",  # conditional
        r"a{101}",  # single repeat above the cap
        r"a{1,101}",
        r"(((((a{10}){10}){10}){10}){10})",  # nested fixed repeats: product above cap
    ],
)
def test_dangerous_patterns_are_rejected(pattern):
    with pytest.raises(ValidationError):
        spec("m", RX, pattern)


def test_regex_text_length_limit_fails_closed():
    ok = run({"m": "a" * MAX_TEXT_LENGTH}, "m", RX, r"a$")
    assert ok.passed is True
    too_long = run({"m": "a" * (MAX_TEXT_LENGTH + 1)}, "m", RX, r"a$")
    assert too_long.passed is False
    assert "too long" in too_long.message


def test_worst_accepted_single_repeat_is_fast_enough():
    # Sanity check, not a hard guarantee: one unbounded repeat over the max text size.
    start = time.perf_counter()
    result = run({"m": "a" * MAX_TEXT_LENGTH}, "m", RX, r"a*b")
    assert result.passed is False
    assert time.perf_counter() - start < 2.0


def test_existing_operators_unaffected_by_new_validation():
    assert spec("m", Op.EQUALS, "(").expected == "("
    assert spec("m", Op.CONTAINS, "a" * 5000).path == "m"


# ---------------------------------------------------------------- CLI integration
def cli(tmp_path, *argv):
    return main(["--db", str(tmp_path / "t.db"), *argv])


def write_case(tmp_path, assertions, name="c.json", case_id="op-case", inp=None):
    data = {
        "case_id": case_id,
        "name": "ops",
        "input": inp or {"action": "classify", "text": "hello"},
        "assertions": assertions,
    }
    f = tmp_path / name
    f.write_text(json.dumps(data))
    return str(f)


def test_cli_accepts_numeric_and_regex_cases(tmp_path, capsys):
    f = write_case(
        tmp_path,
        [
            {"path": "length", "operator": "gt", "expected": 3},
            {"path": "length", "operator": "lt", "expected": 10},
            {"path": "category", "operator": "regex", "expected": "^contains_\\w+$"},
        ],
    )
    assert cli(tmp_path, "case", "add", f) == 0
    capsys.readouterr()
    assert cli(tmp_path, "run", "op-case", "--label", "x") == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True


def test_cli_rejects_redos_pattern_in_case_file(tmp_path, capsys):
    f = write_case(tmp_path, [{"path": "category", "operator": "regex", "expected": "(a+)+b"}])
    assert cli(tmp_path, "case", "add", f) == 2
    assert "Invalid case file" in capsys.readouterr().err


def test_cli_failing_numeric_assertion_gives_exit_1(tmp_path):
    f = write_case(tmp_path, [{"path": "length", "operator": "gt", "expected": 99}])
    assert cli(tmp_path, "case", "add", f) == 0
    assert cli(tmp_path, "run", "op-case", "--label", "x") == 1


def test_report_with_regex_pattern_is_escaped(tmp_path, capsys):
    pattern = r"^Hello, \w+!|x$"
    f = write_case(
        tmp_path,
        [{"path": "message", "operator": "regex", "expected": pattern}],
        inp={"action": "greet", "name": "Ava"},
    )
    cli(tmp_path, "case", "add", f)
    cli(tmp_path, "run", "op-case", "--label", "a", "--runner", "local-legacy")
    cli(tmp_path, "run", "op-case", "--label", "b", "--runner", "local")
    capsys.readouterr()
    assert cli(tmp_path, "report", "1", "2") == 0
    out = capsys.readouterr().out
    assert "improved".upper() in out
    assert "regex" in out
    for line in out.splitlines():
        if line.startswith("| message"):
            assert "|x$" not in line  # raw pipe from the pattern must be escaped


# ---------------------------------------------------------------- assertion-count cap
def _case_with(n):
    from regressagent.models import RegressionCase

    return RegressionCase(
        case_id="many",
        name="many",
        input={},
        assertions=[AssertionSpec(path="a", operator=Op.EXISTS) for _ in range(n)],
    )


def test_assertion_count_is_capped_at_100():
    assert len(_case_with(100).assertions) == 100
    with pytest.raises(ValidationError):
        _case_with(101)


# ---------------------------------------------------------------- matches_type
MT = Op("matches_type")


@pytest.mark.parametrize(
    ("actual", "type_name"),
    [
        ("x", "string"),
        (1, "number"),
        (1.5, "number"),
        (1, "integer"),
        (True, "boolean"),
        (None, "null"),
        ({"a": 1}, "object"),
        ([1], "array"),
        ([], "array"),
        ({}, "object"),
    ],
)
def test_matches_type_accepts_the_right_type(actual, type_name):
    assert run({"v": actual}, "v", MT, type_name).passed is True


@pytest.mark.parametrize(
    ("actual", "type_name"),
    [
        (True, "number"),  # bool is never a number
        (True, "integer"),
        (False, "number"),
        (1.5, "integer"),  # strict: 1.5 is not an integer
        (1.0, "integer"),  # strict: floats are not integers, even whole ones
        ("1", "number"),
        (1, "string"),
        (None, "string"),
        ([], "object"),
        ({}, "array"),
        (0, "boolean"),
        (0, "null"),
    ],
)
def test_matches_type_rejects_the_wrong_type(actual, type_name):
    result = run({"v": actual}, "v", MT, type_name)
    assert result.passed is False
    assert "type" in result.message.lower()


def test_matches_type_accepts_a_list_of_alternatives():
    assert run({"v": None}, "v", MT, ["string", "null"]).passed is True
    assert run({"v": "x"}, "v", MT, ["string", "null"]).passed is True
    assert run({"v": 3}, "v", MT, ["string", "null"]).passed is False


def test_matches_type_missing_path_fails_but_null_value_is_present():
    assert run({}, "v", MT, "null").passed is False
    assert run({"v": None}, "v", MT, "null").passed is True


@pytest.mark.parametrize(
    "bad",
    [None, 5, True, "", "str", "String", "int", "float", [], ["string", "string"], ["nope"], [1], ["string", None], {"a": 1}, ["string"] * 8],
)
def test_matches_type_expected_must_be_known_type_name(bad):
    with pytest.raises(ValidationError):
        spec("v", MT, bad)


def test_matches_type_is_documented_in_the_example_cases_and_cli(tmp_path):
    f = write_case(
        tmp_path,
        [
            {"path": "length", "operator": "matches_type", "expected": "integer"},
            {"path": "category", "operator": "matches_type", "expected": ["string", "null"]},
        ],
    )
    assert cli(tmp_path, "case", "add", f) == 0
    assert cli(tmp_path, "run", "op-case", "--label", "x") == 0
    bad = write_case(
        tmp_path,
        [{"path": "length", "operator": "matches_type", "expected": "float"}],
        name="bad.json",
        case_id="bad-case",
    )
    assert cli(tmp_path, "case", "add", bad) == 2


# ---------------------------------------------------------------- path limits
def test_path_length_and_depth_limits():
    spec("a" * 512, Op.EXISTS)  # the limits themselves are fine
    spec(".".join(["a"] * 32), Op.EXISTS)
    with pytest.raises(ValidationError):
        spec("a" * 513, Op.EXISTS)
    with pytest.raises(ValidationError):
        spec(".".join(["a"] * 33), Op.EXISTS)


@pytest.mark.parametrize("bad", ["a..b", ".a", "a.", ".", ".."])
def test_paths_with_empty_segments_are_rejected_at_load(bad):
    with pytest.raises(ValidationError):
        spec(bad, Op.EXISTS)


def test_cli_rejects_a_case_with_a_too_long_path(tmp_path, capsys):
    f = write_case(tmp_path, [{"path": "a" * 513, "operator": "exists"}])
    assert cli(tmp_path, "case", "add", f) == 2
    assert "Traceback" not in capsys.readouterr().err
