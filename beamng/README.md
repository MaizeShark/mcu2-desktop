# beamng

BeamNG.drive drives the Tesla UI: what the car in the game does, QtCar shows. Works with both
firmwares (Model 3 2019, Model S/X 2026). It runs next to `vehicle/tesla-can.py`, not inside it.

```
the mod (or stock OutGauge + MotionSim) -> UDP :4444 -> beamng-bridge.py -> tesla-can.py's control port (:20180)
                                                                         -> NMEA to QtCarGpsManager (:63277)
```

| File | What |
|---|---|
| `mod/lua/vehicle/protocols/maizeshark_teslamcu.lua` | the game's part, a [UDP protocol module](https://go.beamng.com/protocols) |
| `install-mod.py` | packs the mod into a zip, into the game's mods folder (`./tesla beamng-mod`) |
| `beamng-bridge.py` | packets -> signals of the running car, position -> GPS |
| `fake-game.py` | sends what the game would, for testing without it |
| `test_bridge.py`, `test_lua_stub.lua` | tests: packets, mapping, the Lua against stubs of the game's globals |

## The game on this PC

```sh
# start the game once (it creates its user folder), then:
./tesla beamng-mod
./tesla start --beamng                      # or BEAMNG=1 in tesla.conf; implies --vehicle
./tesla start --beamng --gps 48.1,11.5      # where the game's world origin is on the map
```

Sit in a car in the game. `logs/latest/beamng.log` says what arrives (`connected (the mod)`,
`vehicle: etk800`). Protocol modules load with the vehicle: Ctrl+R after installing the mod in a
running game.

## The game on another PC

The bridge always runs on the PC with the UI. Only the game's packets cross the network; each one
carries the whole state, so a lost one doesn't matter.

On the UI's PC:

```sh
./tesla beamng-mod --zip-only --target 192.0.2.5    # this PC's address; gives beamng/work/maizeshark_teslamcu.zip
./tesla start --beamng-bind 0.0.0.0                 # or BEAMNG=1, BEAMNG_BIND=0.0.0.0 in tesla.conf
```

Copy the zip into `mods/` in the game's user folder on the other PC (the launcher: Manage User
Folder; Windows works too). `--target` takes an IP address or a name it resolves right there; the
game's `sendto()` only takes IP addresses. Without `--target` the mod sends to the game's OutGauge
address setting (Options > Other > Protocols, default 127.0.0.1), so an address can also be
changed in the game.

`./tesla start` prints the address to use. `./tesla check` warns if firewalld doesn't seem to let
4444/udp in, and prints the command; nothing here changes the firewall. Anyone in the network can
then send packets to that port. With several senders the bridge stays with the current one until
it has been silent for 3 s; `beamng.log` shows each sender's address.

## Without the mod

Stock protocols work too, with fewer values: in the game, Options > Other > Protocols, turn on
OutGauge and MotionSim, port 4444 (the default), address of the UI's PC. They count when the mod
has sent nothing for 2 s. No steering, low beams, fog, hazards, doors, odometer (counted from the
speed instead), outside temperature, tires, drive power; OutGauge can't tell P from N, and the
drive rail stays on. Without MotionSim there is no position.

## What shows what

| Game | Model 3 | Model S/X |
|---|---|---|
| ignition on (level 2+) | `SIM_driveRailOn` | `GTW_vehiclePowerState` |
| gear: P/R/N, every forward gear D, manual by its index; ignition off = P | `DI_gear`, `DI_systemState` | `DI_gear` |
| speed, in the UI's units (`GUI_distanceUnits`, read every 2 s) | `DI_uiSpeed`, `DI_uiSpeedHighSpeed`, `DI_uiSpeedUnits`, `DI_vehicleSpeed` | `DI_analogSpeed`, `DI_speedUnits`, `DI_vehicleSpeed` |
| odometer: own count from 12345 km plus what the game's adds, so another vehicle doesn't turn it back | `DI_odometer`, `SIM_batteryOdometer` | `BMS_odometer` |
| steering (negated: positive is left in the game) | `SCCM_steeringAngle` | none on the ETH stream |
| brake, throttle | `VCLEFT_brakePressed`, `DI_brakePedalState`, `VC{LEFT,RIGHT}_brakeLightStatus`, `DI_accelPedalPos` | `DI_brakePedal`, `BC_brakeLight*`, `DI_pedalPos` |
| rpm | `DI_motorRPM` | `DI_motorRPM` |
| **fuel (or an EV's battery) = battery** | `SIM_batteryLevel` | `X_batteryLevel` |
| power at the engines' outputs, as if at 360/385 V | `BMS_packCurrent` | `BMS_packCurrent` |
| low beam | `SIM_headLights`, `SIM_parkingLights` | `BC_headLight*`, `BC_parkingLight*`, `BC_tailLight*` |
| high beam | `SIM_highbeamSwitch` | `BC_flashMainSwitch` |
| fog | `SIM_frontFogLights` | `BC_fogLightF*` |
| turn signals, hazards | `SIM_signalLeft/Right`, `SIM_hazardsSwitch` | `BC_indicator*`, `SC_hazardSwitchStatus` |
| parking brake | `SIM_parkBrakeApplied` | `EPB_*` |
| doors, trunk or tailgate, hood | `SIM_*Door`, `SIM_rearTrunk`, `SIM_frontTrunk` | `SC_*DoorOpenStatus`, `SC_rearTrunkStatus`, `SC_frontTrunkStatus` |
| outside temperature | `SIM_hvacOutsideTemp` | `GTW_ambientTemperature` |
| tires, relative: the pressure it started with = 2.9 bar (game cars run lower, the Model 3 warns below 2.2) | `SIM_tpmsPressure*` | `TPMS_pressure*` |
| TCS, ESC active (Model 3), ABS active (S/X) | `DI_tcTelltaleFlash`, `DI_vdcTelltaleFlash` | `ESP_absBrakeEvent` |
| position, heading | NMEA | NMEA |

**Position:** the game's metres around `--gps LAT,LON`, so the car moves on the Tesla map, but game
roads don't match the map's. On the Model 3's offline maps, pick a place in your map region.

**No packets for 3 s** (game closed, a menu that stops the vehicle's Lua): speed 0, gear P, steering,
pedals, rpm and current 0. The rest holds, and the position stays.

**The web panel** keeps working. Values go out when they change, so a change on the panel holds until
the game changes that value; the panel marks them with a BeamNG badge.

**Rates:** CAN signals at most 20 Hz. The simulator's switches at once, its numbers (battery, temperature,
tires, ...) at most once a second: each is an HTTP request in tesla-can.py. `SIM_vehicleSpeed` and
`SIM_shiftState` aren't sent, the kit patches out the simulator's drive inverter.

## The packet (TMC1)

Little endian, no padding, 116 bytes, 30 Hz; the struct is in the mod, `TMC1` in the bridge.
`format[4]` = `"TMC1"`, `flags` (u32), then floats `posX/Y/Z`, `velX/Y/Z`, `dirX/Y`, `speed`,
`steering`, `throttle`, `brake`, `rpm`, `fuel`, `odometer` (m), `envTemp`, `waterTemp`, `power` (kW),
`tireFL/FR/RL/RR` (Pa over the atmosphere, -1 unknown); `gearIndex` (i8), `gear` (first letter),
`ignition`, `lights` (u8), `vehicle[16]`.

Flags: 1 engine running (unused so far), 2 high beam, 4 fog, 8/16 signal L/R, 32 hazards, 64 parking
brake, 128 ABS, 256 ESC, 512 TCS active; doors from `<coupler>_notAttached`: 65536 FL, 131072 FR,
262144 RL, 524288 RR, 1048576 trunk or tailgate, 2097152 hood. A packet with a zeroed header is a
vehicle that isn't ready; the game sends it anyway (`protocols.lua`).

## Testing

```sh
python3 beamng/test_bridge.py
beamng/fake-game.py [--target ADDR:4444] [--stock]      # with tesla-can.py and the bridge running
```

`test_bridge.py` checks each signal name and enum label against both firmware databases: an unknown
name makes tesla-can.py drop the rest of a message.

## Not tested in the game yet

None of this has run against the real game, only against `fake-game.py` and stubs of its Lua. To
check on the first drive:

- `./tesla beamng-mod` finds the user folder, and the mod activates by itself (`mods/db.json`).
  The folder names are from the binary's strings and the docs, not from a seen install.
- `beamng.log` in the user folder shows no error from `maizeshark_teslamcu`.
- Steering turns the right way: negated because `electrics.lua` treats steering > 0 as left.
- The heading on the map matches the game's compass: +Y = north is inferred from the game's navigation code.
- Doors, trunk, hood on other vehicles (names read from etk800 and covet).
- The current's sign and size: drive power from `outputTorque1 * outputAV1`.
- Model S/X: the cluster's speed and gear. The sandbox's cluster says "Car Off" with or without the
  bridge (also with tesla-can.py's own `drive` preset), so only the values were checked there.
