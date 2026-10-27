# Setup

Everything here is needed once. Afterwards `./tesla check` shows whether all is in place.

## What you need

- **An x86-64 PC with Linux.** Developed on KDE neon (Ubuntu 24.04 base) and on Fedora 44 (KDE
  Plasma, Niri); tested on Debian 13 (the T480 laptop and the USB stick). PipeWire; VNC mode works
  under any desktop, `--native` needs an X11 session or Xwayland. On a Mac: a Linux VM (on Apple
  Silicon with x86 emulation, slow).
- **About 10 GB of disk space**: the firmware dump (~2 GB), the image (6 GB), maps (a few GB
  per region while building).
- **sudo**: the chroot, mounts and kernel modules need root. `./tesla` asks for the password
  once per start.
- **Your own dump of an MCU2 (Intel Atom) firmware**: the Model 3's 2019.20.4.2 or the Model
  S/X's 2026.8.3 (x86_64), or both (one image each). This repository contains no firmware and no
  files from it, and nothing here downloads it. Only these versions were ever tested: the patch kit
  checks every file it patches and refuses unknown versions.

## 1. Host packages

Debian / Ubuntu:

```sh
sudo apt install xvfb x11vnc x11-utils curl python3 e2fsprogs cargo   # always
sudo apt install krdc                        # a VNC viewer (or virt-viewer, remmina, tigervnc-viewer)
sudo apt install alsa-utils sox pipewire-bin # --audio
sudo apt install ffmpeg v4l2loopback-dkms    # --camera
sudo apt install x11-xserver-utils xauth     # --native
sudo apt install podman                      # map tiles, the USB stick
pip install --user unicorn pyelftools        # --vehicle (extracts the CAN database once)
```

Fedora:

```sh
sudo dnf install xorg-x11-server-Xvfb x11vnc xdpyinfo curl python3 e2fsprogs cargo   # always
sudo dnf install krdc                        # a VNC viewer (or virt-viewer, remmina, tigervnc)
sudo dnf install alsa-utils sox pipewire-utils kernel-modules-extra   # --audio (snd-aloop)
sudo dnf install ffmpeg-free akmod-v4l2loopback   # --camera (v4l2loopback: RPM Fusion)
sudo dnf install xrandr xorg-x11-xauth       # --native
sudo dnf install podman                      # map tiles, the USB stick
pip install --user unicorn pyelftools        # --vehicle (extracts the CAN database once)
```

Other distributions: the same programs under their names. `./tesla check` names what's missing
and, on Debian/Ubuntu and Fedora, the package to install. Python 3.11 or newer is needed.

## 2. Touch

`tesla-touch` turns the VNC mouse into a multitouch screen for QtCar (see
[`tesla-touch/README.md`](../tesla-touch/README.md)):

```sh
(cd tesla-touch && cargo build --release)
# keep the host desktop from using the virtual touchscreen too
sudo cp tesla-touch/99-tesla-touch.rules /etc/udev/rules.d/ && sudo udevadm control --reload
```

## 3. The image

Put your firmware dump (the root filesystem, `usr/tesla/UI/bin/QtCar` inside) at
`mcu2-original/` in the repository folder, owned by root as extracted. Never modify it; the
image is a patched copy. (Before 2026-09-25 these were `mcu3-original/` and `mcu3-new.ext4`;
both are still found. To switch to the new names: `./tesla stop`, then
`mv mcu3-original mcu2-original` and, with the image unmounted, `mv mcu3-new.ext4 mcu2.ext4`.)

```sh
./tesla build-image --fresh
```

This creates `mcu2.ext4` (6 GB, `--size`): it copies the dump, applies the patch kit
([`mcu2-patchkit/`](../mcu2-patchkit/README.md): binary patches, a software OpenGL renderer,
config, service scripts) and installs maps if some are built. The kit downloads nothing from Tesla.
The open-source libraries it adds (Mesa, LLVM, ...) are downloaded once (for the Model 3 firmware
from the Ubuntu 16.04 archive, `mcu2-patchkit/profiles/*/sources.toml`, checked by hash) and
cached in `mcu2-patchkit/payload/`; its shims are compiled from the profile's `src/` (needs gcc
and internet access the first time). The kit picks its profile from the firmware's version.

After a `git pull`, bring the image up to date with `./tesla build-image` (only what changed is
applied; `--check` shows what would change). The image remembers which kit version patched it
(`/etc/mcu2-patchkit`), and `./tesla check` warns when it's outdated.

The image is mounted at `chroot/` while it runs (`IMAGE` and `CHROOT` in `tesla.conf`).

**The Model S/X (2026.8.3)** has its own dump, image and config file (one image runs at a time,
both at `chroot/`). The firmware comes as a squashfs partition image (`*.mcu2`):

```sh
sudo unsquashfs -d mcu2s-original path/to/2026.8.3.mcu2      # the dump, root-owned
cat > tesla-s.conf <<EOF
IMAGE=./mcu2s.ext4
DUMP=./mcu2s-original
SANDBOX_ROOT=./sandbox/rootfs-s
EOF
./tesla --config tesla-s.conf build-image --fresh          # (--maps US: maps, section 4)
./tesla --config tesla-s.conf start --vehicle --audio --gps 37.4419,-122.1430
```

Every `./tesla` command takes `--config tesla-s.conf` for it. The viewer shows the center display
(1200x1920); the instrument cluster (1920x720) is on the next VNC port (5901, `./tesla start`
prints it).

## 4. Maps (optional)

Offline routing needs map tiles, built from an OpenStreetMap extract with Valhalla 2.4.3 in
podman (the build image takes ~30 min once, a region like Northern California a few minutes):

```sh
podman build -t valhalla:2.4.3 -f navigation/Containerfile navigation
navigation/build-tiles.sh ~/Downloads/norcal-latest.osm.pbf US     # region: US, EU, ...
./tesla build-image                                                # installs them
```

Details: [`navigation/README.md`](../navigation/README.md). Without maps the map still shows
(online tiles), but routing doesn't work.

## 5. Kernel modules

- `uinput` (touch): usually loaded; else `sudo modprobe uinput`.
- `snd-aloop` (`--audio`): `./tesla start` loads it as card `model3`. If it's already loaded
  without that name: `sudo modprobe -r snd-aloop`.
- `v4l2loopback` (`--camera`): two devices, one for your camera source and one for QtCar:
  `sudo modprobe v4l2loopback devices=2 video_nr=10,32 exclusive_caps=1,1`
  (permanently: `/etc/modprobe.d/` and `/etc/modules-load.d/`).

## 6. Sound cards

For `--audio`, keep PulseAudio/PipeWire from adopting the car's loopback sound cards:

```sh
sudo cp tools/99-tesla-sound.rules /etc/udev/rules.d/ && sudo udevadm control --reload
```

## 7. Check

```sh
./tesla check
```

Every line is `ok`, `--` (not needed for what's enabled), `WARN` or `FAIL`, with a hint how to
fix it. Then: [usage.md](usage.md).
