"""Importers: OpenAI chat / Anthropic messages conversations -> trace v1. Pure data conversion
(no execution); every malformed shape is a TraceError, never a crash."""
import json

import pytest

from regressagent.cli import main
from regressagent.importers import convert
from regressagent.trace import TraceError, parse_trace

OPENAI = {
    "messages": [
        {"role": "system", "content": "You are a support agent."},
        {"role": "user", "content": "Where is order A-100?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "lookup_order", "arguments": '{"order_id": "A-100"}'}},
                {"id": "c2", "type": "function",
                 "function": {"name": "check_stock", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"status": "shipped"}'},
        {"role": "tool", "tool_call_id": "c2", "content": "in stock"},
        {"role": "assistant", "content": "It shipped yesterday."},
    ]
}

ANTHROPIC = {
    "system": "You are a support agent.",
    "messages": [
        {"role": "user", "content": "Where is order A-100?"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "t1", "name": "lookup_order", "input": {"order_id": "A-100"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "shipped"}]},
        ]},
        {"role": "assistant", "content": [{"type": "text", "text": "It shipped "}, {"type": "text", "text": "yesterday."}]},
    ],
}  # fmt: skip


def trace_of(fmt, data, **kw):
    return parse_trace(json.dumps(convert(fmt, data, **kw)).encode())


# ---------------------------------------------------------------- OpenAI
def test_openai_conversation_becomes_a_trace():
    t = trace_of("openai", OPENAI, trace_id="oa-1")
    assert t.trace_id == "oa-1"
    assert t.output == {"message": "It shipped yesterday."}
    assert t.input["messages"][0]["role"] == "system" and t.input["messages"][-1]["role"] == "user"
    assert len(t.input["messages"]) == 2  # everything before the first assistant turn
    assert [s.tool for s in t.steps] == ["lookup_order", "check_stock"]
    assert t.steps[0].arguments == {"order_id": "A-100"} and t.steps[0].result == '{"status": "shipped"}'
    assert t.steps[1].arguments == {} and t.steps[1].result == "in stock"


def test_openai_accepts_a_bare_list_and_content_parts():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "he"}, {"type": "text", "text": "llo"}]},
    ]
    t = trace_of("openai", messages, trace_id="x")
    assert t.output == {"message": "hello"} and t.steps == ()


def test_openai_missing_tool_result_is_none():
    data = {"messages": [OPENAI["messages"][1], OPENAI["messages"][2], OPENAI["messages"][5]]}
    t = trace_of("openai", data, trace_id="x")
    assert [s.result for s in t.steps] == [None, None]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(messages="nope"),
        lambda d: d.update(messages=[]),
        lambda d: d.update(messages=[{"role": "user", "content": "hi"}]),  # no assistant turn
        lambda d: d["messages"].pop(),  # ends with tool results, no final answer
        lambda d: d["messages"][2]["tool_calls"][0]["function"].update(arguments="not json"),
        lambda d: d["messages"][2]["tool_calls"][0]["function"].update(arguments="[1]"),
        lambda d: d["messages"][2]["tool_calls"][0]["function"].update(arguments='{"n": NaN}'),
        lambda d: d["messages"][2]["tool_calls"][0]["function"].update(name=""),
        lambda d: d["messages"][2]["tool_calls"][0].pop("function"),
        lambda d: d["messages"].append("not a dict"),
        lambda d: d["messages"][2].update(tool_calls="nope"),
        lambda d: d["messages"][-1].update(content=7),
    ],
)
def test_openai_malformed_conversations_are_clean_errors(mutate):
    data = json.loads(json.dumps(OPENAI))
    mutate(data)
    with pytest.raises(TraceError):
        convert("openai", data, trace_id="x")


def test_conversation_ending_with_an_unanswered_tool_call_is_an_error():
    data = json.loads(json.dumps(OPENAI))
    data["messages"] = data["messages"][:3]  # ends with an assistant message that only calls tools
    with pytest.raises(TraceError, match="final assistant answer"):
        convert("openai", data, trace_id="x")
    anthropic = json.loads(json.dumps(ANTHROPIC))
    anthropic["messages"] = anthropic["messages"][:2]  # ends with a tool_use block
    with pytest.raises(TraceError, match="final assistant answer"):
        convert("anthropic", anthropic, trace_id="x")


def test_too_many_tool_calls_is_an_error():
    calls = [{"id": f"c{i}", "type": "function", "function": {"name": "t", "arguments": "{}"}} for i in range(201)]
    data = {"messages": [{"role": "user", "content": "go"}, {"role": "assistant", "content": None, "tool_calls": calls},
                         {"role": "assistant", "content": "done"}]}  # fmt: skip
    with pytest.raises(TraceError, match="steps"):
        convert("openai", data, trace_id="x")  # the importer itself enforces the limit


# ---------------------------------------------------------------- Anthropic
def test_anthropic_conversation_becomes_a_trace():
    t = trace_of("anthropic", ANTHROPIC, trace_id="an-1")
    assert t.output == {"message": "It shipped yesterday."}
    assert t.input["system"] == "You are a support agent."
    assert [m["role"] for m in t.input["messages"]] == ["user"]
    assert [s.tool for s in t.steps] == ["lookup_order"]
    assert t.steps[0].arguments == {"order_id": "A-100"} and t.steps[0].result == "shipped"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(messages={}),
        lambda d: d.update(messages=[{"role": "user", "content": "hi"}]),
        lambda d: d["messages"].pop(),
        lambda d: d["messages"][1]["content"][1].update(input="not an object"),
        lambda d: d["messages"][1]["content"][1].update(name=""),
        lambda d: d["messages"][1]["content"].append(5),
        lambda d: d["messages"][3].update(content=7),
    ],
)
def test_anthropic_malformed_conversations_are_clean_errors(mutate):
    data = json.loads(json.dumps(ANTHROPIC))
    mutate(data)
    with pytest.raises(TraceError):
        convert("anthropic", data, trace_id="x")


def test_unknown_format_and_non_object_input():
    with pytest.raises(TraceError, match="format"):
        convert("langchain", OPENAI, trace_id="x")
    with pytest.raises(TraceError):
        convert("anthropic", 5, trace_id="x")


# ---------------------------------------------------------------- CLI
def cli(tmp_path, *argv):
    return main(["--db", str(tmp_path / "t.db"), *argv])


def write(tmp_path, data, name="conv.json"):
    f = tmp_path / name
    f.write_text(json.dumps(data))
    return str(f)


def test_cli_convert_prints_a_valid_trace_with_a_stable_default_id(tmp_path, capsys):
    f = write(tmp_path, OPENAI)
    assert cli(tmp_path, "trace", "convert", f, "--format", "openai") == 0
    first = capsys.readouterr().out
    t = parse_trace(first.encode())
    assert t.trace_id.startswith("openai-") and [s.tool for s in t.steps] == ["lookup_order", "check_stock"]
    cli(tmp_path, "trace", "convert", f, "--format", "openai")
    assert capsys.readouterr().out == first  # deterministic
    cli(tmp_path, "trace", "list")
    assert json.loads(capsys.readouterr().out) == []  # printing does not store


def test_cli_convert_add_stores_redacted_and_feeds_case_generation(tmp_path, capsys):
    secret = "sk-live-abcdef1234567890"
    data = json.loads(json.dumps(ANTHROPIC))
    data["messages"][2]["content"][0]["content"] = f"api_key={secret} shipped"
    f = write(tmp_path, data)
    assert cli(tmp_path, "trace", "convert", f, "--format", "anthropic", "--trace-id", "an-1", "--add") == 0
    assert json.loads(capsys.readouterr().out)["redacted"] is True
    assert secret.encode() not in (tmp_path / "t.db").read_bytes()
    assert cli(tmp_path, "case", "from-trace", "an-1", "--stored") == 0
    case = json.loads(capsys.readouterr().out)
    assert any(a["operator"] == "tool_order" and a["expected"] == ["lookup_order"] for a in case["assertions"])


@pytest.mark.parametrize("content", ["garbage", "[1, 2", '{"messages": "x"}'])
def test_cli_convert_bad_files_exit_2(tmp_path, capsys, content):
    f = tmp_path / "bad.json"
    f.write_text(content)
    assert cli(tmp_path, "trace", "convert", str(f), "--format", "openai") == 2
    assert capsys.readouterr().err.startswith("error:")


def test_cli_convert_requires_a_known_format(tmp_path):
    f = write(tmp_path, OPENAI)
    with pytest.raises(SystemExit) as exc:
        cli(tmp_path, "trace", "convert", f, "--format", "langchain")
    assert exc.value.code == 2
