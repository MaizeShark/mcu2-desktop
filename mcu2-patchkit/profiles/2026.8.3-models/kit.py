"""Kit profile: Model S/X MCU2, firmware 2026.8.3 (the tables mcu2_patch.py applies).

Loaded by mcu2_patch.py, which provides: KIT (the kit folder), PROFILE_DIR (this folder),
TESLA_UID, shared_src(name) (the text of ../../src/<name>). Every table is optional.

So far what QtCar and QtCarCluster need (session 9): QtCar and QtCarCluster start without any
binary patch; the firmware's Mesa 25.0.7 has only the Intel hardware drivers (iris, crocus), so
on Xvfb there's no GL and QtCar crashes creating its first EGL surface.
"""

# ---------------------------------------------------------------------------
# Binary patches: (id, group, file offset, original bytes, patched bytes, description)
# Offsets are file offsets (the QtCar library and Chromium use equal virtual
# addresses; ConnMan's .text virtual address is 0x1000 above its file offset).
# ---------------------------------------------------------------------------
BINARIES = [
    {
        'path': 'usr/tesla/UI/lib/libQtCarGUI.so.1.0.0',
        'sha1_orig': '72aa0d4c8a1ebc1b8527f0c0fa71430e1df43476',
        'patches': [
            # The desktop's V4L2 loopback supplies the backup image; it has no
            # car deserializer for escalator's read-buc-deser-state command.
            ('camera-deserializer-ready', 'camera', 0x2a37bb0,
             '415741564155', 'b801000000c3',
             'use the supplied desktop camera without a car deserializer'),
        ],
        'retired': [
            ('qtcar-gui-force-internet', 'core', 0x272b1e8, '440fb6734b', '4183ce0190',
             'restore TileManager to use QtCar connectivity data values'),
            # session 14: the built-in games (chess, 2048) don't take that path (no
            # GamesLifecycleManager::getWindowSize call when they start); it didn't help them
            ('games-window-size-without-app', 'games', 0x19e3ea8,
             'c390660f1f440000', '89777889577cc390',
             'restore calculateAndSetWindowSize (the games window size patch did not apply to them)'),
        ],
    },
    {
        'path': 'usr/sbin/connmand',
        'sha1_orig': 'c691d6bd033a77b39387be6c39c6438b3194bd06',
        'patches': [
            # The car's worker writes "changehat" to /proc/self/attr/current. A
            # desktop chroot has no matching AppArmor hat; the write fails with
            # EPERM and ConnMan exits before it can expose Wi-Fi. This only
            # skips that car-specific confinement check; the broker/worker
            # split remains active and -i wlan0 selects its adapter.
            ('connman-no-car-apparmor-hat', 'wifi', 0xa89df,
             '0f85fd590000', '909090909090',
             'continue when the car-specific AppArmor hat cannot be entered'),
        ],
    },
]

# Tesla's Chromium 140 unconditionally enters its SHA-NI SHA-1 and SHA-256 block routines.
# The T480 (and many other Intel PCs) lacks the SHA extension. Redirect both entries to the
# binary's own scalar routines, which take the same arguments. These process executables are
# hardlinks in the firmware; keep the shared inode when patching.
for _chromium_process in ('main', 'gpu', 'utility', 'zygote'):
    BINARIES.append({
        'path': f'usr/lib/tesla-chromium/tesla-chromium-{_chromium_process}',
        'sha1_orig': 'ddcf8c541b395f8fb53e0a44ab278b974dd74f19',
        'in_place': True,  # these four paths are hardlinks; retain one shared executable
        'patches': [
            ('chromium-sha1-software', 'media', 0x270c620,
             'f30f6f0766', 'e9dbefffff',
             'use Chromium’s scalar SHA-1 on CPUs without SHA-NI'),
            ('chromium-sha256-software', 'media', 0x374c600,
             '488d0db98a', 'e97beeffff',
             'use Chromium’s scalar SHA-256 on CPUs without SHA-NI'),
        ],
    })

# The built-in games (2048, chess, backgammon) read /dev/input/touch with the car's panel
# transform compiled in (QCTOUCH's default: rotated, 4096 units, X inverted) and have no option
# to change it. Our touchscreen reports QtCar's screen coordinates (QtCar gets --touch
# evdev,autorange): the same for them.
for _game, _sha1, _offset in [('2048/2048', 'de74f4dd4c71043e8666fb8131b9328fe63acd48', 8187752), ('Chess/Chess', '5bc960a8e7e908005092b256ab23ad51f8292024', 8377408), ('Backgammon/Backgammon', '5e50ec70953e0806d63afe192527c35430c80382', 8534440)]:
    BINARIES.append({
        'path': 'usr/tesla/UI/standalone_apps/' + _game,
        'sha1_orig': _sha1,
        'patches': [
            ('game-touch-autorange', 'games', _offset, '65766465763a2f6465762f696e7075742f746f7563682c726f746174653a39302c72616e67653a343039362c696e7665727458', '65766465763a2f6465762f696e7075742f746f7563682c6175746f72616e676500000000000000000000000000000000000000',
             'read the touchscreen in screen coordinates, as QtCar does'),
        ],
    })

# ---------------------------------------------------------------------------
# Files the kit adds (downloaded per sources.toml, cached in payload/2026.8.3-models/):
# (group, path, mode, sha1)
# ---------------------------------------------------------------------------
# The kit's Mesa: Debian 13's 25.0.7 (llvmpipe for Xvfb; radeonsi/r600/nouveau/iris... for
# --native) with glvnd, in its own folder next to the firmware's: /opt/mesa/env.sh puts it first
# for QtCar. Only the libraries the firmware lacks come along (checked with the firmware's loader).
PAYLOAD_FILES = [
    ('mesa', 'opt/mesa/lib/libgallium-25.0.7-2+deb13u1.so', 0o644, '858bb25a31df3818eaf2a21a9f2bb5b287e78032'),
    ('mesa', 'opt/mesa/lib/libEGL_mesa.so.0', 0o644, '0ab1c21242180d5e5a9c2f487fddeeb79baf3e17'),
    ('mesa', 'opt/mesa/lib/libGLX_mesa.so.0', 0o644, 'b9c1a3ed68272b6bd89919bba3c0a18e10f1501c'),
    ('mesa', 'opt/mesa/lib/libgbm.so.1', 0o644, '4b28d20bfedf253d2a50c15094253c2c4e6567bc'),
    ('mesa', 'opt/mesa/lib/gbm/dri_gbm.so', 0o644, 'bc0ffb7b525bca45aa178feb8a803fcb15335e30'),
    ('mesa', 'opt/mesa/lib/dri/libdril_dri.so', 0o644, '759dc0cdd10a4607389d7dbd07fdeed6637e5f81'),
    ('mesa', 'opt/mesa/lib/libEGL.so.1', 0o644, '4ac73df1f4c022df10792db202c9520567abff41'),
    ('mesa', 'opt/mesa/lib/libGL.so.1', 0o644, '58d2051aa4876e7804c3d8de1c08af0cef00041c'),
    ('mesa', 'opt/mesa/lib/libGLX.so.0', 0o644, '7fd2d64a878dec4a04b86158d2ea01155ac0c029'),
    ('mesa', 'opt/mesa/lib/libGLdispatch.so.0', 0o644, '85c206a18f04c3d64cb5244e355c6da1e53f7786'),
    ('mesa', 'opt/mesa/lib/libGLESv2.so.2', 0o644, '277f757a3b1fb34e2549477fc5f4d036afe943a3'),
    ('mesa', 'opt/mesa/lib/libLLVM.so.19.1', 0o644, '63d26a017c120a510bbb4bfdff0aa645e9fe3da8'),
    ('mesa', 'opt/mesa/lib/libdrm.so.2', 0o644, '09c00eae727f91ad171e113038926c1917f68d55'),
    ('mesa', 'opt/mesa/lib/libdrm_intel.so.1', 0o644, '239ee620bb0b1d284d5aa8ca068b65b15c796ef3'),
    ('mesa', 'opt/mesa/lib/libdrm_amdgpu.so.1', 0o644, '10ec839bbb4743a8fbc0c2a2d9b147d758e2240e'),
    ('mesa', 'opt/mesa/lib/libbsd.so.0', 0o644, '6f877eb4b3c6e8d42b476eb7bab3ebba7ed1567f'),
    ('mesa', 'opt/mesa/lib/libedit.so.2', 0o644, '15c2a61afef31c1a3293daa98b30a2cd62a5976d'),
    ('mesa', 'opt/mesa/lib/libmd.so.0', 0o644, '715aaeeae5aee9c6bc3c04b4f4430933fdd55a7e'),
    ('mesa', 'opt/mesa/lib/libsensors.so.5', 0o644, '649054358c806f91a0a9ef5eca06b6f190865316'),
    ('mesa', 'opt/mesa/lib/libtinfo.so.6', 0o644, 'e3bb17a500c087d28fd21a1430542c6a7314b5b7'),
    ('mesa', 'opt/mesa/lib/libwayland-client.so.0', 0o644, '2672f6b22f1119343929ec50e6a9048f97acbe82'),
    ('mesa', 'opt/mesa/lib/libwayland-server.so.0', 0o644, '2a7f20fdddc3a6176f334e5781a505b9cadc5a0e'),
    ('mesa', 'opt/mesa/lib/libz3.so.4', 0o644, 'a6e934d9141c09fcb45c4b3c6d02e35b10cdf87c'),
    ('mesa', 'opt/mesa/lib/libzstd.so.1', 0o644, '30bb4545c80061e58dd4c32609deaf9b78795a18'),
    ('system', 'usr/bin/gdbserver', 0o755, '93c6a4b00313952d7206e1bcec2591a6d6bd899a'),
    # The firmware ships a 14 KiB no-op crashpad handler; Chromium 140 starts it and then
    # aborts when its handshake closes. Use the matching Debian Chromium 140 handler.
    ('media', 'usr/lib/tesla-chromium/chrome_crashpad_handler', 0o755, '741f67728947f4f96d12e2a6f8820a8e0a799f3f'),
    ('media', 'usr/lib/egl_media_pixmap_shim.so', 0o755, '-'),
    # answers QtCar's escalator requests from a fixed table (src/escalator-stub.c); the car's
    # /usr/bin/escalator is never started
    ('system', 'usr/local/bin/escalator-stub', 0o755, '-'),
]

# (group, link path, target): the DRI driver names all go to Mesa's loader stub
SYMLINKS = [('mesa', f'opt/mesa/lib/dri/{n}_dri.so', 'libdril_dri.so')
            for n in ('swrast', 'kms_swrast', 'iris', 'crocus', 'i915', 'radeonsi', 'r600', 'r300',
                      'nouveau', 'virtio_gpu', 'vmwgfx', 'zink', 'd3d12')]

MESA_ENV = '''# mcu2-patchkit: the kit's Mesa (Debian 13, 25.0.7 with llvmpipe) for QtCar and QtCarCluster.
# Sourced by startup.sh and sandbox.sh; software rendering also needs LIBGL_ALWAYS_SOFTWARE=1.
export LD_LIBRARY_PATH=/opt/mesa/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export LIBGL_DRIVERS_PATH=/opt/mesa/lib/dri
export GBM_BACKENDS_PATH=/opt/mesa/lib/gbm
export __EGL_VENDOR_LIBRARY_FILENAMES=/opt/mesa/egl_vendor.d/50_mesa.json
'''

EGL_VENDOR = '''{
    "file_format_version" : "1.0.0",
    "ICD" : {
        "library_path" : "/opt/mesa/lib/libEGL_mesa.so.0"
    }
}
'''

# ./tesla start runs this in the chroot as root, once for QtCar (in its restart loop) and, with a
# cluster screen, once more for QtCarCluster. On the car: runit's qtcar / qtcar-cluster jobs
# (RunQtCar.vars), in minijails; here without them. chpst (runit) instead of su: the users'
# shell is /bin/false.
STARTUP_SH = r'''#!/bin/sh
# /startup.sh (mcu2-patchkit, profile 2026.8.3-models), run by ./tesla start as root in the chroot.
#   QTCAR_BIN        QtCar (default) or QtCarCluster
#   QTCAR_DISPLAY    X display (the car: QtCar :0.0, QtCarCluster :0.1)
#   QTCAR_XAUTHORITY X cookie file (native screen)
#   QTCAR_SIZE       QtCar's size (the car: 1200x1920)
#   QTCAR_ARGS       more QtCar options
#   GDB              run with gdbserver
#   QTCAR_GL         software (the kit's llvmpipe) or hardware; QTCAR_GL_DRIVER: i965/iris/crocus
#                    (Intel: the firmware's own Mesa, as on the car) or a Gallium driver of the kit's
export PATH=/bin:/usr/bin:/sbin:/usr/sbin:/usr/local/bin
. /etc/tesla.env
export LD_LIBRARY_PATH=/usr/tesla/UI/lib:/usr/lib:/lib:/usr/lib/engines-3
export LC_ALL=C LANG=C LANGUAGE=C QT_X11_NO_MITSHM=1 QT_NO_GLIB=1 MESA_IRIS_BATCHBUFFER_SUBMIT_NOEXIT=1
export DISPLAY=${QTCAR_DISPLAY:-:1} XAUTHORITY=${QTCAR_XAUTHORITY:-/root/.Xauthority}

if [ "$QTCAR_GL" = hardware ]; then
    case "$QTCAR_GL_DRIVER" in
        ""|i965|iris|crocus) ;;     # Intel: the firmware's Mesa picks iris/crocus itself
        *) . /opt/mesa/env.sh; export MESA_LOADER_DRIVER_OVERRIDE=$QTCAR_GL_DRIVER ;;
    esac
else
    . /opt/mesa/env.sh
    export LIBGL_ALWAYS_SOFTWARE=1
fi

# the system D-Bus (QtCar's data values, its services' names) is ./tesla start's base service
# "dbus" (profile.toml), started before QtCar

# what the car's qtcar-startup job prepares: the services' data value sockets (/tmp/dvaccess)
mkdir -p /home/tesla/.Tesla/car /home/tesla/.Tesla/cache /home/tesla/.Tesla/data /home/tesla/.Tesla/tmp
chown -h tesla:tesla /home/tesla /home/tesla/.Tesla /home/tesla/.Tesla/car /home/tesla/.Tesla/cache \
    /home/tesla/.Tesla/data /home/tesla/.Tesla/tmp
mkdir -p /tmp/dvaccess /tmp/dbus_dvaccess /var/run/cdtickwatchdog /var/run/uptime
# QtCar writes Theater's selected URL into this Chromium policy directory before
# requesting the fullscreen process. The car's runit setup normally creates it.
# QtCar writes the policies of the media web app and Browser instances there too.
for p in chromium chromium-app chromium-fullscreen; do mkdir -p /run/chromium/policies/$p; done
# QtCar writes the games' settings (tidk/<app>/tidk.vars) and touch rectangles (i2v/*.geometry)
mkdir -p /opt/games/run/tidk/chromium-fullscreen /opt/games/run/i2v
chown tesla:input-to-virtual /opt/games/run/tidk /opt/games/run/i2v && chmod 2775 /opt/games/run/tidk /opt/games/run/i2v
chown tesla:tesla /run/chromium/policies/chromium /run/chromium/policies/chromium-app \
    /run/chromium/policies/chromium-fullscreen /opt/games/run/tidk/chromium-fullscreen
chmod 770 /tmp/dvaccess /tmp/dbus_dvaccess
chown -h tesla:dvaccess /tmp/dvaccess
chown -h qtdvserver:dbus_dvaccess /tmp/dbus_dvaccess
chown -h tesla:monitord /var/run/cdtickwatchdog && chmod 2770 /var/run/cdtickwatchdog

# user:group:its supplementary groups (as minijail on the car; chpst -u user sets none). Members
# of dvshm get it as their primary group: the shared memory data values (DVSM_*) are SysV
# semaphores and segments with mode 660 in the creator's group, and QtCarDvServer, QtCar,
# QtCarVehicle, the cluster, ... (different users) all have to reach each other's.
with_groups() {
    awk -F: -v u="$1" '
        {n = split($4, m, ","); for (i = 1; i <= n; i++) if (m[i] == u) g[++k] = $1}
        END {p = u; for (i = 1; i <= k; i++) if (g[i] == "dvshm") p = "dvshm"
             printf "%s:%s", u, p; if (p != u) printf ":%s", u
             for (i = 1; i <= k; i++) if (g[i] != p) printf ":%s", g[i]}' /etc/group
}

cd /usr/tesla/UI/bin || exit 1
case "${QTCAR_BIN:-QtCar}" in
    QtCarCluster)
        export HOME=/home/qtcluster
        # the shared settings.conf (like qtcar-service does for the services with their own
        # user): without it the cluster's data value listeners did the peer label check again
        mkdir -p $HOME/.Tesla/car $HOME/.Tesla/data
        [ -e $HOME/.Tesla/car/settings.conf ] || ln -s /home/tesla/.Tesla/car/settings.conf $HOME/.Tesla/car/settings.conf
        chown -h qtcluster:qtcluster $HOME/.Tesla $HOME/.Tesla/car $HOME/.Tesla/data $HOME/.Tesla/car/settings.conf
        # --ic --window 1.5 as the car's RunQtCar.env (QCICRES): the 1920x720 panel's layout and
        # hi_res assets (ClusterUtils::isHiRes: windowScale >= 1.5); without it the 1280x480 one
        exec chpst -u "$(with_groups qtcluster)" ./QtCarCluster -graphicssystem raster --isic --ic --window 1.5 $QTCAR_ARGS ;;
    *)
        export HOME=/home/tesla
        # Xvfb/llvmpipe cannot import ChromiumApp's X pixmap as an EGL image. The shim
        # copies it into the texture when the firmware's native import fails. The
        # native GPU can report success but still display a black Browser texture.
        [ "$QTCAR_GL" = hardware ] && export EGL_MEDIA_PIXMAP_FORCE=1
        export LD_PRELOAD=/usr/lib/egl_media_pixmap_shim.so${LD_PRELOAD:+:$LD_PRELOAD}
        # --emulated (RuntimeEnv, Tesla's desktop mode): the center/driver display devices count
        # as powered and trained without the panel's GPIO (BMP-LCD-PWR-EN) and the escalator's
        # read-dpcd/drm-status-detect; without it QtCar retried those ~70x/s (10 000 ERROR lines
        # in 90 s, +40 % CPU). Asserts still only log. Also the "dev" mode (more assert logging).
        QTCAR_ARGS="--emulated $QTCAR_ARGS"
        echo $GDB
        if [ "$GDB" = 1 ]; then
            echo "gdb is on"
            exec chpst -u "$(with_groups tesla)" gdbserver :8888 ./QtCar -graphicssystem raster --cid --size ${QTCAR_SIZE:-1200x1920} \
                --touch evdev,autorange $QTCAR_ARGS
        else
            echo "gdb is off"
            exec chpst -u "$(with_groups tesla)" ./QtCar -graphicssystem raster --cid --size ${QTCAR_SIZE:-1200x1920} \
                --touch evdev,autorange $QTCAR_ARGS
        fi
        ;;
esac
'''

# QtCar switches the car's panels with xrandr ("CenterDisplay disabling output eDP1", "DriverDisplay
# enabling output DP1", about once a second while the escalator, which it asks for the panels'
# power, isn't there). On a PC: nothing (other output names) or, with the intel X driver, the PC's
# own screen; and on a real X server each call took up to a second (output probing), a frame
# stall each time. Output changes are ignored, queries go to the firmware's xrandr.
XRANDR = '''#!/bin/sh
# mcu2-patchkit: QtCar's output switching (the car's panels) is ignored on a PC; see kit.py
case " $* " in *" --output "*) exit 0 ;; esac
exec /usr/bin/xrandr.real "$@"
'''

# audiod runs as root here (real-time priority for its pump threads, see qtcar-service.sh); the
# firmware's policy (usr/share/dbus-1/system.d/com.tesla.AudiodService.conf) lets only the audiod
# user own its name, and without the name it exits ("Cannot initialize D-Bus server", -13)
AUDIOD_DBUS = '''<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<!-- mcu2-patchkit: audiod runs as root on a PC -->
<busconfig>
  <policy user="root">
    <allow own="com.tesla.AudiodService"/>
    <allow send_destination="com.tesla.AudiodService"/>
    <allow send_interface="com.tesla.AudiodService"/>
    <allow send_type="error"/>
  </policy>
</busconfig>
'''

# (group, path, content, mode[, owner uid])
TEXT_FILES = [
    ('audio', 'etc/dbus-1/system.d/mcu2-audiod-root.conf', AUDIOD_DBUS, 0o644),
    ('mesa', 'opt/mesa/env.sh', MESA_ENV, 0o644),
    ('mesa', 'opt/mesa/egl_vendor.d/50_mesa.json', EGL_VENDOR, 0o644),
    ('system', 'startup.sh', STARTUP_SH, 0o755),
    # the car's resolver is its own dnsmasq on 127.0.0.1; the chroot shares the host's network
    ('system', 'etc/resolv.conf', 'nameserver 8.8.8.8\n', 0o644),
    # the firmware's services (qtcar-vehicle, ...) the way their runit jobs run them, see ../../src
    ('system', 'usr/local/bin/qtcar-service', shared_src('qtcar-service.sh'), 0o755),
    ('media', 'usr/local/bin/tesla-media-service',
     open(PROFILE_DIR + '/src/media-services.sh').read(), 0o755),
    # no runit in the chroot: "sv start ..." from the escalator etc. goes to qtcar-service
    ('system', 'sbin/sv', shared_src('sv-wrapper.sh'), 0o755),
    ('system', 'usr/bin/xrandr', XRANDR, 0o755),
]

# text files that replace a firmware program: the program is kept under this name (sbin/sv ->
# sbin/sv.runit is the engine's default)
KEEP_BINARY = {'usr/bin/xrandr': 'usr/bin/xrandr.real'}

# (path, owner uid or None): directories QtCar writes to, mount points of ./tesla start
DIRECTORIES = [
    ('home/tesla/.Tesla/car', TESLA_UID),
    ('home/tesla/.Tesla/data', TESLA_UID),
    ('home/qtcluster/.Tesla/data', 2010),
    ('dev/input', None),
    ('dev/shm', None),
    ('sys/fs/cgroup/net_cls', None),
    ('var/lock', None),
]

# the services' syslog: ./tesla start listens at the chroot's /tmp/log (tools/syslog-sink.py)
SYMLINKS += [('system', 'dev/log', '../tmp/log')]

# Car config keys the kit sets in settings.conf (merged: other keys stay)
SETTINGS_KEYS = {
    # QtCar's display state machine switches the screen on/off from the car (doors, driver present,
    # brake, gear...). Without vehicle data it stays in DisplayStateOff: touches are ignored
    # ("ignoring touch N secs after last display off") and the touch driver doesn't even open
    # /dev/input/touch (CD_displayState 0). Off, the display counts as always on.
    'display_state': {'enable_display_state_manager': 'false'},
    # The services hand each other their data values over Unix sockets (/tmp/dvaccess), and the
    # listener checks the publisher's AppArmor label (SO_PEERSEC). A PC's kernel gives none for
    # them ("[PEERSEC] Failed to get peersec ... Protocol not available"): every connection was
    # dropped and QtCar never got QtCarVehicle's values. Off, as a config value allows.
    'service_data_listener': {'sec_prof_check': 'false'},
    # The Model 3's USB folder key. This firmware's StorageUtils::usbMediaFolder() is /mnt/media
    # regardless (mounterd's mount point): ./tesla start --music mounts the folder at /mnt/media/usb-music
    'usb': {'media_path': '/home/tesla/media'},
    # audio-type asks the gateway's config (/var/etc/gateway.cfg, not here): base = one amplifier
    'audiod': {'audio_type': 'base', 'audio_product': 'models'},
}
SETTINGS_FILES = [('home/tesla/.Tesla/car/settings.conf', TESLA_UID)]

# No "modelsxinfo2" sound card (the car's amplifiers) on a PC. ./tesla start --audio loads
# snd-aloop with the card "model3" (as for the Model 3: shared) next to "virtual"; audiod (with
# AudioWeaver built in) plays its amplifier outputs to model3 device 0, subdevices 0 (amp M) and 1
# (amp S), and the host records them from device 1. The mic (auc:2) is subdevice 2's capture.
ASOUND_MARK = '# --- mcu2-patchkit: sound card on a PC ---'
ASOUND_BLOCK = ASOUND_MARK + '''
pcm.!auc {
        @args [ DEVICE CHANNELS ]
        @args.DEVICE.type integer
        @args.CHANNELS.type integer

        type hw
        card model3
        device 0
        subdevice $DEVICE
        rate 48000
        channels $CHANNELS
}
# The radio (FM/XM) comes over the car's Ethernet as an AVB stream (type aaf, eth0.3). Without
# it audiod retried "tuner" ~25 times a second while the Media app's source was the radio.
# Instead: a loopback capture nobody plays into (silence, at the card's clock).
pcm.!tuner_fm_aaf {
        type hw
        card model3
        device 1
        subdevice 3
}
pcm.!tuner_xm_aaf {
        type hw
        card model3
        device 1
        subdevice 3
}
# --- end mcu2-patchkit ---
'''

# chpst, not su: the passwd stays the firmware's
TESLA_SHELL = False

GROUPS = {
    'mesa': "Mesa 25.0.7 with llvmpipe + glvnd + LLVM 19 (Debian 13) in /opt/mesa",
    'system': '/startup.sh (QtCar, QtCarCluster, system D-Bus), settings.conf, directories, DNS, /dev/log',
    'wifi': 'ConnMan without the car-specific AppArmor hat',
    'games': 'the built-in arcade games (2048, chess, backgammon) on a PC touchscreen',
    'audio': 'the car amplifiers\' sound card on snd-aloop ("model3", as for the Model 3)',
}

GLIBC = (2, 41)

BUILDABLE = {
    'usr/lib/egl_media_pixmap_shim.so': ('src/egl_media_pixmap_shim.c', ['-shared', '-fPIC', '-O2']),
    'usr/local/bin/escalator-stub': ('src/escalator-stub.c', ['-O2', '-Wall']),
}
