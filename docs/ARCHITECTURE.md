# Architecture

## Product loop

Capture observable agent inputs and outputs → turn a failure into a saved regression case → rerun the case against a candidate implementation → evaluate explicit assertions → compare runs and render a reproducible report.

## Current modules

- `models.py`: typed cases, assertions, traces, results and run metadata.
- `runner.py`, `regression.py`, `assertions.py`: provider-neutral runner contract, deterministic local runners and bounded assertion evaluation.
- `storage.py`: the SQLite repository, explicit schema migrations, content hashes, size limits and run/trace persistence. On POSIX, file-backed databases are created/restricted to owner-only mode (`0600`). Database contents remain plaintext; this is not encryption. In-memory repositories are supported.
- `comparison.py`, `report.py`: assertion-level run comparison and Markdown/JSON reports.
- `config.py`, `subprocess_adapter.py`, `http_adapter.py`: explicit local adapter configuration. Tier A commands run as the current user (not a sandbox); HTTP adapters send case input to the configured endpoint; Tier B is a fail-closed bubblewrap command sandbox on supported Linux hosts.
- `trace.py`, `importers.py`: bounded trace parsing, conversion and case generation. Imported traces are treated as data, not executable instructions.
- `api.py`, `ui/`: local HTTP API and browser UI. The API binds to loopback only, uses a random bearer token and rejects adapters; it is not a hosted multi-user API.
- `cli.py`: command-line operator interface.
- `ui-tests/`: jsdom interaction tests plus Playwright + axe checks in Chromium. Browser-test packages are development-only; the browser audit uses a temporary local API/database and synthetic data.

## Data and trust boundaries

- Case files, imported traces, stored database content, runner output and HTTP responses are untrusted data.
- A local HTTP adapter transmits the case input to its configured endpoint. The caller is responsible for endpoint trust and data-sharing decisions.
- Tier A adapter code has the current user's filesystem and network permissions. Tier B adds OS isolation but is not a kernel-escape guarantee.
- Secret redaction is best-effort and does not remove all personal data. The SQLite file is plaintext, and same-user/root access and backups are outside POSIX mode `0600` protection.
- The current local API is deliberately loopback-only. Do not expose it directly or represent it as a public multi-user service.

## Design constraints

- Keep the core provider-neutral, deterministic where possible, and usable offline without paid APIs or hosting.
- Do not execute traces or case-file contents.
- Keep a clear distinction between regression evidence and semantic model quality; passing assertions only establish the configured checks.
- Public demo scope selected by the owner: synthetic/sample data only. A public demo must not accept credentials, real customer data or arbitrary live agent code.

## Known production gaps

No multi-user authentication/tenant authorization, durable hosted data service, backup/restore and retention system, database encryption, arbitrary hosted-agent worker isolation, independent security audit or independent UX review is verified. UI accessibility automation currently covers eight sampled Chromium states with axe; it is not cross-browser testing or an accessibility certification. The local UI/API are not production SaaS infrastructure.
