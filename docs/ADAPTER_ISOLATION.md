# Adapter Isolation and Trust Boundaries

_Last reviewed: 2026-11-06. Current implementation is in `config.py`, `subprocess_adapter.py` and `http_adapter.py`; detailed execution evidence is in `TEST_EVIDENCE.md`. This is design documentation, not a security certification._

## 1. Purpose and hard rules

Adapters let a regression case invoke an agent configured by the local operator. Case files and traces define inputs/assertions only; they never contain executable paths, commands, URLs or environment variable values. Adapters are opt-in, configured through an explicitly supplied local TOML file and selected by name on `run`/`run-all`. There is no config discovery from the current working directory.

All adapter output and HTTP responses are untrusted JSON data. RegressAgent does not evaluate output as code. Errors, timeouts, malformed output and limit violations become failed run results. A requested command sandbox is refused if bubblewrap is absent or cannot create the sandbox; it is never silently downgraded to Tier A.

The config loader parses and validates; it does not execute commands or contact endpoints. It rejects symlink/non-regular configs, configs writable by group/others or owned by another user (root is also allowed), oversized files, unknown keys, invalid argv/URL fields and unsafe environment-variable names. Tier A command configs require `acknowledge_not_sandboxed = true`; Tier B is a separate explicit selection and does not require that Tier A acknowledgement.

## 2. Tier A subprocess adapter: process guard rails, not a sandbox

Tier A is for code the operator would be willing to run directly. The child runs as the current user and can read that user's files and use the network. It is **not** a security boundary against a malicious or compromised agent.

The implemented guard rails are: `shell=False`; argv and executable come only from the validated local config; the executable is resolved and checked as a regular executable that is not group/world writable; its SHA-256 is recorded and an optional configured digest is enforced. The child starts with a clean environment plus fixed `PATH` and explicitly allow-listed variable names; variable values are read at run time and not recorded. A fresh temporary working directory is removed after execution.

A separate process group is supervised with a wall-clock deadline, incremental stdin/stdout/stderr handling, output caps, and termination/reaping of the process group on failure or normal completion. POSIX resource limits cover CPU, address space, file size, open files, core dumps and tasks; a small isolated Python launcher applies them before `exec`. The response must be UTF-8 JSON containing one bounded-depth object and the process must exit successfully. These controls reduce accidents and resource abuse but do not prevent a child from accessing the host. A descendant that calls `setsid` can escape Tier A process-group cleanup; `RLIMIT_NPROC` is per-user and is not enforced for root; executable hash checking has a time-of-check/time-of-use window. Linux is required; unsupported platforms fail closed.

## 3. Tier B subprocess adapter: bubblewrap isolation

Tier B wraps the same configured command in bubblewrap (`bwrap`) on Linux. It uses private namespaces including a network namespace, drops capabilities, exposes system roots read-only, creates private `/tmp` and `/work` tmpfs mounts, and uses `/work` as the working directory. Additional host paths may be explicitly supplied as bounded `ro_binds`; those mounts are read-only. When the installed bubblewrap supports it, nested user namespaces are disabled. The executable and existing absolute-path arguments must be visible inside the sandbox; otherwise the run is refused with a hint to configure a read-only bind.

At startup, RegressAgent runs a smoke check to ensure bubblewrap can create the required environment. If it cannot (for example, host policy disables user namespaces), the adapter refuses to run. No network access is intentionally exposed inside the sandbox. The operator remains responsible for what is included in `ro_binds` and in the case input: those values are visible to the agent.

Tier B is a defense-in-depth OS boundary, **not** a guarantee against kernel or bubblewrap vulnerabilities. The integration suite includes probes for network isolation, host-file visibility, read-only binds, temporary storage, environment leakage, PID namespace/capabilities, exposed filesystems, executable visibility and nested user namespaces. Those tests can skip when bubblewrap is missing or unavailable. In the 2026-11-06 sandbox run, 11 Tier B tests skipped because `bwrap` was not installed; earlier successful Tier B evidence is recorded in `TEST_EVIDENCE.md`. Re-run the suite on each supported target host rather than assuming the sandbox is available.

## 4. HTTP adapter: explicit remote data egress

The HTTP adapter POSTs the case input as JSON to a URL in the explicit local config. A loopback endpoint is permitted by default; other hosts require an allow-list entry and HTTPS unless the operator explicitly enables insecure HTTP. The URL cannot include credentials, query parameters or a fragment. Header values come from named environment variables; secrets are neither stored nor printed in errors.

The adapter resolves the hostname once, checks every resolved address, and pins the connection to a validated address to prevent DNS rebinding. Link-local, unspecified, multicast and reserved addresses are refused; a non-loopback address must be allow-listed. Redirects are never followed and TLS certificate/hostname validation is on. Response bodies are read under a byte cap and a watchdog enforces a total request deadline.

**Data leaves the local machine:** the configured endpoint receives the case input. Only send data to a provider/service approved to process it. DNS lookup time is outside the request deadline; explicitly allow-listed hosts may resolve to private ranges; there are no request retries, proxy support or client certificates. RegressAgent cannot control endpoint logging, retention or training policies. A successful HTTP response is only a candidate output; configured regression assertions still determine the test result.

## 5. Local API boundary

The loopback-only HTTP API runs built-in demo runners; it does not expose subprocess or HTTP adapters. Its bearer token, Host/Origin/Sec-Fetch checks and body cap protect the local UI/API surface. This is not authentication for a hosted multi-user service. Do not change the bind address or expose this API publicly as a shortcut to hosting adapters.

## 6. Stored data and credentials

Run results and metadata pass through best-effort redaction before persistence. Stored traces are also redacted before they are written, but case definitions are stored as supplied. Pattern-based redaction can miss unlabeled, encoded or split secrets and personal information. The SQLite database is plaintext. On POSIX, the current repository creates/restricts file-backed database files to mode `0600`; this is filesystem access control, **not encryption**, and does not protect against the same user, root, process compromise or unprotected backups. Windows relies on filesystem ACLs. Do not rely on redaction or file mode as a confidentiality guarantee.

## 7. Reproducibility and failures

Runs persist the adapter name/tier, executable path and hash for command adapters, argv, environment-variable names (not their values), time/limits, exit status and output-truncation information. HTTP runs record the configured URL and HTTP status, not credential values. Adapter failures are recorded as failed runs with a bounded, sanitized explanation; they do not imply a security incident was prevented.

## 8. Verification and remaining work

Use the relevant unit/integration tests in `tests/test_config.py`, `tests/test_subprocess_adapter.py`, `tests/test_tier_b.py` and `tests/test_http_adapter.py`, along with mutation/security evidence in `docs/TEST_EVIDENCE.md`. Current-environment skips are evidence of an unavailable test capability, not a passing sandbox test. CI for the latest local worktree, other Linux distributions/kernels, Windows filesystem ACL behavior, sustained concurrent load and an independent security review are not verified here.

The current product is a local alpha. There is no public tenant/account system, hosted job isolation or production data service. A public demo must use synthetic/sample data only and must not accept credentials or execute user-supplied agents.

## 9. Decisions and references

D-010 records the accepted adapter design; D-012 refines Tier A implementation; D-015 defines the HTTP adapter; D-020 keeps the local API loopback-only; D-022 implements Tier B. These decisions are historical records; current behavior and limitations are defined by the source code and the verification evidence, not by old implementation-status wording.

Examples: [`examples/regressagent.example.toml`](../examples/regressagent.example.toml), [README adapter sections](../README.md), [Threat Model](THREAT_MODEL.md), [Test Evidence](TEST_EVIDENCE.md).
