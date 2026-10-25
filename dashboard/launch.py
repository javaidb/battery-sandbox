"""Start the dashboard on the first free port and open it in the browser.

Used by run_dashboard.bat; also works directly:  python dashboard/launch.py
"""

import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def free_port(start: int = 8501) -> int:
    """First free port from ``start`` up, so an already-running dashboard does not block this one."""
    return next(p for p in range(start, start + 100) if not port_in_use(p))


def open_when_ready(url: str, port: int, timeout_s: float = 60) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if port_in_use(port):
            webbrowser.open(url)
            return
        time.sleep(0.5)


def quiet_windows_connection_resets() -> None:
    """Stop asyncio printing a traceback each time the browser drops a connection on Windows.

    When a tab reloads or closes, Windows' proactor event loop logs a
    ConnectionResetError (WinError 10054) while tidying up a socket the
    browser already closed. Nothing has failed, so the reset is ignored.
    """
    if sys.platform != "win32":
        return
    from asyncio import proactor_events

    original = proactor_events._ProactorBasePipeTransport._call_connection_lost

    def call_connection_lost(self, exc):
        try:
            original(self, exc)
        except ConnectionResetError:
            pass

    proactor_events._ProactorBasePipeTransport._call_connection_lost = call_connection_lost


def main() -> int:
    port = free_port()
    url = f"http://localhost:{port}"
    print(f"Starting the dashboard at {url}  (press Ctrl+C or close this window to stop it)")
    threading.Thread(target=open_when_ready, args=(url, port), daemon=True).start()
    quiet_windows_connection_resets()
    # Streamlit runs in this process (not a subprocess) so the fix above applies to its server.
    # Headless skips Streamlit's first-run email prompt, which would otherwise block a double-click start.
    # localhost only: the dashboard runs local code, so it should not be reachable from the network.
    from streamlit.web import cli

    sys.argv = ["streamlit", "run", str(ROOT / "dashboard" / "app.py"),
                "--server.headless", "true", "--server.address", "localhost", "--server.port", str(port),
                "--browser.gatherUsageStats", "false"]
    return cli.main()


if __name__ == "__main__":
    sys.exit(main())
