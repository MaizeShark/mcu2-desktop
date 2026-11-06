#!/usr/bin/env python3
"""BeamNG.drive -> the Tesla UI: what the car in the game does, the UI shows.

Receives the game's UDP packets on the host the UI runs on, and hands them on:

    the mod (TMC1), or stock OutGauge + MotionSim -> :4444 -> this -> tesla-can.py's control port
                                                                   -> NMEA to QtCarGpsManager

Works with both firmwares; tesla-can.py says which car it plays. Values are sent when they change,
so a change on the web panel holds until the game changes that value. Fields, signals and the setup
in the game: beamng/README.md.

  beamng-bridge.py                                the game on this PC
  beamng-bridge.py --listen 0.0.0.0:4444          the game on another PC in the network
  beamng-bridge.py --anchor 37.4419,-122.1430     where the game's world origin is on the map
"""
import argparse, http.client, importlib.util, json, math, os, socket, struct, sys, threading, time

HERE = os.path.dirname(os.path.realpath(__file__))
CONTROL = ("127.0.0.1", 20180)      # tesla-can.py
GPS_TARGET = ("127.0.0.1", 63277)   # QtCarGpsManager
EARTH_R = 6371008.8

# the mod's struct (lua/vehicle/protocols/maizeshark_teslamcu.lua)
TMC1 = struct.Struct("<4sI22fbBBB16s")
# lua/vehicle/protocols/outgauge.lua (id is optional in the Live for Speed format) and motionSim.lua
OUTGAUGE = struct.Struct("<I4sHBB7f2I3f16s16s")
MOTIONSIM = struct.Struct("<4s21f")

F_RUNNING, F_HIGHBEAM, F_FOG, F_SIGNAL_L, F_SIGNAL_R, F_HAZARD = 1, 2, 4, 8, 16, 32
F_PARKBRAKE, F_ABS, F_ESC, F_TCS = 64, 128, 256, 512
F_DOOR_FL, F_DOOR_FR, F_DOOR_RL, F_DOOR_RR, F_TRUNK, F_HOOD = 65536, 131072, 262144, 524288, 1048576, 2097152

DL_FULLBEAM, DL_HANDBRAKE, DL_TC, DL_SIGNAL_L, DL_SIGNAL_R, DL_ABS = 2, 4, 16, 32, 64, 1024

STOCK_AFTER = 2.0       # s without the mod's packets before OutGauge/MotionSim count
GONE_AFTER = 3.0        # s without packets: the game is gone (or its sender is, for another's)
SLOW_PERIOD = 1.0       # the simulator's numeric values, s: every SIM_ change is an HTTP request
CAN_PERIOD = 0.05
GPS_PERIOD = 0.2
UNITS_PERIOD = 2.0       # s between looks at the UI's distance setting
QTCAR_PORT = 4220
TIRE_FULL_BAR = 2.9     # what a tire at the pressure it started with shows (see Bridge.tires)


def decode_tmc1(data):
    """The mod's packet as state fields, None if it isn't one (zeroed: the vehicle isn't ready)."""
    if len(data) != TMC1.size or data[:4] != b"TMC1":
        return None
    f = TMC1.unpack(data)
    flags = f[1]
    on = lambda bit: bool(flags & bit)
    return {
        "pos": f[2:5], "vel": f[5:8], "dir": f[8:10], "speed": abs(f[10]), "steering": f[11], "throttle": f[12],
        "brake": f[13], "rpm": f[14], "fuel": f[15], "odometer": f[16], "env_temp": f[17], "power": f[19],
        "tires": dict(zip(("FL", "FR", "RL", "RR"), f[20:24])), "gear_index": f[24], "gear": chr(f[25]) if f[25] else "",
        "ignition": f[26], "lights": f[27], "vehicle": f[28].split(b"\0")[0].decode(errors="replace"),
        "highbeam": on(F_HIGHBEAM) or f[27] == 2, "fog": on(F_FOG), "signal_l": on(F_SIGNAL_L), "signal_r": on(F_SIGNAL_R),
        "hazard": on(F_HAZARD), "parkbrake": on(F_PARKBRAKE), "abs": on(F_ABS), "esc": on(F_ESC), "tcs": on(F_TCS),
        "door_fl": on(F_DOOR_FL), "door_fr": on(F_DOOR_FR), "door_rl": on(F_DOOR_RL), "door_rr": on(F_DOOR_RR),
        "trunk": on(F_TRUNK), "hood": on(F_HOOD),
    }


def decode_outgauge(data):
    if len(data) not in (OUTGAUGE.size, OUTGAUGE.size + 4) or data[4:8] != b"beam":
        return None
    f = OUTGAUGE.unpack(data[:OUTGAUGE.size])
    # f: time car flags gear plid speed rpm turbo engTemp fuel oilPressure oilTemp dash show throttle brake clutch
    show = f[13]
    on = lambda bit: bool(show & bit)
    return {
        "gear_index": f[3] - 1, "speed": abs(f[5]), "rpm": f[6], "fuel": f[9], "throttle": f[14], "brake": f[15],
        "highbeam": on(DL_FULLBEAM), "parkbrake": on(DL_HANDBRAKE), "signal_l": on(DL_SIGNAL_L), "signal_r": on(DL_SIGNAL_R),
        "tcs": on(DL_TC), "abs": on(DL_ABS), "vehicle": "OutGauge",
        "ignition": 2,      # not in the format: the drive rail stays on, or the UI hides the speed
    }


def decode_motionsim(data):
    if len(data) != MOTIONSIM.size or data[:4] != b"BNG1":
        return None
    f = MOTIONSIM.unpack(data)
    return {"pos": f[1:4], "vel": f[4:7]}


def tesla_gear(s):
    """P, R, N or D: from the gear's name, a manual car's from its index; ignition off is P."""
    if s.get("ignition", 2) == 0:
        return "P"
    g = s.get("gear", "")
    if g in ("P", "R", "N"):
        return g
    if g:
        return "D"
    i = s.get("gear_index", 0)
    return "R" if i < 0 else "D" if i > 0 else "N"


def true_false(x):
    return "true" if x else "false"


def map_common(s, odo_km, tires, metric=False):
    """The figures in Tesla's units and steps, for the maps below. Only what the source gave."""
    out = dict(s)
    out["kmh"] = round(s.get("speed", 0.0) * 3.6, 1)
    out["mph"] = out["kmh"] / 1.609344
    # QtCar shows the drive inverter's number as it is, under the label of the UI's own setting:
    # like the car's inverter, send it in the units the UI is set to
    out["ui_speed"], out["ui_units"] = (out["kmh"], "KPH") if metric else (out["mph"], "MPH")
    out["odo_km"], out["tires_bar"] = odo_km, tires
    return out


def map_model3(s):
    """Signals of the Model 3: the simulator's SIM_* values and the drive inverter's CAN signals."""
    v = {}
    if "ignition" in s:
        v["SIM_driveRailOn"] = true_false(s["ignition"] >= 2)
    gear = tesla_gear(s)
    v["DI_gear"] = gear
    v["DI_systemState"] = "ENABLE" if s["kmh"] > 0.3 or gear in ("R", "D") else "STANDBY"
    # below each signal's top value: that one means "not available"
    v["DI_vehicleSpeed"] = min(s["kmh"], 287.5)
    v["DI_uiSpeed"], v["DI_uiSpeedHighSpeed"] = min(round(s["ui_speed"]), 254), min(round(s["ui_speed"]), 510)
    v["DI_uiSpeedUnits"] = s["ui_units"]
    if s["odo_km"] is not None:
        v["DI_odometer"] = round(s["odo_km"], 1)
        v["SIM_batteryOdometer"] = round(s["odo_km"] / 1.609344, 1)
    if "steering" in s:
        v["SCCM_steeringAngle"] = round(-s["steering"] * 2) / 2     # the game's positive is left
    if "brake" in s:
        braking = s["brake"] > 0.02
        v["VCLEFT_brakePressed"] = int(braking)
        v["DI_brakePedalState"] = "ON" if braking else "OFF"
        # the lamps on the driving view's car (LIGHT_brake*): the pedal alone doesn't light them
        v["VCLEFT_brakeLightStatus"] = v["VCRIGHT_brakeLightStatus"] = "ON" if braking else "OFF"
    if "throttle" in s:
        v["DI_accelPedalPos"] = round(s["throttle"] * 100)
    if "rpm" in s:
        v["DI_motorRPM"] = round(s["rpm"] / 10) * 10
    if "fuel" in s:
        v["SIM_batteryLevel"] = round(s["fuel"] * 100, 1)
    if "power" in s:
        v["BMS_packCurrent"] = round(-s["power"] * 1000 / 360)      # A, + = into the pack
    if "lights" in s:
        v["SIM_headLights"] = v["SIM_parkingLights"] = true_false(s["lights"] >= 1)
        v["VCLEFT_tailLightStatus"] = v["VCRIGHT_tailLightStatus"] = "ON" if s["lights"] >= 1 else "OFF"
    if "highbeam" in s:
        v["SIM_highbeamSwitch"] = true_false(s["highbeam"])
    if "fog" in s:
        v["SIM_frontFogLights"] = true_false(s["fog"])
    if "signal_l" in s:
        hazard = s.get("hazard", False)
        v["SIM_signalLeft"] = true_false(s["signal_l"] and not hazard)
        v["SIM_signalRight"] = true_false(s["signal_r"] and not hazard)
    if "hazard" in s:
        v["SIM_hazardsSwitch"] = true_false(s["hazard"])
    if "parkbrake" in s:
        v["SIM_parkBrakeApplied"] = true_false(s["parkbrake"])
    for field, name in (("door_fl", "SIM_driverDoor"), ("door_fr", "SIM_passFrontDoor"), ("door_rl", "SIM_driverRearDoor"),
                        ("door_rr", "SIM_passRearDoor"), ("trunk", "SIM_rearTrunk"), ("hood", "SIM_frontTrunk")):
        if field in s:
            v[name] = true_false(s[field])
    if "env_temp" in s:
        v["SIM_hvacOutsideTemp"] = round(s["env_temp"] * 2) / 2
    for wheel, bar in s["tires_bar"].items():
        v["SIM_tpmsPressure" + wheel] = bar
    if "tcs" in s:
        v["DI_tcTelltaleFlash"] = "ON" if s["tcs"] else "OFF"
    if "esc" in s:
        v["DI_vdcTelltaleFlash"] = "ON" if s["esc"] else "OFF"
    return v


def map_models(s):
    """Signals of the Model S/X: the ECUs' own (there is no simulator)."""
    v = {}
    lamp = lambda x: "On" if x else "Off"
    if "ignition" in s:
        v["GTW_vehiclePowerState"] = "DRIVE" if s["ignition"] >= 2 else "NOT_DRIVE"
    v["DI_gear"] = tesla_gear(s)
    v["DI_vehicleSpeed"] = min(round(s["mph"], 1), 179.6)
    v["DI_analogSpeed"] = min(round(s["ui_speed"], 1), 409.4)
    v["DI_speedUnits"] = s["ui_units"]
    if s["odo_km"] is not None:
        v["BMS_odometer"] = round(s["odo_km"] / 1.609344, 1)
    if "brake" in s:
        braking = s["brake"] > 0.02
        v["DI_brakePedal"] = "Applied" if braking else "Not_applied"
        v["BC_brakeLightLStatus"] = v["BC_brakeLightRStatus"] = lamp(braking)
    if "throttle" in s:
        v["DI_pedalPos"] = round(s["throttle"] * 100)
    if "rpm" in s:
        v["DI_motorRPM"] = round(s["rpm"] / 10) * 10
    if "fuel" in s:
        v["X_batteryLevel"] = round(s["fuel"] * 100, 1)
    if "power" in s:
        v["BMS_packCurrent"] = round(-s["power"] * 1000 / 385)      # A, + = into the pack
    if "lights" in s:
        on = lamp(s["lights"] >= 1)
        for name in ("BC_headLight%sStatus", "BC_parkingLight%sStatus", "BC_tailLight%sStatus"):
            v[name % "L"] = v[name % "R"] = on
    if "highbeam" in s:
        v["BC_flashMainSwitch"] = "Main_beam_toggle" if s["highbeam"] else "Flash_off"
    if "fog" in s:
        v["BC_fogLightFLStatus"] = v["BC_fogLightFRStatus"] = lamp(s["fog"])
    if "signal_l" in s:
        hazard = s.get("hazard", False)
        v["BC_indicatorLStatus"] = lamp(s["signal_l"] or hazard)
        v["BC_indicatorRStatus"] = lamp(s["signal_r"] or hazard)
    if "hazard" in s:
        v["SC_hazardSwitchStatus"] = "Hazard_request" if s["hazard"] else "No_request"
    if "parkbrake" in s:
        p = s["parkbrake"]
        v.update(EPB_brakeApplied="APPLIED" if p else "RELEASED", EPB_state="PARK" if p else "OPEN", EPB_syncState="READY")
    for field, name in (("door_fl", "SC_driverFrontDoorOpenStatus"), ("door_fr", "SC_passFrontDoorOpenStatus"),
                        ("door_rl", "SC_driverRearDoorOpenStatus"), ("door_rr", "SC_passRearDoorOpenStatus"),
                        ("trunk", "SC_rearTrunkStatus"), ("hood", "SC_frontTrunkStatus")):
        if field in s:
            v[name] = "open" if s[field] else "closed"
    if "env_temp" in s:
        v["GTW_ambientTemperature"] = round(s["env_temp"] * 2) / 2
    for wheel, bar in s["tires_bar"].items():
        v["TPMS_pressure" + wheel] = bar
    if "abs" in s:
        v["ESP_absBrakeEvent"] = "ACTIVE" if s["abs"] else "NOT_ACTIVE"
    return v


MAPS = {"Model 3": map_model3, "Model S/X": map_models}


class Bridge:
    """The state of the game's car, and what goes to tesla-can.py and GpsManager from it."""

    def __init__(self, send, mapper, odometer_km):
        self.send, self.mapper = send, mapper
        self.state = {}
        self.odo_km = odometer_km
        self.game_odo = None
        self.tire_full = {}     # the highest seen per wheel of this vehicle
        self.sent = {}
        self.pending_slow = {}
        self.last_slow = 0.0
        self.last_can = 0.0
        self.sender = None
        self.sender_seen = self.mod_seen = -1e9
        self.connected = False
        self.metric = False     # the UI's distance setting, see watch_units()

    def tires(self):
        """Pressures as the car's: game cars run 1.5 to 3 bar and more, the Model 3 warns below
        2.2. So a tire at the pressure it started with shows TIRE_FULL_BAR, a losing one less."""
        bars = {}
        for wheel, pa in self.state.get("tires", {}).items():
            if pa < 0:
                continue
            full = self.tire_full[wheel] = max(self.tire_full.get(wheel, 0.0), pa)
            if full > 0:
                bars[wheel] = round(pa / full * TIRE_FULL_BAR * 20) / 20
        return bars

    def odometer(self, fields, dt):
        """Our own count, so changing vehicles never turns it back: what the game's odometer adds."""
        if "odometer" in fields:
            prev, self.game_odo = self.game_odo, fields["odometer"]
            if prev is not None and 0 < self.game_odo - prev < 1000:
                self.odo_km += (self.game_odo - prev) / 1000
        elif "speed" in fields and "pos" not in fields:      # OutGauge: no odometer
            self.odo_km += fields["speed"] * dt / 1000

    def packet(self, data, addr, now):
        """A datagram from the network. True if it was taken."""
        for decode, is_mod in ((decode_tmc1, True), (decode_outgauge, False), (decode_motionsim, False)):
            fields = decode(data)
            if fields is not None:
                break
        else:
            return False
        if is_mod:
            self.mod_seen = now
        elif now - self.mod_seen < STOCK_AFTER:
            return False
        if self.sender != addr[0]:
            if now - self.sender_seen < GONE_AFTER:
                return False        # another machine's game while this one's still runs
            print("game at %s" % addr[0], file=sys.stderr, flush=True)
            self.sender = addr[0]
        dt = min(now - self.sender_seen, 1.0)
        self.sender_seen = now
        if not self.connected:
            self.connected = True
            print("connected (%s)" % ("the mod" if is_mod else "stock OutGauge/MotionSim: fewer values, see beamng/README.md"),
                  file=sys.stderr, flush=True)
        name = fields.get("vehicle")
        if name and name != self.state.get("vehicle"):
            print("vehicle: %s" % name, file=sys.stderr, flush=True)
            self.tire_full.clear()
            self.game_odo = None
        self.odometer(fields, dt)
        self.state.update(fields)
        return True

    def tick(self, now):
        """Send what changed: CAN signals at CAN_PERIOD, the simulator's switches at once, its
        numbers (one HTTP request each in tesla-can.py) at SLOW_PERIOD."""
        if self.connected and now - self.sender_seen > GONE_AFTER:
            self.connected = False
            print("no packets for %g s: the game is gone, parking the car" % GONE_AFTER, file=sys.stderr, flush=True)
            self.state.update(speed=0.0, steering=0.0, throttle=0.0, brake=0.0, rpm=0.0, power=0.0, gear="P")
            self.push(self.values(), now, flush=True)
            return
        if not self.connected or now - self.last_can < CAN_PERIOD:
            return
        self.last_can = now
        self.push(self.values(), now)

    def values(self):
        return self.mapper(map_common(self.state, self.odo_km, self.tires(), self.metric))

    def push(self, values, now, flush=False):
        changed = {k: v for k, v in values.items() if self.sent.get(k) != v}
        self.pending_slow = {k: v for k, v in self.pending_slow.items() if k in changed}
        out = {}
        for k, v in changed.items():
            slow = k.startswith("SIM_") and v not in ("true", "false") or k == "X_batteryLevel"
            if slow and not flush:
                self.pending_slow[k] = v
            else:
                out[k] = v
                self.pending_slow.pop(k, None)
        if self.pending_slow and (flush or now - self.last_slow >= SLOW_PERIOD):
            self.last_slow = now
            out.update(self.pending_slow)
            self.pending_slow = {}
        if out:
            self.sent.update(out)
            self.send(out)


def ui_metric():
    """Is the UI set to kilometres? None while QtCar doesn't answer."""
    conn = http.client.HTTPConnection("127.0.0.1", QTCAR_PORT, timeout=1.0)
    try:
        conn.request("GET", "/_data_get_value_request_?name=GUI_distanceUnits")
        value = conn.getresponse().read().decode(errors="replace").strip()
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()
    if not value or value.startswith("{"):      # an empty data value
        return None
    return value == "Kilometers"


def watch_units(bridge):
    """Follows the setting, also when it's changed on the screen while driving."""
    while True:
        metric = ui_metric()
        if metric is not None and metric != bridge.metric:
            print("UI units: %s" % ("km/h" if metric else "mph"), file=sys.stderr, flush=True)
            bridge.metric = metric
        time.sleep(UNITS_PERIOD)


def load_tesla_gps():
    """tesla-gps.py's NMEA and the keeper of GpsManager's raw position (its name has a dash)."""
    spec = importlib.util.spec_from_file_location("tesla_gps", os.path.join(HERE, "..", "vehicle", "tesla-gps.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def world_to_geo(anchor, pos):
    """The game's world in metres (+X east, +Y north) around the map's anchor."""
    lat = anchor[0] + math.degrees(pos[1] / EARTH_R)
    lon = anchor[1] + math.degrees(pos[0] / (EARTH_R * math.cos(math.radians(anchor[0]))))
    return lat, lon


class GpsOut:
    def __init__(self, anchor, nmea):
        self.anchor, self.nmea = anchor, nmea
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.heading = 0.0

    def send(self, state, connected):
        pos = state.get("pos", (0.0, 0.0, 30.0))
        vel = state.get("vel", (0.0, 0.0, 0.0))
        speed = math.hypot(vel[0], vel[1]) if connected else 0.0
        if "dir" in state:
            self.heading = math.degrees(math.atan2(state["dir"][0], state["dir"][1])) % 360
        elif speed > 1:         # MotionSim has no heading we can trust: where the car goes
            self.heading = math.degrees(math.atan2(vel[0], vel[1])) % 360
        lat, lon = world_to_geo(self.anchor, pos)
        self.sock.sendto(self.nmea(lat, lon, speed, self.heading, pos[2]).encode(), GPS_TARGET)


def ask_car(control):
    """Which car tesla-can.py plays; waits for it (./tesla start runs it first)."""
    said = False
    control.settimeout(1.0)
    while True:
        control.sendto(json.dumps({"cmd": "info"}).encode(), CONTROL)
        try:
            car = json.loads(control.recv(65536)).get("car")
            if car in MAPS:
                control.setblocking(False)
                return car
            sys.exit("tesla-can.py plays %r, no signals for it here" % car)
        except OSError:
            if not said:
                print("waiting for tesla-can.py on :%d" % CONTROL[1], file=sys.stderr, flush=True)
                said = True


def parse_point(text):
    lat, lon = text.split(",")
    return float(lat), float(lon)


def parse_listen(text):
    host, _, port = text.rpartition(":")
    return host or "127.0.0.1", int(port)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen", type=parse_listen, default=("127.0.0.1", 4444), metavar="ADDR:PORT",
                    help="where the game's packets arrive (default 127.0.0.1:4444; 0.0.0.0:4444 = from the network)")
    ap.add_argument("--anchor", type=parse_point, default=(37.4419, -122.1430), metavar="LAT,LON",
                    help="where the game's world origin is on the map (default %(default)s)")
    ap.add_argument("--odometer", type=float, default=12345, help="start odometer in km (default 12345)")
    ap.add_argument("--no-gps", action="store_true", help="no position for QtCar")
    args = ap.parse_args()

    control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    car = ask_car(control)
    print("car: %s" % car, file=sys.stderr, flush=True)

    def send(values):
        control.sendto(json.dumps({"cmd": "set", "values": values, "source": "beamng", "quiet": True}).encode(), CONTROL)
        try:
            while True:     # tesla-can.py's answers
                reply = json.loads(control.recv(65536))
                if reply.get("error"):
                    print("tesla-can.py: %s" % reply["error"], file=sys.stderr, flush=True)
        except OSError:
            pass

    bridge = Bridge(send, MAPS[car], args.odometer)
    threading.Thread(target=watch_units, args=(bridge,), daemon=True).start()
    gps = None
    if not args.no_gps:
        tesla_gps = load_tesla_gps()
        ports = (4160,) if car == "Model S/X" else tesla_gps.RawGpsKeeper.PORTS
        tesla_gps.RawGpsKeeper(ports).start()
        gps = GpsOut(args.anchor, tesla_gps.nmea_sentences)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(args.listen)
    sock.settimeout(CAN_PERIOD)
    print("waiting for the game on %s:%d" % args.listen, file=sys.stderr, flush=True)
    last_gps = 0.0
    try:
        while True:
            try:
                data, addr = sock.recvfrom(4096)
                bridge.packet(data, addr, time.monotonic())
            except OSError:
                pass
            now = time.monotonic()
            bridge.tick(now)
            if gps and now - last_gps >= GPS_PERIOD:
                last_gps = now
                gps.send(bridge.state, bridge.connected)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
