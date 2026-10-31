# Notes

How everything here was found out, session by session. The user guide is `../README.md`; the
component READMEs have the current details. These notes are history: later sessions supersede
earlier ones where they disagree. Up to session 7 they call the unit "MCU3" and the folders
`mcu3-*`: it's the Model 3's Intel Atom **MCU2**; renamed on 2026-09-25 (`mcu2-*`).

| Note | What |
|---|---|
| `session1-2026-05-15.md` | the first chroot: squashfs dump, Mesa swrast, the binary patches QtCar needed to start |
| `session2-2026-05-16.md` | touch (QtCar's own EvDevTouchDriver, `/dev/input/touch`), early helper scripts |
| `session3-2026-09-22-23.md` | reconstructing the image as the patch kit, tesla-touch, firmware services (`qtcar-service`), offline navigation, the start of the browser work |
| `session4-2026-09-23.md` | fake GPS, vehicle data (simulator, CAN signal database), the browser (CEF without sandbox, V8 snapshot, WebAudio crash), the sandbox tooling |
| `session5-2026-09-23.md` | telltales, presets and the web panel, per-user service homes, real GPS receivers, UI buttons, audio (AudioWeaver/audiod on snd-aloop), backup camera, videos, USB music, Spotify/TuneIn dead ends |
| `session6-2026-09-24.md` | the `./tesla` command (start/stop/status/check/logs/build-image, tesla.conf), kit version stamp, panel services, docs |
| `session7-2026-09-25.md` | all CAN signals on the panel; the arcade (BBR2 and MAME): input devices, GPU, window manager, focus, sv verbs |
| `session8-2026-09-26.md` | the bootable USB stick (`./tesla build-stick`, podman + mmdebstrap, qemu tests); the chroot's syslog; CAMERA/BLUETOOTH=auto |
| `session9-2026-09-27.md` | the Model S MCU2 firmware 2026.8.3: inventory against the Model 3's, first runs of the S UI and cluster in the sandbox |
| `session10-2026-09-27.md` | Model S online vector maps: the TileManager internet gate, missing country on the T480, and stale shared memory |
| `session11-2026-09-28.md` | Model S/X streaming stack and Theater: Chromium web apps in QtCar, touch, TuneIn playback, public video, account and proxy blockers |
| `session12-2026-09-28.md` | Model S/X T480 hardware: simulated GPS, Wi-Fi scan and ConnMan patch, reverse camera feed, Bluetooth stack and discovery |
| `session13-2026-10-21.md` | Fedora 44 (x11vnc under Wayland, pw-play --raw, Xvfb survives Ctrl+C); both firmwares tested there |
| `session14-2026-10-21.md` | Model S/X parity audit; QtCar `--emulated` (no panel loop), the escalator stub (videos, MAME), S offline navigation, clean Chromium stop |
| `session15-2026-10-27.md` | Model S/X charging (charger/BMS signals, the cluster's rate), MAME scroll wheels, USB music at /mnt/media, wiper heater; sandbox.sh and tesla-can.py fixes |
| `session16-2026-10-31.md` | A cellular modem: the car's oFono/Telit stack, what the status bar reads, the host's ModemManager as the car's LTE (`--cellular`), the stick's modem packages; sandbox SELinux fix |
| `model-s-parity.md` | the Model S/X parity list (verified / untested / missing), the agreed order and its progress |
| `ideas.md` | ideas that aren't planned yet (a real HW2.5 Autopilot computer on the PC's Ethernet) |
| `next-session-prompt.md` | the prompt for the next session |
