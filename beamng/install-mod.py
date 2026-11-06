#!/usr/bin/env python3
"""Packs beamng/mod into the zip the game loads, and copies it into the game's user folder.

  install-mod.py                       the game on this PC: into its mods folder
  install-mod.py --user-folder DIR     ... if it isn't found
  install-mod.py --target 192.0.2.5    the UI runs at that address (the game sends its packets there)
  install-mod.py --zip-only ...        only beamng/work/maizeshark_teslamcu.zip, to copy to the game's PC
"""
import argparse, ipaddress, os, socket, sys, zipfile

HERE = os.path.dirname(os.path.realpath(__file__))
NAME = "maizeshark_teslamcu"
LUA = "lua/vehicle/protocols/%s.lua" % NAME


def user_folders():
    """Where the game keeps its user folder on Linux: natively under XDG_DATA_HOME, with Proton in
    the prefix (the same names under AppData/Local). The launcher can move it: --user-folder."""
    share = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    prefix = os.path.join(share, "Steam/steamapps/compatdata/284160/pfx/drive_c/users/steamuser/AppData/Local")
    for base in (share, prefix):
        for name in ("BeamNG/BeamNG.drive", "BeamNG.drive"):
            yield os.path.join(base, name)


def mods_folder(user_folder):
    """Newer versions keep the mods in a version folder with a "current" link."""
    current = os.path.join(user_folder, "current")
    return os.path.join(current if os.path.isdir(current) else user_folder, "mods")


def find_installed():
    """The zip in the game's mods folder, or None."""
    for folder in user_folders():
        path = os.path.join(mods_folder(folder), NAME + ".zip")
        if os.path.exists(path):
            return path
    return None


def resolve(host):
    """An IP address: the game's sendto() doesn't resolve names."""
    try:
        return str(ipaddress.IPv4Address(socket.gethostbyname(host)))
    except OSError as e:
        sys.exit("can't resolve %s: %s" % (host, e))


def build(path, target):
    with open(os.path.join(HERE, "mod", LUA)) as f:
        lua = f.read()
    if target:
        marker = 'local FIXED_ADDRESS = ""'
        if marker not in lua:
            sys.exit("the mod has no %r" % marker)
        lua = lua.replace(marker, 'local FIXED_ADDRESS = "%s"' % target)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(LUA, lua)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", metavar="HOST", help="the address of the PC the UI runs on (default: the game's "
                                                     "OutGauge address setting, 127.0.0.1 unless changed)")
    ap.add_argument("--user-folder", metavar="DIR", help="the game's user folder (the launcher shows it)")
    ap.add_argument("--zip-only", action="store_true", help="only make beamng/work/%s.zip" % NAME)
    ap.add_argument("--find", action="store_true", help="print where the mod is installed, if it is")
    args = ap.parse_args()

    if args.find:
        path = find_installed()
        if path:
            print(path)
        sys.exit(0 if path else 1)
    target = resolve(args.target) if args.target else None
    work = os.path.join(HERE, "work", NAME + ".zip")
    if args.zip_only:
        build(work, target)
        print("%s\ncopy it into mods/ in the game's user folder (the launcher: Manage User Folder)" % work)
        return
    folders = [args.user_folder] if args.user_folder else [f for f in user_folders() if os.path.isdir(f)]
    if not folders or not os.path.isdir(folders[0]):
        sys.exit("no user folder of the game found. Start the game once (it creates it), or give "
                 "--user-folder DIR; the game on another PC: --zip-only --target THIS_PC")
    path = os.path.join(mods_folder(folders[0]), NAME + ".zip")
    build(path, target)
    print("installed: %s" % path)
    print("sends to %s:4444; in the game: Ctrl+R reloads the vehicle" % (target or "the game's OutGauge address setting"))


if __name__ == "__main__":
    main()
