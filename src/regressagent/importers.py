"""Convert recorded conversations from common agent APIs into trace v1 (D-019).

Pure data conversion: nothing in the input is executed, and every unexpected shape raises
``TraceError`` (exit code 2 in the CLI). Supported formats:

- ``openai``: Chat Completions style ``{"messages": [...]}`` (or a bare list) with assistant
  ``tool_calls`` and ``role: "tool"`` results.
- ``anthropic``: Messages style ``{"system": ..., "messages": [...]}`` with ``tool_use`` and
  ``tool_result`` content blocks.

Mapping: ``input`` = the messages before the first assistant turn (plus the system prompt for
anthropic); ``output`` = ``{"message": <text of the final assistant message>}``; ``steps`` = every
tool call in order, with the matching tool result as text (``None`` when absent).
"""
from __future__ import annotations

import json
from typing import Any

from .trace import MAX_STEPS, TraceError

FORMATS = ("openai", "anthropic")
MAX_MESSAGES = 1000


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name}")


def _messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_MESSAGES:
        raise TraceError(f"'messages' must be a list of 1-{MAX_MESSAGES} messages")
    if not all(isinstance(m, dict) for m in value):
        raise TraceError("every message must be a JSON object")
    return value


def _text(content: Any, where: str) -> str:
    """Plain text of a message/result: a string, or a list of ``{"type": "text", "text": ...}`` parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                raise TraceError(f"{where}: content parts must be JSON objects")
            if part.get("type") == "text":
                text = part.get("text")
                if not isinstance(text, str):
                    raise TraceError(f"{where}: a text part has no string 'text'")
                parts.append(text)
        return "".join(parts)
    raise TraceError(f"{where}: content must be a string or a list of parts")


def _arguments(raw: Any, where: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise TraceError(f"{where}: arguments must be a JSON string or object")
    try:
        value = json.loads(raw or "{}", parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise TraceError(f"{where}: arguments are not valid JSON") from None
    if not isinstance(value, dict):
        raise TraceError(f"{where}: arguments must be a JSON object")
    return value


def _tool_name(value: Any, where: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise TraceError(f"{where}: the tool needs a name of 1-128 characters")
    return value


def _first_assistant(messages: list[dict[str, Any]]) -> int:
    for index, message in enumerate(messages):
        if message.get("role") == "assistant":
            return index
    raise TraceError("the conversation has no assistant message")


def _openai(data: Any) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    messages = _messages(data.get("messages") if isinstance(data, dict) else data)
    first = _first_assistant(messages)
    results = {
        m["tool_call_id"]: _text(m.get("content"), "tool result")
        for m in messages
        if m.get("role") == "tool" and isinstance(m.get("tool_call_id"), str)
    }
    steps: list[dict[str, Any]] = []
    for position, message in enumerate(messages):
        if message.get("role") != "assistant" or message.get("tool_calls") is None:
            continue
        calls = message["tool_calls"]
        if not isinstance(calls, list):
            raise TraceError(f"message {position}: tool_calls must be a list")
        for call in calls:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict):
                raise TraceError(f"message {position}: a tool call has no 'function' object")
            where = f"message {position} tool call"
            steps.append(
                {
                    "tool": _tool_name(function.get("name"), where),
                    "arguments": _arguments(function.get("arguments"), where),
                    "result": results.get(call.get("id")),
                }
            )
    last = messages[-1]
    if last.get("role") != "assistant" or last.get("tool_calls"):
        raise TraceError("the conversation does not end with a final assistant answer")
    return (
        {"messages": messages[:first]},
        {"message": _text(last.get("content"), "final message")},
        steps,
    )


def _blocks(message: dict[str, Any], position: int) -> list[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list) or not all(isinstance(b, dict) for b in content):
        raise TraceError(f"message {position}: content must be a string or a list of blocks")
    return content


def _anthropic(data: Any) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(data, dict):
        raise TraceError("an anthropic conversation must be a JSON object")
    messages = _messages(data.get("messages"))
    first = _first_assistant(messages)
    results: dict[str, str] = {}
    for position, message in enumerate(messages):
        if message.get("role") == "user" and not isinstance(message.get("content"), str):
            for block in _blocks(message, position):
                if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                    results[block["tool_use_id"]] = _text(block.get("content"), "tool result")
    steps: list[dict[str, Any]] = []
    for position, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        for block in _blocks(message, position):
            if block.get("type") == "tool_use":
                where = f"message {position} tool_use"
                steps.append(
                    {
                        "tool": _tool_name(block.get("name"), where),
                        "arguments": _arguments(block.get("input", {}), where),
                        "result": results.get(block.get("id")),
                    }
                )
    last = messages[-1]
    last_blocks = _blocks(last, len(messages) - 1) if last.get("role") == "assistant" else []
    if last.get("role") != "assistant" or any(b.get("type") == "tool_use" for b in last_blocks):
        raise TraceError("the conversation does not end with a final assistant answer")
    input_data: dict[str, Any] = {"messages": messages[:first]}
    if data.get("system") is not None:
        input_data["system"] = _text(data["system"], "system prompt")
    return input_data, {"message": _text(last_blocks, "final message")}, steps


def convert(fmt: str, data: Any, *, trace_id: str) -> dict[str, Any]:
    """Return a trace v1 dict for ``data`` in the given conversation format."""
    if fmt == "openai":
        input_data, output, steps = _openai(data)
    elif fmt == "anthropic":
        input_data, output, steps = _anthropic(data)
    else:
        raise TraceError(f"unknown format '{fmt[:30]}' (known: {', '.join(FORMATS)})")
    if len(steps) > MAX_STEPS:
        raise TraceError(f"the conversation has {len(steps)} tool calls; at most {MAX_STEPS} steps are allowed")
    trace: dict[str, Any] = {"trace_id": trace_id, "input": input_data, "output": output}
    if steps:
        trace["steps"] = steps
    return trace
