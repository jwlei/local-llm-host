"""
python -m local_llm_host [--host 0.0.0.0] [--port 8810]

Starts the chat. On 127.0.0.1 (the default) only this PC can open it. --host 0.0.0.0 lets your other devices in,
on your home network and over Tailscale, and needs APP_PASSWORD.
"""
import argparse
import atexit
import logging
import socket
import sys
import threading

from local_llm_host import tailscale
from local_llm_host.app import create_app, lan_ip
from local_llm_host.config import Config

PORT = 8810


class _QuietPolling(logging.Filter):
    """Leaves the page's status checks out of the access log, which they would bury."""

    def filter(self, record):
        return "/api/status" not in record.getMessage()


def connect_tailscale(port):
    url, problem = tailscale.connect(port)
    if url:
        print(f"Anywhere:     {url}  (Tailscale)", flush=True)
    elif problem:
        print(f"{problem} Your other devices can only reach it on this network until it connects.", flush=True)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="local_llm_host", description="Chat with local GGUF models from any of your devices.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1; 0.0.0.0 for other devices).")
    parser.add_argument("--port", type=int, default=PORT, help=f"Port (default {PORT}).")
    parser.add_argument("--no-tailscale", action="store_true", help="Don't start or reconnect Tailscale.")
    args = parser.parse_args(argv)

    try:
        config = Config.from_env()
    except ValueError as e:
        parser.error(str(e))
    if args.host not in ("127.0.0.1", "localhost", "::1") and not config.password:
        parser.error("Set APP_PASSWORD in .env before opening this to other devices (--host).")
    if not config.secret_key:
        print("FLASK_SECRET_KEY isn't set: sign-ins end when the app restarts.", flush=True)
    if not config.llama_server:
        print("LLAMA_SERVER isn't set: the chat opens, but can't start a model until it is.", flush=True)

    app = create_app(config)
    app.config["BOUND_HOST"] = args.host
    runner = app.extensions["local_llm_host"]["runner"]
    atexit.register(runner.unload)
    logging.getLogger("werkzeug").addFilter(_QuietPolling())

    print(f"This PC:      http://127.0.0.1:{args.port}", flush=True)
    if args.host == "0.0.0.0":
        print(f"Home network: http://{lan_ip()}:{args.port}  (or http://{socket.gethostname()}.local:{args.port})", flush=True)
        if not args.no_tailscale:
            threading.Thread(target=connect_tailscale, args=(args.port,), daemon=True).start()
    print("Stop it with Shut down in the menu, or Ctrl+C here.", flush=True)
    try:
        app.run(host=args.host, port=args.port, threaded=True)
    finally:
        runner.unload()


if __name__ == "__main__":
    main()
