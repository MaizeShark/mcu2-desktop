#!/bin/sh
# Direct equivalents of the 2026 S/X media-webapp-server and chromium-app runit jobs.
# Run by qtcar-service in the chroot. The firmware's scripts require minijail, cgroup v1
# and runit; these services use the existing chroot, system bus and QtCar X display.
set -eu

job=$1
export PATH=/bin:/usr/bin:/sbin:/usr/sbin:/usr/local/bin

qtcar_env() {
    case "$1" in
        DISPLAY) [ -n "${TESLA_MEDIA_DISPLAY:-}" ] && { printf '%s\n' "$TESLA_MEDIA_DISPLAY"; return; } ;;
        XAUTHORITY) [ -n "${TESLA_MEDIA_XAUTHORITY:-}" ] && { printf '%s\n' "$TESLA_MEDIA_XAUTHORITY"; return; } ;;
    esac
    pid=$(pidof QtCar | cut -d' ' -f1)
    [ -n "$pid" ] && tr '\0' '\n' <"/proc/$pid/environ" 2>/dev/null | sed -n "s/^$1=//p" || :
}

as_media_user() {
    user=$1; groups=$2; shift 2
    if [ "${QCSVC_NOUSER:-}" = 1 ] || [ "$(id -u)" != 0 ]; then exec "$@"; fi
    exec chpst -u "$user:$groups" "$@"
}

case "$job" in
media-webapp-server)
    cpu=$(/sbin/whatami --cpu 2>/dev/null || :)
    chassis=$(cat /var/etc/chassistype 2>/dev/null || :)
    [ -n "$cpu" ] || cpu=intel
    [ -n "$chassis" ] || chassis=models
    version=$(cat /usr/tesla/UI/bin/customerVersion.txt)
    echo "tesla-media-service: web apps on 127.0.0.1:9000 ($cpu, $chassis, $version)" >&2
    exec /usr/bin/media-webapp-server \
        -is_development_car=false -is_ebuck=false -cache_exp_secs=900 \
        -platform_type="$cpu" -chassis_type="$chassis" -firmware_version="$version" \
        -bind=127.0.0.1 -port=9000 -dir=/opt/media-webapp/ \
        -oauth_reverse_proxy=https://activate.apple.com/ \
        -qq_music_api_reverse_proxy=https://qplaycloud.y.qq.com/rpc_proxy/fcgi-bin/music_open_api.fcg \
        -qq_music_report_reverse_proxy=https://stat.y.qq.com/open/fcgi-bin/music_monitor_api.fcg
    ;;
dbus-session-chromium-app|dbus-session-chromium|dbus-session-chromium-fullscreen)
    user=chromium-app
    [ "$job" = dbus-session-chromium ] && user=chromium
    [ "$job" = dbus-session-chromium-fullscreen ] && user=chromium-fullscreen
    busdir=/run/service/$user/.dbus/session-bus
    mkdir -p "$busdir"
    chown -hR "$user:$user" "/run/service/$user"
    address=unix:path="$busdir/socket"
    printf '%s\n' "$address" >"$busdir/address"
    chown "$user:$user" "$busdir/address"
    rm -f "$busdir/socket"   # stale socket after an interrupted run
    echo "tesla-media-service: Chromium session bus $address" >&2
    as_media_user "$user" "$user:chromium-dbus" \
        /usr/bin/dbus-daemon --session --nofork --address="$address"
    ;;
chromium-adapter)
    home=/home/chromium-adapter
    mkdir -p "$home/.Tesla/car" "$home/.Tesla/data"
    [ -e "$home/.Tesla/car/settings.conf" ] ||
        ln -s /home/tesla/.Tesla/car/settings.conf "$home/.Tesla/car/settings.conf"
    chown -hR chromium-adapter:chromium-adapter "$home/.Tesla"
    cd /usr/tesla/UI/bin
    echo "tesla-media-service: ChromiumAdapter (system D-Bus, websocket :9001)" >&2
    as_media_user chromium-adapter chromium-adapter:dvaccess \
        env -i PATH="$PATH" HOME="$home" LD_LIBRARY_PATH=/usr/tesla/UI/lib:/usr/cid-lib:/usr/lib64:/lib64:/lib:/usr/lib \
        QT_NO_GLIB=1 LC_ALL=C LANG=C ./ChromiumAdapter
    ;;
chromium-app-input|chromium-input|chromium-fullscreen-input)
    display=$(qtcar_env DISPLAY); [ -n "$display" ] || display=:1.0
    xauth=$(qtcar_env XAUTHORITY)
    geometry=/opt/games/run/i2v/chromium-app.touch-ChromiumApp-cid.geometry
    name=game-chromium-touch
    if [ "$job" = chromium-input ]; then
        geometry=/opt/games/run/i2v/chromium.touch-Chromium-cid.geometry
        name=game-browser-touch
    elif [ "$job" = chromium-fullscreen-input ]; then
        geometry=/opt/games/run/i2v/theater.touch-Theater-cid.geometry
        name=game-theater-touch
    fi
    config=/run/teslamcu-$job.conf
    # tesla-touch already reports QtCar screen coordinates. The car's input config assumes
    # a 4096-unit sensor and rotates/inverts it. Keep the virtual device's native
    # 1200x1920 axes so VNC taps land in the Chromium browse rectangle.
    sed -e "s/display=:0/display=$display/g" \
        -e 's/ range=4096//' \
        -e 's/rotate=true/rotate=false/g' -e 's/invertx=true/invertx=false/g' \
        /usr/share/input-to-virtual/input-devices.conf >"$config"
    mkdir -p /opt/games/run/i2v
    touch "$geometry"
    chown tesla:input-to-virtual "$geometry"
    chmod 664 "$geometry"
    # Host touch bridges handle native and VNC. Keep input-to-virtual for QtCar's
    # geometry file, but hide its virtual device from the host's XInput on both
    # displays. Otherwise a keyboard tap can also reach the offscreen Browser and
    # blur its address field.
    echo "tesla-media-service: chromium touch input on $display ($name, 1200x1920)" >&2
    # The firmware creates this virtual touchscreen from the same source QtCar reads.
    # Explicit dimensions and a display-adjusted source config keep VNC on :1 isolated
    # from the PC's :0 session; the car's stock config hard-codes :0.
    exec env -i PATH="$PATH" LC_ALL=C XAUTHORITY="$xauth" \
        /usr/bin/input-to-virtual -C "$config" -g "$geometry" \
        -w 1200 -h 1920 -n "$name" -m 10
    ;;
chromium-app)
    busfile=/run/service/chromium-app/.dbus/session-bus/address
    i=0
    while [ ! -s "$busfile" ] && [ "$i" -lt 100 ]; do sleep 0.1; i=$((i + 1)); done
    [ -s "$busfile" ] || { echo 'tesla-media-service: session bus missing' >&2; exit 1; }
    home=/home/chromium-app
    mkdir -p "$home/.config/chromium" "$home/.cache"
    chown -hR chromium-app:chromium-app "$home"
    display=$(qtcar_env DISPLAY); [ -n "$display" ] || display=:1.0
    xauth=$(qtcar_env XAUTHORITY)
    width=$(DISPLAY="$display" XAUTHORITY="$xauth" xwininfo -root 2>/dev/null | awk '/Width:/ {print $2; exit}')
    [ -n "$width" ] || width=1200
    cd /usr/lib/tesla-chromium
    echo "tesla-media-service: ChromiumApp on $display (offscreen at x=$width)" >&2
    as_media_user chromium-app chromium-app:chromium-dbus:input:video:audio:pipewire \
        env -i PATH="$PATH" HOME="$home" USER=chromium-app DISPLAY="$display" XAUTHORITY="$xauth" \
        DBUS_SESSION_BUS_ADDRESS="$(cat "$busfile")" \
        DBUS_SYSTEM_BUS_ADDRESS=unix:path=/var/run/dbus/system_bus_socket \
        LD_LIBRARY_PATH=/usr/lib/tesla-chromium:/opt/mesa/lib \
        LIBGL_DRIVERS_PATH=/opt/mesa/lib/dri LIBGL_ALWAYS_SOFTWARE=1 \
        __EGL_VENDOR_LIBRARY_FILENAMES=/opt/mesa/egl_vendor.d/50_mesa.json \
        FONTCONFIG_FILE=/etc/fonts/fonts.conf FONTCONFIG_PATH=/etc/fonts \
        LC_ALL=C LANG=C PIPEWIRE_RUNTIME_DIR=/var/run/pipewire PIPEWIRE_CONFIG_NAME=client.conf \
        ./tesla-chromium --no-sandbox --disable-setuid-sandbox \
        --ozone-platform=x11 --use-gl=angle --use-angle=gl \
        --remote-debugging-address=127.0.0.1 --remote-debugging-port=9223 \
        --disable-status-bubble --disable-tab-strip --disable-toolbar --disable-location-bar \
        --no-first-run --no-default-browser-check --enable-logging=stderr --log-level=0 \
        --user-data-dir="$home/.config/chromium" --window-title=ChromiumApp \
        --window-position="$width,0" --supports-active-window \
        --user-agent-suffix=" TeslaMusic/$(cat /etc/product-release)" \
        --unsafely-treat-insecure-origin-as-secure=http://firmware-media.vn.teslamotors.com:9000 \
        --alsa-input-device=plug:chromium_voice --alsa-output-device=plug:chromium_app \
        --autoplay-policy=no-user-gesture-required \
        --policy-config-dir=/etc/chromium/policies/chromium-app,/run/chromium/policies/chromium-app \
        --tesla-dbus-service-name=com.tesla.ChromiumApp \
        --tesla-dbus-interface-name=com.tesla.ChromiumApp \
        --tesla-dbus-object-path=/ChromiumApp \
        --tesla-dbus-observe-loading --tesla-dbus-observe-navigation \
        http://firmware-media.vn.teslamotors.com:9000/
    ;;
chromium)
    busfile=/run/service/chromium/.dbus/session-bus/address
    i=0
    while [ ! -s "$busfile" ] && [ "$i" -lt 100 ]; do sleep 0.1; i=$((i + 1)); done
    [ -s "$busfile" ] || { echo 'tesla-media-service: browser session bus missing' >&2; exit 1; }
    home=/home/chromium
    mkdir -p "$home/.config/chromium" "$home/.cache"
    chown -hR chromium:chromium "$home"
    display=$(qtcar_env DISPLAY); [ -n "$display" ] || display=:1.0
    xauth=$(qtcar_env XAUTHORITY)
    width=$(DISPLAY="$display" XAUTHORITY="$xauth" xwininfo -root 2>/dev/null | awk '/Width:/ {print $2; exit}')
    [ -n "$width" ] || width=1200
    cd /usr/lib/tesla-chromium
    echo "tesla-media-service: Browser on $display (offscreen at x=$width)" >&2
    as_media_user chromium chromium:chromium-dbus:input:video:audio:pipewire \
        env -i PATH="$PATH" HOME="$home" USER=chromium DISPLAY="$display" XAUTHORITY="$xauth" \
        DBUS_SESSION_BUS_ADDRESS="$(cat "$busfile")" \
        DBUS_SYSTEM_BUS_ADDRESS=unix:path=/var/run/dbus/system_bus_socket \
        LD_LIBRARY_PATH=/usr/lib/tesla-chromium:/opt/mesa/lib \
        LIBGL_DRIVERS_PATH=/opt/mesa/lib/dri LIBGL_ALWAYS_SOFTWARE=1 \
        __EGL_VENDOR_LIBRARY_FILENAMES=/opt/mesa/egl_vendor.d/50_mesa.json \
        FONTCONFIG_FILE=/etc/fonts/fonts.conf FONTCONFIG_PATH=/etc/fonts \
        LC_ALL=C LANG=C PIPEWIRE_RUNTIME_DIR=/var/run/pipewire PIPEWIRE_CONFIG_NAME=client.conf \
        ./tesla-chromium --no-sandbox --disable-setuid-sandbox \
        --ozone-platform=x11 --use-gl=angle --use-angle=gl \
        --remote-debugging-address=127.0.0.1 --remote-debugging-port=9224 \
        --no-first-run --no-default-browser-check --enable-logging=stderr --log-level=0 \
        --user-data-dir="$home/.config/chromium" --window-title=Chromium \
        --window-position="$width,0" --window-size=1180,1040 \
        --alsa-output-device=plug:browser \
        --disable-browsing-blocking \
        --policy-config-dir=/etc/chromium/policies/chromium,/run/chromium/policies/chromium \
        --tesla-dbus-service-name=com.tesla.Chromium \
        --tesla-dbus-interface-name=com.tesla.Chromium \
        --tesla-dbus-object-path=/Chromium --tesla-dbus-observe-loading \
        --new-window about:blank
    ;;
chromium-fullscreen)
    url=${2:-about:blank}
    case "$url" in about:blank|https://*|http://*) ;; *) echo 'tesla-media-service: Theater URL invalid' >&2; exit 2 ;; esac
    busfile=/run/service/chromium-fullscreen/.dbus/session-bus/address
    i=0
    while [ ! -s "$busfile" ] && [ "$i" -lt 100 ]; do sleep 0.1; i=$((i + 1)); done
    [ -s "$busfile" ] || { echo 'tesla-media-service: Theater session bus missing' >&2; exit 1; }
    home=/home/chromium-fullscreen
    mkdir -p "$home/.config/chromium" "$home/.cache"
    chown -hR chromium-fullscreen:chromium-fullscreen "$home"
    display=$(qtcar_env DISPLAY); [ -n "$display" ] || display=:1.0
    xauth=$(qtcar_env XAUTHORITY)
    width=$(DISPLAY="$display" XAUTHORITY="$xauth" xwininfo -root 2>/dev/null | awk '/Width:/ {print $2; exit}')
    [ -n "$width" ] || width=1200
    cd /usr/lib/tesla-chromium
    echo "tesla-media-service: Theater on $display (offscreen at x=$width): $url" >&2
    as_media_user chromium-fullscreen chromium-fullscreen:chromium-dbus:input:video:audio:pipewire \
        env -i PATH="$PATH" HOME="$home" USER=chromium-fullscreen DISPLAY="$display" XAUTHORITY="$xauth" \
        DBUS_SESSION_BUS_ADDRESS="$(cat "$busfile")" \
        DBUS_SYSTEM_BUS_ADDRESS=unix:path=/var/run/dbus/system_bus_socket \
        LD_LIBRARY_PATH=/usr/lib/tesla-chromium:/opt/mesa/lib \
        LIBGL_DRIVERS_PATH=/opt/mesa/lib/dri LIBGL_ALWAYS_SOFTWARE=1 \
        __EGL_VENDOR_LIBRARY_FILENAMES=/opt/mesa/egl_vendor.d/50_mesa.json \
        FONTCONFIG_FILE=/etc/fonts/fonts.conf FONTCONFIG_PATH=/etc/fonts \
        LC_ALL=C LANG=C PIPEWIRE_RUNTIME_DIR=/var/run/pipewire PIPEWIRE_CONFIG_NAME=client.conf \
        ./tesla-chromium --no-sandbox --disable-setuid-sandbox \
        --ozone-platform=x11 --use-gl=angle --use-angle=gl \
        --remote-debugging-address=127.0.0.1 --remote-debugging-port=9225 \
        --disable-location-bar --disable-status-bubble --disable-toolbar --disable-tab-strip \
        --supports-active-window \
        --no-first-run --no-default-browser-check --enable-logging=stderr --log-level=0 \
        --user-data-dir="$home/.config/chromium" --window-title=Theater \
        --window-position="$width,0" --window-size=1200,1920 \
        --alsa-output-device=plug:browser_fullscreen \
        --disable-browsing-blocking --disable-bookmarks-blocking --disable-video-blocking \
        --policy-config-dir=/etc/chromium/policies/chromium-fullscreen,/run/chromium/policies/chromium-fullscreen \
        --tesla-dbus-service-name=com.tesla.ChromiumFullscreen \
        --tesla-dbus-interface-name=com.tesla.ChromiumFullscreen \
        --tesla-dbus-object-path=/ChromiumFullscreen \
        --tesla-dbus-observe-loading --tesla-dbus-observe-navigation \
        --new-window "$url"
    ;;
*) echo "tesla-media-service: unsupported job $job" >&2; exit 2 ;;
esac
