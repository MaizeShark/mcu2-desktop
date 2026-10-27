#!/usr/bin/env python3
"""Navigate the persistent Model S/X Theater Chromium on QtCar start/stop requests.

The car's escalator does not run in a desktop chroot. QtCar still logs its Theater
requests and writes the selected URL to Chromium policy. Keep the Theater window
mapped before QtCar starts, then navigate that page through localhost DevTools.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import signal
import socket
import struct
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


START = re.compile(r"(?<![\w-])start-tesla-chromium-fullscreen(?![\w-])")
STOP = re.compile(r"(?<![\w-])stop-tesla-chromium-fullscreen(?![\w-])")
POLICY = "run/chromium/policies/chromium-fullscreen/10-homepage.json"
PORT = 9225  # firmware Chromium; bound to localhost by tesla-media-service
running = True


def shutdown(_signum, _frame):
    global running
    running = False


def requested_url(root):
    path = root / POLICY
    for _ in range(20):
        try:
            urls = json.loads(path.read_text()).get("RestoreOnStartupURLs", [])
            if len(urls) == 1 and urls[0].startswith(("https://", "http://")):
                return urls[0]
        except (OSError, ValueError, TypeError):
            pass
        time.sleep(0.1)
    return None


def navigate(url):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2) as response:
        pages = json.load(response)
    page = next(page for page in pages if page.get("type") == "page")
    path = urlsplit(page["webSocketDebuggerUrl"]).path
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\n"
               f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
    with socket.create_connection(("127.0.0.1", PORT), timeout=2) as conn:
        conn.settimeout(2)
        conn.sendall(request.encode("ascii"))
        headers = bytearray()
        while b"\r\n\r\n" not in headers:
            block = conn.recv(4096)
            if not block:
                raise OSError("Theater DevTools closed during handshake")
            headers.extend(block)
        accept = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
        ).digest())
        if not headers.startswith(b"HTTP/1.1 101 ") or b"Sec-WebSocket-Accept: " + accept not in headers:
            raise OSError("Theater DevTools rejected websocket")
        payload = json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}},
                             separators=(",", ":")).encode()
        mask = os.urandom(4)
        length = bytes([0x80 | len(payload)]) if len(payload) < 126 else b"\xfe" + struct.pack("!H", len(payload))
        conn.sendall(b"\x81" + length + mask +
                     bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)))
        conn.recv(4096)  # wait for Chromium to accept the command before closing


def try_navigate(url):
    for attempt in range(20):
        try:
            navigate(url)
            print(f"theater-control: navigated to {url}", flush=True)
            return
        except (OSError, ValueError, StopIteration) as error:
            if attempt == 19:
                print(f"theater-control: navigation failed: {error}", flush=True)
            else:
                time.sleep(0.2)


def serve(root, qtcar_log):
    while running and not qtcar_log.exists():
        time.sleep(0.2)
    if not running:
        return
    with open(qtcar_log, errors="replace") as source:
        print(f"theater-control: watching {qtcar_log}", flush=True)
        while running:
            line = source.readline()
            if not line:
                time.sleep(0.2)
                continue
            if STOP.search(line):
                try_navigate("about:blank")
            elif START.search(line):
                url = requested_url(root)
                if url:
                    try_navigate(url)
                else:
                    print("theater-control: QtCar did not write a Theater URL", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="mounted MCU image, always chroot/")
    parser.add_argument("qtcar_log", type=Path)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    serve(args.root.resolve(), args.qtcar_log)
