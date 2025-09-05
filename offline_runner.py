from __future__ import annotations
import os
import sys
import socket
import threading
import webbrowser
import time


def find_free_port(candidates=(9629, 9630, 9631, 0)) -> int:
    for p in candidates:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            port = s.getsockname()[1]
            s.close()
            return port
        except OSError:
            s.close()
            continue
    return 0


def main():
    # Prepare env for offline mode
    os.environ.setdefault("OFFLINE", "1")
    # Use a per-user data dir on Windows; fallback to local folder otherwise
    appdata = os.getenv("APPDATA") or os.path.expanduser("~")
    data_dir = os.path.join(appdata, "FamilyFleetOffline")
    os.makedirs(data_dir, exist_ok=True)
    os.environ.setdefault("SQLITE_PATH", os.path.join(data_dir, "app.db"))

    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    os.environ["BASE_URL"] = base_url

    def open_browser():
        time.sleep(0.8)
        webbrowser.open(f"{base_url}/")

    threading.Thread(target=open_browser, daemon=True).start()

    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()

