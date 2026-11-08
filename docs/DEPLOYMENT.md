# Where and how to publish RegressAgent for free

RegressAgent is a **local-first tool**. The CLI, API and UI are meant to run on the user's own machine. There is no
"hosted version", on purpose (see the threat model: loopback-only API, a one-time token printed in the terminal, no TLS,
a single-user model, and adapters that run commands or call endpoints from a local config).

## What to publish (all free)
| What | Where | Why |
|---|---|---|
| Source and releases | GitHub (done: https://github.com/abrehmanmiqbal/RegressAgent, release v0.1.0) | the code, history, CI |
| Project website | **GitHub Pages**, from the `docs/` folder (`docs/index.html`) | a presentable landing page for LinkedIn/CV |
| `pip install regressagent` | **PyPI** (see `docs/PUBLISHING.md`) | the single biggest adoption blocker today |

## GitHub Pages (project website), 1 minute
1. Repository **Settings, Pages**.
2. Source: **Deploy from a branch**, branch **main**, folder **/docs**, Save.
3. After about a minute the site is at `https://abrehmanmiqbal.github.io/RegressAgent/`.
(Free for public repositories. The site is plain HTML/CSS with no JavaScript and no third-party requests.)

## What NOT to do
Do **not** run `regressagent serve` on Render, Railway, Fly.io, a Hugging Face Space or any public server. It would expose
a tool that runs commands and stores agent data behind a single token without TLS, rate limiting or user accounts. If you
ever want a hosted demo, build a separate static "demo mode" UI with fake data; do not host the real server.

## Optional later
- A Docker image on GitHub Container Registry (free for public images) for people who do not want to install Python.
- A GitHub Action so teams can run RegressAgent in CI (needs a token that may edit workflows).
