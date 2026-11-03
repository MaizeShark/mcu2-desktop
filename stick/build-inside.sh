#!/bin/bash
# Builds the stick's disk image inside the builder container (started by tools/build-stick.sh).
#   /repo  the repository (read-only)        /in/mcu2.ext4  the patched image (read-only)
#   /in/music  music for ~/Music (optional)  /work  build folder: stick.env (settings), files.txt
#   (repo files for the stick), tesla.conf, cache/ (debs, base rootfs), the output image
# The rootfs is built in the container's own file system, so no files with foreign owners end up
# in /work. Steps: base rootfs (mmdebstrap, cached per package list) -> configuration ->
# squashfs -> persistence partition (repo, image, music) -> GPT disk image with an EFI partition.
set -euo pipefail
. /work/stick.env
export DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8
W=/work
C=$W/cache
ROOT=/build/rootfs
ESP=/build/esp
PERSIST=/build/persist
mkdir -p "$C/debs" /build

step() { printf '\033[1;36m==>\033[0;1m %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
mib()  { echo $(( ($1 + 1048575) / 1048576 )); }     # bytes -> MiB, rounded up

# --- base rootfs --------------------------------------------------------------------------------
packages=$(sed 's/#.*//' /repo/stick/packages.txt | tr -s ' \n' '\n' | grep . | sort -u)
[ "$SSH" = 1 ] && packages+=$'\nopenssh-server\navahi-daemon\nlibnss-mdns'
key=$( { echo "$SUITE"; echo "$packages"; } | sha1sum | cut -c1-12)
base=$C/base-$key.tar
if [ -f "$base" ] && [ "$REBUILD" != 1 ]; then
    step "Base rootfs: cached ($(basename "$base"))"
else
    step "Base rootfs: Debian $SUITE, $(wc -l <<<"$packages") packages (mmdebstrap)"
    command rm -f "$C"/base-*.tar
    mmdebstrap --mode=root --variant=minbase --components="main contrib non-free non-free-firmware" \
        --aptopt='APT::Install-Recommends "false"' \
        --include="$(paste -sd, <<<"$packages")" \
        --setup-hook='mkdir -p "$1/var/cache/apt/archives"' \
        --setup-hook="sync-in $C/debs /var/cache/apt/archives" \
        --customize-hook="sync-out /var/cache/apt/archives $C/debs" \
        --customize-hook='chroot "$1" sh -c "for k in /lib/modules/*; do dkms autoinstall -k \${k##*/}; done"' \
        --customize-hook='ls "$1"/lib/modules/*/updates/dkms/v4l2loopback.ko* >/dev/null' \
        "$SUITE" "$base.tmp.tar" >"$C/mmdebstrap.log" 2>&1 ||
        { tail -30 "$C/mmdebstrap.log"; echo "mmdebstrap failed (cache/mmdebstrap.log)" >&2; exit 1; }
    mv "$base.tmp.tar" "$base"
    command rm -f "$C"/debs/partial -r
fi

# --- configuration ------------------------------------------------------------------------------
step "Configuration (keyboard $KEYMAP, time zone $TIMEZONE, host name $HOSTNAME)"
command rm -rf "$ROOT"; mkdir -p "$ROOT"
# device nodes: mknod isn't allowed in a rootless container; the squashfs gets them as pseudo files
tar -xf "$base" -C "$ROOT" --exclude="./dev/*"
mount -t proc proc "$ROOT/proc"; mount -t sysfs sys "$ROOT/sys"; mount --rbind /dev "$ROOT/dev"
trap 'umount -R "$ROOT/proc" "$ROOT/sys" "$ROOT/dev" 2>/dev/null || true' EXIT
cp -a /repo/stick/overlay/. "$ROOT/"
chown -R 0:0 "$ROOT/etc/sudoers.d" "$ROOT/usr/local" "$ROOT/etc/tesla-stick"
chmod 440 "$ROOT/etc/sudoers.d/mcu"
# NetworkManager reads profiles only when they are root's and private
chown -R 0:0 "$ROOT/usr/lib/NetworkManager/system-connections"
chmod 600 "$ROOT"/usr/lib/NetworkManager/system-connections/*.nmconnection
cp /repo/tesla-touch/99-tesla-touch.rules /repo/tools/99-tesla-sound.rules "$ROOT/etc/udev/rules.d/"

echo "$HOSTNAME" >"$ROOT/etc/hostname"
printf '127.0.0.1\tlocalhost\n127.0.1.1\t%s\n::1\t\tlocalhost ip6-localhost ip6-loopback\n' "$HOSTNAME" >"$ROOT/etc/hosts"
ln -sf "/usr/share/zoneinfo/$TIMEZONE" "$ROOT/etc/localtime"
echo "$TIMEZONE" >"$ROOT/etc/timezone"
echo 'LANG=C.UTF-8' >"$ROOT/etc/default/locale"
cat >"$ROOT/etc/default/keyboard" <<EOF
XKBMODEL="pc105"
XKBLAYOUT="$KEYMAP"
XKBVARIANT=""
XKBOPTIONS=""
BACKSPACE="guess"
EOF
mkdir -p "$ROOT/etc/X11/xorg.conf.d"
cat >"$ROOT/etc/X11/xorg.conf.d/00-keyboard.conf" <<EOF
Section "InputClass"
    Identifier "keyboard"
    MatchIsKeyboard "on"
    Option "XkbLayout" "$KEYMAP"
EndSection
EOF
# the console keymap from /etc/default/keyboard (setupcon at boot needs its cache)
chroot "$ROOT" setupcon --save-only 2>/dev/null || true

groups=$(for g in sudo video audio input render plugdev bluetooth netdev adm; do
    grep -q "^$g:" "$ROOT/etc/group" && printf '%s,' "$g"; done)
chroot "$ROOT" useradd -m -u 1000 -s /bin/bash -G "${groups%,}" mcu
chroot "$ROOT" passwd -l mcu >/dev/null
chroot "$ROOT" passwd -l root >/dev/null
chroot "$ROOT" systemctl enable tesla-grow.service >/dev/null 2>&1
if [ "$SSH" = 1 ]; then
    mkdir -p "$ROOT/etc/ssh/sshd_config.d"
    printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' >"$ROOT/etc/ssh/sshd_config.d/tesla-stick.conf"
fi
# a live system: a fresh machine id each boot, no journal on the stick
: >"$ROOT/etc/machine-id"
command rm -f "$ROOT/var/lib/dbus/machine-id"
chroot "$ROOT" update-initramfs -u -k all 2>&1 | grep -v '^update-initramfs: Generating' || true
cat >"$ROOT/etc/tesla-stick/build" <<EOF
built $(date '+%F %T') by ./tesla build-stick
kit: $(sed -n "s/^kit=//p" /work/kit-stamp)
keyboard=$KEYMAP timezone=$TIMEZONE hostname=$HOSTNAME ssh=$SSH secure_boot=$SECURE_BOOT
EOF
umount -R "$ROOT/proc" "$ROOT/sys" "$ROOT/dev"; trap - EXIT

# --- EFI partition: boot loader, kernel, squashfs ------------------------------------------------
step "Squashfs"
command rm -rf "$ESP"; mkdir -p "$ESP/live" "$ESP/boot/grub" "$ESP/EFI/BOOT"
kernel=$(ls "$ROOT"/boot/vmlinuz-* | sort -V | tail -1)
cp "$kernel" "$ESP/live/vmlinuz"
cp "$ROOT/boot/initrd.img-${kernel##*/vmlinuz-}" "$ESP/live/initrd.img"
printf '%s\n' 'boot/initrd.img-*' 'var/cache/apt/archives/*.deb' >/build/exclude
mksquashfs "$ROOT" "$ESP/live/filesystem.squashfs" -comp zstd -Xcompression-level 15 -noappend \
    -quiet -wildcards -ef /build/exclude \
    -p 'dev/console c 600 0 0 5 1' -p 'dev/null c 666 0 0 1 3' -p 'dev/tty c 666 0 0 5 0'
info "$(du -h "$ESP/live/filesystem.squashfs" | cut -f1) squashfs, kernel ${kernel##*/vmlinuz-}"

step "Boot loader ($( [ "$SECURE_BOOT" = 1 ] && echo "Secure Boot: shim + Debian's signed GRUB" || echo "GRUB, Secure Boot off"))"
opts="boot=live persistence persistence-label=persistence quiet loglevel=3"
cat >"$ESP/boot/grub/grub.cfg" <<EOF
# The stick's boot menu (./tesla build-stick). tesla.console: no autostart; tesla.gpu=off:
# software rendering.
set timeout=3
set default=0
insmod all_video
menuentry "Tesla MCU2" {
    linux /live/vmlinuz $opts
    initrd /live/initrd.img
}
menuentry "Tesla MCU2: software rendering (no GPU)" {
    linux /live/vmlinuz $opts tesla.gpu=off
    initrd /live/initrd.img
}
menuentry "Tesla MCU2: console only (start with tesla-start)" {
    linux /live/vmlinuz $opts tesla.console
    initrd /live/initrd.img
}
menuentry "UEFI firmware settings" {
    fwsetup
}
EOF
# finds this partition and reads the menu above
cat >/build/find.cfg <<'EOF'
# the device GRUB was loaded from first (the label isn't found on every firmware), then the label,
# then any partition with the menu on it
set found=0
if [ -n "$cmdpath" ]; then
    regexp --set=1:dev '^(\([^)]*\))' "$cmdpath"
    if [ -n "$dev" -a -f "$dev/boot/grub/grub.cfg" ]; then
        set root="$dev"; set found=1
    fi
fi
if [ "$found" = 0 ]; then
    search --no-floppy --set=root --label TESLABOOT
    if [ -f ($root)/boot/grub/grub.cfg ]; then set found=1; fi
fi
if [ "$found" = 0 ]; then
    search --no-floppy --set=root --file /boot/grub/grub.cfg
fi
set prefix=($root)/boot/grub
configfile ($root)/boot/grub/grub.cfg
EOF
if [ "$SECURE_BOOT" = 1 ]; then
    # shim (signed by Microsoft) -> Debian's signed GRUB (reads EFI/debian/grub.cfg) -> the kernel
    # (signed by Debian). Kernel modules must be signed too: dkms' v4l2loopback (camera) isn't.
    cp /usr/lib/shim/shimx64.efi.signed "$ESP/EFI/BOOT/BOOTX64.EFI"
    cp /usr/lib/shim/mmx64.efi.signed "$ESP/EFI/BOOT/mmx64.efi"
    cp /usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed "$ESP/EFI/BOOT/grubx64.efi"
    mkdir -p "$ESP/EFI/debian"
    cp /build/find.cfg "$ESP/EFI/debian/grub.cfg"
    cp /build/find.cfg "$ESP/EFI/BOOT/grub.cfg"
else
    grub-mkstandalone -O x86_64-efi -o "$ESP/EFI/BOOT/BOOTX64.EFI" \
        --modules="part_gpt part_msdos fat ext2 search search_label search_fs_file regexp configfile normal linux echo test all_video efi_gop efi_uga gfxterm" \
        --locales="" --fonts="" --themes="" "boot/grub/grub.cfg=/build/find.cfg"
fi
cat >"$ESP/TESLA-STICK.txt" <<EOF
Tesla MCU2 stick, built by ./tesla build-stick ($(date +%F)).
It contains Tesla firmware from its owner's dump: for its owner's own use only, never share it.
The second partition (label "persistence") holds ~/mcu2-desktop (image, tesla.conf, logs),
~/Music, Wi-Fi settings and Bluetooth pairings.
EOF

# --- persistence partition: /home (repo, image, music), Wi-Fi, BlueZ -----------------------------
step "Persistence partition"
command rm -rf "$PERSIST"
mkdir -p "$PERSIST/home/mcu/mcu2-desktop" "$PERSIST/home/mcu/Music" "$PERSIST/var/lib/bluetooth" \
    "$PERSIST/etc/NetworkManager/system-connections"
cat >"$PERSIST/persistence.conf" <<'EOF'
/home bind
/var/lib/bluetooth bind
/etc/NetworkManager/system-connections bind
EOF
chmod 700 "$PERSIST/var/lib/bluetooth" "$PERSIST/etc/NetworkManager/system-connections"
repo=$PERSIST/home/mcu/mcu2-desktop
(cd /repo && tar -cf - -T /work/files.txt) | tar -xf - -C "$repo"
cp /work/tesla.conf "$repo/tesla.conf"
cp -a "$ROOT/etc/skel/." "$PERSIST/home/mcu/"
[ -d /in/music ] && cp -r /in/music/. "$PERSIST/home/mcu/Music/"
if [ -n "$SSH_KEY" ]; then
    mkdir -p "$PERSIST/home/mcu/.ssh"
    printf '%s\n' "$SSH_KEY" >"$PERSIST/home/mcu/.ssh/authorized_keys"
    chmod 700 "$PERSIST/home/mcu/.ssh"; chmod 600 "$PERSIST/home/mcu/.ssh/authorized_keys"
fi
chown -R 1000:1000 "$PERSIST/home/mcu"
# the image: bind-mounted, not copied (6 GB); its owner is set in the file system afterwards
touch "$repo/mcu2.ext4"
mount --bind /in/mcu2.ext4 "$repo/mcu2.ext4"
trap 'umount "$repo/mcu2.ext4" 2>/dev/null || true' EXIT

img_bytes=$(stat -c %s /in/mcu2.ext4)
rest_bytes=$(du -sb --exclude=mcu2.ext4 "$PERSIST" | cut -f1)
p2_mib=$(( $(mib $((img_bytes + rest_bytes))) * 105 / 100 + EXTRA_MIB ))
mke2fs -q -F -t ext4 -L persistence -m 0 -d "$PERSIST" /build/p2.img "${p2_mib}M"
umount "$repo/mcu2.ext4"; trap - EXIT
debugfs -w -R "set_inode_field /home/mcu/mcu2-desktop/mcu2.ext4 uid 1000" /build/p2.img >/dev/null 2>&1
debugfs -w -R "set_inode_field /home/mcu/mcu2-desktop/mcu2.ext4 gid 1000" /build/p2.img >/dev/null 2>&1
e2fsck -fn /build/p2.img >/dev/null || { echo "e2fsck found errors in the persistence partition" >&2; exit 1; }
info "${p2_mib} MiB (image $(mib "$img_bytes") MiB, $EXTRA_MIB MiB free; grows to the stick's size at the first boot)"

# --- disk image ---------------------------------------------------------------------------------
step "Disk image"
esp_mib=$(( $(mib "$(du -sb "$ESP" | cut -f1)") * 130 / 100 + 64 ))
[ "$esp_mib" -ge 512 ] || esp_mib=512
disk=$W/$OUT_NAME
command rm -f "$disk.tmp"
truncate -s $(( (1 + esp_mib + p2_mib + 1) * 1048576 )) "$disk.tmp"
sfdisk -q "$disk.tmp" <<EOF
label: gpt
start=1MiB, size=${esp_mib}MiB, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name="TESLA-BOOT"
size=${p2_mib}MiB, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="persistence"
EOF
truncate -s "${esp_mib}M" /build/esp.img
mkfs.fat -F 32 -n TESLABOOT /build/esp.img >/dev/null
mcopy -s -i /build/esp.img "$ESP"/* ::/
dd if=/build/esp.img of="$disk.tmp" bs=1M seek=1 conv=notrunc,sparse status=none
dd if=/build/p2.img of="$disk.tmp" bs=1M seek=$((1 + esp_mib)) conv=notrunc,sparse status=none
command rm -f /build/esp.img /build/p2.img
mv "$disk.tmp" "$disk"
info "$OUT_NAME: $(( (1 + esp_mib + p2_mib + 1) )) MiB (EFI ${esp_mib} MiB + persistence ${p2_mib} MiB)"
