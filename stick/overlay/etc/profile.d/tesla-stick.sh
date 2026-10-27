# The stick: tty1 logs in automatically (getty@tty1 autologin) and starts QtCar full screen.
# Ctrl+Alt+Esc returns here; tesla-start starts it again. Boot option tesla.console: no autostart.
if [ "$(id -un)" = mcu ] && [ "$(tty)" = /dev/tty1 ] && [ -z "${DISPLAY:-}" ]; then
    if [ ! -e "${XDG_RUNTIME_DIR:-/tmp}/tesla-console" ] && ! grep -qw tesla.console /proc/cmdline; then
        tesla-start
    fi
    tesla-help
fi
