"""Real Chromium + axe checks for the local UI; all data is generated in a temporary SQLite DB."""
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from regressagent.api import make_server

UI_TESTS = Path(__file__).resolve().parent.parent / "ui-tests"


def _browser_path() -> str | None:
    """Return an installed Chromium path, installing locked Node test deps if needed."""
    if shutil.which("node") is None or shutil.which("npm") is None:
        return None

    modules = UI_TESTS / "node_modules"
    if not (modules / "playwright").is_dir() or not (modules / "axe-core").is_dir():
        try:
            done = subprocess.run(
                ["npm", "ci", "--no-audit", "--no-fund"],
                cwd=UI_TESTS,
                capture_output=True,
                text=True,
                timeout=240,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if done.returncode != 0:
            return None

    configured = os.environ.get("REGRESSAGENT_CHROMIUM_PATH")
    candidates = [
        configured,
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
    ]
    try:
        done = subprocess.run(
            ["node", "--input-type=module", "-e", "import { chromium } from 'playwright'; console.log(chromium.executablePath())"],
            cwd=UI_TESTS,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        done = None
    if done is not None and done.returncode == 0:
        candidates.append(done.stdout.strip())
    return next((str(Path(item)) for item in candidates if item and Path(item).is_file()), None)


CHROMIUM = _browser_path()
pytestmark = pytest.mark.skipif(
    CHROMIUM is None,
    reason="Node/Playwright/Axe or Chromium unavailable (install UI deps and `npx playwright install chromium`)",
)


def test_ui_in_real_chromium_has_no_axe_wcag_violations(tmp_path):
    server, token = make_server(str(tmp_path / "ui-browser.db"), "127.0.0.1", 0)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        env = {
            **os.environ,
            "UI_BASE": f"http://127.0.0.1:{server.server_address[1]}",
            "UI_TOKEN": token,
            "REGRESSAGENT_CHROMIUM_PATH": CHROMIUM or "",
        }
        try:
            done = subprocess.run(
                ["node", "a11y.browser.mjs"],
                cwd=UI_TESTS,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            pytest.fail(f"real-browser accessibility audit timed out: {exc}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert done.returncode == 0, done.stdout[-6000:] + done.stderr[-3000:]
    assert "8 real-Chromium states" in done.stdout
    assert "zero violations" in done.stdout
