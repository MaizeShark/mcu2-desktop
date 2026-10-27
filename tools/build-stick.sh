# ./tesla build-stick (sourced by ./tesla): a bootable USB stick image that starts QtCar.

STICK_WORK=$TESLA_DIR/stick/work
STICK_BUILDER=localhost/mcu2-stick-builder:1

build_stick_usage() {
    cat <<EOF
${B}./tesla build-stick$R0 [options]

Builds a disk image for a USB stick (UEFI): Debian 13, boots straight into QtCar on the PC's own
screen (./tesla start --native). It contains your image ($(basename "$IMAGE")) and so the firmware: for
your own use only, never share it. No sudo: built in a rootless podman container.
Output: stick/work/mcu2-stick.img, written to a stick with dd (printed at the end).

  --conf FILE        the stick's tesla.conf as it is (default: this tesla.conf's features and
                     car, with CAMERA=auto, BLUETOOTH=auto, WIFI=auto: when the hardware is there)
  --music DIR        music for the stick's ~/Music (default: MUSIC from tesla.conf)
  --no-music         no music
  --keymap LAYOUT    keyboard layout, e.g. de, us (default: this PC's: $(host_keymap))
  --timezone TZ      e.g. Europe/Berlin (default: this PC's: $(host_timezone))
  --hostname NAME    (default: tesla-mcu2)
  --ssh              an ssh server, login as mcu with your key (default ~/.ssh/id_ed25519.pub)
  --ssh-key FILE     that key instead (implies --ssh)
  --secure-boot      boot with Secure Boot on (shim + Debian's signed GRUB and kernel); the
                     camera's v4l2loopback module isn't signed, so no camera then
  --free SIZE        free space on the stick's data partition in the image (default 1G); on a
                     larger stick it grows to the stick's size at the first boot
  --rebuild          download the packages again (the base system is cached per package list)
  --out FILE         write the image there instead

On the stick: tty1 logs in as mcu and starts QtCar; Ctrl+Alt+Esc = console, Ctrl+Alt+Backspace
= restart QtCar, Ctrl+Alt+End = power off. Settings, logs, paired phones and Wi-Fi are kept on
the stick. Test it without a stick: stick/qemu.sh. More: stick/README.md.
EOF
}

host_keymap() {
    localectl status 2>/dev/null | sed -n 's/^ *X11 Layout: *\([^,]*\).*/\1/p' | grep . || echo us
}
host_timezone() {
    timedatectl show -p Timezone --value 2>/dev/null | grep . || echo UTC
}

# stick_conf FILE: the stick's tesla.conf
stick_conf() {
    local out=$1
    if [ -n "$S_CONF" ]; then
        cp "$S_CONF" "$out"
        return
    fi
    if [ -f "$CONF_FILE" ]; then cp "$CONF_FILE" "$out"; else cp tesla.conf.example "$out"; fi
    cat >>"$out" <<EOF

# --- added by ./tesla build-stick ($(date +%F)); later lines win over the ones above ---------
# Where things are on the stick; SCREEN is native anyway (tesla-session starts --native).
IMAGE=./mcu2.ext4
CHROOT=./chroot
SCREEN=native
# stick only: auto = the first webcam found / on when there's a Bluetooth adapter, at each start
CAMERA=auto
BLUETOOTH=auto
# the car's Wi-Fi (QtCar's settings) on the PC's adapter while QtCar runs
WIFI=auto
MUSIC=$( [ -n "$S_MUSIC" ] && echo "~/Music" )
EOF
}

cmd_build_stick() {
    local S_CONF="" S_MUSIC=unset S_KEYMAP="" S_TZ="" S_HOST=tesla-mcu2 S_SSH=0 S_KEY="" S_SB=0
    local S_FREE=1G S_REBUILD=0 S_OUT=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --conf) S_CONF=$(abspath "$2" "$CALLER_PWD"); shift ;;
            --music) S_MUSIC=$(abspath "$2" "$CALLER_PWD"); shift ;;
            --no-music) S_MUSIC="" ;;
            --keymap) S_KEYMAP=$2; shift ;;
            --timezone) S_TZ=$2; shift ;;
            --hostname) S_HOST=$2; shift ;;
            --ssh) S_SSH=1 ;;
            --ssh-key) S_SSH=1; S_KEY=$(abspath "$2" "$CALLER_PWD"); shift ;;
            --secure-boot) S_SB=1 ;;
            --free) S_FREE=$2; shift ;;
            --rebuild) S_REBUILD=1 ;;
            --out) S_OUT=$(abspath "$2" "$CALLER_PWD"); shift ;;
            -h|--help) build_stick_usage; return 0 ;;
            *) die "unknown option: $1 (./tesla build-stick --help)" ;;
        esac
        shift
    done
    [ -n "$S_CONF" ] && [ ! -f "$S_CONF" ] && die "no file $S_CONF"
    [ "$S_MUSIC" = unset ] && S_MUSIC=$MUSIC
    [ -z "$S_MUSIC" ] || [ -d "$S_MUSIC" ] || die "no music folder $S_MUSIC (--music DIR or --no-music)"
    S_KEYMAP=${S_KEYMAP:-$(host_keymap)}
    S_TZ=${S_TZ:-$(host_timezone)}
    [ -f "/usr/share/zoneinfo/$S_TZ" ] || die "unknown time zone $S_TZ"
    local key_text="" free_mib
    if [ "$S_SSH" = 1 ]; then
        if [ -z "$S_KEY" ]; then
            for S_KEY in ~/.ssh/id_ed25519.pub ~/.ssh/id_ecdsa.pub ~/.ssh/id_rsa.pub ""; do
                [ -f "$S_KEY" ] && break
            done
            [ -n "$S_KEY" ] || die "no ssh key in ~/.ssh (--ssh-key FILE)"
        fi
        key_text=$(grep -v '^#' "$S_KEY" | grep .) || die "no key in $S_KEY"
        grep -q PRIVATE <<<"$key_text" && die "$S_KEY is a private key; give the .pub file"
    fi
    free_mib=$(numfmt --from=iec --to-unit=1048576 "$S_FREE" 2>/dev/null) || die "bad size: $S_FREE"

    step "Checks"
    command -v podman >/dev/null || die "podman is needed ($(pkg_install podman))"
    [ -f "$IMAGE" ] || die "no image at $IMAGE (./tesla build-image)"
    if image_mounted >/dev/null || [ -n "$(image_mounted_elsewhere)" ]; then
        die "the image is mounted (./tesla start running?); stop it first: ./tesla stop"
    fi
    [ -x "$TOUCH_BIN" ] || die "tesla-touch isn't built (cd tesla-touch && cargo build --release)"
    git -C "$TESLA_DIR" rev-parse >/dev/null 2>&1 || die "not a git checkout (the stick gets the files git tracks or would)"
    local stamp
    stamp=$(kit_stamp)
    [ -n "$stamp" ] || warn "the image has no kit version stamp; ./tesla build-image first?"
    [ "$(sed -n 's/^kit=//p' <<<"$stamp")" = "$(kit_version_for "$stamp")" ] ||
        warn "the image was patched by another kit version: ./tesla build-image first?"
    [ -f "$(can_db)" ] || warn "no CAN signal database ($(can_db)): no vehicle data on the stick"
    info "image $IMAGE, keyboard $S_KEYMAP, time zone $S_TZ, host name $S_HOST"
    info "music: ${S_MUSIC:-none}, ssh: $( [ "$S_SSH" = 1 ] && echo "yes ($S_KEY)" || echo no)," \
        "Secure Boot: $( [ "$S_SB" = 1 ] && echo yes || echo no)"

    mkdir -p "$STICK_WORK/cache"
    if ! podman image exists "$STICK_BUILDER"; then
        step "Builder container ($STICK_BUILDER, once)"
        mkdir -p "$STICK_WORK/ctx"
        podman build -q -t "$STICK_BUILDER" -f - "$STICK_WORK/ctx" <<'EOF' >/dev/null || die "podman build failed"
FROM docker.io/library/debian:trixie
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        mmdebstrap squashfs-tools dosfstools mtools fdisk e2fsprogs grub-efi-amd64-bin \
        grub-efi-amd64-signed shim-signed ca-certificates && rm -rf /var/lib/apt/lists/*
EOF
    fi

    # what goes into the container's /work
    { git -C "$TESLA_DIR" ls-files --cached --others --exclude-standard; echo "${TOUCH_BIN#"$TESLA_DIR/"}"
      [ -f "$(can_db)" ] && can_db; } >"$STICK_WORK/files.txt"
    stick_conf "$STICK_WORK/tesla.conf"
    printf '%s\n' "$stamp" >"$STICK_WORK/kit-stamp"
    {
        printf 'SUITE=trixie OUT_NAME=mcu2-stick.img\n'
        printf 'KEYMAP=%q TIMEZONE=%q HOSTNAME=%q\n' "$S_KEYMAP" "$S_TZ" "$S_HOST"
        printf 'SSH=%q SSH_KEY=%q SECURE_BOOT=%q REBUILD=%q EXTRA_MIB=%q\n' \
            "$S_SSH" "$key_text" "$S_SB" "$S_REBUILD" "$free_mib"
    } >"$STICK_WORK/stick.env"

    local vols=(-v "$TESLA_DIR:/repo:ro" -v "$(realpath "$IMAGE"):/in/mcu2.ext4:ro" -v "$STICK_WORK:/work")
    [ -n "$S_MUSIC" ] && vols+=(-v "$S_MUSIC:/in/music:ro")
    podman run --rm --privileged "${vols[@]}" "$STICK_BUILDER" bash /repo/stick/build-inside.sh ||
        die "the build failed (above)"
    local out=$STICK_WORK/mcu2-stick.img
    if [ -n "$S_OUT" ]; then mv "$out" "$S_OUT" && out=$S_OUT; fi

    step "Done: ${out#"$CALLER_PWD/"}"
    info "Test it here: stick/qemu.sh $( [ "$S_SB" = 1 ] && echo "--secure-boot ")$out"
    info "Write it to a stick (check the device with lsblk first; everything on it is lost):"
    info "  sudo dd if=$out of=/dev/sdX bs=4M conv=fsync status=progress"
    info "Contains the firmware: for your own use only, don't share the file or the stick."
}
