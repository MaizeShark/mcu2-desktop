# stick: a bootable USB stick

`./tesla build-stick` builds a disk image for a USB stick that boots a PC straight into QtCar on
its own screen (`./tesla start --native`): Debian 13, no desktop, no window manager (as on the
car). **It contains your image and so Tesla's firmware: for your own use only. Never share the
image file or the stick.** Its build output (`stick/work/`) is never committed.

```sh
./tesla build-stick --ssh                   # -> stick/work/mcu2-stick.img (no sudo, podman)
stick/qemu.sh                               # try it in a VM first
sudo dd if=stick/work/mcu2-stick.img of=/dev/sdX bs=4M conv=fsync status=progress
```

`./tesla build-stick --help` lists the options: `--conf`, `--music`, `--keymap`, `--timezone`,
`--hostname`, `--ssh`/`--ssh-key`, `--secure-boot`, `--free`, `--rebuild`, `--out`.

## On the stick
- The PC boots from the stick (UEFI; Secure Boot off, or build with `--secure-boot`). The boot
  menu (3 s) has: normal, software rendering (`tesla.gpu=off`), console only (`tesla.console`),
  UEFI settings.
- tty1 logs in as `mcu` and starts X with `tesla-session`, which runs `./tesla start --native`
  in `~/mcu2-desktop` (started again if it ends by itself; after 3 quick failures it gives up).
- Keys: **Ctrl+Alt+Esc** stops QtCar and returns to the console, **Ctrl+Alt+Backspace** restarts
  it, **Ctrl+Alt+End** powers off. The power button powers off too (./tesla cleans up).
- The console (`tesla-help`): `tesla-start` starts QtCar again, `sudo nmtui` sets up Wi-Fi,
  `./tesla status|logs|check --native` work as on a PC.
- `tesla.conf` on the stick: the build's settings (default: your tesla.conf's features and car)
  plus `CAMERA=auto` (the first webcam), `BLUETOOTH=auto` and `WIFI=auto` (on when the adapter
  exists), resolved at each start. With Wi-Fi the car's own stack has the adapter while QtCar
  runs: networks are joined in QtCar's Wi-Fi settings (saved in the image); on the console
  (Ctrl+Alt+Esc) NetworkManager has it again (`sudo nmtui`).
- `--ssh`: an ssh server (key login only, as `mcu`) and mDNS (`ssh mcu@tesla-mcu2.local`).

## Layout
| Partition | Contents |
|---|---|
| 1: EFI (FAT, label `TESLABOOT`) | GRUB (`EFI/BOOT/BOOTX64.EFI`; with `--secure-boot` shim + Debian's signed GRUB), `boot/grub/grub.cfg`, `live/` (kernel, initrd, `filesystem.squashfs`) |
| 2: ext4, label `persistence` | live-boot persistence (`persistence.conf`): `/home` (`~/mcu2-desktop` with the image, tesla.conf and logs; `~/Music`), `/var/lib/bluetooth`, NetworkManager's connections |

Everything else runs from the squashfs with a RAM overlay (changes outside those folders are gone
after a reboot). QtCar's own settings, paired phones (BSA's) etc. live in the image, which sits
on the persistence partition, so they stay. The image is only as large as its contents plus
`--free`; `tesla-grow.service` grows partition 2 to the end of the stick at the first boot.

## How it's built
`tools/build-stick.sh` (host) checks things and starts a rootless podman container
(`localhost/mcu2-stick-builder`, Debian 13 with mmdebstrap, squashfs-tools, mtools, GRUB) that
runs `stick/build-inside.sh`:
1. base system: mmdebstrap with `packages.txt` (no recommends), v4l2loopback built by dkms.
   Cached as `stick/work/cache/base-<hash>.tar` per package list; debs in `cache/debs/`.
2. configuration: `overlay/` (session scripts, keys, autologin, grow service), user `mcu`
   (uid 1000, sudo without password: ./tesla needs it), keyboard, time zone, host name, the
   repo's udev rules, initramfs with live-boot.
3. squashfs (zstd), GRUB (`grub-mkstandalone`, finds the menu by the partition label).
4. the persistence partition with `mke2fs -d`: the files git tracks plus tesla-touch's binary
   and the CAN database, the generated tesla.conf, the image (bind-mounted, not copied), music.
5. the GPT disk image from both partitions.

Nothing needs root on the host; the container's root is your user.

## Slow sticks
Cheap sticks (USB 2, often even when sold as USB 3) are very slow at the small synchronous
writes the firmware services make (settings databases, the file system journal): 17 KB/s on a
T480, and every read waited behind them, so QtCar didn't load in minutes (black screen). Since
the image lives on a USB drive, `./tesla start` runs the chroot on a device-mapper snapshot with
its writes in RAM (`IMAGE_RAM=auto`) and writes the changes into the image when it stops
(`tools/cow-apply.py`, one flush; dm's own merge took 4.5 min for 3.7 MB). QtCar was then ready
in 13 s from the same stick. Use Ctrl+Alt+End (or the power button) to power off: a hard power
off loses that run's changes (the image stays intact).

Check a new stick for fake capacity before using it (it overwrites the stick):
`sudo f3probe --destructive --time-ops /dev/sdX` (package `f3`). A fake stick corrupts the image
once the persistence partition grows past its real size.

## Limits
- UEFI only (no legacy BIOS boot).
- With Secure Boot on, the kernel refuses unsigned modules: dkms' v4l2loopback (the backup
  camera) doesn't load.
- In qemu there is no Intel GPU: QtCar renders in software. Xorg runs as root on the stick
  (xserver-xorg-legacy): software rendering hands frames over as MIT-SHM segments of QtCar's user,
  which only a root X server may attach (the Model S/X image showed a black screen before). The
  Model S/X image (`./tesla --config tesla-s.conf build-stick`) runs in qemu (2026-10-22), not yet
  tested on hardware. `stick/qemu.sh` finds OVMF on Debian/Ubuntu (ovmf) and Fedora (edk2-ovmf).
