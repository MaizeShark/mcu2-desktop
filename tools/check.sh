# Checks before a start (sourced by ./tesla). `./tesla check` shows all of them; `./tesla start`
# shows only warnings and failures and stops on a failure.

CHECK_FAIL=0 CHECK_WARN=0 CHECK_ALL=0

# chk ok|off|warn|fail TEXT [HINT]
chk() {
    local lvl=$1 text=$2 hint=${3:-} tag
    case "$lvl" in
        ok) tag="${GRN}ok  $R0" ;;
        off) tag="${DIM}--  $R0" ;;
        warn) tag="${YEL}WARN$R0"; CHECK_WARN=$((CHECK_WARN + 1)) ;;
        fail) tag="${RED}FAIL$R0"; CHECK_FAIL=$((CHECK_FAIL + 1)) ;;
    esac
    [ "$CHECK_ALL" = 1 ] || [ "$lvl" = warn ] || [ "$lvl" = fail ] || return 0
    printf '  %s  %s\n' "$tag" "$text"
    [ -z "$hint" ] || printf '        %s%s%s\n' "$DIM" "$hint" "$R0"
}

# feature_level ENABLED: fail when the feature is on, "off" otherwise (./tesla check shows it)
lvl() { [ "$1" = 1 ] && echo fail || echo off; }
on() { [ -n "$1" ] && [ "$1" != 0 ] && echo 1 || echo 0; }

# tool LEVEL-IF-MISSING COMMAND PACKAGE WHAT
tool() {
    if command -v "$2" >/dev/null 2>&1; then
        chk ok "$2 ($4)"
    else
        chk "$1" "$2 missing ($4)" "install it: $(pkg_install "$3")"
    fi
}

check_host() {
    if [ "$SCREEN" = native ]; then
        tool fail xrandr x11-xserver-utils "native screen: size and scaling"
        tool fail xauth xauth "native screen: access for QtCar"
        tool fail xdpyinfo x11-utils "native screen"
        if DISPLAY=$NATIVE_DISPLAY xdpyinfo >/dev/null 2>&1; then
            chk ok "native screen $NATIVE_DISPLAY ($(native_output | cut -f2))"
        else
            chk fail "can't open the X display $NATIVE_DISPLAY" "run it from the desktop session, or set NATIVE_DISPLAY / XAUTHORITY"
        fi
        local ts
        if [ "$TOUCH_DEVICE" = auto ]; then ts=$(find_touchscreen); else ts="$TOUCH_DEVICE"; fi
        if [ -n "$ts" ]; then chk ok "touchscreen: ${ts//$'\t'/ }"
        else chk warn "no touchscreen found: the mouse works as one finger (tesla-touch mouse mode)"; fi
    else
        tool fail Xvfb xvfb "virtual screen"
        tool fail x11vnc x11vnc "VNC server"
        tool fail xdpyinfo x11-utils "waits for the screen"
    fi
    tool fail curl curl "talks to QtCar"
    if python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        chk ok "python3 $(python3 -c 'import platform; print(platform.python_version())')"
    else
        chk fail "python3 3.11 or newer missing" "vehicle/, the kit and the log summary need it"
    fi
    if [ "$SCREEN" = native ]; then
        :
    elif [ "$VIEWER" = none ]; then
        chk off "VNC viewer: none (VIEWER=none)"
    else
        local v found=""
        for v in ${VIEWER:-krdc remote-viewer remmina vncviewer}; do command -v "$v" >/dev/null && found=$v && break; done
        if [ -n "$found" ]; then chk ok "VNC viewer: $found"
        else chk warn "no VNC viewer (${VIEWER:-krdc, remote-viewer, remmina, vncviewer})" \
            "install one ($(pkg_install krdc)), or connect to vnc://localhost:$VNC_PORT yourself"
        fi
    fi
    if [ -x "$TOUCH_BIN" ]; then
        chk ok "tesla-touch built"
    else
        chk fail "tesla-touch not built" "(cd tesla-touch && cargo build --release)"
    fi
    if [ -e /dev/uinput ]; then chk ok "/dev/uinput (touch)"
    else chk fail "/dev/uinput missing (touch)" "sudo modprobe uinput"; fi
    if cmp -s /etc/udev/rules.d/99-tesla-touch.rules "$TESLA_DIR/tesla-touch/99-tesla-touch.rules"; then
        chk ok "udev rule for tesla-touch"
    elif [ -f /etc/udev/rules.d/99-tesla-touch.rules ]; then
        chk warn "udev rule for tesla-touch is older than tesla-touch/99-tesla-touch.rules (arcade devices)" \
            "sudo cp tesla-touch/99-tesla-touch.rules /etc/udev/rules.d/ && sudo udevadm control --reload"
    else
        chk warn "udev rule for tesla-touch not installed: the host desktop reacts to the virtual touchscreen too" \
            "sudo cp tesla-touch/99-tesla-touch.rules /etc/udev/rules.d/ && sudo udevadm control --reload"
    fi
}

check_config() {
    [ -n "${CONF_UNKNOWN:-}" ] &&
        chk warn "unknown settings in $(basename "$CONF_FILE"):$CONF_UNKNOWN" "known: ${CONF_VARS[*]}"
    case "$SIZE" in [0-9]*x[0-9]*) ;; *) chk fail "SIZE=$SIZE is not WIDTHxHEIGHT" ;; esac
    case "$CURSOR" in auto|on|off) ;; *) chk fail "CURSOR=$CURSOR is not auto, on or off" ;; esac
    case "$ROTATE" in normal|left|right|inverted) ;; *) chk fail "ROTATE=$ROTATE is not normal, left, right or inverted" ;; esac
    if [ "$SCREEN" != native ] && [ "$ROTATE" != normal ]; then
        chk fail "ROTATE=$ROTATE needs --native"
    fi
    return 0
}

check_image() {
    local src stamp kit have date maps other
    other=$(image_mounted_elsewhere | head -1)
    if [ -n "$other" ] && ! image_mounted >/dev/null; then
        chk fail "the image is mounted at $other, not at $CHROOT (mounting it twice would corrupt it)" \
            "unmount it: ./tesla stop; sudo umount $other  (or use it: CHROOT=$other in tesla.conf)"
        return
    fi
    if src=$(image_mounted); then
        if [ "$(realpath "$src" 2>/dev/null)" = "$IMAGE" ] || loop_devs_of "$IMAGE" | grep -qx "$src"; then
            chk ok "image mounted at $CHROOT"
        elif [ "$src" = "/dev/mapper/$RAM_DM" ]; then
            chk ok "image mounted at $CHROOT, its writes in RAM (IMAGE_RAM)"
        else
            chk warn "$CHROOT has $src mounted, not $IMAGE" "that one is used; to switch: ./tesla stop && sudo umount $CHROOT"
        fi
    elif [ -f "$IMAGE" ]; then
        chk ok "image $IMAGE ($(( $(stat -c %s "$IMAGE") / 1024 / 1024 / 1024 )) GB, mounted at start)"
    else
        chk fail "no image at $IMAGE" "build one: ./tesla build-image --fresh (needs the firmware dump in $DUMP)"
        return
    fi
    stamp=$(kit_stamp)
    kit=$(kit_version_for "$stamp")
    have=$(sed -n 's/^kit=//p' <<<"$stamp")
    date=$(sed -n 's/^date=//p' <<<"$stamp" | cut -c1-16 | tr T ' ')
    if [ -z "$stamp" ]; then
        if [ -r "$IMAGE" ] || image_mounted >/dev/null; then
            chk warn "the image has no kit version stamp (built before 2026-09-24)" \
                "update it: ./tesla build-image  (or see what's missing: ./tesla build-image --check)"
        else
            chk off "kit version unknown (image not readable)"
        fi
    elif [ "$have" = "$kit" ]; then
        chk ok "patch kit up to date ($kit, applied $date)"
    else
        chk warn "the image was patched by another kit version ($have, $date; this kit: $kit)" \
            "update it: ./tesla build-image  (./tesla build-image --check shows what changes)"
    fi
    if image_mounted >/dev/null && other=$(owners_lost "$CHROOT"); then
        chk fail "the image lost the firmware's file owners: $other" \
            "extract the firmware dump again as root (sudo), then: ./tesla build-image --fresh"
    elif [ -d "$DUMP" ] && other=$(owners_lost "$DUMP"); then
        chk warn "the firmware dump lost its file owners (extracted without root?): $other" \
            "extract it again as root (sudo), then: ./tesla build-image --fresh"
    fi
    maps=$(image_file opt/navigon/VERSION)
    if [ -n "$maps" ]; then
        [ "$NAV" = 1 ] && chk ok "maps: $maps (navigation)" || chk off "maps: $maps (navigation off: NAV=0)"
    else
        chk off "no offline maps (navigation off)" "see navigation/README.md"
    fi
}

# ports [quiet]: firmware service ports held by processes that aren't this run's
check_ports() {
    local busy p line who
    busy=$( { ss -Hltnp 2>/dev/null; ss -Hlunp 2>/dev/null; } | awk '{print $4, $NF}')
    for p in $SERVICE_TCP_PORTS $SERVICE_UDP_PORTS $VNC_PORT $PANEL_PORT; do
        line=$(grep -E "[:.]$p users:|[:.]$p \$|[:.]$p " <<<"$busy" | head -1)
        [ -n "$line" ] || continue
        who=$(grep -o '"[^"]*",pid=[0-9]*' <<<"$line" | head -1 | tr -d '"' | sed 's/,pid=/ pid /')
        chk fail "port $p is in use by ${who:-a process of another user (root?)}" \
            "an earlier run? ./tesla stop. The sandbox? podman ps; podman kill <id>"
    done
    if podman ps -q 2>/dev/null | grep -q .; then
        chk warn "podman containers are running (./sandbox.sh?): they share the network and the service ports" \
            "podman ps; podman kill <id>"
    fi
}

check_features() {
    local v n
    # VEHICLE
    v=$(on "$VEHICLE")
    if [ -s "$(can_db)" ]; then
        chk ok "CAN signal database (vehicle data): $(can_db)"
    elif python3 -c 'import unicorn, elftools' 2>/dev/null; then
        chk ok "CAN signal database: extracted from the firmware at the first VEHICLE=1 start"
    else
        chk "$( [ "$v" = 1 ] && echo warn || echo off)" "CAN signal database missing, can't build it (vehicle data)" \
            "pip install --user unicorn pyelftools (it's extracted from the firmware once)"
    fi
    check_choice color "$VEHICLE_COLOR" $(look_names color)
    check_choice wheels "$VEHICLE_WHEELS" $(look_names wheels)
    check_choice performance "$VEHICLE_PERFORMANCE" $(look_names performance)
    if [ "$v" = 0 ] && [ -n "$VEHICLE_COLOR$VEHICLE_WHEELS$VEHICLE_PERFORMANCE" ]; then
        chk warn "VEHICLE_COLOR/WHEELS/PERFORMANCE only apply with vehicle data" "--vehicle (VEHICLE=1)"
    fi

    # AUDIO
    v=$(on "$AUDIO")
    tool "$(lvl "$v")" arecord alsa-utils "audio: captures the firmware's loopback"
    tool "$(lvl "$v")" sox sox "audio: 8 channels -> stereo"
    if command -v pw-play >/dev/null; then chk ok "pw-play (audio: plays on this PC, PipeWire)"
    elif command -v pacat >/dev/null; then chk ok "pacat (audio: plays on this PC, PulseAudio)"
    else tool "$(lvl "$v")" aplay alsa-utils "audio: plays on this PC (ALSA)"; fi
    # both firmwares: AudioWeaver's amplifier output on "model3" (the S/X's audiod too)
    local card=model3
    if grep -q "^ *[0-9]* \[$card *\]" /proc/asound/cards 2>/dev/null; then
        chk ok "sound card $card (snd-aloop) loaded"
    elif lsmod 2>/dev/null | grep -q '^snd_aloop'; then
        chk "$( [ "$v" = 1 ] && echo fail || echo off)" "snd-aloop is loaded, but not as card $card (audio)" \
            "sudo modprobe -r snd-aloop (the next start loads the required card)"
    elif PATH=$PATH:/sbin:/usr/sbin modinfo snd-aloop >/dev/null 2>&1 ||
        ls /lib/modules/"$(uname -r)"/kernel/sound/drivers/snd-aloop.ko* >/dev/null 2>&1; then
        chk ok "snd-aloop available (loaded as card $card at start)"
    else
        chk "$(lvl "$v")" "kernel module snd-aloop not available (audio)" "$(pkg_install "linux-modules-extra-$(uname -r)")"
    fi

    # the loopback cards must not be taken by PulseAudio / PipeWire
    if [ "$(on "$AUDIO")" = 1 ] && pgrep -x "pulseaudio|pipewire" >/dev/null && [ ! -f /etc/udev/rules.d/99-tesla-sound.rules ]; then
        chk warn "the sound server may adopt the loopback cards (underruns, stutter)" \
            "sudo cp tools/99-tesla-sound.rules /etc/udev/rules.d/ && sudo udevadm control --reload (then reload snd-aloop or reboot)"
    fi

    # BLUETOOTH
    v=$(on "$BLUETOOTH")
    if [ -d "/sys/class/bluetooth/$BT_ADAPTER" ]; then
        chk ok "Bluetooth adapter $BT_ADAPTER ($(cat /sys/class/bluetooth/$BT_ADAPTER/device/uevent 2>/dev/null | sed -n 's/^DRIVER=//p'))"
    else
        chk "$( [ "$v" = 1 ] && echo fail || echo off)" "no Bluetooth adapter $BT_ADAPTER (bluetooth)" "BT_ADAPTER=hciN in tesla.conf"
    fi

    # WIFI
    v=$(on "$WIFI")
    local wdev=$WIFI_ADAPTER
    [ "$wdev" = auto ] && wdev=$(find_wifi)
    if [ -n "$wdev" ] && [ -d "/sys/class/net/$wdev/wireless" ]; then
        chk "$( [ "$v" = 1 ] && echo ok || echo off)" "Wi-Fi adapter $wdev ($(sed -n 's/^DRIVER=//p' "/sys/class/net/$wdev/device/uevent" 2>/dev/null))$( [ "$v" = 1 ] || echo ' (wifi off: --wifi)')"
    else
        chk "$( [ "$v" = 1 ] && echo fail || echo off)" "no Wi-Fi adapter${wdev:+ $wdev} (wifi)" "WIFI_ADAPTER=NAME in tesla.conf"
    fi

    # CELLULAR: ModemManager's modems (NetworkManager connects them; QtCar only shows the link)
    v=$(on "$CELLULAR")
    [ "$CELLULAR" = auto ] && v=auto    # on when there is a modem: nothing to complain about
    local mlist
    if ! mlist=$(modems); then
        if [ "$v" = 1 ]; then
            chk fail "ModemManager isn't running (cellular)" "install and start it: sudo dnf/apt install ModemManager; sudo systemctl enable --now ModemManager"
        else
            chk off "no ModemManager (cellular)"
        fi
    elif [ -z "$mlist" ]; then
        if [ "$v" = 1 ]; then
            chk warn "ModemManager runs, no modem found (cellular)" "plug the modem in; usb-modeswitch switches most USB sticks into modem mode"
        else
            chk off "ModemManager runs, no modem found (cellular)"
        fi
    else
        while IFS= read -r m; do
            chk "$( [ "$v" = 0 ] && echo off || echo ok)" "modem $m$( [ "$v" = 0 ] && echo ' (cellular off: --cellular)')"
        done <<<"$mlist"
    fi

    # MUSIC
    if [ -n "$MUSIC" ]; then
        if [ -d "$MUSIC" ]; then
            n=$(find "$MUSIC" -type f \( -iname '*.mp3' -o -iname '*.flac' -o -iname '*.m4a' -o -iname '*.ogg' \
                -o -iname '*.wav' -o -iname '*.aac' \) 2>/dev/null | head -5000 | wc -l)
            chk ok "music folder $MUSIC ($n audio files)"
            [ "$n" = 0 ] && chk warn "no audio files (mp3, flac, m4a, ogg, wav) in $MUSIC"
            [ "$(on "$AUDIO")" = 1 ] || chk warn "music without sound: nothing to hear" "--audio (AUDIO=1)"
        else
            chk fail "MUSIC=$MUSIC is not a folder"
        fi
    else
        chk off "music folder: none (--music DIR)"
    fi

    # CAMERA
    v=$(on "$CAMERA")
    tool "$(lvl "$v")" ffmpeg ffmpeg "camera: converts the picture"
    if [ -e "$CAMERA_DEV" ]; then
        chk ok "camera output $CAMERA_DEV (v4l2loopback)"
    else
        chk "$( [ "$v" = 1 ] && echo warn || echo off)" "$CAMERA_DEV missing: no backup camera" \
            "once: sudo modprobe v4l2loopback devices=2 video_nr=10,32 exclusive_caps=1,1"
    fi
    if [ "$v" = 1 ]; then
        if [ -e "$CAMERA" ]; then chk ok "camera source $CAMERA"
        else chk warn "camera source $CAMERA doesn't exist (yet); retried every 2 s" \
            "e.g. scrcpy --video-source=camera --v4l2-sink=$CAMERA --no-video-playback"; fi
    fi

    # GPS
    if [ -n "$GPS" ]; then
        local dev
        dev=$(sed -n 's/.*--uart  *\([^ ]*\).*/\1/p' <<<"$GPS")
        if [ -n "$dev" ]; then
            if [ -r "$dev" ]; then chk ok "GPS receiver $dev"
            elif [ -e "$dev" ]; then chk fail "GPS receiver $dev not readable" "sudo usermod -aG dialout $USER (then log in again)"
            else chk warn "GPS receiver $dev not there (plugged in?)"; fi
        else
            chk ok "fake GPS: $GPS"
        fi
        case "$GPS" in *--to*) [ -n "$(image_file opt/navigon/VERSION)" ] ||
            chk fail "GPS --to needs offline maps for the route" "see navigation/README.md" ;; esac
    else
        chk off "GPS: none (--gps LAT,LON)"
    fi
}

# check_choice NAME VALUE CHOICE...
check_choice() {
    local name=$1 value=$2; shift 2
    [ -n "$value" ] || return 0
    case " $* " in *" $value "*) chk ok "$name $value" ;; *) chk fail "unknown $name '$value'" "one of: $*" ;; esac
}

# run_checks all|start
run_checks() {
    CHECK_ALL=0; [ "$1" = all ] && CHECK_ALL=1
    [ "$CHECK_ALL" = 1 ] && say "${B}Host$R0"
    check_host
    check_config
    [ "$CHECK_ALL" = 1 ] && say "${B}Image$R0"
    check_image
    [ "$CHECK_ALL" = 1 ] && say "${B}Features$R0 (enabled: $(features_on))"
    check_features
    if [ "$CHECK_ALL" = 1 ]; then
        say "${B}Running$R0"
        local pid
        if pid=$(running_pid); then
            chk ok "running (pid $pid, logs $(readlink "$LOGS/current")); ./tesla status"
        else
            local before=$((CHECK_FAIL + CHECK_WARN))
            check_ports
            [ -z "$(mounts_below)" ] || chk warn "mounts left in $CHROOT from an earlier run" "./tesla stop"
            [ $((CHECK_FAIL + CHECK_WARN)) = "$before" ] && chk ok "not running, ports free"
        fi
    fi
}

features_on() {
    local f=""
    [ "$(on "$VEHICLE")" = 1 ] && f="$f vehicle"
    [ -n "$GPS" ] && f="$f gps"
    [ "$(on "$AUDIO")" = 1 ] && f="$f audio"
    [ -n "$MUSIC" ] && f="$f music"
    [ -n "$CAMERA" ] && f="$f camera"
    [ "$(on "$BLUETOOTH")" = 1 ] && f="$f bluetooth"
    [ "$(on "$WIFI")" = 1 ] && f="$f wifi"
    { [ "$CELLULAR" = 1 ] || { [ "$CELLULAR" = auto ] && [ -n "$(modems)" ]; }; } && f="$f cellular"
    [ "$NAV" = 1 ] && f="$f nav"
    echo "${f# }"
}
