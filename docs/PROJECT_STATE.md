# Project State

_Last updated: 2026-11-07. Latest exact checks are recorded in `TEST_EVIDENCE.md`._

## Current release status

- `RegressAgent` is at **0.1.0 alpha / pre-release**. It is not a production-ready public multi-user service and must not be exposed as one.
- Repository: `abrehmanmiqbal/RegressAgent`, branch `main`. The owner-authorized storage/UI hardening and browser-test update was pushed as `abfa656`; the GitHub Actions run for that commit passed on Python 3.11 and 3.12.

## Verified capabilities

- Provider-neutral regression core, deterministic offline runners, typed cases/results, assertion operators, bounded trace conversion, SQLite persistence, comparisons, repeated runs, reports, CLI, loopback-only HTTP API and local UI.
- Explicit HTTP and command adapters exist. Tier A runs as the current user and is **not a sandbox**. Tier B uses bubblewrap, fails closed and has host/kernel limits.
- Secret redaction is best-effort. Case definitions and traces can contain sensitive input; SQLite is plaintext.
- On POSIX, file-backed SQLite databases are created or restricted to mode `0600` when opened. This protects against other local users with ordinary filesystem permissions; it is **not encryption**, does not protect against the same user/root or unprotected backups, and Windows relies on filesystem ACLs. The existing repo demo DB was restricted from `0644` to `0600`; only `regressagent case list` was run for that check.
- The local UI has a maintained Playwright + axe audit in real Chromium for eight selected states: login, overview auto/light/dark, cases, traces, command palette and a mobile viewport. Issues found by the audit were fixed: login main landmark, visible button names and light/dark status/accent contrast. This is sampled Chromium automation, not a full accessibility certification or independent UX review.

## Checks run in this sandbox

- Python 3.12.3/Linux arm64; Node.js 22.23.4; Chromium 156.0.8078.73.
- `npm ci --no-audit --no-fund` in `ui-tests/`: succeeded from the lockfile (42 packages installed).
- `/tmp/regressagent-check/bin/python -m pytest -q`: **776 passed, 11 skipped in 39.01s**. All 11 skips are Tier B integration tests because bubblewrap is unavailable here. The full suite includes the real Chromium/axe test; it asserts eight states and zero axe violations.
- `/tmp/regressagent-check/bin/ruff check .`: passed.
- `/tmp/regressagent-check/bin/regressagent demo`: passed (exit 0).
- `git diff --check`: passed. Three prior local dashboard preview screenshots were intentionally excluded from the GitHub commit.
- GitHub Actions run [42357784214](https://github.com/abrehmanmiqbal/RegressAgent/actions/runs/42357784214) for `abfa656` completed successfully on Python 3.11 and 3.12.

## Open production-readiness work

- The API binds to loopback only and has no user accounts or tenant isolation. A public multi-user service needs a separate authentication, authorization, storage and agent-execution design; merely changing the bind address is unsafe.
- No database-at-rest encryption, retention/pruning, backup/restore workflow or independent security audit is verified. Redaction does not guarantee that secrets or personal data are removed.
- Tier A is intentionally unsandboxed; Tier B isolation depends on the host kernel and bubblewrap. Public arbitrary agent-code execution is not part of the current local API.
- The owner selected a **public demo with synthetic data only**. WebDev initialization failed twice at its `mount` step; no WebDev project, preview or deployment exists. The public demo remains blocked on that platform issue. Do not claim it is hosted or free-to-run until publication and actual provider costs are confirmed.
- Remaining historical notes still need reconciliation. The UI still needs independent UX review and broader browser/manual assistive-technology evaluation.

## Next concrete work

1. Continue reconciling historical documentation with the implementation.
2. After WebDev mounting is restored, build and inspect the public synthetic-data demo; do not expose the current loopback API.
3. Design and independently review any future multi-user/production architecture before accepting real customer inputs or credentials.

## Constraints

- Keep the core free-first and offline-capable; use synthetic data in public demos.
- Do not expose the current local API, accept confidential public-demo inputs, or claim a security guarantee without evidence.
- Never execute imported traces; never commit secrets.
