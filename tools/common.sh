# Shared by the ./tesla commands (sourced; the working directory is the repo).

TESLA_DIR=$PWD
LOGS=$TESLA_DIR/logs
DISPLAY_NUM=:1
TOUCH_BIN=$TESLA_DIR/tesla-touch/target/release/tesla-touch
KIT=$TESLA_DIR/mcu2-patchkit/mcu2_patch.py
# ports the firmware services listen on (TCP data value servers, UDP CAN/GPS/control)
SERVICE_TCP_PORTS="4220 4160 4030 4190 4060 8002"
SERVICE_UDP_PORTS="1234 1235 4321 20100 20101 20180 63277"

if [ -t 1 ]; then
    B=$'\e[1m' DIM=$'\e[2m' RED=$'\e[31m' GRN=$'\e[32m' YEL=$'\e[33m' CYN=$'\e[36m' R0=$'\e[0m'
else
    B="" DIM="" RED="" GRN="" YEL="" CYN="" R0=""
fi
say()  { printf '%s\n' "$*"; }
step() { printf '%s==>%s %s\n' "$B$CYN" "$R0$B" "$*$R0"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    %sWARNING:%s %s\n' "$YEL" "$R0" "$*"; }
die()  { printf '%sError:%s %s\n' "$RED" "$R0" "$*" >&2; exit 1; }

# --- Packages ---------------------------------------------------------------------------------
# pkg_install DEBIAN-PACKAGE...: the command that installs them on this distribution (Debian and
# Ubuntu: apt; Fedora: dnf with its names; others: the Debian names as a hint)
pkg_install() {
    local id="" p out=()
    id=$( . /etc/os-release 2>/dev/null; echo " ${ID:-} ${ID_LIKE:-} ")
    case "$id" in
        *" fedora "*|*" rhel "*|*" centos "*)
            for p in "$@"; do
                case "$p" in
                    x11-xserver-utils) out+=(xrandr) ;;
                    xauth) out+=(xorg-x11-xauth) ;;
                    x11-utils) out+=(xdpyinfo) ;;
                    xvfb) out+=(xorg-x11-server-Xvfb) ;;
                    pipewire-bin) out+=(pipewire-utils) ;;
                    dmsetup) out+=(device-mapper) ;;
                    linux-modules-extra-*) out+=(kernel-modules-extra) ;;
                    v4l2loopback-dkms) out+=(akmod-v4l2loopback) ;;   # RPM Fusion
                    ffmpeg) out+=(ffmpeg-free) ;;
                    imagemagick) out+=(ImageMagick) ;;
                    *) out+=("$p") ;;
                esac
            done
            echo "sudo dnf install ${out[*]}" ;;
        *" debian "*|*" ubuntu "*) echo "sudo apt install $*" ;;
        *) echo "install $* (Debian package names)" ;;
    esac
}

# --- Configuration ---------------------------------------------------------------------------
# defaults < tesla.conf < environment < command line. CONF_SRC[var] says where a value came from.
CONF_VARS=(VEHICLE VEHICLE_COLOR VEHICLE_WHEELS VEHICLE_PERFORMANCE GPS AUDIO AUDIO_REMIX MUSIC
           CAMERA CAMERA_DEV CAMERA_SIZE NAV SERVICES SIZE VIEWER REMOTE VNC_PORT PANEL_PORT RESTART
           IMAGE IMAGE_RAM CHROOT DUMP SANDBOX_ROOT SCREEN NATIVE_DISPLAY NATIVE_OUTPUT ROTATE TOUCH_DEVICE QTCAR_ARGS GPU CURSOR BLUETOOTH BT_ADAPTER WIFI WIFI_ADAPTER TIMEZONE)
PATH_VARS=" MUSIC IMAGE CHROOT DUMP SANDBOX_ROOT "    # relative paths: to the caller's directory (env, command line)
declare -A CONF_SRC

set_defaults() {
    VEHICLE=0 VEHICLE_COLOR="" VEHICLE_WHEELS="" VEHICLE_PERFORMANCE="" GPS=""
    AUDIO=0 AUDIO_REMIX="1v0.5,4v0.5,5v0.5,7v0.5 1v0.5,2v0.5,6v0.5,8v0.5"
    MUSIC="" CAMERA="" CAMERA_DEV=/dev/video32 CAMERA_SIZE=1280x960 NAV=1 SERVICES=""
    SIZE=1920x1200 VIEWER="" REMOTE=0 VNC_PORT=5900 PANEL_PORT=8099 RESTART=1
    IMAGE=./mcu2.ext4 IMAGE_RAM=auto CHROOT=./chroot DUMP=./mcu2-original SANDBOX_ROOT=./sandbox/rootfs
    SCREEN=vnc NATIVE_DISPLAY=:0 NATIVE_OUTPUT=auto ROTATE=normal TOUCH_DEVICE=auto QTCAR_ARGS="" GPU=auto CURSOR=auto BLUETOOTH=0 BT_ADAPTER=hci0
    WIFI=0 WIFI_ADAPTER=auto TIMEZONE=auto
}

# abspath <path> <base dir>: ~ and relative paths resolved against the base dir
abspath() {
    local p=$1
    case "$p" in "~") p=$HOME ;; "~/"*) p=$HOME/${p#"~/"} ;; esac
    case "$p" in ""|/*) ;; *) p=$2/$p ;; esac
    [ -n "$p" ] && p=$(realpath -m "$p")
    printf '%s' "$p"
}

# load_config [FILE]: CONF_FILE is tesla.conf unless given; created from the example if missing
load_config() {
    local v line
    local -A env_val
    for v in "${CONF_VARS[@]}"; do
        [ -n "${!v+x}" ] && env_val[$v]=${!v}
    done
    set_defaults
    for v in "${CONF_VARS[@]}"; do CONF_SRC[$v]=default; done
    CONF_FILE=${1:-$TESLA_DIR/tesla.conf}
    if [ "${CREATE_CONF:-0}" = 1 ] && [ ! -e "$CONF_FILE" ] && [ "$CONF_FILE" = "$TESLA_DIR/tesla.conf" ] &&
        [ -f tesla.conf.example ]; then
        cp tesla.conf.example "$CONF_FILE" && CONF_CREATED=1
    fi
    if [ -f "$CONF_FILE" ]; then
        local -A before
        for v in "${CONF_VARS[@]}"; do before[$v]=${!v}; done
        # shellcheck disable=SC1090
        . "$CONF_FILE" || die "error in $CONF_FILE"
        for v in "${CONF_VARS[@]}"; do [ "${!v}" != "${before[$v]}" ] && CONF_SRC[$v]=${CONF_FILE##*/}; done
        # typos: assignments to names nobody reads
        CONF_UNKNOWN=""
        while read -r line; do
            case " ${CONF_VARS[*]} " in *" $line "*) ;; *) CONF_UNKNOWN="$CONF_UNKNOWN $line" ;; esac
        done < <(sed -n 's/^[[:space:]]*\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$CONF_FILE" | sort -u)
    fi
    for v in "${!env_val[@]}"; do
        printf -v "$v" '%s' "${env_val[$v]}"
        CONF_SRC[$v]=environment
    done
    for v in IMAGE CHROOT DUMP SANDBOX_ROOT; do
        case "${CONF_SRC[$v]}" in environment) ;; *) printf -v "$v" '%s' "$(abspath "${!v}" "$TESLA_DIR")" ;; esac
    done
    # until 2026-09-25 the default image was mcu3-new.ext4 (the unit was called MCU3 by mistake)
    if [ "${CONF_SRC[IMAGE]}" = default ] && [ ! -e "$IMAGE" ] && [ -e "$TESLA_DIR/mcu3-new.ext4" ]; then
        IMAGE=$TESLA_DIR/mcu3-new.ext4
    fi
    # the dump's folder was called mcu3-original until 2026-09-25
    if [ "${CONF_SRC[DUMP]}" = default ] && [ ! -d "$DUMP" ] && [ -d "$TESLA_DIR/mcu3-original" ]; then
        DUMP=$TESLA_DIR/mcu3-original
    fi
    for v in $PATH_VARS; do
        [ "${CONF_SRC[$v]}" = environment ] && printf -v "$v" '%s' "$(abspath "${!v}" "$CALLER_PWD")"
    done
    [ "${CONF_SRC[MUSIC]}" = "${CONF_FILE##*/}" ] && MUSIC=$(abspath "$MUSIC" "$TESLA_DIR")
    return 0
}

# set_opt VAR VALUE: a command line option
set_opt() {
    local v=$1 val=$2
    case "$PATH_VARS" in *" $v "*) val=$(abspath "$val" "$CALLER_PWD") ;; esac
    printf -v "$v" '%s' "$val"
    CONF_SRC[$v]="command line"
}

# sudo_auth: make sure sudo works now. With NOPASSWD (e.g. a live system) `sudo -v` can still ask
# for a password (it checks all of the user's rules), so try without one first.
sudo_auth() {
    sudo -n true 2>/dev/null || sudo -v
}

# --- The running instance --------------------------------------------------------------------
# logs/current -> the log folder of the running `./tesla start`, which has:
#   state   START_PID, CHROOT, IMAGE, MOUNTED_IMAGE, ... (shell assignments)
#   pids    "pid name" of the host-side helpers it started
#   events.log   what happened when (start, QtCar restarts, stop)
# logs/latest -> the last run's folder, running or not.

# running_pid: the pid of a running `./tesla start`, or nothing
running_pid() {
    [ -f "$LOGS/current/state" ] || return 1
    local START_PID
    START_PID=$(sed -n 's/^START_PID=//p' "$LOGS/current/state")
    [ -n "$START_PID" ] && [ -d "/proc/$START_PID" ] &&
        tr '\0' ' ' <"/proc/$START_PID/cmdline" 2>/dev/null | grep -q 'tesla\|start_all' || return 1
    echo "$START_PID"
}

# chroot_pids: pids of processes whose root is $CHROOT (needs sudo: they belong to other users)
chroot_pids() {
    sudo sh -c 'for p in /proc/[0-9]*; do [ "$(readlink "$p/root" 2>/dev/null)" = "$1" ] && echo "${p#/proc/}"; done' _ "$CHROOT"
}

# stop_chroot_procs [quiet]: SIGTERM everything whose root is the chroot (QtCar, services,
# escalator, tesla-cef, ...), SIGKILL whatever is still there after 3 s. Some services hang on
# SIGTERM (a QtCarGpsManager once survived for hours, kept ports 4160/63277 and the next runs'
# GpsManager couldn't start). Prints the names of what it stopped unless quiet.
stop_chroot_procs() {
    sudo sh -c '
        list() { for p in /proc/[0-9]*; do [ "$(readlink "$p/root" 2>/dev/null)" = "$1" ] && echo "${p#/proc/}"; done; }
        pids=$(list "$1"); [ -n "$pids" ] || exit 0
        [ -n "$2" ] || for p in $pids; do tr "\0" " " 2>/dev/null <"/proc/$p/cmdline" | cut -c1-70; echo; done |
            sort -u | grep . | sed "s/^/      /"
        # first the restart loops of the services (start.sh: sh -c "while :; do ... qtcar-service"):
        # else they start a service again during the stop, and a Chromium that is just starting
        # ignores SIGTERM
        loops=$(for p in $pids; do tr "\0" " " 2>/dev/null <"/proc/$p/cmdline" |
            grep -q "^/bin/sh -c while :; do" && echo "$p"; done)
        [ -z "$loops" ] || kill $loops 2>/dev/null
        # then the S/X Chromium browsers (not their --type= children): they exit cleanly on
        # SIGTERM, but crashed (139) when their session D-Bus or zygote went at the same time
        browsers=$(for p in $pids; do tr "\0" " " 2>/dev/null <"/proc/$p/cmdline" | grep -q "tesla-chromium-main " &&
            ! tr "\0" " " 2>/dev/null <"/proc/$p/cmdline" | grep -q -- "--type=" && echo "$p"; done)
        if [ -n "$browsers" ]; then
            kill $browsers 2>/dev/null
            for _ in 1 2 3 4 5 6; do
                sleep 0.5; alive=0
                for p in $browsers; do kill -0 "$p" 2>/dev/null && alive=1; done
                [ "$alive" = 0 ] && break
            done
            pids=$(list "$1")
        fi
        kill $pids 2>/dev/null
        for _ in 1 2 3 4 5 6; do sleep 0.5; [ -n "$(list "$1")" ] || exit 0; done
        pids=$(list "$1"); echo "      still running after SIGTERM, killed:"
        for p in $pids; do printf "        %s " "$p"; tr "\0" " " 2>/dev/null <"/proc/$p/cmdline" | cut -c1-80; echo; done
        kill -9 $pids 2>/dev/null' _ "$CHROOT" "${1:-}"
}

# mounts_below: mount points inside $CHROOT, deepest first
mounts_below() {
    awk -v r="$CHROOT/" 'index($2, r) == 1 {print $2}' /proc/mounts | sed 's/\\040/ /g' | sort -r
}

# image_mounted: is something mounted at $CHROOT (and prints the source)
image_mounted() {
    awk -v r="$CHROOT" '$2 == r {print $1; f=1} END {exit !f}' /proc/mounts
}

# loop_devs_of FILE: the loop devices backed by FILE, compared by inode (losetup -j compares paths
# unless it may open /dev/loopN: the stick's bind-mounted /home gives the image a second path)
loop_devs_of() {
    local id f b
    id=$(stat -L -c %d:%i "$1" 2>/dev/null) || return 0
    for f in /sys/block/loop*/loop/backing_file; do
        [ -r "$f" ] || continue
        b=$(cat "$f"); b=${b% (deleted)}
        [ "$(stat -L -c %d:%i "$b" 2>/dev/null)" = "$id" ] || continue
        f=${f#/sys/block/}; echo "/dev/${f%%/*}"
    done
}

# on_usb FILE: is FILE on a USB drive (a stick: slow, small synchronous writes stall it)
on_usb() {
    local dev
    dev=$(df --output=source "$1" 2>/dev/null | tail -1)
    [ -b "$dev" ] || return 1
    [ "$(lsblk -dno TRAN "/dev/$(lsblk -no PKNAME "$dev" | head -1)" 2>/dev/null)" = usb ]
}

# IMAGE_RAM: the chroot runs on a device-mapper snapshot of the image whose copy-on-write store
# is in RAM (/dev/shm/tesla-cow); image_ram_merge writes the changes into the image at the end
# (tools/cow-apply.py: dm's own snapshot-merge flushes after every chunk, minutes on a stick).
# Power lost before that: the run's changes are gone, the image is intact.
RAM_DM=tesla-image
image_ram_merge() {
    local quiet=${1:-} out
    if sudo dmsetup info "$RAM_DM" >/dev/null 2>&1; then
        if image_mounted >/dev/null; then
            warn "the image is still mounted; its changes stay in RAM ($RAM_DM)"
            return 1
        fi
        sudo dmsetup remove "$RAM_DM" 2>/dev/null || sudo dmsetup remove --retry "$RAM_DM" ||
            { warn "couldn't remove $RAM_DM; the changes stay in RAM"; return 1; }
    fi
    if [ -f /dev/shm/tesla-cow ]; then
        [ -n "$quiet" ] || info "writing the run's changes (in RAM) into the image..."
        if out=$(sudo python3 "$TESLA_DIR/tools/cow-apply.py" /dev/shm/tesla-cow "$IMAGE" 2>&1); then
            [ -n "$quiet" ] || info "  $out"
        else
            warn "$out (the changes are still in /dev/shm/tesla-cow)"
            return 1
        fi
    fi
    # the snapshot's loop devices; a loop device of the image that isn't mounted is one of ours
    # (mount -o loop detaches its own)
    local lo
    for lo in $(loop_devs_of /dev/shm/tesla-cow) $(loop_devs_of "$IMAGE"); do
        grep -q "^$lo " /proc/mounts || sudo losetup -d "$lo" 2>/dev/null
    done
    sudo rm -f /dev/shm/tesla-cow
}

# image_mounted_elsewhere: mount points of $IMAGE other than $CHROOT. Mounting an ext4 image a
# second time (another loop device) would corrupt it.
image_mounted_elsewhere() {
    local dev
    for dev in $(loop_devs_of "$IMAGE"); do
        awk -v d="$dev" -v r="$CHROOT" '$1 == d && $2 != r {print $2}' /proc/mounts
    done
}

# unmount_image: unmount $CHROOT; if it's busy, show who holds it
unmount_image() {
    sudo umount "$CHROOT" 2>/dev/null && return 0
    sleep 1
    sudo umount "$CHROOT" 2>/dev/null && return 0
    warn "couldn't unmount $CHROOT, it's busy. Held by:"
    sudo fuser -vm "$CHROOT" 2>&1 | sed 's/^/      /'
    info "Stop those, then: sudo umount $CHROOT"
    return 1
}

# proc_start PID: the process's start time (clock ticks since boot), to tell a reused pid apart
proc_start() {
    local s
    s=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
    s=${s##*) }
    set -- $s
    echo "${20}"
}

# helper_pids FILE: pids from a pids file ("pid starttime name") that still run
helper_pids() {
    local pid start name
    [ -f "$1" ] || return 0
    while read -r pid start name; do
        [ "$(proc_start "$pid")" = "$start" ] && echo "$pid"
    done <"$1"
}

# chroot_procs_maybe: are firmware processes running (without sudo: by name; may be the sandbox)
chroot_procs_maybe() {
    pgrep -f '(^|/)(QtCar[A-Za-z0-9]*|valhalla_server|AWE_command_line_tesla|audiod|escalator|tesla-cef[a-z-]*)( |$)' >/dev/null
}

# teardown STATE_DIR [quiet]: stop everything a run left (processes, mounts). Used by the trap of
# `./tesla start` and by `./tesla stop` when that instance is gone.
teardown() {
    local dir=$1 quiet=${2:-} m pids
    local MOUNTED_IMAGE=0
    [ -f "$dir/state" ] && MOUNTED_IMAGE=$(sed -n 's/^MOUNTED_IMAGE=//p' "$dir/state")
    # the cluster's restart loop (Model S/X) first: it would start QtCarCluster again as soon as
    # the chroot's processes are stopped (it crashed without its X screen, and its startup started
    # things in the chroot while it was being unmounted)
    local p start name
    [ -f "$dir/pids" ] && while read -r p start name; do
        [ "$name" = cluster ] && [ "$(proc_start "$p")" = "$start" ] && kill "$p" 2>/dev/null
    done <"$dir/pids"
    stop_chroot_procs "$quiet"
    pids=$(helper_pids "$dir/pids")
    if [ -n "$pids" ]; then
        [ -n "$quiet" ] || info "helpers: $(for p in $pids; do ps -o comm= -p "$p"; done | sort -u | tr '\n' ' ')"
        # root helpers (tesla-touch) need sudo; kill both ways
        kill $pids 2>/dev/null; sudo kill $pids 2>/dev/null
    fi
    # native screen: the touchscreen's access for QtCar's user, the screensaver
    local touch_acl native_display
    touch_acl=$(sed -n 's/^\(TOUCH_\)\?ACL=//p' "$dir/state" 2>/dev/null)
    native_display=$(sed -n 's/^NATIVE=//p' "$dir/state" 2>/dev/null)
    sed -n 's/^SND_ACL=//p' "$dir/state" 2>/dev/null | while read -r gid f; do
        sudo setfacl -x "g:$gid" "$f" 2>/dev/null
    done
    for f in $touch_acl; do
        sudo setfacl -x u:1111 "$f" 2>/dev/null
        case "$f" in /dev/dri/*) sudo setfacl -x u:1226 "$f"; sudo setfacl -x u:1980 "$f" ;; esac 2>/dev/null
    done
    if [ -n "$native_display" ]; then
        sudo rm -f /run/tesla-native
        DISPLAY=$native_display xset s default +dpms 2>/dev/null
        local restore
        restore=$(sed -n 's/^NATIVE_RESTORE=//p' "$dir/state" 2>/dev/null)
        if [ -n "$restore" ] && ! restore_native_layout "$native_display" "$restore"; then
            warn "could not restore the native display; check xrandr on $native_display"
        fi
    fi
    # audio clock (low tick kernels): back to jiffies; Bluetooth: give the adapter back to BlueZ
    local tcard
    tcard=$(sed -n 's/^TIMER_CARD=//p' "$dir/state" 2>/dev/null)
    [ -n "$tcard" ] && echo "" | sudo tee "/proc/asound/card$tcard/timer_source" >/dev/null 2>&1
    sudo pkill -f "aplay -q -D hw:CARD=hrcloc[k]" 2>/dev/null
    local bt
    bt=$(sed -n 's/^BT_RESTORE=//p' "$dir/state" 2>/dev/null)
    [ -n "$bt" ] && restore_bluetooth "$bt" "$(sed -n 's/^BT_USBPORT=//p' "$dir/state" 2>/dev/null)"
    restore_wifi "$(sed -n 's/^WIFI_RENAME=//p' "$dir/state" 2>/dev/null)" "$(sed -n 's/^WIFI_NM=//p' "$dir/state" 2>/dev/null)"
    # children of the helper loops (their loop may be gone already). The [x] keeps the pattern
    # from matching the command line of pkill's own sudo.
    pkill -f 'arecord -q -D hw:CARD=model[3]' 2>/dev/null
    pkill -f 'arecord -q -D hw:CARD=virtua[l]' 2>/dev/null
    pkill -f 'ffmpeg -nostdin -loglevel warning -f v4l[2]' 2>/dev/null
    sudo pkill -f -- "--bind $CHROOT/dev/input/touc[h]" 2>/dev/null
    sleep 1
    # unmount everything below the chroot, deepest first
    for m in $(mounts_below); do
        [ -n "$quiet" ] || info "unmount $m"
        sudo umount "$m" 2>/dev/null || sudo umount -l "$m" 2>/dev/null
    done
    if [ "$MOUNTED_IMAGE" = 1 ] && image_mounted >/dev/null; then
        [ -n "$quiet" ] || info "unmount the image from $CHROOT"
        unmount_image
    fi
    image_ram_merge "$quiet"
    rm -f "$LOGS/current"
}

# A rotated QtCar output can be taller than the old desktop framebuffer. Restore the outputs
# in a framebuffer large enough for both layouts, then shrink it back to the saved size.
# Applying --fb OLD and --rotate normal in one xrandr call fails on some X servers because the
# still-rotated output does not fit OLD when the framebuffer is resized first.
restore_native_layout() {
    local display=$1 saved=$2 current old interim w h i outputs=0
    local -a args normal_args
    read -r -a args <<<"$saved"
    [ "${args[0]}" = --fb ] && [ -n "${args[1]}" ] || return 1
    old=${args[1]}
    current=$(DISPLAY=$display xrandr --current 2>/dev/null |
        sed -n 's/^Screen [0-9]*:.* current \([0-9]*\) x \([0-9]*\),.*/\1x\2/p' | head -1)
    [ -n "$current" ] || return 1
    w=$(( ${old%x*} > ${current%x*} ? ${old%x*} : ${current%x*} ))
    h=$(( ${old#*x} > ${current#*x} ? ${old#*x} : ${current#*x} ))
    interim=${w}x${h}
    # State written before --rotate existed lacks it; restore those normal desktop layouts too.
    if [[ " $saved " != *" --rotate "* ]]; then args+=(--rotate normal); fi
    DISPLAY=$display xrandr --fb "$interim" "${args[@]:2}" || return 1
    DISPLAY=$display xrandr --fb "$old" && return 0
    # A previous run may have left the laptop rotated. Its saved landscape framebuffer cannot
    # contain the rotated physical mode without the old QtCar transform. For one output, recover
    # the normal desktop geometry rather than leave the user in the oversized interim layout.
    normal_args=("${args[@]:2}")
    for ((i=0; i<${#normal_args[@]}; i++)); do
        [ "${normal_args[i]}" = --output ] && ((outputs+=1))
        [ "${normal_args[i]}" = --rotate ] && normal_args[i+1]=normal
    done
    [ "$outputs" = 1 ] || return 1
    DISPLAY=$display xrandr --fb "$interim" "${normal_args[@]}" || return 1
    DISPLAY=$display xrandr --fb "$old" || return 1
    warn "saved rotated layout did not fit its old framebuffer; restored the output to normal"
}

# image_file PATH: read a (small) file from the image, mounted or not
image_file() {
    if image_mounted >/dev/null; then
        cat "$CHROOT/$1" 2>/dev/null
    elif [ -r "$IMAGE" ]; then
        # debugfs prints nothing (and still exits 0) for a missing file
        PATH=$PATH:/sbin debugfs -R "cat /$1" "$IMAGE" 2>/dev/null | grep . || return 1
    fi
}

# the kit's version stamp in the image (etc/mcu3-patchkit from kits before the rename)
kit_stamp() { image_file etc/mcu2-patchkit || image_file etc/mcu3-patchkit; }
# profile_info ROOT KEY: a value of the kit profile for the rootfs at ROOT (mcu2_patch.py --info):
# profile, car, center, cluster, maps
profile_info() {
    python3 "$KIT" "$1" --info 2>/dev/null | sed -n "s/^$2=//p"
}
# image_profile: the kit profile the image was built with (its stamp; the Model 3's for stamps
# from before profiles and for images without a stamp)
image_profile() {
    local p
    p=$(kit_stamp 2>/dev/null | sed -n 's/^profile=//p')
    echo "${p:-2019.20.4.2-model3}"
}
# profile_value PROFILE KEY: car, center, cluster, mit_shm, maps, services of a profile (mcu2_patch.py --info)
profile_value() {
    python3 "$KIT" --info --profile "$1" 2>/dev/null | sed -n "s/^$2=//p"
}
# can_db [PROFILE]: the CAN signal database for the image's car type (vehicle/work/, made by
# extract-can-db.py; the Model 3's has the old name can-db.json)
can_db() {
    local car
    car=$(profile_value "${1:-${PROFILE:-$(image_profile)}}" car)
    if [ -z "$car" ] || [ "$car" = model3 ]; then echo vehicle/work/can-db.json; else echo "vehicle/work/can-db-$car.json"; fi
}
# look_choices color|wheels|performance: the names --color/--wheels/--performance take for this
# image's car, in the order of their values ("-" = unused). Model 3: the simulator's SIM_* labels.
# Model S/X 2026: libQtCarVAPI's names for GTW_exteriorColorCode, GTW_wheelType (without the
# Model X's 20" and 22" wheels: the S has no pictures of them) and GTW_performanceAddOn.
look_choices() {
    if [ "$(can_db)" = vehicle/work/can-db.json ]; then
        case $1 in
            color) echo RedMulticoat SolidBlack SilverMetallic MidnightSilver - DeepBlue PearlWhite ;;
            wheels) echo Pinwheel18 Stiletto19 Stiletto20 Stiletto20Staggered Gemini19Square Gemini19Staggered ;;
            performance) echo Base Performance Ludicrous BasePlus ;;
        esac
    else
        case $1 in
            color) echo Default Black White Silver Grey Blue Green Brown Pearl SigRed Red SteelGrey \
                MetallicBlack TitaniumCopper SignatureBlue ;;
            wheels) echo Base19 Silver21 Charcoal21 Silver21Euro Aero19 Charcoal21Euro Super21Gray \
                Super21Silver Turbine19 Turbine19Dark AeroTurbine19 AeroTurbine19Black - - - Cyclone19Dark \
                - - Arachnid21Silver Arachnid21Black Arachnid21Grey - - Slipstream19Carbon \
                TwinTurbine21Silver TwinTurbine21Carbon Tempest19SonicSilver ;;
            performance) echo Standard Ludicrous ;;
        esac
    fi
}
# look_names color|wheels|performance: the same without the gaps
look_names() { local n out=(); for n in $(look_choices "$1"); do [ "$n" = - ] || out+=("$n"); done; echo "${out[*]}"; }
# kit_version_for STAMP: this kit's version for the stamp's profile (stamps without profile=: the
# kit's default, the Model 3 profile)
kit_version_for() {
    local p
    p=$(sed -n 's/^profile=//p' <<<"$1")
    python3 "$KIT" --kit-version ${p:+--profile "$p"} 2>/dev/null
}

# native_output: "OUTPUT<TAB>WxH" of the first connected output of $NATIVE_DISPLAY with a mode.
# The size is the panel's own mode, not a scaled framebuffer.
native_output() {
    local want=${NATIVE_OUTPUT:-auto}
    native_layout | awk -F'\t' -v want="$want" '
        want != "auto" && $1 == want {print $1 "\t" $3; found=1; exit}
        want == "auto" && $2 == 1 && !found {print $1 "\t" $3; found=1; exit}
        !first {first = $1 "\t" $3}
        END {if (!found && want == "auto" && first) print first}'
}

# native_layout: active outputs, one per line:
# NAME<TAB>PRIMARY(0/1)<TAB>MODE WxH<TAB>X<TAB>Y<TAB>ROTATION.
# The active mode is taken from its mode line: after rotation the output geometry has swapped
# dimensions, but xrandr --mode and the physical panel size still use the unrotated mode.
native_layout() {
    DISPLAY=$NATIVE_DISPLAY xrandr --current 2>/dev/null | awk '
        / connected / {
            name = $1; primary = ($3 == "primary"); g = primary ? $4 : $3
            active = (g ~ /^[0-9]+x[0-9]+\+[0-9]+\+[0-9]+$/)
            if (active) {
                split(g, a, /[x+]/); x = a[3]; y = a[4]
                # The word after geometry is either the active rotation or "(normal".
                # Searching every word accidentally selects "left" from the list of
                # supported rotations when the output is actually normal.
                rotation = $(primary ? 5 : 4)
                sub(/^\(/, "", rotation)
                if (rotation != "left" && rotation != "right" && rotation != "inverted")
                    rotation = "normal"
            }
            next
        }
        active && $1 ~ /^[0-9]+x[0-9]+$/ && /\*/ {
            print name "\t" primary "\t" $1 "\t" x "\t" y "\t" rotation
            active = 0
        }'
}

# native_screen_size: the X screen's current size (WxH), e.g. 3840x1080 for two monitors
native_screen_size() {
    DISPLAY=$NATIVE_DISPLAY xrandr --current 2>/dev/null |
        sed -n 's/^Screen [0-9]*:.* current \([0-9]*\) x \([0-9]*\),.*/\1x\2/p' | head -1
}

# find_touchscreen: "/dev/input/eventN<TAB>name" of the first touchscreen: a multitouch device
# (ABS_MT_POSITION_X, bit 53) with INPUT_PROP_DIRECT (it's the screen; a touchpad is "pointer")
find_touchscreen() {
    python3 - <<'PY'
import re
for block in open("/proc/bus/input/devices").read().split("\n\n"):
    name = re.search(r'N: Name="(.*)"', block)
    ev = re.search(r"H: Handlers=.*\b(event\d+)", block)
    prop = re.search(r"B: PROP=(\w+)", block)
    absb = re.search(r"B: ABS=([\w ]+)", block)
    if not (name and ev and prop and absb):
        continue
    words = absb.group(1).split()
    if int(prop.group(1), 16) & 2 and int(words[-1], 16) >> 53 & 1:
        print("/dev/input/%s\t%s" % (ev.group(1), name.group(1)))
        break
PY
}

# find_camera: the first webcam (CAMERA=auto): the capture node (index 0) of a UVC device whose
# name doesn't say IR/Infrared (a laptop's IR camera also offers MJPG/YUYV), no v4l2loopback
find_camera() {
    local d
    for d in /sys/class/video4linux/video*; do
        [ "$(basename "$(readlink -f "$d/device/driver" 2>/dev/null)" 2>/dev/null)" = uvcvideo ] || continue
        [ "$(cat "$d/index" 2>/dev/null)" = 0 ] || continue
        grep -qiE '\bIR\b|infrared' "$d/name" 2>/dev/null && continue
        echo "/dev/${d##*/}"
        return
    done
}

# find_wifi: the first wireless network interface (WIFI_ADAPTER=auto)
find_wifi() {
    local d
    for d in /sys/class/net/*/wireless; do [ -d "$d" ] && { d=${d%/wireless}; echo "${d##*/}"; return; }; done
}

# restore_wifi NAME NM_DEVICE: after --wifi: wlan0 gets its name back (without connmand's
# addresses), NetworkManager takes it again
restore_wifi() {
    if [ -n "$1" ] && [ -e /sys/class/net/wlan0 ] && [ ! -e "/sys/class/net/$1" ]; then
        sudo ip addr flush dev wlan0 2>/dev/null
        sudo ip link set wlan0 down 2>/dev/null
        sudo ip link set wlan0 name "$1"
    fi
    [ -n "$2" ] && sudo nmcli device set "$2" managed yes 2>/dev/null
    return 0
}

# restore_bluetooth hciN: give the adapter back to BlueZ after --bluetooth. The bridge took it down;
# after the Broadcom stack an Intel AX210 didn't answer anymore ("Connection timed out") until its
# driver was reloaded (which loads its firmware again).
restore_bluetooth() {
    local bt=$1 usbport=${2:-} drv i
    sudo pkill -f "bluetooth/hci-bridge.p[y]" 2>/dev/null
    for i in $(seq 20); do pgrep -f "bluetooth/hci-bridge.p[y]" >/dev/null || break; sleep 0.2; done
    sudo systemctl start bluetooth 2>/dev/null
    for i in $(seq 6); do hciconfig "$bt" 2>/dev/null | grep -q "UP RUNNING" && break; sudo hciconfig "$bt" up 2>/dev/null; sleep 0.5; done
    if ! hciconfig "$bt" 2>/dev/null | grep -q "UP RUNNING"; then
        drv=$(sed -n 's/^DRIVER=//p' "/sys/class/bluetooth/$bt/device/uevent" 2>/dev/null)
        if [ -n "$drv" ]; then
            sudo modprobe -r "$drv" 2>/dev/null; sleep 1; sudo modprobe "$drv" 2>/dev/null
            for i in $(seq 12); do hciconfig "$bt" 2>/dev/null | grep -q "UP RUNNING" && break; sleep 0.5; done
        fi
    fi
    # still gone (the chip dropped off USB): power-cycle its USB port, then the hub's empty internal
    # ports too (on a ThinkPad T480 the AX210 came back only after cycling port 6, it sits on 7)
    if ! hciconfig "$bt" 2>/dev/null | grep -q "UP RUNNING" && [ -n "$usbport" ] && [ -e "$usbport/disable" ]; then
        local ports=$usbport p
        for p in "$(dirname "$usbport")"/*-port*; do
            [ "$(cat "$p/connect_type" 2>/dev/null)" = "not used" ] && [ -z "$(ls "$p/device" 2>/dev/null)" ] &&
                ports="$ports $p"
        done
        for p in $ports; do echo 1 | sudo tee "$p/disable" >/dev/null; done
        sleep 3
        for p in $ports; do echo 0 | sudo tee "$p/disable" >/dev/null; done
        for i in $(seq 20); do hciconfig "$bt" 2>/dev/null | grep -q "UP RUNNING" && break; sleep 0.5; done
    fi
    bluetoothctl power on >/dev/null 2>&1
    if hciconfig "$bt" 2>/dev/null | grep -q "UP RUNNING"; then
        info "bluetooth: $bt is back with BlueZ"
    else
        warn "bluetooth: $bt didn't come back; try: sudo modprobe -r btusb && sudo modprobe btusb"
    fi
}
