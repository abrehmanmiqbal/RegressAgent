# Architecture Decision Log

## D-001: Offline deterministic core
**Status:** Accepted  
**Decision:** The core demo and tests must work without paid APIs, accounts, or hosted services.  
**Reason:** Free-first accessibility and reproducibility.

## D-002: Provider-neutral runner boundary
**Status:** Accepted  
**Decision:** External model integrations must sit behind a narrow runner interface.  
**Reason:** Avoid provider lock-in and keep the test engine independently testable.

## D-003: Explicit assertions over vague scores
**Status:** Accepted  
**Decision:** A regression passes or fails based on declared, inspectable assertions; any aggregate score must be explainable.  
**Reason:** Results should be reproducible and auditable.

## D-004: Do not execute imported traces
**Status:** Accepted  
**Decision:** Recorded traces and test fixtures are data, not executable instructions.  
**Reason:** Reduce code-execution and prompt/tool injection risk.

## D-005: Delay framework lock-in until environment inspection
**Status:** Resolved (session 2)  
**Decision:** Keep the dependency set minimal: pydantic only at runtime; persistence uses stdlib `sqlite3`; no web framework yet. Revisit when the HTTP API is built.  
**Reason:** Avoid unnecessary complexity and unsupported assumptions.

## D-006: Type-strict equality for assertions
**Status:** Accepted  
**Decision:** `equals`/`not_equals` never equate bool with int (True != 1), recursively; int/float still compare numerically.  
**Reason:** Python's `True == 1` produced false passes, violating D-003.

## D-007: Runner failures are results, not exceptions
**Status:** Accepted  
**Decision:** Runner exceptions and non-dict output are recorded as failed `RegressionResult` with `error` set.  
**Reason:** Agent code is untrusted/unreliable; a crash must be a persisted, comparable failure.

## D-008: Persistence design
**Status:** Accepted  
**Decision:** SQLite with ordered append-only migrations via `PRAGMA user_version`; refuse newer schemas; runs store the SHA-256 of the exact case JSON; 1,000,000-byte cap per stored case/result.  
**Reason:** Reproducibility, safe upgrades, bounded storage. Note: adapter-isolation design remains an open decision and must precede any adapter that executes code.

## D-009: Regex assertions are restricted and fail closed (ReDoS)
**Status:** Accepted (session 4)  
**Decision:** `regex` uses `re.search`. Patterns are validated when the `AssertionSpec` is built (so `case add` rejects them): max 200 chars; no backreferences/conditionals; at most one unbounded repeat (`*`, `+`, `{n,}`, counted through enclosing fixed repeats); no variable-width repeat inside another; no alternation inside a variable repeat; repeat counts <= 100 (nested product <= 10,000); estimated cost <= 2e8 steps. Matched text must be <= 5,000 chars, otherwise the assertion fails with a clear message (never truncated). `gt`/`lt` require a finite non-bool number for `expected` and a non-bool number as actual. A case may have at most 100 assertions.  
**Reason:** CPython `re` backtracks and cannot be interrupted, so safety must be preventive. Measured on 3.12.3: `a*a*b` on 2,000 chars took 1.8 s and >20 s on 5,000; `(a+)+b` is exponential. Fuzzing 570 accepted patterns against adversarial 5,000-char texts: slowest 0.73-0.81 s, none >30 s.  
**Limits / trade-offs:** Not a proof. The cost model is an estimate; it rejects some legitimate patterns (e.g. `\w+@\w+\.\w+`, `(foo|bar)+`). The "max 1 unbounded repeat" rule is redundant with the cost cap (mutation check showed tests pass without it); kept as an explicit defense. Parsing uses `re._parser`/`re._constants` (private, present in 3.11 and 3.12); if unavailable, regex is rejected entirely. A hard guarantee would need a linear-time engine (e.g. RE2) or a killable worker process; revisit with the adapter-isolation work.

## D-010: Adapter isolation design
**Status:** ACCEPTED 2026-10-18 (owner delegated the answers to the agent in chat: "jo best approach ho woh karo"; the owner may override any answer below). Full text: [ADAPTER_ISOLATION.md](ADAPTER_ISOLATION.md). (HANDOFF previously called this "D-008"; D-008 is the persistence decision.)  
**Decision (summary):** Adapters run user agents only as separate processes (argv list, no shell, absolute allow-listed executable with recorded SHA-256, empty env + named allow-list, temp cwd, wall-clock timeout with process-group kill, byte caps on stdout/stderr, POSIX rlimits) or as allow-listed HTTP endpoints (SSRF guard, no redirects, size caps). Commands/URLs come only from a local user-authored config, never from case files or traces; adapters are off by default; failures become recorded failed results; missing protections fail closed. Tier A (these guard rails) does **not** restrict filesystem/network access of the child; Tier B (bubblewrap/nsjail/container) does but needs an external tool.  
**Reason:** D-004 (traces are data) and D-007 (runner failures are results). The design is honest about what a plain subprocess cannot prevent.  
**Answers (see ADAPTER_ISOLATION.md section 10):** (1) Tier A ships first, labelled trusted-code-only, and a config entry must carry an explicit trust acknowledgement; (2) Tier B targets bubblewrap (optional external tool, never a Python dependency; Docker/podman only later, same fail-closed rule); (3) Linux implemented and tested first, macOS only after it is tested there, Windows refuses; (4) defaults 30 s / 1 MB / 512 MB kept, overridable per adapter up to the hard maxima.  
**Note:** acceptance does not mean implemented: nothing in D-010 is built or tested yet.

## D-011: License = MIT
**Status:** ACCEPTED 2026-10-18 (owner delegated the choice).  
**Decision:** MIT, "Copyright (c) 2026 AgentProof contributors".  
**Reason:** maximum adoption, shortest text, exactly reproducible. Apache-2.0 (explicit patent grant) was the alternative; its official text could not be fetched in the session (apache.org blocked by the egress proxy) and is not reproduced from memory. Switching before the first release or outside contributions is cheap; after contributions it needs contributor consent.

## D-012: Tier A adapter implementation choices (refines D-010)
**Status:** ACCEPTED 2026-10-18 (agent decision under the owner's delegation; the owner may override).  
**Decisions:** (1) The executable is resolved with `realpath` (symlinked entries such as `/usr/bin/python3` are allowed); the *resolved* path and its SHA-256 are recorded, and an optional `executable_sha256` pin must match. This replaces "path must exactly match the config entry", which would reject ordinary symlinked installs; the hash pin is the stronger check. (2) A resolved executable that is group/world writable is refused. (3) rlimits are applied by a small launcher (`python -I -S -c`, then `execve`) instead of `preexec_fn`, so the adapter is safe in threaded hosts such as the future HTTP API; a failed limit or exec fails the run. (4) stderr is kept up to `max_stderr_bytes` (truncated and flagged) and the child is killed after 10 MiB of stderr (flood rule). (5) `RLIMIT_NPROC` = tasks the user already has + 256 (best effort: not enforced for root, per-user not per-tree). (6) Output JSON: UTF-8, one object, depth <= 64, no NaN/Infinity. (7) The launcher (our own interpreter) receives the parent's `LD_LIBRARY_PATH` when it is set, because interpreters built with a shared libpython (GitHub setup-python, pyenv) cannot start without it; the launcher drops it before `exec`, so the agent never sees it unless `env_allow` names it, and `LD_LIBRARY_PATH` is the one `LD_*` name `env_allow` may list (LD_PRELOAD etc. stay refused). (8) The config path is always explicit; there is no implicit discovery of `agentproof.toml` in the working directory (a cloned repository must not be able to supply commands).  
**Reason:** each choice fails closed and was covered by a test or mutation check (TEST_EVIDENCE.md). **Not claimed:** any filesystem or network isolation (that is Tier B).

## D-013: Project name is taken (RESOLVED by D-016)
**Status:** RESOLVED 2026-10-24 by D-016 (renamed to RegressAgent). Original text below.  
**Facts observed:** PyPI `agentproof` already exists (0.1.0, "pytest-based behavioral testing framework for AI agents", github.com/praxiumlabs/agentproof): same space, so publishing under this name is impossible and would confuse users. GitHub search finds 151 repositories with "agentproof" in the name (e.g. NordicAgents/AgentProof, jayblast-spec/agentproof). PyPI treats `agent-proof` as a different name (404), but it is confusingly close and not recommended.  
**PyPI-free names found (PyPI only; GitHub, domain and trademark NOT checked):** `agentdiff`, `agentbaseline`, `agentregression`, `regressagent`, `agentreg`. Taken: `agentregress`, `agentcheck`, `replaykit`.  
**Needed from the owner:** keep the name for GitHub-only use, or rename (then: package, CLI command, env var `AGENTPROOF_DB`, docs, repo URL). Do not publish before this is decided.

## D-014: Trace v1 and `case from-trace`
**Status:** ACCEPTED (session 8, agent decision under the owner's delegation; owner may override).  
**Decision:** Minimal trace format `{trace_id, input, output}` (objects only, extra fields forbidden, 1 MB, depth 32, strict JSON). `case from-trace` generates `equals` assertions for scalar leaves of `output` (or explicit `--path`s), never copies secret-looking values (uses `redaction.Redactor`), refuses secret-looking `input`, skips unaddressable/too-long items with notes on stderr, and by default only prints the case so a human reviews it. No DB change (raw-trace persistence needs schema v3 and is a later task).  
**Reason:** delivers the "capture a behavior, turn it into a regression case" promise with the smallest safe surface; D-004 (data, not code).  
**Limits:** auto-generated equals assertions are brittle by design; multi-step traces (tool calls) and framework importers are not supported yet.

## D-015: HTTP adapter (implements ADAPTER_ISOLATION.md s4)
**Status:** ACCEPTED (session 8, agent decision under the owner's delegation; the owner may override).  
**Decision:** `[adapters.NAME] type = "http"` with `url`, `timeout_seconds`, `max_response_bytes`, `allow_hosts`, `allow_insecure_http`, `headers_env`. Loopback hosts need nothing else; any other host must be in `allow_hosts` and use https (or `allow_insecure_http`). Resolve once, check all addresses, pin the connection; always refuse link-local/unspecified/multicast/reserved; refuse non-loopback unless allow-listed. No redirects, TLS always verified, body read with a cap, a watchdog enforces one total deadline (DNS lookup time is not covered). Credentials only via env var names; URLs may not carry credentials or queries. No schema change: metadata reuses the v2 columns (`adapter_tier` `H`, `argv` = `["POST", url]`, `exit_code` = HTTP status, `stdout_truncated` = response cut at the cap, `env_names` = header variable names).  
**Limits:** an allow-listed host may resolve to private ranges (the owner listed it); no proxy support; no client certificates; no request retries; a hostile endpoint can still return plausible-looking wrong answers (that is what assertions are for).

## D-016: Rename AgentProof -> RegressAgent
**Status:** ACCEPTED (session 9, 2026-10-24). The owner said to choose a name ("apne hisab se"); the agent chose it. The owner may still override.  
**Name:** product **RegressAgent**, package/command **`regressagent`**. Chosen because it says what the tool does and was free where checked on 2026-10-24: PyPI `regressagent` = 404 (free), GitHub search for `regressagent` in repository names = 0 results. Other candidates were rejected for existing repos or PyPI packages (`agentdiff` 35 repos, `agentreg` 61, `agentsnap`/`agentpin`/`agentgate`/`agentlock`/`agentverdict` taken on PyPI). **Not checked:** trademarks, domain names, npm, other registries.  
**Breaking changes (nothing was ever released, so no migration is provided):** Python package `agentproof` -> `regressagent`; command `agentproof` -> `regressagent`; environment variable `AGENTPROOF_DB` -> `REGRESSAGENT_DB`; default database file `agentproof.db` -> `regressagent.db` (existing databases keep working with `--db`); conventional config name `agentproof.toml` -> `regressagent.toml` (the path is always passed explicitly); example config renamed; User-Agent is `regressagent/<version>`; report titles read "RegressAgent report". Decisions and logs written before this date (D-001..D-015, TEST_EVIDENCE, SESSION_LOG, CHANGELOG) keep the old names on purpose: they record what was run then.  
**Done 2026-10-31:** the owner renamed the GitHub repository to `RegressAgent` (GitHub keeps redirects from the old URL); `origin`, the two `[project.urls]` lines and `tests/test_packaging.py` were updated. The old PyPI name `agentproof` belongs to someone else: the README warns not to install it.

## D-017: Raw trace persistence (schema v3)
**Status:** ACCEPTED (session 9, agent decision under the owner's delegation; the owner may override).  
**Decision:** migration v3 adds `traces(trace_id PK, trace_json, trace_hash, redacted, created_at, updated_at)`. A trace is validated (D-014 rules), passed through `redaction.Redactor`, stored as canonical sorted JSON, and the hash is of the stored (redacted) form; `redacted` says whether anything changed. Same-id saves are refused unless `--overwrite`; deleting needs `--yes`. Generated cases skip any value that contains a `[REDACTED` marker. Traces are independent of cases and runs (no foreign keys).  
**Reason:** keeps captured behaviour for later case generation/replay without ever writing secrets to disk (same policy as run results).  
**Limits:** redaction is best-effort pattern matching; a trace is stored whole (no size beyond the 1 MB trace cap); multi-step traces and replay do not exist yet.

## D-018: Multi-step traces and tool-call assertions
**Status:** ACCEPTED (session 9; the owner said to do all non-optional remaining work and may override).  
**Decision:** a trace may carry `steps: [{tool, arguments, result}]` (max 200, strict model). Agents expose their tool calls to assertions by returning a `steps` list in their output (convention, no new protocol). New operators on that list: `tool_called`, `tool_not_called` (fail closed when any step is malformed), `tool_order` (subsequence, 1-50 names). `case from-trace` addresses steps through a view (`output` plus `steps` = tool + arguments only), generates `tool_order` and argument-equality assertions, never asserts on `result`, skips secrets/redacted values as before, and refuses a trace whose output already has a `steps` key. Steps are stored (redacted) with the trace.  
**Reason:** agents usually break in what they do, not what they say; the demo `support` regression has identical text and only the tool assertions catch it.  
**Limits:** only tool calls are modelled (no LLM-call or retrieval steps); no argument matching operators beyond `equals` on paths; no replay; `tool_order` is limited to 50 names (longer traces note this).

## D-019: Conversation importers (OpenAI chat, Anthropic messages)
**Status:** ACCEPTED (session 9; owner may override).  
**Decision:** `importers.convert(fmt, data, trace_id)` maps a recorded conversation to trace v1 (input = messages before the first assistant turn (+ system prompt for Anthropic), output = `{"message": final assistant text}`, steps = tool calls with results as text). Pure data conversion with strict JSON (no NaN), message/step limits and a mandatory final assistant answer; the CLI re-validates the result with `parse_trace`. `trace convert` prints by default and stores (redacted) only with `--add`.  
**Limits:** two formats only; streaming/delta formats, images and non-text tool results are not interpreted (text parts only); the mapping is a convention, not a replay of the original run.
## D-020: Local HTTP API
**Status:** ACCEPTED (session 9; the owner said to decide and finish everything; may override).  
**Decision:** stdlib `ThreadingHTTPServer`, bound only to loopback (127.0.0.1, ::1, localhost; anything else is refused). A random 256-bit-ish bearer token is generated per start and printed once; compared in constant time; never accepted in the query string. Host must equal the listening loopback address and port (DNS rebinding), `Origin` must be the same origin if present, `Sec-Fetch-Site` must be same-origin/none (CSRF); no CORS headers are ever sent. Checks run before the body is read; bodies need Content-Length and are capped at 1 MB; JSON must be an object, strict (no NaN), depth-limited. Unexpected exceptions return a generic 500. Only GET/POST/DELETE; deletes need `confirm=yes`. The API can run only the built-in demo runners: adapters (which execute commands or call the network) are CLI-only.  
**Reason:** a local port is reachable by every local process and, via the browser, by any web page; these are the standard defences.  
**Limits:** no TLS (loopback only); the token is printed to the terminal; the page/JS in a browser tab holds it in memory; no rate limiting; no multi-user model.
