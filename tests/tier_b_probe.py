"""A probe agent for the Tier B sandbox tests: it TRIES to escape and reports what worked.
Stdlib only. Input: {"mode": ..., ...}; output: a JSON object describing the attempt."""
import json
import os
import socket
import sys


def attempt(fn):
    try:
        return {"ok": True, "value": fn()}
    except BaseException as exc:  # noqa: BLE001 - report everything
        return {"ok": False, "error": type(exc).__name__}


def main() -> dict:
    req = json.loads(sys.stdin.read())
    mode = req["mode"]
    if mode == "hello":
        return {"status": "ok", "cwd": os.getcwd()}
    if mode == "net":
        def connect():
            with socket.create_connection((req["host"], req["port"]), timeout=2):
                return True
        return attempt(connect)
    if mode == "read":
        return attempt(lambda: len(open(req["path"], "rb").read()))
    if mode == "write":
        def write():
            with open(req["path"], "wb") as handle:
                handle.write(b"x")
            return True
        return attempt(write)
    if mode == "env":
        return {"keys": sorted(os.environ)}
    if mode == "pids":
        return {"count": len([d for d in os.listdir("/proc") if d.isdigit()]), "pid": os.getpid()}
    if mode == "caps":
        with open("/proc/self/status", encoding="ascii") as handle:
            status = handle.read()
        return {"cap_eff": next(x.split()[1] for x in status.splitlines() if x.startswith("CapEff"))}
    if mode == "ls":
        return attempt(lambda: sorted(os.listdir(req["path"])))
    if mode == "tmp":
        with open("/tmp/probe-file", "wb") as handle:
            handle.write(b"data")
        return {"exists": os.path.exists("/tmp/probe-file")}
    if mode == "userns":
        def make_userns():
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            return libc.unshare(0x10000000) == 0  # CLONE_NEWUSER
        return attempt(make_userns)
    return {"error": "unknown mode"}


if __name__ == "__main__":
    print(json.dumps(main()))
