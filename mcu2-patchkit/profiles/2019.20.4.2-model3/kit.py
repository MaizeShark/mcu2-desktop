"""Kit profile: Model 3 MCU2, firmware 2019.20.4.2 (the tables mcu2_patch.py applies).

Loaded by mcu2_patch.py, which provides: KIT (the kit folder), PROFILE_DIR (this folder),
TESLA_UID, shared_src(name) (the text of ../../src/<name>). Every table is optional.
"""
import os

# ---------------------------------------------------------------------------
# Binary patches: (id, group, file offset, original bytes, patched bytes, description)
# Offsets are file offsets (== virtual addresses for these binaries).
# ---------------------------------------------------------------------------
BINARIES = [
    {
        'path': 'usr/tesla/UI/bin/QtCar',
        'sha1_orig': '53f1c1bd1770fe5528ab29f2d797ef7b645f41db',
        'patches': [],
        # the escalator's cgroup setup succeeds in the chroot (./tesla start mounts net_cls; QtCar
        # ends up in net_cls:/qtcar), so QtCar's exit(1) on failure never happens (2026-09-25)
        'retired': [
            ('qtcar-cgroup-exit-1', 'core', 0x2ea5e5, 'e80645f7ffbf01000000e85c48f7ff', '9090909090bf010000009090909090',
             'ChromiumManager::initializeCef: perror("failed to set internet-cgroups") + exit(1)'),
            ('qtcar-cgroup-exit-2', 'core', 0x2ea691, 'e85a44f7ffe94f', '9090909090e959',
             'ChromiumManager::initializeCef: perror("failed to set qtcar-cgroups") + exit(1)'),
        ],
    },
    {
        'path': 'usr/tesla/UI/lib/libQtCarGUI.so.1.0.0',
        'sha1_orig': 'df5b74322502ea348e259d1c3b432c1e0c0b4421',
        'patches': [
        ],
        # patches of earlier kits that do nothing (or no longer apply): put back where found.
        # (id, group, offset, firmware bytes, bytes an earlier kit wrote, why)
        'retired': [
            # an assert only logs (utils-assert-nonfatal); the branch logs and continues
            ('gui-energy-roadload', 'core', 0xd75ed8, '0f8502020000', '909090909090',
             'EnergyModelUtils::roadLoadEstimateFor: carType assert (hid a log line)'),
            ('gui-energy-driveunit', 'core', 0xd7617d, '7571', '9090', 'EnergyModelUtils::driveUnitConfig: same'),
            ('gui-energy-pack', 'core', 0xd762af, '752f', '9090', 'EnergyModelUtils::packEnergy_kWh: same'),
            ('gui-escalator-1', 'core', 0x7c26a3, 'e8d89bf1ff', '31c0909090',
             'libQtCarGUI\'s ChromiumManager::initializeCef is never called: QtCar has its own copy'),
            ('gui-escalator-2', 'core', 0x7c295f, 'e81c99f1ff', '31c0909090', 'same'),
            ('gui-cgroup-exit-1', 'core', 0x7c32a5, 'e81609f2ffbf01000000e8cc99f2ff', '9090909090bf010000009090909090',
             'same (qtcar-cgroup-exit-* patch the copy in QtCar)'),
            ('gui-cgroup-exit-2', 'core', 0x7c3351, 'e86a08f2ffe94f', '9090909090e959', 'same'),
            ('gui-camera-reset', 'core', 0xd3f760, '4155be', '31c0c3',
             'the old "no cameras" stub CameraStream::resetDevice() -> 0 (the backup camera works)'),
            ('gui-camera-init', 'core', 0xd43000, '55beff', '31c0c3', 'the old stub CameraStream::initDevice() -> 0'),
            ('gui-video-ximagesink', 'core', 0x12f2e93, b'xvimagesink'.hex(), b'ximagesink\0'.hex(),
             'a gst-launch pipeline string nobody uses (the videos play in /usr/bin/tvideo)'),
            ('gui-cef-nosandbox', 'cef', 0x12ae593, '498d7c2408498d75', '41c7431001000000',
             'no_sandbox in the library\'s initializeCef copy (unused); src/cef_nosandbox.c sets it'),
        ],
    },
    {
        'path': 'usr/tesla/UI/lib/libQtCarUtils.so.1.0.0',
        'sha1_orig': '7286a3e0c86b69962ea1b102c48aa69432852fce',
        'patches': [
            ('utils-assert-nonfatal', 'core', 0xb4703, 'c604', 'eb0b',
             'Logger::logAssert: skip the deliberate write to address 0 outside "prod" mode, so a '
             'failed assert is only logged, like on the car (was: media button asserts crashed QtCar)'),
        ],
        # NOPs of single logAssert calls: with logAssert log-only they only hid a log line
        'retired': [
            ('utils-assert-ctor', 'core', 0xaf0ea, 'e87113feff', '9090909090', 'DataValue::DataValue: logAssert'),
            ('utils-assert-protected', 'core', 0xaf69f, 'e8bc0dfeff', '9090909090',
             'DataValue::setValueNoLock: "protected DataValue" logAssert'),
            ('utils-assert-duplicate', 'core', 0xc3997, 'e8c4cafcff', '9090909090',
             'DataValueManager::addValue: "Duplicate data value" logAssert'),
        ],
    },
    {
        'path': 'usr/tesla/UI/lib/libQtCarMediaV2.so.1.0.0',
        'sha1_orig': '89923a08454a1b9dd459a389184806c1eef1b14f',
        'patches': [
        ],
        'retired': [
            ('media-assert-pause', 'core', 0x463763, 'e8680df8ff', '9090909090',
             'MediaV2::MediaMasterSourceController::pause: logAssert (only hid a log line)'),
        ],
    },
    {
        # Tesla's CEF 3.3683 (Chrome 73) is built with Blink's stub FFT (fft_frame_stub.cc: FFTFrame
        # allocates nothing). Any page that creates a WebAudio OscillatorNode makes
        # PeriodicWave::CreateBandLimitedTables write into the null FFT arrays (VectorMath::Vsmul)
        # and the renderer dies ("tesla-cef-zygot segfault ... libcef.so[4165885]"). Many sites do
        # (fingerprinting / bot detection), tesla.com too. createOscillator() now returns null.
        'path': 'usr/lib/libcef.so',
        'sha1_orig': '975741de4fff287a55d498cf137e104623d23852',
        'patches': [
            ('cef-no-oscillator', 'cef', 0x8170490, '554889', '31c0c3',
             'BaseAudioContext::createOscillator() -> return null (WebAudio FFT is a stub in this build)'),
        ],
    },
    {
        # QtCarSimService (vehicle simulator, qtcar-service qtcar-sim): with the drive rail on it
        # also simulates the drive inverter, but its gear/speed don't follow SIM_shiftState /
        # SIM_vehicleSpeed and DI_vehicleSpeed comes out as -40 km/h. vehicle/tesla-can.py sends
        # the DI frames instead, so the sim's DI module is switched off.
        'path': 'usr/tesla/UI/lib/libQtCarSim.so.1.0.0',
        'sha1_orig': '96bcf8a90c03a7fa2183e9a62d53121ae1e31a26',
        'patches': [
            ('sim-no-di-2hz', 'vehicle', 0x51cd60, '4155b908', 'c355b908',
             'SimDriveInverter::send2Hz() -> return (no DI frames from the simulator)'),
            ('sim-no-di-10hz', 'vehicle', 0x523180, '41574156', 'c3574156',
             'SimDriveInverter::send10Hz() -> return (no DI_systemStatus/DI_speed/odometer frames)'),
        ],
    },
    {
        # audiod does the audio I/O for AudioWeaver (DSP) and pumps it. Once the A2B PCM device
        # pumps, its main loop brings up the A2B stack (transceiver on /dev/i2c-4, amplifiers,
        # microphones); without that hardware a2b_stackAlloc fails and audiod exits. The base
        # amp output alone is enough on a PC, so never start A2B.
        'path': 'usr/bin/audiod',
        'sha1_orig': '0ef70bcffe81e771d3b042970e972a3cbffc79e2',
        'patches': [
            ('audiod-no-a2b', 'audio', 0xa0aa, '488d0d', '31c0c3',
             'audiod: "is the A2B device pumping" -> 0, no A2B stack (no /dev/i2c-4 on a PC)'),
        ],
    },
]

# ---------------------------------------------------------------------------
# Files the kit adds (downloaded per sources.toml or built from src/, cached in payload/): (group, path, mode, sha1)
# ---------------------------------------------------------------------------
PAYLOAD_FILES = [
    # Mesa 18.0.5 swrast (Ubuntu) replaces Tesla's GL stack, which does not work on Xvfb
    ('mesa', 'usr/lib/dri/swrast_dri.so', 0o644, 'e79ef300403a067005be6480841930965d8d7e64'),
    # Intel GPUs (./tesla start --native): the firmware's own i965 is Mesa 11.2.2 (2016), which
    # knows the car's Apollo Lake but not newer GPUs such as Kaby Lake. Mesa 18.0.5's knows them.
    ('mesa', 'usr/lib/dri/i965_dri.so', 0o644, 'c9d25e4f047893cd423d11cba4379db278c50570'),
    ('mesa', 'usr/lib/libglapi.so.0.0.0', 0o755, '17173722ee272789ff970d1ea45cb0694a5c27c9'),
    ('mesa', 'usr/lib/libdrm.so.2.4.0', 0o644, '20611823a72925941768e3c50844ffa42f811913'),
    ('mesa', 'usr/lib/libdrm_intel.so.1.0.0', 0o644, '8e3131c329c05700c3611c2cb73523ad830456a8'),
    ('mesa', 'usr/lib/libdrm_amdgpu.so.1.0.0', 0o644, 'a59141838c286e6b733cd01e5ed3f6d6d688aedd'),
    ('mesa', 'usr/lib/libdrm_nouveau.so.2.0.0', 0o644, '125decd5b11f506ffd816cb0fa64a547814bdb60'),
    ('mesa', 'usr/lib/libdrm_radeon.so.1.0.1', 0o644, '8ba204b8d56d6c10dd91cb20e0d6394324483222'),
    ('mesa', 'usr/lib/libffi.so.6.0.4', 0o644, 'a5be37d7823f44e5cb5399a41df25c76f4e1649d'),
    ('mesa', 'usr/lib/libLLVM-6.0.so.1', 0o644, 'cb66fc578ab21f183fc2ed8e64082757c8d92b1b'),
    ('mesa', 'usr/lib/libLLVM-3.8.so.1', 0o644, 'abea0c731126dfbfc04ad61c4d154a5dfe11f291'),
    ('mesa', 'usr/lib/libbsd.so.0.8.2', 0o644, 'c3e805c53edb389d81371800c2dc4d87ee6f60e4'),
    ('mesa', 'usr/lib/libedit.so.2.0.53', 0o644, '40321cfa7515de7c8889796bdbf8755c92e2b6c4'),
    ('mesa', 'usr/lib/libelf-0.165.so', 0o644, 'c3e1a15086816cb6cb7ed96e9884373222340f5c'),
    ('mesa', 'usr/lib/libexpatw.so.1.6.0', 0o644, '70f1d3fcde86b8d23b3b0abe79fc52ced557b688'),
    ('mesa', 'usr/lib/libsensors.so.4.4.0', 0o644, 'b68396fbd099769297be424b72a6505eca9838da'),
    ('mesa', 'usr/lib/libtic.so.5.9', 0o644, '9cf8c1943c093e781b747de427b03f8146844031'),
    ('mesa', 'usr/lib/libtinfo.so.5.9', 0o644, '2bd9389f52439de7a42efcb8a3c3f8d4c881e8b2'),
    # Passes an fd for icudtl.dat to CEF (fixes "Invalid file descriptor to ICU data"). src/icu_preload.c
    # Was /tmp/icu_preload.so in the original setup; moved out of /tmp so it can't get cleaned away.
    ('system', 'usr/lib/icu_preload.so', 0o755, 'a3da594a29ad33acc8ab61fa57e1139759804e4b'),
    # Shows the browser: emulates EGL images from X pixmaps, which Xvfb + Mesa swrast can't do.
    # Always built from src/egl_pixmap_shim.c (no fixed hash).
    ('cef', 'usr/lib/egl_pixmap_shim.so', 0o755, '-'),
    # Sets CefSettings.no_sandbox (and optional CEF logging / DevTools) via a cef_initialize()
    # wrapper. Always built from src/cef_nosandbox.c.
    ('cef', 'usr/lib/cef_nosandbox.so', 0o755, '-'),
]

# Files earlier kits added that aren't needed: removed where found (with their .orig backups).
# They aren't in the firmware, so whatever is there is the kit's. (group, path, why)
RETIRED_FILES = [
    ('cef', 'usr/tesla/UI/bin/chrome-sandbox',
     'fake setuid sandbox helper: CEF runs with no_sandbox (src/cef_nosandbox.c) and never starts it'),
    ('cef', 'usr/tesla/UI/lib/chrome-sandbox', 'patched copy of /usr/lib/chrome-sandbox, unused for the same reason'),
]

# (group, link path, target)
SYMLINKS = [
    # AMD GPUs (./tesla start --native --gpu): radeonsi (GCN) and r600 (TeraScale, e.g. HD 6670)
    # are part of the Gallium megadriver
    ('mesa', 'usr/lib/dri/radeonsi_dri.so', 'swrast_dri.so'),
    ('mesa', 'usr/lib/dri/r600_dri.so', 'swrast_dri.so'),
    ('mesa', 'usr/lib/libLLVM-6.0.so', 'libLLVM-6.0.so.1'),
    ('mesa', 'usr/lib/libLLVM-3.8.so', 'libLLVM-3.8.so.1'),
    ('mesa', 'usr/lib/libbsd.so.0', 'libbsd.so.0.8.2'),
    ('mesa', 'usr/lib/libdrm_amdgpu.so.1', 'libdrm_amdgpu.so.1.0.0'),
    ('mesa', 'usr/lib/libdrm_nouveau.so.2', 'libdrm_nouveau.so.2.0.0'),
    ('mesa', 'usr/lib/libdrm_radeon.so.1', 'libdrm_radeon.so.1.0.1'),
    ('mesa', 'usr/lib/libedit.so.2', 'libedit.so.2.0.53'),
    ('mesa', 'usr/lib/libelf.so.1', 'libelf-0.165.so'),
    ('mesa', 'usr/lib/libexpatw.so.1', 'libexpatw.so.1.6.0'),
    ('mesa', 'usr/lib/libsensors.so.4', 'libsensors.so.4.4.0'),
    ('mesa', 'usr/lib/libtic.so.5', 'libtic.so.5.9'),
    ('mesa', 'usr/lib/libtinfo.so.5', 'libtinfo.so.5.9'),
    ('assets', 'usr/tesla/UI/assets/day/about/badge_model_s_cid.png',
     '/usr/tesla/UI/assets/day/about/badge_model_3_cid.png'),
    ('assets', 'usr/tesla/UI/assets/night/about/badge_model_s_cid.png',
     '/usr/tesla/UI/assets/night/about/badge_model_3_cid.png'),
    ('assets', 'usr/tesla/UI/assets/night/car/park/roof_glass.png', '../hero/roof_glass.png'),
    ('cef', 'usr/tesla/UI/lib/libcef.so', '/usr/lib/libcef.so'),
    ('cef', 'usr/tesla/UI/lib/icudtl.dat', '/usr/lib/icudtl.dat'),
    ('cef', 'usr/tesla/UI/lib/locales', '/usr/lib/locales'),
    ('cef', 'usr/tesla/UI/lib/natives_blob.bin', '/usr/lib/natives_blob.bin'),
    ('cef', 'usr/tesla/UI/lib/snapshot_blob.bin', '/usr/lib/snapshot_blob.bin'),
    ('cef', 'usr/tesla/UI/lib/cef.pak', '/usr/lib/cef.pak'),
    ('cef', 'usr/tesla/UI/lib/cef_100_percent.pak', '/usr/lib/cef_100_percent.pak'),
    ('cef', 'usr/tesla/UI/lib/cef_200_percent.pak', '/usr/lib/cef_200_percent.pak'),
    ('cef', 'usr/tesla/UI/lib/cef_extensions.pak', '/usr/lib/cef_extensions.pak'),
    # CEF looks for its resources next to libcef.so, which the link above puts in UI/lib. Without
    # this one every renderer dies with "Failed to deserialize the V8 snapshot blob".
    ('cef', 'usr/tesla/UI/lib/v8_context_snapshot.bin', '/usr/lib/v8_context_snapshot.bin'),
    ('cef', 'usr/tesla/UI/lib/devtools_resources.pak', '/usr/lib/devtools_resources.pak'),
    ('cef', 'usr/tesla/UI/lib/swiftshader', '/usr/lib/swiftshader'),
]

# Directories QtCar expects, plus mount points used by start_all.sh. (path, owner uid or None)
# (path, mode) of existing directories whose mode needs changing
DIR_MODES = [
    # CEF's cache/cookies/local storage (cache_path). The browser process is QtCar (user tesla,
    # member of group tesla-cef), but the directory is 0755 tesla-cef: "Unable to create cache",
    # "Cookie sqlite error 14".
    ('home/tesla-cef', 0o775),
]

DIRECTORIES = [
    ('home/tesla', TESLA_UID),
    ('home/tesla/.Tesla/car', TESLA_UID),
    ('home/tesla/.Tesla/data', TESLA_UID),
    ('root/.Tesla/car', None),
    ('root/.Tesla/data/media', None),
    ('root/.Tesla/cache', None),
    ('root/.Tesla/browser', None),
    ('var/lock', None),
    ('run/connman', None),
    ('var/etc/qtcar-connman', None),   # qtcar-connman caches its cellular policy here
    ('dev/input', None),
    ('dev/shm', None),
    ('sys/fs/cgroup/net_cls', None),
    ('sys/fs/cgroup/memory', None),
]
EMPTY_FILES = ['dev/null', 'dev/random', 'dev/urandom', 'var/lock/log-top']  # bind-mount targets / lock file

STARTUP_SH = r'''#!/bin/sh
# Run inside chroot as root (started by start_all.sh on the host)
export PATH=/bin:/usr/bin:/sbin:/usr/sbin:/usr/local/bin

# Kernel settings. NOTE: /proc is the host's, so these change the HOST kernel until reboot.
echo 1 > /proc/sys/vm/overcommit_memory
echo 0 > /proc/sys/vm/mmap_min_addr

# Cgroups QtCar / CEF expect (net_cls is mounted by start_all.sh)
mkdir -p /sys/fs/cgroup/net_cls/nonet
mkdir -p /sys/fs/cgroup/net_cls/qtcar
mkdir -p /sys/fs/cgroup/net_cls/internet-cgroup
mkdir -p /sys/fs/cgroup/net_cls/qtcar-cgroup
mkdir -p /sys/fs/cgroup/net_cls/internet
mkdir -p /sys/fs/cgroup/net_cls/tesla-cef
mkdir -p /sys/fs/cgroup/memory/cef
mkdir -p /var/lock

# DNS (connman is not running; /etc/resolv.conf points here). Needed for map tiles.
mkdir -p /var/run/connman
echo "nameserver 8.8.8.8" > /var/run/connman/resolv.conf

# Escalator (privileged helper QtCar talks to via /tmp/escalator). It sometimes exits after a
# request it can't serve (seen: starting the dashcam daemon on QtCar's first start), and
# QtCar's ChromiumManager::initializeCef crashes without it ("Memory corruption detected").
# So keep it in a respawn loop, one loop across QtCar restarts, and wait for its socket.
if ! grep -qs 'escalator-loo[p]' /proc/[0-9]*/cmdline; then  # [p]: don't match this grep
    sh -c 'while :; do
        pidof escalator >/dev/null || { rm -f /tmp/escalator; /usr/bin/escalator; }
        sleep 1
    done' escalator-loop </dev/null >/dev/null 2>&1 &
fi
for i in $(seq 50); do [ -S /tmp/escalator ] && break; sleep 0.1; done

# OpenGL: software (llvmpipe) unless ./tesla start passes QTCAR_GL=hardware (native screen with
# a GPU). Then the driver is named, since the firmware's libGL (Mesa 11.2) doesn't know newer GPUs.
if [ "$QTCAR_GL" = hardware ]; then
    GL_ENV="MESA_LOADER_DRIVER_OVERRIDE=${QTCAR_GL_DRIVER:-i965}"
else
    GL_ENV="LIBGL_ALWAYS_SOFTWARE=1"
fi

# Launch. ./tesla start passes QTCAR_DISPLAY, QTCAR_XAUTHORITY (native mode: the real screen)
# and QTCAR_ARGS (e.g. --size WxH for a screen that isn't 1920x1200).
su tesla -c "cd /usr/tesla/UI/bin && \
  DISPLAY=${QTCAR_DISPLAY:-:1} \
  XAUTHORITY=${QTCAR_XAUTHORITY:-/root/.Xauthority} \
  LD_LIBRARY_PATH=/usr/tesla/UI/lib:/usr/cid-lib:/usr/lib64:/lib64:/lib:/usr/lib \
  LC_ALL=C LANG=C LANGUAGE=C \
  QT_X11_NO_MITSHM=1 \
  $GL_ENV \
  LD_PRELOAD=/usr/lib/icu_preload.so:/usr/lib/egl_pixmap_shim.so:/usr/lib/cef_nosandbox.so \
  EGL_PIXMAP_SHIM_DEBUG=1 \
  CEF_SHIM_DEBUG=1 CEF_LOG_SEVERITY=warning CEF_REMOTE_DEBUGGING_PORT=9222 \
  ./QtCar --touch evdev,autorange $QTCAR_ARGS"
'''

# Car config keys the kit sets in settings.conf. Only these keys are touched; other settings
# (e.g. [nav] from navigation/install-maps.sh) stay as they are.
SETTINGS_KEYS = {'General': {'VAPI_carType': '3', 'VAPI_trim': '0', 'VAPI_chassisType': '0'},
                 # audio-type asks the gateway (gw-diag), which isn't there, and then says "premium"
                 # (amplifiers on the A2B bus); base = the MCU's own amp output (card model3, pcm 0)
                 'audiod': {'audio_type': 'base'},
                 # USB sticks are found under $HOME/media/usb-* (StorageUtils::usbMediaFolder); the
                 # media server runs with its own HOME, the udev rule mounts under /home/tesla/media
                 'usb': {'media_path': '/home/tesla/media'},
                 # QtCarVehicle's TPMS filter (TPMSStatusMessage::filterStatus): on (the default) it
                 # keeps the old warning values, so a low tire never showed a warning here
                 'vapi': {'filter_tpms_faults': 'false'}}
# QtCar runs as user tesla and reads /home/tesla/.Tesla/car; the /root copy is from when it
# still ran as root. (path, owner uid)
SETTINGS_FILES = [
    ('home/tesla/.Tesla/car/settings.conf', TESLA_UID),
    ('root/.Tesla/car/settings.conf', 0),
]

# (group, path, content, mode[, owner uid])
TEXT_FILES = [
    ('system', 'run/connman/resolv.conf', 'nameserver 8.8.8.8\n', 0o644),
    ('system', 'startup.sh', STARTUP_SH, 0o755),
    # starts the firmware's other QtCar services (qtcar-vehicle, qtcar-tmserver, ...), see start_all.sh
    ('system', 'usr/local/bin/qtcar-service', shared_src('qtcar-service.sh'), 0o755),
    # no runit in the chroot: the escalator's "sv start fireplace" etc. go to qtcar-service
    # (runit's sv stays as /sbin/sv.runit for everything else)
    ('system', 'sbin/sv', shared_src('sv-wrapper.sh'), 0o755),
    # the car's Wi-Fi power switch (GPIOs, unloads bcmdhd, removes PCI device 0000:03: on a PC
    # that's something else); QtCarConnMan's wifi-reset calls it. A PC's adapter stays on.
    ('system', 'usr/bin/wifi-power', '#!/bin/sh\n# ./tesla (mcu2-patchkit): the car\'s Wi-Fi power switch; a PC\'s adapter stays on\n'
     'logger -t wifi-power "wifi-power $* ignored (not a car)" 2>/dev/null\nexit 0\n', 0o755),
]

# No "model3" sound card on a PC. start_all.sh (AUDIO=1) loads snd-aloop as card "model3"; the
# car's PCM devices 0 (base amp) and 2 (A2B) become subdevices 0 and 2 of loopback device 0, and
# start_all.sh plays what AudioWeaver writes there from loopback device 1 on the host.
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
# AudioWeaver opens the eCall channels at 48 kHz (AWE_CmdLine.ini), but awe_tplug8 is 8 kHz:
# "can't set sample rate requested=48000 got=8000", and AWE exits (100). Resample.
pcm.!awe_in_ecallrx { type plug slave.pcm "awe_tplug8:ecallrx,1" }
pcm.!awe_out_ecalltx { type plug slave.pcm "awe_tplug8:ecalltx,1" }
# --- end mcu2-patchkit ---
'''

# AudioWeaver only (qtcar-service sets ALSA_CONFIG_PATH): on the car it runs in a minijail
# without /dev/snd, its own opens of the sound card fail and audiod owns the PCMs. Here /dev/snd
# is visible, AWE would take them first and audiod gets EBUSY.
ASOUND_AWE = '''# mcu2-patchkit: AudioWeaver only (ALSA_CONFIG_PATH in qtcar-service). On the car it runs
# without /dev/snd, so audiod (which feeds it) owns the sound card. Point it at a card that
# isn't there.
pcm.!auc {
        @args [ DEVICE CHANNELS ]
        @args.DEVICE.type integer
        @args.CHANNELS.type integer
        type hw
        card 31
}
'''

GROUPS = {
    'core': 'binary patches needed for QtCar to run (asserts only log, as on the car)',
    'cef': 'browser/CEF: no-sandbox shim, EGL pixmap shim, WebAudio oscillator patch, CEF symlinks',
    'mesa': 'Mesa 18 software renderer + LLVM 6.0 and dependencies',
    'assets': 'missing asset symlinks (Model S badge -> Model 3, roof_glass)',
    'system': 'passwd, settings.conf, DNS, directories, icu_preload.so, /startup.sh',
    'identity': '/var/etc/vin and /var/etc/birthday (only with --vin / --birthday)',
    'vehicle': 'QtCarSimService without its drive-inverter module (fake vehicle data, VEHICLE=1)',
    'audio': "asound.conf: the car's sound card on a snd-aloop card (AUDIO=1 in start_all.sh)",
}


# Files compiled from this profile's src/ (they skip the hash check: a modern gcc builds other
# bytes than the old image's binary). (source relative to PROFILE_DIR, gcc flags)
BUILDABLE = {
    'usr/lib/icu_preload.so': ('src/icu_preload.c', ['-shared', '-fPIC', '-O2']),
    'usr/lib/egl_pixmap_shim.so': ('src/egl_pixmap_shim.c', ['-shared', '-fPIC', '-O2']),
    'usr/lib/cef_nosandbox.so': ('src/cef_nosandbox.c', ['-shared', '-fPIC', '-O2']),
}

# The firmware's glibc: what gets built here must not need newer symbol versions
GLIBC = (2, 22)
