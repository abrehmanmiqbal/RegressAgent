"""The same example agent behind HTTP, for the http adapter. Dependency-free, loopback only.

Run:  python examples/agents/greeter_http.py [--bug]      (listens on 127.0.0.1:8000, POST /agent)
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


def make_server(host: str = "127.0.0.1", port: int = 8000, bug: bool = False) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep test and demo output quiet
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            name = request.get("name", "stranger")
            greeting = "Hi" if bug else "Hello"  # "Hi" is the regression
            body = json.dumps({"message": f"{greeting}, {name}!", "action": "greet"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return HTTPServer((host, port), Handler)


if __name__ == "__main__":
    server = make_server(bug="--bug" in sys.argv)
    print(f"listening on http://127.0.0.1:{server.server_address[1]}/agent", file=sys.stderr)
    server.serve_forever()
