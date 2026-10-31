"""Local SQLite persistence for cases and runs (stdlib only, offline, no accounts).

Design notes:
- All SQL is parameterized; stored content is data and is never executed.
- Schema changes are explicit, ordered migrations tracked via ``PRAGMA user_version``.
  A database newer than this code is refused rather than silently misread.
- Each run records the SHA-256 of the exact case JSON it ran against, so a result can
  always be tied to the precise case definition (case edits do not rewrite history).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, ValidationError

from .models import RegressionCase, RegressionResult, RunMetadata
from .redaction import Redactor
from .trace import Trace

MAX_JSON_BYTES = 1_000_000  # per stored case or result
MAX_LABEL_LENGTH = 64

# Ordered migrations. Index i upgrades schema version i -> i+1. Never edit shipped entries;
# append new ones.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE cases (
        case_id     TEXT PRIMARY KEY,
        case_json   TEXT NOT NULL,
        case_hash   TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );
    CREATE TABLE runs (
        run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id     TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
        case_hash   TEXT NOT NULL,
        label       TEXT NOT NULL,
        runner_name TEXT NOT NULL,
        passed      INTEGER NOT NULL CHECK (passed IN (0, 1)),
        result_json TEXT NOT NULL,
        created_at  TEXT NOT NULL
    );
    CREATE INDEX idx_runs_case ON runs(case_id, run_id);
    """,
    # v2: adapter run metadata (ADAPTER_ISOLATION.md section 5). All columns are nullable:
    # runs recorded before v2, and runs by built-in demo runners, have no adapter metadata.
    """
    ALTER TABLE runs ADD COLUMN adapter_name TEXT;
    ALTER TABLE runs ADD COLUMN adapter_tier TEXT;
    ALTER TABLE runs ADD COLUMN executable_path TEXT;
    ALTER TABLE runs ADD COLUMN executable_sha256 TEXT;
    ALTER TABLE runs ADD COLUMN argv_json TEXT;
    ALTER TABLE runs ADD COLUMN env_names_json TEXT;
    ALTER TABLE runs ADD COLUMN timeout_seconds REAL;
    ALTER TABLE runs ADD COLUMN limits_json TEXT;
    ALTER TABLE runs ADD COLUMN exit_code INTEGER;
    ALTER TABLE runs ADD COLUMN duration_ms INTEGER;
    ALTER TABLE runs ADD COLUMN stdout_truncated INTEGER CHECK (stdout_truncated IN (0, 1));
    ALTER TABLE runs ADD COLUMN stderr_truncated INTEGER CHECK (stderr_truncated IN (0, 1));
    """,
    # v3: raw traces. Stored REDACTED (secrets never reach the disk); `redacted` records whether
    # redaction changed anything. Traces are independent of cases and runs.
    """
    CREATE TABLE traces (
        trace_id   TEXT PRIMARY KEY,
        trace_json TEXT NOT NULL,
        trace_hash TEXT NOT NULL,
        redacted   INTEGER NOT NULL CHECK (redacted IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
]
SCHEMA_VERSION = len(MIGRATIONS)


class StorageError(Exception):
    """Base class for predictable persistence errors."""


class CaseExistsError(StorageError):
    pass


class TraceExistsError(StorageError):
    pass


class NotFoundError(StorageError):
    pass


class SchemaTooNewError(StorageError):
    pass


class PayloadTooLargeError(StorageError):
    pass


class StoredRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: int
    case_id: str
    case_hash: str
    label: str
    runner_name: str
    created_at: str
    result: RegressionResult
    metadata: RunMetadata | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _canonical_json(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def case_hash(case: RegressionCase) -> str:
    return hashlib.sha256(_canonical_json(case).encode("utf-8")).hexdigest()


def _check_size(text: str, what: str) -> None:
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        raise PayloadTooLargeError(f"{what} exceeds {MAX_JSON_BYTES} bytes")


def _metadata_to_columns(meta: RunMetadata | None) -> tuple:
    if meta is None:
        return (None,) * 12

    def flag(value: bool | None) -> int | None:
        return None if value is None else int(value)

    return (
        meta.adapter_name, meta.adapter_tier, meta.executable_path, meta.executable_sha256,
        json.dumps(meta.argv, ensure_ascii=False), json.dumps(meta.env_names),
        meta.timeout_seconds, json.dumps(meta.limits, sort_keys=True), meta.exit_code,
        meta.duration_ms, flag(meta.stdout_truncated), flag(meta.stderr_truncated),
    )


def _columns_to_metadata(run_id: int, cols: tuple) -> RunMetadata | None:
    """Rebuild metadata from a row. The database file is untrusted: validate, never assume."""
    (name, tier, path, sha, argv, env, timeout, limits, exit_code, duration, out_trunc,
     err_trunc) = cols
    if name is None:
        return None

    def flag(value: int | None) -> bool | None:
        return None if value is None else bool(value)

    try:
        return RunMetadata(
            adapter_name=name,
            adapter_tier=tier,
            executable_path=path,
            executable_sha256=sha,
            argv=json.loads(argv) if argv is not None else [],
            env_names=json.loads(env) if env is not None else [],
            timeout_seconds=timeout,
            limits=json.loads(limits) if limits is not None else {},
            exit_code=exit_code,
            duration_ms=duration,
            stdout_truncated=flag(out_trunc),
            stderr_truncated=flag(err_trunc),
        )
    except (ValidationError, ValueError, TypeError, RecursionError) as exc:
        raise StorageError(f"Stored adapter metadata for run {run_id} is invalid") from exc


class Repository:
    def __init__(self, path: str | Path = ":memory:", *, redactor: Redactor | None = None) -> None:
        # ``None`` means: build a Redactor from the live environment at each record_run, so
        # secrets exported after the Repository was opened are still caught.
        self._redactor = redactor
        try:
            self._conn = sqlite3.connect(str(path))
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._migrate()
        except sqlite3.Error as exc:
            conn = getattr(self, "_conn", None)
            if conn is not None:
                conn.close()
            raise StorageError(f"Cannot open database '{path}': {exc}") from exc

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def _migrate(self) -> None:
        version = self.schema_version
        if version < 0:
            raise StorageError(f"Database has an invalid schema version ({version})")
        if version > SCHEMA_VERSION:
            raise SchemaTooNewError(
                f"Database schema v{version} is newer than supported v{SCHEMA_VERSION}"
            )
        for target in range(version, SCHEMA_VERSION):
            # The version bump is part of the same transaction as the DDL, so a failure (or a
            # crash) can never leave a half-migrated file whose version number lies.
            # ``target`` is an int we control; PRAGMA values cannot be parameterized.
            script = f"BEGIN;{MIGRATIONS[target]}PRAGMA user_version = {target + 1};COMMIT;"
            try:
                self._conn.executescript(script)
            except sqlite3.Error:
                self._conn.rollback()
                raise

    # ---- cases -------------------------------------------------------------------
    def save_case(self, case: RegressionCase, *, overwrite: bool = False) -> str:
        """Store a case; returns its content hash. Refuses to replace unless overwrite."""
        payload = _canonical_json(case)
        _check_size(payload, "case")
        digest = case_hash(case)
        now = _now()
        exists = self._conn.execute(
            "SELECT 1 FROM cases WHERE case_id = ?", (case.case_id,)
        ).fetchone()
        if exists and not overwrite:
            raise CaseExistsError(f"Case '{case.case_id}' already exists")
        if exists:
            self._conn.execute(
                "UPDATE cases SET case_json=?, case_hash=?, updated_at=? WHERE case_id=?",
                (payload, digest, now, case.case_id),
            )
        else:
            self._conn.execute(
                "INSERT INTO cases(case_id, case_json, case_hash, created_at, updated_at)"
                " VALUES (?,?,?,?,?)",
                (case.case_id, payload, digest, now, now),
            )
        self._conn.commit()
        return digest

    def get_case(self, case_id: str) -> RegressionCase:
        row = self._conn.execute(
            "SELECT case_json FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Case '{case_id}' not found")
        return RegressionCase.model_validate_json(row[0])

    def list_case_ids(self) -> list[str]:
        rows = self._conn.execute("SELECT case_id FROM cases ORDER BY case_id").fetchall()
        return [r[0] for r in rows]

    def delete_case(self, case_id: str) -> None:
        cur = self._conn.execute("DELETE FROM cases WHERE case_id = ?", (case_id,))
        self._conn.commit()
        if cur.rowcount == 0:
            raise NotFoundError(f"Case '{case_id}' not found")

    # ---- traces ------------------------------------------------------------------
    def save_trace(self, trace: Trace, *, overwrite: bool = False) -> tuple[str, bool]:
        """Store a trace after redaction. Returns ``(content hash, whether anything was redacted)``."""
        original = trace.model_dump(mode="json")
        safe = Redactor().value(original)
        redacted = safe != original
        try:
            stored = Trace.model_validate(safe)
        except ValidationError:
            raise StorageError("trace cannot be stored: redaction changed its shape") from None
        payload = json.dumps(
            stored.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        _check_size(payload, "trace")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        now = _now()
        exists = self._conn.execute(
            "SELECT 1 FROM traces WHERE trace_id = ?", (trace.trace_id,)
        ).fetchone()
        if exists and not overwrite:
            raise TraceExistsError(f"Trace '{trace.trace_id}' already exists")
        if exists:
            self._conn.execute(
                "UPDATE traces SET trace_json=?, trace_hash=?, redacted=?, updated_at=?"
                " WHERE trace_id=?",
                (payload, digest, int(redacted), now, trace.trace_id),
            )
        else:
            self._conn.execute(
                "INSERT INTO traces(trace_id, trace_json, trace_hash, redacted, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?)",
                (trace.trace_id, payload, digest, int(redacted), now, now),
            )
        self._conn.commit()
        return digest, redacted

    def get_trace(self, trace_id: str) -> Trace:
        row = self._conn.execute(
            "SELECT trace_json FROM traces WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Trace '{trace_id}' not found")
        try:
            return Trace.model_validate_json(row[0])
        except ValidationError:
            raise StorageError(f"Stored trace '{trace_id}' is corrupt") from None

    def list_trace_ids(self) -> list[str]:
        rows = self._conn.execute("SELECT trace_id FROM traces ORDER BY trace_id").fetchall()
        return [r[0] for r in rows]

    def delete_trace(self, trace_id: str) -> None:
        cur = self._conn.execute("DELETE FROM traces WHERE trace_id = ?", (trace_id,))
        self._conn.commit()
        if cur.rowcount == 0:
            raise NotFoundError(f"Trace '{trace_id}' not found")

    # ---- runs --------------------------------------------------------------------
    def record_run(
        self,
        case: RegressionCase,
        result: RegressionResult,
        *,
        label: str,
        runner_name: str,
        metadata: RunMetadata | None = None,
    ) -> int:
        """Persist a run of ``case``. The case must already be saved.

        ``metadata`` (adapter facts, see ``RunMetadata``) is optional; demo runners have none.
        """
        if not label or len(label) > MAX_LABEL_LENGTH:
            raise StorageError(f"label must be 1-{MAX_LABEL_LENGTH} characters")
        if result.case_id != case.case_id:
            raise StorageError("result.case_id does not match case.case_id")
        # Single chokepoint: nothing reaches the database without best-effort redaction
        # (see redaction.py for what it does and does not catch; THREAT_MODEL #8).
        redactor = self._redactor or Redactor.from_environ()
        result = redactor.result(result)
        if metadata is not None:
            metadata = metadata.model_copy(
                update={
                    "argv": redactor.argv(metadata.argv),
                    "executable_path": (
                        redactor.text(metadata.executable_path)
                        if metadata.executable_path is not None
                        else None
                    ),
                }
            )
        payload = _canonical_json(result)
        _check_size(payload, "result")
        if not self._conn.execute(
            "SELECT 1 FROM cases WHERE case_id = ?", (case.case_id,)
        ).fetchone():
            raise NotFoundError(f"Case '{case.case_id}' must be saved before recording runs")
        cur = self._conn.execute(
            "INSERT INTO runs(case_id, case_hash, label, runner_name, passed, result_json,"
            " created_at, adapter_name, adapter_tier, executable_path, executable_sha256,"
            " argv_json, env_names_json, timeout_seconds, limits_json, exit_code, duration_ms,"
            " stdout_truncated, stderr_truncated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                case.case_id, case_hash(case), label, runner_name, int(result.passed), payload,
                _now(), *_metadata_to_columns(metadata),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def _row_to_run(row: tuple) -> StoredRun:
        metadata = _columns_to_metadata(row[0], row[7:])
        return StoredRun(
            run_id=row[0],
            case_id=row[1],
            case_hash=row[2],
            label=row[3],
            runner_name=row[4],
            created_at=row[5],
            result=RegressionResult.model_validate_json(row[6]),
            metadata=metadata,
        )

    _RUN_COLS = (
        "run_id, case_id, case_hash, label, runner_name, created_at, result_json,"
        " adapter_name, adapter_tier, executable_path, executable_sha256, argv_json,"
        " env_names_json, timeout_seconds, limits_json, exit_code, duration_ms,"
        " stdout_truncated, stderr_truncated"
    )

    def get_run(self, run_id: int) -> StoredRun:
        row = self._conn.execute(
            f"SELECT {self._RUN_COLS} FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Run {run_id} not found")
        return self._row_to_run(row)

    def list_runs(self, case_id: str) -> list[StoredRun]:
        rows = self._conn.execute(
            f"SELECT {self._RUN_COLS} FROM runs WHERE case_id = ? ORDER BY run_id", (case_id,)
        ).fetchall()
        return [self._row_to_run(r) for r in rows]
