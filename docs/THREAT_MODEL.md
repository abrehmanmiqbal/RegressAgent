# Threat Model

_Last reviewed: 2026-11-06. See `TEST_EVIDENCE.md` for exact executed checks. This is an engineering threat model, not a security certification._

## Assets and trust boundaries

Protected assets include saved test inputs, traces, agent outputs, adapter configuration, environment credentials, and the user's local files/network accessible to configured commands. Treat case JSON, imported trace JSON, database contents, runner output, HTTP responses, IDs, and report values as untrusted. The local operator is trusted to choose the database path and configured adapters, but a configured adapter target is not inherently trustworthy.

## Current mitigations and residual risks

| Threat | Current mitigation | Residual risk / status |
|---|---|---|
| Malformed, oversized, deeply nested case or trace input | Byte/depth/field limits, strict JSON parsing, typed validation, bounded assertions and paths; hostile-input tests | Nested model input remains subject to interpreter limits; generated cases need human review. |
| Trace or case contents causing code execution | Case and trace contents are parsed as data; imported content is not executed. Adapters are invoked only through explicit local configuration. | Tier A commands run as the current user and can use that user's files/network. Never treat a user-supplied adapter config as safe. |
| Tier B sandbox escape or resource abuse | Bubblewrap namespaces, read-only filesystem binds, no network/capabilities, limits and fail-closed behavior; hostile probes and mutation checks are documented in test evidence. | Not a guarantee against kernel/bubblewrap bugs. Tier B checks are skipped where the local host cannot create a sandbox; verify on the target host. |
| HTTP adapter SSRF and data egress | Host allow-listing, resolved-address checks, pinned connection, no redirects, TLS verification, response-size and total-deadline caps. | Case input is sent to the configured endpoint. DNS resolution itself is not bounded, and endpoint/provider retention terms are outside RegressAgent's control. |
| Local API access, CSRF, DNS rebinding and oversized bodies | Loopback-only binding, random bearer token, Host/Origin/Sec-Fetch checks, no CORS, size cap, generic errors, API does not run adapters. | No TLS or rate limit. Any process that can read the terminal output can obtain the token. There is no user login or tenant isolation; do not expose as a public service. |
| UI XSS and token exposure | Static CSP, text-only rendering, token kept in memory after URL-fragment parsing, jsdom/static guard tests. | Maintained real-browser rendering, accessibility and browser-extension threat coverage remain incomplete. |
| Secrets or personal data persisted in cases/results/traces | Best-effort pattern redaction for stored results/metadata and traces; on POSIX the SQLite database file is created/restricted to mode `0600`. | Redaction can miss unlabeled, encoded or split secrets and personal data; case definitions are stored as given. The database is plaintext. Mode `0600` is not encryption and does not protect against same-user/root access or unprotected backups. Windows relies on filesystem ACLs. |
| SQL injection and database corruption | Parameterized SQL, explicit atomic migrations, schema-version checks, payload limits and tampered-row validation. | Large-database and concurrent-load behavior has not been characterized as a hosted database. |
| Path abuse / report output | Explicit CLI paths, validation, overwrite confirmation for reports, bounded reads. | User-selected `--force` output paths can follow symlinks; special-file handling is not fully hardened. |
| Dependency / release supply chain | CI, dependency update configuration, lint/test workflow and prior reviewed scans are recorded in test evidence. | Python dependencies are not hash-locked; action pins are not all commit-SHA pins; independent security audit and current-commit CI are not verified. |
| Flaky agent behavior | Repeat-run stability summaries and assertion-level comparisons. | Stability of the configured assertions is not a guarantee of model quality or semantic correctness. |

## Production boundaries

The current product is a local alpha tool: local SQLite, loopback-only UI/API, no public account system, no tenant separation, no durable hosted data service, no backup/restore/retention service, and no independent security review are verified. A public demo selected by the owner must use synthetic/sample data only and must not accept credentials or run arbitrary user agent code. Public demo project initialization is currently blocked by a WebDev `mount` failure; no deployment exists.

For data not intended to leave the user's control, run locally and do not use an external model/HTTP endpoint. RegressAgent does not promise that secret redaction, local file permissions, or any sandbox provides zero-risk confidentiality.
