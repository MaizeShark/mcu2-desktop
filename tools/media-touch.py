#!/usr/bin/env python3
"""Forward Model S/X touches into an offscreen Chromium window.

The firmware's input-to-virtual touchscreen does not reach Chromium reliably on
native X11, and Xvfb does not attach it to XInput at all. QtCar writes the browse
geometry and CDP is bound to localhost; this helper sends only touch events.
"""

import argparse
import base64
import errno
import hashlib
import json
import os
import socket
import struct
import threading
import time
import urllib.request
from urllib.parse import urlsplit


EVENT = struct.Struct("llHHi")
EV_SYN, EV_ABS = 0, 3
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 47, 53, 54, 57


def geometry(path):
    try:
        with open(path, encoding="ascii") as source:
            for line in source:
                fields = line.split()
                if len(fields) >= 9 and fields[3] == "cid":
                    view = tuple(map(int, fields[5:9]))
                    keyboard = tuple(map(int, fields[13:17])) if len(fields) >= 17 else None
                    return view, keyboard
    except (OSError, ValueError):
        pass
    return None


class DevTools:
    def __init__(self, port):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as response:
            pages = json.load(response)
        page = next(page for page in pages if page.get("type") == "page")
        url = urlsplit(page["webSocketDebuggerUrl"])
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=2)
        self.sock.settimeout(3)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (f"GET {url.path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                   f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(request.encode("ascii"))
        headers = bytearray()
        while b"\r\n\r\n" not in headers:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("Chromium DevTools disconnected during handshake")
            headers.extend(chunk)
        accept = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
        ).digest())
        if not headers.startswith(b"HTTP/1.1 101 ") or b"Sec-WebSocket-Accept: " + accept not in headers:
            raise OSError("Chromium rejected the local DevTools websocket")
        self.pending = bytes(headers.split(b"\r\n\r\n", 1)[1])
        self.sock.settimeout(None)
        self.sequence = 0
        self.error = None
        # Chromium may take a frame to acknowledge a touch. Reading that reply in the
        # input loop queued every following evdev event and made drags feel delayed.
        # Drain replies independently; CDP does not require waiting for each one.
        self.reader = threading.Thread(target=self.drain, daemon=True)
        self.reader.start()

    def read_exact(self, length):
        result = bytearray()
        while len(result) < length:
            if self.pending:
                chunk, self.pending = self.pending[:length - len(result)], self.pending[length - len(result):]
            else:
                chunk = self.sock.recv(length - len(result))
            if not chunk:
                raise OSError("Chromium DevTools disconnected")
            result.extend(chunk)
        return bytes(result)

    def send(self, kind, points):
        if self.error:
            raise OSError(f"Chromium DevTools disconnected: {self.error}")
        self.sequence += 1
        payload = json.dumps({"id": self.sequence, "method": "Input.dispatchTouchEvent",
                              "params": {"type": kind, "touchPoints": points}}, separators=(",", ":")).encode()
        mask = os.urandom(4)
        size = len(payload)
        length = bytes([0x80 | size]) if size < 126 else b"\xfe" + struct.pack("!H", size)
        self.sock.sendall(b"\x81" + length + mask +
                          bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)))

    def drain(self):
        try:
            while True:
                header = self.read_exact(2)
                size = header[1] & 127
                if size == 126:
                    size = struct.unpack("!H", self.read_exact(2))[0]
                elif size == 127:
                    size = struct.unpack("!Q", self.read_exact(8))[0]
                self.read_exact(size)
                if header[0] & 15 == 8:
                    raise OSError("Chromium DevTools closed")
        except (OSError, ValueError) as error:
            self.error = error

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def run(device, geometry_path, port, debug=False):
    while True:
        try:
            devtools = DevTools(port)
            print("media-touch: connected to local Chromium DevTools", flush=True)
            break
        except (OSError, ValueError, KeyError, StopIteration) as error:
            print(f"media-touch: waiting for Chromium ({error})", flush=True)
            time.sleep(2)

    slot = 0
    contacts = {}
    sent = None
    area = None
    with open(device, "rb", buffering=0) as source:
        while True:
            try:
                frame = source.read(EVENT.size)
            except OSError as error:
                if error.errno == errno.ENODEV:
                    print("media-touch: touchscreen closed", flush=True)
                    return
                raise
            if len(frame) != EVENT.size:
                raise OSError("touch device disappeared")
            _, _, kind, code, value = EVENT.unpack(frame)
            if kind == EV_ABS:
                if code == ABS_MT_SLOT:
                    slot = value
                elif code == ABS_MT_TRACKING_ID:
                    if value < 0:
                        contacts.pop(slot, None)
                    else:
                        contacts[slot] = [None, None]
                elif code in (ABS_MT_POSITION_X, ABS_MT_POSITION_Y) and slot in contacts:
                    contacts[slot][code == ABS_MT_POSITION_Y] = value
            elif kind == EV_SYN and code == 0:
                contact = contacts.get(0)
                if contact and None not in contact:
                    if sent is None:
                        area = geometry(geometry_path)
                    x, y = contact
                    if area:
                        (left, top, width, height), keyboard = area
                        covered = False
                        if keyboard:
                            kx, ky, kw, kh = keyboard
                            covered = kw > 0 and kh > 0 and kx <= x < kx + kw and ky <= y < ky + kh
                        point = (x - left, y - top) if not covered and left <= x < left + width and top <= y < top + height else None
                    else:
                        point = None
                else:
                    point = None
                if point == sent:
                    continue
                event = "touchEnd" if point is None else "touchStart" if sent is None else "touchMove"
                points = [] if point is None else [{"x": point[0], "y": point[1], "id": 0}]
                try:
                    devtools.send(event, points)
                    if debug and event != "touchMove":
                        print(f"media-touch: {event} {point}", flush=True)
                    sent = point
                except (OSError, ValueError) as error:
                    print(f"media-touch: reconnecting ({error})", flush=True)
                    devtools.close()
                    while True:
                        try:
                            devtools = DevTools(port)
                            sent = None
                            break
                        except (OSError, ValueError, KeyError, StopIteration):
                            time.sleep(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="mounted MCU image, always chroot/")
    parser.add_argument("--port", type=int, default=9223)
    parser.add_argument("--debug", action="store_true", help="log touch starts and ends")
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    run(os.path.join(root, "dev/input/touch"),
        os.path.join(root, "opt/games/run/i2v/chromium-app.touch-ChromiumApp-cid.geometry"),
        args.port, args.debug)
