# Publishing RegressAgent to PyPI (free)

Why: today people must clone the repository; after this they can run `pip install regressagent`.
Nothing here has been done yet: it needs **your** PyPI account (the assistant has no credentials).

## One-time setup
1. Create accounts on https://test.pypi.org and https://pypi.org (enable 2FA on both).
2. Confirm the name is still free: https://pypi.org/project/regressagent/ should say "Not Found" (it was free on 2026-10-24).
   Names are first come, first served: publish soon after checking.

## Build and check (works from a clone, Python 3.11+)
```bash
pip install build twine
python -m build            # creates dist/regressagent-0.1.0-py3-none-any.whl and .tar.gz
twine check dist/*         # both must say PASSED
```

## Rehearse on TestPyPI first
1. TestPyPI, Account settings, **API tokens**, create a token (scope: entire account for the first upload).
2. ```bash
   twine upload --repository testpypi dist/*      # username: __token__   password: the token
   python -m venv /tmp/t && /tmp/t/bin/pip install --index-url https://test.pypi.org/simple/ regressagent
   /tmp/t/bin/regressagent demo
   ```

## Real upload
1. PyPI, Account settings, **API tokens**, create a token. After the first upload, replace it with a token scoped to the project `regressagent`.
2. ```bash
   twine upload dist/*
   ```
3. Check https://pypi.org/project/regressagent/ and `pip install regressagent` in a fresh environment.
4. Then update `README.md` and `docs/index.html` (they currently say "not on PyPI yet"; tests enforce that wording, so update `tests/test_site.py` and `tests/test_packaging.py` in the same change).

## Safety notes
- Never commit the token; revoke it after the upload if you do not need it.
- Uploaded files cannot be replaced: a mistake needs a new version number (bump `version` in `pyproject.toml` and `src/regressagent/__init__.py`).
- Prefer PyPI "trusted publishing" (GitHub Actions) later; it avoids long-lived tokens but needs a workflow.
