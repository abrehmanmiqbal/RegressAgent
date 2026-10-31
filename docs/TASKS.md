# Tasks and Milestones
_[x] = done AND verified by an executed check (see TEST_EVIDENCE.md). Local-only = not yet on GitHub._

## Milestone 0 — Baseline and memory
- [x] Inspect repository; add AGENTS.md and memory docs.
- [x] Record initial repository/tooling facts (Python 3.12.3, pydantic 2.14.1, pytest 8.4.2, ruff 0.17.1).

## Milestone 1 — Deterministic core
- [x] Typed models, runner interface, deterministic runner, assertions, unit tests, demo CLI, CI workflow file.
- [x] Execute tests and record exact evidence (5/5 initially; 69/69 now).
- [x] Fix real defects found: unicode-digit path crash; bool==int false pass; runner exceptions crashing engine.
- [x] CI passes on GitHub for 3.11 and 3.12 (observed via API on 161104d, session 4; see TEST_EVIDENCE.md).
- [x] Python 3.11: verified by CI only (ruff + pytest + demo succeeded on 161104d); not run locally.

## Milestone 2 — Persistence and regression workflow
- [x] SQLite persistence + explicit versioned migrations (stdlib sqlite3; see D-005).
- [x] Save cases and runs (with case hash and result JSON).
- [x] Rerun + assertion-based before/after comparison.
- [x] Tests: persistence across connections, hash stability, history immutability, size limits, injection-like ids, corrupt/newer DB.
- [x] Trace model + `case from-trace` (trace file -> reviewable case; session 8). [ ] Persist raw traces in the DB (needs schema v3) and trace capture/import from real frameworks.
- [x] Reports: `report` command renders a deterministic, timestamp-free Markdown/JSON report from two runs to stdout (golden-file + injection tests).
- [x] `report --output PATH [--force]` writes the report to a file (refuses to overwrite without `--force`).
- [x] Migration test with a real v1 -> v2 upgrade (session 7: data preserved, newer/negative/foreign/corrupt/failed-migration cases; see TEST_EVIDENCE.md).
- [x] `run --repeat N` (1-20): records N runs, reports pass/fail counts and `stable` (same per-assertion outcomes every time).
- [x] `run-all --repeat N` (per-case summary + `unstable` list).
- [x] Oversized runner output (> 1,000,000 bytes) is recorded as a failed run instead of aborting (session 4).

## Milestone 1b — Assertion operators
- [x] `gt`, `lt` (numeric), `regex` (restricted, length-limited; D-009) with 61 tests incl. CLI integration, mutation checks and fuzzing.
- [x] `matches_type` operator (JSON types; strict). [ ] Full JSON-schema validation (would need a dependency or a bounded validator; not started).

## Milestone 3 — API and CLI
- [x] CLI: case add/list/show, run, runs, compare (+ exit codes) with tests.
- [x] CLI: `report <before> <after> [--format markdown|json]` (exit 1 if regressed).
- [x] CLI: `case delete <id> --yes` (refuses without --yes; reports runs deleted) and `run-all --label L [--runner R]` (exit 1 if any fail, exit 2 if no cases).
- [ ] Validated HTTP API (local-only bind by default) + integration tests.
- [x] README documents every CLI command, case-file format, operators, limits and exit codes (snippets executed; see TEST_EVIDENCE.md).
- [ ] Docs for the HTTP API (API not built).
- [x] HTTP adapter (`type = "http"`, session 8, D-015): SSRF guard, pinned connection, no redirects, TLS verified, size/deadline caps, env-var credentials.

## Milestone 4 — Usable interface
- [ ] Minimal local UI (cases, runs, diffs, reports); empty/error/loading states.

## Milestone 5 — Security and robustness
- [x] Case files: size cap (1,000,000 bytes), JSON-only parse, typed validation, `extra=forbid`; hostile/malformed/deep-nesting/invalid-UTF-8 probes rejected cleanly.
- [x] Regex safety (ReDoS): validation at case load, text cap, cost model, fuzzed (D-009). Assertion count capped at 100.
- [x] Best-effort secret redaction of stored results and run metadata (src/regressagent/redaction.py; limits documented, NOT a guarantee). [ ] Limit assertion depth/path length.
- [x] Tier A subprocess adapter + hostile test agent (reachable from the CLI since session 8: `run`/`run-all --adapter NAME --config FILE`). [ ] Tier B (bubblewrap), HTTP adapter.
- [x] Isolation design written: docs/ADAPTER_ISOLATION.md + D-010 (status PROPOSED; design only, nothing implemented/tested). [x] D-010 accepted 2026-10-18 (owner delegated answers); adapter code may now be written test-first, Linux only.
- [x] Threat model doc: docs/THREAT_MODEL.md (honest status per threat; secrets redaction is best-effort only).

## Milestone 6 — Release preparation
- [x] Ruff added to dev extras and CI (verified locally only).
- [x] LICENSE (MIT, D-011), CONTRIBUTING.md (session 6, docs only). ~~examples/ dir, README CLI docs~~ (done, session 4).
- [x] Clean-install verification on 3.11 and 3.12 (fresh clone, editable and wheel installs; session 8, TEST_EVIDENCE #30-33).

## Milestone 7 — Distribution readiness
- [x] Name conflict (old name `agentproof` taken on PyPI) resolved: renamed to RegressAgent, D-016 (PyPI and GitHub names free on 2026-10-24). [ ] Owner: rename the GitHub repository and then update URLs (see D-016). [x] Release notes DRAFT: docs/RELEASE_NOTES_0.1.0.md (not published; tag/publish needs owner authorization).
- [x] Name conflict resolved: renamed to RegressAgent (D-016). [ ] Owner: rename the GitHub repo, then update URLs (see D-016).
