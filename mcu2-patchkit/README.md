# mcu2-patchkit

Turns a pristine MCU2 firmware dump into a rootfs that runs in the chroot: binary patches,
preload shims, open-source libraries, config. What that takes depends on the firmware, so each
supported version has a **profile**; `mcu2_patch.py` detects which one a rootfs needs.

| Profile | Firmware | What it does |
|---|---|---|
| `2019.20.4.2-model3` | Model 3, 2019.20.4.2 (dump in `../mcu2-original`) | 5 byte patches in 4 binaries, Mesa 18, CEF shims, audio, system config (below) |
| `2026.8.3-models` | Model S/X, 2026.8.3 (`../mcu2s-original`) | Mesa 25.0.7 with llvmpipe, the escalator stub, the Chromium media/Browser/Theater jobs, audio, games, a few checked patches (below) |

`mcu2_patch.py --profiles` lists them, `--list` shows everything a profile does.

## Profiles

```
mcu2_patch.py          the engine: detection, applying, --check/--list, stamp, downloads
src/                   shared by the profiles: qtcar-service.sh, sv-wrapper.sh
profiles/<name>/
  profile.toml         [detect] version + platform (+ car), [screen] sizes, [features]: ./tesla's defaults
  kit.py               the tables: BINARIES, PAYLOAD_FILES, SYMLINKS, DIRECTORIES, TEXT_FILES, ...
  sources.toml         where its payload files come from
  src/                 what gets compiled for it (the Model 3's shims)
payload/<name>/        downloaded / built files (untracked); downloads/ caches the packages
```

Detection: `usr/tesla/UI/bin/customerVersion.txt` (first word) and `UI_PLATFORM` in
`etc/RunQtCar.env` must both match a profile's `[detect]`; an unknown firmware is refused
(`--profile NAME` forces one). `--detect ROOT` prints the profile, `--info` its values as
`key=value` for the shell tools (`profile_info` in tools/common.sh). A firmware of the same car
with another version gets its own profile: binary patches are tied to exact files.

## The Model 3 profile (2019.20.4.2)

### The binary patches

| Patch | File | Why |
|---|---|---|
| `utils-assert-nonfatal` | libQtCarUtils | `Logger::logAssert` writes to address 0 on purpose outside "prod" mode; now it only logs, as on the car |
| `cef-no-oscillator` | libcef | Tesla's CEF has Blink's stub FFT: a WebAudio oscillator crashed the renderer |
| `sim-no-di-2hz`, `sim-no-di-10hz` | libQtCarSim | the simulator's drive inverter sends a wrong gear/speed; tesla-can.py sends those frames |
| `audiod-no-a2b` | audiod | no A2B amplifier bus on a PC (audiod exited) |

**Retired** (2026-09-25): 17 patches and 2 files of earlier kits did nothing or only hid log
lines. The kit puts the firmware's bytes back where it finds them, and removes the files:
- `qtcar-cgroup-exit-1/2`: QtCar exits if the escalator can't put it into its cgroups. It can
  (`./tesla start` mounts net_cls; QtCar lands in `net_cls:/qtcar`).
- `gui-escalator-*`, `gui-cgroup-exit-*`, `gui-cef-nosandbox`: patched libQtCarGUI's copy of
  `ChromiumManager::initializeCef`, which is never called (QtCar has its own).
- `utils-assert-ctor/-protected/-duplicate`, `media-assert-pause`, `gui-energy-*`: each skipped
  one `logAssert` call; with `utils-assert-nonfatal` that only hid a log line. None of them fired
  in test runs.
- `gui-camera-reset/-init`, `gui-video-ximagesink`: undone before already (the old `*-revert`).
- `UI/bin/chrome-sandbox` (fake setuid helper) and `UI/lib/chrome-sandbox` (patched copy): CEF
  runs with `no_sandbox` and never starts a sandbox helper.
Tried instead of the assert patch: QtCar's `--prod` switch. It makes asserts non-fatal too, but
changes feature defaults (vector map tiles on: an empty map) and would be needed by every
service; one 2-byte patch is simpler.

### Contents

| Path (in `profiles/2019.20.4.2-model3/`) | What |
|---|---|
| `kit.py` | The tables. The engine checks every patch's original bytes and SHA-1 first |
| `sources.toml` | Where each library comes from: Ubuntu 16.04 package URL, sha256, path inside the package |
| `src/icu_preload.c` | Source of `icu_preload.so` (verified: compiles to the same code) |
| `src/egl_pixmap_shim.c` | Preloaded into QtCar: shows the browser's picture (EGL images from X pixmaps without DRI) and keeps the browser's window away from a desktop's window manager |
| `src/cef_nosandbox.c` | Preloaded into QtCar: `CefSettings.no_sandbox`, CEF logging, DevTools |

### Where payload files come from

The repository contains no binaries: the libraries are downloaded, the shims are built. For each
file, `mcu2_patch.py` tries in order:

1. `payload/<profile>/<path>` (the local cache), if its hash matches (kits before profiles
   cached in `payload/<path>`: a matching file there is taken over);
2. the `url` in the profile's `sources.toml`: a package (`.deb`, `.tar.*`, `.zip`) from the Ubuntu 16.04
   archive with `member` naming the file inside. Packages are cached in `downloads/`. The
   download is checked against `sha256` and the extracted file against the exact hash in
   the profile's `kit.py`;
3. for `icu_preload.so`, `egl_pixmap_shim.so` and `cef_nosandbox.so`: compiled from `src/` with
   gcc (the kit refuses a result that needs a glibc newer than the chroot's 2.22).

Tested 2026-09-24: all files obtained into an empty `payload/` (then 17 downloaded, 4 built; the
fake chrome-sandbox is gone since).

## Build a fresh image

```sh
./tesla build-image --fresh          # new mcu2.ext4 from mcu2-original/: copy, kit, maps
./tesla build-image                  # after a git pull: apply what's new (only what's missing)
./tesla build-image --check          # what an update would change
```

By hand, `--fresh` is:

```sh
truncate -s 6G mcu2.ext4 && mkfs.ext4 -F mcu2.ext4
sudo mkdir -p /mnt/mcu3new && sudo mount -o loop mcu2.ext4 /mnt/mcu3new
sudo cp -a mcu2-original/. /mnt/mcu3new/
sudo python3 mcu2-patchkit/mcu2_patch.py /mnt/mcu3new
sudo umount /mnt/mcu3new
```

Another image: `IMAGE=` in `tesla.conf`. Another firmware: its own config file with `IMAGE=`,
`DUMP=` and `SANDBOX_ROOT=` (e.g. `tesla-s.conf`), used with `./tesla --config tesla-s.conf build-image ...`.

**Kit version**: after a successful run the kit writes `/etc/mcu2-patchkit` into the image
(`kit=` a hash over `mcu2_patch.py`, `src/` and the profile's folder without `sources.toml`,
`profile=`, the git commit, the date, skipped groups; stamps from before 2026-09-27 have no
`profile=`: the Model 3 one). `./tesla check` compares it with the current kit and says when the
image needs `./tesla build-image`. Any edit of those files changes the version, also a comment.

## Usage

```sh
python3 mcu2_patch.py --profiles           # the profiles and the firmware each is for
python3 mcu2_patch.py --list [--profile P] # all groups / patches / files (default: the Model 3's)
sudo python3 mcu2_patch.py ROOT --check    # report only, changes nothing (text files: missing / N lines differ)
sudo python3 mcu2_patch.py ROOT -q         # only what isn't already ok
python3 mcu2_patch.py --kit-version --profile P   # the current kit version of a profile
sudo python3 mcu2_patch.py ROOT            # apply
sudo python3 mcu2_patch.py ROOT --skip cef --skip sim-no-di-2hz
sudo python3 mcu2_patch.py ROOT --vin ... --birthday ...
```

Groups of the Model 3 profile: `core` (needed binary patches), `cef` (browser), `mesa`,
`assets`, `system`, `identity`, `vehicle` (simulator without its drive-inverter module, for
`--vehicle`, see `../vehicle/README.md`), `audio`. The script refuses to run while something is
mounted inside ROOT, so stop QtCar first (`./tesla stop`). Patched and replaced files are kept as `<file>.orig`.

## The Model S/X profile (2026.8.3)

The firmware needs no binary patch to start (asserts already only log). Its Mesa 25.0.7 has only
the Intel hardware drivers (iris, crocus): on Xvfb QtCar crashed creating its first EGL surface.
The profile adds Debian 13's Mesa 25.0.7 (the same version, with llvmpipe and all Gallium
drivers), glvnd, LLVM 19 and the nine libraries they need that the firmware lacks, in
`/opt/mesa` next to the firmware's own. `/opt/mesa/env.sh` (sourced before QtCar/QtCarCluster)
puts it first: `LD_LIBRARY_PATH`, `LIBGL_DRIVERS_PATH`, `GBM_BACKENDS_PATH`, the glvnd EGL vendor
file. Software rendering also needs `LIBGL_ALWAYS_SOFTWARE=1`.

Groups (`mcu2_patch.py --list` on its rootfs): `mesa`, `system` (startup.sh for QtCar and
QtCarCluster `--emulated`, the escalator stub, settings.conf, D-Bus policies), `media` (the
media/Browser/Theater Chromium jobs and their EGL pixmap shim; Chromium's scalar SHA-1/SHA-256 on
CPUs without SHA-NI), `wifi` (ConnMan without the car's AppArmor hat), `games` (2048, chess and
backgammon read the touchscreen in screen coordinates), `audio` (the amplifiers' card on
snd-aloop, as for the Model 3), `camera` (the desktop camera without the car's deserializer).
Maps are installed as for the Model 3 (`maps = true`; tiles with elevation, see
`../navigation/README.md`). Retired: the earlier `NativeMaps::TileManager` patch and a games
window size patch (the original bytes are restored). `./tesla start` feeds the host's internet status into
QtCar's `LINK_linkState`, `LINK_wifiState`, and `CONN_connectedToInternet` data values instead.
CenterDisplay computes `GUI_canUseInternet` for maps; MediaV2 separately uses
`LINK_wifiState=online` for its internet availability. It does not set the car country. Details:
`../notes/session9-2026-09-27.md` and `../notes/session10-2026-09-27.md`.

## Differences from the old image (Model 3)

- `icu_preload.so` lives in `/usr/lib` instead of `/tmp`, and `/startup.sh` points there.
- `/startup.sh` also sets DNS (`/var/run/connman/resolv.conf`). Without it no map tiles
  load. This was the actual cause of the "invalid API key" issue.
- `libLLVM-6.0.so`, `libexpatw.so.1` and `libsensors.so.4` are symlinks instead of duplicate copies.
- Left out on purpose: the Google API key patch (reverted, DNS was the real issue), the libcef.so
  sandbox patch (reverted), crash dumps, `/model3_init.sh` (old QEMU attempt), QtCarSettings.db.

## Notes

- The browser (2026-09-23, session 4). What it needed:
  - `src/cef_nosandbox.c`, preloaded by `startup.sh`: sets `CefSettings.no_sandbox` in a
    `cef_initialize()` wrapper. Env: `CEF_LOG_SEVERITY` (startup.sh: warning), `CEF_LOG_FILE`,
    `CEF_REMOTE_DEBUGGING_PORT` (startup.sh: 9222, DevTools on localhost:
    `curl http://127.0.0.1:9222/json`), `CEF_EXTRA_ARGS` (Chromium switches).
  - The old `gui-cef-nosandbox` patch never did anything: it patched libQtCarGUI's copy of
    `ChromiumManager::initializeCef`, but QtCar uses its own copy (retired, like
    `gui-escalator-*` and `gui-cgroup-exit-*`).
  - `UI/lib/libcef.so` links to `/usr/lib/libcef.so` and CEF looks for its resources next to it;
    `v8_context_snapshot.bin` (plus `devtools_resources.pak`, `swiftshader`) were missing there
    and every renderer died with "Failed to deserialize the V8 snapshot blob".
  - `cef-no-oscillator`: Tesla's CEF has Blink's stub FFT, so a WebAudio `OscillatorNode` crashes
    the renderer (kernel log: `tesla-cef-zygot segfault ... libcef.so[4165885]`, an AVX vector
    loop writing to a null array; the SSE path crashes the same way). `createOscillator()` now
    returns null. `new OscillatorNode()` and `createPeriodicWave()` would still crash.
  - `egl_pixmap_shim.c` read X pixmap sizes wrong (0x0).
- Audio (session 5, `--audio`): apps write to tplug channels (/dev/shm), AudioWeaver
  (`AWE_command_line_tesla`, DSP) processes, **audiod** does the ALSA I/O ("pump") for it and
  owns the sound card. On a PC: snd-aloop as card `model3` (asound.conf block maps pcm 0/2/3 to
  its subdevices), AWE gets `/etc/asound-awe.conf` via `ALSA_CONFIG_PATH` so it can't grab the
  card (on the car its minijail has no /dev/snd; otherwise audiod gets EBUSY and never pumps),
  the eCall tplugs are resampled (48 vs 8 kHz, AWE exited), and `audiod-no-a2b` keeps audiod
  from starting the A2B stack (I2C amplifiers/mics; a2b_stackAlloc fails and it exits).
  audiod commands: `echo "volume ?" | socat - tcp:127.0.0.1:18466` (`?` reads, `<cmd> <v>` sets,
  list in /opt/audioweaver/dsp-control.csv). Base amp channels (1-based): 4,5,7 left, 2,6,8
  right, 1 both, 3 unused; `./tesla start` sums them to stereo (`sox remix -m`) into pw-play.
- `startup.sh` writes `vm.mmap_min_addr=0` and `vm.overcommit_memory=1` to the **host** kernel
  (`/proc` is bind-mounted). They stay set until reboot.
- Developer mode needs no patch. While QtCar is running, inside the chroot:
  `wget -q -O - "http://127.0.0.1:4220/_data_set_value_request_?name=GUI_developerMode&value=true"`

## Firmware services

Besides QtCar, the firmware has 17 more services built on the same framework (`/etc/sv/qtcar-*`).
The kit installs `/usr/local/bin/qtcar-service`, which starts one of them the way its runit script
does on the car (same binary, options and user), without minijail, AppArmor, cgroups or firewall,
and on loopback instead of the car's `192.168.90.x` network:

```sh
./tesla start --services "qtcar-connman"    # started before QtCar, logs in logs/
./tesla services                            # the available services
```

Survey 2026-09-23, each service alone for 6 s: all 17 start and keep running.
- `qtcar-vehicle`: the CAN↔data-value bridge (VehicleService :4030, Power :4170); it expects
  CAN frames as UDP from the gateway on :1234 / :31415
- `qtcar-carserver`, `-gpsmanager`, `-audiod`, `-evlogservice`, `-connman`, `-mediaserver`,
  `-tmserver`, `-nuanceserver`, `-monitor`, `-radioserver`, `-bluetooth`, `-speechrecognizer`,
  `-spotifyserver`, `-ecallclient`, `-connmetrics`, `-startup`: run; the only errors are missing
  hardware (LTE GPIO) and unsupported features ("Service Directives")

Not a runit service but in `/usr/tesla/UI/bin`: `QtCarSimService`, Tesla's vehicle simulator
(486 `SIM_*` signals: position, battery, doors, gear, charging, …). `qtcar-service qtcar-sim`
starts it: it acts as the gateway and sends a simulated car's CAN frames to `qtcar-vehicle`.
`./tesla start --vehicle` uses it (details in `../vehicle/README.md`).

`./tesla start` restarts services that exit, like runit: `qtcar-vehicle` exits on purpose when the
car configuration changes.

Testing without sudo: a user-owned copy of the rootfs runs in rootless podman:
`podman run --rm --network host -e QCSVC_NOUSER=1 --rootfs $PWD/sandbox/rootfs qtcar-service qtcar-vehicle`
(`sandbox/` = `cp -a mcu2-original sandbox/rootfs` + this kit; a few root-only files are skipped by
the copy, e.g. `/usr/bin/escalator`, so QtCar dies in CEF there after ~20 s; see
`notes/session4-*.md` for running it under gdb with the browser skipped).
