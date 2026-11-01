"""Safe, explicit assertions over structured output values."""
from __future__ import annotations

import re
from typing import Any

from .models import AssertionOperator, AssertionResult, AssertionSpec
from .regex_safety import MAX_TEXT_LENGTH

_INDEX_RE = re.compile(r"[0-9]+")


def _strict_equal(a: Any, b: Any) -> bool:
    """Equality that never conflates bool with int (True != 1), recursively.

    int and float still compare numerically (1 == 1.0).
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_strict_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_strict_equal(x, y) for x, y in zip(a, b))
    return a == b


def _resolve_path(value: Any, path: str) -> tuple[bool, Any]:
    current = value
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and _INDEX_RE.fullmatch(segment):
            index = int(segment)
            if index >= len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def _has_type(value: Any, name: str) -> bool:
    """JSON type check. Strict: bool is never a number; only ints are integers."""
    if name == "string":
        return isinstance(value, str)
    if name == "number":
        return _is_number(value)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if name == "object":
        return isinstance(value, dict)
    return name == "array" and isinstance(value, list)


def _tool_names(actual: list[Any]) -> tuple[list[str], bool]:
    """Names of well-formed steps (``{"tool": "<name>", ...}``) and whether any step was malformed."""
    names: list[str] = []
    malformed = False
    for item in actual:
        tool = item.get("tool") if isinstance(item, dict) else None
        if isinstance(tool, str) and tool:
            names.append(tool)
        else:
            malformed = True
    return names, malformed


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def evaluate_assertion(output: dict[str, Any], spec: AssertionSpec) -> AssertionResult:
    exists, actual = _resolve_path(output, spec.path)

    if spec.operator == AssertionOperator.EXISTS:
        passed = exists
        message = "Path exists" if passed else "Path does not exist"
    elif not exists:
        passed = False
        message = f"Path '{spec.path}' does not exist"
    elif spec.operator == AssertionOperator.EQUALS:
        passed = _strict_equal(actual, spec.expected)
        message = "Values are equal" if passed else "Values are not equal"
    elif spec.operator == AssertionOperator.NOT_EQUALS:
        passed = not _strict_equal(actual, spec.expected)
        message = "Values differ" if passed else "Values unexpectedly match"
    elif spec.operator == AssertionOperator.CONTAINS:
        try:
            passed = spec.expected in actual
        except (TypeError, AttributeError):
            passed = False
        message = "Expected value is contained" if passed else "Expected value is not contained"
    elif spec.operator in (AssertionOperator.GT, AssertionOperator.LT):
        if not _is_number(actual):
            passed = False
            message = "Actual value is not a number"
        elif spec.operator == AssertionOperator.GT:
            passed = actual > spec.expected
            message = "Value is greater" if passed else "Value is not greater"
        else:
            passed = actual < spec.expected
            message = "Value is less" if passed else "Value is not less"
    elif spec.operator in (
        AssertionOperator.TOOL_CALLED,
        AssertionOperator.TOOL_NOT_CALLED,
        AssertionOperator.TOOL_ORDER,
    ):
        if not isinstance(actual, list):
            passed = False
            message = "Actual value is not a list of tool calls"
        else:
            names, malformed = _tool_names(actual)
            if spec.operator == AssertionOperator.TOOL_CALLED:
                passed = spec.expected in names
                message = "Tool was called" if passed else "Tool was not called"
            elif spec.operator == AssertionOperator.TOOL_NOT_CALLED:
                if malformed:  # cannot prove absence when some steps cannot be read
                    passed = False
                    message = "Tool calls contain malformed steps; cannot prove the tool was not called"
                else:
                    passed = spec.expected not in names
                    message = "Tool was not called" if passed else "Tool was called"
            else:
                remaining = iter(names)
                passed = all(any(n == want for n in remaining) for want in spec.expected)
                message = "Tools were called in order" if passed else "Tools were not called in this order"
    elif spec.operator == AssertionOperator.MATCHES_TYPE:
        names = spec.expected if isinstance(spec.expected, list) else [spec.expected]
        passed = any(_has_type(actual, n) for n in names)
        message = "Type matches" if passed else f"Value type does not match {'|'.join(names)}"
    elif spec.operator == AssertionOperator.REGEX:
        if not isinstance(actual, str):
            passed = False
            message = "Actual value is not a string"
        elif len(actual) > MAX_TEXT_LENGTH:
            passed = False
            message = f"Actual text is too long for regex matching (> {MAX_TEXT_LENGTH} chars)"
        else:
            # Pattern was validated when the AssertionSpec was constructed (regex_safety).
            passed = re.search(spec.expected, actual) is not None
            message = "Pattern matches" if passed else "Pattern does not match"
    else:
        passed = False
        message = f"Unsupported operator: {spec.operator}"

    return AssertionResult(
        path=spec.path,
        operator=spec.operator,
        passed=passed,
        expected=spec.expected,
        actual=actual if exists else None,
        message=message,
    )
