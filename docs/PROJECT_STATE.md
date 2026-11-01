# Project State

_Last updated: 2026-10-18 (session 7: schema v2, secret redaction, config loader and Tier A subprocess adapter added; 356 tests on Python 3.13.16; the rest of this file still describes session 4). Everything below marked "verified" was executed in this session; see TEST_EVIDENCE.md for exact commands/output._

## Verified status
- Repository: abrehmanmiqbal/RegressAgent (renamed from AgentProof by the owner). Local clone built and tested on **Python 3.12.3 / Linux** only. Python 3.11.15 (installed via uv) and 3.12.3 are both run locally; GitHub CI (3.11 + 3.12) is also green. Renamed AgentProof -> RegressAgent in session 9 (D-016); the GitHub repo still carries the old name until the owner renames it.
- `pytest`: **718 passed** (3.12.3 and 3.11.15 locally; CI 3.11 + 3.12 green on c323adf and 059bdc6; session 9). `ruff check .`: **clean**. `regressagent demo`: exit 0. Black-box CLI loop via the installed `regressagent` binary verified (before fails -> after passes -> verdict `improved`).
- Implemented and tested: typed models; provider-neutral runner protocol + deterministic offline runners (`local`, `local-legacy`); assertion engine (equals/not_equals/contains/exists/gt/lt/matches_type/regex; regex is restricted, see D-009; dot paths, list indices, type-strict bool/int equality); regression engine (runner exceptions and non-dict output become failed results, never crashes); SQLite persistence (versioned migrations, case content hashes, size limits, parameterized SQL, cascade delete); assertion-level before/after comparison (`improved` / `regressed` / `unchanged`); CLI: `demo`, `case add|list|show`, `run`, `runs`, `compare`, `report` (exit 1 on regression), `case delete --yes`, `run-all [--repeat N]`, `report --output/--force`, `run --repeat N`; Markdown/JSON report rendering (`report.py`).
- Docs: README has full CLI/operator/limits documentation; `examples/` exists (tests keep it valid).
- Docs: THREAT_MODEL.md; ADAPTER_ISOLATION.md (D-010, proposed, design only).
- NOT implemented: HTTP API, UI, report file export / HTML reports, trace import/capture, real-agent adapters (subprocess/HTTP), JSON-schema assertions, CHANGELOG-level release docs. (LICENSE = MIT and CONTRIBUTING.md added 2026-10-18; threat-model doc exists.)

## Commits
Session 2 work was pushed to GitHub `main` in session 3 (remote head verified with `git ls-remote`: d5b6852 -> e9e4ac4). Session 3 added the `report` command on top.
Session 2 commits: 9d1146a hardening fixes, f3ae004 storage, 6eeb09b comparison, fe75aa6 CLI + corrupt-DB fix, e1827bf ruff dev dep/CI step, lint fixes, docs. Base on GitHub: d5b6852.

## Blockers
None known. Push access uses a short-lived owner-provided token (expires ~2026-10-21); if a push returns 401/403, ask the owner for a new token. Token is never stored.

## Current milestone
Milestone 1 verified. Milestone 2 core (persistence, rerun, comparison) done locally. Milestone 3 CLI partly done.

## Constraints
- Free-first, offline-capable core; no paid API/hosting/accounts for core features.
- Never claim results not executed; no secrets committed; do not execute traces; do not publish without authorization.
