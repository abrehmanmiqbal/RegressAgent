# RegressAgent 0.1.0 (DRAFT, not published)

Status: **DRAFT.** Nothing has been published to PyPI or released on GitHub; that needs the owner's explicit go-ahead. The GitHub repository was renamed to `RegressAgent` (D-016). Numbers below were measured on 2026-10-24 and are recorded in `docs/TEST_EVIDENCE.md`.

## What it does
Offline-first regression testing for AI agents. Capture a behaviour once (a case file, or a trace converted into a case), run it against your agent, record the run, and compare before and after with an assertion-level diff and a report.

- Cases with 8 assertion operators: `exists`, `equals`, `not_equals`, `contains`, `gt`, `lt`, `matches_type`, `regex` (the regex is restricted against ReDoS).
- SQLite persistence of cases and immutable runs; `compare` and deterministic Markdown/JSON `report`.
- `run --repeat` and `run-all --repeat` to measure flakiness (`stable` / `unstable`).
- `case from-trace` turns a captured trace (`{trace_id, input, output}`) into a reviewable case.
- Real agents through adapters: a local command (Tier A) or an HTTP endpoint (SSRF-guarded). Demo runners (`local`, `local-legacy`) need nothing.
- Best-effort secret redaction of stored text; no network use unless you configure an HTTP adapter.

## Install
Not on PyPI. From a clone (Python 3.11 or 3.12; developed and tested on Linux only):

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest && regressagent demo
./examples/run_example.sh
```

Do not run `pip install agentproof`: that PyPI name is a different project. Formerly called AgentProof.

## Verified
- 551 tests pass on Python 3.12.3 and 3.11.15; `ruff check .` is clean; GitHub CI (3.11 and 3.12) passed on the last commit that could be checked.
- Wheel and sdist build, `twine check` passes, and the wheel installs and runs the whole loop in fresh virtual environments.

## Known limitations
- **Tier A adapters are not a sandbox.** The configured command runs as you, with your files and network. Only use it for code you would run by hand. Tier B (a real sandbox) does not exist yet.
- Linux only is tested; macOS and Windows are not (Tier A adapters refuse to run on Windows).
- Generated assertions from traces pin exact values and need human review; only single-step traces are supported and raw traces are not stored in the database yet.
- No HTTP API and no UI; CLI only.
- Regex safety is a mitigation, not a proof; secret redaction is best-effort pattern matching.
- Full JSON-schema validation is not implemented (only `matches_type`).
- Trademark, domain and npm availability of the name were not checked.

## Security notes
Case files, traces, runner and HTTP responses are treated as untrusted data and are never executed. The config that defines adapters is always passed explicitly with `--config` and is never discovered from the working directory. See `docs/THREAT_MODEL.md` and `docs/ADAPTER_ISOLATION.md` for what is and is not mitigated.

## Upgrade notes
The project was renamed before any release (D-016): command `regressagent`, environment variable `REGRESSAGENT_DB`, default database `regressagent.db`.
