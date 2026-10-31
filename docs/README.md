# Documentation

| Document | For |
|---|---|
| [setup.md](setup.md) | everything needed once: host packages, kernel modules, the firmware dump, building the image, maps |
| [usage.md](usage.md) | the `./tesla` commands, all options and `tesla.conf` settings, the screen and the web panel |
| [features.md](features.md) | each feature: what it does, what it needs, how it works, where its logs are, its limits |
| [how-it-works.md](how-it-works.md) | the architecture: image, chroot, patch kit, firmware services, ports, data paths, run folders |
| [troubleshooting.md](troubleshooting.md) | checks, log summaries, known symptoms and their fixes, problem reports |
| [development.md](development.md) | working on this repo: the sudo-free sandbox, the patch kit, testing, conventions |

Both firmwares are covered: the Model 3's 2019.20.4.2 and the Model S/X's 2026.8.3 (its setup:
[setup.md](setup.md#3-the-image); how it differs: [how-it-works.md](how-it-works.md#the-model-sx-202683);
each feature's section in [features.md](features.md) says what works on it).

Deeper technical details are in the component READMEs: [`mcu2-patchkit/`](../mcu2-patchkit/README.md),
[`tesla-touch/`](../tesla-touch/README.md), [`navigation/`](../navigation/README.md),
[`vehicle/`](../vehicle/README.md), [`stick/`](../stick/README.md) (the bootable USB stick). The session notes in [`notes/`](../notes/README.md) tell how
everything was found out.

## What needs what

| Feature | Switch | On the host | In the image | Firmware services started | Host helpers | Logs (`logs/<run>/`) |
|---|---|---|---|---|---|---|
| UI, touch, screen | always | Xvfb, x11vnc, xdpyinfo, a VNC viewer, `tesla-touch` (+ udev rule), `/dev/uinput` | kit groups `core`, `mesa`, `system`, `assets` | QtCar (`/startup.sh`), escalator | Xvfb, x11vnc, tesla-touch, viewer | `qtcar.log`, `touch.err`, `xvfb.log`, `x11vnc.log` |
| Browser | always | – | kit group `cef` | (inside QtCar: CEF) | – | `qtcar.log` (DevTools on :9222) |
| Model S/X web media | always in 2026 profile | python3; VNC touch uses DevTools on local :9223 | kit group `media` | `media-webapp-server`, Chromium session bus, `chromium-adapter`, `chromium-app-input`, `chromium-app` | `host-connectivity.py`, VNC: `media-touch.py` | `chromium-app.log`, `chromium-adapter.log`, `media-webapp-server.log`, `media-touch.log` |
| Arcade (BBR2, MAME) | always; steering/pedal need `--vehicle` | `/dev/uinput`, `tesla-touch/99-tesla-touch.rules` (hides the `game-*` devices) | `/sbin/sv` wrapper, `qtcar-service` (`system`) | `cobalt` + `cobalt-input`, `mame` on demand | `tools/game-windows.py` | `game-windows.log`; image: `/run/qtcar-sv/*.log` |
| Fireplace, dog mode, HAL 9000 videos | always | – | `/sbin/sv` wrapper (`system`) | `fireplace`/`dog-mode`/`hal-9000` on demand | – | `qtcar.log` |
| Offline navigation | maps installed, `NAV=1` | podman (only to build tiles) | map tiles (`./tesla build-image`) | `valhalla`, `qtcar-tmserver` | – | `valhalla.log`, `qtcar-tmserver.log` |
| Vehicle data + panel | `--vehicle` | python3; once: `unicorn`, `pyelftools` | kit group `vehicle` | `qtcar-sim`, `qtcar-vehicle` | `tesla-can.py` (panel :8099) | `qtcar-sim.log`, `qtcar-vehicle.log`, `can.log` |
| GPS | `--gps` | python3; receiver: read access (group `dialout`) | – | `qtcar-gpsmanager` | `tesla-gps.py` | `gps.log`, `qtcar-gpsmanager.log` |
| Sound | `--audio` | `snd-aloop`, `arecord`, `pw-play` (PipeWire) or `pacat`/`aplay`; Model 3 also needs `sox`; `tools/99-tesla-sound.rules` | Model 3: kit group `audio` | Model 3: `audioweaver`, `audiod`, `qtcar-audiod`, `qtcar-mediaserver`; Model S/X: `qtcar-audiod`, `qtcar-mediaserver` | audio pipeline, speech-state keeper | `audio.log`, `qtcar-audiod.log`; Model 3: `audiod.log`, `audioweaver.log` |
| Bluetooth | `--bluetooth` | a Bluetooth adapter (BlueZ stopped while it runs) | – | `dbus`, `bsa_server`, `btd`, `qtcar-bluetooth`, `a2dpbridge` (with audio) | `bluetooth/hci-bridge.py` | `bsa_server.log`, `btd.log`, `qtcar-bluetooth.log`, `bt-bridge.log` |
| Cellular | `--cellular` or `auto` | ModemManager with a modem, NetworkManager connecting it (Fedora: `NetworkManager-wwan`) | – | – | `host-connectivity.py --cellular` (Model 3: `--http`, no sudo) | `host-connectivity.log` |
| USB music | `--music DIR` | a folder with music | `[usb] media_path` (`system`) | `qtcar-mediaserver` | – | `qtcar-mediaserver.log` |
| Backup camera | `--camera DEV` or `auto` | `ffmpeg`, `v4l2loopback` with a second device (`/dev/video32`) | `[bkcam]` (set at start) | – | ffmpeg | `camera.log` |

`./tesla check` checks all of it and says what's missing.
