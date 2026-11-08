"""`regressagent ... | head -1` must end quietly (no traceback) when the reader closes early."""
import subprocess
import sys

START = [sys.executable, "-c", "from regressagent.cli import main; raise SystemExit(main())"]


def test_closed_stdout_is_not_a_traceback(tmp_path):
    db = str(tmp_path / "p.db")
    case = tmp_path / "c.json"
    case.write_text('{"case_id": "p", "name": "p", "input": {}, "assertions": [{"path": "a", "operator": "exists"}]}')
    subprocess.run([*START, "--db", db, "case", "add", str(case)], check=True, capture_output=True)
    proc = subprocess.Popen([*START, "--db", db, "case", "show", "p"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.stdout.close()  # the reader goes away before we print
    err = proc.stderr.read().decode()
    code = proc.wait(timeout=20)
    proc.stderr.close()
    assert "Traceback" not in err and "BrokenPipeError" not in err
    assert code in (0, 141)
