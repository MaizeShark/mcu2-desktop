#!/usr/bin/env python3
"""Tests of the BeamNG bridge without the game: packets, the mapping to signals, the mod's Lua.

    python3 beamng/test_bridge.py
"""
import importlib.util, json, os, shutil, subprocess, sys, tempfile, unittest, zipfile

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bridge = load("bridge", "beamng/beamng-bridge.py")
game = load("fake_game", "beamng/fake-game.py")
install = load("install_mod", "beamng/install-mod.py")
LUA = os.path.join(HERE, "mod", install.LUA)


def full_state(t=12):
    return bridge.decode_tmc1(game.tmc1(t, 5000.0))


def mapped(mapper, state, odo=100.0, tires=None, metric=False):
    return mapper(bridge.map_common(state, odo, tires or {"FL": 2.9}, metric))


def signals(db_file):
    """The names tesla-can.py can set for a database (as its load_db and VIRTUAL do)."""
    path = os.path.join(REPO, "vehicle", "work", db_file)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        db = json.load(f)
    if any("busname" in m for m in db.values()):
        db = {n: m for n, m in db.items() if m.get("busname") == "ETH" and "@" not in n}
    return db, {k: s for m in db.values() for k, s in m["signals"].items() if "bits" in s and "base" in s}


class Packets(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(bridge.TMC1.size, 116)
        self.assertEqual(len(game.tmc1(0, 0)), bridge.TMC1.size)
        self.assertEqual(bridge.MOTIONSIM.size, 88)

    def test_zeroed_dropped(self):
        self.assertIsNone(bridge.decode_tmc1(bytes(116)))   # the vehicle isn't initialized yet
        self.assertIsNone(bridge.decode_tmc1(b"TMC1"))

    def test_outgauge_with_and_without_id(self):
        pkt = game.outgauge(5)
        self.assertEqual(len(pkt), 96)
        for data in (pkt, pkt[:92]):
            s = bridge.decode_outgauge(data)
            self.assertEqual(s["gear_index"], 2)
            self.assertAlmostEqual(s["speed"], 6.0)
        self.assertIsNone(bridge.decode_outgauge(b"x" * 96))

    def test_motionsim(self):
        self.assertEqual(bridge.decode_motionsim(game.motionsim(5))["vel"][0], 6.0)


class Gears(unittest.TestCase):
    def test_gears(self):
        for state, want in (({"gear": "P"}, "P"), ({"gear": "R"}, "R"), ({"gear": "N"}, "N"), ({"gear": "D"}, "D"),
                            ({"gear": "S"}, "D"), ({"gear": "M"}, "D"), ({"gear": "3"}, "D"),
                            ({"gear_index": -1}, "R"), ({"gear_index": 0}, "N"), ({"gear_index": 4}, "D"),
                            ({"gear": "D", "ignition": 0}, "P")):
            self.assertEqual(bridge.tesla_gear(state), want, state)


class Mapping(unittest.TestCase):
    def check_names(self, db_file, mapper, virtual=()):
        have = signals(db_file)
        if not have:
            self.skipTest("no " + db_file)
        names = have[1]
        for t in (1, 12, 17, 22, 27, 35, 50):
            for k, v in mapped(mapper, full_state(t), tires={"FL": 2.9, "FR": 2.9, "RL": 2.9, "RR": 1.5}).items():
                if k.startswith("SIM_") or k in virtual:
                    continue
                self.assertIn(k, names)     # an unknown name makes tesla-can.py drop the rest of a batch
                enum = names[k].get("enum", {})
                if isinstance(v, str):
                    self.assertTrue(any(l.lower() == v.lower() for l in enum.values()), "%s=%s %s" % (k, v, enum))

    def test_model3_names(self):
        self.check_names("can-db.json", bridge.map_model3)

    def test_models_names(self):
        self.check_names("can-db-models.json", bridge.map_models, ("X_batteryLevel",))

    def test_sim_names_in_firmware(self):
        path = os.path.join(REPO, "mcu2-original/usr/tesla/UI/bin/QtCarSimService")
        if not os.access(path, os.R_OK):
            self.skipTest("no firmware dump")
        with open(path, "rb") as f:
            data = f.read()
        for k in mapped(bridge.map_model3, full_state(), tires={"FL": 2.9, "FR": 2.9, "RL": 2.9, "RR": 2.9}):
            if k.startswith("SIM_") and not k.startswith("SIM_tpmsPressure"):
                self.assertIn(k.encode(), data)

    def test_model3_values(self):
        v = mapped(bridge.map_model3, dict(full_state(), steering=45.0, speed=10.0, fuel=0.5, power=36.0))
        self.assertEqual(v["SCCM_steeringAngle"], -45.0)    # game: left positive; Tesla: right positive
        self.assertEqual(v["DI_vehicleSpeed"], 36.0)
        self.assertEqual(v["DI_uiSpeedHighSpeed"], 22)
        self.assertEqual(v["SIM_batteryLevel"], 50.0)
        self.assertEqual(v["BMS_packCurrent"], -100)        # + = into the pack
        self.assertEqual(v["DI_odometer"], 100.0)
        self.assertEqual(v["SIM_tpmsPressureFL"], 2.9)

    def test_hazards_and_signals(self):
        s = dict(full_state(), hazard=True, signal_l=True, signal_r=False)
        self.assertEqual(mapped(bridge.map_model3, s)["SIM_signalLeft"], "false")
        v = mapped(bridge.map_models, s)
        self.assertEqual((v["BC_indicatorLStatus"], v["BC_indicatorRStatus"]), ("On", "On"))

    def test_stock_only_has_no_invented_values(self):
        s = bridge.decode_outgauge(game.outgauge(5))
        v = mapped(bridge.map_model3, s, tires={})
        self.assertNotIn("SCCM_steeringAngle", v)
        self.assertNotIn("SIM_driverDoor", v)
        self.assertEqual(v["SIM_driveRailOn"], "true")      # or the UI hides the speed


class Runtime(unittest.TestCase):
    def setUp(self):
        self.out = []
        self.b = bridge.Bridge(self.out.append, bridge.map_model3, 1000.0)
        self.addr = ("10.0.0.2", 5000)

    def sent(self):
        all_ = {}
        for d in self.out:
            all_.update(d)
        return all_

    def test_only_changes(self):
        self.b.packet(game.tmc1(5, 5000.0), self.addr, 100.0)
        self.b.tick(100.0)
        n = len(self.out)
        self.assertGreater(n, 0)
        self.b.packet(game.tmc1(5, 5000.0), self.addr, 100.1)
        self.b.tick(100.1)
        self.assertEqual(len(self.out), n)

    def test_sim_numbers_are_rate_limited_switches_not(self):
        self.b.packet(game.tmc1(5, 5000.0), self.addr, 100.0)
        self.b.tick(100.0)
        self.out.clear()
        fields = bridge.TMC1.unpack(game.tmc1(5, 5000.0))
        packet = lambda fuel, flags: bridge.TMC1.pack(fields[0], flags, *fields[2:15], fuel, *fields[16:])
        self.b.packet(packet(0.2, fields[1] | bridge.F_HAZARD), self.addr, 100.2)
        self.b.tick(100.2)
        self.assertIn("SIM_hazardsSwitch", self.sent())
        self.assertNotIn("SIM_batteryLevel", self.sent())
        self.b.tick(101.3)
        self.assertEqual(self.sent()["SIM_batteryLevel"], 20.0)

    def test_stale_slow_value_is_not_sent(self):
        self.b.packet(game.tmc1(5, 5000.0), self.addr, 100.0)
        self.b.tick(100.0)
        before = self.b.sent["SIM_batteryLevel"]
        self.b.pending_slow["SIM_batteryLevel"] = before + 5
        self.b.tick(100.1)
        self.assertNotIn("SIM_batteryLevel", self.b.pending_slow)

    def test_odometer_never_goes_back(self):
        self.b.packet(game.tmc1(5, 5000.0), self.addr, 100.0)
        self.b.packet(game.tmc1(5, 5500.0), self.addr, 100.1)
        self.assertAlmostEqual(self.b.odo_km, 1000.5)
        self.b.packet(game.tmc1(5, 10.0), self.addr, 100.2)     # another vehicle
        self.b.packet(game.tmc1(5, 110.0), self.addr, 100.3)
        self.assertAlmostEqual(self.b.odo_km, 1000.6)

    def test_game_gone_parks_the_car_and_comes_back(self):
        self.b.packet(game.tmc1(12, 5000.0), self.addr, 100.0)
        self.b.tick(100.0)
        self.assertEqual(self.sent()["DI_gear"], "D")
        self.b.tick(104.0)
        self.assertEqual(self.sent()["DI_gear"], "P")
        self.assertEqual(self.sent()["DI_vehicleSpeed"], 0.0)
        self.assertFalse(self.b.connected)
        self.b.packet(game.tmc1(12, 5000.0), self.addr, 105.0)
        self.b.tick(105.0)
        self.assertEqual(self.sent()["DI_gear"], "D")

    def test_stays_with_one_sender(self):
        self.b.packet(game.tmc1(12, 5000.0), self.addr, 100.0)
        self.assertFalse(self.b.packet(game.tmc1(12, 5000.0), ("10.0.0.3", 1), 101.0))
        self.assertTrue(self.b.packet(game.tmc1(12, 5000.0), ("10.0.0.3", 1), 104.0))

    def test_stock_ignored_while_the_mod_sends(self):
        self.b.packet(game.tmc1(12, 5000.0), self.addr, 100.0)
        self.assertFalse(self.b.packet(game.outgauge(5), self.addr, 100.5))
        self.assertTrue(self.b.packet(game.outgauge(5), self.addr, 103.0))

    def test_garbage(self):
        self.assertFalse(self.b.packet(b"hello", self.addr, 100.0))
        self.assertFalse(self.b.connected)

    def test_tires_relative_to_start(self):
        s = bridge.decode_tmc1(game.tmc1(5, 0))
        self.b.state.update(s)
        self.assertEqual(self.b.tires()["RR"], 2.9)         # a game car's 2 bar isn't a warning
        self.b.state["tires"] = dict(s["tires"], RR=100000.0)
        self.assertEqual(self.b.tires()["RR"], 1.45)
        self.b.state["tires"] = dict(s["tires"], RR=-1.0)
        self.assertNotIn("RR", self.b.tires())


class Geo(unittest.TestCase):
    def test_axes(self):
        lat, lon = bridge.world_to_geo((37.0, -122.0), (0.0, 1000.0))
        self.assertAlmostEqual(lat - 37.0, 0.008993, places=5)
        self.assertEqual(lon, -122.0)
        lat, lon = bridge.world_to_geo((37.0, -122.0), (1000.0, 0.0))
        self.assertGreater(lon, -122.0)


class Lua(unittest.TestCase):
    def run_stub(self, path=LUA):
        lua = shutil.which("luajit")
        if not lua:
            self.skipTest("no luajit")
        r = subprocess.run([lua, os.path.join(HERE, "test_lua_stub.lua"), path], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_mod_packet_decodes(self):
        data, target = self.run_stub()
        s = bridge.decode_tmc1(bytes.fromhex(data))
        self.assertIsNotNone(s, "struct size or magic differs from the Python format")
        self.assertEqual(s["pos"], (1.0, 2.0, 3.0))
        self.assertEqual(s["dir"], (0.5, -0.5))
        self.assertEqual(s["speed"], 20.0)
        self.assertEqual(s["steering"], 45.0)
        self.assertEqual(s["fuel"], 0.5)
        self.assertAlmostEqual(s["env_temp"], 20.0, places=1)
        self.assertEqual(s["power"], 70.0)
        self.assertEqual((s["gear"], s["gear_index"], s["ignition"], s["lights"]), ("D", 3, 2, 1))
        self.assertEqual(s["vehicle"], "etk800")
        self.assertEqual(s["tires"]["FL"], 200000.0)
        self.assertEqual(s["tires"]["RR"], 100000.0)
        self.assertEqual(s["tires"]["RL"], -1.0)
        for on in ("highbeam", "signal_l", "abs", "door_fl", "trunk"):
            self.assertTrue(s[on], on)
        for off in ("fog", "signal_r", "hazard", "parkbrake", "esc", "tcs", "door_fr", "door_rl", "hood"):
            self.assertFalse(s[off], off)

    def test_address_from_setting(self):
        self.assertEqual(self.run_stub()[1].split(), ["10.0.0.7", "4444"])

    def test_baked_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "m.zip")
            install.build(zip_path, "192.0.2.5")
            with zipfile.ZipFile(zip_path) as z:
                self.assertEqual(z.namelist(), [install.LUA])
                z.extract(install.LUA, tmp)
            self.assertEqual(self.run_stub(os.path.join(tmp, install.LUA))[1].split(), ["192.0.2.5", "4444"])

    def test_no_target_keeps_the_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "m.zip")
            install.build(zip_path, None)
            with zipfile.ZipFile(zip_path) as z:
                self.assertIn('local FIXED_ADDRESS = ""', z.read(install.LUA).decode())


class SpeedUnits(unittest.TestCase):
    """QtCar shows the number as it is under the UI's own label: 250 km/h showed as 155 KM/H."""

    def state(self, kmh):
        s = full_state()
        s["speed"] = kmh / 3.6
        return s

    def test_model3_follows_the_ui(self):
        v = mapped(bridge.map_model3, self.state(250), metric=True)
        self.assertEqual((v["DI_uiSpeedHighSpeed"], v["DI_uiSpeed"], v["DI_uiSpeedUnits"]), (250, 250, "KPH"))
        self.assertEqual(v["DI_vehicleSpeed"], 250)
        v = mapped(bridge.map_model3, self.state(250), metric=False)
        self.assertEqual((v["DI_uiSpeedHighSpeed"], v["DI_uiSpeed"], v["DI_uiSpeedUnits"]), (155, 155, "MPH"))
        self.assertEqual(v["DI_vehicleSpeed"], 250)     # km/h whatever the UI shows

    def test_models_follows_the_ui(self):
        v = mapped(bridge.map_models, self.state(250), metric=True)
        self.assertEqual((v["DI_analogSpeed"], v["DI_speedUnits"]), (250, "KPH"))
        self.assertAlmostEqual(v["DI_vehicleSpeed"], 155.3, 1)  # mph whatever the UI shows
        v = mapped(bridge.map_models, self.state(250), metric=False)
        self.assertEqual(v["DI_speedUnits"], "MPH")
        self.assertAlmostEqual(v["DI_analogSpeed"], 155.3, 1)

    def test_never_the_not_available_value(self):
        v = mapped(bridge.map_model3, self.state(600), metric=True)
        self.assertEqual((v["DI_uiSpeed"], v["DI_uiSpeedHighSpeed"], v["DI_vehicleSpeed"]), (254, 510, 287.5))
        v = mapped(bridge.map_models, self.state(600), metric=True)
        self.assertEqual((v["DI_analogSpeed"], v["DI_vehicleSpeed"]), (409.4, 179.6))

    def test_a_change_on_the_screen_goes_out(self):
        out = []
        b = bridge.Bridge(out.append, bridge.map_model3, 100.0)
        b.state.update(self.state(100))
        b.push(b.values(), 0.0)
        out.clear()
        b.metric = True
        b.push(b.values(), 0.1)
        self.assertEqual(out[0]["DI_uiSpeedUnits"], "KPH")
        self.assertEqual(out[0]["DI_uiSpeedHighSpeed"], 100)


class Lamps(unittest.TestCase):
    def test_model3_brake_and_tail_lamps(self):
        s = full_state()
        s.update(brake=0.5, lights=1)
        v = mapped(bridge.map_model3, s)
        for name in ("VCLEFT_brakeLightStatus", "VCRIGHT_brakeLightStatus", "VCLEFT_tailLightStatus", "VCRIGHT_tailLightStatus"):
            self.assertEqual(v[name], "ON")
        s.update(brake=0.0, lights=0)
        v = mapped(bridge.map_model3, s)
        for name in ("VCLEFT_brakeLightStatus", "VCRIGHT_brakeLightStatus", "VCLEFT_tailLightStatus", "VCRIGHT_tailLightStatus"):
            self.assertEqual(v[name], "OFF")


if __name__ == "__main__":
    unittest.main()
