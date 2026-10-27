#!/usr/bin/env python3
"""
Turn a pristine Tesla MCU2 rootfs (x86_64) into the state that runs QtCar in a chroot on a
desktop Linux host (./tesla start). What gets done depends on the firmware: each supported
version has a profile in profiles/<name>/ (profile.toml: which firmware, ./tesla's defaults;
kit.py: the binary patches, payload files, symlinks, directories, text files; sources.toml:
where the payload comes from; src/: what gets compiled). The profile is detected from the
rootfs (usr/tesla/UI/bin/customerVersion.txt and UI_PLATFORM in etc/RunQtCar.env).

  profiles/2019.20.4.2-model3   Model 3: 5 byte patches, Mesa 18, CEF shims, audio, ...
  profiles/2026.8.3-models      Model S/X: Mesa 25 with llvmpipe (Debian 13)

Usage:
  sudo python3 mcu2_patch.py /path/to/rootfs              # apply everything
  sudo python3 mcu2_patch.py /path/to/rootfs --check      # only report status
  python3 mcu2_patch.py --list [--profile NAME]           # list steps and patches
  python3 mcu2_patch.py --profiles                        # the profiles and what they match
  sudo python3 mcu2_patch.py ROOT --skip cef --skip identity
  sudo python3 mcu2_patch.py ROOT --vin 5YJ3... --birthday ...

Every step is idempotent: already applied things are detected and skipped.
Patched binaries are backed up as <file>.orig (unless --no-backup).
"""
import argparse
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request

KIT = os.path.dirname(os.path.abspath(__file__))
PROFILES = os.path.join(KIT, 'profiles')
SHARED_SRC = os.path.join(KIT, 'src')             # used by several profiles (qtcar-service, sv wrapper)
PAYLOAD_ROOT = os.path.join(KIT, 'payload')       # payload/<profile>/<path>: downloaded / built files
DOWNLOADS = os.path.join(KIT, 'downloads')        # cache for downloaded packages (all profiles)
TESLA_UID = TESLA_GID = 1111
# images stamped before profiles existed (2026-09-27) were all built for this one
LEGACY_PROFILE = '2019.20.4.2-model3'

P = None            # the loaded profile's kit.py (a module)
PROFILE = None      # its name (folder)
PAYLOAD = None      # payload/<profile>


# ---------------------------------------------------------------------------
# Profiles

def profile_names():
    return sorted(d for d in os.listdir(PROFILES) if os.path.isfile(os.path.join(PROFILES, d, 'profile.toml')))


def profile_meta(name):
    with open(os.path.join(PROFILES, name, 'profile.toml'), 'rb') as f:
        return tomllib.load(f)


def firmware_id(root):
    """(version, platform) of a rootfs: customerVersion.txt's first word, RunQtCar.env's UI_PLATFORM."""
    version = platform = None
    try:
        with open(os.path.join(root, 'usr/tesla/UI/bin/customerVersion.txt')) as f:
            version = (f.read().split() or [None])[0]
    except OSError:
        pass
    try:
        with open(os.path.join(root, 'etc/RunQtCar.env')) as f:
            m = re.search(r'^UI_PLATFORM=\$\{UI_PLATFORM:-(\w+)\}', f.read(), re.M)
            platform = m and m.group(1)
    except OSError:
        pass
    return version, platform


def detect_profile(root):
    version, platform = firmware_id(root)
    for name in profile_names():
        d = profile_meta(name).get('detect', {})
        if d.get('version') == version and d.get('platform') == platform:
            return name
    sys.exit(f'{root}: firmware {version or "?"} (UI_PLATFORM {platform or "?"}) has no kit profile '
             f'(--profiles lists them; --profile NAME forces one)')


def load_profile(name):
    global P, PROFILE, PAYLOAD
    d = os.path.join(PROFILES, name)
    if not os.path.isfile(os.path.join(d, 'kit.py')):
        sys.exit(f'no kit profile {name!r} ({", ".join(profile_names())})')
    spec = importlib.util.spec_from_file_location(f'kit_profile_{name}', os.path.join(d, 'kit.py'))
    mod = importlib.util.module_from_spec(spec)
    mod.KIT, mod.PROFILE_DIR, mod.TESLA_UID = KIT, d, TESLA_UID
    mod.shared_src = lambda n: open(os.path.join(SHARED_SRC, n)).read()
    spec.loader.exec_module(mod)
    defaults = {'BINARIES': [], 'PAYLOAD_FILES': [], 'RETIRED_FILES': [], 'SYMLINKS': [], 'DIR_MODES': [],
                'DIRECTORIES': [], 'EMPTY_FILES': [], 'SETTINGS_KEYS': {}, 'SETTINGS_FILES': [],
                'TEXT_FILES': [], 'ASOUND_BLOCK': None, 'ASOUND_AWE': None, 'GROUPS': {}, 'BUILDABLE': {},
                'GLIBC': None,
                'TESLA_SHELL': True, 'KEEP_BINARY': {}}
    for k, v in defaults.items():
        if not hasattr(mod, k):
            setattr(mod, k, v)
    P, PROFILE, PAYLOAD = mod, name, os.path.join(PAYLOAD_ROOT, name)
    return mod



# ---------------------------------------------------------------------------
class Runner:
    def __init__(self, root, check, backup, skip, quiet=False):
        self.root, self.check, self.backup, self.skip = root, check, backup, set(skip)
        self.is_root = os.geteuid() == 0
        self.counts = {'ok': 0, 'done': 0, 'todo': 0, 'error': 0}
        self.quiet = quiet          # only what isn't 'ok' (and its section header)
        self.header = None

    def section(self, name):
        self.header = f'\n== {name}'
        if not self.quiet:
            print(self.header)
            self.header = None

    def p(self, rel):
        return os.path.join(self.root, rel)

    def report(self, state, what):
        self.counts[state] += 1
        if self.quiet and state == 'ok':
            return
        if self.header:
            print(self.header)
            self.header = None
        tag = {'ok': 'already', 'done': 'APPLIED', 'todo': 'TODO', 'error': 'ERROR'}[state]
        print(f'  [{tag:>7}] {what}')

    def skipped(self, group, ident=None):
        return group in self.skip or (ident is not None and ident in self.skip)

    def chown(self, path, uid=0, gid=0, follow=True):
        if self.is_root:
            os.chown(path, uid, gid, follow_symlinks=follow)

    def write_atomic(self, path, data, mode, uid=0, gid=0):
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix='.mcu2patch.')
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            os.chmod(tmp, mode & 0o777)
            self.chown(tmp, uid, gid)
            os.chmod(tmp, mode)  # after chown, so setuid bits survive
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def backup_file(self, path):
        bak = path + '.orig'
        if self.backup and os.path.isfile(path) and not os.path.islink(path) and not os.path.exists(bak):
            shutil.copy2(path, bak)

    # -- binary patches ------------------------------------------------------
    def binaries(self):
        self.section('Binary patches')
        for b in P.BINARIES:
            patches = [x for x in b['patches'] if not self.skipped(x[1], x[0])]
            retired = b.get('retired', [])
            path = self.p(b['path'])
            if not os.path.exists(path):
                self.report('error', f"{b['path']}: file not found")
                continue
            data = bytearray(open(path, 'rb').read())
            base_sha = sha1(data)
            if base_sha != b['sha1_orig'] and not self.partially_patched(data, b['patches'] + retired):
                self.report('error', f"{b['path']}: unknown file version (sha1 {base_sha}), not touching it")
                continue
            changed = False
            writes = []
            # (id, offset, bytes found = ok, bytes to replace, what to write, label)
            todo = [(i, off, new, old, new, f"{b['path']} @0x{off:x} {i}: {d}") for i, g, off, old, new, d in patches]
            todo += [(i, off, orig, old, orig, f"{b['path']} @0x{off:x} {i} (retired, restoring the firmware's "
                      f"bytes): {d}") for i, g, off, orig, old, d in retired]
            for ident, off, good, bad, write, label in todo:
                good, bad = bytes.fromhex(good), bytes.fromhex(bad)
                cur = bytes(data[off:off + len(good)])
                if cur == good:
                    # a retired patch that isn't there is nothing to mention
                    if ident not in {x[0] for x in retired}:
                        self.report('ok', label)
                elif cur == bad:
                    if self.check:
                        self.report('todo', label)
                    else:
                        data[off:off + len(good)] = good
                        writes.append((off, good))
                        changed = True
                        self.report('done', label)
                else:
                    self.report('error', f'{label} -- unexpected bytes {cur.hex()}')
            if changed:
                self.backup_file(path)
                if b.get('in_place'):
                    # Some firmware executables are hardlinks to one 235 MiB image. A replace
                    # would split them into four copies and exhaust the ext4 image. Only the
                    # exact hash/bytes checked above are changed; keep the shared inode.
                    with open(path, 'r+b') as f:
                        for off, blob in writes:
                            f.seek(off)
                            f.write(blob)
                        f.flush()
                        os.fsync(f.fileno())
                else:
                    self.write_atomic(path, bytes(data), os.stat(path).st_mode & 0o7777)

    @staticmethod
    def partially_patched(data, patches):
        for _, _, off, a, b, _ in patches:
            cur = bytes(data[off:off + len(bytes.fromhex(a))])
            if cur not in (bytes.fromhex(a), bytes.fromhex(b)):
                return False
        return True

    def retired_files(self):
        """Remove files earlier kits added that aren't needed any more (P.RETIRED_FILES)."""
        self.section('Retired files')
        for group, rel, why in P.RETIRED_FILES:
            if self.skipped(group):
                continue
            for f in (rel, rel + '.orig'):
                path = self.p(f)
                if not os.path.lexists(path):
                    continue
                label = f'{f} (retired: {why})'
                if self.check:
                    self.report('todo', 'remove ' + label)
                else:
                    os.unlink(path)
                    self.report('done', 'removed ' + label)

    # -- payload -------------------------------------------------------------
    def payload(self):
        self.section('Payload files')
        for group, rel, mode, want in P.PAYLOAD_FILES:
            if self.skipped(group):
                continue
            src = os.path.join(PAYLOAD, rel)
            dst = self.p(rel)
            if not payload_ok(rel, want):
                if self.check:
                    self.report('todo', f'{rel} (not in payload/, would be fetched)')
                    continue
                try:
                    fetch_payload(rel, want)
                except FetchError as e:
                    self.report('error', f'{rel}: {e}')
                    continue
            want_installed = sha1_file(src)  # differs from `want` for files built from src/
            if os.path.isfile(dst) and not os.path.islink(dst) and sha1_file(dst) == want_installed:
                if (os.stat(dst).st_mode & 0o7777) != mode and not self.check:
                    os.chmod(dst, mode)
                self.report('ok', rel)
                continue
            if self.check:
                self.report('todo', rel)
                continue
            self.backup_file(dst)
            if os.path.islink(dst):
                os.unlink(dst)
            self.write_atomic(dst, open(src, 'rb').read(), mode)
            self.report('done', rel)

    def symlinks(self):
        self.section('Symlinks')
        for group, rel, target in P.SYMLINKS:
            if self.skipped(group):
                continue
            dst = self.p(rel)
            label = f'{rel} -> {target}'
            if os.path.islink(dst) and os.readlink(dst) == target:
                self.report('ok', label)
                continue
            if self.check:
                self.report('todo', label)
                continue
            if os.path.lexists(dst):
                if os.path.isdir(dst) and not os.path.islink(dst):
                    self.report('error', f'{label}: a directory is in the way')
                    continue
                # a plain copy of the link target (e.g. libLLVM-6.0.so) needs no backup
                resolved = os.path.join(os.path.dirname(dst), target) if not target.startswith('/') \
                    else self.p(target.lstrip('/'))
                if not (os.path.isfile(resolved) and not os.path.islink(dst)
                        and sha1_file(dst) == sha1_file(resolved)):
                    self.backup_file(dst)
                os.unlink(dst)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.symlink(target, dst)
            self.chown(dst, follow=False)
            self.report('done', label)

    # -- system --------------------------------------------------------------
    def system(self):
        if self.skipped('system'):
            return
        self.section('System config')
        if P.TESLA_SHELL:
            self.tesla_shell()

        for rel, uid in P.DIRECTORIES:
            d = self.p(rel)
            if os.path.isdir(d):
                self.report('ok', f'dir {rel}')
            elif self.check:
                self.report('todo', f'dir {rel}')
            else:
                os.makedirs(d)
                self.report('done', f'dir {rel}')
            if uid is not None and not self.check and os.path.isdir(d):
                self.chown(d, uid, uid)

        for rel, mode in P.DIR_MODES:
            d = self.p(rel)
            if not os.path.isdir(d):
                self.report('error', f'dir {rel} not found')
            elif os.stat(d).st_mode & 0o7777 == mode:
                self.report('ok', f'dir {rel} mode {mode:o}')
            elif self.check:
                self.report('todo', f'dir {rel} mode {mode:o}')
            else:
                os.chmod(d, mode)
                self.report('done', f'dir {rel} mode {mode:o}')

        for rel in P.EMPTY_FILES:
            f = self.p(rel)
            if os.path.lexists(f):  # may be a live bind mount - never replace
                self.report('ok', f'file {rel}')
            elif self.check:
                self.report('todo', f'file {rel}')
            else:
                open(f, 'a').close()
                os.chmod(f, 0o644)
                self.chown(f)
                self.report('done', f'file {rel}')

        for rel, owner in P.SETTINGS_FILES:
            self.settings_file(rel, owner)
        for group, rel, content, mode, *owner in P.TEXT_FILES:
            if not self.skipped(group):
                self.text_file(rel, content, mode, *owner)

    def tesla_shell(self):
        """The tesla user needs a real shell for `su tesla -c ...` (startup.sh)."""
        passwd = self.p('etc/passwd')
        text = open(passwd).read()
        if re.search(r'^tesla:[^\n]*:/bin/sh$', text, re.M):
            self.report('ok', 'etc/passwd: tesla shell is /bin/sh')
        elif re.search(r'^tesla:[^\n]*:/bin/false$', text, re.M):
            if self.check:
                self.report('todo', 'etc/passwd: tesla shell /bin/false -> /bin/sh')
            else:
                new = re.sub(r'^(tesla:[^\n]*:)/bin/false$', r'\1/bin/sh', text, flags=re.M)
                self.write_atomic(passwd, new.encode(), os.stat(passwd).st_mode & 0o7777)
                self.report('done', 'etc/passwd: tesla shell /bin/false -> /bin/sh')
        else:
            self.report('error', 'etc/passwd: tesla user not found')

    def settings_file(self, rel, owner):
        """Make sure P.SETTINGS_KEYS are set in an INI file, keeping everything else in it."""
        import configparser
        import io
        f = self.p(rel)
        conf = configparser.ConfigParser(interpolation=None)
        conf.optionxform = str  # keep key case
        if os.path.isfile(f):
            conf.read(f)
        missing = [(sec, k) for sec, keys in P.SETTINGS_KEYS.items() for k, v in keys.items()
                   if not conf.has_section(sec) or conf[sec].get(k) != v]
        if not missing:
            self.report('ok', rel)
            return
        if self.check:
            self.report('todo', f'{rel} (set {", ".join(k for _, k in missing)})')
            return
        for sec, keys in P.SETTINGS_KEYS.items():
            if not conf.has_section(sec):
                conf.add_section(sec)
            conf[sec].update(keys)
        out = io.StringIO()
        conf.write(out, space_around_delimiters=False)
        self.write_atomic(f, out.getvalue().encode(), 0o644, owner, owner)
        self.report('done', f'{rel} (set {", ".join(k for _, k in missing)})')

    def text_file(self, rel, content, mode, owner=0):
        f = self.p(rel)
        if os.path.isfile(f) and open(f, 'rb').read() == content.encode():
            self.report('ok', rel)
        elif self.check:
            self.report('todo', f'{rel} ({describe_change(f, content)})')
        else:
            if rel == 'startup.sh':
                self.backup_file(f)
            keep = dict({'sbin/sv': 'sbin/sv.runit'}, **P.KEEP_BINARY).get(rel)
            if keep and os.path.isfile(f) and open(f, 'rb').read(4) == b'\x7fELF':
                # a wrapper replaces a firmware program and hands on what it doesn't handle
                shutil.copy2(f, self.p(keep))
            self.write_atomic(f, content.encode(), mode, owner, owner)
            self.report('done', rel)

    def audio(self):
        if self.skipped('audio') or not P.ASOUND_BLOCK:
            return
        self.section('Audio')
        f = self.p('etc/asound.conf')
        text = open(f).read()
        if P.ASOUND_BLOCK in text:
            self.report('ok', 'etc/asound.conf: sound card block')
        elif self.check:
            self.report('todo', 'etc/asound.conf: sound card block')
        else:
            for mark in (P.ASOUND_MARK, P.ASOUND_MARK.replace('mcu2-', 'mcu3-')):
                if mark in text:        # an older version of the block (or the old name): replace it
                    text = text[:text.index(mark)].rstrip('\n') + '\n'
            self.write_atomic(f, (text.rstrip('\n') + '\n\n' + P.ASOUND_BLOCK).encode(), 0o644)
            self.report('done', 'etc/asound.conf: sound card block')
        if P.ASOUND_AWE:        # the Model 3's standalone AudioWeaver
            self.text_file('etc/asound-awe.conf', P.ASOUND_AWE, 0o644)

    def identity(self, vin, birthday):
        if self.skipped('identity') or not (vin or birthday):
            return
        self.section('Identity')
        for name, value in (('vin', vin), ('birthday', birthday)):
            if value:
                self.text_file(f'var/etc/{name}', value.strip() + '\n', 0o644)


# ---------------------------------------------------------------------------
# Getting payload files: payload/<profile>/ -> download (the profile's sources.toml) -> build
# (the profile's src/)

class FetchError(Exception):
    pass


def payload_ok(rel, want):
    path = os.path.join(PAYLOAD, rel)
    if not os.path.isfile(path):
        # kits before profiles cached in payload/<path>: take a file from there if it's the one
        old = os.path.join(PAYLOAD_ROOT, rel)
        if rel in P.BUILDABLE or not os.path.isfile(old) or sha1_file(old) != want:
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copy2(old, path)
    if rel in P.BUILDABLE:  # rebuild when the source changed
        return os.path.getmtime(path) >= os.path.getmtime(os.path.join(P.PROFILE_DIR, P.BUILDABLE[rel][0]))
    return sha1_file(path) == want


def fetch_payload(rel, want):
    """Put payload/<profile>/<rel> in place: download it per sources.toml, or build it from src/."""
    out = os.path.join(PAYLOAD, rel)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if rel in P.BUILDABLE:
        source, flags = P.BUILDABLE[rel]
        cmd = ['gcc', *flags, '-o', out, os.path.join(P.PROFILE_DIR, source)]
        print(f'            building {rel}: {" ".join(cmd)}')
        try:
            subprocess.run(cmd, check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            raise FetchError(f'build failed ({e})')
        check_glibc(out)
        return

    entry = load_sources().get(rel, {})
    url = entry.get('url', '').strip()
    if not url:
        raise FetchError(f'not in payload/ and no url in {PROFILE}/sources.toml [\"{rel}\"]')
    archive = download(url, entry.get('sha256', '').strip())
    member = entry.get('member', '').strip()
    data = extract(archive, member) if member else open(archive, 'rb').read()
    if sha1(data) != want:
        raise FetchError(f'file from {url}{" -> " + member if member else ""} has sha1 {sha1(data)}, '
                         f'expected {want} (wrong package version?)')
    with open(out, 'wb') as f:
        f.write(data)
    print(f'            fetched {rel} from {url}')


def check_glibc(path):
    """A shim built on the host must not need newer glibc symbol versions than the firmware's
    (the profile's GLIBC; e.g. __isoc23_strtol@GLIBC_2.38 on 2.22), or it won't load in the chroot."""
    if not P.GLIBC:
        return
    try:
        out = subprocess.run(['objdump', '-T', path], capture_output=True, text=True).stdout
    except OSError:
        return      # no objdump: can't check
    newer = sorted({v for v in re.findall(r'GLIBC_(\d+\.\d+)', out)
                    if tuple(map(int, v.split('.'))) > P.GLIBC}, key=lambda v: tuple(map(int, v.split('.'))))
    if newer:
        os.unlink(path)
        have = '.'.join(map(str, P.GLIBC))
        raise FetchError(f'{os.path.basename(path)} needs GLIBC_{newer[-1]}, the firmware has {have} '
                         f'(objdump -T shows which functions; see CLAUDE.md)')


def load_sources():
    path = os.path.join(P.PROFILE_DIR, 'sources.toml')
    if not os.path.exists(path):
        return {}
    with open(path, 'rb') as f:
        return tomllib.load(f)


def download(url, sha256):
    os.makedirs(DOWNLOADS, exist_ok=True)
    path = os.path.join(DOWNLOADS, os.path.basename(url.split('?')[0]) or 'download')
    if not os.path.exists(path):
        print(f'            downloading {url}')
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(path + '.part', 'wb') as f:
                shutil.copyfileobj(r, f)
        except OSError as e:
            raise FetchError(f'download failed: {e}')
        os.replace(path + '.part', path)
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    got = h.hexdigest()
    if not sha256:
        print(f'            WARNING: no sha256 in sources.toml for {url}; it is {got}')
    elif got != sha256.lower():
        os.unlink(path)
        raise FetchError(f'sha256 mismatch for {url}: got {got}')
    return path


def extract(archive, member):
    """Read one file from a .deb, tar(.gz/.xz/.bz2/.zst) or .zip."""
    import io
    import tarfile
    import zipfile
    want = member.lstrip('./')
    if archive.endswith('.deb'):
        with open(archive, 'rb') as f:
            data = f.read()
        if not data.startswith(b'!<arch>\n'):
            raise FetchError(f'{archive} is not a .deb')
        pos = 8
        while pos + 60 <= len(data):  # ar archive: 60-byte headers
            name = data[pos:pos + 16].decode().strip().rstrip('/')
            size = int(data[pos + 48:pos + 58])
            body = data[pos + 60:pos + 60 + size]
            pos += 60 + size + (size & 1)
            if name.startswith('data.tar'):
                if name.endswith('.zst'):
                    body = subprocess.run(['zstd', '-dc'], input=body, capture_output=True, check=True).stdout
                return extract_tar(tarfile.open(fileobj=io.BytesIO(body)), want)
        raise FetchError(f'no data.tar in {archive}')
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if name.lstrip('./') == want:
                    return z.read(name)
        raise FetchError(f'{member} not found in {archive}')
    return extract_tar(tarfile.open(archive), want)


def extract_tar(tar, want):
    with tar:
        for m in tar:
            if m.name.lstrip('./') == want:
                while m.issym() or m.islnk():  # packages often ship the soname as a symlink
                    target = m.linkname if m.islnk() else os.path.normpath(
                        os.path.join(os.path.dirname(m.name), m.linkname))
                    m = tar.getmember(next(n for n in tar.getnames() if n.lstrip('./') == target.lstrip('./')))
                return tar.extractfile(m).read()
    raise FetchError(f'{want} not found in archive')


def sha1(data):
    return hashlib.sha1(data).hexdigest()


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def mounts_below(root):
    root = os.path.realpath(root)
    found = []
    with open('/proc/mounts') as f:
        for line in f:
            mp = line.split()[1].replace('\\040', ' ')
            if mp != root and mp.startswith(root + '/'):
                found.append(mp)
    return found


# The image remembers which kit built it (etc/mcu2-patchkit), so `./tesla check` can tell whether
# it is up to date without reading every file. The version is a hash over everything in the kit
# that ends up in the image: this script, the shared src/ and the profile's folder (its kit.py
# pins all payload hashes; sources.toml only says where they come from, so it's left out). The
# stamp also names the profile (profile=; missing in stamps before 2026-09-27: LEGACY_PROFILE).
STAMP = 'etc/mcu2-patchkit'
OLD_STAMP = 'etc/mcu3-patchkit'   # kits before 2026-09-25 (the unit was called MCU3 by mistake)


def kit_version():
    h = hashlib.sha1()
    files = [os.path.abspath(__file__)] + sorted(os.path.join(SHARED_SRC, f) for f in os.listdir(SHARED_SRC))
    for d, dirs, names in sorted(os.walk(P.PROFILE_DIR)):
        dirs[:] = sorted(x for x in dirs if x != '__pycache__')
        files += sorted(os.path.join(d, n) for n in names if n != 'sources.toml')
    for path in files:
        h.update(os.path.relpath(path, KIT).encode() + b'\0')
        with open(path, 'rb') as f:
            h.update(f.read())
    return h.hexdigest()[:12]


def git_describe():
    try:
        r = subprocess.run(['git', '-C', KIT, 'log', '-1', '--format=%h %cs'], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ''
    except OSError:
        return ''


def read_stamp(root):
    """The stamp as a dict (kit, commit, date, skipped), or {} if there is none."""
    try:
        path = os.path.join(root, STAMP)
        if not os.path.exists(path):
            path = os.path.join(root, OLD_STAMP)
        with open(path) as f:
            return dict(line.rstrip('\n').split('=', 1) for line in f if '=' in line)
    except OSError:
        return {}


def describe_change(path, content):
    """How a text file differs from what the kit writes, for --check."""
    if not os.path.isfile(path):
        return 'missing'
    try:
        have = open(path, encoding='utf-8').read().splitlines()
    except UnicodeDecodeError:
        return 'a binary file, replaced by a script'
    import difflib
    diff = [l for l in difflib.unified_diff(have, content.splitlines(), lineterm='', n=0)
            if l[:1] in '+-' and not l.startswith(('+++', '---'))]
    added = sum(l.startswith('+') for l in diff)
    return f'differs: {added} lines new/changed, {len(diff) - added} removed'


def list_profiles():
    for name in profile_names():
        m = profile_meta(name)
        d = m.get('detect', {})
        print(f"{name:22} {m.get('description', '')}")
        print(f"{'':22} matches firmware {d.get('version')} with UI_PLATFORM={d.get('platform')}; car {d.get('car')}")


def list_all():
    print(f'Profile {PROFILE}: {profile_meta(PROFILE).get("description", "")}\n')
    print('Groups (use with --skip):')
    for g, d in P.GROUPS.items():
        print(f'  {g:9} {d}')
    print('\nBinary patches (ids also usable with --skip):')
    for b in P.BINARIES:
        print(f"  {b['path']}")
        for ident, group, off, old, new, desc in b['patches']:
            print(f'    {ident:24} [{group}] 0x{off:x}: {desc}')
        for ident, group, off, orig, old, desc in b.get('retired', []):
            print(f'    {ident:24} retired, restored where found: {desc}')
    print('\nRetired files (removed where found):')
    for group, rel, why in P.RETIRED_FILES:
        print(f'  [{group}] {rel}: {why}')
    print('\nPayload files:')
    for group, rel, mode, _ in P.PAYLOAD_FILES:
        print(f'  [{group}] {rel} ({oct(mode)})')
    print('\nSymlinks:')
    for group, rel, target in P.SYMLINKS:
        print(f'  [{group}] {rel} -> {target}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('root', nargs='?', help='root of the MCU rootfs (e.g. the mounted mcu2.ext4)')
    ap.add_argument('--check', action='store_true', help='only report what is applied / missing')
    ap.add_argument('--list', action='store_true', help='list all groups, patches and files')
    ap.add_argument('--skip', action='append', default=[], metavar='GROUP|ID',
                    help="skip a group (the profile's, see --list) or a patch id; repeatable")
    ap.add_argument('--no-backup', action='store_true', help='do not keep <file>.orig backups')
    ap.add_argument('--force', action='store_true', help='run even if something is mounted inside ROOT')
    ap.add_argument('-q', '--quiet', action='store_true', help="only show what isn't already ok")
    ap.add_argument('--kit-version', action='store_true',
                    help="print the kit version (hash) and exit: the profile's (--profile, or detected from ROOT; "
                         f'without either {LEGACY_PROFILE})')
    ap.add_argument('--profile', metavar='NAME', help='use this profile instead of detecting it (see --profiles)')
    ap.add_argument('--profiles', action='store_true', help='list the profiles and the firmware each is for')
    ap.add_argument('--detect', action='store_true', help="print ROOT's profile name and exit")
    ap.add_argument('--info', action='store_true',
                    help="print the profile's values for ./tesla (key=value; ROOT's profile or --profile) and exit")
    ap.add_argument('--vin', help='write /var/etc/vin')
    ap.add_argument('--birthday', help='write /var/etc/birthday')
    args = ap.parse_args()

    if args.profiles:
        list_profiles()
        return 0
    root = os.path.abspath(args.root) if args.root else None
    if root and not os.path.isfile(os.path.join(root, 'usr/tesla/UI/bin/QtCar')):
        sys.exit(f'{root} does not look like the MCU rootfs (usr/tesla/UI/bin/QtCar missing)')
    if args.detect:
        if not root:
            ap.error('--detect needs ROOT')
        print(detect_profile(root))
        return 0
    if args.info:
        name = args.profile or (detect_profile(root) if root else LEGACY_PROFILE)
        m = profile_meta(name)
        d, scr, feat = m.get('detect', {}), m.get('screen', {}), m.get('features', {})
        print(f"profile={name}\ncar={d.get('car', '')}\ncenter={scr.get('center', '')}\n"
              f"cluster={scr.get('cluster', '')}\nmit_shm={str(scr.get('mit_shm', True)).lower()}\n"
              f"maps={str(feat.get('maps', False)).lower()}\nservices={' '.join(m.get('services', {}).get('base', []))}")
        return 0
    if args.list or args.kit_version:
        load_profile(args.profile or (detect_profile(root) if root else LEGACY_PROFILE))
        if args.list:
            list_all()
        else:
            print(kit_version())
        return 0
    if not root:
        ap.error('ROOT is required (or use --list / --profiles)')
    load_profile(args.profile or detect_profile(root))
    for s in args.skip:
        known = set(P.GROUPS) | {x[0] for b in P.BINARIES for x in b['patches']}
        if s not in known:
            sys.exit(f'--skip {s}: unknown group / patch id (see --list)')

    busy = mounts_below(root)
    if busy and not args.check and not args.force:
        sys.exit('Things are mounted inside the rootfs (QtCar running?). Stop it / run teardown first:\n  '
                 + '\n  '.join(busy) + '\n(or pass --force)')
    if os.geteuid() != 0 and not args.check:
        print('WARNING: not running as root - file ownership (root / tesla) will not be set.\n')

    r = Runner(root, args.check, not args.no_backup, args.skip, args.quiet)
    version_fw, platform = firmware_id(root)
    print(f'Rootfs: {root}' + ('  (check only)' if args.check else ''))
    print(f'Firmware {version_fw} ({platform}), profile {PROFILE}' + (' (--profile)' if args.profile else ''))
    r.binaries()
    r.retired_files()
    r.payload()
    r.symlinks()
    r.system()
    r.audio()
    r.identity(args.vin, args.birthday)

    c = r.counts
    print(f"\nSummary: {c['ok']} already ok, {c['done']} applied, {c['todo']} todo, {c['error']} errors")
    version, stamp = kit_version(), read_stamp(root)
    if stamp and stamp.get('profile', LEGACY_PROFILE) != PROFILE:
        print(f"Note: the image was patched with profile {stamp.get('profile', LEGACY_PROFILE)}, this is {PROFILE}")
    if args.check:
        if not stamp:
            print('Kit version: the image has no stamp (built by a kit older than 2026-09-24)')
        elif stamp.get('kit') == version and c['todo']:
            print(f"Kit version: {version} (applied {stamp.get('date', '?')}), but {c['todo']} item(s) differ "
                  f"from it (changed afterwards?)")
        elif stamp.get('kit') == version:
            print(f"Kit version: {version}, the image is up to date (applied {stamp.get('date', '?')})")
        else:
            print(f"Kit version: the image has {stamp.get('kit')} ({stamp.get('commit') or '?'}, applied "
                  f"{stamp.get('date', '?')}), this kit is {version}")
    elif not c['error'] and (c['done'] or stamp.get('kit') != version or stamp.get('profile') != PROFILE
                             or stamp.get('skipped') != ','.join(args.skip)):
        import datetime
        text = (f'kit={version}\nprofile={PROFILE}\ncommit={git_describe()}\n'
                f'date={datetime.datetime.now().isoformat(timespec="seconds")}\n'
                f'skipped={",".join(args.skip)}\n')
        r.write_atomic(r.p(STAMP), text.encode(), 0o644)
        if os.path.exists(r.p(OLD_STAMP)):
            os.unlink(r.p(OLD_STAMP))
        print(f'Kit version {version} written to /{STAMP}')
    return 1 if c['error'] else 0


if __name__ == '__main__':
    sys.exit(main())
