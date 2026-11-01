# Changelog

> **Naming note (D-016):** the project was renamed from AgentProof to RegressAgent on 2026-10-24. Entries below that mention `agentproof`, `AGENTPROOF_DB` or `agentproof.toml` record what was actually run under the old name and are kept as written.

User-visible changes are recorded here. This project has not had a software release yet.

## Unreleased
- Added: Tier A subprocess adapter (`agentproof.subprocess_adapter`), NOT a sandbox and not yet reachable from the CLI: timeouts with process-group kill, output caps, empty environment plus allow-list, temp cwd, rlimits, bad output becomes a failed result. Tested against a hostile agent.
- Added: `agentproof.toml` loader (`agentproof.config`, parse and validate only; nothing executes yet). Strict keys, argv lists with absolute executables, mandatory `acknowledge_not_sandboxed = true`, hard limit maxima, env allow-list of names, symlink/permission/size checks. Not wired into the CLI yet.
- Added: best-effort secret redaction of everything stored in run history (outputs, errors, assertion values, adapter metadata); the CLI now prints the stored (redacted) result. Not a guarantee, see docs/THREAT_MODEL.md #8.
- Added: DB schema v2 (nullable run-metadata columns for future adapters), atomic migrations.
- Added AGENTS.md with operating rules, security guardrails, memory-file requirements, and truthful completion reporting.
- Added initial project planning and architecture memory documents.
- Added the first Python core: typed models, deterministic offline runner, assertion evaluation, regression engine, and demo CLI.
- Added pytest coverage, packaging metadata, and a GitHub Actions test workflow. These have not yet been executed/verified in this session.
- Fixed: path segments such as "²" crashed assertion evaluation; `equals` no longer treats `True` as `1`; runner exceptions are recorded as failed results.
- Added: SQLite persistence (cases, runs, content hashes, versioned migrations), before/after comparison with `improved/regressed/unchanged` verdicts, `local-legacy` demo runner.
- Added CLI: `case add|list|show`, `run`, `runs`, `compare` (exit 1 on regression). Corrupt DB files give a clear error (exit 2).
- Added ruff to dev dependencies and CI.
- Added `agentproof report <before_run_id> <after_run_id> [--format markdown|json]`: deterministic, timestamp-free report from a comparison; untrusted values are length-limited and escaped. Exits 1 on regression.
- Added `agentproof case delete <id> --yes` (deletes the case and its runs; refuses without `--yes`) and `agentproof run-all --label L [--runner R]` (runs and records every saved case; exit 1 if any fail, exit 2 if there are no cases).
- Added `agentproof report --output PATH [--force]` (refuses to overwrite an existing file without `--force`) and `agentproof run --repeat N` (1-20; records each run, reports `stable` when every repeat had identical per-assertion outcomes; exit 1 if any repeat failed). Default `run` output is unchanged.
- Added assertion operators `gt` / `lt` (finite numbers only; bool/str actuals fail) and `regex` (`re.search`; restricted patterns, see D-009: max 200 chars, no backreferences, no nested/ambiguous repeats, text capped at 5,000 chars). Unsafe patterns are rejected when a case is loaded. Cases are limited to 100 assertions.
- Added `run-all --repeat N` (per-case `stable`, top-level `unstable` list; default output unchanged). A runner result over 1,000,000 bytes is now recorded as a failed run (previously the run was lost with exit 2).
- Added docs/THREAT_MODEL.md and docs/ADAPTER_ISOLATION.md (design proposal D-010; no adapter code exists).
- Documentation: README now documents the case format, all operators and limits, every CLI command with exit codes. Added `examples/` (three sample cases and `run_example.sh`, a runnable offline walk-through); tests keep the examples valid.
- Added `scripts/fuzz_regex_safety.py` (manual ReDoS fuzz/timing tool, not part of CI).
- Added LICENSE (MIT, D-011) and CONTRIBUTING.md. D-010 (adapter isolation) accepted with the owner-delegated answers; still design only, no adapter code exists. Docs only: no code or test changes; tests were not run in this session (pip blocked).
- Database schema v2: `runs` gains nullable adapter-metadata columns (adapter name/tier, executable path + SHA-256, argv, env variable *names*, timeout/limits, exit code, duration, stdout/stderr truncation flags). Existing v1 databases are upgraded in place on first open; old runs read back with no metadata. Migrations are now atomic (the version bump is part of the transaction) and a negative `user_version` is refused. No adapter exists yet; nothing writes this metadata outside tests.
- CLI: `run` and `run-all` accept `--adapter NAME --config FILE` (Tier A subprocess adapter). Config path is explicit (never discovered), `--adapter` cannot be combined with `--runner`, runs are recorded as `adapter:NAME` with `RunMetadata`; `ConfigError` maps to exit 2. `--runner` now defaults to `None` and resolves to `local`.
- CI: workflow emits pytest failures as annotations and uses `fail-fast: false`; test helper no longer picks a group/world-writable interpreter as the hostile agent (fixed the red CI).
- Packaging: pyproject now declares `license = "MIT"` (SPDX; build needs setuptools>=77), license-files, classifiers (Alpha, Python 3.11/3.12, POSIX Linux), keywords and project URLs; `twine check` passes. README warns not to `pip install agentproof` (name belongs to another project, D-013). Added tests/test_packaging.py.
- Added `case from-trace` and `agentproof.trace` (trace v1 format, strict JSON, redaction-aware, bounded; prints or `--save`s a case). Added `examples/traces/greet-ava.json`. `case from-trace --save` on an existing id now says to use `--overwrite`.
- Added assertion operator `matches_type` (JSON type check: string/number/integer/boolean/null/object/array, or a list of alternatives; strict booleans/integers; unknown names rejected at load).
- Added the HTTP adapter (`[adapters.NAME] type = "http"`, `agentproof.http_adapter`, D-015): POSTs the case input as JSON; loopback by default, other hosts need `allow_hosts` + https; SSRF guard with a pinned connection, no redirects, TLS verification, response-size cap and one total deadline enforced by a watchdog, credentials from env vars. Runs are recorded with `adapter_tier = "H"`. Added `examples/agentproof.example.toml`.
- **BREAKING (never released): renamed AgentProof -> RegressAgent** (D-016): package/command `regressagent`, env var `REGRESSAGENT_DB`, default DB `regressagent.db`, example config `examples/regressagent.example.toml`. Added tests/test_naming.py to keep the old name out of code. The GitHub repository itself must be renamed by the owner.
- Added docs/RELEASE_NOTES_0.1.0.md (DRAFT) and a test that keeps it honest (mentions that Tier A is not a sandbox, that nothing is published).
- Schema v3: `traces` table; `trace add/list/show/delete` commands; `case from-trace ID --stored`. Traces are redacted before they are written. Added `examples/agents/greeter_agent.py` and `greeter_http.py` plus end-to-end tests with them.
- Multi-step traces (D-018): optional `steps` (tool calls) in traces; operators `tool_called`, `tool_not_called` (fails closed on malformed steps), `tool_order` (subsequence); `case from-trace` adds a `tool_order` assertion and argument assertions (results are never asserted); demo runner `support` action (legacy runner skips the lookup); `examples/cases/support.json` and `examples/traces/support-order.json`.
