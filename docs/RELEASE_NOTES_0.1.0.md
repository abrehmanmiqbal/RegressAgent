# RegressAgent 0.1.0 (alpha pre-release)

First public release of RegressAgent: offline-first regression testing for AI agents. **Alpha:** the CLI, API and
UI work and are well tested on Linux, but expect rough edges. Not published on PyPI (the old name `agentproof` on
PyPI is a different project; this project was renamed before release, D-016).

## What it does
Capture a behaviour once (a case file, or a recorded trace/conversation), run it against your agent, record the
run, and compare before and after with an assertion-level diff and a report. It catches what normal tests miss:
the same words with different behaviour (for example a skipped tool call).

- **11 assertion operators:** `exists`, `equals`, `not_equals`, `contains`, `gt`, `lt`, `matches_type`, `regex`
  (restricted against ReDoS) and the tool-call operators `tool_called`, `tool_not_called`, `tool_order`.
- **Real agents** through adapters: a local command (**Tier A**, no sandbox), the same command inside a
  **bubblewrap sandbox** (**Tier B**: no network, read-only minimal filesystem, no capabilities, fail-closed) or an
  **HTTP endpoint** (SSRF-guarded, TLS verified, no redirects). Demo runners need nothing.
- **Traces:** store them (redacted before they touch the disk), convert OpenAI/Anthropic conversations, and
  generate reviewable cases from them (including tool-call assertions).
- **Reports and history:** SQLite persistence, immutable runs, `compare`, deterministic Markdown/JSON reports,
  `--repeat` to measure flakiness.
- **`regressagent serve`:** a loopback-only HTTP API (bearer token, Host/Origin checks) and a web UI with a dashboard,
  case/trace pages, compare view, command palette (`Ctrl/Cmd+K`), keyboard shortcuts and light/dark themes.

## Install
From a clone (Python 3.11 or 3.12; developed and tested on Linux):

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest && regressagent demo
./examples/run_example.sh
regressagent serve        # then open the "Open the UI" link it prints
```
Tier B needs `bubblewrap` (`apt install bubblewrap`). Do not run `pip install agentproof`: that is someone else's project.

## Verified
- 782 tests pass on Python 3.12.3 and 3.11.18; `ruff` clean; GitHub CI (3.11 and 3.12) green; bandit, pip-audit and
  npm audit reviewed (see `docs/TEST_EVIDENCE.md`, which records every command that was run and what was NOT verified).
- Security-relevant code was checked with mutation testing (break the code on purpose, confirm a test fails), a regex
  fuzzer, a hostile test agent, a sandbox-escape probe agent and hostile-input probes.
- Wheel and sdist build, `twine check` passes, the wheel installs and serves the UI in a fresh environment.

## Known limitations
- **Tier A is not a sandbox**: the command runs as you. Use Tier B for untrusted code. Tier B is not a guarantee
  either (a kernel or bubblewrap bug defeats it) and needs unprivileged user namespaces.
- The **UI was tested with jsdom against the real server, never in a real browser**: layout, CSS and dark-mode colours
  are unverified; no accessibility audit has been run.
- Only Linux is tested. macOS/Windows are untested (Tier A/B adapters refuse to run on Windows).
- The CI workflow does not yet install jsdom, so CI skips the UI end-to-end test (`docs/ci-node-step.patch` fixes that
  once applied with a token that may edit workflows); Tier B tests also skip on runners that forbid user namespaces.
- Importers were tested on hand-written conversations that follow the public OpenAI/Anthropic shapes, not on real exports.
- Generated assertions pin exact values and need human review; only tool calls are modelled as steps; no replay.
- Redaction is best-effort pattern matching; the API has no TLS (loopback only) and no rate limiting.
- Trademark, domain and npm availability of the name were not checked.

## Security notes
Case files, traces, conversations and runner/HTTP output are untrusted data and are never executed. The config that
defines adapters is always passed explicitly with `--config` and never discovered from the working directory.
See `SECURITY.md`, `docs/THREAT_MODEL.md` and `docs/ADAPTER_ISOLATION.md` for what is and is not mitigated.

## Upgrade notes
Nothing to upgrade from: this is the first release. Command `regressagent`, environment variable `REGRESSAGENT_DB`,
default database `regressagent.db`.
