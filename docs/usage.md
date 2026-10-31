# Usage

## Commands

| Command | What it does | sudo |
|---|---|---|
| `./tesla start [options]` | checks, then starts everything; stays in the foreground until Ctrl+C or `./tesla stop` | yes |
| `./tesla stop` | stops a running start from any terminal; cleans up what a killed run left (processes, mounts) | only if something is left |
| `./tesla status` | running or not; each service up/down with its restart count; the helpers; the image mount | no |
| `./tesla check` | host tools, kernel modules, the image and its kit version, maps, ports, the settings | no |
| `./tesla logs [RUN]` | summary of a run (default: the last one); `--list` lists the runs | no |
| `./tesla config` | the settings a start would use and where each comes from | no |
| `./tesla services` | the firmware services that can be added with `--services` | no |
| `./tesla build-image` | updates the image (kit + maps); `--check`, `--fresh`, `--maps REGION`, `--sandbox` | yes |
| `./tesla build-stick` | a disk image for a bootable USB stick that starts QtCar ([stick/README.md](../stick/README.md)) | no (podman) |
| `./tesla help` | the list of commands | no |

`./start_all.sh` is the old name of `./tesla start` and still works.

## Starting

```sh
./tesla start                                    # the UI with navigation (if maps are installed)
./tesla start --vehicle                          # + a parked car, web panel
./tesla start --vehicle --gps 37.4419,-122.1430  # + standing in Palo Alto
./tesla start --vehicle --gps "--pos 37.4419,-122.1430 --to 37.4275,-122.1697 --speedup 2"
./tesla start --audio --music ~/Music            # sound, a USB stick with music
./tesla start --camera /dev/video10              # backup camera (shown in R)
./tesla start --dry-run --audio                  # only check and show what would start
```

For the 2026.8.3 Model S/X image, use its config and the portrait VNC screen:

```sh
./tesla --config tesla-s.conf build-image         # after updating the patch kit
VIEWER=none ./tesla --config tesla-s.conf start --vnc --vehicle --audio
./tesla --config tesla-s.conf status
./tesla --config tesla-s.conf stop
```

Connect a viewer to `localhost:5900` for the center display and `localhost:5901` for the
cluster. The media stack and the separate Browser app start automatically. Browser loads public
HTTPS pages without a car account; its page, address bar and scrolling worked on the T480 over
VNC. TuneIn's public catalog can be browsed
and its stations play through `--audio`. Spotify opens a sign-in page, but the test image lacks
the provisioned Tesla car credentials and driver profile needed for its device-code request.
Apple Music and Apple Podcasts also fail before sign-in; Amazon Music, Audible and YouTube Music
reach their account activation pages. YouTube Music remains on its login page even after external
Google activation because its token poll requires the absent local Tesla music proxy. See
[features.md](features.md#model-sx-streaming-media-202683)
for the tested state of each source. Sign in only through the app's own page; do not place account
passwords or tokens in `tesla.conf`.

The Model S/X Theater tiles also start automatically. Open Theater from the bottom bar, then
choose YouTube, Netflix, Hulu, Twitch, TikTok or Tutorials. Public YouTube and Twitch video
played visibly on the T480; Netflix's public landing page opened, but playback still needs a
real account and has not been tested. Hulu redirected to Disney+ in the tested region. Swipe
down from the top handle to return to Theater. See
[features.md](features.md#model-sx-theater-202683) for the tile-by-tile results.

On native displays, the media touch bridge is also needed for taps in TuneIn's lower web area.
If Browser or a music source does not respond, check `logs/latest/browser-touch.log` or
`logs/latest/media-touch.log` along with the corresponding Chromium service log. Native Browser
and its on-screen keyboard were tested on the T480. VNC rendering can remain slow with software graphics even when
the touch bridge accepts events promptly.

Use `--audio` to hear TuneIn and Browser through the host speakers. The media source uses one
loopback capture and the separate Browser uses another; both appear as host playback streams.
Without `--audio`, source pages can display “playing” while Chromium has no sound device.

A start first runs the checks (only problems are shown; a failed check stops it, `--force` starts
anyway), then prints what runs and where:

```
==> Running  (stop: Ctrl+C here, or ./tesla stop)
    Screen    1920x1200, VNC localhost:5900 (viewer: krdc)
    Features  vehicle, audio, navigation
    Off       gps music camera  (./tesla start --help)
    Panel     http://localhost:8099/  (doors, charging, lights, services, logs)
    Services  valhalla qtcar-tmserver qtcar-sim qtcar-vehicle audioweaver audiod qtcar-audiod qtcar-mediaserver
    Logs      logs/20260924-154854/  (./tesla logs: summary)
```

QtCar restarts itself now and then to apply settings (e.g. after the first car configuration
arrives); `./tesla start` starts it again, like the car does. Services that exit are restarted too.
When it stops, a summary of the run's logs is printed.

## Options and settings

Every option has a setting of the same meaning in `tesla.conf`. The file is created from
`tesla.conf.example` on the first start, with every line commented out (= the default). Order:
defaults < `tesla.conf` < environment variables of the same name < command line options.
`--config FILE` (with any command, e.g. `./tesla --config tesla-s.conf build-image`) uses another
file: one per image/firmware, with `IMAGE`, `DUMP` and `SANDBOX_ROOT`.

| Option | Setting (default) | What |
|---|---|---|
| `--vehicle` / `--no-vehicle` | `VEHICLE=0` | fake vehicle data and the web panel |
| `--color C` | `VEHICLE_COLOR=` | Model 3: RedMulticoat SolidBlack SilverMetallic MidnightSilver DeepBlue PearlWhite; Model S/X: Default Black White Silver Grey Blue Green Brown Pearl SigRed Red SteelGrey MetallicBlack TitaniumCopper SignatureBlue (implies `--vehicle`) |
| `--wheels W` | `VEHICLE_WHEELS=` | Model 3: Pinwheel18 Stiletto19 Stiletto20 Stiletto20Staggered Gemini19Square Gemini19Staggered; Model S/X: Base19 Silver21 Charcoal21 Arachnid21Silver Turbine19 Tempest19SonicSilver ... (`./tesla start --help`) |
| `--performance P` | `VEHICLE_PERFORMANCE=` | Model 3: Base Performance Ludicrous BasePlus; Model S/X: Standard Ludicrous |
| `--gps SPEC` / `--no-gps` | `GPS=` | `LAT,LON`, or `tesla-gps.py` options (see [features.md](features.md#gps)) |
| | `TIMEZONE=auto` | the car's clock: a time zone such as `Europe/Berlin`; auto = this PC's (the car looks it up online, which can't work here) |
| `--audio` / `--no-audio` | `AUDIO=0` | sound |
| | `AUDIO_REMIX=...` | which amplifier channels go left/right (`sox remix` syntax; Model S/X: 16 channels, amp M then amp S) |
| `--bluetooth` / `--no-bluetooth` | `BLUETOOTH=0`, `BT_ADAPTER=hci0` | the car's Bluetooth on this PC's adapter (BlueZ is stopped while it runs); `BLUETOOTH=auto`: on when the adapter exists |
| `--wifi` / `--no-wifi` | `WIFI=0`, `WIFI_ADAPTER=auto` | the car's Wi-Fi (wpa_supplicant, connman, QtCarConnMan) on this PC's adapter: QtCar's Wi-Fi settings connect the PC; NetworkManager lets go of the adapter while it runs; `auto`: when there's an adapter (the stick) |
| `--cellular` / `--no-cellular` | `CELLULAR=0` | this PC's modem (ModemManager + NetworkManager connect it) as the car's LTE: while the PC is online over it, the cell icon, network type, bars and operator; `auto`: when there is a modem (the stick); see [features.md](features.md#cellular) |
| `--music DIR` / `--no-music` | `MUSIC=` | a folder as USB stick |
| `--camera DEV` / `--no-camera` | `CAMERA=` | backup camera source; `auto`: the first webcam (UVC, not an IR camera), decided at each start |
| | `CAMERA_DEV=/dev/video32`, `CAMERA_SIZE=1280x960` | the device QtCar reads, the picture size |
| `--nav` / `--no-nav` | `NAV=1` | offline navigation (when maps are installed) |
| `--services "A B"` | `SERVICES=` | more firmware services (`./tesla services`) |
| `--image-ram` / `--no-image-ram` | `IMAGE_RAM=auto` | the chroot's writes go to RAM (a device-mapper snapshot) and into the image at the stop; auto = when the image is on a USB drive (the stick). A power loss before the stop loses that run's changes, not the image |
| `--native` / `--vnc` | `SCREEN=vnc` | QtCar on this PC's own screen (see below) or on a virtual screen over VNC |
| `--touch-device DEV` | `TOUCH_DEVICE=auto`, `NATIVE_DISPLAY=:0` | native: the touchscreen and the X display |
| `--rotate DIR` | `ROTATE=normal` | native: rotate the output and touchscreen; `right`/`90`, `left`/`270`, `inverted`/`180`, or `normal` |
| `--cursor` / `--no-cursor` | `CURSOR=auto` | native: the mouse cursor (auto: hidden with a touchscreen, shown when the mouse is the finger) |
| `--gpu` / `--no-gpu` | `GPU=auto` | native: OpenGL on the GPU (Intel; AMD only with `--gpu`, untested) or in software |
| `--size WxH` | `SIZE=1920x1200` | the UI's size (the car's screen) |
| `--qtcar-args "..."` | `QTCAR_ARGS=` | more QtCar options |
| `--viewer CMD` | `VIEWER=` | VNC viewer; empty = the first of krdc, remote-viewer, remmina, vncviewer; `none` |
| `--remote` | `REMOTE=0` | VNC (with a password) and the panel reachable from the network |
| | `VNC_PORT=5900`, `PANEL_PORT=8099` | ports |
| `--no-restart` | `RESTART=1` | don't restart QtCar when it exits |
| | `IMAGE=./mcu2.ext4`, `CHROOT=./chroot` | the image and its mount point |
| `--verbose`, `-v` | | QtCar's output in the terminal too |
| `--strace "SVC ..."` | | run services under strace; traces end up in the run's log folder |
| `--dry-run`, `-n` | | only check and show what would start |
| `--force`, `-f` | | start although a check failed |

`./tesla config` prints the resulting values with their source. Unknown names in `tesla.conf`
are reported by the checks (typos).

## The screen

The UI runs on a virtual display (Xvfb `:1`) and is shown over VNC on `localhost:5900`.
`./tesla start` opens a viewer.
The VNC servers listen on IPv4; `127.0.0.1:5900` is the center display and, for Model S/X,
`127.0.0.1:5901` is the cluster (view only: it has no touch, and it shares the X server's pointer with the center display). With `--remote`, they listen on the machine's IPv4 interfaces.

- **Touch:** left click / drag = one finger, mouse wheel = pinch zoom, right click = long press.
- **Viewers:** KRDC, `remote-viewer` and Remmina scale the picture to the window; TigerVNC
  `vncviewer` can't. Scaling doesn't affect touch accuracy.
- **From another machine:** `ssh -L 5901:localhost:5900 <this-pc>`, then connect to
  `localhost:5901` (macOS: Screen Sharing, `open vnc://localhost:5901`). Or `--remote` with a
  password set once by `x11vnc -storepasswd`.

## On the PC's own screen (native)

`./tesla start --native`, run from the desktop session (or with `NATIVE_DISPLAY`/`XAUTHORITY`
pointing at it): QtCar opens fullscreen on the real screen, no Xvfb, VNC or viewer.

- **Scaling:** the UI is 1920x1200 like the car's screen. On a panel with another size the X
  screen is set to 1920x1200 and scaled to fit (`xrandr --transform`), with black bars; the
  original setting comes back when it stops.
- **Rotation:** `--rotate right` (`90`) or `--rotate left` (`270`) turns the panel portrait and
  remaps touch. This is especially useful for the Model S/X 1200x1920 UI on a landscape laptop:
  physically turn the laptop the same way. `--rotate inverted` (`180`) is also available. The
  original output orientation comes back when QtCar stops. For example:

  ```sh
  ./tesla --config tesla-s.conf start --native --rotate right
  ```
- **Touch:** the first touchscreen (a multitouch device marked as direct) is grabbed and passed
  through by `tesla-touch --from`, mapped to the letterboxed UI; real multitouch works (pinch).
  The desktop doesn't see those touches while it runs. Without a touchscreen, the mouse works as
  one finger, as over VNC.
- **GPU:** with an Intel GPU, OpenGL runs on it (`GPU=auto`; `--no-gpu` for software). The kit
  installs Mesa 18.0.5's `i965` driver (the firmware's own is Mesa 11.2 from 2016 and doesn't know
  newer Intel GPUs, but only up to about the 8th/9th generation: Mesa 18's i965 doesn't know the
  newer Xe GPUs); `/dev/dri` is bound into the chroot. AMD GPUs: `--gpu` (`GPU=on`) tries Mesa
  18's radeonsi (GCN up to Vega/Raven, not RDNA) or r600 (TeraScale: HD 2000-6000). r600 worked
  on a Radeon HD 6670; radeonsi is untested, so AMD isn't the default. The screen has to be on that GPU (the X server runs on it); with several
  GPUs the first Intel or AMD one is used. With an NVIDIA card as the X server's primary GPU
  (`xrandr --listproviders`: provider 0) QtCar can't render on the AMD one; make the AMD card
  primary, e.g. `/etc/X11/xorg.conf.d/20-amd-primary.conf` with
  `Section "OutputClass"` / `Identifier "amd"` / `MatchDriver "radeon"` (`"amdgpu"` for newer
  cards) / `Option "PrimaryGPU" "yes"` / `EndSection`, then log in again (delete the file to go
  back).
- **Several monitors:** QtCar takes one, the primary (or `--native-output NAME`, `NATIVE_OUTPUT`);
  the others are switched off while it runs. `./tesla stop` restores the whole layout. NVIDIA: software.
- The screensaver is off while it runs. Stop with `./tesla stop` from another terminal (or
  Ctrl+C in the start terminal, Alt+Tab to reach it).
- Tested on a ThinkPad T480 (1920x1080 touch panel) with a Debian 13 live system: the whole
  thing ran from RAM, `./tesla build-image` on the laptop, no install needed besides the tools.

## A bootable USB stick

`./tesla build-stick` builds a disk image for a USB stick (no sudo: rootless podman) that boots
a PC straight into `./tesla start --native`: Debian 13, no desktop, your image, tesla.conf and
music on a persistence partition. Ctrl+Alt+Esc returns to a console, Ctrl+Alt+Backspace restarts
QtCar, Ctrl+Alt+End powers off. `stick/qemu.sh` tries it in a VM. It contains the firmware: for
your own use only. Details: [stick/README.md](../stick/README.md).

## The web panel

With `--vehicle`: **http://localhost:8099/**. Tabs: **Car**, **Arcade**, **CAN signals**,
**Services and logs**; `#car`, `#arcade`, `#can`, `#run` after the address open one.

- **Car:**
  - **What QtCar sees:** live values (gear, speed, odometer, battery, doors, lock, charging,
    lights, telltales, tire pressures, ...), read from the vehicle service every 2 s.
  - **Presets:** parked, drive, stop, charging, charge-complete, unplugged, doors-open/-closed,
    locked/unlocked, lights-on/-off; the Model 3 also tires-ok, tire-low, winter, summer, rail-off;
    the Model S/X also charge-stopped.
  - **Controls:** gear, speed, battery, the driver in the car or gone (Model S/X: standby or the
    dog screen); doors, frunk, trunk, lock; charging state (Model S/X: also the station's
    current and voltage); lights; turn
    signals, hazards, parking brake, seat belt; outside temperature; tire pressures; any CAN signal
    by name. Each car has its own table (`CONTROLS` and `CONTROLS_SX` in `tesla-can.py`): the
    Model 3's go through Tesla's simulator, the Model S/X's set the ECUs' own CAN signals
    ([vehicle/README.md](../vehicle/README.md#the-model-sx-2026-what-drives-what-sandbox-2026-10-22)).
    The Model 3 also has the drive rail, charge current, time to full and the inside temperature.
- **Arcade**: the steering wheel, brake pedal and scroll wheels for the Arcade (Beach
  Buggy Racing 2 steers with the wheel; the MAME games use the scroll wheels). The Model S/X has
  only the scroll wheels here (its firmware has no Beach Buggy Racing 2). A slider that
  springs back, a hold-to-brake button, both scroll wheels (tilt left, press, tilt right, scroll).
  Keys after a click into the section (or with "keys anywhere"): ←/→ or A/D steer toward the
  "full lock" angle (30° = full lock in BBR2), ↓/S/Space brake. A gamepad works too: left stick,
  B or the left trigger.
- **CAN signals:** every message and signal of the firmware's CAN database (Model 3: 379 messages,
  8759 signals). Search by signal or message name, id (`0x118`), unit or enum label (several
  words: all must match); filters "on the wire" (ids seen since tesla-can.py started, with rate
  and sender: this panel, the simulator, another sender, or QtCar's own requests to the car),
  "set here" and "watched". Open a message to see its frames (per multiplexer branch) and every
  signal's decoded live value; set a value (number in the signal's unit or an enum label) or
  unset it; ☆ pins a signal to the top of the tab (remembered in the browser).
  `http://localhost:8099/#DI_gear` opens the tab with that search.
- **GPS marks:** with `--gps`, gear, speed, drive rail and odometer follow the GPS and carry a
  GPS badge. A change there holds until the GPS car starts or stops moving.
- **Services and logs:** every service and helper of the run with an up/down dot and its restart
  count, links to each log file and to the run summary.

## Stopping

Ctrl+C in the start terminal, or `./tesla stop` anywhere. Both stop QtCar, the services and the
helpers, and unmount what the start mounted (the image too, if the start mounted it). The summary
of the run follows. If a run was killed or its terminal closed, `./tesla stop` cleans up what it
left.

## The Model S firmware (experimental)

A second firmware, the Model S/X MCU2's 2026.8.3, runs with its own config file (see
[development.md](development.md#a-second-firmware) for the image): `./tesla --config tesla-s.conf
start` shows the portrait center display (1200x1920) on VNC port 5900 and the instrument cluster
(1920x720) on port 5901; `--native` shows the center display on this PC's screen, scaled to fit,
and the cluster on VNC port 5901. Touch works. `--vehicle` sends the Model S/X CAN messages
without the older simulator, and `--gps` gives GpsManager a valid simulated position. Browser,
web media, Theater and Chromium audio run with this profile; see their results and account
limits in [features.md](features.md). On the T480, `--camera /dev/video2` showed live reverse
video, and `--wifi` discovered networks; the user also reported that the Wi-Fi UI worked.
`--bluetooth --audio` paired a rooted phone and carried its music to the laptop speakers.
The Bluetooth recovery helper reconnects `btd` when the firmware's BSA server restarts;
long-term connection stability and calls remain unverified.
QtCar runs with `--emulated` (Tesla's desktop mode: the panels count as on, no retry loop for
their power and link checks), and the profile's `escalator-stub` stands in for the car's
escalator (videos, MAME). Offline navigation uses the same tiles as the Model 3 (remove an old
`NAV=0` from `tesla-s.conf`). What the S still lacks compared with the Model 3:
[notes/model-s-parity.md](../notes/model-s-parity.md).
