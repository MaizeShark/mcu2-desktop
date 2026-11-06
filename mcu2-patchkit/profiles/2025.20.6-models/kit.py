"""Kit profile: Model S/X MCU2, firmware 2025.20.6 (older than 2026.8.3, same architecture).

Takes the tables of ../2026.8.3-models/kit.py (startup.sh, settings, sound card, escalator stub,
...) and replaces what depends on the firmware's build: the binary patches (same code, other
offsets) and the payload (this firmware has glibc 2.35 and GLIBCXX_3.4.29, the Debian 13 files
need 2.39: see sources.toml). Mesa is the same 25.0.7 with llvmpipe; only its LLVM is 15, not 19.
"""
import os

BASE_DIR = os.path.join(os.path.dirname(PROFILE_DIR), '2026.8.3-models')   # also goes into the kit version
_own_dir, PROFILE_DIR = PROFILE_DIR, BASE_DIR
with open(os.path.join(BASE_DIR, 'kit.py')) as _f:
    exec(compile(_f.read(), os.path.join(BASE_DIR, 'kit.py'), 'exec'))
PROFILE_DIR = _own_dir

# ---------------------------------------------------------------------------
# Binary patches: what they do is explained in ../2026.8.3-models/kit.py
# ---------------------------------------------------------------------------
BINARIES = [
    {
        'path': 'usr/tesla/UI/lib/libQtCarGUI.so.1.0.0',
        'sha1_orig': '77fb9d630222c423d94f072524f26de3805ea409',
        'patches': [
            ('camera-deserializer-ready', 'camera', 0x25aa650,      # CameraDevice::deserializer_ready()
             '415641554154', 'b801000000c3',
             'use the supplied desktop camera without a car deserializer'),
        ],
    },
    {
        'path': 'usr/sbin/connmand',
        'sha1_orig': '40733645ebf39fbb1fe57f85da1959e595a18512',
        'patches': [
            ('connman-no-car-apparmor-hat', 'wifi', 0xb5d1d,
             '0f855d5a0000', '909090909090',
             'continue when the car-specific AppArmor hat cannot be entered'),
        ],
    },
]

# Chromium 127: the same layout as 140, each scalar routine at the same distance before the SHA-NI one
for _chromium_process in ('main', 'gpu', 'utility', 'zygote'):
    BINARIES.append({
        'path': f'usr/lib/tesla-chromium/tesla-chromium-{_chromium_process}',
        'sha1_orig': '5f979b0a1941550c7fa733e5cdf159826d88931d',
        'in_place': True,  # hardlinks, as in 2026.8.3
        'patches': [
            ('chromium-sha1-software', 'media', 0x3651a40,
             'f30f6f0766', 'e9dbefffff',
             'use Chromium’s scalar SHA-1 on CPUs without SHA-NI'),
            ('chromium-sha256-software', 'media', 0x3af47c0,
             '488d0d79ba', 'e97beeffff',
             'use Chromium’s scalar SHA-256 on CPUs without SHA-NI'),
        ],
    })

for _game, _sha1, _offset in [('2048/2048', '67d39a10630b41931a37fbd313fe3065ee2d3681', 8268568),
                              ('Chess/Chess', '4aa4f0000734a2117e9a8d16e25398854869ee12', 8456984),
                              ('Backgammon/Backgammon', '2e583fe3f616ccab79b632afe448f729472ea8f0', 8600344)]:
    BINARIES.append({
        'path': 'usr/tesla/UI/standalone_apps/' + _game,
        'sha1_orig': _sha1,
        'patches': [
            ('game-touch-autorange', 'games', _offset,
             '65766465763a2f6465762f696e7075742f746f7563682c726f746174653a39302c72616e67653a343039362c696e7665727458',
             '65766465763a2f6465762f696e7075742f746f7563682c6175746f72616e676500000000000000000000000000000000000000',
             'read the touchscreen in screen coordinates, as QtCar does'),
        ],
    })

# ---------------------------------------------------------------------------
# Files the kit adds (sources.toml; cached in payload/2025.20.6-models/)
# ---------------------------------------------------------------------------
PAYLOAD_FILES = [
    ('mesa', 'opt/mesa/lib/libgallium-25.0.7-2~bpo12+1.so', 0o644, 'eb7ee9e6be2e5c7864e6462e2685e80009662260'),
    ('mesa', 'opt/mesa/lib/libEGL_mesa.so.0', 0o644, '36302c369a256eebc972564c3eea4224adcf82eb'),
    ('mesa', 'opt/mesa/lib/libGLX_mesa.so.0', 0o644, 'f21d50d5bc0704f467e50eeaf21306068f59d681'),
    ('mesa', 'opt/mesa/lib/libgbm.so.1', 0o644, '138ddbc23efe1c67394d4ea75baf952621cc720e'),
    ('mesa', 'opt/mesa/lib/gbm/dri_gbm.so', 0o644, '36880b7887386e72dd48b61d25e22f654bcdf153'),
    ('mesa', 'opt/mesa/lib/dri/libdril_dri.so', 0o644, '58fe5503abe11dd1e07fb5e545f7c521396bba5e'),
    ('mesa', 'opt/mesa/lib/libEGL.so.1', 0o644, 'a2da2e8a87e76d704f509db7b72ba9e20be86c9e'),
    ('mesa', 'opt/mesa/lib/libGL.so.1', 0o644, '5cc660a162cd07d3db97cbaa58bcd3bb89f29581'),
    ('mesa', 'opt/mesa/lib/libGLX.so.0', 0o644, '77e7ce1dfa2489b1aef02c9173fcf02ee2af08bb'),
    ('mesa', 'opt/mesa/lib/libGLdispatch.so.0', 0o644, '4b47cdf9a9b2e1eb1899a0d8779eab0ebcd40b02'),
    ('mesa', 'opt/mesa/lib/libGLESv2.so.2', 0o644, '25582a9633537b12323031e6bc3af008381743e5'),
    ('mesa', 'opt/mesa/lib/libdrm.so.2', 0o644, '86793d6cf9f145460205961f39517b544b3f1638'),
    ('mesa', 'opt/mesa/lib/libdrm_intel.so.1', 0o644, '3761cb347823b57798cfc27c4af6e633755d878b'),
    ('mesa', 'opt/mesa/lib/libdrm_amdgpu.so.1', 0o644, 'fd03cd0efdf71aab6c680fc8c7f70fefbe26710c'),
    ('mesa', 'opt/mesa/lib/libbsd.so.0', 0o644, 'fed6915970c8f8895cf2df73decad75abecf1e97'),
    ('mesa', 'opt/mesa/lib/libedit.so.2', 0o644, '354852bc1e4bc70972f5d241e0a44391a924efac'),
    ('mesa', 'opt/mesa/lib/libelf.so.1', 0o644, 'e734c3f19604af6eb40b5ad0693e181c1a31879d'),
    ('mesa', 'opt/mesa/lib/libmd.so.0', 0o644, 'f3dfddc728e623482805c3a53c4830e0ebe7495c'),
    ('mesa', 'opt/mesa/lib/libsensors.so.5', 0o644, '1d11ca147592f3a8c1478376e0403f0bc56f7097'),
    ('mesa', 'opt/mesa/lib/libtinfo.so.6', 0o644, '1aa7a9af81fc1d4ac839eb28af6bdac71d46514a'),
    ('mesa', 'opt/mesa/lib/libwayland-client.so.0', 0o644, 'f30fc7660432420bfe575674af51747e8c1856d8'),
    ('mesa', 'opt/mesa/lib/libwayland-server.so.0', 0o644, 'e79b7f1f8d89834b96e71045688d02c11ebb633e'),
    ('mesa', 'opt/mesa/lib/libzstd.so.1', 0o644, '2001f522271305b7e8b6fd773462e78ff405a587'),
    ('mesa', 'opt/mesa/lib/libLLVM-15.so.1', 0o644, '8867c0183d9d005971f0604905141d84ad4cf5a2'),
    ('mesa', 'opt/mesa/lib/libstdc++.so.6', 0o644, '234c6fd91ba1ef9372803d1a20b2f6a8ad07cfa1'),
    ('system', 'usr/bin/gdbserver', 0o755, '5a2eed8ecea20457654953f1f01f22ca919ce18c'),
    # a real crashpad handler (the firmware's is a no-op, Chromium aborts; see sources.toml) + its libc++
    ('media', 'usr/lib/tesla-chromium/chrome_crashpad_handler', 0o755, '9043d738ccdcfe4f300ae57ce5c139a8c09ef6c2'),
    ('media', 'usr/lib/tesla-chromium/libc++.so.1', 0o755, '3a99bd87e726fe6ea11540f3070f372ae424b4f0'),
    ('media', 'usr/lib/tesla-chromium/libc++abi.so.1', 0o755, '61f6a3bb0cf29e29e3a063add5a58ab9bf81ca86'),
    ('media', 'usr/lib/tesla-chromium/libunwind.so.1', 0o755, '2937a0b4c54d6697c3e9f8aa08a6fd6533c525fb'),
    ('media', 'usr/lib/egl_media_pixmap_shim.so', 0o755, '-'),
    ('system', 'usr/local/bin/escalator-stub', 0o755, '-'),
]

# 2025's asound.conf calls the music app's output apple_music and has no input for it (its run script
# passes no --alsa-input-device); with 2026's names Chromium opens no PCM and TuneIn & co. stay silent.
TEXT_FILES = [(g, p, c.replace('Debian 13', 'Debian 12 backports').replace('2026.8.3-models', '2025.20.6-models')
                      .replace('--alsa-input-device=plug:chromium_voice --alsa-output-device=plug:chromium_app',
                               '--alsa-output-device=plug:apple_music'), m)
              for g, p, c, m in TEXT_FILES]

GROUPS = {**GROUPS,
          'mesa': 'Mesa 25.0.7 with llvmpipe + glvnd + LLVM 15 (Debian 12 backports, Ubuntu 22.04) in /opt/mesa'}

GLIBC = (2, 35)

BUILDABLE = {path: (os.path.join('..', '2026.8.3-models', source), flags)
             for path, (source, flags) in BUILDABLE.items()}
