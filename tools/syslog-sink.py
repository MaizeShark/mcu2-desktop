#!/usr/bin/env python3
"""The chroot's syslog (./tesla start): the firmware's /dev/log points to /tmp/log.

The services log a lot through syslog(3) (config files, ports, data values, ...). Without a
listener glibc falls back to /dev/console (LOG_CONS): the text console behind the X server, which
a PC hides, but the stick (X on tty1) showed after QtCar ended. This takes the messages and
writes them to the run's syslog.log instead.

  syslog-sink.py SOCKET LOGFILE
"""
import os, re, signal, socket, sys, time

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # ./tesla stop: remove the socket too

sock_path, log_path = sys.argv[1], sys.argv[2]
try:
    os.unlink(sock_path)
except FileNotFoundError:
    pass
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
s.bind(sock_path)
os.chmod(sock_path, 0o666)     # the services run as many users
# "<PRI>Mmm dd hh:mm:ss " (the time is the car's time zone; ours goes in front instead)
head = re.compile(rb"^<(\d+)>(?:[A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d )?")
levels = "EMERG ALERT CRIT ERR WARNING NOTICE INFO DEBUG".split()
with open(log_path, "ab", buffering=0) as log:
    try:
        while True:
            msg = s.recv(65536)
            m = head.match(msg)
            level = levels[int(m.group(1)) & 7] if m else "?"
            text = msg[m.end():] if m else msg
            log.write(b"%s %-7s %s\n" % (time.strftime("%T").encode(), level.encode(), text.rstrip(b"\n\0")))
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        try:
            os.unlink(sock_path)
        except OSError:
            pass
