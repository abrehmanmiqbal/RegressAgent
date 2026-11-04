# Security Policy

RegressAgent runs commands and calls endpoints that you configure, stores data your agents produce, and
serves a local API/UI. Security matters here; please report problems privately.

## Reporting a vulnerability
Open a **private security advisory** on GitHub (Security tab, "Report a vulnerability") for this repository.
Please do not open a public issue for an unpatched vulnerability. Include the version, what you did, what
you expected and what happened. There is no bug bounty and no response-time guarantee (a single
maintainer), but reports are taken seriously.

## What is in scope
- Escaping the Tier B sandbox, or making a Tier B/Tier A/HTTP adapter run something the config did not allow.
- Making the local API/UI accept requests it should reject (token, Host, Origin, size limits), XSS in the UI,
  or reading the token from the server.
- Secrets reaching the SQLite file, reports or the API despite redaction.
- Crashes/hangs from hostile case files, traces, conversations or runner/HTTP output (ReDoS, huge input).
- SQL injection, path abuse, unsafe file handling in the CLI.

## Known limits (not vulnerabilities by themselves)
See `docs/THREAT_MODEL.md`: Tier A is deliberately not a sandbox; redaction is best-effort pattern matching;
the API has no TLS (loopback only) and no rate limiting; a kernel or bubblewrap bug defeats Tier B.

## Supported versions
Only the latest commit on `main` (pre-1.0, alpha).

## Hardening already in place
Strict JSON parsing, size/depth limits, restricted regexes, parameterised SQL, redaction before storage,
loopback-only API with bearer token and Host/Origin checks, strict CSP, SSRF-guarded HTTP adapter, a
fail-closed bubblewrap sandbox, fuzzing and mutation checks recorded in `docs/TEST_EVIDENCE.md`.
