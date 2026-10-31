# How it works

## The pieces

Three views of the same setup: on the left what runs on the host, on the right what runs in the
chroot (the patched firmware image). Everything talks over loopback.

**Screen and touch**

```mermaid
flowchart LR
  subgraph host1["Host"]
    viewer["VNC viewer"] -- "mouse" --> vnc["x11vnc :5900"] --> xvfb["Xvfb :1<br/>virtual screen"]
    xvfb -- "pointer" --> touch["tesla-touch"]
  end
  subgraph chroot1["chroot/ (firmware)"]
    qtcar["QtCar<br/>the UI"] -- "videos, browser setup" --> esc["escalator"]
  end
  touch -- "/dev/input/touch" --> qtcar
  qtcar -- "draws" --> xvfb
```

**Vehicle data, GPS, navigation**

```mermaid
flowchart LR
  subgraph host2["Host"]
    can["tesla-can.py<br/>web panel :8099"]
    gps["tesla-gps.py"]
  end
  subgraph chroot2["chroot/ (firmware)"]
    sim["QtCarSimService<br/>simulated gateway"]
    veh["QtCarVehicle"]
    gpsm["QtCarGpsManager"]
    qtcar["QtCar"]
    tm["QtCarTMServer"] <--> val["valhalla_server :8002<br/>map tiles"]
  end
  sim -- "CAN frames<br/>UDP :1235" --> can
  can -- "merged frames<br/>UDP :1234" --> veh
  veh -- "button requests<br/>UDP :4321" --> can
  gps -- "NMEA<br/>UDP :63277" --> gpsm
  gps -. "gear, speed, odometer" .-> can
  veh -- "vehicle data" --> qtcar
  gpsm -- "position" --> qtcar
  qtcar <-- "routes" --> tm
```

**Sound and camera**

```mermaid
flowchart LR
  subgraph chroot3["chroot/ (firmware)"]
    qtcar["QtCar"] -- "chimes" --> awe["AudioWeaver<br/>DSP"]
    media["media server"] -- "music" --> awe
    qaud["QtCarAudiod"] -- "volume, source" --> audiod["audiod"]
    awe <-- "8 channels" --> audiod
  end
  subgraph host3["Host"]
    aloop["snd-aloop card 'model3'"] --> play["arecord → sox → pw-play<br/>PC speakers"]
    cam["camera source<br/>e.g. scrcpy"] --> ffmpeg["ffmpeg"] --> v4l["/dev/video32<br/>v4l2loopback"]
  end
  audiod --> aloop
  v4l -- "backup camera" --> qtcar
```

- **The image** (`mcu2.ext4`) is a copy of the firmware's root filesystem with the patch
  kit applied. It's loop-mounted at `chroot/`; `/proc`, a few `/dev` nodes, a private tmpfs
  `/dev/shm`, a `net_cls` cgroup and the optional devices/folders are mounted into it.
- **QtCar** runs in the chroot as on the car (`/startup.sh` from the kit sets its environment
  and preloads the shims). Its privileged helper, the escalator, runs next to it (Model S/X:
  the kit's `escalator-stub` instead, which answers a fixed table of requests).
- **The firmware services** (`qtcar-*`, `valhalla`, `audiod`, ...) are started by
  `/usr/local/bin/qtcar-service` (from the kit) the way their runit scripts do it on the car:
  same binary, options and user, minus the car's sandboxing (minijail, AppArmor, cgroups,
  firewall), and on loopback instead of the car's internal network (192.168.90.x). They find
  each other by UDP broadcast on 127.255.255.255.
- **The host helpers** feed what the car's hardware would: the screen and touch, CAN frames,
  GPS, the speakers, the camera.

## The Model S/X (2026.8.3)

The same pieces, with these differences (profile `mcu2-patchkit/profiles/2026.8.3-models/`):

- **Two screens:** the center display (1200x1920) and the instrument cluster (1920x720,
  QtCarCluster, the car's `--ic --window 1.5`) are two screens of one Xvfb; the cluster has its own
  x11vnc (view only, port 5901). Xvfb runs as root: the firmware's processes draw in software
  through MIT-SHM, and only a root X server can attach their users' segments.
- **QtCar runs `--emulated`** (Tesla's desktop mode: the display hardware counts as present, no
  panel polling). The car's escalator never runs; the kit's `escalator-stub` answers QtCar's and
  audiod's requests (videos, games, game mode, audio bridges) from a fixed table and refuses the
  rest (`escalator-stub.log`).
- **No vehicle simulator:** `tesla-can.py --all-zero` sends every ECU message of the gateway's
  Ethernet stream itself (UDP :20101, 4-byte ids) and answers the UI's requests (UDP :20100), with
  its own presets and panel controls for this car (`vehicle/README.md`). Some values QtCar would get
  from hardware that isn't there (internet link, audio ready, the charge power) are set over D-Bus
  by `tools/host-connectivity.py`. With `--cellular` it also presents the PC's modem
  (ModemManager) as the car's LTE, on the Model 3 through QtCar's :4220
  ([features.md](features.md#cellular)).
- **Sound:** the firmware's audiod has AudioWeaver built in; it plays the car's two amplifiers
  (16 channels) into the `model3` loopback card, the host mixes them to stereo. The web media,
  Browser, games and Bluetooth reach it through the `virtual` loopback card and the bridges audiod
  starts.
- **Three Chromium instances** besides QtCar: the media web apps (`media-webapp-server` on :9000),
  the Browser and Theater, each with its session bus, input helper and localhost DevTools
  (9223/9224/9225); host helpers forward touches to their offscreen windows.
- QtCar's :4220 is its Spotify client here, not a data value server; QtCarVehicle's :4030 answers
  data value requests on both firmwares.

## The patch kit

`mcu2-patchkit/mcu2_patch.py` turns the pristine dump into the working image and can be applied
again at any time (it only does what's missing). Groups:

| Group | What |
|---|---|
| `core` | the one binary patch QtCar needs on a PC: asserts only log (as on the car) instead of crashing |
| `cef` | the browser: no-sandbox shim, EGL pixmap shim, V8 snapshot files, WebAudio oscillator patch |
| `mesa` | Mesa 18.0.5 software renderer, LLVM 6.0 and their libraries (open source, downloaded from the Ubuntu 16.04 archive) |
| `assets` | missing asset links |
| `system` | users, `settings.conf` keys, DNS, directories, `/startup.sh`, `qtcar-service`, the `sv` wrapper |
| `vehicle` | the simulator without its drive-inverter module (tesla-can.py sends that instead) |
| `audio` | the sound card on snd-aloop (`asound.conf`), audiod without the A2B amplifier bus |
| `identity` | VIN and birthday files (only with `--vin` / `--birthday`) |

These are the Model 3 profile's groups; the Model S/X profile has its own (`mcu2_patch.py --list`
on its rootfs): Mesa 25.0.7 from the Debian 13 archive, the escalator stub, the media/Browser/Theater
jobs, the EGL pixmap shim for their windows, audio, the built-in games' fixes.

Every binary patch checks the original bytes and the file's SHA-1 first; an unknown firmware
version is refused. Patches of earlier kits that turned out unnecessary are "retired": the kit
restores the firmware's bytes where it finds them, so an existing image ends up like a fresh one. `mcu2_patch.py --list` lists everything. Details and the history of each
patch: [`mcu2-patchkit/README.md`](../mcu2-patchkit/README.md).

## Ports

All on localhost (the chroot shares the host's network).

| Port | Who |
|---|---|
| TCP 5900 | x11vnc (VNC) |
| TCP 5901 | x11vnc of the cluster (Model S/X) |
| TCP 8099 | web panel (tesla-can.py) |
| TCP 9222 | CEF DevTools (browser) |
| TCP 4220 | QtCar's data value server (`_data_get_value_request_?name=...`) |
| TCP 4030 / 4160 / 4190 / 4060 / 4050 | QtCarVehicle / GpsManager / simulator / NetManager / Audiod |
| TCP 8002 | valhalla_server |
| TCP 18466 | audiod's command port |
| UDP 1234 | CAN frames to QtCarVehicle (from tesla-can.py) |
| UDP 1235 | CAN frames from the simulator to tesla-can.py |
| UDP 4321 | the UI's requests to the gateway (tesla-can.py answers) |
| UDP 20180 | tesla-can.py's control port (tesla-gps.py, `tesla-can.py --set`) |
| UDP 63277 | NMEA to GpsManager |
| TCP 9000 / 9223 / 9224 / 9225 | Model S/X: media web apps / DevTools of the media apps, Browser, Theater |
| UDP 20101 / 20100 | Model S/X: CAN frames to QtCarVehicle / the UI's requests |

The sandbox (`sandbox.sh`) uses the same ports; only one of the two can run.

## A run

`./tesla start` (in `tools/start.sh`): load the settings, check, `sudo -v`, clean up leftovers
of an earlier run, mount the image, start Xvfb and x11vnc, mount `/proc` and friends, start
tesla-touch and the viewer, set up the features (settings DB values, `settings.conf` keys,
devices), start the firmware services (each in a loop that restarts it when it exits), start the
host helpers, then QtCar in a loop. On exit (Ctrl+C, `./tesla stop`, a closed terminal): stop
everything in the chroot (SIGTERM, SIGKILL after 3 s), the helpers, unmount deepest first,
summarize the logs.

Each run has a folder `logs/<date-time>/`:

| File | What |
|---|---|
| `<service>.log` | output of each firmware service, with `tesla: <service> exited (N) ... [t=EPOCH]` on restarts |
| `qtcar.log` | QtCar's output |
| `syslog.log` | what the firmware services log through syslog (the chroot's `/dev/log` -> `/tmp/log`, `tools/syslog-sink.py`) |
| `can.log`, `gps.log`, `audio.log`, `camera.log`, `touch.err`, `xvfb.log`, `x11vnc.log` | the host helpers |
| `events.log` | start, the services started, QtCar starts and exits (with the reason for its own restarts), stop |
| `config` | the settings of the run and their source |
| `state`, `pids` | for `./tesla stop` / `status`: the start's pid, what it mounted, the helpers' pids |

`logs/latest` points to the last run, `logs/current` to the running one.

## QtCar's stored state

QtCar keeps settings and many car values in `/home/tesla/.Tesla/data/QtCarSettings.db` inside the
image (and services with their own user in their own home). Values stored there survive restarts
and config changes, which explains many "worked once, not again" effects.
`vehicle/qtcar-settings.py` reads and edits it. `/home/tesla/.Tesla/car/settings.conf` holds the
car's configuration file; the kit and `./tesla start` merge keys into it.
