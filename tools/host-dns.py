#!/usr/bin/env python3
"""Keep the chroot's DNS resolvers (its /etc/resolv.conf) at the PC's, or at given servers.

The firmware's resolv.conf is the car's (Model 3: /etc/resolv.conf -> /var/run/connman/
resolv.conf, which the car's ConnMan writes; S/X: its own dnsmasq), and the kit puts 8.8.8.8
there. Networks that only allow their own DNS server (or a VPN's) left the chroot without
names: no map, no browser. The chroot shares the PC's network, so the PC's resolvers work
inside it, including systemd-resolved's 127.0.0.53.

  host-dns.py CHROOT host               the PC's /etc/resolv.conf, followed while it runs
  host-dns.py CHROOT 1.1.1.1 9.9.9.9    these servers

The file is checked every --interval seconds and written again when it differs (the PC
changed networks, the Model 3's startup.sh of an older kit wrote 8.8.8.8 at a QtCar start).
While the PC has no resolver (between networks) the last one stays."""

import argparse
import ipaddress
import os
import time

FALLBACK = "nameserver 8.8.8.8\n"
HEADER = "# written by mcu2-desktop's tools/host-dns.py (DNS in tesla.conf)\n"


def chroot_path(root, path):
    """PATH inside ROOT with its symlinks resolved inside ROOT (an absolute link must not lead
    to the PC's own file)."""
    root = os.path.realpath(root)
    parts = [p for p in path.split("/") if p]
    current = root
    for _ in range(40):
        if not parts:
            return current
        name = parts.pop(0)
        if name == "..":
            current = os.path.dirname(current) if current != root else root
            continue
        if name == ".":
            continue
        candidate = os.path.join(current, name)
        if os.path.islink(candidate):
            target = os.readlink(candidate)
            parts = [p for p in target.split("/") if p] + parts
            if target.startswith("/"):
                current = root
            continue
        current = candidate
    raise OSError(f"too many symlinks in {path}")


def host_resolvers(path="/etc/resolv.conf"):
    """The PC's resolv.conf without comments and systemd-resolved's "search ." (an empty
    search list; glibc 2.22 would append the dot). Empty when it has no nameserver."""
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    keep = []
    for line in lines:
        words = line.split()
        if not words or words[0].startswith(("#", ";")):
            continue
        if words[0] in ("search", "domain") and all(w == "." for w in words[1:]):
            continue
        keep.append(" ".join(words))
    if not any(line.startswith("nameserver ") for line in keep):
        return ""
    return "\n".join(keep) + "\n"


def servers_resolvers(servers):
    return "".join(f"nameserver {server}\n" for server in servers)


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = f"{path}.host-dns"
    with open(temp, "w") as f:
        f.write(content)
    os.chmod(temp, 0o644)
    os.replace(temp, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("chroot", help="the mounted chroot/ directory")
    parser.add_argument("dns", nargs="+", help="host, or DNS server addresses")
    parser.add_argument("--interval", type=float, default=2, help="seconds between checks")
    parser.add_argument("--once", action="store_true", help="write once, then exit")
    parser.add_argument("--host-resolv", default="/etc/resolv.conf", help=argparse.SUPPRESS)
    args = parser.parse_args()
    servers = [word for item in args.dns for word in item.replace(",", " ").split()]
    follow_host = servers == ["host"]
    if not follow_host:
        try:
            servers = [str(ipaddress.ip_address(server)) for server in servers]
        except ValueError as error:
            parser.error(f"{error} (DNS: host or IP addresses)")
    target = chroot_path(args.chroot, "etc/resolv.conf")
    last = None
    while True:
        wanted = host_resolvers(args.host_resolv) if follow_host else servers_resolvers(servers)
        if not wanted:
            # the PC is between networks: keep what the chroot has, unless it has nothing
            wanted = last or FALLBACK
            if last is None:
                print("the PC has no DNS server (yet): 8.8.8.8 for now", flush=True)
        if wanted != last:
            print("chroot DNS: " + ", ".join(line.split()[1] for line in wanted.splitlines()
                                             if line.startswith("nameserver ")), flush=True)
        if read(target) != HEADER + wanted:
            try:
                write(target, HEADER + wanted)
            except OSError as error:
                print(f"couldn't write {target}: {error}", flush=True)
        last = wanted
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
