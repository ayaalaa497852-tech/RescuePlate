"""One-click local RescuePlate launcher. Starts server first and only then opens the browser."""
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)


def free_port():
    for port in range(8000, 8110):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise RuntimeError('No free port from 8000 to 8109. Close previous server windows and try again.')


def main():
    port = free_port()
    url = f'http://127.0.0.1:{port}'
    print('\nRescuePlate - starting local server...', flush=True)
    print(f'Project folder: {ROOT}', flush=True)
    print('Please keep this window open while using the website.\n', flush=True)
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT)
    try:
        for attempt in range(100):
            if server.poll() is not None:
                raise RuntimeError(f'Server stopped unexpectedly (exit code {server.returncode}). Check errors above.')
            try:
                with urllib.request.urlopen(url + '/health', timeout=0.35) as resp:
                    if resp.status == 200 and b'"status":"ok"' in resp.read():
                        print(f'READY: {url}', flush=True)
                        print('Opening the website in your default browser...', flush=True)
                        print('Press Ctrl+C here when you are finished.\n', flush=True)
                        webbrowser.open(url + '/?v=2')
                        return server.wait()
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
            time.sleep(0.15)
        raise RuntimeError('The server did not become ready. Check the error messages above.')
    except KeyboardInterrupt:
        print('\nStopping RescuePlate...', flush=True)
        return 0
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr, flush=True)
        sys.exit(1)
