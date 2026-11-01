# Handoff — Read This First

## LATEST (session 8, 2026-10-20) — read this first
- **CI is green again:** commit 8a3e726, jobs `test (3.11)` and `test (3.12)` both success (checked via API). Root cause of the earlier red CI (b91c26e, 20d77b5, be24649): `tests/test_subprocess_adapter.py::_pick_agent_python` chose GitHub's group-writable toolcache interpreter, which the adapter correctly refuses (44 tests failed). Fixed in the test helper only; the security check is unchanged. Details: TEST_EVIDENCE.md #21-24.
- CI logs cannot be read from the agent sandbox. The workflow now emits pytest failures as `::error::` annotations; read them with `GET /repos/OWNER/REPO/check-runs/<job_id>/annotations`.
- Python 3.11 is installable locally: `pip install uv && uv python install 3.11` (suite: 359 passed on 3.11.15).
- **CLI wiring done (session 8):** `run`/`run-all --adapter NAME --config FILE` (README section "Running a real agent"). 370 tests, ruff clean.
- **GitHub repo renamed by the owner to `RegressAgent`** (seen 2026-10-31); `origin`, pyproject URLs and the packaging test were updated. Pushing to the old URL still works through GitHub's redirect. Release notes draft is on `main`.
- **Renamed to RegressAgent (D-016, session 9).** Command `regressagent`, env `REGRESSAGENT_DB`, package `regressagent`. Never publish without explicit authorization.
- **Trace v1 + `case from-trace` done (D-014).** Raw-trace persistence (schema v3), multi-step traces and framework importers are NOT done.
- **HTTP adapter done (D-015).** `matches_type` done.
- **Raw trace persistence done (schema v3, D-017).**
- **Multi-step traces done (D-018).**
- Next: framework importers, local HTTP API + UI, Tier B, local HTTP API + UI, Tier B (needs bubblewrap to test; claim nothing untested), release notes. The name no longer blocks a release (D-016); release notes and the owner's repo rename remain. Do not claim sandboxing for Tier A. D-010/D-011 (MIT)/D-012 were accepted in earlier sessions under the owner's chat delegation ("jo best approach ho woh karo"); the owner may override any of them.

## State (end of session 7, 2026-10-18)
Session 7 added: DB schema v2 + `RunMetadata` (pushed, 5a3ac08) and best-effort secret redaction (`src/regressagent/redaction.py`, wired into `Repository.record_run`; 227 tests, ruff clean on Python 3.13.16; 6 mutations caught). Items 0-2 below are DONE; item 3 is DONE: `src/regressagent/config.py` loader (79 tests) and `src/regressagent/subprocess_adapter.py` Tier A adapter with `tests/hostile_agent.py` + `tests/test_subprocess_adapter.py` (50 tests; 356 total). Design refinements are in D-012. NEXT: CLI wiring (`--config PATH --adapter NAME`, explicit config path only, record `RunMetadata` via `record_run(metadata=...)`, README section 'what Tier A does NOT protect against', exit 2 on ConfigError, refuse adapters on non-Linux). The text in the next paragraph describes session 4 and is older.

## State (end of session 4)
`main` contains: assertion operators `gt`/`lt`/`regex` (D-009), 100-assertion cap, README CLI docs, `examples/`, `run-all --repeat`, oversized-output fix, `docs/THREAT_MODEL.md`, `docs/ADAPTER_ISOLATION.md` (design only). 142 tests passing, `ruff check .` clean, Python 3.12.3 locally. **CI on GitHub was observed passing on 3.11 and 3.12 for 161104d**; check the Actions tab / API for the newest commit first (CI for the session-4 commits was checked at the end of the session, see TEST_EVIDENCE.md).

## Rules
1. Read AGENTS.md and docs/ first; verify the repo rather than trusting notes.
2. `python -m pip install -e ".[dev]" && ruff check . && pytest && regressagent demo` before and after changes.
3. Test-first; record exact commands/results; never fabricate evidence; say what is NOT verified.
4. Traces/cases are untrusted data. Adapters that execute commands/code must follow the accepted D-010 (docs/ADAPTER_ISOLATION.md) exactly, test-first, Linux only, Tier A labelled trusted-code-only. No network calls or command execution outside that design.
5. Do not publish, spend money, or commit secrets. (LICENSE is MIT, D-011.) Push tokens are used inline in one `git push` command only (never saved or printed).

## Known issues / design notes
- Dot-path syntax cannot address keys that contain "." (no escaping yet).
- `contains` follows Python `in` (substring / membership / dict key).
- Run history stores full result JSON; no retention/pruning. Redaction is best-effort only (THREAT_MODEL #8); the case file is stored as given.
- `local-legacy` runner is a deliberate demo "bug", not a real agent.
- Regex validation is an estimate-based mitigation, not a proof (D-009); it uses private `re._parser` (fails closed).
- Piping CLI output into `head` prints a `BrokenPipeError` traceback (cosmetic, unfixed).
- Python 3.11 is verified only through GitHub CI, not locally.
- GitHub access: repo must be attached in the session via add_repo (owner linked GitHub in claude.ai/code); the repo moved to the lowercase name `abrehmanmiqbal/regressagent`.

## Next concrete tasks (in order)
0. (done in session 7) Step 0 first: the environment in sessions 5-6 could not `pip install` (pypi.org DNS/proxy). Re-test with `pip download pydantic`. If it still fails, do NOT write code you cannot test. Also record the CI results (2c2affd, 5c2bd95 and 7d02442 were observed success via API in session 6; 1e71429 was still running) in TEST_EVIDENCE.md and re-run `ruff check . && pytest && regressagent demo`.
1. (DONE) DB migration v2 with run-metadata columns (ADAPTER_ISOLATION.md section 8 step 1; also the first real v1 -> v2 migration test).
2. (DONE) Secret redaction of stored outputs (THREAT_MODEL #8).
3. (loader and Tier A adapter DONE; CLI wiring is next) `regressagent.toml` loader (stdlib tomllib, trust acknowledgement key required), hostile test agent, Tier A subprocess adapter (test-first, Linux only). D-010 is accepted, but NOTHING in it is implemented or tested yet.
4. Later: Tier B with bubblewrap (needs bwrap installed to test; claim nothing untested).
