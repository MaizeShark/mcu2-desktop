#!/usr/bin/env python3
"""Stands in for BeamNG.drive, for testing without the game: sends the mod's packets (or stock
OutGauge + MotionSim with --stock) for a scripted drive.

  fake-game.py                            drive around for a minute, 127.0.0.1:4444
  fake-game.py --target 192.0.2.5:4444    to the bridge on another machine
  fake-game.py --stock                    what the game sends without the mod
"""
import argparse, math, socket, struct, time


def tmc1(t, odo):
    """One packet of the script: a parked start, a drive with a turn, a door, lights, a stop."""
    speed = 0.0 if t < 3 else min(25.0, (t - 3) * 3) if t < 40 else max(0.0, 25 - (t - 40) * 5)
    heading = math.radians(90 + max(0.0, t - 15) * 3)       # 0 = north
    flags = 1 | (64 if t < 3 or t > 46 else 0)
    if 10 < t < 15:
        flags |= 8      # left signal
    if 15 < t < 20:
        flags |= 16     # right signal
    if 20 < t < 25:
        flags |= 32     # hazards
    if 25 < t < 30:
        flags |= 2 | 4  # high beam, fog
    if t > 47:
        flags |= 65536 | 1048576 | 2097152      # driver's door, trunk, hood
    gear = b"P" if t < 3 or t > 46 else b"D"
    tire = (200000.0, 200000.0, 200000.0, 120000.0 if t > 30 else 200000.0)   # the last one leaks
    return struct.pack(
        "<4sI22fbBBB16s", b"TMC1", flags,
        speed * t * math.sin(heading) / 4, speed * t * math.cos(heading) / 4, 120.0,
        speed * math.sin(heading), speed * math.cos(heading), 0.0, math.sin(heading), math.cos(heading),
        speed, 30.0 * math.sin(t / 2), 0.6 if 3 < t < 40 else 0.0, 0.8 if t > 40 else 0.0, 2000.0 + speed * 150,
        max(0.0, 0.75 - t * 0.002), odo, 18.5, 90.0, 60.0 if 3 < t < 40 else 0.0, *tire,
        0 if gear == b"P" else 3, gear[0], 2, 1 if 25 < t < 35 else 0, b"fakecar")


def outgauge(t):
    speed = 0.0 if t < 3 else min(25.0, (t - 3) * 3)
    show = (32 if 10 < t < 15 else 0) | (4 if t < 3 else 0)
    return struct.pack("<I4sHBB7f2I3f16s16si", 0, b"beam", 49152, 1 if t < 3 else 3, 0, speed, 2000.0, 0.0, 90.0,
                       max(0.0, 0.75 - t * 0.002), 0.0, 90.0, 0xfff, show, 0.6, 0.0, 0.0, b"", b"", 0)


def motionsim(t):
    speed = 0.0 if t < 3 else min(25.0, (t - 3) * 3)
    return struct.pack("<4s21f", b"BNG1", speed * t / 4, 0.0, 120.0, speed, 0.0, 0.0, *([0.0] * 15))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="127.0.0.1:4444")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--stock", action="store_true")
    args = ap.parse_args()
    host, port = args.target.rsplit(":", 1)
    target = (host, int(port))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    start = time.monotonic()
    odo, last = 5000.0, start
    while True:
        now = time.monotonic()
        t = now - start
        if t > args.seconds:
            return
        odo += (0.0 if t < 3 else 20.0) * (now - last)
        last = now
        if args.stock:
            sock.sendto(outgauge(t), target)
            sock.sendto(motionsim(t), target)
        else:
            sock.sendto(tmc1(t, odo), target)
        time.sleep(1 / 30)


if __name__ == "__main__":
    main()
