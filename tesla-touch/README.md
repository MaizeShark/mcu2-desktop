# tesla-touch

Mouse → multitouch bridge for QtCar running on a virtual X display, and with `--from` the
passthrough of a real touchscreen (`./tesla start --native`).

It creates a uinput touchscreen named `cyttsp6_mt` (like the car's controller) and feeds it
from the pointer events of the X display QtCar runs on (Xvfb `:1`). It never touches the host
desktop, so the viewer can be anything that speaks VNC, on any OS.

| Mouse | Touch |
|---|---|
| left button / drag | one finger |
| wheel | two-finger pinch zoom around the pointer (maps) |
| right click | long press (800 ms, `--long-press MS`) |

QtCar hides the mouse cursor. tesla-touch puts a real arrow cursor back on its windows, so the
VNC client draws a normal, lag-free cursor (`--no-cursor` turns this off).

## Build / install

```sh
cargo build --release                       # -> target/release/tesla-touch
sudo cp 99-tesla-touch.rules /etc/udev/rules.d/ && sudo udevadm control --reload
```

The udev rule matters. Without it the host's libinput (Wayland or Xorg) also adopts the virtual
touchscreen, so touches meant for QtCar land on your desktop too. tesla-touch warns if the rule
is missing. The same file hides QtCar's own `tesla-uinput` device (the Model 3 browser's touches;
left to the X server with `--native`), the arcade's `game-*` devices and the Model S/X Chromium
input device `game-chromium-touch`.

## Usage

`./tesla start` runs it like this:

```sh
sudo tesla-touch --display :1 --bind <chroot>/dev/input/touch
```

`--bind` mounts just the event node into the chroot and removes it again on exit. The host's
`/dev/input` no longer needs to be bind-mounted. `tesla-touch -h` lists all options. Without
`--bind` it runs fine as a normal user in the `input` group, which is handy for testing.
The pointer is watched with the X RECORD extension (it sees x11vnc's XTEST events; Xvfb runs
with `+extension RECORD`). `--size` is the touch range (default: the X screen; the Model S/X
center display is 1200x1920).

**Passthrough** (`--native`): `--from /dev/input/eventN` grabs a real touchscreen (the desktop
no longer sees its touches) and re-emits its fingers on the virtual device, real multitouch
included. `--panel WxH` is the panel's pixel size: the UI is shown scaled to fit with black bars,
and touches are mapped into it. `--rotate normal|left|right|inverted` follows a rotated output
(`./tesla start --rotate`). Without a touchscreen `./tesla start --native` uses the mouse mode
on the desktop's X display.

## Viewers

- **Linux (X11 or Wayland):** `./tesla start` starts the first viewer it finds: KRDC,
  `remote-viewer` (virt-viewer package), Remmina, then TigerVNC `vncviewer`. The first three
  scale the picture to the window; TigerVNC can't. Or `--viewer CMD` (`VIEWER=` in tesla.conf). Scaling doesn't affect touch accuracy, because positions are
  taken on the server side.
- **macOS:** Screen Sharing is built in and scales to the window. The VNC server only listens on localhost by default, so
  tunnel it: `ssh -L 5901:localhost:5900 <linux-box>`, then `open vnc://localhost:5901`. Port
  5901 locally, because macOS won't connect to its own port 5900. Or start with `--remote` and
  set a password first with `x11vnc -storepasswd`.
- **Windows:** any VNC client, same tunnel or `--remote`.

QtCar and the chroot itself need x86-64 Linux: a Linux PC, or a Linux VM on a Mac.
