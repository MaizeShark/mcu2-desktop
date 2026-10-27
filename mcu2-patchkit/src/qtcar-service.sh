#!/bin/sh
# Start one of the firmware's QtCar-framework services inside the chroot, the way
# /etc/sv/<job>/run does it on the car, minus minijail, AppArmor, cgroups and firewall rules.
#
#   qtcar-service <runit job> [extra args]     e.g.  qtcar-service qtcar-vehicle
#   qtcar-service valhalla                     the routing engine (see /etc/sv/valhalla/run)
#   qtcar-service qtcar-sim                    QtCarSimService, Tesla's vehicle simulator: acts as
#                                              the gateway and sends a simulated car's CAN frames
#   qtcar-service audioweaver | audiod         the audio DSP (AudioWeaver) and its control daemon;
#                                              need the "model3" sound card (snd-aloop, start_all AUDIO=1)
#   qtcar-service --list
#
# Everything runs on loopback (like QtCar in this setup), not on the car's 192.168.90.x net.
# QCSVC_NOUSER=1 runs the service as the calling user instead of its own (e.g. in a rootless
# container, where switching users doesn't work).

if [ "$1" = "--list" ]; then
    for j in $(sed -n "s/^\([a-z0-9-]*\))$/\1/p" /etc/RunQtCar.vars); do [ -d "/etc/sv/$j" ] && echo "$j"; done
    echo valhalla
    echo qtcar-sim
    echo audioweaver
    echo audiod
    echo a2dpbridge
    echo dbus; echo bsa_server; echo btd; echo wpa_supplicant; echo connman
    echo fireplace; echo dog-mode; echo hal-9000; echo camp-mode; echo camp-mode-holiday; echo mame; echo cobalt
    [ -d /usr/tesla/UI/standalone_apps ] && echo 2048 chess backgammon
    [ -x /usr/local/bin/tesla-media-service ] &&
        echo media-webapp-server dbus-session-chromium-app chromium-adapter chromium-app-input chromium-app \
            dbus-session-chromium chromium-input chromium dbus-session-chromium-fullscreen \
            chromium-fullscreen-input chromium-fullscreen
    exit 0
fi
JOB=$1
[ -n "$JOB" ] || { echo "usage: qtcar-service <job> | --list" >&2; exit 2; }
shift
[ -d "/etc/sv/$JOB" ] || [ "$JOB" = qtcar-sim ] || [ "$JOB" = escalator-stub ] ||
    { echo "qtcar-service: no service $JOB" >&2; exit 1; }

# The S/X profile's stand-in for the car's escalator (QtCar's privileged helper); it only answers
# a fixed table of requests (src/escalator-stub.c in the profile). The real one is never run.
if [ "$JOB" = escalator-stub ]; then
    [ -x /usr/local/bin/escalator-stub ] || { echo "qtcar-service: no escalator-stub in this image" >&2; exit 1; }
    exec /usr/local/bin/escalator-stub
fi

# The 2026 S/X firmware runs media web apps in a separate Chromium process. Its runit scripts
# depend on minijail, cgroups and sv, so the profile installs a direct launcher for these jobs.
case "$JOB" in media-webapp-server|dbus-session-chromium-app|chromium-adapter|chromium-app-input|chromium-app|dbus-session-chromium|chromium-input|chromium|dbus-session-chromium-fullscreen|chromium-fullscreen-input|chromium-fullscreen)
    [ -x /usr/local/bin/tesla-media-service ] || { echo "qtcar-service: media jobs need the S/X profile" >&2; exit 1; }
    exec /usr/local/bin/tesla-media-service "$JOB" "$@" ;;
esac

export PATH=/bin:/usr/bin:/sbin:/usr/sbin:/usr/local/bin

# The audio type (base/premium) as /usr/bin/audio-type gives it, but without asking the gateway
# first: audio-type runs `gw-diag GET_CONFIG_DATA 0 31`, which sends UDP to the car's gateway
# (192.168.90.102:3500, which here goes out to the LAN) and waits 5 x 10 s for an answer before
# it uses [audiod] audio_type from settings.conf anyway (the kit sets it). That held up audiod and
# QtCarAudiod for 50 s, and QtCar's media sources wait for the audio system ("Loading..." on USB).
audio_type() {
    t=$(sed -r -n -e '/\[audiod\]/,/\[/s/^audio_type=(.*)/\1/p' /home/tesla/.Tesla/car/settings.conf 2>/dev/null)
    [ -n "$t" ] && echo "$t" || audio-type
}

as_user() {  # as_user <user[:groups]> <cmd...>
    u=$1; shift
    if [ -n "$QCSVC_NOUSER" ] || [ "$(id -u)" != 0 ]; then exec "$@"; fi
    exec chpst -u "$u" "$@"
}

if [ "$JOB" = valhalla ]; then
    # same as /etc/sv/valhalla/run: tiles in /opt/navigon/tm/<2-letter region>/valhalla,
    # overridable with force_maps_path= in settings.conf
    . /etc/tesla.env
    SETTINGSCONF=$TESLA_HOME/.Tesla/car/settings.conf
    REGION=$(ls /opt/navigon/tm/ 2>/dev/null | grep '^..$' | head -1)
    MAPPATH=$(grep -i -E '^force_maps_path *=' "$SETTINGSCONF" 2>/dev/null | sed 's/=/ /' | awk '{print $2}')
    [ -z "$MAPPATH" ] && MAPPATH=/opt/navigon/tm/$REGION/valhalla
    [ -d "$MAPPATH" ] || { echo "qtcar-service: no map tiles in $MAPPATH" >&2; exit 1; }
    OVERRIDE=$TESLA_HOME/.Tesla/data/map_override.pbf.gz
    echo "qtcar-service: valhalla, region ${REGION:-?}, tiles $MAPPATH" >&2
    # Clean environment like runit on the car: valhalla builds a C++ locale from LANG/LC_*, and
    # the host's values (e.g. de_DE.UTF-8, passed through sudo) don't exist in the firmware, so
    # every route failed with "locale::facet::_S_create_c_locale name not valid".
    as_user valhalla env -i PATH="$PATH" LD_LIBRARY_PATH=/usr/proto2/lib:/usr/lib:/lib \
        valhalla_server --config "$TESLA_UI/assets/tesla_maps/valhalla.json" \
        --tile_dir "$MAPPATH" --override_file "$OVERRIDE"
fi

# Audio, as /etc/sv/{audioweaver,audiod}/run: AudioWeaver (AWE) mixes the apps' tplug channels
# (shared memory, /dev/shm/tplug-*) and plays them on the car's card "model3"; audiod configures
# it (volumes, routing) over AWE's TCP port and creates the tplugs. Without minijail/sandbox,
# iptables, apparmor, RT tuning and the amplifier GPIO/I2C setup (/opt/audio/tesla/init.sh).
if [ "$JOB" = audioweaver ]; then
    # AWE must not open the sound card itself (on the car its minijail has no /dev/snd):
    # audiod does the I/O. /etc/asound-awe.conf points its hardware PCMs at a missing card.
    mkdir -p /var/log/audioweaver
    cd / || exit 1
    SCRIPT=/opt/audioweaver/$(audio-type --product).aws
    echo "qtcar-service: audioweaver $SCRIPT" >&2
    exec env -i PATH="$PATH" LD_LIBRARY_PATH=/opt/audioweaver LC_ALL=C \
        ALSA_CONFIG_PATH=/usr/share/alsa/alsa.conf:/etc/asound-awe.conf \
        /opt/audioweaver/AWE_command_line_tesla -nocmd -norate "-script:$SCRIPT"
fi
if [ "$JOB" = audiod ]; then
    mkdir -p /home/audiod/audiologs
    chown -h audiod:audio /home/audiod/audiologs 2>/dev/null
    TYPE=$(audio_type 2>/dev/null)
    echo "qtcar-service: audiod, audio type $TYPE" >&2
    if [ ! -x /opt/audioweaver/AWE_command_line_tesla ]; then
        # The 2026 S/X: AudioWeaver is built into audiod (/opt/audioweaver/libAWECore.so + the
        # .awb model), which mixes the apps' tplug channels and the virtual card's loopbacks
        # (Chromium, Browser, games, A2DP) to the amplifier outputs (auc, the kit's asound block).
        # Its options as /etc/sv/audiod/run gives them (the gateway's config as settings.conf says);
        # the system bus: it exits without its D-Bus name (com.tesla.AudiodService).
        mkdir -p /var/run/audiod /tmp/audiod/stream/persist
        touch /var/lock/audio-stack.lock
        as_user root env -i PATH="$PATH" HOME=/home/audiod LD_LIBRARY_PATH=/opt/audioweaver LC_ALL=C \
            TPLUG_LOG_XRUN=1 DBUS_SYSTEM_BUS_ADDRESS=unix:path=/var/run/dbus/system_bus_socket \
            /usr/bin/audiod --audio-type="$TYPE" --audio-platform="$(audio-type -p 2>/dev/null)" \
            --pws-type=none --country=unknown --audio-non-blocking-rate=4 --log-phone-status-seconds=0 \
            --use-new-premium-amp-periph-init=0 --audio-reopen-unpumpable-devs=true \
            --audio-repump-can-init-devs=true --mlock-memory=true "$@"
    fi
    # as root: its pump threads need real-time priority (4 ms periods). On the car minijail gives
    # the audiod user CAP_SYS_NICE; without it the output underran now and then (stutter).
    as_user root env -i PATH="$PATH" HOME=/home/audiod LD_LIBRARY_PATH=/opt/audioweaver LC_ALL=C \
        TPLUG_LOG_XRUN=1 /usr/bin/audiod --audio-type="$TYPE" --audio-api=/opt/audioweaver/dsp-control.csv "$@"
fi

# Model 3 Bluetooth music, as /etc/sv/a2dpbridge/run (minus sandbox and taskset): btd plays
# into the snd-aloop card "virtual"; alsaloop copies it into AudioWeaver's a2dp tplug. The
# Model S/X launcher captures that loopback directly because it has no standalone AudioWeaver.
if [ "$JOB" = a2dpbridge ]; then
    echo "qtcar-service: a2dpbridge (alsaloop a2dp_in_loop -> a2dp_out_loop)" >&2
    exec env -i PATH="$PATH" LC_ALL=C /usr/bin/alsaloop -v -p 35 -C a2dp_in_loop -P a2dp_out_loop -U -S1 -l 6144
fi
# The S/X audiod's other media sources, as /etc/sv/alsaloop-chromium/run and media1bridge/run:
# Chromium's web media and the media server's player play into "virtual" loopbacks, alsaloop
# copies them into AudioWeaver's inputs. audiod starts them through the escalator (stub -> sv).
if [ "$JOB" = alsaloop-chromium ]; then
    echo "qtcar-service: alsaloop-chromium (chromium_media_loop -> app_out_media)" >&2
    exec env -i PATH="$PATH" LC_ALL=C /usr/bin/alsaloop -vv -p 35 -U -S1 -l 6144 -C chromium_media_loop -P app_out_media
fi
if [ "$JOB" = media1bridge ]; then
    IN=$(cat /var/run/media1bridge/pcm_in 2>/dev/null)
    echo "qtcar-service: media1bridge (${IN:-media1_loop_in} -> media1_out_loop)" >&2
    exec env -i PATH="$PATH" LC_ALL=C /usr/bin/alsaloop -vv -p 35 -C "${IN:-media1_loop_in}" -P media1_out_loop -U -S1 -l 6144
fi

# Bluetooth (./tesla start --bluetooth, see bluetooth/README.md): the firmware's own system bus,
# Broadcom's BSA stack on the "UART" /dev/ttyS0 (on a PC: bluetooth/hci-bridge.py's pseudo
# terminal, bound there), and Tesla's btd on top, which QtCarBluetooth reaches over D-Bus.
if [ "$JOB" = dbus ]; then
    mkdir -p /var/run/dbus
    rm -f /var/run/messagebus.pid /var/run/dbus/pid /var/run/dbus/system_bus_socket
    echo "qtcar-service: dbus -> dbus-daemon --system (the firmware's own bus)" >&2
    exec /usr/bin/dbus-daemon --nofork --system
fi
if [ "$JOB" = bsa_server ]; then
    mkdir -p /var/run/bsa_server
    rm -f /var/run/bsa_server/bt-avk-fifo /var/run/bsa_server/bt-daemon-socket
    echo "qtcar-service: bsa_server on /dev/ttyS0 ${BSA_ARGS:-}" >&2
    # no -p (Broadcom firmware patch) unless the controller is the car's BCM4349. Not with env -i:
    # it segfaults with an empty environment.
    exec /usr/bin/bsa_server -d /dev/ttyS0 -u /var/run/bsa_server/ ${BSA_ARGS:-}
fi
if [ "$JOB" = btd ]; then
    # it needs the bus (without it, it runs but QtCarBluetooth never finds it) and BSA's socket
    for i in $(seq 100); do
        [ -S /var/run/dbus/system_bus_socket ] && [ -S /var/run/bsa_server/bt-daemon-socket ] && break
        sleep 0.2
    done
    mkdir -p /var/lib/btd /var/run/btd
    cd /var/run/btd || exit 1
    echo "qtcar-service: btd" >&2
    exec /usr/bin/btd
fi

# Wi-Fi (./tesla start --wifi): the car's stack on the PC's adapter, as /etc/sv/wpa_supplicant
# and /etc/sv/connman (minus sandbox and firewall). wpa_supplicant only listens on D-Bus; connmand
# asks it for the adapter (wlan0: QtCarConnMan only knows that name, ./tesla start renames the
# PC's adapter; the only interface connmand manages, the car's own -I eth0 would let it take the
# PC's Ethernet too) and serves QtCarConnMan (qtcar-connman), which QtCar's Wi-Fi settings talk
# to. -r: no DNS proxy, resolv.conf gets the network's servers (still valid after the run).
# Saved networks: /var/lib/connman in the image.
wait_bus() {
    for i in $(seq 100); do [ -S /var/run/dbus/system_bus_socket ] && return; sleep 0.2; done
}
if [ "$JOB" = wpa_supplicant ]; then
    wait_bus
    mkdir -p /var/run/wpa_supplicant
    echo "qtcar-service: wpa_supplicant (D-Bus only)" >&2
    exec /usr/sbin/wpa_supplicant -u -O /var/run/wpa_supplicant
fi
if [ "$JOB" = connman ]; then
    wait_bus
    # connmand gives up on Wi-Fi if wpa_supplicant isn't on the bus yet
    for i in $(seq 50); do
        dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.ListNames 2>/dev/null |
            grep -q fi.w1.wpa_supplicant1 && break
        sleep 0.2
    done
    mkdir -p /var/lib/connman /var/run/connman
    # Retain ConnMan's broker/worker privilege split. The profile patch only
    # skips its worker's unavailable car-specific AppArmor hat.
    echo "qtcar-service: connmand -i ${WIFI_DEV:-wlan0}" >&2
    exec /usr/sbin/connmand -n -r -i "${WIFI_DEV:-wlan0}"
fi

# qtcar_env VAR: a variable from QtCar's environment (its display, X cookie, GL settings), for the
# programs QtCar has the escalator start on its screen (videos, games)
qtcar_env() {
    p=$(pidof QtCar | cut -d' ' -f1)
    [ -n "$p" ] && tr '\0' '\n' <"/proc/$p/environ" 2>/dev/null | sed -n "s/^$1=//p"
}

# gl_env: QtCar's GL settings as VAR=value words. The Model 3's Mesa is in place (only software or
# the driver to pick); the 2026 S/X uses the kit's Mesa in /opt/mesa next to the firmware's, so its
# library and loader paths come along too (without QtCar: the kit's Mesa in software).
gl_env() {
    if [ -f /opt/mesa/env.sh ]; then
        vars="LD_LIBRARY_PATH LIBGL_ALWAYS_SOFTWARE LIBGL_DRIVERS_PATH GBM_BACKENDS_PATH __EGL_VENDOR_LIBRARY_FILENAMES MESA_LOADER_DRIVER_OVERRIDE"
        if [ -z "$(pidof QtCar)" ]; then
            echo "LD_LIBRARY_PATH=/opt/mesa/lib:/usr/tesla/UI/lib:/usr/lib:/lib:/usr/lib/engines-3 LIBGL_ALWAYS_SOFTWARE=1" \
                "LIBGL_DRIVERS_PATH=/opt/mesa/lib/dri __EGL_VENDOR_LIBRARY_FILENAMES=/opt/mesa/egl_vendor.d/50_mesa.json"
            return
        fi
    else
        # only what QtCar has: an empty MESA_LOADER_DRIVER_OVERRIDE would name driver ""
        vars="LIBGL_ALWAYS_SOFTWARE MESA_LOADER_DRIVER_OVERRIDE"
    fi
    for v in $vars; do
        val=$(qtcar_env $v); [ -n "$val" ] && printf '%s=%s ' "$v" "$val"
    done
}

# The games (Toybox -> Arcade), as /etc/sv/{mame,cobalt,cobalt-input}/run minus sandbox, AppArmor,
# CPU pinning (game-mode.sh would also stop services such as valhalla). QtCar asks the escalator,
# which runs "sv start mame|cobalt" (the kit's /sbin/sv wrapper sends that here).
#   mame: the arcade classics (Asteroids, Missile Command, ...), /usr/bin/mametesla, game name in
#         /home/mame/game (QtCar writes it)
#   cobalt: Beach Buggy Racing 2 (/usr/bin/games/cobalt/CobaltLinux), window and car colour from
#         /tmp/games/cobalt/tidk/TIDK_*; cobalt-input (input_to_virtual) turns /dev/input/touch into
#         the virtual device game-touch (uinput), which the game finds in /dev/input
case "$JOB" in mame|cobalt|cobalt-input)
    # ./tesla start gives every service QtCar's display (TESLA_MEDIA_*)
    DISP=${TESLA_MEDIA_DISPLAY:-$(qtcar_env DISPLAY)}; XAUTH=${TESLA_MEDIA_XAUTHORITY:-$(qtcar_env XAUTHORITY)}
    # the game window's place and the car's colour etc., written by QtCar; input_to_virtual needs
    # the window too (it maps the touches into it)
    TIDK=""
    for f in /tmp/games/cobalt/tidk/TIDK_*; do [ -f "$f" ] && TIDK="$TIDK ${f##*/}=$(cat "$f")"; done
    GLENV=$(gl_env)
    ;;
esac
if [ "$JOB" = mame ]; then
    export HOME=/home/mame
    mkdir -p $HOME /var/run/mame
    [ -e $HOME/game ] || echo missile >$HOME/game
    touch /var/run/mame/polepos.state && chmod 644 /var/run/mame/polepos.state
    chown -hR mame:mame $HOME /var/run/mame
    game=$(cat $HOME/game)
    echo "qtcar-service: mame -> mametesla $game (DISPLAY ${DISP:-:1})" >&2
    # VNC: the X pointer is the finger (tesla-touch follows it), and SDL MAME grabs the mouse
    # (mame.ini: mouse 1), which pinned the pointer to its window: no touches. The trackball
    # games still get QtCar's keys (trackball_device keyboard).
    MOUSE=""; [ "${TESLA_MEDIA_SCREEN:-}" = vnc ] && MOUSE=-nomouse
    as_user mame env -i PATH="$PATH" HOME=$HOME DISPLAY="${DISP:-:1}" XAUTHORITY="$XAUTH" $GLENV LC_ALL=C \
        /usr/bin/mametesla -inipath /opt/mame/ $MOUSE "$game"
fi
if [ "$JOB" = cobalt-input ]; then
    mkdir -p /home/games/cobalt-input /tmp/games/cobalt-input
    echo "qtcar-service: cobalt-input -> input_to_virtual" >&2
    cd /usr/bin/games/cobalt || exit 1
    # as root: it needs /dev/uinput and /dev/input/touch (on the car its sandbox grants them)
    exec env -i PATH="$PATH" LD_LIBRARY_PATH=/usr/bin/games/cobalt/ $TIDK LC_ALL=C ./input_to_virtual
fi
if [ "$JOB" = cobalt ]; then
    mkdir -p /home/games/cobalt /tmp/games/cobalt /home/tesla/.Tesla/data/cobalt
    chown -hR cobalt:games /home/games/cobalt /tmp/games/cobalt /home/tesla/.Tesla/data/cobalt
    # the virtual input devices, made through uinput: game-touch by input_to_virtual, game-steering
    # and game-scroll-left/right by QtCarVehicle when GUI_vehicleGameMode turns on (from
    # VAPI_steeringAngle, VAPI_brakePedal, STW_*). The game finds them by name in /sys/class/input;
    # the chroot's /dev/input is a tmpfs, so make their nodes as they appear, while the game runs
    # (this shell's pid becomes the game's: as_user execs). input_to_virtual ends with the game.
    pkill -f '^\./input_to_virtual'
    /usr/local/bin/qtcar-service cobalt-input </dev/null >>/tmp/games/cobalt-input.log 2>&1 &
    ( while kill -0 $$ 2>/dev/null; do
        for d in /sys/class/input/event*; do
            [ -r "$d/dev" ] || continue
            n=/dev/input/${d##*/}; [ -e "$n" ] && continue
            name=$(cat "$d/device/name" 2>/dev/null)
            case "$name" in game-*) ;; *) continue ;; esac     # not the host's keyboard etc.
            mknod "$n" c "$(cut -d: -f1 "$d/dev")" "$(cut -d: -f2 "$d/dev")" && chgrp games "$n" && chmod 660 "$n" &&
                echo "qtcar-service: cobalt: $n = $name" >&2
        done
        sleep 0.5
    done
    pkill -f '^\./input_to_virtual'
    rm -f /dev/input/event* ) &      # the next game's devices may get other numbers
    sleep 1
    echo "qtcar-service: cobalt -> CobaltLinux (DISPLAY ${DISP:-:1}, $TIDK)" >&2
    cd /usr/bin/games/cobalt || exit 1
    as_user cobalt:games env -i PATH="$PATH" HOME=/home/games/cobalt DISPLAY="${DISP:-:1}" XAUTHORITY="$XAUTH" \
        $GLENV $TIDK LD_LIBRARY_PATH=/usr/bin/games/cobalt/ FMOD_ALSA_DEVICE=game LC_ALL=C \
        ./CobaltLinux --RootPath /usr/bin/games/cobalt/
fi

# The 2026 S/X's built-in games (Arcade: 2048, Chess, Backgammon), as /etc/sv/<name>/run with
# app-service.bash, minus minijail, cgroups and kafel. QtCar writes the game's window and the
# car's look to /opt/games/run/tidk/<name>/tidk.vars (TIDK_*=...) before it asks the escalator
# for start-tesla-game-<name>; the game draws into its own X window, which QtCar shows in the
# Arcade, and reads /dev/input/touch itself (the profile patches its compiled-in car panel
# transform to QtCar's). The firmware's <name>-input job (input-to-virtual) isn't needed.
# The S shim (preloaded into QtCar too): the game asks the X display ":0.0" for the center
# display's size (its layout); here that's QtCar's DISPLAY.
case "$JOB" in 2048|chess|backgammon)
    case "$JOB" in
        2048) GUSER=game2048 BIN=/usr/tesla/UI/standalone_apps/2048/2048 ;;
        chess) GUSER=chess BIN=/usr/tesla/UI/standalone_apps/Chess/Chess ;;
        backgammon) GUSER=backgammon BIN=/usr/tesla/UI/standalone_apps/Backgammon/Backgammon ;;
    esac
    GHOME=/home/$GUSER
    # the shared settings.conf (the kit's [display_state]: else its touch driver stays closed)
    mkdir -p "$GHOME/.Tesla/data" "$GHOME/.Tesla/car" "/opt/games/var/$JOB"
    [ -L "$GHOME/.Tesla/car/settings.conf" ] || { rm -f "$GHOME/.Tesla/car/settings.conf"
        ln -s /home/tesla/.Tesla/car/settings.conf "$GHOME/.Tesla/car/settings.conf"; }
    chown -hR "$GUSER:$GUSER" "$GHOME" "/opt/games/var/$JOB"
    TIDK=""
    while IFS= read -r line; do
        case "$line" in TIDK_*=*) TIDK="$TIDK $line" ;; esac
    done <"/opt/games/run/tidk/$JOB/tidk.vars" 2>/dev/null
    DISP=${TESLA_MEDIA_DISPLAY:-$(qtcar_env DISPLAY)}; XAUTH=${TESLA_MEDIA_XAUTHORITY:-$(qtcar_env XAUTHORITY)}
    SHIM=""; [ -f /usr/lib/egl_media_pixmap_shim.so ] && SHIM=LD_PRELOAD=/usr/lib/egl_media_pixmap_shim.so
    echo "qtcar-service: $JOB -> $BIN (DISPLAY ${DISP:-:1},$TIDK)" >&2
    cd /usr/tesla/UI/standalone_apps || exit 1
    # gl_env: QtCar's GL and library path; the group input for /dev/input/touch
    as_user "$GUSER:$GUSER:input" env -i PATH="$PATH" HOME="$GHOME" USER="$GUSER" DISPLAY="${DISP:-:1}" \
        XAUTHORITY="$XAUTH" LC_ALL=C LANG=C QT_NO_GLIB=1 $(gl_env) $SHIM $TIDK "$BIN"
    ;;
esac

# The videos of fireplace / dog mode / HAL 9000 (as /etc/sv/<name>/run, minus the sandbox):
# /usr/bin/tvideo as user tvideo. QtCar asks the escalator, which runs "sv start <name>"
# (the kit's /sbin/sv wrapper sends that here). DISPLAY: QtCar's, not the car's :0.
case "$JOB" in fireplace|dog-mode|hal-9000|camp-mode|camp-mode-holiday)
    V=file:///usr/tesla/UI/assets/videos
    QTCAR_PID=$(pidof QtCar | cut -d' ' -f1)
    if [ -f /etc/tvideo.env ]; then
        # The 2026 S/X firmware: tvideo is a GStreamer player (GL sink); its jobs (/etc/sv/<name>/run)
        # place the dog/camp mode windows for the portrait screen. No gst-register job here: the
        # plugin registry is built in tvideo's home on the first start. GL as QtCar has it (gl_env).
        # The sink: without the car's VA-API, playbin took glimagesink, which opened its own
        # "OpenGL renderer" window instead of drawing into tvideo's; QtCar (window manager and
        # compositor here) parked that one offscreen at 1x1 and the view stayed at "loading".
        # ximagesink draws into tvideo's window.
        SINK=GST_PLUGIN_FEATURE_RANK=ximagesink:MAX,glimagesink:NONE,vaapisink:NONE,xvimagesink:NONE
        case "$JOB" in
            fireplace) ARGS="-l -s 1.0 $V/fireplace.mp4" ;;
            dog-mode) ARGS="-l -n dog-mode -x 200 -y 1100 -s 1.0 $V/dog-mode.mp4" ;;
            *) ARGS="-l -n $JOB -x 0 -y 0 -s 1.0 $V/$JOB.mp4" ;;
        esac
        DISP=${TESLA_MEDIA_DISPLAY:-$(qtcar_env DISPLAY)}
        XAUTH=${TESLA_MEDIA_XAUTHORITY:-$(qtcar_env XAUTHORITY)}
        GLENV=$(gl_env)
        mkdir -p /home/tvideo && chown -hR tvideo:tvideo /home/tvideo 2>/dev/null
        echo "qtcar-service: $JOB -> tvideo $ARGS (DISPLAY ${DISP:-:1})" >&2
        as_user tvideo env -i PATH="$PATH" HOME=/home/tvideo DISPLAY="${DISP:-:1}" XAUTHORITY="$XAUTH" LC_ALL=C \
            $GLENV $SINK GST_GL_PLATFORM=egl GST_REGISTRY_1_0=/home/tvideo/gst-registry.bin \
            GST_PLUGIN_SYSTEM_PATH_1_0=/usr/lib/gstreamer-1.0 \
            GST_PLUGIN_PATH_1_0=/usr/lib/gstreamer-1.0:/usr/tesla/UI/lib/gst_plugin \
            /usr/bin/tvideo $ARGS
    fi
    case "$JOB" in
        fireplace) ARGS="-l $V/fireplace.mp4" ;;
        dog-mode) ARGS="-l -n dog-mode -x 0 -y 0 $V/dog-mode.mp4" ;;
        hal-9000) ARGS="-l -n hal-9000 -c $V/hal-9000.mp4" ;;
    esac
    DISP=$(tr '\0' '\n' <"/proc/$QTCAR_PID/environ" 2>/dev/null | sed -n 's/^DISPLAY=//p')
    XAUTH=$(tr '\0' '\n' <"/proc/$QTCAR_PID/environ" 2>/dev/null | sed -n 's/^XAUTHORITY=//p')
    mkdir -p /home/tvideo && chown -hR tvideo:tvideo /home/tvideo 2>/dev/null
    echo "qtcar-service: $JOB -> tvideo $ARGS (DISPLAY ${DISP:-:1})" >&2
    as_user tvideo env -i PATH="$PATH" HOME=/home/tvideo DISPLAY="${DISP:-:1}" XAUTHORITY="$XAUTH" LC_ALL=C \
        /usr/bin/tvideo $ARGS
    ;;
esac

export QCSUBNET=127.0.0 QCCID=1 QCIC=1 QCGW=1 QCAP=1 QCLB=1

if [ "$JOB" = qtcar-sim ]; then
    # No runit job on the car (dev tool). Without options it talks to 127.0.0.1 / 127.255.255.255
    # like everything here: CAN frames to :1234, SIM_* data values over HTTP on port 4190.
    . /etc/tesla.env
    cd "$TESLA_BIN" || exit 1
    echo "qtcar-service: qtcar-sim -> QtCarSimService $*" >&2
    exec env -i PATH="$PATH" HOME=/root LD_LIBRARY_PATH="$TESLA_LIB:/usr/cid-lib:/usr/lib64:/lib64:/lib:/usr/lib" \
        LC_ALL=C LANG=C ./QtCarSimService "$@"
fi

# QtCarStartup (the 2026 firmware's qtcar-startup job, root with group dvshm): creates the shared
# memory data values (DVSM_*) everyone else attaches to. First what its run script prepares.
if [ "$JOB" = qtcar-startup ]; then
    mkdir -p /home/tesla/.Tesla/car /home/tesla/.Tesla/cache /home/tesla/.Tesla/data /home/tesla/.Tesla/tmp
    chown -h tesla:tesla /home/tesla /home/tesla/.Tesla /home/tesla/.Tesla/car /home/tesla/.Tesla/cache \
        /home/tesla/.Tesla/data /home/tesla/.Tesla/tmp
    mkdir -p /tmp/dvaccess /tmp/dbus_dvaccess /var/run/cdtickwatchdog /var/run/uptime
    chmod 770 /tmp/dvaccess /tmp/dbus_dvaccess
    chown -h tesla:dvaccess /tmp/dvaccess
    chown -h qtdvserver:dbus_dvaccess /tmp/dbus_dvaccess 2>/dev/null
    chown -h tesla:monitord /var/run/cdtickwatchdog 2>/dev/null && chmod 2770 /var/run/cdtickwatchdog
fi

cd "/etc/sv/$JOB" || exit 1    # RunQtCar.vars takes the job name from $PWD
. /etc/tesla.env
. /etc/RunQtCar.vars
export TESLA_IP=127.0.0.1 TESLA_IC=127.0.0.1 TESLA_GW=127.0.0.1
export LD_LIBRARY_PATH=$TESLA_LIB:/usr/cid-lib:/usr/lib64:/lib64:/lib:/usr/lib
export LC_ALL=C LANG=C LANGUAGE=C
# the 2026 S/X jobs name the car's X screen (DISPLAY=:0.0, e.g. the media server's video player:
# "setMedia: failed to open display"); here it's QtCar's, which ./tesla start passes on
if [ -n "${DISPLAY:-}" ] && [ -n "${TESLA_MEDIA_DISPLAY:-}" ]; then
    export DISPLAY=$TESLA_MEDIA_DISPLAY
    [ -n "${TESLA_MEDIA_XAUTHORITY:-}" ] && export XAUTHORITY=$TESLA_MEDIA_XAUTHORITY
fi

[ -n "$QCAPP" ] || { echo "qtcar-service: $JOB is not a QtCar service" >&2; exit 1; }

# On the 2026 S/X image the firmware's QtCarBluetooth runit job enters a
# sandbox as qtbt. RunQtCar.vars leaves QCUSER at its default tesla, which
# cannot own com.tesla.Bluetooth/Phone on the system bus. The chroot omits
# that sandbox, so select the same service user and its IPC groups here.
if [ "$JOB" = qtcar-bluetooth ] && [ "${UI_PLATFORM:-}" = SX ]; then
    QCUSER=qtbt
    QCGROUPS=:dvshm:qtbt:tesla:dvaccess:audio:infohealthd
fi

# Services with their own user (gpsmanager, qtaudiod, mediaserver, ...) keep their settings DB
# and .watches in $HOME/.Tesla. On the car their minijail mounts their own home writable; here
# chpst would leave HOME=/home/tesla, which they can't write. Then a car config change (e.g.
# VAPI_driverAssist from the simulator) is never stored, and QtCarGpsManager restarted itself
# every ~13 s ("Restarting ... because of changed DVs") until one restart hung on exit.
# So: HOME = the user's home, with its .Tesla dirs and a link to the shared settings.conf.
if [ -n "$QCUSER" ] && [ "$QCUSER" != tesla ] && [ -z "$QCSVC_NOUSER" ] && [ "$(id -u)" = 0 ]; then
    UHOME=$(awk -F: -v u="$QCUSER" '$1 == u {print $6}' /etc/passwd)
    if [ "$UHOME" = / ]; then
        # qtbt (QtCarBluetooth) has "/" as its home. On the car its minijail mounts /home/tesla
        # writable and it keeps PhonebookV2.db, BTDevices.db there, where QtCar reads the contacts
        # (with HOME=/ they went to /.Tesla/data and the phone app stayed at "Loading...").
        # It's in group tesla: make tesla's data directory and files group-writable (SQLite also
        # needs the directory for its journal).
        chgrp tesla "$TESLA_HOME/.Tesla/data" && chmod g+ws "$TESLA_HOME/.Tesla/data"
        for f in "$TESLA_HOME"/.Tesla/data/*.db "$TESLA_HOME"/.Tesla/data/*.db-journal; do
            [ -e "$f" ] && chgrp tesla "$f" && chmod g+w "$f"
        done
        # contacts stored under /.Tesla by an earlier run: move them over (only if newer)
        for f in PhonebookV2.db BTDevices.db QtCarBluetoothSettings.db; do
            [ -f "/.Tesla/data/$f" ] && [ "/.Tesla/data/$f" -nt "$TESLA_HOME/.Tesla/data/$f" ] &&
                mv -f "/.Tesla/data/$f" "$TESLA_HOME/.Tesla/data/$f" && chgrp tesla "$TESLA_HOME/.Tesla/data/$f" &&
                chmod g+w "$TESLA_HOME/.Tesla/data/$f"
        done
        export HOME=$TESLA_HOME
    elif [ -n "$UHOME" ] && [ -d "$UHOME" ]; then
        mkdir -p "$UHOME/.Tesla/car" "$UHOME/.Tesla/data"
        [ -e "$UHOME/.Tesla/car/settings.conf" ] || ln -s "$TESLA_HOME/.Tesla/car/settings.conf" "$UHOME/.Tesla/car/settings.conf"
        chown -h "$QCUSER" "$UHOME/.Tesla" "$UHOME/.Tesla/car" "$UHOME/.Tesla/data" "$UHOME/.Tesla/car/settings.conf"
        export HOME=$UHOME
    fi
fi
# QtCar itself runs without address options and then uses local broadcast (127.255.255.255) for
# its data. With --gw/--ip/... the services switch to the car network's multicast (224.0.0.26)
# instead and QtCar never hears them (e.g. TMServer -> no navigation). Keep only the ports.
QCOPTS=$(echo " $QCOPTS " | sed -E 's/ --(gw|ip|ic|ap|apb|lb) +[0-9.]+/ /g')

# like /etc/sv/qtcar-audiod/run
[ "$JOB" = qtcar-audiod ] && QCOPTS="$QCOPTS --audio-type $(audio_type) --audio-platform $(audio-type -p)"

cd "$TESLA_BIN" || exit 1
echo "qtcar-service: $JOB -> $QCAPP $QCOPTS $* (user ${QCUSER:-root})" >&2
# QCGROUPS is ":grp:grp" for most jobs, but "tesla" (no colon) for qtcar-bluetooth
case "$QCGROUPS" in ""|:*) ;; *) QCGROUPS=":$QCGROUPS" ;; esac
# minijail jobs name no groups: on the car minijail gives the user its supplementary groups from
# /etc/group (see RunQtCar.vars), chpst doesn't. The 2026 firmware's services meet in
# /tmp/dvaccess (group dvaccess, 770).
if [ -z "$QCGROUPS" ] && [ "$QCUSEMINIJAIL" = true ] && [ -n "$QCUSER" ]; then
    QCGROUPS=":$QCUSER$(awk -F: -v u="$QCUSER" '{n = split($4, m, ","); for (i = 1; i <= n; i++) if (m[i] == u) printf ":%s", $1}' /etc/group)"
fi
# dvshm (the 2026 firmware) first, as the primary group: the shared memory data values (DVSM_*)
# are SysV objects with mode 660 in their creator's group, shared by processes of several users
case "$QCGROUPS" in *:dvshm*) QCGROUPS=":dvshm$(echo "$QCGROUPS" | sed 's/:dvshm\(:\|$\)/\1/')" ;; esac
as_user "$QCUSER$QCGROUPS" "./$QCAPP" $QCOPTS "$@"
