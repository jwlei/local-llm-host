"""
Double-click to open local-llm-host.

Starts the app in the background if it isn't running yet (no console window; its output goes to
data/logs/app.log) and opens it in your browser, unless --headless is passed. With APP_PASSWORD set in .env, it
listens for your other devices too (home network and Tailscale); without, only this PC. Stop it with Shut down
in the app's menu.

The .pyw extension makes Windows run this with pythonw, which has no window.
"""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 8810
URL = f"http://localhost:{PORT}"


def running():
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/login", timeout=2)
        return True
    except urllib.error.HTTPError:
        return True  # It answered, just not with 200
    except Exception:
        return False


def password_set():
    try:
        from dotenv import dotenv_values
    except ImportError:
        return bool(os.getenv("APP_PASSWORD"))
    return bool(os.getenv("APP_PASSWORD") or dotenv_values(os.path.join(ROOT, ".env")).get("APP_PASSWORD"))


def console_python():
    """python.exe beside this pythonw.exe: llama-server, which the app starts, expects a console."""
    candidate = os.path.join(os.path.dirname(sys.executable), "python.exe")
    return candidate if os.path.exists(candidate) else sys.executable


def start():
    os.makedirs(os.path.join(ROOT, "data", "logs"), exist_ok=True)
    log = open(os.path.join(ROOT, "data", "logs", "app.log"), "a", encoding="utf-8")
    host = "0.0.0.0" if password_set() else "127.0.0.1"
    subprocess.Popen(
        [console_python(), "-m", "local_llm_host", "--host", host, "--port", str(PORT)],
        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
    )


if not running():
    start()
    for _ in range(40):
        if running():
            break
        time.sleep(0.5)

if "--headless" not in sys.argv[1:]:
    webbrowser.open(URL)
