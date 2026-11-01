# Threat Model (session 4, 2026-10-18)

Scope: the current code (CLI + SQLite + deterministic demo runners). Adapters that run real agents, an HTTP API and a UI do **not exist yet**; their threats are listed as *future* and must be re-assessed (see D-010). "Mitigated" means a test or an executed probe exists (see TEST_EVIDENCE.md); it does not mean "proven secure". No independent security review has been done.

## Assets
- Integrity of regression verdicts (a false pass is the worst failure, per D-003).
- The user's machine (no code from case files/traces may run on it).
- Stored runs in the SQLite file (may contain whatever the agent output, including secrets or personal data).
- Availability of the CLI (no hangs/crashes from hostile input).

## Trust boundaries (all of these are untrusted data)
1. Case JSON files given to `case add`.
2. Agent/runner output (today only demo runners; later real agents).
3. Contents of an existing SQLite file passed via `--db`.
4. Strings echoed into reports (case ids, paths, expected/actual values).

Trusted: the person running the CLI (they choose file paths, `--db`, `--output`).

## Threats and status

| # | Threat | Status | Mitigation / evidence | Gap |
|---|---|---|---|---|
| 1 | Oversized, malformed, deeply nested, or non-UTF-8 case file | Mitigated | 1,000,000-byte read cap, JSON-only parse, typed models with `extra=forbid`, 100-assertion cap; hostile probes exit 2 cleanly (TEST_EVIDENCE s2 #9) | `input`/`expected` nesting is only bounded by Python's recursion limit (which is caught); assertion paths are now limited to 512 chars / 32 segments (session 9) |
| 1b | Hostile trace file given to `case from-trace` | Mitigated | Strict JSON, 1 MB read cap, depth 32, no NaN/Infinity, errors never echo trace values, secret-looking values never copied, generated paths capped at 512 chars; probes: 200k-deep nesting exit 2 in 0.2 s, 60k-key trace exit 2 in 0.75 s | Brittle generated assertions need human review; secret detection is best-effort |
| 1c | Secrets in stored traces | Mitigated (best effort) | Traces are redacted before they are written (schema v3); tests scan the raw DB file for the secrets; black-box check found 0 occurrences | Pattern based: an unusual secret format can still be stored |
| 1d | Local HTTP API reachable by other processes / web pages (DNS rebinding, CSRF, token guessing, oversized bodies) | Mitigated | Loopback-only bind, random token, Host/Origin/Sec-Fetch-Site checks, 1 MB cap, no CORS, generic 500; 8 mutation checks of these layers all caught by tests | No TLS and no rate limiting; any local process that reads the terminal output can see the token; adapters are intentionally not runnable via API |
| 2 | Code execution through a case/trace | Mitigated by construction | No `eval`/`exec`/`subprocess`/`pickle`/dynamic import in `src/` (grep, session 4); case data is only parsed as JSON | `src/regressagent/subprocess_adapter.py` (Tier A, D-012) is the ONLY place that starts a process (`shell=False`, argv from the validated local config, never from case data); it exists but is not reachable from the CLI yet. It is not a sandbox: the child can read your files and use the network (tests assert this on purpose) |
| 3 | ReDoS via `regex` assertions | Partially mitigated | Static validation + 200-char / 5,000-char caps (D-009); fuzz: slowest accepted 0.73-0.81 s, no 30 s timeouts | Estimate, not proof; no hard timeout possible in-process; worst case per assertion is about 1 s x up to 100 assertions per case |
| 4 | Report/Markdown injection (pipes, newlines, HTML, huge values) | Mitigated for Markdown/JSON | Values length-limited, whitespace-collapsed, HTML-entity escaped; golden-file + injection tests | HTML reports do not exist; if added, re-assess |
| 5 | SQL injection | Mitigated | All user data bound as parameters; the only f-strings in SQL interpolate internal constants; injection-like ids tested | None known |
| 6 | Corrupt or newer-schema database | Mitigated | Clear exit 2 (no traceback); newer or negative schema version refused; versioned atomic migrations; real v1->v2 upgrade tested (session 7); tampered metadata columns give a clean `StorageError` | Large databases and concurrent migration untested |
| 7 | Runner output too large to store | Mitigated (session 4) | Recorded as a failed run with an explanatory error instead of crashing/losing the run (test added) | Runner CPU/memory use is not limited (demo runners only) |
| 8 | Secrets / personal data in stored outputs | **Partially mitigated (best-effort)** | `redaction.py`: all stored results and run metadata pass one chokepoint (`Repository.record_run`); redacts values of secret-named env vars (>= 8 chars), well-known token formats, secret-named assignments and keys, URL passwords, PEM keys | NOT a guarantee: misses unlabeled/encoded/split secrets and personal data; the case file itself is stored as given; DB is plaintext with default permissions. Do not run real agents with sensitive data yet |
| 9 | Path abuse via `--output` / `case add FILE` | Partially mitigated | `report --output` refuses to overwrite without `--force`; read errors are clean exit 2 | `--force` follows symlinks; reading a FIFO/special file path is untested and could block. Acceptable only because the user chooses the paths |
| 10 | Concurrent access to one SQLite file | Not verified | SQLite's own locking | Not tested |
| 11 | Supply chain | Partial | One runtime dependency (`pydantic>=2.6,<3`); CI uses pinned major versions of actions | No lock file, no hash pinning, actions not pinned by SHA; regex analysis relies on private `re._parser` (fails closed if missing) |
| 12 | Flaky/nondeterministic agents hiding as passes | Partially mitigated | `run --repeat` and `run-all --repeat` report `stable` | Only meaningful with a real nondeterministic agent (none exists yet) |
| 13 | Terminal noise: `BrokenPipeError` traceback when output is piped into `head` | Known, cosmetic | Observed in session 3 | Not fixed |

## Future threats (not yet applicable)
- Adapters executing user commands/code (sandbox escape, resource exhaustion, env/secret leakage, output injection). The HTTP adapter now exists (D-015): SSRF guard with pinned connection, no redirects, TLS verified, response cap and total deadline; remaining risks: the endpoint receives the case input (data leaves the machine if the host is remote), DNS lookup time is unbounded, allow-listed hosts may resolve to private ranges. Tier A subprocess adapter: built and tested against a hostile agent for hangs, floods, orphan children, env leakage, bad output and limits (see TEST_EVIDENCE); NOT covered: filesystem/network access of the child, setsid escapes, a file swapped between hash check and launch, RLIMIT_NPROC as root. HTTP adapter (SSRF) and Tier B: not built.
- HTTP API: authentication, CSRF, local-only bind, request size limits.
- Trace import: untrusted large/nested files; prompt-injection text inside traces is data and must never be executed or fed to tools.

## Process note
A short-lived GitHub token was provided by the owner for pushing. It is used only inline in a single `git push` command, never written to files, git config, docs, or logs.
