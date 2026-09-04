"""Trail Coach — macOS app entry point (PyWebView + Flask)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import threading
import time
import urllib.request
import urllib.error
import webview
from server import run as run_server

PORT = 7432


def start_server():
    run_server(port=PORT)


def wait_for_server(timeout=15):
    url = f"http://localhost:{PORT}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


if __name__ == "__main__":
    t = threading.Thread(target=start_server, daemon=True)
    t.start()

    if not wait_for_server():
        print("ERROR: Flask server did not start within 15 seconds")
        raise SystemExit(1)

    webview.create_window(
        "Trail Coach",
        url=f"http://localhost:{PORT}",
        width=1200,
        height=800,
        min_size=(900, 600),
    )
    webview.start()
