#!/bin/bash
# Run QtCar and firmware services in the sudo-free podman sandbox (sandbox/rootfs, see CLAUDE.md),
# on Xvfb :97. For testing without the real chroot.
#
#   ./sandbox.sh [options] [SERVICE...]        e.g. ./sandbox.sh -t 90 -s "60 85" --touch qtcar-sim qtcar-vehicle
#
#   --root DIR   another user-owned rootfs (default: $SANDBOX_ROOT or sandbox/rootfs), e.g.
#                sandbox/rootfs-s; its kit profile sets the screens (Model S: 1200x1920 on :97.0
#                plus the cluster, QtCarCluster, 1920x720 on :97.1). Output: sandbox/out-<name>
#   --no-cluster don't start QtCarCluster
#   -t SECONDS   how long to run (default 60)
#   -s "T ..."   screenshots after T seconds -> sandbox/out/shot_T.png (+ shot_T-cluster.png)
#   --touch      tesla-touch on :97 (user in group input / uinput ACL), passed into the container;
#                then e.g. DISPLAY=:97 xdotool mousemove 858 203 click 1 taps the screen
#   --plain      run QtCar directly instead of under gdb
#   --browser    under gdb, don't skip the browser (CEF) either
#   -x FILE      extra gdb commands (copied into the sandbox)
#   --audio      pass /dev/snd in (the "model3" snd-aloop card, see start_all.sh AUDIO=1)
#   --video      pass /dev/video* in (backup camera, [bkcam] in settings.conf)
#   --music DIR  DIR as a USB stick (/home/tesla/media/usb-music, S/X /mnt/media/usb-music; read-only)
#
# QTCAR_ARGS="..." adds QtCar command line options. With the kit's Mesa in the rootfs
# (/opt/mesa/env.sh, the Model S profile) QtCar uses it and the container shares the host's IPC
# namespace (llvmpipe draws through MIT-SHM).
# QtCar runs under the host's gdb (host / mounted at /host, run with the host's loader) with
# WebKitView::createBrowserClient skipped: without /usr/bin/escalator (root-only, not in the
# sandbox) CEF kills QtCar after ~20 s. A crash leaves a backtrace in qtcar.log. QtCar and the
# services are restarted when they exit. Logs: sandbox/out/. QtCar reads /root/.Tesla here.
set -u
cd "$(dirname "$(realpath "$0")")"
ROOT=$(realpath -m "${SANDBOX_ROOT:-sandbox/rootfs}")
T=60 SHOTS="" TOUCH=0 GDB=1 BROWSER=0 GDBX="" AUDIODEV=0 VIDEODEV=0 MUSIC="" CLUSTER_ON=1
while [ $# -gt 0 ]; do
    case "$1" in
        --root) ROOT=$(realpath -m "$2"); shift 2 ;;
        --no-cluster) CLUSTER_ON=0; shift ;;
        -t) T=$2; shift 2 ;;
        -s) SHOTS=$2; shift 2 ;;
        --touch) TOUCH=1; shift ;;
        --plain) GDB=0; shift ;;
        --browser) BROWSER=1; shift ;;
        -x) GDBX=$2; shift 2 ;;
        --audio) AUDIODEV=1; shift ;;
        --video) VIDEODEV=1; shift ;;
        --music) MUSIC=$(realpath "$2"); shift 2 ;;
        -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
        *) break ;;
    esac
done
[ -d "$ROOT/usr/tesla" ] || { echo "no sandbox rootfs at $ROOT"; exit 1; }
OUT=$PWD/sandbox/out
[ "$ROOT" = "$PWD/sandbox/rootfs" ] || OUT=$PWD/sandbox/out-$(basename "$ROOT")
mkdir -p "$OUT"
rm -f "$OUT"/*.log "$OUT"/shot_*.png

# what the rootfs needs (its kit profile, see mcu2-patchkit/README.md)
INFO=$(python3 mcu2-patchkit/mcu2_patch.py "$ROOT" --info 2>/dev/null)
CENTER=$(sed -n 's/^center=//p' <<<"$INFO") CLUSTER=$(sed -n 's/^cluster=//p' <<<"$INFO")
CENTER=${CENTER:-1920x1200}
SX=""; [ -n "$CLUSTER" ] && SX=1                            # an S/X profile (with or without --no-cluster)
[ "$CLUSTER_ON" = 1 ] || CLUSTER=""
CEF=0; [ -f "$ROOT/usr/lib/libcef.so" ] && CEF=1          # the Model 3's browser (CEF in QtCar)
MESA=""; [ -f "$ROOT/opt/mesa/env.sh" ] && MESA=/opt/mesa/env.sh
QARGS="--touch evdev,autorange"
# S/X: QtCar picks its layout from --cid and the size (on the car QCSIZE, RunQtCar.env)
[ -n "$SX" ] && QARGS="-graphicssystem raster --cid --size $CENTER $QARGS"
# the S/X profile's startup.sh runs QtCar --emulated (the panels count as on, see its kit.py)
[ -n "$SX" ] && QARGS="$QARGS --emulated"

cat > "$ROOT/sandbox-qtcar.sh" <<'EOF'
#!/bin/sh
# written by sandbox.sh: QtCar (or SB_BIN, e.g. QtCarCluster) as container root, optionally under
# the host's gdb. SB_DISPLAY, SB_ARGS, SB_HOME, SB_MESA come from sandbox.sh.
cd /usr/tesla/UI/bin
export PATH=/bin:/usr/bin:/sbin:/usr/sbin:/usr/local/bin DISPLAY=${SB_DISPLAY:-:97} \
    LD_LIBRARY_PATH=/usr/tesla/UI/lib:/usr/cid-lib:/usr/lib64:/lib64:/lib:/usr/lib:/usr/lib/engines-3 \
    LC_ALL=C LANG=C LANGUAGE=C QT_X11_NO_MITSHM=1 LIBGL_ALWAYS_SOFTWARE=1 \
    EGL_PIXMAP_SHIM_DEBUG=1 CEF_SHIM_DEBUG=1 CEF_LOG_SEVERITY=${CEF_LOG_SEVERITY:-info} CEF_EXTRA_ARGS=$CEF_EXTRA_ARGS CEF_REMOTE_DEBUGGING_PORT=$CEF_REMOTE_DEBUGGING_PORT
[ -n "$SB_HOME" ] && export HOME=$SB_HOME
# the cluster's home: the shared settings.conf, as startup.sh links it (without it the data value
# listeners checked the peers' security labels and dropped every connection)
if [ -n "$SB_HOME" ] && [ "$SB_HOME" != /home/tesla ] && [ ! -e "$SB_HOME/.Tesla/car/settings.conf" ]; then
    mkdir -p "$SB_HOME/.Tesla/car" && ln -s /home/tesla/.Tesla/car/settings.conf "$SB_HOME/.Tesla/car/settings.conf"
fi
[ -n "$SB_MESA" ] && . "$SB_MESA"
BIN=${SB_BIN:-QtCar}
PRELOAD=""
for so in icu_preload egl_pixmap_shim egl_media_pixmap_shim cef_nosandbox sandbox_escalator_stub; do
    [ -f /usr/lib/$so.so ] && PRELOAD=$PRELOAD${PRELOAD:+:}/usr/lib/$so.so
done
[ "$1" = gdb ] || LD_PRELOAD=$PRELOAD exec ./$BIN $SB_ARGS $QTCAR_ARGS
H=/host
unset LD_PRELOAD    # only for QtCar, not for the host's gdb
export PYTHONHOME=$H/usr   # gdb's embedded Python, with the host's standard library
# the host's libraries: Debian/Ubuntu (x86_64-linux-gnu), Fedora (lib64)
exec $H/lib64/ld-linux-x86-64.so.2 --library-path $H/lib/x86_64-linux-gnu:$H/usr/lib/x86_64-linux-gnu:$H/usr/lib64:$H/lib64 \
    $H/usr/bin/gdb -nx -batch -data-directory $H/usr/share/gdb -ex 'set startup-with-shell off' -ex 'set pagination off' -ex 'set auto-load off' \
    -ex 'handle SIGPIPE nostop noprint pass' -ex 'handle SIGUSR1 nostop noprint pass' \
    -ex 'handle SIG32 nostop noprint pass' -ex 'handle SIG33 nostop noprint pass' \
    -ex "set environment LD_PRELOAD=$PRELOAD" -ex 'set follow-fork-mode parent' -ex 'set detach-on-fork on' \
    ${NOBROWSER:+-x /sandbox-nobrowser.gdb} ${GDBX:+-x /sandbox-extra.gdb} -ex run -ex 'bt 30' --args ./$BIN $SB_ARGS $QTCAR_ARGS
EOF
chmod +x "$ROOT/sandbox-qtcar.sh"
cat > "$ROOT/sandbox-nobrowser.gdb" <<'EOF'
break WebKitView::createBrowserClient
commands
silent
printf "gdb: skipping WebKitView::createBrowserClient\n"
return
continue
end
EOF

# sandbox only: no /usr/bin/escalator and no cgroups here. QtCar's ChromiumManager::initializeCef
# dies when Escalator::runCommand() (cgroup setup) can't reach it, and tesla-cef-launcher insists
# on being in the net_cls:/nonet cgroup. Pretend both worked.
cat > "$OUT/stub.c" <<'EOF'
#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
// the real fopen under its other name: no dlsym, which on the firmware's glibc lives in libdl and
// tesla-cef-launcher doesn't load that
FILE *_IO_fopen(const char *path, const char *mode);
int _ZN9Escalator10runCommandEv(void *self) { return 0; }
FILE *fopen64(const char *path, const char *mode)
{
    if (path && strcmp(path, "/proc/self/cgroup") == 0) {
        FILE *f = _IO_fopen("/tmp/sandbox-cgroup", "w");
        if (f) {
            fputs("4:net_cls:/nonet\n", f);
            fclose(f);
            return _IO_fopen("/tmp/sandbox-cgroup", mode);
        }
    }
    return _IO_fopen(path, mode);
}
// crash backtraces from CEF's child processes (no core dumps, no Chromium handler with
// --disable-in-process-stack-traces): frames + load address of libcef.so to stderr
#define _GNU_SOURCE
#include <execinfo.h>
#include <link.h>
#include <signal.h>
#include <unistd.h>
static int print_base(struct dl_phdr_info *info, size_t size, void *data)
{
    if (strstr(info->dlpi_name, "libcef.so")) {
        char buf[128];
        int n = snprintf(buf, sizeof buf, "sandbox-segv: libcef.so base 0x%lx\n", (unsigned long)info->dlpi_addr);
        write(2, buf, n);
    }
    return 0;
}
static void on_segv(int sig, siginfo_t *si, void *uc)
{
    char buf[128];
    int n = snprintf(buf, sizeof buf, "sandbox-segv: pid %d signal %d addr %p\n", getpid(), sig, si->si_addr);
    write(2, buf, n);
    dl_iterate_phdr(print_base, NULL);
    void *frames[64];
    int k = backtrace(frames, 64);
    backtrace_symbols_fd(frames, k, 2);
    signal(sig, SIG_DFL);
    raise(sig);
}
__attribute__((constructor)) static void init(void)
{
    char exe[256] = {0};
    readlink("/proc/self/exe", exe, sizeof exe - 1);
    if (!strstr(exe, "tesla-cef"))
        return;
    struct sigaction sa = {0};
    sa.sa_sigaction = on_segv;
    sa.sa_flags = SA_SIGINFO;
    sigaction(SIGSEGV, &sa, NULL);
}
EOF
if [ "$CEF" = 1 ] && ! cmp -s "$OUT/stub.c" "$ROOT/usr/lib/sandbox_escalator_stub.c"; then
    gcc -shared -fPIC -O2 -o "$ROOT/usr/lib/sandbox_escalator_stub.so" "$OUT/stub.c" &&
        cp "$OUT/stub.c" "$ROOT/usr/lib/sandbox_escalator_stub.c"
fi

# Xvfb :97 with the profile's screens; one left from a run with other screens is replaced
SCREENS="$CENTER${CLUSTER:+ $CLUSTER}"
if [ "$(DISPLAY=:97 xdpyinfo 2>/dev/null | sed -n 's/^ *dimensions: *\([0-9x]*\) pixels.*/\1/p' | xargs)" != "$SCREENS" ]; then
    pkill -f '^Xvfb :97( |$)' && sleep 0.5
    XARGS=(-screen 0 "${CENTER}x24"); [ -n "$CLUSTER" ] && XARGS+=(-screen 1 "${CLUSTER}x24")
    setsid -f Xvfb :97 "${XARGS[@]}" -nolisten tcp >/dev/null 2>&1 </dev/null
    for _ in $(seq 30); do xdpyinfo -display :97 >/dev/null 2>&1 && break; sleep 0.1; done
fi

DEV=()
TPID=""
if [ "$TOUCH" = 1 ]; then
    tesla-touch/target/release/tesla-touch --display :97 --no-cursor >"$OUT/touch.log" 2>&1 &
    TPID=$!
    trap 'kill "$TPID" 2>/dev/null' EXIT; trap 'exit 143' TERM INT    # also when the script is killed
    for _ in $(seq 50); do grep -q '^ready' "$OUT/touch.log" && break; sleep 0.1; done
    EV=$(grep -o '/dev/input/event[0-9]*' "$OUT/touch.log" | head -1)
    [ -n "$EV" ] && DEV=(--device "$EV:/dev/input/touch") || echo "tesla-touch didn't start, see $OUT/touch.log"
fi

[ "$AUDIODEV" = 1 ] && DEV+=(--device /dev/snd)
if [ "$VIDEODEV" = 1 ]; then for v in /dev/video*; do DEV+=(--device "$v"); done; fi
USBDIR=/home/tesla/media/usb-music; [ -n "$SX" ] && USBDIR=/mnt/media/usb-music   # see start.sh
[ -n "$MUSIC" ] && DEV+=(-v "$MUSIC:$USBDIR:ro")
SVC=""
for s in "$@"; do    # a SERVICE can have options: "qtcar-sim --udp :1235"
    SVC="$SVC (while :; do /usr/local/bin/qtcar-service $s; echo sandbox: $s exited; sleep 2; done) >/out/${s%% *}.log 2>&1 &"
done
MODE=plain; [ "$GDB" = 1 ] && MODE=gdb
NOBROWSER=1; { [ "$BROWSER" = 1 ] || [ "$CEF" = 0 ]; } && NOBROWSER=
[ -n "$GDBX" ] && cp "$GDBX" "$ROOT/sandbox-extra.gdb"
for t in $SHOTS; do
    ( sleep "$t"; DISPLAY=:97.0 import -window root "$OUT/shot_$t.png"
      [ -z "$CLUSTER" ] || DISPLAY=:97.1 import -window root "$OUT/shot_$t-cluster.png" ) &
done
IPC=(); [ -n "$MESA" ] && IPC=(--ipc host)
CL=""
[ -n "$CLUSTER" ] && CL="(while :; do SB_BIN=QtCarCluster SB_DISPLAY=:97.1 SB_HOME=/home/qtcluster SB_ARGS='-graphicssystem raster --isic --ic --window 1.5' SB_MESA=$MESA NOBROWSER= GDBX= /sandbox-qtcar.sh $MODE; echo '=== sandbox: QtCarCluster exited, restarting'; sleep 2; done) >/out/cluster.log 2>&1 &"
HOMEQ=""; [ -n "$SX" ] && HOMEQ=/home/tesla

podman run --rm --timeout $((T + 20)) --network host "${IPC[@]}" -e QCSVC_NOUSER=1 -e WIFI_DEV="${WIFI_DEV:-}" -e QTCAR_ARGS="${QTCAR_ARGS:-}" -e CEF_EXTRA_ARGS="${CEF_EXTRA_ARGS:-}" -e CEF_REMOTE_DEBUGGING_PORT="${CEF_REMOTE_DEBUGGING_PORT:-}" -v /:/host:ro "${DEV[@]}" \
    -v /tmp/.X11-unix:/tmp/.X11-unix -v "$OUT":/out --rootfs "$ROOT" /bin/sh -c "
    $SVC
    sleep 3
    (while :; do SB_DISPLAY=:97.0 SB_HOME=$HOMEQ SB_ARGS='$QARGS' SB_MESA=$MESA NOBROWSER=$NOBROWSER GDBX=$GDBX /sandbox-qtcar.sh $MODE; echo '=== sandbox: QtCar exited, restarting'; sleep 2; done) >/out/qtcar.log 2>&1 &
    $CL
    sleep $T"
[ -n "$TPID" ] && kill "$TPID"
wait
echo "logs and screenshots: $OUT"
