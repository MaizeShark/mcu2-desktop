#!/bin/bash
# Boots a stick image (./tesla build-stick) in qemu: UEFI (OVMF), KVM, the image as a USB stick.
# The image itself isn't changed: writes go to a qcow2 overlay in stick/work/, kept between runs
# (--fresh starts over). QtCar renders in software (no Intel GPU in qemu); the mouse is the finger.
set -eu
usage() {
    cat <<EOF
stick/qemu.sh [options] [IMAGE]    (default: stick/work/mcu2-stick.img)
  --secure-boot    OVMF with Secure Boot on and Microsoft's keys (as on a PC)
  --size SIZE      the virtual stick's size (default 16G; bigger than the image, so the first
                   boot grows the persistence partition)
  --fresh          new overlay: like a freshly written stick
  --headless       no window; the screen on VNC :5 (localhost:5905), monitor on stick/work/qemu.mon
                   (screenshots: echo "screendump x.ppm" | socat - UNIX:stick/work/qemu.mon)
  --res WxH        the screen (default 1920x1080)
ssh (with build-stick --ssh): ssh -p 2222 mcu@localhost; the web panel: http://localhost:18099/
EOF
}
cd "$(dirname "$(realpath "$0")")"
work=$PWD/work
img=$work/mcu2-stick.img sb=0 size=16G fresh=0 headless=0 res=1920x1080
while [ $# -gt 0 ]; do
    case "$1" in
        --secure-boot) sb=1 ;;
        --size) size=$2; shift ;;
        --fresh) fresh=1 ;;
        --headless) headless=1 ;;
        --res) res=$2; shift ;;
        -h|--help) usage; exit 0 ;;
        -*) usage >&2; exit 1 ;;
        *) img=$(realpath "$1") ;;
    esac
    shift
done
[ -f "$img" ] || { echo "no image $img (./tesla build-stick)" >&2; exit 1; }
overlay=$work/qemu-overlay.qcow2
# a rebuilt image makes the old overlay useless
if [ "$fresh" = 1 ] || [ ! -f "$overlay" ] || [ "$img" -nt "$overlay" ]; then
    command rm -f "$overlay"
    qemu-img create -q -f qcow2 -b "$img" -F raw "$overlay" "$size"
fi
# OVMF: Debian/Ubuntu (ovmf) or Fedora (edk2-ovmf, whose .secboot vars have Microsoft's keys)
ovmf() {  # ovmf DEBIAN-NAME FEDORA-NAME
    if [ -f "/usr/share/OVMF/$1" ]; then echo "/usr/share/OVMF/$1"; else echo "/usr/share/edk2/ovmf/$2"; fi
}
if [ "$sb" = 1 ]; then
    code=$(ovmf OVMF_CODE_4M.secboot.fd OVMF_CODE.secboot.fd) vars_src=$(ovmf OVMF_VARS_4M.ms.fd OVMF_VARS.secboot.fd)
    machine=(-machine q35,smm=on -global driver=cfi.pflash01,property=secure,value=on)
else
    code=$(ovmf OVMF_CODE_4M.fd OVMF_CODE.fd) vars_src=$(ovmf OVMF_VARS_4M.fd OVMF_VARS.fd)
    machine=(-machine q35)
fi
[ -f "$code" ] || { echo "no OVMF firmware ($code): install ovmf (Debian) or edk2-ovmf (Fedora)" >&2; exit 1; }
vars=$work/qemu-vars-$sb.fd
[ -f "$vars" ] && [ "$fresh" = 0 ] || cp "$vars_src" "$vars"
display=(-display gtk)
[ "$headless" = 1 ] && display=(-display none -vnc 127.0.0.1:5 -monitor "unix:$work/qemu.mon,server,nowait")
exec qemu-system-x86_64 "${machine[@]}" -enable-kvm -cpu host -smp 4 -m 4G \
    -drive if=pflash,format=raw,readonly=on,file="$code" -drive if=pflash,format=raw,file="$vars" \
    -device qemu-xhci -drive if=none,id=stick,file="$overlay",format=qcow2 \
    -device usb-storage,drive=stick,removable=on,bootindex=0 \
    -device virtio-vga,xres="${res%x*}",yres="${res#*x}" -device usb-tablet -device usb-kbd \
    -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:2222-:22,hostfwd=tcp:127.0.0.1:18099-:8099 \
    -audiodev none,id=snd -device intel-hda -device hda-duplex,audiodev=snd \
    "${display[@]}"
