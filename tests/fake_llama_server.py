"""
A stand-in for llama.cpp's llama-server, for the tests: it takes the same arguments, answers /v1/models, and streams
a chat reply the way llama-server does. The reply echoes the last message; each request is appended, as JSON, to the
file in FAKE_LLAMA_RECORD, with the arguments it was started with.
"""
import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--alias", default="model")
parser.add_argument("--fail", action="store_true", help="Exit at once, as a server that can't load its model does.")
args, _ = parser.parse_known_args()
if args.fail:
    print("error: failed to load model", flush=True)
    sys.exit(1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _json(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/v1/models":
            self._json({"data": [{"id": args.alias}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        record = os.getenv("FAKE_LLAMA_RECORD")
        if record:
            with open(record, "a", encoding="utf-8") as f:
                f.write(json.dumps({"argv": sys.argv[1:], "body": body}) + "\n")
        last = body["messages"][-1]["content"]
        text = last if isinstance(last, str) else " ".join(p.get("text", "[image]") if p["type"] == "text" else "[image]" for p in last)
        if "CHINESE ONLY" in text:
            pieces = ["你好", "世界"]
        else:
            pieces = ["Echo: ", *text.split(" ")]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for index, piece in enumerate(pieces):
            if index > 1:
                piece = " " + piece
            chunk = {"choices": [{"delta": {"content": piece}, "finish_reason": None}],
                     "timings": {"predicted_per_second": 42.0}}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
        finish = "length" if "CUT OFF" in text else "stop"
        self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': finish}]})}\n\n".encode())
        usage = {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": len(pieces)}}
        self.wfile.write(f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n".encode())
        self.wfile.flush()


ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
