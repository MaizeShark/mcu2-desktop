# Model S/X parity with the Model 3 (plan, from session 14)

Audit of 2026-10-21 (session 14): every Model 3 feature against the 2026.8.3 S/X profile.
`model-s` is merged into `main` once this list is done (or the rest is accepted as a limit).
Status per item: open / done (session) / limit.

## Verified on the S (sessions 9-13)
UI + touch over VNC (center + cluster), `--native` on X11 (T480 Xfce: rotation, iris GPU),
online map tiles (needs a country), host connectivity, Browser (Chromium 140), web media
(TuneIn plays), Theater (YouTube/Twitch play), GPS fixed position, reverse camera, Bluetooth
pairing + A2DP, Wi-Fi scan, Chromium/Browser/A2DP sound, vehicle data `parked`.

## Untested on the S
| Item | Finding | Effort |
|---|---|---|
| USB music | the S kit lacks the Model 3's `[usb] media_path`; playback needs the car sound (6) | S |
| GPS route driving | `tesla-gps --can` speaks Model 3 signals; the S has no `DI_odometer` | S-M |
| Panel live values | read from qtcar-vehicle over HTTP :4030; the S logs "local data value endpoint" warnings, answers unconfirmed | S |
| `build-stick` with tesla-s.conf | profile-aware (CAN DB), never built or booted | M |
| `--native` on Wayland (both) | relies on xrandr on :0; Niri's :0 is a rootless Xwayland (no rotate/transform) | M |
| GPS receiver, Bluetooth calls | never tested | - |

## Missing, in the order agreed with the user
1. **Smaller things + Chromium segfaults at stop** (XS-S)
   - `Invalid method name _data_get_value_request_`: start.sh's speech-state loop and
     tesla-gps.py call HTTP :4220, on the S QtCar's Spotify client.
   - `/run/chromium/policies/chromium-app`, `/chromium`: startup.sh creates only chromium-fullscreen.
   - chromium, chromium-app, chromium-fullscreen exit 139 at every stop.
   - kernel segfault `CenterDisplayDe` in libQtCore at QtCar's first self-restart.
2. **Escalator stub + fake GPIO** (M). QtCar's socket is `/tmp/escalator`. Commands in the
   firmware: drm-status-detect, read-dpcd, CLUSTER_POWER, set-gpio, start-/stop-fireplace,
   -dog-mode, -mame, start-tesla-game-2048/-chess/-backgammon, chromium-fullscreen, ...
   Serve the display ones, launch the allowed apps, refuse the rest. QtCar reads
   `/sys/class/gpio/gpio467/value` (BMP-LCD-PWR-EN) itself; the PC has no /sys/class/gpio.
   Never run the real escalator.
3. **Offline navigation** (S-M). Same Valhalla 2.4.3; the S's valhalla_run_route routes on
   `navigation/work/tiles-US`. Profile `maps = true`, install-maps for the S, sandbox test.
4. **Videos** (fireplace, dog mode, camp mode; no HAL 9000 in this firmware) and **arcade**
   (MAME + 9 ROMs, 2048/chess/backgammon standalone apps; Beach Buggy Racing 2 and the other
   downloadable games aren't in the dump: limit). Need 2.
5. **Vehicle data** (L): presets drive/charging/doors/lights/tires/climate, answers to the S's
   UI requests (none of the Model 3's request signals exist on the S), charge model, car look
   from GTW_carConfig*.
6. **Car sound channels** (L, uncertain): the S's audiod has AudioWeaver built in
   (`/opt/audioweaver`), plays to `auc` on card `modelsxinfo2`; the Model 3's loopback recipe
   (snd-aloop card of that name, asound override, A2B/GPIO/audio-type checks off).
7. **`--native` on Wayland** (M, both firmwares).
8. **The stick with the S** (M): build, qemu boot.

Not a parity gap: Spotify / YouTube Music sign-in (Tesla's backend, session 11).

## Progress
(updated as items get done; details in the session notes)

Session 14 (2026-10-21), all tested in the sandbox/podman, not yet in a real run:
- 1 done: Chromium browsers stop first (exit 0 instead of 139); no HTTP data values to the S's
  QtCar (:4220 is its Spotify client); policy dirs. The kernel segfault (`CenterDisplayDe`) came
  at the stop, from the display thread of the panel loop: check it again after `--emulated`.
- 2 done differently: QtCar `--emulated` (Tesla's desktop mode) ends the panel loop and the GPIO
  reads (10 204 -> 350 ERROR lines in 90 s, 148 -> 108 % CPU in the sandbox). The escalator stub
  exists anyway (for 4): display/backlight/cluster requests answered "ok".
- 3 done: `maps = true`; QtCar "maps found", Valhalla answers TMServer. Routing in the UI untested.
- 4 partly: escalator stub launches fireplace, dog mode, camp mode, MAME (podman: fireplace plays,
  MAME 61 FPS). Open: from the UI (sandbox touch doesn't reach the S QtCar), MAME's picture/keys
  in QtCar, 2048/chess/backgammon (`app-service.bash` environment), sounds (6).
- Panel live values: verified (qtcar-vehicle answers HTTP on the S, with a usage warning).
- USB music: `[usb] media_path` set, media server on QtCar's display; open: its session bus and
  gvfs (`dbus-session-mediaserver`, `gvfs-session-mediaserver`), indexing, sound (6).
- First real run: ERROR lines 15 248 -> 347. Chromium still crashed on Ctrl+C (tty SIGINT to all
  services): services now in their own session. Videos/arcade blocked by "Audio Initializing"
  (`GUI_audioReady`, kept true now) and dog mode by `HVAC_systemNominal` (CAN, item 5); arcade
  downloads need Tesla's update servers (limit).
- Second real run: Chromium stops cleanly, routing works (route not drawn on the map), MAME runs
  (cursor issue), 2048 makes QtCar exit (startGameMode fails), fireplace stays at "loading"
  (TVideoView doesn't show tvideo's window). Details: session 14 notes.
- After it: 2048/chess/backgammon launcher + game mode ok (podman), fireplace sink changed to
  ximagesink (unverified), route line still open ("updateRoutePath, empty route").
- Third run: fireplace works; MAME pointer free (-nomouse on VNC), its games need the S's
  steering wheel keys (5); chess/2048 cut off: QtCar gives them a 16:9 strip because no app is
  registered for their lifecycle state (details in the notes); stop now kills the restart loops
  first.
- Late: the window-size patch doesn't fix the games (QtCar asks with an empty region at launch);
  tiles with elevation for the route line (build-tiles.sh). See the last notes follow-up.
- Later: the games' window found (QtCar asked the PC's :0 for the center display size; the shim
  maps it to QtCar's display): 2048 right in the sandbox; their touch device (input-to-virtual)
  started again, untested; elevation tiles built.
- 2026-10-22: games draw completely with MIT-SHM (Xvfb as root, untested for real), touch via a
  patched default, route origin from the GPS position (untested).
- 2026-10-22, all sandbox-tested only: vehicle presets and request answers for the S (5, lights
  missing), the car's sound through the S's audiod/AudioWeaver (6), the stick with the S (8: see
  notes). Open: 7 (Wayland native), lights, frunk request, the car's look, MAME keys (steering
  wheel signals of the S).
- Stick (8): the S image boots in qemu and shows its UI (Xorg as root on the stick); hardware
  untested.
- 2026-10-22, the user's first run: 2048/chess work (root Xvfb, touch). Fixed after it: the root
  Xvfb died at once (sudo's pipe, SIGPIPE), audiod restarted 96x (D-Bus name, kit policy for
  root). Vehicle data (5), sandbox: lights (BC_* -> LIGHT_*), high beam, turn signals, hazards,
  parking brake, belt, outside temperature, tires, speedometer (DI_analogSpeed), frunk request
  (MCU_frontHoodCommand), light switch/fog/parking brake requests; the panel got tabs and per-car
  controls, and no longer crashes QtCarVehicle (one kept-open connection). Open: the car's look,
  MAME keys, the dog screen.
- Cluster: the car's `--ic --window 1.5` (1920x720 layout; it was the 1280x480 one in a corner).
  Radio input: a silent loopback instead of the car's Ethernet stream (audiod pump errors).
- Car sound (6) works in a real run (TuneIn, 2026-10-22): media bridges via the stub, both
  amplifiers in the mix (measured channel map), sound card ACL for the chroot's audio gid,
  pw-play on the default output. Still to check: chimes, USB music, Bluetooth, the levels.
- The user's run: sound ok. The clock showed Los Angeles time (the car asks Google for the zone):
  `TIMEZONE=auto` (this PC's) is written to QtCar's settings at each start, both firmwares.
- The car's look for the S: `--color`, `--wheels`, `--performance` (QtCar, cluster, car config
  together; sandbox-tested).
- Climate for the S (THC response, recirculation, heaters) and the dog screen (display state
  machine on with --vehicle, panel Driver switch): sandbox-tested.
- The user's run (2026-10-27, session 15): clock, Pearl paint, climate panel, dog screen and back,
  display state machine in normal use, chimes vs music: all fine.

Session 15 (2026-10-27), each confirmed in a real run by the user unless noted:
- Charge model for the S (`ChargeModelSX`: CHG_*/BMS_* signals, the UI's current and limit,
  Stop/Start, complete at the limit; the cluster's mi/hr via host-connectivity.py --copy).
- MAME keys: the panel's Arcade tab sets the S's steering wheel scroll wheels (STW_status).
- USB music: indexed and played (the S looks in /mnt/media/usb-*, not [usb] media_path).
- Wiper heater toggles (sandbox; QtCar sends no CAN request for it here).
- Tools: tesla-can.py --set/--status use the running instance's database; sandbox.sh
  --no-cluster keeps the S's QtCar options and home.

- Bluetooth music through the S's audiod (a2dpbridge via the escalator stub): works (Pixel 7a).

Merged into `main` on 2026-10-27 (the user: ready for GitHub; `--native` on Wayland not needed).
Left open, as limits for now: the route line on the elevation tiles
(user: "all seems to work", not confirmed separately), roof colour / Model X look / badge
(optional), 7 (`--native` on Wayland, with the user), 8 (the stick on hardware), then the merge.
