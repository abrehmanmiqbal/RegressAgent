"""Command-line entry point for RegressAgent.

Imported case files are untrusted data: they are size-capped, parsed as JSON only, and
validated through typed models. Nothing in a case file is ever executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .comparison import Verdict, compare_runs
from .config import ConfigError, HttpAdapterConfig, load_config
from .http_adapter import HttpAdapter
from .importers import FORMATS, convert
from .models import AssertionOperator, AssertionSpec, RegressionCase, RegressionResult
from .regression import RegressionEngine
from .report import render_json, render_markdown
from .runner import RUNNERS, DeterministicLocalRunner
from .storage import (
    MAX_JSON_BYTES,
    CaseExistsError,
    PayloadTooLargeError,
    Repository,
    StorageError,
    TraceExistsError,
)
from .subprocess_adapter import SubprocessAdapter
from .trace import MAX_TRACE_BYTES, TraceError, case_from_trace, parse_trace

DEFAULT_DB = "regressagent.db"
MAX_REPEAT = 20


class CliError(Exception):
    pass


def _emit(data: object) -> None:
    print(json.dumps(data, indent=2, sort_keys=False))


def load_case_file(path: str) -> RegressionCase:
    file = Path(path)
    try:
        with file.open("rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
    except OSError as exc:
        raise CliError(f"Cannot read case file: {exc.strerror or exc}") from exc
    if len(raw) > MAX_JSON_BYTES:
        raise CliError(f"Case file exceeds {MAX_JSON_BYTES} bytes")
    try:
        return RegressionCase.model_validate_json(raw)
    except ValidationError as exc:
        raise CliError(f"Invalid case file: {exc.error_count()} validation error(s): {exc}") from exc


def _read_trace_file(path: str):
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(MAX_TRACE_BYTES + 1)
    except OSError as exc:
        raise CliError(f"Cannot read trace file: {exc.strerror or exc}") from exc
    try:
        return parse_trace(raw)
    except TraceError as exc:
        raise CliError(str(exc)) from exc


def _case_from_trace_file(args: argparse.Namespace, repo: Repository | None = None):
    trace = repo.get_trace(args.file) if args.stored and repo else _read_trace_file(args.file)
    try:
        return case_from_trace(trace, case_id=args.case_id, name=args.name, paths=args.paths)
    except TraceError as exc:
        raise CliError(str(exc)) from exc


def _converted_trace(args: argparse.Namespace):
    try:
        with Path(args.file).open("rb") as handle:
            raw = handle.read(MAX_TRACE_BYTES + 1)
    except OSError as exc:
        raise CliError(f"Cannot read conversation file: {exc.strerror or exc}") from exc
    if len(raw) > MAX_TRACE_BYTES:
        raise CliError(f"conversation file exceeds {MAX_TRACE_BYTES} bytes")
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise CliError("conversation file is not valid JSON") from None
    trace_id = args.trace_id or f"{args.format}-{hashlib.sha256(raw).hexdigest()[:12]}"
    try:
        converted = convert(args.format, data, trace_id=trace_id)
        return parse_trace(json.dumps(converted).encode("utf-8"))
    except TraceError as exc:
        raise CliError(str(exc)) from exc


def _reject_constant(name: str):
    raise ValueError(f"non-finite number {name}")


def _trace_command(args: argparse.Namespace, repo: Repository) -> None:
    if args.trace_command == "convert":  # --add
        trace = _converted_trace(args)
        try:
            digest, redacted = repo.save_trace(trace, overwrite=args.overwrite)
        except TraceExistsError as exc:
            raise CliError(f"{exc}; use --overwrite to replace it") from exc
        _emit(
            {"saved": True, "trace_id": trace.trace_id, "trace_hash": digest, "redacted": redacted}
        )
    elif args.trace_command == "add":
        trace = _read_trace_file(args.file)
        try:
            digest, redacted = repo.save_trace(trace, overwrite=args.overwrite)
        except TraceExistsError as exc:
            raise CliError(f"{exc}; use --overwrite to replace it") from exc
        _emit(
            {"saved": True, "trace_id": trace.trace_id, "trace_hash": digest, "redacted": redacted}
        )
    elif args.trace_command == "list":
        _emit(repo.list_trace_ids())
    elif args.trace_command == "show":
        _emit(repo.get_trace(args.trace_id).model_dump(mode="json"))
    else:  # delete
        repo.get_trace(args.trace_id)  # clean "not found" error first
        if not args.yes:
            raise CliError(f"Refusing to delete trace '{args.trace_id}' without --yes")
        repo.delete_trace(args.trace_id)
        _emit({"deleted": args.trace_id})


def _print_notes(notes: list[str]) -> None:
    for note in notes:
        print(f"note: {note}", file=sys.stderr)


def _demo() -> int:
    case = RegressionCase(
        case_id="demo-greeting",
        name="Deterministic greeting",
        input={"action": "greet", "name": "RegressAgent"},
        assertions=[
            AssertionSpec(
                path="message", operator=AssertionOperator.EQUALS, expected="Hello, RegressAgent!"
            ),
            AssertionSpec(path="action", operator=AssertionOperator.EQUALS, expected="greet"),
        ],
    )
    result = RegressionEngine(DeterministicLocalRunner()).run_case(case)
    _emit(result.model_dump(mode="json"))
    return 0 if result.passed else 1


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer 0-65535") from None
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("must be an integer 0-65535")
    return port


def _serve(args: argparse.Namespace) -> int:
    from .api import make_server  # lazy: the API module imports this one

    with Repository(args.db):  # create/migrate the database now and fail early if it is unusable
        pass
    try:
        server, token = make_server(args.db, args.host, args.port)
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    except OSError as exc:
        raise CliError(f"cannot start the server: {exc.strerror or exc}") from exc
    host, port = server.server_address[:2]
    shown = f"[{host}]" if ":" in str(host) else host
    print(f"RegressAgent API listening on http://{shown}:{port}/", file=sys.stderr)
    print(f"Token (shown once, keep it private): {token}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    finally:
        server.server_close()
    return 0


def _repeat_count(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be an integer 1-{MAX_REPEAT}") from None
    if not 1 <= value <= MAX_REPEAT:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_REPEAT}")
    return value


def _outcome_signature(result) -> tuple:
    """What counts as 'the same result' across repeats: pass/fail per assertion, not raw output."""
    return (
        result.passed,
        result.error is not None,
        tuple((a.path, a.operator.value, a.passed) for a in result.assertions),
    )


def _resolve_runner(args: argparse.Namespace) -> tuple[str, Callable[[], Any]]:
    """Pick the runner for ``run``/``run-all``: a demo runner, or a configured adapter.

    Adapters need an explicit ``--config`` file (never discovered from the working directory, so
    a cloned repository cannot supply commands, D-012 #8) and cannot be mixed with ``--runner``.
    """
    if args.adapter is None:
        if args.config is not None:
            raise CliError("--config is only used together with --adapter")
        name = args.runner or "local"
        return name, RUNNERS[name]
    if args.runner is not None:
        raise CliError("--adapter and --runner cannot be used together")
    if args.config is None:
        raise CliError("--adapter requires --config FILE (regressagent.toml is never auto-discovered)")
    config = load_config(args.config)
    adapter = config.adapters.get(args.adapter)
    if adapter is None:
        known = ", ".join(sorted(config.adapters)) or "none"
        raise CliError(f"unknown adapter '{args.adapter}' in {args.config} (known: {known})")
    if isinstance(adapter, HttpAdapterConfig):
        return f"adapter:{args.adapter}", lambda: HttpAdapter(adapter)
    return f"adapter:{args.adapter}", lambda: SubprocessAdapter(adapter)


def _add_runner_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--runner",
        choices=sorted(RUNNERS),
        default=None,
        help="Built-in demo runner (default: local). Not combinable with --adapter.",
    )
    parser.add_argument(
        "--adapter",
        help="Run the agent through an adapter from --config. A subprocess (Tier A) adapter runs "
        "the configured command as YOU, without a sandbox: trusted code only. An http adapter "
        "sends the case input to the configured endpoint.",
    )
    parser.add_argument("--config", help="regressagent.toml with [adapters.NAME] entries (explicit path).")


def _run_repeated(
    repo: Repository,
    case: RegressionCase,
    runner: tuple[str, Callable[[], Any]],
    label: str,
    repeat: int,
):
    """Run ``case`` ``repeat`` times, recording every run. Returns (results, run_ids)."""
    runner_name, factory = runner
    results, run_ids = [], []
    for _ in range(repeat):
        instance = factory()
        result = RegressionEngine(instance).run_case(case)
        metadata = getattr(instance, "last_metadata", None)  # adapters only
        try:
            run_ids.append(
                repo.record_run(
                    case, result, label=label, runner_name=runner_name, metadata=metadata
                )
            )
        except PayloadTooLargeError:
            # Runner output is untrusted: an oversized result is a failed run, not a lost one.
            result = RegressionResult(
                case_id=case.case_id,
                passed=False,
                error=f"Result exceeds {MAX_JSON_BYTES} bytes; output was not stored",
            )
            run_ids.append(
                repo.record_run(
                    case, result, label=label, runner_name=runner_name, metadata=metadata
                )
            )
        # Use what was actually stored (redacted), so nothing printed differs from the record.
        results.append(repo.get_run(run_ids[-1]).result)
    return results, run_ids


def _repeat_summary(results, run_ids) -> dict:
    passed = sum(1 for r in results if r.passed)
    return {
        "run_ids": run_ids,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "stable": len({_outcome_signature(r) for r in results}) == 1,
    }


def _write_report(path: str, text: str, *, force: bool) -> None:
    try:
        with open(path, "w" if force else "x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError:
        raise CliError(f"'{path}' already exists; use --force to overwrite") from None
    except OSError as exc:
        raise CliError(f"Cannot write report: {exc.strerror or exc}") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="regressagent", description="Offline-first AI agent regression testing.")
    parser.add_argument("--db", default=os.environ.get("REGRESSAGENT_DB", DEFAULT_DB), help="SQLite file")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="Run the built-in offline demo case.")

    case = sub.add_parser("case", help="Manage saved regression cases.").add_subparsers(
        dest="case_command", required=True
    )
    add = case.add_parser("add", help="Import a case from a JSON file.")
    add.add_argument("file")
    add.add_argument("--overwrite", action="store_true")
    ft = case.add_parser(
        "from-trace",
        help="Turn a captured trace (JSON) into a regression case; prints it unless --save.",
    )
    ft.add_argument("file")
    ft.add_argument("--case-id", help="Default: the trace_id.")
    ft.add_argument("--name")
    ft.add_argument(
        "--path",
        action="append",
        dest="paths",
        help="Output path to assert on (repeatable). Default: every scalar field.",
    )
    ft.add_argument(
        "--stored",
        action="store_true",
        help="FILE is the id of a trace stored with 'trace add' instead of a file path.",
    )
    ft.add_argument("--save", action="store_true", help="Save the case instead of printing it.")
    ft.add_argument("--overwrite", action="store_true", help="With --save: replace an existing case.")
    case.add_parser("list", help="List case ids.")
    show = case.add_parser("show", help="Show a saved case.")
    show.add_argument("case_id")
    delete = case.add_parser("delete", help="Delete a case and all of its recorded runs.")
    delete.add_argument("case_id")
    delete.add_argument("--yes", action="store_true", help="Confirm the irreversible deletion.")

    srv = sub.add_parser(
        "serve", help="Start the local HTTP API (loopback only, one-time bearer token)."
    )
    srv.add_argument("--host", default="127.0.0.1", help="Loopback address only (default 127.0.0.1).")
    srv.add_argument("--port", type=_port, default=8765, help="0 picks a free port (default 8765).")
    trace_cmd = sub.add_parser("trace", help="Store and inspect raw traces.").add_subparsers(
        dest="trace_command", required=True
    )
    ta = trace_cmd.add_parser("add", help="Validate, redact and store a trace file.")
    ta.add_argument("file")
    ta.add_argument("--overwrite", action="store_true", help="Replace an existing trace id.")
    tc = trace_cmd.add_parser(
        "convert", help="Convert an OpenAI/Anthropic conversation file into a trace (prints it)."
    )
    tc.add_argument("file")
    tc.add_argument("--format", required=True, choices=FORMATS)
    tc.add_argument("--trace-id", help="Default: <format>-<first 12 hex of the file's SHA-256>.")
    tc.add_argument("--add", action="store_true", help="Store the converted trace (redacted).")
    tc.add_argument("--overwrite", action="store_true", help="With --add: replace an existing id.")
    trace_cmd.add_parser("list", help="List stored trace ids.")
    ts = trace_cmd.add_parser("show", help="Print a stored (redacted) trace.")
    ts.add_argument("trace_id")
    td = trace_cmd.add_parser("delete", help="Delete a stored trace.")
    td.add_argument("trace_id")
    td.add_argument("--yes", action="store_true", help="Confirm the deletion.")
    run = sub.add_parser("run", help="Run a saved case and record the result.")
    run.add_argument("case_id")
    run.add_argument("--label", required=True)
    _add_runner_args(run)
    run.add_argument(
        "--repeat",
        type=_repeat_count,
        default=1,
        help=f"Run the case N times (1-{MAX_REPEAT}) and report whether outcomes were stable.",
    )

    run_all = sub.add_parser(
        "run-all", help="Run every saved case and record each result (exit 1 if any fail)."
    )
    run_all.add_argument("--label", required=True)
    _add_runner_args(run_all)
    run_all.add_argument(
        "--repeat",
        type=_repeat_count,
        default=1,
        help=f"Run each case N times (1-{MAX_REPEAT}) and report stability per case.",
    )

    runs = sub.add_parser("runs", help="List recorded runs for a case.")
    runs.add_argument("case_id")

    cmp = sub.add_parser("compare", help="Compare two recorded runs (exit 1 if regressed).")
    cmp.add_argument("before_run_id", type=int)
    cmp.add_argument("after_run_id", type=int)

    rep = sub.add_parser(
        "report", help="Render a deterministic report comparing two runs (exit 1 if regressed)."
    )
    rep.add_argument("before_run_id", type=int)
    rep.add_argument("after_run_id", type=int)
    rep.add_argument("--format", choices=["markdown", "json"], default="markdown")
    rep.add_argument("--output", help="Write the report to this file instead of stdout.")
    rep.add_argument("--force", action="store_true", help="Overwrite --output if it exists.")
    return parser


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "demo":
        return _demo()
    if args.command == "serve":
        return _serve(args)
    if args.command == "trace" and args.trace_command == "convert" and not args.add:
        if args.overwrite:
            raise CliError("--overwrite is only used together with --add")
        _emit(_converted_trace(args).model_dump(mode="json"))
        return 0
    if (
        args.command == "case"
        and args.case_command == "from-trace"
        and not args.save
        and not args.stored
    ):
        if args.overwrite:
            raise CliError("--overwrite is only used together with --save")
        case, notes = _case_from_trace_file(args)
        _print_notes(notes)
        _emit(case.model_dump(mode="json"))
        return 0

    with Repository(args.db) as repo:
        if args.command == "trace":
            _trace_command(args, repo)
            return 0
        if args.command == "case":
            if args.case_command == "add":
                digest = repo.save_case(load_case_file(args.file), overwrite=args.overwrite)
                _emit({"saved": True, "case_hash": digest})
            elif args.case_command == "from-trace" and not args.save:  # --stored
                if args.overwrite:
                    raise CliError("--overwrite is only used together with --save")
                case, notes = _case_from_trace_file(args, repo)
                _print_notes(notes)
                _emit(case.model_dump(mode="json"))
            elif args.case_command == "from-trace":  # --save
                case, notes = _case_from_trace_file(args, repo)
                try:
                    digest = repo.save_case(case, overwrite=args.overwrite)
                except CaseExistsError as exc:
                    raise CliError(f"{exc}; use --overwrite to replace it") from exc
                _print_notes(notes)
                _emit({"saved": True, "case_id": case.case_id, "case_hash": digest})
            elif args.case_command == "list":
                _emit(repo.list_case_ids())
            elif args.case_command == "delete":
                repo.get_case(args.case_id)  # clean "not found" error before anything else
                run_count = len(repo.list_runs(args.case_id))
                if not args.yes:
                    raise CliError(
                        f"Refusing to delete '{args.case_id}' and its {run_count} recorded run(s) "
                        "without --yes"
                    )
                repo.delete_case(args.case_id)
                _emit({"deleted": args.case_id, "runs_deleted": run_count})
            else:
                _emit(repo.get_case(args.case_id).model_dump(mode="json"))
            return 0

        if args.command == "run":
            runner = _resolve_runner(args)
            case = repo.get_case(args.case_id)
            results, run_ids = _run_repeated(repo, case, runner, args.label, args.repeat)
            all_passed = all(r.passed for r in results)
            if args.repeat == 1:
                _emit({"run_id": run_ids[0], "passed": results[0].passed, "error": results[0].error})
            else:
                _emit(_repeat_summary(results, run_ids))
            return 0 if all_passed else 1

        if args.command == "run-all":
            runner = _resolve_runner(args)
            case_ids = repo.list_case_ids()
            if not case_ids:
                raise CliError("No cases saved; add one with 'case add' first")
            entries, unstable, ok_cases = [], [], 0
            for case_id in case_ids:
                case = repo.get_case(case_id)
                results, run_ids = _run_repeated(repo, case, runner, args.label, args.repeat)
                case_ok = all(r.passed for r in results)
                ok_cases += case_ok
                if args.repeat == 1:
                    entries.append(
                        {
                            "case_id": case_id,
                            "run_id": run_ids[0],
                            "passed": results[0].passed,
                            "error": results[0].error,
                        }
                    )
                else:
                    summary = _repeat_summary(results, run_ids)
                    if not summary["stable"]:
                        unstable.append(case_id)
                    entries.append({"case_id": case_id, **summary})
            output = {
                "total": len(entries),
                "passed": ok_cases,
                "failed": len(entries) - ok_cases,
                "results": entries,
            }
            if args.repeat > 1:
                output["unstable"] = unstable
            _emit(output)
            return 0 if ok_cases == len(entries) else 1

        if args.command == "runs":
            _emit(
                [
                    {
                        "run_id": r.run_id,
                        "label": r.label,
                        "runner": r.runner_name,
                        "passed": r.result.passed,
                        "created_at": r.created_at,
                    }
                    for r in repo.list_runs(args.case_id)
                ]
            )
            return 0

        if args.command == "compare":
            comparison = compare_runs(repo.get_run(args.before_run_id), repo.get_run(args.after_run_id))
            _emit(comparison.model_dump(mode="json"))
            return 1 if comparison.verdict == Verdict.REGRESSED else 0

        if args.command == "report":
            comparison = compare_runs(repo.get_run(args.before_run_id), repo.get_run(args.after_run_id))
            render = render_json if args.format == "json" else render_markdown
            text = render(comparison)
            if args.output:
                _write_report(args.output, text, force=args.force)
                print(f"wrote {args.output}")
            else:
                print(text, end="")
            return 1 if comparison.verdict == Verdict.REGRESSED else 0

    return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except (StorageError, CliError, ConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
