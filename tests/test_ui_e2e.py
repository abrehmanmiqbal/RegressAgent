"""Runs ui-tests/ui.test.mjs (real app.js in jsdom against a real API server). Skipped when node or
jsdom cannot be installed (``npm ci`` in ui-tests/ runs automatically). NOT a real browser."""
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from regressagent.api import make_server

UI_TESTS = Path(__file__).resolve().parent.parent / "ui-tests"



def _jsdom_ready() -> bool:
    """True when node and jsdom are available; installs jsdom with ``npm ci`` (pinned by the lock
    file) on first use, so CI needs no extra workflow step."""
    if shutil.which("node") is None:
        return False
    if (UI_TESTS / "node_modules" / "jsdom").is_dir():
        return True
    if shutil.which("npm") is None:
        return False
    done = subprocess.run(
        ["npm", "ci", "--no-audit", "--no-fund"], cwd=UI_TESTS, capture_output=True, timeout=240, check=False
    )
    return done.returncode == 0 and (UI_TESTS / "node_modules" / "jsdom").is_dir()


pytestmark = pytest.mark.skipif(not _jsdom_ready(), reason="node/npm or jsdom unavailable (run `npm ci` in ui-tests/)")


def test_ui_end_to_end_in_jsdom(tmp_path):
    server, token = make_server(str(tmp_path / "ui.db"), "127.0.0.1", 0)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True).start()
    try:
        env = {**os.environ, "UI_BASE": f"http://127.0.0.1:{server.server_address[1]}", "UI_TOKEN": token}
        done = subprocess.run(
            ["node", "--test", "ui.test.mjs"], cwd=UI_TESTS, env=env,
            capture_output=True, text=True, timeout=180, check=False,
        )  # fmt: skip
    finally:
        server.shutdown()
        server.server_close()
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
    assert "# fail 0" in done.stdout and "# pass 10" in done.stdout
