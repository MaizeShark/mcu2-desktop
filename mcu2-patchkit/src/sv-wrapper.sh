#!/bin/sh
# /sbin/sv in the chroot (mcu2-patchkit). On the car runit supervises the services and the
# escalator starts some of them with "sv start <name>" (fireplace, dog mode, HAL 9000 videos).
# There is no runsvdir in the chroot, so for those services this runs qtcar-service <name>
# in the background instead; everything else goes to runit's sv (/sbin/sv.runit).
#   sv start|up|u|stop|down|d|force-stop|status <service>...

RUNDIR=/run/qtcar-sv
handled() {
    case "$1" in fireplace|dog-mode|hal-9000|camp-mode|camp-mode-holiday|mame|cobalt|cobalt-input|2048|chess|backgammon) return 0 ;; esac
    # the S/X audiod's media bridges (through the profile's escalator-stub; the Model 3's
    # a2dpbridge is a service of ./tesla start)
    case "$1" in alsaloop-chromium|media1bridge|a2dpbridge) [ -x /usr/local/bin/escalator-stub ] && return 0 ;; esac
    return 1
}

# the escalator's stop-mame runs "sv DOWN mame" (runit takes any case)
cmd=$(echo "$1" | tr 'A-Z' 'a-z')
[ $# -ge 2 ] || exec /sbin/sv.runit "$@"
shift
for s in "$@"; do handled "${s##*/}" || exec /sbin/sv.runit "$cmd" "$@"; done

mkdir -p "$RUNDIR"
rc=0
for s in "$@"; do
    s=${s##*/}
    pidfile=$RUNDIR/$s.pid
    # "pid starttime": a pid file of an earlier run (the image keeps /run) can name a pid that
    # another process has now; then the job didn't start ("ok: run" without it)
    read -r pid start 2>/dev/null <"$pidfile"
    running=0
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null &&
        [ "$(sed 's/.*) //' "/proc/$pid/stat" 2>/dev/null | cut -d' ' -f20)" = "$start" ] && running=1
    case "$cmd" in
        start|up|u|o|once)
            if [ "$running" = 0 ]; then
                setsid /usr/local/bin/qtcar-service "$s" </dev/null >>"$RUNDIR/$s.log" 2>&1 &
                echo "$! $(sed 's/.*) //' "/proc/$!/stat" 2>/dev/null | cut -d' ' -f20)" >"$pidfile"
                # where the car's jobs keep their pid (audiod reads /run/service/<job>/<job>.pid)
                mkdir -p "/run/service/$s" && echo "$!" >"/run/service/$s/$s.pid"
            fi
            echo "ok: run: $s" ;;
        # force-stop: the escalator's stop-tesla-game-cobalt and stop-mame (Arcade -> EXIT)
        stop|down|d|exit|x|kill|k|force-stop|force-shutdown)
            if [ "$running" = 1 ]; then
                kill "$pid" 2>/dev/null
                sleep 1
                kill -9 "$pid" 2>/dev/null
            fi
            rm -f "$pidfile" "/run/service/$s/$s.pid"
            echo "ok: down: $s" ;;
        status|s)
            if [ "$running" = 1 ]; then echo "run: $s: (pid $pid)"; else echo "down: $s"; rc=3; fi ;;
        *)
            echo "sv (chroot wrapper): '$cmd' not supported for $s" >&2; rc=1 ;;
    esac
done
exit $rc
