from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
from urllib.request import Request, urlopen


class Receiver(BaseHTTPRequestHandler):
    accepting = False
    rejected = 0
    spans: set[str] = set()
    private_attribute_received = False

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "rejected": Receiver.rejected,
            "receivedSpans": len(Receiver.spans),
            "privateAttributeReceived": Receiver.private_attribute_received,
        }).encode("utf-8"))

    def do_POST(self) -> None:
        payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/allow":
            Receiver.accepting = True
        elif self.path == "/api/internal/otel/v1/traces":
            if not Receiver.accepting:
                Receiver.rejected += 1
                self.send_response(503)
                self.end_headers()
                return
            for resource in json.loads(payload).get("resourceSpans", []):
                for scope in resource.get("scopeSpans", []):
                    for span in scope.get("spans", []):
                        Receiver.spans.add(span["spanId"])
                        Receiver.private_attribute_received |= any(
                            attribute.get("key") == "command" for attribute in span.get("attributes", [])
                        )
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_arguments) -> None:
        pass


def main() -> None:
    action = sys.argv[1]
    if action == "serve":
        ThreadingHTTPServer(("0.0.0.0", 8000), Receiver).serve_forever()
        return
    if action == "send":
        request = Request("http://collector:4318/v1/traces", data=sys.stdin.buffer.read(), headers={"Content-Type": "application/json"})
    elif action == "allow":
        request = Request("http://127.0.0.1:8000/allow", data=b"")
    elif action == "collector-ready":
        request = Request("http://collector:13133/")
    else:
        request = Request("http://127.0.0.1:8000/status")
    with urlopen(request, timeout=5) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()