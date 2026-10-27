#!/usr/bin/env python3
"""Send Model S/X Browser touches to its offscreen X11 window.

CDP touch events reach web content but not Chromium's address bar and toolbar.
XSendEvent reaches both while the window sits outside the visible X screen.
"""

import argparse
import ctypes
import errno
import os
import re
import struct
import subprocess


EVENT = struct.Struct("llHHi")
EV_SYN, EV_ABS = 0, 3
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 47, 53, 54, 57
BUTTON_PRESS, BUTTON_RELEASE = 4, 5
BUTTON_PRESS_MASK, BUTTON_RELEASE_MASK = 1 << 2, 1 << 3
BUTTON1_MASK = 1 << 8
WHEEL_STEP, TAP_SLOP = 60, 12


class XButtonEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong), ("root", ctypes.c_ulong),
                ("subwindow", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("x_root", ctypes.c_int), ("y_root", ctypes.c_int),
                ("state", ctypes.c_uint), ("button", ctypes.c_uint),
                ("same_screen", ctypes.c_int)]


class XEvent(ctypes.Union):
    _fields_ = [("xbutton", XButtonEvent), ("pad", ctypes.c_long * 24)]


class BrowserWindow:
    def __init__(self, display, title):
        self.name = display
        self.title = title
        self.lib = ctypes.CDLL("libX11.so.6")
        self.lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.lib.XOpenDisplay.restype = ctypes.c_void_p
        self.lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.lib.XDefaultRootWindow.restype = ctypes.c_ulong
        self.lib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                        ctypes.c_long, ctypes.POINTER(XEvent)]
        self.lib.XSendEvent.restype = ctypes.c_int
        self.lib.XFlush.argtypes = [ctypes.c_void_p]
        self.display = self.lib.XOpenDisplay(display.encode())
        if not self.display:
            raise OSError(f"cannot open X display {display}")
        self.root = self.lib.XDefaultRootWindow(self.display)

    def find(self):
        listing = subprocess.check_output(["xwininfo", "-root", "-tree"],
                                          env={**os.environ, "DISPLAY": self.name},
                                          stderr=subprocess.DEVNULL, text=True)
        match = re.search(r'^\s*(0x[0-9a-f]+) "' + re.escape(self.title) + r'":',
                          listing, re.MULTILINE)
        return int(match.group(1), 16) if match else None

    def button(self, window, x, y, button):
        for release in (False, True):
            event = XEvent()
            event.xbutton.type = BUTTON_RELEASE if release else BUTTON_PRESS
            event.xbutton.send_event = 1
            event.xbutton.display = self.display
            event.xbutton.window = window
            event.xbutton.root = self.root
            event.xbutton.x = event.xbutton.x_root = x
            event.xbutton.y = event.xbutton.y_root = y
            event.xbutton.same_screen = 1
            event.xbutton.button = button
            event.xbutton.state = (1 << (7 + button)) if release else 0
            mask = BUTTON_RELEASE_MASK if release else BUTTON_PRESS_MASK
            if not self.lib.XSendEvent(self.display, window, 1, mask, ctypes.byref(event)):
                raise OSError("XSendEvent failed")
        self.lib.XFlush(self.display)


def geometry(path):
    try:
        with open(path, encoding="ascii") as source:
            for line in source:
                fields = line.split()
                if len(fields) >= 9 and fields[3] == "cid":
                    # QtCar adds the on-screen keyboard as the final rectangle while
                    # it is open. Those taps belong to QtCar, not the Browser window:
                    # forwarding them to Chromium immediately blurs the address bar.
                    view = tuple(map(int, fields[5:9]))
                    keyboard = tuple(map(int, fields[13:17])) if len(fields) >= 17 else None
                    return view, keyboard
    except (OSError, ValueError):
        pass
    return None


def run(root, display, title, geometry_file):
    x11 = BrowserWindow(display, title)
    device = os.path.join(root, "dev/input/touch")
    geometry_path = os.path.join(root, "opt/games/run/i2v", geometry_file)
    print(f"browser-touch: listening on {device}, sending to {title} on {display}", flush=True)
    slot, contacts, start, last, moved, scroll = 0, {}, None, None, False, 0
    regions, window = None, None
    with open(device, "rb", buffering=0) as source:
        while True:
            try:
                frame = source.read(EVENT.size)
            except OSError as error:
                if error.errno == errno.ENODEV:
                    print("browser-touch: touchscreen closed", flush=True)
                    return
                raise
            if len(frame) != EVENT.size:
                return
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
                continue
            if kind != EV_SYN or code != 0:
                continue
            contact = contacts.get(0)
            if start is None:
                regions = geometry(geometry_path)
            point = None
            if contact and None not in contact and regions:
                x, y = contact
                (left, top, width, height), keyboard = regions
                covered = False
                if keyboard:
                    kx, ky, kw, kh = keyboard
                    covered = kw > 0 and kh > 0 and kx <= x < kx + kw and ky <= y < ky + kh
                if not covered and left <= x < left + width and top <= y < top + height:
                    point = (x - left, y - top)
            if point is not None and start is None:
                start = last = point
                moved, scroll = False, 0
                window = x11.find()
            elif point is not None and start is not None and point != last:
                if abs(point[0] - start[0]) > TAP_SLOP or abs(point[1] - start[1]) > TAP_SLOP:
                    moved = True
                if moved:
                    scroll += point[1] - last[1]
                    while abs(scroll) >= WHEEL_STEP:
                        if window:
                            x11.button(window, *point, 4 if scroll > 0 else 5)
                        scroll += -WHEEL_STEP if scroll > 0 else WHEEL_STEP
                last = point
            elif point is None and start is not None:
                if not moved:
                    if window:
                        x11.button(window, *last, 1)
                start = last = None
                window = None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="mounted MCU image, always chroot/")
    parser.add_argument("--display", default=":1.0")
    parser.add_argument("--window-title", default="Chromium")
    parser.add_argument("--geometry-file", default="chromium.touch-Chromium-cid.geometry")
    args = parser.parse_args()
    run(os.path.abspath(args.root), args.display, args.window_title, args.geometry_file)
