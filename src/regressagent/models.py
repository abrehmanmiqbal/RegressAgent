"""Typed models used by RegressAgent's regression-testing core."""
from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .regex_safety import UnsafeRegexError, validate_pattern

MAX_ASSERTIONS = 100
MAX_TOOL_NAME = 128
MAX_PATH_LENGTH = 512
MAX_PATH_SEGMENTS = 32
MAX_TOOL_ORDER = 50
TYPE_NAMES = ("string", "number", "integer", "boolean", "null", "object", "array")


def _is_tool_name(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= MAX_TOOL_NAME


class AssertionOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    EXISTS = "exists"
    GT = "gt"
    LT = "lt"
    REGEX = "regex"
    MATCHES_TYPE = "matches_type"
    TOOL_CALLED = "tool_called"
    TOOL_NOT_CALLED = "tool_not_called"
    TOOL_ORDER = "tool_order"


class AssertionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(
        min_length=1,
        max_length=MAX_PATH_LENGTH,
        description="Dot-separated path into the structured output.",
    )
    operator: AssertionOperator
    expected: Any = None

    @model_validator(mode="after")
    def _validate_expected_for_operator(self) -> AssertionSpec:
        segments = self.path.split(".")
        if len(segments) > MAX_PATH_SEGMENTS or not all(segments):
            raise ValueError(
                f"path must have 1-{MAX_PATH_SEGMENTS} non-empty dot-separated segments"
            )
        if self.operator in (AssertionOperator.GT, AssertionOperator.LT):
            value = self.expected
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"'{self.operator.value}' requires a numeric 'expected'")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("'expected' must be a finite number")
        elif self.operator in (AssertionOperator.TOOL_CALLED, AssertionOperator.TOOL_NOT_CALLED):
            if not _is_tool_name(self.expected):
                raise ValueError(
                    f"'{self.operator.value}' requires a tool name (1-{MAX_TOOL_NAME} chars)"
                )
        elif self.operator == AssertionOperator.TOOL_ORDER:
            names = self.expected
            if (
                not isinstance(names, list)
                or not 1 <= len(names) <= MAX_TOOL_ORDER
                or not all(_is_tool_name(n) for n in names)
            ):
                raise ValueError(
                    f"'tool_order' requires a list of 1-{MAX_TOOL_ORDER} tool names"
                )
        elif self.operator == AssertionOperator.MATCHES_TYPE:
            names = self.expected if isinstance(self.expected, list) else [self.expected]
            if (
                not names
                or len(names) > len(TYPE_NAMES)
                or not all(isinstance(n, str) and n in TYPE_NAMES for n in names)
                or len(set(names)) != len(names)
            ):
                raise ValueError(
                    "'matches_type' requires one of "
                    + ", ".join(TYPE_NAMES)
                    + " (or a list of distinct names)"
                )
        elif self.operator == AssertionOperator.REGEX:
            try:
                validate_pattern(self.expected)
            except UnsafeRegexError as exc:
                raise ValueError(str(exc)) from exc
        return self


class RegressionCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    input: dict[str, Any]
    assertions: list[AssertionSpec] = Field(min_length=1, max_length=MAX_ASSERTIONS)


class AssertionResult(BaseModel):
    path: str
    operator: AssertionOperator
    passed: bool
    expected: Any = None
    actual: Any = None
    message: str


class RegressionResult(BaseModel):
    case_id: str
    passed: bool
    output: dict[str, Any] = Field(default_factory=dict)
    assertions: list[AssertionResult] = Field(default_factory=list)
    error: str | None = None


MAX_ARGV_ITEMS = 256
MAX_ARGV_ITEM_LENGTH = 4096
MAX_ENV_NAMES = 64
MAX_LIMIT_ENTRIES = 32
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class RunMetadata(BaseModel):
    """Reproducibility facts about how an adapter produced a run (ADAPTER_ISOLATION.md s5).

    Holds only *names* of environment variables, never values. Everything is bounded because
    it is persisted and later rendered. ``argv`` comes from the trusted local config and is
    stored as data; it is never executed from the database.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter_name: str = Field(min_length=1, max_length=64)
    adapter_tier: Literal["A", "B", "H"]
    executable_path: str | None = Field(default=None, max_length=MAX_ARGV_ITEM_LENGTH)
    executable_sha256: str | None = None
    argv: list[str] = Field(default_factory=list, max_length=MAX_ARGV_ITEMS)
    env_names: list[str] = Field(default_factory=list, max_length=MAX_ENV_NAMES)
    timeout_seconds: float | None = Field(default=None, gt=0, le=300)
    limits: dict[str, int] = Field(default_factory=dict, max_length=MAX_LIMIT_ENTRIES)
    exit_code: int | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    stdout_truncated: bool | None = None
    stderr_truncated: bool | None = None

    @property
    def response_truncated(self) -> bool | None:
        """HTTP adapters: the response hit the byte cap (stored in the ``stdout_truncated`` column)."""
        return self.stdout_truncated

    @field_validator("executable_sha256")
    @classmethod
    def _check_sha256(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value):
            raise ValueError("executable_sha256 must be 64 lowercase hex characters")
        return value

    @field_validator("argv")
    @classmethod
    def _check_argv(cls, value: list[str]) -> list[str]:
        if any(len(item) > MAX_ARGV_ITEM_LENGTH for item in value):
            raise ValueError(f"argv items must be at most {MAX_ARGV_ITEM_LENGTH} characters")
        return value

    @field_validator("env_names")
    @classmethod
    def _check_env_names(cls, value: list[str]) -> list[str]:
        for name in value:
            if not _ENV_NAME.fullmatch(name):
                raise ValueError("env_names must be plain variable names (no '=' or values)")
        return value
