# Session Log

> **Naming note (D-016):** the project was renamed from AgentProof to RegressAgent on 2026-10-24. Entries below that mention `agentproof`, `AGENTPROOF_DB` or `agentproof.toml` record what was actually run under the old name and are kept as written.

## 2026-10-17 (session 1)
- Repository was empty. Added AGENTS.md, memory docs, and first Python core slice (unexecuted).

## 2026-10-17 (session 2)
- Cloned repo, built venv, ran tests (5/5) and demo (exit 0) — first real execution of the code.
- Probed for defects; found and fixed (test-first) 3 core bugs plus 1 corrupt-DB crash.
- Added SQLite persistence (versioned migrations, hashing, size limits), comparison engine, runner registry (`local`, `local-legacy`), CLI subcommands, ruff in dev/CI.
- Tests: 5 -> 44, all passing on Python 3.12.3. Ruff clean.
- BLOCKED on push: no GitHub credentials. Delivered git bundle + patch series instead.
- Next: reports (Markdown/JSON export), run-all, repeat-run flakiness, adapter isolation design. See HANDOFF.md.

## 2026-10-18 (session 4)
- Step 0: CI on 161104d observed green (3.11 + 3.12) via GitHub API.
- Added `gt`/`lt`/`regex` operators with ReDoS guards (D-009), 100-assertion cap, fuzz script; tests 69 -> 130.
- README CLI documentation + `examples/` (3 cases, runnable script, tests); tests 130 -> 137.
- `run-all --repeat`; oversized-output fix; docs/THREAT_MODEL.md; tests 137 -> 142.
- Adapter-isolation design (docs only) + D-010 proposed; HANDOFF rewritten. LICENSE intentionally NOT added (owner must choose).

## 2026-10-18 (sessions 5 and 6: blocked by environment)
- Session 5 changed nothing in the repo. Session 6 only added these notes.
- Blocker 1: `pip download pydantic` fails with "Name or service not known" (pypi.org is in the proxy NO_PROXY list, so pip connects directly and DNS does not resolve; session 5 saw a proxy 403). No dependencies could be installed, so pytest and `agentproof demo` could NOT be run.
- Blocker 2: GitHub API call with the owner token answered "GitHub access to this repository is not enabled for this session"; `add_repo` with access "push" answered "link your GitHub account". CI status and pushing are not possible. Anonymous `git clone` / `git ls-remote` work.
- Not worked around (no mirrors, proxy and TLS settings untouched). No code written, because it could not be tested.
- Session 6 (later): owner linked GitHub, repo attached, docs commit pushed (1e71429, `git ls-remote` == HEAD). Owner delegated decisions: LICENSE = MIT (D-011), D-010 accepted with answers. Added LICENSE and CONTRIBUTING.md. Still no code: pip remained blocked, so no tests could be run.

## 2026-10-18 (session 7)
- Pip works again. Step 0 done: CI green for the 8 newest runs (run level), baseline re-verified locally on Python 3.13.16: 142 tests, ruff clean, demo and example script OK.
- Task 1: DB migration v2 with run metadata (`RunMetadata`), atomic migrations, negative-version guard, real v1 -> v2 tests with mutation checks; tests 142 -> 161.
- Task 2: best-effort secret redaction (`redaction.py`): env-name values, known token formats, secret-named assignments/keys, URL passwords, PEM; one chokepoint in `Repository.record_run`; hostile-input and ReDoS-timing tests; CLI prints the stored (redacted) result. tests 161 -> 227. Limits documented honestly.
- Task 3a: `agentproof.toml` loader (`config.py`), test-first (collection error first, then 79 tests green; 306 total). Parse/validate only. Mutation checks: 8 of 10 caught at first; tier-B message mutant caught after tightening the test; the file-size pre-check mutant is equivalent (the post-read cap still refuses) and was left.
- Task 3b: hostile test agent (`tests/hostile_agent.py`, ~30 modes) and Tier A adapter (`subprocess_adapter.py`), tests written first (collection error), then 50 adapter tests green; 356 total. 20 mutation checks run; see TEST_EVIDENCE for survivors. D-012 records refinements. Adapter is not wired into the CLI yet.

## 2026-10-20 (session 8)
- Found main had moved (sessions 5-7) and CI was red. Added CI failure annotations, reproduced locally with a group-writable interpreter (44 failed), fixed the test helper, CI green on 8a3e726. Installed Python 3.11 locally via uv: 359 passed.
- CLI wiring for subprocess adapters (`--adapter/--config`), README section, 11 tests, mutation-checked; tests 359 -> 370.
- Clean-install verification (3.11/3.12, editable + wheel), packaging metadata, twine check, name check: PyPI `agentproof` is taken (D-013 OPEN).
- Trace v1 + `case from-trace` (D-014), path-length cap, example trace; tests 376 -> 410; 5 mutations caught.
- `matches_type` operator (strict JSON types); tests 410 -> 450.
- HTTP adapter (D-015) with SSRF guard/pinning/watchdog, example config, README; tests 450 -> 548; 6 mutations caught; found+fixed a watchdog bug via the trickle test.

## 2026-10-24 (session 9)
- Owner delegated the name; chose RegressAgent (D-016) after PyPI/GitHub checks; renamed package, command, env var, defaults, docs; tests 548 -> 551; wheel and clean-install re-verified on 3.12 and 3.11.
- Schema v3 trace store (redacted before write), `trace` commands, `case from-trace --stored`, example agents; tests 552 -> 569; found+fixed partial-redaction bug.
- Multi-step traces (D-018): steps, tool_* operators, support demo + examples; tests 569 -> 633; 5 mutations caught.
- Assertion path limits; importers (OpenAI/Anthropic) + `trace convert`; tests 633 -> 671.
- Local HTTP API + `serve` (D-020); tests 672 -> 718; 8 security mutations caught.
- Local UI + jsdom e2e tests; fixed a fresh-DB migration race and 3 UI/API bugs found by them; tests 718 -> 744.
