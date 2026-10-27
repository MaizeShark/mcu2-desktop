# Features

Each section: what it does, how to switch it on, what it needs, how it works, where to look when
it doesn't, and its limits. The overview table is in [README.md](README.md#what-needs-what).

## The UI and touch

Always on. QtCar, the firmware's UI, runs in the chroot on a virtual X display with a software
OpenGL renderer (Mesa 18 with LLVM 6, from the patch kit). All apps and settings open, the car is drawn
in 3D, the map shows online tiles.

- Needs: the patched image, Xvfb, x11vnc, `tesla-touch`, a VNC viewer.
- How: `tesla-touch` creates a virtual multitouch device (`/dev/uinput`) and bind-mounts its
  event node as `/dev/input/touch` into the chroot, where QtCar's own touch driver reads it. It
  follows the X pointer of the virtual display, so VNC clicks become touches. Details:
  [`tesla-touch/README.md`](../tesla-touch/README.md).
- Logs: `qtcar.log` (QtCar; many ERROR lines are normal: services and hardware that aren't
  there), `touch.err`.
- Limits: the "touchscreen unresponsive" alert can show without touch. The UI assumes a car that
  answers; without `--vehicle` it shows "no car" states.

**Native screen** (`--native`): the same on the PC's own screen, scaled to fit with black bars,
with a real touchscreen passed through (see [usage.md](usage.md#on-the-pcs-own-screen-native)).

## Model 3 Browser (2019.20.4.2)

On the 2019 Model 3 image, the Web app is Tesla's Chromium Embedded Framework build (Chrome 73).

- How: the kit preloads `cef_nosandbox.so` (the Chromium sandbox can't work in the chroot),
  installs the missing V8 snapshot files and patches WebAudio's `createOscillator()` to return
  null (it crashed the renderer: Tesla's build has no FFT). DevTools: `curl http://127.0.0.1:9222/json`.
  Touches: QtCar replays them on its own uinput touchscreen (`tesla-uinput`) and the X server
  delivers them to the browser's (invisible) window, as on the car. With `--native` the preloaded
  `egl_pixmap_shim.so` keeps that window away from the desktop's window manager (on top, opacity
  0, no focus). Over VNC, Xvfb doesn't read that touchscreen: the shim sends taps as clicks and
  drags as wheel steps instead, and the udev rule keeps the desktop from taking it (else the
  desktop's pointer jumped with every touch in the browser).
- Limits: an old browser; pages that need a newer one fail. Pages that create a WebAudio
  oscillator lose that feature. Over VNC there are no multi-touch gestures (pinch zoom).

## Videos: fireplace, dog mode, HAL 9000

Always on (Toybox and the climate screen). QtCar asks its privileged helper (the escalator) to
start them with runit's `sv`; the kit's `/sbin/sv` wrapper starts `/usr/bin/tvideo` on QtCar's
display instead. Sound with `--audio`.

Model S/X 2026.8.3: fireplace, dog mode and camp mode (no HAL 9000 in this firmware). The car's
escalator never runs here; the profile's `escalator-stub` answers QtCar's requests on
`/tmp/escalator` from a fixed table (start/stop of the videos and MAME go to the `sv` wrapper,
display and backlight requests get an "ok", everything else is refused and logged in
`escalator-stub.log`). The fireplace played through the stub and the S's GStreamer `tvideo` in
podman; started from the UI it's untested. Their sounds need the car's audio (not yet on the S).
QtCar waits for its audio system before the videos and games ("Audio Initializing"): `./tesla
start` keeps `GUI_audioReady` true for the S. Dog mode also needs the car's HVAC to report
nominal (`HVAC_systemNominal`, from CAN): "Climate Keeper Unavailable" until the vehicle data has it.

The S's dog screen ("My driver will be back soon", the temperature, the dog) needs `--vehicle`:
it's a state of QtCar's display state machine, which `./tesla start` turns on for the S with
vehicle data (`[display_state] enable_display_state_manager`; without vehicle data it kept the
display off, so it stays off then). Switch Dog on in the climate panel, then the web panel's
Car tab, Drive, **Driver** off: the driver leaves and the drive rail turns off, the screen
changes to the dog screen. Driver on again: back to the normal screen, dog mode off. Without dog
mode, Driver off puts the screen in standby ("Accessory": off after 30 minutes, a touch wakes it).

## Offline navigation

On when maps are installed (`--no-nav` switches it off).

- Needs: map tiles built with [`navigation/`](../navigation/README.md) (podman, an OSM extract),
  installed by `./tesla build-image`. One region at a time.
- How: QtCar asks `QtCarTMServer` (service `qtcar-tmserver`) for routes, which asks Tesla's
  modified Valhalla 2.4.3 (`valhalla`, HTTP on :8002) on the tiles in
  `/opt/navigon/tm/<region>/valhalla`. The kit and `install-maps.sh` set the settings QtCar
  needs (`[nav] forcePremiumNav`, the map region).
- Logs: `valhalla.log`, `qtcar-tmserver.log`.
- Limits: **online routing doesn't work** (it needs the car's credentials / key for Tesla's
  servers), so only places inside the installed region can be routed to. Address search and
  geocoding go to Google online and work. Traffic, superchargers availability and similar online
  data don't load.

The 2026.8.3 Model S/X firmware has the same Valhalla 2.4.3, and the same tiles work (built with
elevation since 2026-10-22: without it the S draws no route line; `navigation/README.md`): its
`valhalla_run_route` routed Palo Alto to San Francisco on them, and in the sandbox QtCar found the
maps and the nav map server (session 14). `./tesla --config tesla-s.conf build-image` installs
them; `NAV=0` in `tesla-s.conf` (from before) switches navigation off. A routed destination in
the S's UI is not tested yet. Its on-screen map uses online vector tiles. QtCar needs both an internet-available signal and a country for Google's
`region_code` request parameter. Without `--wifi`, `./tesla start` probes the host's internet access and sends it
through QtCar's `LINK_linkState`, `LINK_wifiState`, and `CONN_connectedToInternet` data values.
CenterDisplay then updates `GUI_canUseInternet` for maps; MediaV2 independently uses
`LINK_wifiState=online` to report internet availability to media sources, and ChromiumAdapter
uses `CONN_wifiConnected=true` to clear the web apps' connectivity gate. The earlier
TileManager binary patch is retired by
`./tesla build-image`; existing images need that update to restore the original bytes. The probe
and data-value updates are logged in `logs/latest/host-connectivity.log`. With `--vehicle`, the
fake gateway's parked preset supplies `US` as
the car country. Without `--vehicle`, a fresh image can have `map/MapCountry` unset: tile requests
then receive HTTP 400. While QtCar is stopped and the image is mounted at `chroot/`, seed it with:

```sh
sudo python3 vehicle/qtcar-settings.py chroot/home/tesla/.Tesla/data/QtCarSettings.db \
    VAPI_countryCode=21843 VAPI_priorCountryCode=21843 map/MapCountry=US
```

Choose a different two-letter country if appropriate; the numeric value is its two ASCII bytes
read as a big-endian integer. This is separate from Valhalla's offline routing tiles.

## Model S/X streaming media (2026.8.3)

`./tesla --config tesla-s.conf start` starts the firmware's `media-webapp-server`, Chromium
session bus, `chromium-adapter`, `chromium-app-input` and `chromium-app` alongside
`qtcar-spotifyserver`. The web server binds only to `127.0.0.1:9000`; Chromium's debugging
interface binds only to `127.0.0.1:9223`. The browser window renders into QtCar's media panel
through the kit's EGL pixmap shim. `tools/media-touch.py` forwards touches to that offscreen
window on both native and VNC displays; the firmware's input helper alone did not deliver native
touches to the lower web content. The bridge sends events without waiting for a DevTools reply
after each tap. The input helper's virtual device is named `game-chromium-touch` on both native
and VNC displays so the installed udev rule keeps the host desktop from treating it as a
touchscreen. Native startup
passes QtCar's X display and Xauthority to the media jobs, including on a rotated screen.
Check `./tesla status`, `logs/latest/{chromium-app,chromium-adapter,
media-webapp-server,chromium-app-input,media-touch}.log` and
`logs/latest/host-connectivity.log` when a source does not open.

The T480 VNC test opened TuneIn's public catalog, browsed its Music category by touch, and
played a station. A later native T480 run mapped the browser window on `:0`, opened source
pages, and the user confirmed that the touch bridge made TuneIn's lower web content interactive.
With `--audio`, the browser wrote nonzero stereo PCM to the firmware's
`virtual` loopback card and the host forwarded it to PulseAudio. Other sources reached these
states without an account:

| Source | Open / browse | Play |
|---|---|---|
| TuneIn | Public catalog and station categories work | Public station reached `playing`; PCM captured |
| Spotify | Login page opens but says “Connection failed” | Blocked before sign-in |
| Apple Music | Page opens; developer-token request fails, “Something went wrong” | Blocked before sign-in |
| Amazon Music | QR sign-in page opens | Needs a real account and QR sign-in |
| Audible | Sign-in page with a QR code opens | Needs a real account and QR sign-in |
| Apple Podcasts | Login page opens; “Failed to begin login process” | Blocked before sign-in |
| YouTube Music | Google activation page opens, but stays there after external sign-in | Token polling is blocked by the absent local Tesla music proxy |

Spotify's page requests `/vehicles/spotify/device_code` from the firmware's mothership proxy on
port 9002. The test image has no running proxy, provisioned car certificate/key, selected driver
profile (`GUI_currentProfileUUID` is empty), or saved media credentials. The firmware does ship
`hermes_proxy`, but its music proxy requires the car credentials to reach Tesla's backend.
SpotifyServer itself reported wireless connectivity and `SPOTIFY_sdkHasConnectivity=true`, then
`LoggedOut`/`NeedAcceptTC` and “no credentials saved.” A real Tesla backend path, driver profile
and the user's own Spotify sign-in are required to continue. No account tokens or passwords are
supplied by the kit.

YouTube Music similarly polls `/vehicles/youtube_music/auth_token` on the local Tesla music
proxy at port 9002. In a native T480 run, the user completed external Google activation twice,
but Chromium logged `Failed to fetch` for this poll and stayed on the login page. The test image
has no listener on port 9002 or provisioned car credentials; repeating the external sign-in
does not complete the in-car session.

## Model S/X Browser (2026.8.3)

The separate Browser app uses the firmware's Chromium 140, its own session bus and
`chromium-input` service. The patch kit starts all three jobs with the base services. DevTools
listens on `127.0.0.1:9224`. Browser needs no Tesla car credential for ordinary public pages.
On the T480, a fresh VNC run rendered Browser in QtCar, loaded `https://example.org/`, followed
its link by touch, and scrolled a long local test page. `tools/browser-touch.py` forwards touches
to the offscreen X11 window so both page content and the address bar receive them. Native GPU
rendering also needs the EGL shim's X pixmap copy path; the GPU's direct EGL import returned a
black Browser view despite QtCar receiving a first frame. The T480 user confirmed the rebuilt
native Browser and its on-screen keyboard work. While QtCar's keyboard is visible, its rectangle
is excluded from Browser touch forwarding; otherwise those taps can blur the address field.
Check `logs/latest/{chromium,chromium-input,dbus-session-chromium,browser-touch}.log` for startup
or input faults. Rendering uses the same EGL pixmap shim as the media window.

## Model S/X Theater (2026.8.3)

The Theater tiles use a third Chromium instance, separate from Browser and the music web apps.
The patch kit starts its session bus, input helper and persistent offscreen `Theater` window before
QtCar. This lets QtCar attach to the window when it first discovers app windows. On tile selection,
`tools/theater-control.py` reads QtCar's selected homepage policy and navigates that window through
Chromium's localhost DevTools port 9225; dismissing Theater returns it to `about:blank`.
`tools/browser-touch.py` forwards Theater taps to the offscreen window. The bundled firmware's
escalator command cannot launch this instance in the chroot, so a `Failed to execute
start-tesla-chromium-fullscreen` line can coexist with a working Theater page. The policy and
runtime directories are created by the kit. The window hides Chromium's normal toolbar.

On the T480 native display with `--audio`, live tests reached:

| Tile | Observed result |
|---|---|
| YouTube | Public site opens; consent can be dismissed by touch. |
| Tutorials | Opens Tesla's YouTube playlist; a public video played visibly. |
| Netflix | Public landing page, catalog previews and sign-in entry open; account playback and DRM untested. |
| Hulu | The tile's `hulu.com/app` URL redirects to a Disney+ public landing page in the test region; account playback untested. |
| Twitch | Public live catalog opens; a stream played visibly after touch. |
| TikTok | Public feed and video content render; sustained playback untested. |

Swipe down from the top Theater handle to dismiss it. For a blank or stuck view, check
`./tesla status` for `dbus-session-chromium-fullscreen`, `chromium-fullscreen-input`,
`chromium-fullscreen`, `theater-touch` and `theater-control`; inspect their logs and
`qtcar.log` for `AppWindow TheaterView, first frame received`. Theater uses the
`audio-browser` loopback path with `--audio`, but its audible output was not separately
confirmed in these tests. Theater's YouTube site is distinct from the YouTube Music source,
whose sign-in still needs the Tesla music proxy.

## Arcade

Always on (launcher -> Arcade): Beach Buggy Racing 2 (Tesla's own port, "cobalt") and the MAME
classics (Asteroids, Missile Command, Centipede, Lunar Lander, ...). 2048 is part of QtCar.

- How: QtCar asks the escalator to start the game (`sv up mame`, `start-tesla-game-cobalt`); the
  kit's `/sbin/sv` wrapper runs it through `qtcar-service` instead of runit, and stops it again on
  EXIT (`sv DOWN mame`, `sv force-stop cobalt`).
- **Beach Buggy Racing 2** draws its own window at QtCar's game area (0,60 1920x1140) and reads
  four evdev devices: `game-touch` (made by `input_to_virtual` from the touchscreen, mapped into
  the game window), `game-steering` and `game-scroll-left/-right` (made by `qtcar-vehicle` while
  the game runs, from the steering angle, the brake pedal and the scroll wheels). On the car you
  steer with the wheel and brake with the pedal; the car accelerates by itself. Here they come
  from CAN signals: needs `--vehicle`, and the panel's **Arcade controls** (slider, keys, a
  gamepad) set them. Touch works without it (on-screen arrows and pedals).
- **MAME** games: QtCar shows the game's picture itself and sends the keys with XTest to MAME's
  window, which stays below QtCar: COIN/START in the top bar, the rest with the scroll wheels
  (`GUI_steeringWheelControlsMode` TeslAtari), i.e. the panel's scroll wheel buttons.
- Native screen: the car has no window manager. `tools/game-windows.py` (started by `./tesla
  start`) takes the game windows back from the desktop's: BBR2 at QtCar's place and on top, MAME
  below QtCar with the keyboard focus. It also hides the mouse cursor while the run lasts when a
  touchscreen is used (QtCar moves it with the steering wheel, for the trackball games);
  `--cursor` / `--no-cursor` (`CURSOR=`) decide it yourself.
- Speed: both render on the GPU with `--native` on Intel (the game users get `/dev/dri` too). In
  software (VNC, other GPUs) BBR2 needs 6+ cores and runs slowly; taps can get lost.
- Sound: through the media channel (`tplug-media`), like the media player; with `--audio`. The
  backup camera's ffmpeg is off while a game runs: game + camera made audiod fall behind (pump
  errors, audible dropouts). A few can still come while the game loads.
- Logs: in the image, `/run/qtcar-sv/cobalt.log`, `mame.log`; `/tmp/games/cobalt-input.log`.
  `game-windows.log` in the run folder.
- Model S/X 2026.8.3: MAME (`mametesla`, 9 ROMs) starts through the escalator stub and ran in
  the user's run (over VNC without its mouse grab). Its keys are the steering wheel's scroll
  wheels: the panel's Arcade tab sets the S's `STW_status` (sandbox: the `STW_*` values follow;
  the keys in a running game are untested). 2048, chess and backgammon (built into the
  firmware) start through the stub and `qtcar-service` with the settings QtCar writes for them
  (`tidk.vars`). They need QtCar to see its own screen as the center display (the S shim maps the
  car's display name `:0` to QtCar's), MIT-SHM (`mit_shm = "root"`: Xvfb runs as root) and a patch
  of their compiled-in touch transform; 2048 plays in the sandbox. Beach Buggy
  Racing 2 and the other downloadable games are not in the firmware dump, and "Install" can't
  fetch them: the packages come from Tesla's update servers, which need the car's credentials.
- Limits: the MAME games aren't really usable on a desktop (the desktop's panel over QtCar's top
  bar, windows in odd places); Beach Buggy Racing 2 and 2048 are the ones to play. VNC mode
  (Xvfb) is untested with the games.

## Vehicle data and the web panel

`--vehicle` (`VEHICLE=1`). The UI sees a car: configuration (Model 3, color, wheels, map
region), battery and range, gear and speed, doors, trunk and frunk, locks, charge port and
charging, lights, outside temperature, tire pressures, telltales.

- Needs: python3; the CAN signal database, which `./tesla start` extracts from the firmware once
  (`vehicle/work/can-db.json`, needs `pip install --user unicorn pyelftools`).
- How: Tesla's own vehicle simulator (`QtCarSimService`, service `qtcar-sim`) plays the gateway
  and sends a simulated car's CAN frames as UDP. `vehicle/tesla-can.py` sits in between (merge
  mode): it writes its own signals into the simulator's frames (gear, speed, odometer, charge
  current, telltales, anything set on the panel) and forwards them to `qtcar-vehicle`, which turns
  them into the values QtCar shows. It also answers the UI's buttons: frunk, trunk, lock, charge
  port, lights, fog lights, wipers.
- Panel: http://localhost:8099/ (see [usage.md](usage.md#the-web-panel)).
- The car's look: `--color`, `--wheels`, `--performance` (the names differ per car: `./tesla start
  --help`). QtCar reads it at startup. Model S/X: paint, 19" and 21" wheels (the firmware has no
  S pictures of the Model X's 20" and 22" wheels), Standard/Ludicrous; the cluster too.
- Logs: `qtcar-sim.log`, `qtcar-vehicle.log`, `can.log` (what tesla-can.py sends and answers).
  Climate: power, fan speed, air distribution, recirculation and A/C are answered like the car's
  climate controller would; seat heaters and temperatures work in the UI (nothing heats). Model
  S/X: the same, plus defrost, steering wheel heat, rear defrost and the wiper heater.
  A tire below 2.2 bar (preset `tire-low`, the panel's sliders) shows the TPMS warning (with the
  drive rail on).
  While charging, tesla-can.py fills the battery at the set charge current (`BMS_packCurrent` into
  a 75 kWh pack at 360 V): energy/range added, time remaining, complete at 100%.
- Model S/X: no simulator; `tesla-can.py` sends all of the gateway's messages itself and the
  panel sets the ECUs' own signals: doors, frunk, trunk, lock, charge port and charging, gear and
  speed, battery, lamps (the car picture's), turn signals, hazards, parking brake, belt, outside
  temperature, tire pressures. It answers the UI's lock, frunk, trunk, charge port, light switch,
  fog light and parking brake buttons. Charging: the panel's Charging state, the station's current
  and voltage; the car draws the UI's charge current (up to the station's), the battery fills,
  Stop/Start Charging works, and the session ends at the UI's charge limit. The cluster shows
  time remaining, energy added, kW and mi/hr (the center display's Charging tab has no session
  figures on this firmware).
- Limits: the charger values on the charging screen (A, V) are the simulator's. Details: [`vehicle/README.md`](../vehicle/README.md).

## GPS

`--gps SPEC` (`GPS=`):

| SPEC | |
|---|---|
| `37.4419,-122.1430` | stand at a fixed position |
| `"--pos 37.4419,-122.1430 --heading 90"` | ... facing east |
| `"--pos A --to B [--speedup 3] [--loop]"` | drive a route from A to B (routed on the installed maps) |
| `"--gpx track.gpx"`, `"--path 'A B C'"` | drive a GPX track, or straight lines |
| `"--uart /dev/ttyACM0 --baud 9600"` | pass a real GPS receiver's NMEA through |

- Needs: `qtcar-gpsmanager` (started automatically); routes: maps; a receiver: read access to the
  device (group `dialout`).
- How: `vehicle/tesla-gps.py` sends NMEA sentences over UDP to GpsManager (port 63277), which
  publishes the position to QtCar. With `--vehicle`, driving also sets gear D, speed and odometer
  (a real receiver only counts as moving above 1 m/s). Without `--vehicle` the map uses the raw
  GPS position (`GUI_smoothGPSUpdates=false`).
- Logs: `gps.log` (every 30 s with a receiver: sentences, fix status, satellites, whether
  GpsManager has a fix), `qtcar-gpsmanager.log`. `./tesla logs` summarizes it.
- Limits: a receiver indoors often has no fix (RMC status V).
- The clock: the car takes its time zone from the GPS position through Google's time zone API
  (with Tesla's key), so here it doesn't follow the position. `./tesla start` sets QtCar's stored
  zone (`GUI_timeZoneId`) to `TIMEZONE` (default auto = this PC's; both firmwares shipped with
  America/Los_Angeles). The cluster takes it over from QtCar a few seconds after the start.
- Model S/X 2026.8.3: a T480 VNC run with `--gps 37.4419,-122.1430` logged a complete
  NMEA sentence, `LOC_geoValidFix=true` and QtCar switching to raw GPS. No physical GPS
  receiver was attached to the T480, so receiver passthrough remains untested there.

## Sound

`--audio` (`AUDIO=1`): the car's sound (chimes, media, navigation voice, Bluetooth music), mixed
by AudioWeaver; on the Model S/X also Chromium web media, Browser and games through it.

- Needs: the `snd-aloop` kernel module (cards `model3` and `virtual`), `arecord`, `sox`, a host
  player (`pw-play`, `pacat` or `aplay`).
- Model 3: the car's sound card is replaced by a loopback card. The apps write into shared-memory
  channels, AudioWeaver (the car's DSP software) mixes them, audiod plays the result on the
  loopback card as the 8 channels of the car's amplifier. On the host, `arecord | sox | pw-play`
  records the other end, mixes the 8 channels to stereo (`AUDIO_REMIX`) and plays it.
- Logs: `audio.log` (the host side: clipping, restarts), `audiod.log`, `audioweaver.log`,
  `qtcar-audiod.log`. `./tesla logs` counts pump errors and clipped samples.
- Kernels with a coarse timer tick (Debian: 250 Hz; Ubuntu uses 1000 Hz) made the loopback card
  run in 4 ms jumps and the audio stutter badly. Then `./tesla start` clocks the `model3` card
  from a dummy sound card on a high-resolution timer (`snd-dummy` "hrclock", snd-aloop's
  `timer_source`), automatically; the log says "the model3 card runs on a high-resolution clock".
- PulseAudio/PipeWire must not adopt the loopback cards: install `tools/99-tesla-sound.rules`
  (`./tesla check` warns). Without PipeWire, the host side plays through `pacat` or `aplay`.
- Model S/X (2026-10-22): the firmware's `audiod` has AudioWeaver built in. The kit points its
  amplifier PCM (`auc`, the car's card `modelsxinfo2`) at the same `model3` loopback card, and
  `./tesla start --audio` runs `audiod` with its runit options. AudioWeaver mixes the apps' tplug
  channels (chimes, turn signal, navigation voice, media player) and the `virtual` card's
  loopbacks (Chromium web media on subdevice 2, Browser 3, games 4, Bluetooth A2DP 0) into amp M,
  and amp S, which the host records (16 channels: 1-8 amp M, 9-16 amp S) and mixes to stereo
  (S default `AUDIO_REMIX`: left M4 + S4, right M2 + M6, the center M8 on both; the odd channels
  stay silent; measured with test tones in the sandbox). The media sources' bridges
  (`alsaloop-chromium` for web media, `media1bridge` for the media server's player, `a2dpbridge`
  for Bluetooth) start when audiod asks the escalator, i.e. the profile's `escalator-stub`. The
  radio's input (the car's Ethernet AVB stream) is a silent loopback. The amplifier outputs are
  quiet, so the host mix adds 18 dB (with a limiter): a UI volume around 40 % is normal loudness.
  Chimes (lock, turn signal) play at fixed levels as in the car; with the UI at 100 % the music
  drowns them, as it would in the car.
  Sandbox-tested: a tone into Chromium's loopback reaches the stereo mix on the right side.
  Earlier runs (sessions 11-13) captured the `virtual` subdevices directly, without the car's sounds.
- Limits: the Model 3 stereo mix of the 8 amplifier channels is approximate. The 2019 Model 3's
  TuneIn still needs its media backend. Radio and Bluetooth need their respective hardware.

## Bluetooth

`--bluetooth` (`BLUETOOTH=1`): the car's own Bluetooth stack (Broadcom's BSA and Tesla's `btd`
from the firmware) runs on the PC's adapter. On the 2019 Model 3 profile, pairing a phone, the
phone app (contacts, call lists), and media control work; with `--audio` the phone's music plays
through the car's audio.
`BLUETOOTH=auto` turns it on at a start when `BT_ADAPTER` exists (the stick's default).

- Needs: a Bluetooth adapter (`BT_ADAPTER=hci0`), python3. BlueZ (`bluetooth.service`) is
  stopped while it runs and gets the adapter back at `./tesla stop`.
- How: `bluetooth/hci-bridge.py` hands the adapter's raw HCI channel to the firmware's stack as
  if it were its serial Broadcom chip. Details: [`bluetooth/README.md`](../bluetooth/README.md).
- Music: select the Bluetooth source in the Media app ("Phone").
- Logs: `bsa_server.log`, `btd.log`, `qtcar-bluetooth.log`, `bt-bridge.log`, `a2dpbridge.log`.
- Limits: tested with an Intel AX210 and two Android phones. The music connection sometimes
  needs a second attempt (the car reconnects by itself). Calls are untested.
- Model S/X 2026.8.3: QtCarBluetooth must run as firmware user `qtbt` to own its D-Bus names.
  Its newer LGIT/BSA stack expects Broadcom vendor setup. On the T480's Intel adapter the
  bridge acknowledges those commands locally, without sending them to Intel hardware. In an
  T480 test, BSA, btd and QtCarBluetooth started, a D-Bus discovery request issued an HCI
  inquiry, and a rooted Pixel 7a paired with both HFP and A2DP. Bluetooth music reached the
  laptop speakers with `--audio`; calls remain untested. BSA can terminate itself on this
  Intel adapter. `bt-watch` detects its replacement and restarts the otherwise stale btd
  connection; a forced BSA restart recovered both phone profiles automatically. The underlying
  BSA termination remains unexplained, so uninterrupted long-term pairing is not confirmed.
  Since the S's car sound runs through its audiod (2026-10-22), Bluetooth music goes through it
  too: audiod asks the escalator stub for `a2dpbridge` and mixes the phone's stream (A2DP input of
  AudioWeaver) with the other sources. Tested 2026-10-27 on the Fedora PC's USB adapter with the
  Pixel 7a: HFP and A2DP connected, QtCar showed the phone's player, the user heard the music.
  `BT_EMULATE_VENDOR_SUCCESS=0` disables the local acknowledgements
  for diagnosis; `BT_TRACE=1` records HCI packets in `bt-bridge.log` (which may include device
  addresses). The bridge does not emulate command-specific Broadcom response data.

## Wi-Fi

`--wifi` (`WIFI=1`, `WIFI_ADAPTER=auto`): the car's own Wi-Fi stack runs on the PC's adapter:
`wpa_supplicant` (D-Bus only) and `connmand` from the firmware, and QtCarConnMan
(`qtcar-connman`), which QtCar's Wi-Fi settings talk to. Networks are found, joined and saved in
QtCar (saved in the image, `/var/lib/connman`); the PC is then online through them.
- While it runs NetworkManager doesn't manage the adapter (`nmcli device set DEV managed no`),
  and the adapter is renamed to `wlan0`: QtCarConnMan only knows that name. `./tesla stop`
  gives both back.
- connmand manages only `wlan0` (`-i wlan0`, `-r`: no DNS proxy; the chroot's resolv.conf gets
  the network's DNS servers). The car's `wifi-power` (GPIOs, PCI device removal) is a stub.
- Limits: wpa_supplicant 2.6 (WPA2, no WPA3). The connection only exists while QtCar runs.
- Logs: `wpa_supplicant.log`, `connman.log`, `qtcar-connman.log`.
- Model S/X 2026.8.3: the patch kit lets ConnMan's worker continue when the car-only AppArmor
  hat is unavailable in a desktop chroot. Its broker/worker split remains active. On the T480,
  ConnMan exposed powered Wi-Fi to QtCarConnMan and discovered seven service paths while Ethernet
  kept SSH connected. No network was joined, so authentication, DHCP and DNS are unverified.

## USB music

`--music DIR` (`MUSIC=`): a folder shows up as a USB stick (Media -> USB) and plays with
`--audio`.

- How: the folder is bind-mounted read-only at `/home/tesla/media/usb-music` in the chroot, where
  the kit points QtCar's USB search (`[usb] media_path`). QtCar indexes it like a stick.
- Logs: `qtcar.log` (USBAccess, USBDeviceIndexer), `qtcar-mediaserver.log` (playback).
- Model S/X 2026.8.3: the firmware looks for sticks in `/mnt/media/usb-*` (where its mounterd
  mounts them; `[usb] media_path` doesn't apply), so `./tesla start` mounts the folder at
  `/mnt/media/usb-music`. QtCar indexes it at the start; the Media app's source list has USB
  (songs, artists, albums, folders with their tags) once the car's audio runs (`--audio`), and
  the media server plays the files (sandbox; the sound through the car's audio is untested). The
  media server's own session bus and gvfs (firmware jobs) aren't needed for this.

## Backup camera

`--camera DEV` (`CAMERA=`): the picture of any V4L2 camera is shown in R (shift into R on the
panel), e.g. a phone through `scrcpy --video-source=camera --v4l2-sink=/dev/video10 --no-video-playback`.
`--camera auto` (`CAMERA=auto`) takes the first webcam at each start: the capture node of a UVC
device whose name doesn't say IR (a laptop's IR camera offers MJPG/YUYV too).

- Needs: `ffmpeg`, `v4l2loopback` with a second device for QtCar (`/dev/video32`, `CAMERA_DEV`):
  `sudo modprobe v4l2loopback devices=2 video_nr=10,32 exclusive_caps=1,1`.
- How: QtCar reads 32-bit BGRX frames from `[bkcam] deviceId` (set at start, with
  `forceFeedGood`, since no camera module reports the feed as good). ffmpeg converts the source
  (any format) into that device. It's off while an arcade game runs (together they overloaded the
  laptop: audible dropouts), like the car's dashcam in game mode.
- Logs: `camera.log` (ffmpeg, and when it was off for a game).
- Limits: the source must deliver frames; ffmpeg retries every 2 s until it does.
- Model S/X 2026.8.3: the desktop image has no physical backup camera deserializer. A checked
  patch makes `CameraDevice::deserializer_ready()` accept the V4L2 loopback source. On the T480,
  `/dev/video2` through ffmpeg reached QtCar in R; the log reported the first valid frame in
  1 ms, and the reverse view showed the webcam image under parking lines. The webcam's covered
  view was red during that test. `reinit-buc` can still log an escalator error without blocking
  capture.
