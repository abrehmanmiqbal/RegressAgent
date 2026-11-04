# Adapter Isolation Design (D-010)

**Status: ACCEPTED (D-010, 2026-10-18; owner delegated the answers). Implementation in progress: section 8 step 1 (DB migration v2 + `RunMetadata`) step 2 (`regressagent.toml` loader, parse/validate only) and step 3 (hostile test agent + Tier A subprocess adapter, `src/regressagent/subprocess_adapter.py`, Linux only) are done and tested (session 7, see D-012 for refinements of this design); CLI wiring, HTTP adapter and Tier B are still unimplemented and untested.** No adapter that runs user code, commands, or network calls may be written until the owner has reviewed this document and its open questions (end of file). Environment facts checked in session 4 (Linux 6.18, container, uid 0): `resource`, `os.killpg`, `tomllib` (stdlib, Python 3.11+) are available; `unshare` exists; `bwrap`, `docker`, `podman`, `nsjail`, `firejail` are **not** installed here, so no sandbox tier below has been tried.

## 1. Problem
Today only built-in demo runners exist. A real adapter would start the user's agent (a command or an HTTP endpoint) and feed it recorded inputs. The agent is untrusted-ish: buggy, nondeterministic, possibly compromised, and its output can be hostile. The tool itself must not become a way to run attacker-controlled code on the user's machine (D-004).

## 2. Hard rules (apply to every adapter)
1. **Case files and traces never contain commands, URLs, paths to executables or environment variables.** They contain only the agent *input* and assertions. A case refers to an adapter by *name*; the mapping name -> command lives in a separate, local, user-authored config file (`regressagent.toml`, parsed with stdlib `tomllib`), never in the database or imported data.
2. **Adapters are off by default.** Using one requires the config entry plus an explicit opt-in at run time (e.g. `--adapter NAME`); there is no implicit discovery.
3. **No in-process execution of user code** (no `import`/`exec` of user modules, no plugin loading). Isolation is by process boundary only.
4. **No shell.** Commands are an argv list with `shell=False`; the executable must be an absolute path (resolved with `realpath`) that exactly matches the config entry; its SHA-256 is recorded with each run.
5. **Everything coming back is untrusted data** (D-004, D-007): bounded, parsed as JSON only, never evaluated, never fed to tools or prompts, and any failure becomes a *failed recorded result* with an `error`, not a crash.
6. **Fail closed.** If a requested protection is unavailable (platform, missing tool), the run is refused, not silently weakened.

## 3. Subprocess adapter
### Tier A: baseline guard rails (honest scope)
Process-level limits and hygiene. **Tier A does not stop the child from reading files or using the network as the invoking user.** It is only appropriate for code the user would be willing to run by hand, and docs/CLI must say so.
- **stdin/stdout contract:** one JSON object in on stdin; one JSON object out on stdout; exit code 0 required.
- **Timeout:** wall-clock, default 30 s, hard max 300 s; on expiry kill the whole process group (`start_new_session=True`, then `os.killpg` SIGTERM -> short grace -> SIGKILL) and reap it. No orphan children.
- **Output caps:** read stdout/stderr incrementally with a byte cap (stdout <= 1,000,000 to match the storage cap; stderr <= 64 KB, truncated and flagged); exceeding the cap kills the process and records a failed result. JSON parsing with a depth limit; top level must be an object.
- **Environment:** start from an empty environment plus an explicit allow-list of variable *names* from the config (values read from the parent at run time, never stored; only the names are recorded). `PATH` is fixed. No inherited secrets (tokens, cloud credentials).
- **Working directory:** a fresh temporary directory, deleted after the run; stdin never a TTY.
- **Resource limits (POSIX):** `RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_FSIZE`, `RLIMIT_NOFILE`, `RLIMIT_NPROC`, `RLIMIT_CORE=0`, applied in the child before exec. Caveats to test: `preexec_fn` is not safe with threads (prefer a tiny launcher or `prlimit`), `RLIMIT_NPROC` is per-user not per-process-tree, `RLIMIT_AS` can break some runtimes.
- **Windows:** no rlimits/process groups equivalent without Job Objects; adapters are **unsupported on Windows** until a Job Object design exists (refuse, do not degrade).

### Tier B: sandbox (IMPLEMENTED in session 9, see D-022; the text below is the original design)
Run the Tier A command inside an external sandbox: network disabled, read-only filesystem except the temp dir, no access to the home directory, dropped capabilities. Candidates: `bwrap` (bubblewrap), `nsjail`, or a container runtime (`docker`/`podman`). Requirements: fail closed if the configured sandbox binary is absent; the sandbox command line is built by RegressAgent from structured config (never from case data); one integration test per promised property (e.g. "child cannot read `~/.ssh`", "child cannot open a socket"). Not available on this machine, so **untested**; adding a dependency on an external tool is an owner decision (question 2).

## 4. HTTP adapter (IMPLEMENTED in session 8, see D-015; this section is the original design)
- Target comes from config only; default allow-list is `localhost`/`127.0.0.1`. Other hosts need an explicit entry.
- **SSRF guard:** resolve the host, refuse loopback/private/link-local/metadata ranges unless explicitly allow-listed, re-check after redirects; redirects off by default.
- TLS verification always on; request/response size caps; connect + total timeouts.
- Credentials are never stored in config or the database: the config names an environment variable; header values are redacted from logs and stored metadata.
- Uses the stdlib (`http.client`/`urllib`) unless a dependency is justified (AGENTS.md).

## 5. Reproducibility metadata (needs DB migration v2)
Each run records: adapter name, tier, executable path and SHA-256 (or URL host), argv, env variable *names*, timeout and limits, exit code, duration, whether stdout/stderr were truncated. This also gives the first real v1 -> v2 migration test (TASKS).

## 6. Secrets and output hygiene
Before storing output/stderr, run the redaction pass planned in TASKS (not built yet). Until that exists, real adapters must carry a warning that stored outputs may contain secrets (THREAT_MODEL #8).

## 7. Test plan (must pass in CI on Linux before any adapter merges)
A purpose-built **hostile test agent** (a small Python script in `tests/`) with modes, each asserting a *failed recorded result* and a clean exit, never a hang or crash:
infinite loop (timeout kills it, no leftover processes); fork bomb / many children (process group killed); stdout flood beyond cap; stderr flood; endless output without newline; invalid UTF-8; non-JSON; JSON array instead of object; 5,000-deep JSON; huge string; exit code != 0 with valid JSON; ignores SIGTERM (SIGKILL path); prints its environment (assert no secrets or unlisted variables leak); tries to write outside the temp dir (documented as *allowed* in Tier A, asserted *blocked* in Tier B); symlink tricks in the temp dir; slow trickle output (total timeout still applies). Also: executable path mismatch / hash change is refused; config file missing/invalid is a clean exit 2; adapter use without opt-in is refused.

## 8. Implementation order (each step its own commit, test-first)
1. DB migration v2 + run metadata model (no execution).
2. `regressagent.toml` loader with strict validation (no execution).
3. Hostile test agent + Tier A subprocess adapter, Linux only, tests above.
4. CLI wiring (`--adapter`), README "what Tier A does NOT protect against" section.
5. HTTP adapter with SSRF tests.
6. Tier B, only after owner sign-off on question 2.

## 9. Non-goals
Protecting against a kernel exploit, hardware side channels, or a malicious *user*; running adapters on Windows in the first version; deterministic replay of nondeterministic agents (we measure it with `--repeat`, we do not enforce it).

## 10. Open questions for the owner
1. Is "Tier A is for trusted code only" acceptable as the first shipped tier, or should the first adapter wait for Tier B?
2. May Tier B depend on an external tool (bubblewrap/Docker)? Which one is acceptable on your machines?
3. Linux/macOS only at first (no Windows adapters)?
4. Default limits: 30 s timeout, 1 MB stdout, 512 MB address space: OK?

### Answers (2026-10-18, accepted via D-010; owner may override)
1. **Tier A first: yes, as "trusted code only".** The CLI and docs must say so, and an `regressagent.toml` adapter entry is refused unless it carries an explicit trust acknowledgement key. Tier A must never be described as a sandbox. Tier B follows as a separate milestone.
2. **Tier B target: bubblewrap** (unprivileged, no daemon, small). It is an optional external tool, never a Python dependency; if it is missing the run is refused (fail closed). Docker/podman may be added later under the same rule. Each promised property needs its own integration test, and none can be claimed until bubblewrap is available and tested (not installed in the dev environment so far).
3. **Platforms: Linux implemented and tested first.** macOS only after tests run on macOS (RLIMIT_AS is unreliable there). Windows: refuse, do not degrade.
4. **Limits: 30 s timeout, 1 MB stdout, 512 MB address space kept as defaults.** Overridable per adapter in config up to the hard maxima (timeout 300 s); RLIMIT_AS may need raising for Node/JVM agents, which must be documented.
