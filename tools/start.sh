# ./tesla start (sourced by ./tesla): QtCar in the chroot on a virtual X display, with VNC, touch,
# the firmware services and the optional features.

# usage_wrap TEXT: TEXT wrapped into the help's second column
usage_wrap() { echo "$1" | fold -s -w 75 | sed 's/ *$//; 2,$s/^/                         /'; }

start_usage() {
    cat <<EOF
${B}./tesla start$R0 [options]     start everything; Ctrl+C or ./tesla stop stops it

Features (defaults from tesla.conf):
  --vehicle              fake vehicle data (simulator + tesla-can.py), web panel on :$PANEL_PORT
  --color C              $(usage_wrap "car color: $(look_names color) (implies --vehicle)")
  --wheels W             $(usage_wrap "$(look_names wheels) (implies --vehicle)")
  --performance P        $(usage_wrap "$(look_names performance) (implies --vehicle)")
  --gps LAT,LON          fake GPS at a fixed position
  --gps "OPTIONS"        tesla-gps.py options: "--pos LAT,LON --to LAT,LON [--speedup 2]" drives a
                         route, "--uart /dev/ttyACM0 [--baud 9600]" passes a real receiver through
  --audio                sound: Model 3 amplifier or Model S/X web media/Browser -> PC speakers
  --wifi                 the car's Wi-Fi (wpa_supplicant, connman, QtCarConnMan) on this PC's adapter:
                         QtCar's Wi-Fi settings connect the PC (NetworkManager lets go of it)
  --cellular             $(usage_wrap "this PC's modem (ModemManager; NetworkManager connects it) as the car's LTE: while the PC is online over it, QtCar shows the cell icon, network type, bars and operator (auto: when there is a modem)")
  --bluetooth            the car's Bluetooth (Broadcom stack) on this PC's adapter: phone, contacts,
                         music with --audio (BT_ADAPTER=hci0; BlueZ is stopped while it runs)
  --music DIR            DIR as a USB stick with music (Media -> USB)
  --camera DEV           backup camera from a V4L2 device (e.g. scrcpy --v4l2-sink=/dev/video10;
                         auto: the first webcam)
  --no-nav               no offline navigation (on when maps are installed)
  --services "A B"       more firmware services (./tesla services lists them)
  --no-vehicle, --no-gps, --no-audio, --no-music, --no-camera   switch off what tesla.conf enables

Screen and access:
  --native               QtCar on this PC's own screen instead of a virtual one over VNC:
                         fullscreen, scaled to fit with black bars, the touchscreen passed through
                         (without one the mouse is a finger). Run it in the desktop session.
  --vnc                  the virtual screen over VNC (default)
  --touch-device DEV     native: the touchscreen (default: found automatically)
  --native-output NAME   native: the monitor for QtCar (xrandr name; default: the primary one).
                         Other monitors are off while it runs and come back afterwards
  --rotate DIRECTION     native: rotate the output and touchscreen: left, right, inverted,
                         normal (default); also 90=right, 180=inverted, 270=left
  --no-gpu, --gpu        native: OpenGL in software instead of on the GPU (default: the GPU when
                         it's an Intel one; --gpu also tries an AMD one, untested)
  --cursor, --no-cursor  native: show / hide the mouse cursor (default: hidden with a touchscreen,
                         as on the car; shown when the mouse is the finger)
  --size WxH             the UI's size (default 1920x1200, the car's screen)
  --image-ram, --no-image-ram
                         the chroot's writes go to RAM and into the image at the end (default:
                         when the image is on a USB drive, e.g. the stick)
  --viewer CMD|none      VNC viewer to open (default: krdc, remote-viewer, remmina or vncviewer)
  --remote               VNC (password: x11vnc -storepasswd) and panel reachable from the network
  --no-restart           don't restart QtCar when it exits

Other:
  --config FILE          settings file instead of tesla.conf
  --qtcar-args "..."     more QtCar options (e.g. --prod)
  --verbose, -v          QtCar's output here too (it's always in logs/latest/qtcar.log)
  --strace "SVC ..."     run these services under strace (-f -tt); the traces end up in the
                         log folder as strace-SVC.txt (debugging)
  --gdb "SVC"            run that service interactively under gdbserver on port 8888
  --dry-run              only run the checks and show what would start
  --force                start even when a check fails

Examples:
  ./tesla start --vehicle --gps 37.4419,-122.1430
  ./tesla start --vehicle --gps "--pos 37.4419,-122.1430 --to 37.4275,-122.1697 --speedup 2"
  ./tesla start --audio --music ~/Music --camera /dev/video10 --color DeepBlue
EOF
}

parse_start_opts() {
    DRY_RUN=0 FORCE=0 VERBOSE=0 STRACE="" GDB=""
    local opt val
    while [ $# -gt 0 ]; do
        opt=$1
        case "$opt" in --*=*) val=${opt#*=}; opt=${opt%%=*}; set -- "$opt" "$val" "${@:2}" ;; esac
        case "$opt" in
            --color|--wheels|--performance|--gps|--music|--camera|--services|--size|--viewer|--config|--strace|--gdb|--touch-device|--qtcar-args|--rotate)
                [ $# -ge 2 ] || die "$opt needs a value (./tesla start --help)" ;;
        esac
        case "$opt" in
            --vehicle) set_opt VEHICLE 1 ;;
            --no-vehicle) set_opt VEHICLE 0 ;;
            --color) set_opt VEHICLE_COLOR "$2"; set_opt VEHICLE 1; shift ;;
            --wheels) set_opt VEHICLE_WHEELS "$2"; set_opt VEHICLE 1; shift ;;
            --performance) set_opt VEHICLE_PERFORMANCE "$2"; set_opt VEHICLE 1; shift ;;
            --gps) set_opt GPS "$2"; shift ;;
            --no-gps) set_opt GPS "" ;;
            --audio) set_opt AUDIO 1 ;;
            --bluetooth) set_opt BLUETOOTH 1 ;;
            --no-bluetooth) set_opt BLUETOOTH 0 ;;
            --wifi) set_opt WIFI 1 ;;
            --no-wifi) set_opt WIFI 0 ;;
            --cellular) set_opt CELLULAR 1 ;;
            --no-cellular) set_opt CELLULAR 0 ;;
            --no-audio) set_opt AUDIO 0 ;;
            --music) set_opt MUSIC "$2"; shift ;;
            --no-music) set_opt MUSIC "" ;;
            --camera) set_opt CAMERA "$2"; shift ;;
            --no-camera) set_opt CAMERA "" ;;
            --nav) set_opt NAV 1 ;;
            --no-nav) set_opt NAV 0 ;;
            --services) set_opt SERVICES "$2"; shift ;;
            --size) set_opt SIZE "$2"; shift ;;
            --image-ram) set_opt IMAGE_RAM on ;;
            --no-image-ram) set_opt IMAGE_RAM off ;;
            --viewer) set_opt VIEWER "$2"; shift ;;
            --remote) set_opt REMOTE 1 ;;
            --native) set_opt SCREEN native ;;
            --gpu) set_opt GPU on ;;
            --cursor) set_opt CURSOR on ;;
            --native-output) set_opt NATIVE_OUTPUT "$2"; shift ;;
            --rotate) set_opt ROTATE "$2"; shift ;;
            --no-cursor) set_opt CURSOR off ;;
            --no-gpu) set_opt GPU off ;;
            --vnc) set_opt SCREEN vnc ;;
            --touch-device) set_opt TOUCH_DEVICE "$2"; shift ;;
            --qtcar-args) set_opt QTCAR_ARGS "$2"; shift ;;
            --no-restart) set_opt RESTART 0 ;;
            --config) shift ;;      # read before the config (./tesla)
            --dry-run|-n) DRY_RUN=1 ;;
            --verbose|-v) VERBOSE=1 ;;
            --strace) STRACE=$2; shift ;;
            --gdb) GDB=$2; shift;;
            --force|-f) FORCE=1 ;;
            -h|--help) start_usage; exit 0 ;;
            *) die "unknown option: $opt (./tesla start --help)" ;;
        esac
        shift
    done
    # image on a USB stick: its writes into RAM, merged at the end (image_ram_mount)
    case "$IMAGE_RAM" in
        auto) if on_usb "$IMAGE" && PATH=$PATH:/sbin:/usr/sbin command -v dmsetup >/dev/null; then
                  IMAGE_RAM=1; else IMAGE_RAM=0; fi ;;
        on) IMAGE_RAM=1 ;;
        off) IMAGE_RAM=0 ;;
    esac
    # auto: decided at each start (a webcam or adapter that isn't always there; the stick)
    [ "$CAMERA" = auto ] && CAMERA=$(find_camera)
    if [ "$BLUETOOTH" = auto ]; then
        if [ -e "/sys/class/bluetooth/$BT_ADAPTER" ]; then BLUETOOTH=1; else BLUETOOTH=0; fi
    fi
    if [ "$WIFI" = auto ]; then
        if [ -n "$(find_wifi)" ]; then WIFI=1; else WIFI=0; fi
    fi
    WIFI=$(on "$WIFI")
    if [ "$CELLULAR" = auto ]; then
        if [ -n "$(modems)" ]; then CELLULAR=1; else CELLULAR=0; fi
    fi
    CELLULAR=$(on "$CELLULAR")
    case "$ROTATE" in
        0) ROTATE=normal ;; 90) ROTATE=right ;; 180) ROTATE=inverted ;;
        270|-90) ROTATE=left ;;
    esac
    # the image's firmware (its kit profile): QtCar's screen size, the cluster (Model S/X)
    PROFILE=$(image_profile)
    CLUSTER_SIZE=$(profile_value "$PROFILE" cluster)
    if [ "${CONF_SRC[SIZE]}" = default ]; then
        local center
        center=$(profile_value "$PROFILE" center)
        SIZE=${center:-$SIZE}
    fi
    # the cluster needs a screen of its own: a second one on Xvfb (VNC mode, :1.1) or, natively,
    # a virtual one next to the PC's screen (Xvfb :1), both on VNC port VNC_PORT+1
    VEHICLE=$(on "$VEHICLE") AUDIO=$(on "$AUDIO") BLUETOOTH=$(on "$BLUETOOTH") REMOTE=$(on "$REMOTE") RESTART=$(on "$RESTART") NAV=$(on "$NAV")
}

PIDS=()
# add_pid PID NAME: a host-side helper, stopped at the end (and by ./tesla stop if this is gone)
add_pid() {
    PIDS+=("$1")
    echo "$1 $(proc_start "$1") $2" >>"$LOG_DIR/pids"
}
event() {
    printf '%s %s %s\n' "$(date +%s)" "$(date +%T)" "$*" >>"$LOG_DIR/events.log"
}
add_service() {
    local s
    for s in "$@"; do case " $SERVICES " in *" $s "*) ;; *) SERVICES="${SERVICES:+$SERVICES }$s" ;; esac; done
}

cleanup() {
    trap - EXIT INT TERM HUP
    echo
    step "Stopping"
    event "stop"
    local svc
    [ -n "$STRACE" ] && stop_chroot_procs quiet     # strace has finished writing then
    for svc in $STRACE; do     # --strace: the traces from the chroot's /tmp
        sudo mv "$CHROOT/tmp/strace-$svc.txt" "$LOG_DIR/" 2>/dev/null && sudo chown "$(id -u):$(id -g)" "$LOG_DIR/strace-$svc.txt"
    done
    teardown "$LOG_DIR" quiet
    say "    Logs: ${LOG_DIR#"$TESLA_DIR/"}"
    echo
    python3 tools/logsummary.py "$LOG_DIR" --short 2>/dev/null | sed 's/^/    /'
}

# the car's look: the car config (GTW_carConfig*) carries it; QtCar keeps its own copy in its
# settings DB and only reads it at startup, so set both. (The S/X restarts QtCar and the cluster
# when the car config differs from its copy.)
# car_opt <VAPI name> <signal> <value> <color|wheels|performance>: Model 3: the simulator's SIM_*
# signal takes the label, S/X: the GTW_* signal the value
CAR_SIM=()
car_opt() {
    local name=$1 sig=$2 value=$3 i=0 label
    for label in $(look_choices "$4"); do
        if [ "$label" = "$value" ]; then
            case $sig in SIM_*) CAR_SIM+=("$sig=$label") ;; *) CAR_SIM+=("$sig=$i") ;; esac
            sudo python3 vehicle/qtcar-settings.py "$CHROOT/home/tesla/.Tesla/data/QtCarSettings.db" "VAPI_$name=$i" >/dev/null
            # the S/X cluster keeps its own copy
            case $sig in SIM_*) ;; *)
                sudo python3 vehicle/qtcar-settings.py --owner "$(awk -F: '$1=="qtcluster"{print $3":"$4}' "$CHROOT/etc/passwd")" \
                    "$CHROOT/home/qtcluster/.Tesla/data/QtCarClusterSettings.db" "VAPI_$name=$i" >/dev/null ;;
            esac
            return 0
        fi
        i=$((i + 1))
    done
}

# The clock's time zone. QtCar sets its process time zone from GUI_timeZoneId (its settings DB)
# and the car updates that by asking Google's time zone API (with Tesla's key) for the GPS
# position, which never happens here: the image's America/Los_Angeles stayed. auto = this PC's.
set_timezone() {
    local tz=$TIMEZONE ids
    if [ "$tz" = auto ]; then
        tz=$(timedatectl show -p Timezone --value 2>/dev/null)
        [ -n "$tz" ] || tz=$(readlink /etc/localtime 2>/dev/null | sed -n 's|.*zoneinfo/||p')
    fi
    [ -n "$tz" ] || return 0
    if [ ! -f "$CHROOT/usr/share/zoneinfo/$tz" ]; then warn "unknown time zone $tz (TIMEZONE)"; return 0; fi
    ids=$(awk -F: '$1=="tesla"{print $3":"$4}' "$CHROOT/etc/passwd")
    # GUI_timeZoneName came from Google's answer ("Pacific Daylight Time"); without it it's empty
    sudo python3 vehicle/qtcar-settings.py --owner "$ids" "$CHROOT/home/tesla/.Tesla/data/QtCarSettings.db" \
        "GUI_timeZoneId=$tz" -d GUI_timeZoneName >/dev/null || warn "could not set the time zone"
}

# settings_conf SECTION KEY=VALUE...: merge keys into the car's settings.conf
settings_conf() {
    sudo python3 - "$CHROOT/home/tesla/.Tesla/car/settings.conf" "$@" <<'PY'
import configparser, sys
path, section, *kv = sys.argv[1:]
c = configparser.ConfigParser(interpolation=None)
c.optionxform = str
c.read(path)
if not c.has_section(section):
    c.add_section(section)
c[section].update(dict(x.split("=", 1) for x in kv))
with open(path, "w") as f:
    c.write(f, space_around_delimiters=False)
PY
}

print_summary() {
    local what=$1 viewer_note="" off=""
    [ -n "${VIEWER_USED:-}" ] && viewer_note=" (viewer: $VIEWER_USED)"
    if [ "$what" = Running ]; then step "$what$R0  ${DIM}(stop: Ctrl+C here, or ./tesla stop)$R0"; else step "$what"; fi
    if [ "$SCREEN" = native ]; then
        info "Screen    native $NATIVE_DISPLAY${NATIVE_OUT:+ ($NATIVE_OUT $PANEL)}, rotate: $ROTATE, touch: ${TOUCH_FROM:-mouse}, OpenGL: ${QT_GL:-software}"
    elif [ "$REMOTE" = 1 ]; then
        info "Screen    $SIZE, VNC on port $VNC_PORT of this machine, password protected$viewer_note"
    else
        info "Screen    $SIZE, VNC localhost:$VNC_PORT$viewer_note"
    fi
    [ -n "$CLUSTER_SIZE" ] && info "Cluster   $CLUSTER_SIZE, VNC port $((VNC_PORT + 1))"
    [ "$PROFILE" = 2019.20.4.2-model3 ] || info "Firmware  kit profile $PROFILE"
    local f=""
    if [ "$VEHICLE" = 1 ]; then
        f="vehicle"
        local look="$VEHICLE_COLOR $VEHICLE_WHEELS $VEHICLE_PERFORMANCE"
        look=$(echo $look)
        [ -n "$look" ] && f="$f ($look)"
    else off="$off vehicle"; fi
    if [ -n "$GPS" ]; then f="$f, GPS $GPS"; else off="$off gps"; fi
    if [ "$AUDIO" = 1 ]; then f="$f, audio"; else off="$off audio"; fi
    if [ "$BLUETOOTH" = 1 ]; then f="$f, bluetooth ($BT_ADAPTER)"; else off="$off bluetooth"; fi
    if [ "$WIFI" = 1 ]; then f="$f, wifi"; else off="$off wifi"; fi
    if [ "$CELLULAR" = 1 ]; then f="$f, cellular"; else off="$off cellular"; fi
    if [ -n "$MUSIC" ]; then f="$f, music ${MUSIC/#$HOME/\~}"; else off="$off music"; fi
    if [ -n "$CAMERA" ]; then f="$f, camera $CAMERA"; else off="$off camera"; fi
    case " $SERVICES " in *" valhalla "*) f="$f, navigation" ;; *) off="$off nav" ;; esac
    info "Features  ${f#, }"
    [ -z "$off" ] || info "Off      $off  ${DIM}(./tesla start --help)$R0"
    [ "$VEHICLE" = 1 ] && info "Panel     http://localhost:$PANEL_PORT/  (doors, charging, lights, services, logs)"
    info "Services  ${SERVICES:-none}"
    [ -n "${LOG_DIR:-}" ] && info "Logs      ${LOG_DIR#"$TESLA_DIR/"}/  (./tesla logs: summary)"
    return 0
}

cmd_start() {
    parse_start_opts "$@"
    if [ -n "${CONF_CREATED:-}" ]; then
        info "Created tesla.conf with the default settings (all commented out); edit it to change them."
    fi
    local pid
    if pid=$(running_pid); then
        die "already running (pid $pid, logs $(readlink "$LOGS/current")). Stop it first: ./tesla stop"
    fi

    step "Checking"
    run_checks start
    if [ "$CHECK_FAIL" -gt 0 ]; then
        [ "$FORCE" = 1 ] || die "$CHECK_FAIL check(s) failed, see above. ./tesla check shows all; --force starts anyway"
    elif [ "$CHECK_WARN" = 0 ]; then
        info "all ok"
    fi
    [ "$NAV" = 1 ] && [ -n "$(image_file opt/navigon/VERSION)" ] && add_service valhalla qtcar-tmserver
    if [ "$DRY_RUN" = 1 ]; then
        if [ "$VEHICLE" = 1 ]; then
            if [ "$(can_db)" = vehicle/work/can-db.json ]; then add_service qtcar-sim qtcar-vehicle; else add_service qtcar-vehicle; fi
        fi
        if [ "$AUDIO" = 1 ]; then
            if [ "$PROFILE" = 2026.8.3-models ]; then add_service audiod qtcar-audiod qtcar-mediaserver
            else add_service audioweaver audiod qtcar-audiod qtcar-mediaserver; fi
        fi
        [ "$BLUETOOTH" = 1 ] && add_service dbus bsa_server btd qtcar-bluetooth
        [ "$WIFI" = 1 ] && add_service dbus wpa_supplicant connman qtcar-connman
        [ -n "$MUSIC" ] && add_service qtcar-mediaserver
        [ -n "$GPS" ] && add_service qtcar-gpsmanager
        print_summary "Would start (--dry-run)"
        exit 0
    fi

    sudo_auth || exit 1
    LOG_DIR=$LOGS/$(date +%Y%m%d-%H%M%S)
    mkdir -p "$LOG_DIR"
    ln -sfn "$(basename "$LOG_DIR")" "$LOGS/latest"
    ln -sfn "$(basename "$LOG_DIR")" "$LOGS/current"
    cat >"$LOG_DIR/state" <<EOF
START_PID=$$
CHROOT=$CHROOT
IMAGE=$IMAGE
VNC_PORT=$VNC_PORT
PANEL_PORT=$PANEL_PORT
VEHICLE=$VEHICLE
SCREEN=$SCREEN
MOUNTED_IMAGE=0
EOF
    : >"$LOG_DIR/pids"
    event "start: $(features_on)"
    # the effective settings, for problem reports
    for v in "${CONF_VARS[@]}"; do printf '%s=%q  # %s\n' "$v" "${!v}" "${CONF_SRC[$v]}"; done >"$LOG_DIR/config"
    trap cleanup EXIT
    trap 'exit 130' INT TERM HUP
    # keep sudo's timestamp fresh: the cleanup at the end (also after ./tesla stop from another
    # terminal, hours later) must not ask for a password
    ( while sleep 60; do sudo -n -v 2>/dev/null || sudo -n true 2>/dev/null || exit; done ) </dev/null >/dev/null 2>&1 &
    add_pid $! sudo-keepalive

    # leftovers of an earlier run (e.g. a closed terminal) would hold the services' ports
    if [ -n "$(chroot_pids)" ]; then
        info "Stopping processes left over in $CHROOT from an earlier run:"
        stop_chroot_procs
    fi
    local m
    for m in $(mounts_below); do sudo umount "$m" 2>/dev/null || sudo umount -l "$m"; done
    CHECK_ALL=0 CHECK_FAIL=0
    check_ports
    [ "$CHECK_FAIL" -gt 0 ] && [ "$FORCE" = 0 ] && die "ports in use, see above (--force starts anyway)"

    if ! image_mounted >/dev/null; then
        [ -z "$(image_mounted_elsewhere)" ] || die "$IMAGE is mounted at $(image_mounted_elsewhere | head -1) already"
        sudo mkdir -p "$CHROOT"
        if [ "$IMAGE_RAM" = 1 ]; then
            image_ram_mount
        else
            sudo mount -o loop "$IMAGE" "$CHROOT" || die "couldn't mount $IMAGE"
        fi
        sed -i 's/^MOUNTED_IMAGE=.*/MOUNTED_IMAGE=1/' "$LOG_DIR/state"
    fi
    clean_ipc

    if [ "$SCREEN" = native ]; then start_screen_native; else start_screen_vnc; fi

    step "Mounts and touch"
    sudo mkdir -p "$CHROOT/dev/input"
    sudo mount --bind /proc "$CHROOT/proc"
    # /dev/input: a tmpfs (touch is bound in below; the arcade game's virtual input devices get
    # their nodes there); /dev/uinput and /sys/class + /sys/devices (read-only) for the game's
    # input_to_virtual and its device scan. Not all of /sys: it would cover the net_cls cgroup.
    sudo mount -t tmpfs -o mode=755,size=64k tmpfs "$CHROOT/dev/input"
    sudo touch "$CHROOT/dev/uinput" && sudo mount --bind /dev/uinput "$CHROOT/dev/uinput"
    # QtCarVehicle (user tesla, uid 1111) makes the arcade's steering wheel and scroll wheel
    # devices through it when a game starts ("game-scroll-left Unable to open input device")
    sudo setfacl -m u:1111:rw /dev/uinput && echo "ACL=/dev/uinput" >>"$LOG_DIR/state"
    # /sys/dev: libdrm finds a GPU's PCI id through /sys/dev/char/226:N; without it the 2026
    # firmware's Mesa took the kernel driver's name (i915) and found no driver (iris) for it
    for d in class devices dev; do
        sudo mkdir -p "$CHROOT/sys/$d" && sudo mount --bind -o ro /sys/$d "$CHROOT/sys/$d"
    done
    # The chroot's own /dev/shm, not the host's: its shared memory (audio tplug channels, DVSM data
    # values, ...) is shared between processes of different users. In the host's sticky /dev/shm,
    # fs.protected_regular (Linux >= 4.19, not on the car) refuses O_CREAT on a file another user
    # owns ("Cannot open ring buffer shared memory", no sound), and leftovers of earlier runs got
    # stuck. Not sticky, fresh every start.
    sudo mount -t tmpfs -o mode=0777,nosuid,nodev,size=512m tmpfs "$CHROOT/dev/shm"
    for dev in null zero random urandom; do
        sudo touch "$CHROOT/dev/$dev"
        sudo mount --bind "/dev/$dev" "$CHROOT/dev/$dev"
    done
    sudo mount -t cgroup -o net_cls none "$CHROOT/sys/fs/cgroup/net_cls"
    # the chroot's syslog (/dev/log -> /tmp/log) into syslog.log; without it the messages go to
    # the text console (tools/syslog-sink.py)
    python3 tools/syslog-sink.py "$CHROOT/tmp/log" "$LOG_DIR/syslog.log" </dev/null >/dev/null 2>&1 &
    add_pid $! syslog
    # QtCar's touchscreen: tesla-touch turns the X mouse (VNC) or a real touchscreen (native,
    # mapped to the letterboxed UI) into a virtual multitouch device, bound as /dev/input/touch
    local touch_args=(--display "$QT_DISPLAY")
    # (--size: the UI's size, the letterbox inside the panel; the S/X center display is 1200x1920)
    [ -n "${TOUCH_FROM:-}" ] && touch_args=(--from "$TOUCH_FROM" --panel "$PANEL" --size "$SIZE")
    [ -n "${TOUCH_FROM:-}" ] && touch_args+=(--rotate "$ROTATE")
    sudo "$TOUCH_BIN" "${touch_args[@]}" --bind "$CHROOT/dev/input/touch" \
        >"$LOG_DIR/touch.out" 2> >(tee "$LOG_DIR/touch.err" >&2) &
    add_pid $! tesla-touch
    for _ in $(seq 100); do grep -q '^ready' "$LOG_DIR/touch.out" 2>/dev/null && break; sleep 0.1; done
    grep -q '^ready' "$LOG_DIR/touch.out" || die "tesla-touch failed to start, see $LOG_DIR/touch.err"

    [ "$SCREEN" = native ] || start_viewer

    step "Services"
    # A previous Theater selection must not become Chromium's startup page on
    # the next run. QtCar writes a fresh URL when the user selects a source.
    if [ "$PROFILE" = 2026.8.3-models ]; then
        sudo rm -f "$CHROOT/run/chromium/policies/chromium-fullscreen/10-homepage.json"
    fi
    SERVICES=$(echo $SERVICES)
    start_features
    # the profile's base services first (the S/X firmware: its system D-Bus, which QtCar and the
    # services register on)
    local base
    base=$(profile_value "$PROFILE" services)
    [ -n "$base" ] && SERVICES=$(echo $base $(for s in $SERVICES; do case " $base " in *" $s "*) ;; *) echo $s ;; esac; done))
    local args svc wrap
    info "$SERVICES"
    for svc in $SERVICES; do
        args="" wrap=""
        [ "$svc" = qtcar-sim ] && [ -n "$SIM_PORT" ] && args="--udp :$SIM_PORT"
        if [ "$GDB" = "$svc" ]; then
            # Run this one interactively under gdb.
            info "Running $svc on gdbserver port 8888"
            sudo chroot "$CHROOT" gdbserver :8888 /usr/local/bin/qtcar-service "$svc" $args
        else
            case " $STRACE " in *" $svc "*) wrap="strace -f -tt -s 200 -o /tmp/strace-$svc.txt" ;; esac
            # restarted when they exit, like runit does on the car (qtcar-vehicle exits on purpose
            # when the car configuration changes). t= is the time, for ./tesla logs. In their own
            # session (setsid): Ctrl+C in the terminal hit every process at once, and the S/X
            # Chromium browsers crashed (139) when their zygote and D-Bus went with them; the
            # teardown stops them in order instead.
            sudo setsid -w chroot "$CHROOT" /usr/bin/env TESLA_MEDIA_DISPLAY="$QT_DISPLAY" \
                TESLA_MEDIA_XAUTHORITY="${QT_XAUTH:-}" TESLA_MEDIA_SCREEN="$SCREEN" \
                /bin/sh -c 'while :; do $2 /usr/local/bin/qtcar-service "$0" $1
                echo "tesla: $0 exited ($?), restarting in 2 s [t=$(date +%s)]"; sleep 2; done' "$svc" "$args" "$wrap" \
                </dev/null >"$LOG_DIR/$svc.log" 2>&1 &
        fi
        # the others connect to the bus when they start
        if [ "$svc" = dbus ]; then
            for _ in $(seq 50); do sudo test -S "$CHROOT/var/run/dbus/system_bus_socket" && break; sleep 0.1; done
        fi
        [ "$svc" = bsa_server ] && [ "$BLUETOOTH" = 1 ] && start_bluetooth_watch
    done
    sleep 1
    if [ "$PROFILE" = 2026.8.3-models ]; then
        # QtCar only discovers Theater's X window at startup. Wait until the
        # persistent blank Chromium page exists before starting QtCar.
        for _ in $(seq 100); do
            curl -fsS --max-time 1 http://127.0.0.1:9225/json 2>/dev/null | grep -q '"type"' && break
            sleep 0.1
        done
    fi
    start_helpers
    if [ -n "$CLUSTER_SIZE" ]; then
        # QtCarCluster (on the car runit's qtcar-cluster), restarted when it exits
        ( while :; do
            run_startup QtCarCluster </dev/null
            echo "tesla: QtCarCluster exited ($?), restarting in 2 s [t=$(date +%s)]"
            sleep 2
          done ) >"$LOG_DIR/cluster.log" 2>&1 &
        add_pid $! cluster
    fi

    print_summary "Running"
    event "running: $SERVICES"
    # sudo runs commands in its own pty and puts the terminal in raw mode, so a Ctrl+C would only
    # reach QtCar, never this script. Hence: no terminal input for sudo (</dev/null), run it in the
    # background and `wait` (Ctrl+C interrupts that), and treat QtCar's own Ctrl+C exit as "stop".
    touch "$LOG_DIR/qtcar.log"
    local start_line status
    while true; do
        start_line=$(wc -l <"$LOG_DIR/qtcar.log")
        event "qtcar start"
        if [ "$VERBOSE" = 1 ]; then
            run_startup </dev/null > >(tee -a "$LOG_DIR/qtcar.log") 2>&1 &
        else
            run_startup </dev/null >>"$LOG_DIR/qtcar.log" 2>&1 &
        fi
        wait $!
        status=$?
        sleep 0.5   # let tee catch up
        # QtCar restarts itself to apply some settings ("CID UI is restarting reason: ...") and
        # exits with 137 then, same as on Ctrl+C. Only treat 130/137 as "stop" without that.
        # (the 2026 firmware says "Restarting QtCar because of changed DVs: ...")
        if tail -n +$((start_line + 1)) "$LOG_DIR/qtcar.log" | grep -qE 'CID UI is restarting|Restarting QtCar because'; then
            event "qtcar exit $status: $(tail -n +$((start_line + 1)) "$LOG_DIR/qtcar.log" |
                grep -oE '(CID UI is restarting reason|Restarting QtCar because of changed DVs): .*' | head -1 | cut -c1-150)"
        else
            event "qtcar exit $status"
            # 130 = SIGINT; 137 = QtCar kills itself with SIGKILL when it gets SIGINT ("killing app")
            [ "$status" = 130 ] || [ "$status" = 137 ] && break
        fi
        [ "$RESTART" = 1 ] || break
        # QtCar exits on purpose to apply some settings (e.g. on first boot); the car restarts it too
        info "QtCar exited ($status), restarting in 2 s"
        sleep 2 & wait $!
    done
}

# before the services start: which services, the car's settings
start_features() {
    SIM_PORT=""
    set_timezone
    CAN_DB=$(can_db)
    if [ "$VEHICLE" = 1 ]; then
        # the Model 3's firmware has a simulator (QtCarSimService) that tesla-can.py adds to; the
        # S/X 2026 firmware has none: tesla-can.py sends all the car's messages (--all-zero)
        if [ "$CAN_DB" = vehicle/work/can-db.json ]; then add_service qtcar-sim qtcar-vehicle; else add_service qtcar-vehicle; fi
        # the firmware's CAN signal database, for tesla-can.py (once; needs unicorn + pyelftools)
        if [ ! -s "$CAN_DB" ]; then
            info "Extracting the CAN signal database from the firmware (once)..."
            python3 vehicle/extract-can-db.py "$CHROOT" -o "$CAN_DB" >"$LOG_DIR/can-db.log" 2>&1 ||
                warn "failed, see can-db.log (needs: pip install --user unicorn pyelftools)"
        fi
    fi
    if [ "$VEHICLE" = 1 ] && [ "$CAN_DB" = vehicle/work/can-db.json ]; then
        [ -n "$VEHICLE_COLOR" ] && car_opt exteriorColor SIM_exteriorColor "$VEHICLE_COLOR" color
        [ -n "$VEHICLE_WHEELS" ] && car_opt wheelType SIM_wheelType "$VEHICLE_WHEELS" wheels
        [ -n "$VEHICLE_PERFORMANCE" ] && car_opt performancePackage SIM_performancePackage "$VEHICLE_PERFORMANCE" performance
        # with tesla-can.py running, the sim sends to it (:1235) and it forwards to qtcar-vehicle
        # (:1234), with its own signals written into the sim's frames
        [ -s vehicle/work/can-db.json ] && SIM_PORT=1235
    elif [ "$VEHICLE" = 1 ]; then
        # the S/X: tesla-can.py sends the car config (all zero = Default, Base19, Standard), so the
        # settings DB gets the same values even without options (else QtCar restarts once)
        car_opt exteriorColor GTW_exteriorColorCode "${VEHICLE_COLOR:-Default}" color
        car_opt wheelType GTW_wheelType "${VEHICLE_WHEELS:-Base19}" wheels
        car_opt performanceAddOn GTW_performanceAddOn "${VEHICLE_PERFORMANCE:-Standard}" performance
    fi
    if [ "$PROFILE" = 2026.8.3-models ]; then
        # the display state machine (standby when the driver leaves, the dog screen) needs the
        # car's data: without it QtCar kept the display off and ignored touches (the kit's "false")
        settings_conf display_state "enable_display_state_manager=$([ "$VEHICLE" = 1 ] && echo true || echo false)"
    fi
    if [ "$AUDIO" = 1 ]; then
        # AudioWeaver plays to the "model3" amplifier loopback (the Model 3: the standalone AWE
        # through audiod; the 2026 S/X: audiod with AudioWeaver built in, the kit's asound block);
        # "virtual" carries the Chromium, Browser, game and A2DP loopbacks AudioWeaver mixes in
        if ! grep -q '^ *[0-9]* \[model3 *\]' /proc/asound/cards; then
            if grep -q '^ *[0-9]* \[virtual *\]' /proc/asound/cards; then
                warn "snd-aloop is loaded without the model3 card: no car sound (sudo modprobe -r snd-aloop, then start again)"
            else
                sudo modprobe snd-aloop id=model3,virtual enable=1,1 pcm_substreams=4,8 ||
                    warn "couldn't load snd-aloop, no audio"
            fi
        fi
        sudo mkdir -p "$CHROOT/dev/snd" && sudo mount --bind /dev/snd "$CHROOT/dev/snd"
        snd_acl
        if [ "$PROFILE" = 2026.8.3-models ]; then add_service audiod qtcar-audiod qtcar-mediaserver
        else add_service audioweaver audiod qtcar-audiod qtcar-mediaserver; fi
        audio_clock
    fi
    [ "$BLUETOOTH" = 1 ] && start_bluetooth
    [ "$WIFI" = 1 ] && start_wifi
    if [ -n "$CAMERA" ]; then
        # QtCar's backup camera reads V4L2 XR24 (32 bit BGRX) from [bkcam] deviceId. ffmpeg
        # converts $CAMERA (any format, e.g. scrcpy's YU12 on v4l2loopback) into CAMERA_DEV.
        if [ ! -e "$CAMERA_DEV" ]; then
            warn "$CAMERA_DEV doesn't exist, no backup camera"
            CAMERA=""
        else
            sudo chmod 666 "$CAMERA_DEV"      # the chroot's video group has another gid
            sudo touch "$CHROOT$CAMERA_DEV" && sudo mount --bind "$CAMERA_DEV" "$CHROOT$CAMERA_DEV"
            # forceFeedGood: on the car the camera module reports VAPI_backupCameraFeedGood
            settings_conf bkcam deviceId="$CAMERA_DEV" deviceFmt=XR24 textureFmt=bgra \
                width="${CAMERA_SIZE%x*}" height="${CAMERA_SIZE#*x}" forceFeedGood=true
        fi
    fi
    if [ -n "$MUSIC" ]; then
        # a folder as a USB stick: the car's udev rule mounts sticks at /home/tesla/media/usb-<dev>,
        # the media server finds them there ([usb] media_path, kit) and indexes them. The 2026 S/X
        # looks in /mnt/media/usb-* (where its mounterd mounts them; [usb] media_path doesn't apply)
        local usb=/home/tesla/media/usb-music
        [ "$PROFILE" = 2026.8.3-models ] && usb=/mnt/media/usb-music
        sudo mkdir -p "$CHROOT$usb" && sudo mount --bind -o ro "$MUSIC" "$CHROOT$usb"
        add_service qtcar-mediaserver
    fi
    if [ -n "$GPS" ]; then
        add_service qtcar-gpsmanager
        # QtCar shows the car at GpsManager's estimated position by default, and the estimator
        # only follows GPS while the car reports wheel speed. Without vehicle data use raw GPS
        # instead (GUI_smoothGPSUpdates, a data value QtCar keeps in its settings DB).
        local ids
        ids=$(awk -F: '$1=="tesla"{print $3":"$4}' "$CHROOT/etc/passwd")
        # LocationManagerDataStorage: QtCar's last stored position (the S/X navigation routes
        # from it: the route started at an old parked place, not at the car's GPS position)
        sudo python3 vehicle/qtcar-settings.py --owner "$ids" \
            "$CHROOT/home/tesla/.Tesla/data/QtCarSettings.db" GUI_smoothGPSUpdates=false \
            -d LocationManagerDataStorage >/dev/null
    fi
}

# after the services: the host-side helpers
start_helpers() {
    if [ "$PROFILE" = 2026.8.3-models ]; then
        # The offscreen media web view misses native and VNC touchscreen events.
        # Forward them through its localhost DevTools socket. The standalone Browser
        # needs X11 events so its toolbar receives taps as well as page content.
        python3 tools/media-touch.py "$CHROOT" </dev/null >"$LOG_DIR/media-touch.log" 2>&1 &
        add_pid $! media-touch
        python3 tools/browser-touch.py "$CHROOT" --display "$QT_DISPLAY" \
            </dev/null >"$LOG_DIR/browser-touch.log" 2>&1 &
        add_pid $! browser-touch
        python3 tools/browser-touch.py "$CHROOT" --display "$QT_DISPLAY" \
            --window-title Theater --geometry-file theater.touch-Theater-cid.geometry \
            </dev/null >"$LOG_DIR/theater-touch.log" 2>&1 &
        add_pid $! theater-touch
        # QtCar requests Theater through the car's escalator, which is absent in
        # this chroot. Launch only fullscreen Chromium on its start/stop events.
        python3 tools/theater-control.py "$CHROOT" "$LOG_DIR/qtcar.log" \
            </dev/null >"$LOG_DIR/theater-control.log" 2>&1 &
        add_pid $! theater-control
    fi
    if [ "$PROFILE" = 2026.8.3-models ]; then
        # This firmware normally gets CONN_/LINK_ data values from the car's ConnMan. The
        # chroot shares the host network instead; feed its reachability into QtCar's normal
        # data-value path (which also controls media). With --wifi, QtCarConnMan owns these values.
        # GUI_audioReady: QtCar takes it from the car's audio DSP, which doesn't run here; the
        # arcade and the fireplace wait for it ("Audio Initializing"). Their sound is silent.
        # The charge rate on the cluster (mi/hr) is QtCar's GUI_chargeAveragePower, which it doesn't
        # take over from QtCarVehicle here: copied.
        # --cellular: while the PC is online over its modem, the link is presented as the car's LTE.
        local link=()
        if [ "$WIFI" = 1 ]; then
            link=(--no-link)
            [ "$CELLULAR" = 1 ] && link=(--cellular-only)
        elif [ "$CELLULAR" = 1 ]; then
            link=(--cellular)
        fi
        [ "$VEHICLE" = 1 ] && link+=(--copy VAPI_chargeAveragePower=GUI_chargeAveragePower)
        sudo python3 tools/host-connectivity.py "$CHROOT" "${link[@]}" --set GUI_audioReady=variant:boolean:true \
            </dev/null >"$LOG_DIR/host-connectivity.log" 2>&1 &
        add_pid $! host-connectivity
    elif [ "$CELLULAR" = 1 ]; then
        # Model 3: the cellular values through QtCar's HTTP data values, only while the PC is
        # online over its modem (otherwise QtCar keeps its own)
        python3 tools/host-connectivity.py "$CHROOT" --http http://127.0.0.1:4220 --cellular-only \
            </dev/null >"$LOG_DIR/host-connectivity.log" 2>&1 &
        add_pid $! host-connectivity
    fi
    if [ "$VEHICLE" = 1 ] && [ -s "$CAN_DB" ]; then
        local bind=127.0.0.1 car_args=(--sim-port "$SIM_PORT")
        [ "$REMOTE" = 1 ] && bind=0.0.0.0
        [ "$CAN_DB" = vehicle/work/can-db.json ] || car_args=(--db "$CAN_DB" --all-zero)
        python3 vehicle/tesla-can.py --preset parked "${car_args[@]}" --http "$PANEL_PORT" --http-bind "$bind" \
            --log-dir "$LOG_DIR" --respond "${CAR_SIM[@]}" </dev/null >"$LOG_DIR/can.log" 2>&1 &
        add_pid $! tesla-can
    fi
    if [ "$AUDIO" = 1 ]; then
        # QtCar ignores volume changes while SPEECH_voiceRecGUIState is invalid ("Delaying volume
        # change"); the speech recognizer, not running here, would set it. Keep it at Inactive.
        # (Model 3: the S/X QtCar has no HTTP data values, its :4220 is the Spotify client's)
        if [ "$PROFILE" != 2026.8.3-models ]; then
            ( while :; do
                v=$(curl -s --max-time 2 --http1.0 "http://127.0.0.1:4220/_data_get_value_request_?name=SPEECH_voiceRecGUIState")
                [ "$v" = "<invalid>" ] && curl -s --max-time 2 --http1.0 \
                    "http://127.0.0.1:4220/_data_set_value_request_?name=SPEECH_voiceRecGUIState&value=Inactive" >/dev/null
                sleep 5
              done ) </dev/null >/dev/null 2>&1 &
            add_pid $! speech-state
        fi
        # AudioWeaver's 8-channel amplifier output (amp M), mixed to host stereo. The 2026 S/X mixes
        # the Chromium, Browser, game and Bluetooth loopbacks of the "virtual" card into it too and
        # has a second amplifier output (amp S, subdevice 1): both are recorded, channels 1-8 = amp
        # M, 9-16 = amp S. Its speakers (test tones, sandbox; the base audio type): left M4 + S4,
        # right M2 + M6, center M8; the odd channels stay silent.
        local remix=$AUDIO_REMIX
        [ "$PROFILE" = 2026.8.3-models ] && [ "${CONF_SRC[AUDIO_REMIX]}" = default ] &&
            remix="4v0.5,12v0.5,8v0.35 2v0.5,6v0.5,8v0.35"
        amp() {
            arecord -q -D "hw:CARD=model3,DEV=1,SUBDEV=$1" -f S32_LE -c 8 -r 48000 -t raw \
                --period-size=192 --buffer-size=3072
        }
        ( while :; do
            if [ "$PROFILE" = 2026.8.3-models ]; then
                # +18 dB with a limiter: the amplifier outputs are quiet (a half-scale tone at UI volume
                # 38 %: -36 dBFS, 100 %: -6), and with the UI turned up to compensate the media drowned
                # the chimes, which play at fixed levels (lock 0.01, turn signal 0.007; tick -42 dBFS)
                sox -q -M -t raw -e signed -b 32 -c 8 -r 48000 <(amp 0) -t raw -e signed -b 32 -c 8 -r 48000 <(amp 1) \
                    -t raw -e signed -b 16 -c 2 - remix -m $remix gain -l 18
            else
                amp 0 | sox -q -t raw -e signed -b 32 -c 8 -r 48000 - -t raw -e signed -b 16 -c 2 - remix -m $remix
            fi | play_stereo
            echo "tesla: audio pipeline exited, restarting in 2 s [t=$(date +%s)]"
            sleep 2
          done ) </dev/null >"$LOG_DIR/audio.log" 2>&1 &
        add_pid $! audio
    fi
    if [ -n "$CAMERA" ]; then
        local cw=${CAMERA_SIZE%x*} ch=${CAMERA_SIZE#*x}
        # off while an arcade game runs (the car's game-mode.sh stops its dashcam too): with the game
        # and the camera together audiod fell behind (pump errors, audible); without the camera
        # not (A/B in a race on the T480: 0,4,3,3 vs 0,0,0,0 errors per 15 s). Stopped, not paused:
        # after SIGCONT ffmpeg made up for the pause with 1000+ duplicated frames, and that burst
        # caused errors of its own.
        game() { pgrep -x CobaltLinux >/dev/null || pgrep -x mametesla >/dev/null; }
        ( off=0
          while :; do
            if game; then
                [ $off = 0 ] && echo "tesla: camera off while a game runs [t=$(date +%s)]"
                off=1; sleep 1; continue
            fi
            [ $off = 1 ] && echo "tesla: camera on again [t=$(date +%s)]"
            off=0
            # cropped to QtCar's aspect ratio (4:3), not stretched: a 16:9 webcam looked distorted
            ffmpeg -nostdin -loglevel warning -f v4l2 -i "$CAMERA" \
                -vf "crop='min(iw,ih*$cw/$ch)':'min(ih,iw*$ch/$cw)',scale=$cw:$ch,format=bgr0" -f v4l2 "$CAMERA_DEV" &
            ff=$!
            while kill -0 $ff 2>/dev/null; do
                game && { kill $ff; break; }
                sleep 1
            done
            wait $ff 2>/dev/null
            game || sleep 2
          done ) </dev/null >"$LOG_DIR/camera.log" 2>&1 &
        add_pid $! camera
    fi
    # the arcade's game windows: the car has no window manager, a desktop has one (native screen);
    # on Xvfb it only gives MAME the keyboard focus for QtCar's XTest keys
    # (and hides the desktop's mouse cursor, as on the car: with a touchscreen, or CURSOR=off)
    local hide=()
    if [ "$SCREEN" = native ]; then
        case "$CURSOR" in
            off) hide=(--hide-cursor) ;;
            auto) [ -n "${TOUCH_FROM:-}" ] && hide=(--hide-cursor) ;;
        esac
    fi
    DISPLAY=$QT_DISPLAY python3 tools/game-windows.py --tidk "$CHROOT/tmp/games/cobalt/tidk" "${hide[@]}" \
        </dev/null >"$LOG_DIR/game-windows.log" 2>&1 &
    add_pid $! game-windows
    if [ -n "$GPS" ]; then
        local gps_args
        case "$GPS" in -*) read -ra gps_args <<<"$GPS" ;; *) gps_args=(--pos "$GPS") ;; esac
        [ "$VEHICLE" = 1 ] && gps_args+=(--can)
        python3 vehicle/tesla-gps.py -q --rootfs "$CHROOT" "${gps_args[@]}" </dev/null >"$LOG_DIR/gps.log" 2>&1 &
        add_pid $! tesla-gps
    fi
}

# SysV semaphores and shared memory of the chroot's users from an earlier run: they outlive the
# processes (the host's IPC namespace), and a stale one with another group or size blocks the
# next run's QSharedMemory (the 2026 firmware's DVSM_* data values). Their key files in the
# chroot's /tmp (qipc_*) go too.
clean_ipc() {
    local uids me dvshm_gid
    me=$(id -u)
    [ -f "$CHROOT/etc/passwd" ] || image_mounted >/dev/null || return 0
    uids=" $(awk -F: -v me="$me" '$3 >= 1000 && $3 < 65534 && $3 != me {printf "%s ", $3}' "$CHROOT/etc/passwd" 2>/dev/null) "
    # ipcs prints owner *names* when the host knows them, not always numeric UIDs. QtCarStartup
    # also creates DVSM_* as root with the chroot's dvshm group. Read numeric kernel fields so
    # both those and the services' own stale objects are found. Never remove attached segments.
    dvshm_gid=$(awk -F: '$1 == "dvshm" {print $3}' "$CHROOT/etc/group" 2>/dev/null)
    sudo python3 - "$uids" "$dvshm_gid" <<'PY'
import subprocess, sys

uids = {int(x) for x in sys.argv[1].split()}
dvshm_gid = int(sys.argv[2]) if sys.argv[2] else None
for kind, flag, id_field in (("shm", "-m", "shmid"), ("sem", "-s", "semid")):
    with open("/proc/sysvipc/" + kind) as f:
        header = f.readline().split()
        rows = [dict(zip(header, line.split())) for line in f]
    for row in rows:
        if int(row["uid"]) not in uids and (dvshm_gid is None or int(row["gid"]) != dvshm_gid):
            continue
        if kind == "shm" and int(row["nattch"]) != 0:
            continue
        subprocess.run(("ipcrm", flag, row[id_field]), stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
PY
    sudo sh -c "rm -f '$CHROOT'/tmp/qipc_*" 2>/dev/null
}

# /startup.sh in the chroot, with the display QtCar uses (QTCAR_* are read by the kit's startup.sh)
# (QTCAR_BIN, QTCAR_SIZE: the S/X profile's startup.sh; QtCarCluster runs from it too)
run_startup() {
    local bin=${1:-QtCar} display=$QT_DISPLAY gl=${QT_GL:-software} gdb=""
    [ "$GDB" = "QtCar" ] && gdb=1
    # the cluster: Xvfb's second screen (VNC mode) or its own Xvfb next to a native screen, where
    # it draws in software (no GPU on Xvfb)
    [ "$bin" = QtCarCluster ] && display=$CLUSTER_DISPLAY gl=${CLUSTER_GL:-$gl}
    sudo chroot "$CHROOT" /bin/sh -c 'QTCAR_DISPLAY=$1 QTCAR_XAUTHORITY=$2 QTCAR_ARGS=$3 QTCAR_GL=$4 QTCAR_GL_DRIVER=$5
        QTCAR_BIN=$6 QTCAR_SIZE=$7 GDB=$8
        export QTCAR_DISPLAY QTCAR_XAUTHORITY QTCAR_ARGS QTCAR_GL QTCAR_GL_DRIVER QTCAR_BIN QTCAR_SIZE GDB
        exec /bin/sh /startup.sh' \
        _ "$display" "${QT_XAUTH:-/root/.Xauthority}" "$QTCAR_ARGS" "$gl" "${QT_GL_DRIVER:-}" "$bin" "$SIZE" "$gdb"
}

# x11vnc (0.9.17, Fedora) exits in a Wayland session ("only supported via -rawfb") even for an
# Xvfb display; it only serves our Xvfb, so hide the session from it
X11VNC=(env -u WAYLAND_DISPLAY XDG_SESSION_TYPE=x11 x11vnc)

# warn when an x11vnc quit right away (its -quiet log only shows why then): PID LOG
check_vnc() {
    local i
    for i in 1 2 3 4; do kill -0 "$1" 2>/dev/null || break; sleep 0.25; done
    kill -0 "$1" 2>/dev/null || warn "x11vnc quit: $(grep -v '^\s*$' "$2" | tail -1) (${2#"$TESLA_DIR/"})"
}

# the virtual screen (Xvfb :1) and x11vnc; with a cluster (Model S/X) a second screen :1.1 like
# the car's :0.1, on its own VNC port
start_screen_vnc() {
    step "Screen and VNC"
    QT_DISPLAY=$DISPLAY_NUM.0 CLUSTER_DISPLAY=$DISPLAY_NUM.1
    local screens=(-screen 0 "${SIZE}x24")
    [ -n "$CLUSTER_SIZE" ] && screens+=(-screen 1 "${CLUSTER_SIZE}x24")
    local noshm=()
    [ "$(profile_value "$PROFILE" mit_shm)" = false ] && noshm=(-noshm)
    if xdpyinfo -display $DISPLAY_NUM >/dev/null 2>&1; then
        info "X display $DISPLAY_NUM already running, using it"
    else
        start_xvfb Xvfb "$LOG_DIR/xvfb.log" "${screens[@]}" +extension RECORD
    fi
    local vnc_access=(-localhost -nopw)
    [ "$REMOTE" = 1 ] && vnc_access=(-usepw)
    # x11vnc's IPv6 listener can claim port 5900 for the cluster despite its -rfbport 5901.
    # localhost may resolve to ::1, then both viewer ports show the cluster. Disable IPv6
    # completely: -no6 alone can still leave a listener on ::1.
    "${X11VNC[@]}" -display $DISPLAY_NUM.0 -no6 -noipv6 "${vnc_access[@]}" "${noshm[@]}" -rfbport "$VNC_PORT" -forever -shared -noxdamage -quiet \
        >"$LOG_DIR/x11vnc.log" 2>&1 &
    add_pid $! x11vnc
    local vnc_pid=$!
    if [ -n "$CLUSTER_SIZE" ]; then
        "${X11VNC[@]}" -display $DISPLAY_NUM.1 -no6 -noipv6 "${vnc_access[@]}" "${noshm[@]}" -rfbport "$((VNC_PORT + 1))" -forever -shared -noxdamage -viewonly -nocursor \
            -quiet >"$LOG_DIR/x11vnc-cluster.log" 2>&1 &
        add_pid $! x11vnc-cluster
    fi
    check_vnc $vnc_pid "$LOG_DIR/x11vnc.log"
}

# IMAGE_RAM=1: mount a device-mapper snapshot of the image with its copy-on-write store in RAM
# (/dev/shm). On a USB stick the firmware services' small synchronous writes (settings databases,
# the journal) stalled the stick and every read behind them: QtCar didn't even load in minutes.
# The image is also read into the page cache in the background (idle I/O, after 2 min). The teardown merges
# the changes into the image (image_ram_merge in common.sh).
image_ram_mount() {
    local size origin cow
    command -v dmsetup >/dev/null || PATH=$PATH:/sbin:/usr/sbin
    command -v dmsetup >/dev/null || die "IMAGE_RAM needs dmsetup ($(pkg_install dmsetup)), or --no-image-ram"
    sudo modprobe dm-snapshot 2>/dev/null
    image_ram_merge quiet      # leftovers of a killed run: keep their changes
    size=$(stat -c %s "$IMAGE")
    origin=$(sudo losetup -f --show "$IMAGE") || die "couldn't attach $IMAGE"
    sudo truncate -s "$size" /dev/shm/tesla-cow
    cow=$(sudo losetup -f --show /dev/shm/tesla-cow) || die "couldn't attach /dev/shm/tesla-cow"
    sudo dmsetup create "$RAM_DM" --table "0 $((size / 512)) snapshot $origin $cow P 8" ||
        die "couldn't create the snapshot (dm-snapshot module?); --no-image-ram"
    sudo mount "/dev/mapper/$RAM_DM" "$CHROOT" || die "couldn't mount the snapshot of $IMAGE"
    info "image: writes go to RAM, into the image at the end (on a USB drive; --no-image-ram)"
    # later, when QtCar has what it needs: a stick's sequential read (12 MB/s on a USB 2 one)
    # would compete with QtCar's own reads at the start
    ( sleep 120; exec ionice -c3 nice -n19 cat "$IMAGE" ) >/dev/null 2>&1 &
    add_pid $! image-read
}

# the real screen: QtCar's UI (SIZE, 1920x1200) scaled to fit the panel with black bars. The X
# framebuffer becomes SIZE and the output gets a transform (scale + centering offset); xrandr
# complains that the offset output doesn't fit the framebuffer, but applies it.
start_screen_native() {
    step "Screen (native, $NATIVE_DISPLAY)"
    QT_DISPLAY=$NATIVE_DISPLAY
    local out ui_w=${SIZE%x*} ui_h=${SIZE#*x} pw ph logical_w logical_h interim_w interim_h
    local tf name primary geo x y rotation restore others=() off=()
    out=$(native_output)
    NATIVE_OUT=${out%%$'\t'*} PANEL=${out#*$'\t'}
    [ -n "$NATIVE_OUT" ] || die "no connected output${NATIVE_OUTPUT:+ named $NATIVE_OUTPUT} on $NATIVE_DISPLAY (xrandr)"
    # several monitors: QtCar gets one (the primary, or NATIVE_OUTPUT), the others are off while it
    # runs; the whole layout (modes, positions, primary, screen size) comes back at the end
    restore="--fb $(native_screen_size)"
    while IFS=$'\t' read -r name primary geo x y rotation; do
        restore="$restore --output $name --transform none --mode $geo --rotate ${rotation:-normal} --pos ${x}x$y"
        [ "$primary" = 1 ] && restore="$restore --primary"
        if [ "$name" != "$NATIVE_OUT" ]; then others+=("$name"); off+=(--output "$name" --off); fi
    done < <(native_layout)
    echo "NATIVE_RESTORE=$restore" >>"$LOG_DIR/state"
    if [ ${#others[@]} -gt 0 ]; then
        DISPLAY=$NATIVE_DISPLAY xrandr "${off[@]}" --output "$NATIVE_OUT" --pos 0x0 --fb "$PANEL" 2>/dev/null
        info "QtCar on $NATIVE_OUT; off while it runs: ${others[*]}"
    fi
    pw=${PANEL%x*} ph=${PANEL#*x}
    logical_w=$pw logical_h=$ph
    if [ "$ROTATE" = left ] || [ "$ROTATE" = right ]; then
        logical_w=$ph logical_h=$pw
    fi
    echo "NATIVE=$NATIVE_DISPLAY" >>"$LOG_DIR/state"
    # the udev rule (tesla-touch/99-tesla-touch.rules) leaves QtCar's tesla-uinput to this X server
    sudo touch /run/tesla-native
    if [ "$ROTATE" != normal ]; then
        interim_w=$((ui_w > logical_w ? ui_w : logical_w))
        interim_h=$((ui_h > logical_h ? ui_h : logical_h))
        DISPLAY=$NATIVE_DISPLAY xrandr --fb "${interim_w}x${interim_h}" \
            --output "$NATIVE_OUT" --rotate "$ROTATE" --transform none 2>/dev/null ||
            die "could not rotate $NATIVE_OUT $ROTATE (xrandr); the display will be restored"
    fi
    if [ "${logical_w}x${logical_h}" != "$SIZE" ]; then
        tf=$(python3 -c "
uw, uh, pw, ph = $ui_w, $ui_h, $logical_w, $logical_h
s = max(uw / pw, uh / ph)                      # framebuffer pixels per panel pixel
print('%f,0,%f,0,%f,%f,0,0,1' % (s, -(pw - uw / s) / 2 * s, s, -(ph - uh / s) / 2 * s))")
        DISPLAY=$NATIVE_DISPLAY xrandr --output "$NATIVE_OUT" --fb "$SIZE" --transform "$tf" 2>/dev/null
        info "$NATIVE_OUT is $PANEL ($ROTATE): the UI ($SIZE) is scaled to fit, with black bars"
    elif [ "$ROTATE" != normal ]; then
        DISPLAY=$NATIVE_DISPLAY xrandr --fb "$SIZE" 2>/dev/null
    fi
    DISPLAY=$NATIVE_DISPLAY xset s off -dpms 2>/dev/null
    # QtCar (user tesla in the chroot) needs the session's X cookie
    local cookie=$CHROOT/tmp/qtcar-xauth
    sudo rm -f "$cookie"
    xauth -f "${XAUTHORITY:-$HOME/.Xauthority}" nlist "$NATIVE_DISPLAY" 2>/dev/null | sed 's/^..../ffff/' |
        sudo xauth -q -f "$cookie" nmerge - 2>/dev/null
    sudo touch "$cookie" && sudo chmod 644 "$cookie"
    QT_XAUTH=/tmp/qtcar-xauth
    # the touchscreen, passed through by tesla-touch (mouse mode if there is none)
    local ts
    if [ "$TOUCH_DEVICE" = auto ]; then ts=$(find_touchscreen); else ts=$TOUCH_DEVICE; fi
    TOUCH_FROM=${ts%%$'\t'*}
    if [ -n "$TOUCH_FROM" ]; then info "touchscreen: ${ts//$'\t'/ }"
    else info "no touchscreen found: the mouse works as one finger"; fi
    native_gpu
    [ -n "$CLUSTER_SIZE" ] && start_cluster_screen
}

# start_xvfb NAME LOG ARGS...: Xvfb on $DISPLAY_NUM, in a session of its own (Ctrl+C in the terminal
# would kill it with the rest, and Chromium and QtCar crashed without their X screen before the
# teardown stopped them). The profile's mit_shm (profile.toml):
#   true   MIT-SHM on (the Model 3: QtCar runs as one user)
#   false  MIT-SHM off (Mesa's software path then sends frames with PutImage; x11vnc needs -noshm)
#   root   MIT-SHM on, Xvfb as root: the S/X clients run as several users (QtCar 1111, the games,
#          Chromium) and create their segments 0600; only root may attach them. Without MIT-SHM the
#          built-in games' frames came out interlaced (only their top half, stretched)
start_xvfb() {
    local name=$1 log=$2 shm pid; shift 2
    shm=$(profile_value "$PROFILE" mit_shm)
    [ "$shm" = false ] && set -- "$@" -extension MIT-SHM
    if [ "$shm" = root ]; then
        # the redirections inside the root shell: sudo (use_pty) passes the output through a pipe
        # that closes when sudo exits, and Xvfb's first xkbcomp warning then killed it (SIGPIPE)
        sudo rm -f "$LOG_DIR/$name.pid"
        : >"$log"
        sudo setsid -f sh -c 'echo $$ >"$1"; log=$2; shift 2; exec "$@" </dev/null >>"$log" 2>&1' \
            _ "$LOG_DIR/$name.pid" "$log" Xvfb $DISPLAY_NUM "$@" -nolisten tcp </dev/null >/dev/null 2>&1
        for _ in $(seq 30); do pid=$(sudo cat "$LOG_DIR/$name.pid" 2>/dev/null) && [ -n "$pid" ] && break; sleep 0.1; done
        add_pid "$pid" "$name"
    else
        setsid Xvfb $DISPLAY_NUM "$@" -nolisten tcp 2>"$log" &
        add_pid $! "$name"
    fi
    for _ in $(seq 50); do xdpyinfo -display $DISPLAY_NUM >/dev/null 2>&1 && return; sleep 0.1; done
    die "Xvfb didn't start on $DISPLAY_NUM, see $log"
}

# natively: the cluster (Model S/X) on a virtual screen of its own, over VNC (VNC_PORT+1)
start_cluster_screen() {
    local noshm=()
    [ "$(profile_value "$PROFILE" mit_shm)" = false ] && noshm=(-noshm)
    start_xvfb Xvfb-cluster "$LOG_DIR/xvfb-cluster.log" -screen 0 "${CLUSTER_SIZE}x24"
    local vnc_access=(-localhost -nopw)
    [ "$REMOTE" = 1 ] && vnc_access=(-usepw)
    "${X11VNC[@]}" -display $DISPLAY_NUM.0 -no6 -noipv6 "${vnc_access[@]}" "${noshm[@]}" -rfbport "$((VNC_PORT + 1))" -forever -shared -noxdamage -viewonly -nocursor \
        -quiet >"$LOG_DIR/x11vnc-cluster.log" 2>&1 &
    add_pid $! x11vnc-cluster
    check_vnc $! "$LOG_DIR/x11vnc-cluster.log"
    CLUSTER_DISPLAY=$DISPLAY_NUM.0 CLUSTER_GL=software
    info "cluster: VNC port $((VNC_PORT + 1)) (localhost$( [ "$REMOTE" = 1 ] && echo ", password"))"
}

# OpenGL on the GPU (GPU=auto|on|off): the kit has Mesa 18's i965 for Intel GPUs. QtCar (user
# tesla, uid 1111 in the chroot) and the arcade games (cobalt 1226, mame 1980; in software they
# run at a few frames a second and miss short taps) get /dev/dri and access to its nodes; removed
# again at the end.
native_gpu() {
    QT_GL=software QT_GL_DRIVER=""
    [ "$GPU" = off ] && { info "OpenGL: software (--no-gpu)"; return; }
    local card vendor driver name
    # the first Intel or AMD GPU (a PC can have several, e.g. an NVIDIA card next to it)
    for card in /sys/class/drm/renderD*; do
        vendor=$(cat "$card/device/vendor" 2>/dev/null)
        case "$vendor" in 0x8086|0x1002) break ;; esac
    done
    case "$vendor" in
        0x8086) driver=i965 name=Intel ;;
        0x1002)
            # AMD, Mesa 18.0.5's Gallium drivers (links to the kit's megadriver): radeonsi for GCN
            # up to Vega/Raven (not RDNA), r600 for the older TeraScale GPUs (HD 2000-6000).
            # Never tested: only when asked for (--gpu)
            driver=$(amd_driver "$card")
            if [ "$GPU" != on ]; then
                info "OpenGL: software (AMD GPU: --gpu tries $driver, untested)"
                return
            fi
            name=AMD ;;
        *)
            info "OpenGL: software (GPU ${vendor:-none}: the kit's Mesa has drivers for Intel and AMD only)"
            return ;;
    esac
    sudo mkdir -p "$CHROOT/dev/dri" && sudo mount --bind /dev/dri "$CHROOT/dev/dri" || return
    local f
    for f in /dev/dri/card* /dev/dri/renderD*; do
        sudo setfacl -m u:1111:rw,u:1226:rw,u:1980:rw "$f" && echo "ACL=$f" >>"$LOG_DIR/state"
    done
    QT_GL=hardware QT_GL_DRIVER=$driver
    info "OpenGL: GPU ($name, $driver)"
}

# amd_driver RENDER_NODE_SYSFS: radeonsi or r600. amdgpu (kernel) runs GCN and newer only; radeon
# runs TeraScale and the first two GCN generations (SI, CIK), told apart by PCI id.
amd_driver() {
    local dev kdrv
    kdrv=$(basename "$(readlink -f "$1/device/driver")")
    dev=$(( $(cat "$1/device/device" 2>/dev/null || echo 0) ))
    if [ "$kdrv" = radeon ]; then
        # SI: Tahiti, Pitcairn, Cape Verde, Oland, Hainan; CIK: Bonaire, Hawaii, Kaveri, Kabini, Mullins
        if (( (dev >= 0x6780 && dev <= 0x679f) || (dev >= 0x6800 && dev <= 0x683f) ||
              (dev >= 0x6600 && dev <= 0x666f) || (dev >= 0x67a0 && dev <= 0x67bf) ||
              (dev >= 0x1304 && dev <= 0x131d) || (dev >= 0x9830 && dev <= 0x983f) ||
              (dev >= 0x9850 && dev <= 0x985f) )); then
            echo radeonsi
        else
            echo r600
        fi
    else
        echo radeonsi
    fi
}

start_viewer() {
    if [ -z "$VIEWER" ]; then
        for v in krdc remote-viewer remmina vncviewer; do command -v $v >/dev/null && VIEWER=$v && break; done
    fi
    VIEWER_USED=${VIEWER:-}
    case "${VIEWER:-none}" in
        none) VIEWER_USED="" ;;
        vncviewer) vncviewer "127.0.0.1::$VNC_PORT" >/dev/null 2>&1 & add_pid $! viewer ;;
        remmina) remmina -c "vnc://127.0.0.1:$VNC_PORT" >/dev/null 2>&1 & add_pid $! viewer ;;
        *) $VIEWER "vnc://127.0.0.1:$VNC_PORT" >/dev/null 2>&1 & add_pid $! viewer ;;
    esac

}

# 16 bit stereo 48 kHz from stdin to the PC's speakers: PipeWire, PulseAudio or plain ALSA
play_stereo() {
    # newer pw-play (PipeWire 1.6, Fedora 44) reads stdin as a sound file unless told --raw
    # to the default output: WirePlumber remembers an output per program and sent pw-play to
    # an earlier one (an analog jack with nothing plugged in) while the default was a headset
    local raw=() sink target=()
    pw-play --help 2>&1 | grep -q -- '--raw' && raw=(--raw)
    sink=$(pactl get-default-sink 2>/dev/null)
    if command -v pw-play >/dev/null; then
        [ -n "$sink" ] && target=(--target "$sink")
        pw-play "${raw[@]}" "${target[@]}" --format s16 --channels 2 --rate 48000 -
    elif command -v pacat >/dev/null; then
        [ -n "$sink" ] && target=(--device="$sink")
        pacat --playback "${target[@]}" --format=s16le --channels=2 --rate=48000 --latency-msec=60
    else aplay -q -t raw -f S16_LE -c 2 -r 48000 -
    fi
}

# The firmware's users (Chromium, MAME, the media server, ...) reach the sound cards through the
# chroot's group "audio" (gid 29, as Debian's). Another distribution's audio group has another gid
# (Fedora: 63): then only root's audiod could open them, and Chromium's web media failed ("Cannot
# get card index for virtual", no TuneIn sound). The loopback cards' nodes get an ACL for the
# chroot's gid while it runs (removed by the teardown).
snd_acl() {
    local gid host n f
    gid=$(awk -F: '$1 == "audio" {print $3}' "$CHROOT/etc/group" 2>/dev/null)
    host=$(stat -c %g /dev/snd/timer 2>/dev/null)
    [ -n "$gid" ] && [ "$gid" != "$host" ] || return 0
    for f in /dev/snd/timer $(awk '$2 == "[model3" || $2 == "[virtual" {
                                      printf "/dev/snd/controlC%d /dev/snd/pcmC%dD*\n", $1, $1}' /proc/asound/cards); do
        [ -e "$f" ] && sudo setfacl -m "g:$gid:rw" "$f" && echo "SND_ACL=$gid $f" >>"$LOG_DIR/state"
    done
}

# On kernels with a coarse tick (Debian: 250 Hz) snd-aloop moves in 4 ms jiffies steps and
# audiod's 4 ms periods underrun all the time (hundreds of "Pump error"s a minute, audio mostly
# silent). Then the model3 card gets a sound timer instead: snd-dummy's PCM, which runs on a
# high-resolution timer, playing with exactly audiod's period (192 frames). snd-aloop requires
# every stream of the card to use that period (the host's arecord does).
audio_clock() {
    local res card
    res=$(sed -n 's/^G0:.*: *\([0-9.]*\)us.*/\1/p' /proc/asound/timers 2>/dev/null | cut -d. -f1)
    [ -n "$res" ] && [ "$res" -gt 1000 ] || return 0
    card=$(awk '$2 == "[model3" {print $1}' /proc/asound/cards)
    [ -n "$card" ] || return 0
    grep -q '^ *[0-9]* \[hrclock' /proc/asound/cards ||
        sudo modprobe snd-dummy hrtimer=1 pcm_devs=1 pcm_substreams=1 id=hrclock ||
        { warn "couldn't load snd-dummy: audio will stutter (system timer ${res} us)"; return 0; }
    ( while :; do
        sudo aplay -q -D hw:CARD=hrclock,DEV=0 -f S16_LE -c 2 -r 48000 --period-size=192 --buffer-size=768 \
            -t raw /dev/zero
        sleep 1
      done ) </dev/null >"$LOG_DIR/audio-clock.log" 2>&1 &
    add_pid $! audio-clock
    echo hrclock | sudo tee "/proc/asound/card$card/timer_source" >/dev/null
    echo "TIMER_CARD=$card" >>"$LOG_DIR/state"
    info "audio: kernel tick ${res} us, the model3 card runs on a high-resolution clock (snd-dummy)"
}

# Bluetooth: the firmware's Broadcom stack (bsa_server) on this PC's adapter, through
# bluetooth/hci-bridge.py (HCI user channel <-> a pseudo terminal bound as the chroot's ttyS0).
# Wi-Fi: the car's stack (wpa_supplicant, connmand, QtCarConnMan; qtcar-service) on this PC's
# adapter; QtCar's Wi-Fi settings then connect the PC. NetworkManager lets go of the adapter while
# it runs; it's renamed to wlan0, the only name QtCarConnMan knows ("invalid wifi interface").
# restore_wifi (common.sh) undoes both.
start_wifi() {
    local dev=$WIFI_ADAPTER
    [ "$dev" = auto ] && dev=$(find_wifi)
    if [ -z "$dev" ] || [ ! -d "/sys/class/net/$dev/wireless" ]; then
        warn "no Wi-Fi adapter${dev:+ $dev}, no Wi-Fi"; WIFI=0; return
    fi
    if [ "$dev" != wlan0 ] && [ -e /sys/class/net/wlan0 ]; then
        warn "another interface is called wlan0 already, no Wi-Fi"; WIFI=0; return
    fi
    if command -v nmcli >/dev/null && nmcli -t -f RUNNING general 2>/dev/null | grep -q running; then
        sudo nmcli device set "$dev" managed no && echo "WIFI_NM=$dev" >>"$LOG_DIR/state"
    fi
    sudo rfkill unblock wifi 2>/dev/null
    sudo ip link set "$dev" down
    if [ "$dev" != wlan0 ]; then
        sudo ip link set "$dev" name wlan0 || { warn "couldn't rename $dev to wlan0, no Wi-Fi"; WIFI=0; return; }
        echo "WIFI_RENAME=$dev" >>"$LOG_DIR/state"
    fi
    info "wifi: $dev is the car's (as wlan0) while it runs; connect in QtCar's Wi-Fi settings"
    add_service dbus wpa_supplicant connman qtcar-connman
}

start_bluetooth() {
    local dev=${BT_ADAPTER#hci} tty
    [ -d "/sys/class/bluetooth/hci$dev" ] || { warn "no Bluetooth adapter hci$dev, no Bluetooth"; BLUETOOTH=0; return; }
    if systemctl is-active -q bluetooth 2>/dev/null; then
        sudo systemctl stop bluetooth && echo "BT_RESTORE=hci$dev" >>"$LOG_DIR/state"
        info "bluetooth: BlueZ stopped while it runs (the adapter belongs to the car's stack)"
    fi
    # the adapter's USB port, to power-cycle it if it hangs (see restore_bluetooth)
    local port
    port=$(readlink -f "/sys/class/bluetooth/hci$dev/device/../port" 2>/dev/null)
    [ -e "$port/disable" ] && echo "BT_USBPORT=$port" >>"$LOG_DIR/state"
    local bridge_args=()
    [ "${BT_TRACE:-0}" = 1 ] && bridge_args+=(-v)
    # The 2026 LGIT/BSA stack expects successful Broadcom vendor setup before it
    # permits inquiry. A non-Broadcom adapter cannot run those commands; answer
    # them at the serial bridge while leaving the adapter's standard HCI intact.
    # Keep an override for diagnosing other adapters or stack revisions.
    local emulate_vendor=${BT_EMULATE_VENDOR_SUCCESS:-auto}
    if [ "$emulate_vendor" = auto ]; then
        [ "$PROFILE" = 2026.8.3-models ] && emulate_vendor=1 || emulate_vendor=0
    fi
    [ "$emulate_vendor" = 1 ] && bridge_args+=(--emulate-vendor-success)
    sudo python3 bluetooth/hci-bridge.py --dev "$dev" --link "$LOG_DIR/bt-tty" "${bridge_args[@]}" >"$LOG_DIR/bt-bridge.log" 2>&1 &
    add_pid $! bt-bridge
    for _ in $(seq 50); do grep -q '^ready' "$LOG_DIR/bt-bridge.log" 2>/dev/null && break; sleep 0.1; done
    tty=$(sed -n 's/^ready: \([^ ]*\).*/\1/p' "$LOG_DIR/bt-bridge.log")
    [ -n "$tty" ] || { warn "the Bluetooth bridge didn't start, see bt-bridge.log"; BLUETOOTH=0; return; }
    sudo touch "$CHROOT/dev/ttyS0" && sudo mount --bind "$tty" "$CHROOT/dev/ttyS0"
    add_service dbus bsa_server btd qtcar-bluetooth
    [ "$AUDIO" = 1 ] && [ "$PROFILE" != 2026.8.3-models ] && add_service a2dpbridge
}

# The BSA server (both firmwares) can terminate itself after a controller reset and the
# service loop restarts it. btd retains its old BSA connection and then returns
# "Status is invalid" for every inquiry until it too is restarted (a Model 3 on Debian:
# Bluetooth stayed at "Searching", GitHub issue #2). Watch from the first BSA launch,
# before btd starts, so an early restart is not missed.
start_bluetooth_watch() {
    ( local seen="" now btd_pid
      echo "watching bsa_server: btd gets restarted when it restarts"
      while :; do
          grep -q ' stop$' "$LOG_DIR/events.log" 2>/dev/null && break
          now=$(pidof -s bsa_server 2>/dev/null)
          if [ -n "$now" ]; then
              if [ -n "$seen" ] && [ "$now" != "$seen" ]; then
                  echo "BSA restarted ($seen -> $now); reconnecting btd"
                  # The replacement BSA creates its command socket shortly
                  # after exec. Give it time before btd's service loop retries.
                  sleep 1
                  if [ "$(pidof -s bsa_server 2>/dev/null)" = "$now" ]; then
                      btd_pid=$(pidof -s btd 2>/dev/null)
                      [ -z "$btd_pid" ] || sudo -n kill -TERM "$btd_pid"
                  fi
              fi
              seen=$now
          fi
          sleep 1
      done ) </dev/null >"$LOG_DIR/bt-watch.log" 2>&1 &
    add_pid $! bt-watch
}
