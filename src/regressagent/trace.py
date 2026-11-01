"""Turn a captured agent trace into a regression case.

A trace is untrusted DATA (D-004): it is parsed as strict JSON with size and depth limits and is
never executed. Values that look like secrets (per ``redaction``) are never copied into a case.
The generated assertions are a starting point that a human must review (they pin *every*
selected value exactly, including ones that may legitimately vary).
"""
from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .assertions import _resolve_path, _strict_equal
from .models import MAX_ASSERTIONS, AssertionOperator, AssertionSpec, RegressionCase
from .redaction import Redactor

MAX_TRACE_BYTES = 1_000_000
MAX_TRACE_DEPTH = 32
MAX_EXPECTED_STRING = 1_000  # longer strings are skipped by default (not assertable by equality)
MAX_PATH_LENGTH = 512  # longer paths are skipped by default
MAX_EXPECTED_JSON = 10_000  # cap for an explicitly selected composite value


class TraceError(ValueError):
    """A predictable, user-facing problem with a trace (CLI maps it to exit code 2)."""


MAX_STEPS = 200
MAX_ORDER_NAMES = 50


class Step(BaseModel):
    """One tool call made by the agent. ``result`` is kept for humans but never asserted on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None


class Trace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str = Field(min_length=1, max_length=128)
    input: dict[str, Any]
    output: dict[str, Any]
    steps: tuple[Step, ...] = Field(default=(), max_length=MAX_STEPS)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name}")


def _too_deep(value: Any) -> bool:
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_TRACE_DEPTH:
            return True
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
    return False


def parse_trace(raw: bytes) -> Trace:
    """Parse and validate trace bytes. Error messages never echo values from the trace."""
    if len(raw) > MAX_TRACE_BYTES:
        raise TraceError(f"trace exceeds {MAX_TRACE_BYTES} bytes")
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except UnicodeDecodeError:
        raise TraceError("trace is not valid UTF-8") from None
    except RecursionError:
        raise TraceError(f"trace is nested too deeply (limit {MAX_TRACE_DEPTH})") from None
    except ValueError as exc:  # JSONDecodeError and our non-finite number rejection
        raise TraceError(f"trace is not valid JSON: {type(exc).__name__}") from None
    if not isinstance(data, dict):
        raise TraceError("trace must be a JSON object")
    if _too_deep(data):
        raise TraceError(f"trace is nested too deeply (limit {MAX_TRACE_DEPTH})")
    try:
        return Trace.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
        )
        raise TraceError(f"invalid trace: {problems}") from None


def _leaves(value: Any, path: str = "") -> Iterator[tuple[str, Any, str | None]]:
    """Yield (path, value, problem). ``problem`` is set when the path cannot be addressed."""
    if isinstance(value, dict) and value:
        for key, item in value.items():
            child = f"{path}.{key}" if path else key
            if not key or "." in key:
                yield child or "(empty key)", item, "key is empty or contains '.'"
            else:
                yield from _leaves(item, child)
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            yield from _leaves(item, f"{path}.{index}" if path else str(index))
    else:  # scalar, None, or an empty container
        yield path, value, None


def _assertable_view(trace: Trace) -> dict[str, Any]:
    """What assertions can address: the output, plus ``steps`` (tool + arguments, no results)."""
    if not trace.steps:
        return trace.output
    if "steps" in trace.output:
        raise TraceError(
            "the trace output already has a 'steps' field, which conflicts with trace steps; "
            "rename one of them"
        )
    steps = [{"tool": s.tool, "arguments": s.arguments} for s in trace.steps]
    return {**trace.output, "steps": steps}


def _is_secret_like(redacted_root: Any, root: Any, path: str, value: Any) -> bool:
    found, redacted = _resolve_path(redacted_root, path)
    return not found or not _strict_equal(redacted, value)


def case_from_trace(
    trace: Trace,
    *,
    case_id: str | None = None,
    name: str | None = None,
    paths: Sequence[str] | None = None,
) -> tuple[RegressionCase, list[str]]:
    """Build a case from ``trace``. Returns ``(case, notes)``; notes list what was skipped."""
    redactor = Redactor()
    if redactor.value(trace.input) != trace.input:
        raise TraceError(
            "trace input contains secret-looking values; remove or replace them in the trace "
            "before creating a case"
        )
    view = _assertable_view(trace)
    redacted_view = redactor.value(view)
    notes: list[str] = []
    specs: list[AssertionSpec] = []

    if paths:
        unique = list(dict.fromkeys(paths))  # de-duplicate, keep order
        if len(unique) > MAX_ASSERTIONS:
            raise TraceError(f"at most {MAX_ASSERTIONS} paths are allowed per case")
        for path in unique:
            found, value = _resolve_path(view, path) if path else (False, None)
            if not found:
                raise TraceError(f"path '{path}' not found in the trace output")
            if (
                isinstance(value, str) and "[REDACTED" in value
            ) or _is_secret_like(redacted_view, view, path, value):
                raise TraceError(
                    f"path '{path}' looks like a secret (or contains one); refusing to store it"
                )
            if len(json.dumps(value)) > MAX_EXPECTED_JSON:
                raise TraceError(f"value at '{path}' is too large to assert on")
            specs.append(AssertionSpec(path=path, operator=AssertionOperator.EQUALS, expected=value))
    else:
        def consider(path: str, value: Any, problem: str | None) -> None:
            if len(path) > MAX_PATH_LENGTH:
                notes.append(f"skipped '{path[:40]}...': path longer than {MAX_PATH_LENGTH} chars")
            elif problem:
                notes.append(f"skipped '{path}': {problem}")
            elif isinstance(value, str) and "[REDACTED" in value:
                notes.append(f"skipped '{path}': value was redacted when the trace was stored")
            elif _is_secret_like(redacted_view, view, path, value):
                notes.append(f"skipped '{path}': looks like a secret (redaction would change it)")
            elif isinstance(value, str) and len(value) > MAX_EXPECTED_STRING:
                notes.append(f"skipped '{path}': string longer than {MAX_EXPECTED_STRING} chars")
            else:
                specs.append(
                    AssertionSpec(path=path, operator=AssertionOperator.EQUALS, expected=value)
                )

        for path, value, problem in _leaves(trace.output):
            consider(path, value, problem)
        if trace.steps:
            names = [step.tool for step in trace.steps]
            if len(names) > MAX_ORDER_NAMES:
                notes.append(f"tool_order covers only the first {MAX_ORDER_NAMES} of {len(names)} steps")
            specs.append(
                AssertionSpec(
                    path="steps",
                    operator=AssertionOperator.TOOL_ORDER,
                    expected=names[:MAX_ORDER_NAMES],
                )
            )
            for index, step in enumerate(trace.steps):
                for path, value, problem in _leaves(step.arguments, f"steps.{index}.arguments"):
                    consider(path, value, problem)
        if not specs:
            raise TraceError("no assertable fields found in the trace output")
        if len(specs) > MAX_ASSERTIONS:
            raise TraceError(
                f"{len(specs)} assertable fields found, at most {MAX_ASSERTIONS} are allowed; "
                "choose some with --path (repeatable)"
            )

    try:
        case = RegressionCase(
            case_id=case_id or trace.trace_id,
            name=name or f"Trace {trace.trace_id}",
            input=trace.input,
            assertions=specs,
        )
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
        )
        raise TraceError(f"cannot build a valid case: {problems}") from None
    return case, notes
