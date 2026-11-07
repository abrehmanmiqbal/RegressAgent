# Handoff — Current State

## Current verified snapshot (2026-11-07)

- Repository: `abrehmanmiqbal/RegressAgent`, branch `main`, version `0.1.0` alpha pre-release. The checkout and `origin/main` were at base commit `1c71f0c` when this update began.
- The owner authorized syncing the current source, tests and docs to `main`; remote identity and the starting `main` ref were checked. The intended commit excludes the three existing local preview screenshots (`case-preview.png`, `cli-adapter-dashboard.png`, `dashboard-preview.png`).
- POSIX file-backed SQLite DBs are created/restricted to owner-only mode `0600` before SQLite opens them. Tests cover permissive-umask creation, reopening a `0644` DB, rollback-journal permissions and FIFO refusal. This is not encryption; same-user/root access, plaintext backups and Windows ACL behavior remain separate concerns.
- The UI has a maintained Playwright + axe audit in real Chromium across eight states: login; overview auto/light/dark; cases; traces; palette; mobile viewport. It checks configured WCAG 2.0/2.1 A/AA and axe best-practice rules with zero violations in the final test run. This is not cross-browser coverage, a full accessibility certification or an independent UX review.
- `npm ci --no-audit --no-fund` succeeded from the `ui-tests/` lockfile (42 packages). Final local checks: `776 passed, 11 skipped in 39.01s`; the 11 skips are Tier B tests because bubblewrap is unavailable in this sandbox. Ruff passed, `regressagent demo` passed, and `git diff --check` passed.
- The GitHub Actions workflow is configured to install Node dependencies and Chromium on the Python 3.12 job. Its result for the changed workflow is pending the requested push.
- RegressAgent remains alpha and is **not production-ready as a public multi-user service**. SQLite is plaintext; redaction is best-effort; Tier A is not a sandbox; the local API remains loopback-only.
- The requested public demo is synthetic data only. WebDev initialization failed twice at its `mount` step; no project, preview or deployment exists. Do not describe a public site or free hosting as completed.

## Safe next actions

1. Push only source, tests and documentation to the verified `main` branch, then inspect the GitHub Actions run.
2. Continue reconciling the remaining historical documentation against the implementation.
3. Once WebDev mounting is restored, build and inspect the public synthetic-data demo; do not expose the current local API.
4. Treat any future multi-user service as a separate architecture/security project; require independent review before accepting confidential user data or credentials.

## Guardrails

- Run the full tests and lint and record exact results for meaningful code changes.
- Never commit secrets or imported trace contents; traces are data and must not be executed.
- Do not claim zero data-sharing risk, production readiness, or an accessibility certification.
- Tier A is unsandboxed; Tier B is not an escape-proof security guarantee.
