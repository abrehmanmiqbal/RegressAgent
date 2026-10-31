"""A tiny example agent for the command (Tier A) adapter. Dependency-free.

Contract: read ONE JSON object from stdin, print ONE JSON object to stdout, exit 0.
Set GREETER_BUG=1 (and list it in env_allow) to simulate a bad deploy.
"""
import json
import os
import sys


def greet(request: dict) -> dict:
    name = request.get("name", "stranger")
    if os.environ.get("GREETER_BUG") == "1":
        return {"message": f"Hi, {name}!", "action": "greet"}  # the regression
    return {"message": f"Hello, {name}!", "action": "greet"}


if __name__ == "__main__":
    print(json.dumps(greet(json.loads(sys.stdin.read()))))
