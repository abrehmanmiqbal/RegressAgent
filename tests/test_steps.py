"""Multi-step traces (D-018): tool-call steps in traces, the tool_* operators, and cases built
from steps. Steps are untrusted data like everything else in a trace."""
import json

import pytest
from pydantic import ValidationError

from regressagent.assertions import evaluate_assertion
from regressagent.cli import main
from regressagent.models import AssertionOperator as Op
from regressagent.models import AssertionSpec
from regressagent.runner import RUNNERS
from regressagent.storage import Repository
from regressagent.trace import TraceError, case_from_trace, parse_trace

TOKEN = "github_pat_" + "A1b2C3d4E5" * 3
STEPS = [
    {"tool": "search_orders", "arguments": {"order_id": "A-100", "limit": 3}, "result": {"hits": 9}},
    {"tool": "send_reply", "arguments": {"template": "shipping_update"}},
]


def trace_bytes(output=None, steps=None, **extra):
    data = {"trace_id": "t1", "input": {"q": "hi"}, "output": {"message": "ok"} if output is None else output}
    if steps is not None:
        data["steps"] = steps
    data.update(extra)
    return json.dumps(data).encode()


def spec(op, expected, path="steps"):
    return AssertionSpec(path=path, operator=Op(op), expected=expected)


def ev(actual, op, expected):
    return evaluate_assertion({"steps": actual}, spec(op, expected))


CALLS = [{"tool": "a", "arguments": {}}, {"tool": "b"}, {"tool": "a"}, {"tool": "c"}]


# ---------------------------------------------------------------- operators
def test_tool_called():
    assert ev(CALLS, "tool_called", "b").passed is True
    assert ev(CALLS, "tool_called", "z").passed is False
    assert ev([], "tool_called", "a").passed is False


def test_tool_not_called():
    assert ev(CALLS, "tool_not_called", "z").passed is True
    assert ev(CALLS, "tool_not_called", "a").passed is False
    assert ev([], "tool_not_called", "a").passed is True


def test_tool_order_is_a_subsequence_check():
    assert ev(CALLS, "tool_order", ["a", "b", "c"]).passed is True
    assert ev(CALLS, "tool_order", ["a", "a", "c"]).passed is True
    assert ev(CALLS, "tool_order", ["b", "a"]).passed is True
    assert ev(CALLS, "tool_order", ["c", "a"]).passed is False  # wrong order
    assert ev(CALLS, "tool_order", ["a", "a", "a"]).passed is False  # only two calls to a
    assert ev(CALLS, "tool_order", ["z"]).passed is False


@pytest.mark.parametrize("op", ["tool_called", "tool_not_called", "tool_order"])
@pytest.mark.parametrize("actual", ["a", 5, None, {"tool": "a"}, True])
def test_non_list_actual_always_fails(op, actual):
    expected = ["a"] if op == "tool_order" else "a"
    result = ev(actual, op, expected)
    assert result.passed is False and "list" in result.message


def test_missing_path_fails():
    assert evaluate_assertion({}, spec("tool_called", "a")).passed is False
    assert evaluate_assertion({}, spec("tool_not_called", "a")).passed is False


MALFORMED = [{"tool": "a"}, "oops", {"tool": ""}, {"tool": 5}, {"name": "x"}, None]


def test_malformed_steps_cannot_prove_absence_but_cannot_fake_presence():
    assert ev(MALFORMED, "tool_called", "a").passed is True  # the well-formed entry counts
    assert ev(MALFORMED, "tool_called", "x").passed is False
    result = ev(MALFORMED, "tool_not_called", "z")
    assert result.passed is False and "malformed" in result.message  # fail closed
    assert ev(MALFORMED, "tool_order", ["a"]).passed is True
    assert ev(MALFORMED, "tool_order", ["a", "x"]).passed is False


@pytest.mark.parametrize("op", ["tool_called", "tool_not_called"])
@pytest.mark.parametrize("bad", [None, 5, True, "", ["a"], {"a": 1}, "x" * 129])
def test_single_tool_expected_must_be_a_name(op, bad):
    with pytest.raises(ValidationError):
        spec(op, bad)


@pytest.mark.parametrize(
    "bad", [None, "a", [], [1], [""], ["a", None], ["x" * 129], ["a"] * 51, {"a": 1}, True]
)
def test_tool_order_expected_must_be_a_list_of_names(bad):
    with pytest.raises(ValidationError):
        spec("tool_order", bad)
    spec("tool_order", ["a"] * 50)  # the limit itself is fine


# ---------------------------------------------------------------- trace model
def test_trace_with_steps_parses_and_steps_default_to_empty():
    t = parse_trace(trace_bytes(steps=STEPS))
    assert [s.tool for s in t.steps] == ["search_orders", "send_reply"]
    assert t.steps[0].arguments == {"order_id": "A-100", "limit": 3} and t.steps[0].result == {"hits": 9}
    assert t.steps[1].arguments == {"template": "shipping_update"} and t.steps[1].result is None
    assert parse_trace(trace_bytes(steps=[{"tool": "x"}])).steps[0].arguments == {}
    assert parse_trace(trace_bytes()).steps == ()


@pytest.mark.parametrize(
    "steps",
    [
        "nope",
        [{"arguments": {}}],  # no tool
        [{"tool": ""}],
        [{"tool": "x" * 129}],
        [{"tool": "a", "arguments": []}],
        [{"tool": "a", "extra": 1}],
        ["a"],
        [{"tool": "a"}] * 201,
    ],
)
def test_bad_steps_are_rejected_cleanly(steps):
    with pytest.raises(TraceError):
        parse_trace(trace_bytes(steps=steps))
    assert parse_trace(trace_bytes(steps=[{"tool": "a"}] * 200))  # the limit itself is fine


# ---------------------------------------------------------------- cases from steps
def view_of(output, steps):
    return {**output, "steps": [{"tool": s["tool"], "arguments": s.get("arguments", {})} for s in steps]}


def test_case_from_steps_generates_order_and_argument_assertions():
    t = parse_trace(trace_bytes(steps=STEPS))
    case, notes = case_from_trace(t)
    got = [(a.path, a.operator.value, a.expected) for a in case.assertions]
    assert got == [
        ("message", "equals", "ok"),
        ("steps", "tool_order", ["search_orders", "send_reply"]),
        ("steps.0.arguments.order_id", "equals", "A-100"),
        ("steps.0.arguments.limit", "equals", 3),
        ("steps.1.arguments.template", "equals", "shipping_update"),
    ]
    assert notes == []
    # results are volatile: never asserted
    assert not any("result" in a.path for a in case.assertions)
    view = view_of({"message": "ok"}, STEPS)
    assert all(evaluate_assertion(view, a).passed for a in case.assertions)


def test_secrets_in_arguments_are_skipped_not_copied():
    steps = [{"tool": "call_api", "arguments": {"api_key": "sk-live-abcdef1234567890", "q": "x", "t": f"v {TOKEN}"}}]
    case, notes = case_from_trace(parse_trace(trace_bytes(steps=steps)))
    paths = [a.path for a in case.assertions]
    assert "steps.0.arguments.q" in paths and "steps.0.arguments.api_key" not in paths
    assert "steps.0.arguments.t" not in paths
    assert sum("skipped" in n for n in notes) == 2
    assert TOKEN not in json.dumps(case.model_dump()) + " ".join(notes)


def test_explicit_paths_can_target_steps_but_not_results():
    t = parse_trace(trace_bytes(steps=STEPS))
    case, _ = case_from_trace(t, paths=["steps.1.arguments.template", "message"])
    assert [a.path for a in case.assertions] == ["steps.1.arguments.template", "message"]
    with pytest.raises(TraceError, match="not found"):
        case_from_trace(t, paths=["steps.0.result"])


def test_output_that_already_has_a_steps_field_conflicts():
    with pytest.raises(TraceError, match="steps"):
        case_from_trace(parse_trace(trace_bytes(output={"steps": 1}, steps=STEPS)))
    # without trace.steps the output's own "steps" field is just a normal field
    case, _ = case_from_trace(parse_trace(trace_bytes(output={"steps": 1})))
    assert [a.path for a in case.assertions] == ["steps"]


def test_too_many_generated_assertions_still_asks_for_paths():
    steps = [{"tool": "t", "arguments": {f"k{i}": i for i in range(60)}} for _ in range(2)]
    with pytest.raises(TraceError, match="--path"):
        case_from_trace(parse_trace(trace_bytes(steps=steps)))


# ---------------------------------------------------------------- storage
def test_steps_survive_the_trace_store_and_are_redacted(tmp_path):
    steps = [{"tool": "call_api", "arguments": {"api_key": "sk-live-abcdef1234567890"}, "result": f"got {TOKEN}"}]
    path = tmp_path / "t.db"
    with Repository(path) as repo:
        _, redacted = repo.save_trace(parse_trace(trace_bytes(steps=steps)))
        stored = repo.get_trace("t1")
    assert redacted is True and stored.steps[0].tool == "call_api"
    raw = path.read_bytes()
    assert b"sk-live-abcdef1234567890" not in raw and TOKEN.encode() not in raw


# ---------------------------------------------------------------- demo runners + CLI
def test_support_action_calls_tools_and_legacy_skips_the_lookup():
    request = {"action": "support", "order_id": "A-100"}
    good = RUNNERS["local"]().run(request)
    bad = RUNNERS["local-legacy"]().run(request)
    assert [s["tool"] for s in good["steps"]] == ["lookup_order", "send_reply"]
    assert [s["tool"] for s in bad["steps"]] == ["send_reply"]  # the regression
    assert good["message"] == bad["message"]  # same words, different behaviour


def cli(tmp_path, *argv):
    return main(["--db", str(tmp_path / "t.db"), *argv])


def test_headline_loop_a_skipped_tool_call_is_caught(tmp_path, capsys):
    request = {"action": "support", "order_id": "A-100"}
    good = RUNNERS["local"]().run(request)
    steps = good.pop("steps")
    f = tmp_path / "tr.json"
    f.write_text(json.dumps({"trace_id": "support-1", "input": request, "output": good, "steps": steps}))
    assert cli(tmp_path, "trace", "add", str(f)) == 0
    assert cli(tmp_path, "case", "from-trace", "support-1", "--stored", "--save") == 0
    assert cli(tmp_path, "run", "support-1", "--label", "good", "--runner", "local") == 0
    assert cli(tmp_path, "run", "support-1", "--label", "bad", "--runner", "local-legacy") == 1
    capsys.readouterr()
    assert cli(tmp_path, "compare", "1", "2") == 1
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "regressed"
    by_path = {d["path"]: d["change"] for d in out["diffs"]}
    assert by_path["steps"] == "regressed"  # the skipped lookup is caught...
    assert by_path["message"] == "still_passing"  # ...even though the words are identical
