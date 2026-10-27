#!/usr/bin/env python3
"""Fake vehicle CAN data for QtCar: sends CAN frames to QtCarVehicle, signal by signal.

On the car the gateway forwards CAN frames to the MCU as UDP datagrams: 2 bytes CAN id (big
endian) + 8 data bytes, broadcast to port 1234 (--udp :1234 in /etc/RunQtCar.vars). QtCarVehicle
(service qtcar-vehicle) turns them into data values (VAPI_*, ...) for QtCar. This tool encodes
signals by name with the firmware's own signal database (vehicle/work/can-db.json, made by
extract-can-db.py) and sends each message at its period, with counter and checksum filled in.

  tesla-can.py DI_gear=P DI_odometer=12345             run: send these signals until Ctrl+C
  tesla-can.py --preset parked [SIGNAL=VALUE ...]       start from a preset (see PRESETS)
  tesla-can.py --set DI_gear=D DI_vehicleSpeed=50       change values of the running instance
  tesla-can.py --set --preset charging                  ... or apply a preset to it
  tesla-can.py --list-presets                           what the presets set
  tesla-can.py --preset parked --http 8099              plus a web page to change things at runtime
  tesla-can.py --preset parked --respond                answer the UI's requests (frunk button, locks, ...)
  tesla-can.py --unset DI_vehicleSpeed                  stop sending a signal (its message stops
                                                        when no signal of it is left)
  tesla-can.py --status                                 what the running instance sends
  tesla-can.py --find odometer                          search signals (name, message, unit, enum)
  tesla-can.py SIM_driveRailOn=true                     SIM_* names go to QtCarSimService instead
                                                        (its data values, HTTP port 4190)

VALUE is a number in the signal's unit (see --find) or one of its enum labels (P, R, N, D, ...).
Other signals of the same message are sent as 0. A message that someone else is sending is left
to them: this tool listens on the port and pauses such ids (--no-yield to send anyway).

With --sim-port PORT (and QtCarSimService started with --udp :PORT, as start_all.sh does) the
sim's frames come here first and are forwarded, with the signals set here written into them. So
any signal can be set, also in messages the sim sends (e.g. BMS_packCurrent in 0x132).
"""
import argparse, collections, gzip, http.server, json, math, os, socket, struct, subprocess, sys, threading, time, urllib.parse

HERE = os.path.dirname(os.path.realpath(__file__))
DB_PATH = os.path.join(HERE, "work", "can-db.json")
# (20100 was taken: the 2026 S/X firmware's QtCarVehicle sends its requests there, --udptx :20100)
CONTROL = ("127.0.0.1", 20180)
# bytes of CAN id in front of each frame: 2 (the Model 3's 2019 firmware), 4 (the S/X 2026
# firmware: QtCarVehicle reads the gateway's "ETH" stream, a 32-bit big-endian id + the data);
# set from the database by load_db()
HEADER = 2
# the port QtCarVehicle sends the UI's requests to (--udptx in RunQtCar.vars/.env)
TX_PORT = 4321

# CAN ids QtCarSimService sends by itself (firmware 2019.20.4.2, Model 3 defaults)
SIM_IDS = {0x03e, 0x102, 0x103, 0x129, 0x132, 0x204, 0x20d, 0x210, 0x212, 0x214, 0x21d, 0x221, 0x229,
           0x243, 0x249, 0x261, 0x264, 0x292, 0x2a9, 0x2e1, 0x2e8, 0x2f2, 0x320, 0x321, 0x322, 0x339,
           0x340, 0x352, 0x36f, 0x3a1, 0x3c2, 0x3e2, 0x3e3, 0x3f1, 0x3f9, 0x3fa, 0x438, 0x458, 0x4e2,
           0x743, 0x744, 0x7ff}

TPMS_WARN_BAR = 2.2     # soft warning below this (25 % under the Model 3's 2.9 bar)

PRESETS = {
    # what QtCarSimService leaves out: the drive inverter (gear, speed, odometer)
    "parked": {
        "DI_gear": "P", "DI_systemState": "STANDBY", "DI_immobilizerState": "DISARMED",
        "DI_vehicleSpeed": 0, "DI_uiSpeed": 0, "DI_uiSpeedUnits": "MPH",
        "DI_odometer": 12345,
        # with the drive rail on, ESPStatusMessage (0x145) turns ESP on while DI_tcTelltaleOn
        # is invalid, and BRAKE amber unless the iBooster reports READY/ACTUATION
        "DI_tcTelltaleOn": "OFF", "DI_ptcStateUI": "ON",
        "IBST_iBoosterStatus": "READY", "IBST_driverBrakeApply": "BRAKES_NOT_APPLIED",
        # the simulator's tires are at 1.0 bar; a TPMS module that has learned its sensors
        "SIM_tpmsPressureFL": 2.9, "SIM_tpmsPressureFR": 2.9, "SIM_tpmsPressureRL": 2.9, "SIM_tpmsPressureRR": 2.9,
        "TPMS_learningStatus": "PASSED", "TPMS_localisationStatus": "PASSED",
    },
    # the rest change one part of the car; combine them, also at runtime (--set --preset NAME).
    # SIM_* go to QtCarSimService; CAN signals in messages the sim sends (BMS_packCurrent in
    # 0x132) need --sim-port. See vehicle/README.md for what each one drives in QtCar.
    "drive": {"SIM_driveRailOn": "true", "DI_gear": "D", "DI_uiSpeedHighSpeed": 30, "DI_vehicleSpeed": 48},
    "stop": {"DI_gear": "P", "DI_uiSpeedHighSpeed": 0, "DI_vehicleSpeed": 0},
    "rail-off": {"SIM_driveRailOn": "false"},
    "charging": {
        "SIM_chargePortDoor": "true", "SIM_chargerProximity": 2, "SIM_chargePortLatch": "Engaged",
        "SIM_isPilotGood": "true", "SIM_chargeState": "Charging", "SIM_chargeTimeToFull": 1.5,
        "BMS_packCurrent": 30,      # positive = into the pack: charge rate (mi/hr) on the charging screen
    },
    "charge-complete": {"SIM_chargeState": "Complete", "SIM_chargeTimeToFull": 0, "BMS_packCurrent": 0},
    "unplugged": {
        "SIM_chargeState": "Disconnected", "SIM_chargerProximity": 0, "SIM_chargePortLatch": "Disengaged",
        "SIM_isPilotGood": "false", "SIM_chargePortDoor": "false", "SIM_chargeTimeToFull": 0, "BMS_packCurrent": 0,
    },
    "doors-open": {"SIM_driverDoor": "true", "SIM_passFrontDoor": "true", "SIM_driverRearDoor": "true",
                   "SIM_passRearDoor": "true", "SIM_rearTrunk": "true", "SIM_frontTrunk": "true"},
    "doors-closed": {"SIM_driverDoor": "false", "SIM_passFrontDoor": "false", "SIM_driverRearDoor": "false",
                     "SIM_passRearDoor": "false", "SIM_rearTrunk": "false", "SIM_frontTrunk": "false"},
    "locked": {"SIM_vehicleLockState": "true"},
    "unlocked": {"SIM_vehicleLockState": "false"},
    "lights-on": {"SIM_headLights": "true", "SIM_parkingLights": "true"},
    "lights-off": {"SIM_headLights": "false", "SIM_parkingLights": "false", "SIM_highbeamSwitch": "false",
                   "SIM_frontFogLights": "false"},
    # a pressure below TPMS_WARN_BAR also sets that wheel's warning (Sender.set). The warning
    # (telltale, "Tire pressure low") shows with the drive rail on; the image needs
    # [vapi] filter_tpms_faults=false (the kit sets it)
    "tires-ok": {"SIM_tpmsPressureFL": 2.9, "SIM_tpmsPressureFR": 2.9, "SIM_tpmsPressureRL": 2.9, "SIM_tpmsPressureRR": 2.9},
    "tire-low": {"SIM_tpmsPressureFL": 1.7},
    "winter": {"SIM_hvacOutsideTemp": -5, "SIM_hvacInsideTemp": 2},
    "summer": {"SIM_hvacOutsideTemp": 32, "SIM_hvacInsideTemp": 40},
}


# The Model S/X 2026 firmware (no simulator: with --all-zero every ECU message is sent, these set
# what matters on top; found in session 9, see notes/session9-*.md). Replaces PRESETS for its
# database (load_db).
PRESETS_SX = {
    "parked": {
        # the car's configuration comes from the gateway: GTW_packConfig indexes libQtCarVAPI's
        # packTypeParams, 24 = the 85 kWh pack (265 mi rated); another soft pack = "mismatch"
        "GTW_packConfig": 24, "GTW_softPackConfig": 0,
        # The map tile API needs a nonempty region_code. Without a gateway country QtCar keeps
        # map/MapCountry null and Google rejects every tile request with HTTP 400.
        # The gateway encodes the two ASCII letters as a 16-bit integer (US = 0x5553).
        "GTW_country": 0x5553, "GTW_mapRegion": "us",
        # rails: VAPI_driveRailOn is GTW_gatewayStatus byte 4 bit 1 (ModelS_GatewayStatusMessage)
        "GTW_vehiclePowerState": 1, "GTW_accRailRequest": 1, "GTW_hvacRailRequest": 1,
        "X_driver": 1,
        # the touchscreen/panel power (0: QtCar natively showed its logo instead of the UI)
        "GTW_touchPower": 1,
        "DI_gear": "P",
        # EnergyStatusMessage: level = (remaining - buffer) / (full pack - buffer); X_batteryLevel sets the
        # remaining energies for a level (VIRTUAL)
        "BMS_nominalFullPackEnergy": 85, "BMS_energyBuffer": 4, "X_batteryLevel": 69, "BMS_odometer": 12345,
        "BMS_packVoltage": 385,
        # the thermal controller reports the HVAC system nominal (HVAC_systemNominal): else
        # climate keeper and dog mode are "unavailable due to system fault"
        "THC_hvacSystemNominal": 1,
        # heated seats (front, rear), steering wheel and wipers installed (VAPI_hasSeatHeaters, ...)
        "GTW_seatHeatersInstalled": 1, "GTW_rearSeatHeatersInstalled": "BASE", "GTW_steeringHeat": "INSTALLED",
        "GTW_wiperHeatInstalled": 1,
        # speed 0 explicitly: the signal has an offset (raw 0 = -25 mph in VAPI_signedVehicleSpeed);
        # the speedometer (VAPI_vehicleSpeed, VAPI_displaySpeed) is DI_analogSpeed
        "DI_vehicleSpeed": 0, "DI_analogSpeed": 0,
        # outside temperature (HVAC_outsideTemp): the gateway's, through a filter that takes the first
        # value as it is and later lets it rise only while the car moves (heat soak when parked)
        "GTW_ambientTemperature": 20,
        # doors and trunks (VAPI_doorState), lock (VAPI_isLocked), charging (VAPI_isCharging),
        # charge port (VAPI_chargePortDoorSensor, VAPI_chargePortLatch): found with the probe of
        # session 14 (set one signal, see which VAPI_* changes)
        "SC_driverFrontDoorOpenStatus": "closed", "SC_driverRearDoorOpenStatus": "closed",
        "SC_passFrontDoorOpenStatus": "closed", "SC_passRearDoorOpenStatus": "closed",
        "SC_frontTrunkStatus": "closed", "SC_rearTrunkStatus": "closed",
        "SC_vehicleLockStatus": "RKE_UNLOCKED", "GTW_chargeState": "DISCONNECTED",
        "CP_chargeDoorOpen": 0, "CP_latchState": "DISENGAGED",
        "TPMS_pressureFL": 2.9, "TPMS_pressureFR": 2.9, "TPMS_pressureRL": 2.9, "TPMS_pressureRR": 2.9,
    },
    "drive": {"DI_gear": "D", "DI_vehicleSpeed": 30, "DI_analogSpeed": 30},
    "stop": {"DI_gear": "P", "DI_vehicleSpeed": 0, "DI_analogSpeed": 0},
    # a 240 V station with a 32 A pilot; ChargeModelSX sets the currents, the power and the battery
    "charging": {"CP_chargeDoorOpen": 1, "CP_latchState": "ENGAGED", "GTW_chargeState": "CHARGING",
                 "CHG_proximity": "LATCHED", "CHG_pilot": "LINE_CHARGE", "CHG_evseAccept": 1, "CHG_chargeEnabled": 1,
                 "CHG_pilotCurrent": 32, "CHG_inputVoltage": 240},
    "charge-complete": {"GTW_chargeState": "COMPLETE", "CHG_chargeEnabled": 0},
    "unplugged": {"GTW_chargeState": "DISCONNECTED", "CP_latchState": "DISENGAGED", "CP_chargeDoorOpen": 0,
                  "CHG_proximity": "DISCONNECTED", "CHG_pilot": "NONE", "CHG_evseAccept": 0, "CHG_chargeEnabled": 0,
                  "CHG_pilotCurrent": 0, "CHG_inputVoltage": 0, "CHG_maxLineCurrent": 0, "BMS_chgLineCurrentAvail": 0},
    "doors-open": {"SC_driverFrontDoorOpenStatus": "open", "SC_driverRearDoorOpenStatus": "open",
                   "SC_passFrontDoorOpenStatus": "open", "SC_passRearDoorOpenStatus": "open",
                   "SC_frontTrunkStatus": "open", "SC_rearTrunkStatus": "open"},
    "doors-closed": {"SC_driverFrontDoorOpenStatus": "closed", "SC_driverRearDoorOpenStatus": "closed",
                     "SC_passFrontDoorOpenStatus": "closed", "SC_passRearDoorOpenStatus": "closed",
                     "SC_frontTrunkStatus": "closed", "SC_rearTrunkStatus": "closed"},
    "charge-stopped": {"GTW_chargeState": "STOPPED", "CHG_chargeEnabled": 0},
    "locked": {"SC_vehicleLockStatus": "RKE_LOCKED"},
    "unlocked": {"SC_vehicleLockStatus": "RKE_UNLOCKED"},
    "lights-on": {"BC_headLightLStatus": "On", "BC_headLightRStatus": "On", "BC_parkingLightLStatus": "ON",
                  "BC_parkingLightRStatus": "ON", "BC_tailLightLStatus": "ON", "BC_tailLightRStatus": "ON"},
    "lights-off": {"BC_headLightLStatus": "Off", "BC_headLightRStatus": "Off", "BC_parkingLightLStatus": "OFF",
                   "BC_parkingLightRStatus": "OFF", "BC_tailLightLStatus": "OFF", "BC_tailLightRStatus": "OFF",
                   "BC_highBeamLStatus": "OFF", "BC_highBeamRStatus": "OFF", "BC_fogLightFLStatus": "Off",
                   "BC_fogLightFRStatus": "Off", "BC_fogLightRLStatus": "Off", "BC_fogLightRRStatus": "Off"},
}


def _sx_pair(pattern, sides, on):
    """The S/X body controller's lamp pair, e.g. BC_fogLightFLStatus + FR. Their enums differ in case
    (On/Off, ON/OFF); parse_value ignores it."""
    return {pattern % s: "On" if on else "Off" for s in sides}


def _sx_epb(applied):
    """The S/X parking brake (VAPI_parkBrakeApplied needs all three)."""
    return {"EPB_brakeApplied": "APPLIED" if applied else "RELEASED", "EPB_state": "PARK" if applied else "OPEN",
            "EPB_syncState": "READY"}


def _sx_thc(v, cur):
    """The S/X climate: the thermal controller echoes the UI's request (MCU_THCHumanInterfaceRequest)
    in THC_MCUHMIResponse, which QtCarVehicle turns into HVAC_powerState, _aconStatus, _fanStatus,
    _driverTempStatus, _passengerTempStatus, _ventStatus, _intakeStatus. Recomputed from the whole last request on
    every change; in AUTO what a car might pick (fan 3, panel and floor, A/C on)."""
    req = lambda name: str(cur.get("req:MCU_" + name, ""))
    off = req("OffBtnState_Flag") == "OFF"
    manual = lambda flag: req(flag) == "MANUAL"
    # while off the fan shows 0 and QtCar copies that into its manual request: back on, the car
    # runs at the last speed again
    fan = req("BlowerMotorMnlSet_Seg")
    if fan not in ("", "0"):
        _sx_thc.fan = fan
    return {
        "THC_MCUOffInd_Flag": int(off),
        "THC_MCUAutoInd_Flag": int(req("AutoBtnState_Flag") == "AUTO" and not off),
        "THC_MCUAconInd_Flag": int(not off and (req("AconBtnState_Flag") == "ON" if manual("AconMnl_Flag") else True)),
        "THC_MCUBlowerSegDis_Flag": 0 if off else _sx_thc.fan if manual("BlowerMotorMnl_Flag") else 3,
        "THC_MCUModeDis_Flag": req("ModeActMnlSet_Flag") if manual("ModeActMnl_Flag") else "PanelAndFloor",
        "THC_MCULeftTempSetpoint_DegC": req("LeftTempSetpoint_DegC") or 21,
        "THC_MCURightTempSetpoint_DegC": req("RightTempSetpoint_DegC") or 21,
        "THC_MCUIntakeInd_Flag": req("IntakeBtnState_Flag") or "AUTO",
        # HVAC_intakeStatus: Recirc above 50 % recirculated air (the recirc button asks for
        # FRESH_OFF_RECIRC)
        "THC_MCUIntakePercent": 100 if req("IntakeBtnState_Flag") == "FRESH_OFF_RECIRC" and not off else 0,
        "THC_MCUDefogInd_Flag": req("DefogBtnState_Flag") or "OFF",
        "THC_MCUDualInd_Flag": req("DualBtnState_Flag") or 0,
        "THC_MCUHtronInd_Flag": req("HtronBtnState_Flag") or 0,
    }


_sx_thc.fan = "3"


def _sx_charge_enable(v, cur):
    """The S/X UI's Stop/Start Charging: stops or resumes a plugged-in session."""
    state = cur.get("GTW_chargeState")
    if v == "ENABLE" and state == 5:            # STOPPED
        return {"GTW_chargeState": "CHARGING", "CHG_chargeEnabled": 1}
    if v != "ENABLE" and state == 3:            # CHARGING
        return PRESETS_SX["charge-stopped"]
    return {}


def _sx_lamps(head, park):
    """Low beams and parking + tail lights of the S/X (Pektron body controller)."""
    return dict(_sx_pair("BC_headLight%sStatus", "LR", head), **_sx_pair("BC_parkingLight%sStatus", "LR", park),
                **_sx_pair("BC_tailLight%sStatus", "LR", park))


# Values that stand for several signals (Sender.set): X_batteryLevel = the S/X's level in %, as the
# BMS energies of the parked preset give it (level = (remaining - buffer) / (full - buffer))
def _sx_battery(pct):
    remaining = 4 + float(pct) * (85 - 4) / 100
    return {"BMS_nominalEnergyRemaining": remaining, "BMS_expectedEnergyRemaining": remaining,
            "BMS_idealEnergyRemaining": remaining + 2}


# X_driver = the S/X driver in the car (1) or gone (0). Gone: first not present, the drive rail only
# 2 s later; QtCar's display state machine decides at the rail change (On -> Dog with dog mode on,
# else Accessory) and SCStatus goes out only once a second ("_later": (seconds, values))
def _sx_driver(v):
    if float(v):
        return {"SEC_driverPresent": "PRESENT", "GTW_driverIsLeaving": 0, "GTW_vehiclePowerState": "DRIVE"}
    return {"SEC_driverPresent": "NOT_PRESENT", "GTW_driverIsLeaving": 1,
            "_later": (2.0, {"GTW_vehiclePowerState": "NOT_DRIVE"})}


VIRTUAL = {"X_batteryLevel": _sx_battery, "X_driver": _sx_driver}


# The S/X scroll wheels (STW_status): the panel's Arcade controls use the Model 3's names
# (VCLEFT_swc{Left,Right}{TiltLeft,Pressed,TiltRight,ScrollTicks}); on the S they are one button
# bit mask per side (bit 2 top = STW_leftTop, 1 middle, 0 bottom; the Model 3's tilt left is top)
# and the wheel's ticks (8 bits, 255 = -1) -> STW_leftScroll. Only in the S/X's VIRTUAL.
_sx_swc_bits = {"TiltLeft": 4, "Pressed": 2, "TiltRight": 1}
_sx_swc_held = {"Left": 0, "Right": 0}


def _sx_swc(side, part):
    def fn(v):
        if part == "ScrollTicks":
            return {"STW_%sWheel" % side.lower(): int(float(v)) % 256}
        on = str(v).upper() in ("ON", "1", "1.0", "TRUE")
        bit = _sx_swc_bits[part]
        _sx_swc_held[side] = _sx_swc_held[side] | bit if on else _sx_swc_held[side] & ~bit
        return {"STW_%sButtons" % side.lower(): _sx_swc_held[side]}
    return fn


VIRTUAL_SX = {"VCLEFT_swc%s%s" % (s, p): _sx_swc(s, p) for s in ("Left", "Right")
              for p in ("TiltLeft", "Pressed", "TiltRight", "ScrollTicks")}


# The web panel's controls (panel.html renders them): one table per car. A section has rows
# {label, owner (a signal the GPS may drive: badge), items}; a row without a label shows its items
# as chips. Items: {toggle: label, on: {...}, off: {...}} (on when all of "on" are set),
# {seg: signal, options}, {range: signal, min, max, step, unit}, {preset: name, label, match: {...}},
# {speed: {signal: factor, ...}} (mph, the first signal shows it; the others get mph * factor).
def _tog(label, name):
    return {"toggle": label, "on": {name: "true"}, "off": {name: "false"}}


def _range(name, lo, hi, step=1, unit=""):
    return {"range": name, "min": lo, "max": hi, "step": step, "unit": unit}


CONTROLS = [      # the Model 3: QtCarSimService's SIM_* values and the drive inverter
    {"title": "Drive", "note": "gps", "rows": [
        {"label": "Drive rail", "owner": "SIM_driveRailOn", "items": [_tog("on", "SIM_driveRailOn")]},
        {"label": "Gear", "owner": "DI_gear", "items": [{"seg": "DI_gear", "options": ["P", "R", "N", "D"]}]},
        {"label": "Speed", "owner": "DI_uiSpeedHighSpeed", "items": [{"speed": {"DI_uiSpeedHighSpeed": 1, "DI_vehicleSpeed": 1.609344}}]},
        {"label": "Battery", "items": [_range("SIM_batteryLevel", 0, 100, 1, "%")]}]},
    {"title": "Doors and locks", "rows": [
        {"items": [_tog("driver", "SIM_driverDoor"), _tog("passenger", "SIM_passFrontDoor"),
                   _tog("rear left", "SIM_driverRearDoor"), _tog("rear right", "SIM_passRearDoor"),
                   _tog("frunk", "SIM_frontTrunk"), _tog("trunk", "SIM_rearTrunk")]},
        {"label": "Locked", "items": [_tog("locked", "SIM_vehicleLockState")]}]},
    {"title": "Charging", "rows": [
        {"label": "State", "items": [
            {"preset": "unplugged", "match": {"SIM_chargeState": "Disconnected"}},
            {"preset": "charging", "match": {"SIM_chargeState": "Charging"}},
            {"preset": "charge-complete", "label": "complete", "match": {"SIM_chargeState": "Complete"}}]},
        {"label": "Pack current", "items": [_range("BMS_packCurrent", 0, 250, 1, " A")]},
        {"label": "Time to full", "items": [_range("SIM_chargeTimeToFull", 0, 12, 0.25, " h")]}]},
    {"title": "Lights", "rows": [
        {"items": [_tog("headlights", "SIM_headLights"), _tog("parking", "SIM_parkingLights"),
                   _tog("high beam", "SIM_highbeamSwitch"), _tog("fog", "SIM_frontFogLights")]}]},
    {"title": "Signals and more", "rows": [
        {"items": [_tog("◀ left", "SIM_signalLeft"), _tog("right ▶", "SIM_signalRight"), _tog("hazards", "SIM_hazardsSwitch")]},
        {"label": "Parking brake", "items": [_tog("applied", "SIM_parkBrakeApplied")]},
        {"label": "Driver belt", "items": [_tog("unbuckled", "SIM_driverSeatbelt")]}]},
    {"title": "Climate", "rows": [
        {"label": "Outside", "items": [_range("SIM_hvacOutsideTemp", -25, 45, 0.5, " °C")]},
        {"label": "Inside", "items": [_range("SIM_hvacInsideTemp", -25, 60, 0.5, " °C")]}]},
    {"title": "Tire pressure (bar)", "layout": "tires", "rows": [
        {"label": w, "items": [_range("SIM_tpmsPressure" + w, 1, 3.5, 0.05)]} for w in ("FL", "FR", "RL", "RR")]},
]


def _sx_tog(label, on, off):
    return {"toggle": label, "on": on, "off": off}


def _sx_door(label, name):
    return _sx_tog(label, {name: "open"}, {name: "closed"})


CONTROLS_SX = [   # the S/X 2026: the ECUs' own CAN signals (found with the probe of session 14)
    {"title": "Drive", "hint": "Driver off = the driver has left (drive rail off): the screen goes to "
                               "standby, or shows the dog screen while Dog mode is on.", "rows": [
        {"label": "Gear", "owner": "DI_gear", "items": [{"seg": "DI_gear", "options": ["P", "R", "N", "D"]}]},
        {"label": "Speed", "owner": "DI_vehicleSpeed", "items": [{"speed": {"DI_analogSpeed": 1, "DI_vehicleSpeed": 1}}]},
        {"label": "Battery", "items": [_range("X_batteryLevel", 0, 100, 1, "%")]},
        {"label": "Driver", "items": [_sx_tog("in the car", {"X_driver": 1}, {"X_driver": 0})]}]},
    {"title": "Doors and locks", "rows": [
        {"items": [_sx_door("driver", "SC_driverFrontDoorOpenStatus"), _sx_door("passenger", "SC_passFrontDoorOpenStatus"),
                   _sx_door("rear left", "SC_driverRearDoorOpenStatus"), _sx_door("rear right", "SC_passRearDoorOpenStatus"),
                   _sx_door("frunk", "SC_frontTrunkStatus"), _sx_door("trunk", "SC_rearTrunkStatus")]},
        {"label": "Locked", "items": [_sx_tog("locked", {"SC_vehicleLockStatus": "RKE_LOCKED"},
                                              {"SC_vehicleLockStatus": "RKE_UNLOCKED"})]},
        {"label": "Charge port", "items": [_sx_tog("open", {"CP_chargeDoorOpen": 1}, {"CP_chargeDoorOpen": 0})]}]},
    {"title": "Charging", "rows": [
        {"label": "State", "items": [
            {"preset": "unplugged", "match": {"GTW_chargeState": "DISCONNECTED"}},
            {"preset": "charging", "match": {"GTW_chargeState": "CHARGING"}},
            {"preset": "charge-complete", "label": "complete", "match": {"GTW_chargeState": "COMPLETE"}},
            {"preset": "charge-stopped", "label": "stopped", "match": {"GTW_chargeState": "STOPPED"}}]},
        {"label": "Station", "items": [_range("CHG_pilotCurrent", 6, 80, 1, " A")]},
        {"label": "Voltage", "items": [_range("CHG_inputVoltage", 100, 250, 5, " V")]}]},
    {"title": "Lights", "rows": [
        {"items": [_sx_tog("headlights", _sx_pair("BC_headLight%sStatus", "LR", True), _sx_pair("BC_headLight%sStatus", "LR", False)),
                   _sx_tog("parking", dict(_sx_pair("BC_parkingLight%sStatus", "LR", True), **_sx_pair("BC_tailLight%sStatus", "LR", True)),
                           dict(_sx_pair("BC_parkingLight%sStatus", "LR", False), **_sx_pair("BC_tailLight%sStatus", "LR", False))),
                   _sx_tog("high beam", {"BC_flashMainSwitch": "Main_beam_toggle"}, {"BC_flashMainSwitch": "Flash_off"}),
                   _sx_tog("fog front", _sx_pair("BC_fogLight%sStatus", ("FL", "FR"), True), _sx_pair("BC_fogLight%sStatus", ("FL", "FR"), False)),
                   _sx_tog("fog rear", _sx_pair("BC_fogLight%sStatus", ("RL", "RR"), True), _sx_pair("BC_fogLight%sStatus", ("RL", "RR"), False))]}]},
    {"title": "Signals and more", "rows": [
        {"items": [_sx_tog("◀ left", {"BC_indicatorLStatus": "On"}, {"BC_indicatorLStatus": "Off"}),
                   _sx_tog("right ▶", {"BC_indicatorRStatus": "On"}, {"BC_indicatorRStatus": "Off"}),
                   _sx_tog("hazards", {"SC_hazardSwitchStatus": "Hazard_request", "BC_indicatorLStatus": "On", "BC_indicatorRStatus": "On"},
                           {"SC_hazardSwitchStatus": "No_request", "BC_indicatorLStatus": "Off", "BC_indicatorRStatus": "Off"})]},
        {"label": "Parking brake", "items": [_sx_tog("applied", _sx_epb(True), _sx_epb(False))]},
        {"label": "Driver belt", "items": [_sx_tog("buckled", {"SDM_buckleDriverStatus": "BUCKLE_LATCHED"},
                                                   {"SDM_buckleDriverStatus": "BUCKLE_UNLATCHED"})]}]},
    {"title": "Climate", "hint": "The car filters its outside sensor: a lower value shows within seconds, "
                                 "a higher one only while the car moves (speed > 0).", "rows": [
        {"label": "Outside", "items": [_range("GTW_ambientTemperature", -25, 45, 0.5, " °C")]}]},
    {"title": "Tire pressure (bar)", "layout": "tires", "rows": [
        {"label": w, "items": [_range("TPMS_pressure" + w, 1, 3.5, 0.05)]} for w in ("FL", "FR", "RL", "RR")]},
]

# data values the web page shows (read from QtCarVehicle, port 4030)
PANEL_VALUES = ["VAPI_driveRailOn", "VAPI_shiftState", "VAPI_vehicleSpeed", "VAPI_odometer", "VAPI_batteryLevel",
                "VAPI_ratedRange", "VAPI_doorState", "VAPI_isLocked", "VAPI_isCharging", "VAPI_chargePortLatch",
                "VAPI_chargeTimeToFull", "VAPI_batteryCurrent", "VAPI_headLights", "VAPI_highBeamLights",
                "VAPI_frontFogLights", "HVAC_outsideTemp", "TPMS_pressureFL", "TPMS_pressureFR", "TPMS_pressureRL",
                "TPMS_pressureRR", "VAPI_telltaleESP", "VAPI_telltaleBrakes"]
# the S/X: its lamps are LIGHT_* (VAPI_headLights stays invalid, nothing in its firmware sets it)
PANEL_VALUES_SX = ["VAPI_driveRailOn", "VAPI_shiftState", "VAPI_vehicleSpeed", "VAPI_odometer", "VAPI_batteryLevel",
                   "VAPI_ratedRange", "VAPI_doorState", "VAPI_isLocked", "VAPI_isCharging", "VAPI_chargePortDoorSensor",
                   "VAPI_chargePortLatch", "VAPI_chargerPower", "VAPI_chargerCurrent", "VAPI_chargeTimeToFull",
                   "LIGHT_headlightLeft", "LIGHT_parkingLeft", "LIGHT_highBeamLeft",
                   "LIGHT_frontFogLeft", "LIGHT_rearFogLeft", "VAPI_turnSignalActive", "VAPI_hazards",
                   "VAPI_parkBrakeApplied", "VAPI_driverSeatbelt", "HVAC_outsideTemp", "TPMS_pressureFLConti",
                   "TPMS_pressureFRConti", "TPMS_pressureRLConti", "TPMS_pressureRRConti"]
CAR = "Model 3"


# How the "car" answers requests from QtCar. QtCarVehicle sends the UI's requests as CAN frames to
# the gateway (UDP broadcast to :4321, 2-byte header bus << 12 | CAN id, then 8 data bytes); the
# simulator ignores most of them, so tesla-can.py (--respond) plays the ECUs:
# UI signal -> (kind, function(value) -> values to set). "edge": on a change to a non-zero value
# (buttons: frunk, trunk), "level": on every change (switches).
def _toggle(name):
    return lambda v, cur: {name: "false" if str(cur.get(name, "false")).lower() == "true" else "true"}


def _blower(req):
    """The fan speed the car runs at for a requested one (AUTO: 3)."""
    return {"VCRIGHT_hvacBlowerSegment": "3" if req in ("AUTO", "SNA") else req}


RESPONSES = {
    "UI_frunkRequest": ("edge", lambda v, cur: {"SIM_frontTrunk": "true"}),
    "UI_trunkRequest": ("edge", _toggle("SIM_rearTrunk")),        # no power liftgate: open, or close again
    "UI_lockRequest": ("level", lambda v, cur: {"SIM_vehicleLockState": "true"} if v in ("LOCK", "REMOTE_LOCK")
                       else {"SIM_vehicleLockState": "false"} if v in ("UNLOCK", "REMOTE_UNLOCK") else {}),
    "UI_lightSwitch": ("level", lambda v, cur: {
        "ON": {"SIM_headLights": "true", "SIM_parkingLights": "true"},
        "PARKING": {"SIM_headLights": "false", "SIM_parkingLights": "true"},
        "OFF": {"SIM_headLights": "false", "SIM_parkingLights": "false"}}.get(v, {})),
    "UI_frontFogSwitch": ("level", lambda v, cur: {"SIM_frontFogLights": "true" if v == "1" else "false"}),
    # wiper button in the UI -> the front body controller's wiper state (0x2E1, in the sim's frames)
    "UI_wiperRequest": ("level", lambda v, cur: {"VCFRONT_wiperState": {
        "OFF": "PARK", "AUTO": "INT_AUTO_LOW", "SLOW_INTERMITTENT": "INTERMITTENT_LOW",
        "FAST_INTERMITTENT": "INTERMITTENT_HIGH", "SLOW_CONTINUOUS": "CONT_SLOW",
        "FAST_CONTINUOUS": "CONT_FAST"}[v]} if v not in ("SNA",) else {}),
    # climate (UI_hvacRequest 0x2f3) -> the right body controller's feedback (0x243, in the sim's
    # frames), which the climate panel shows (fan speed, A/C, ...). AUTO: what a car might pick.
    # "req:NAME" in cur = the UI's last request of that signal.
    "UI_hvacReqUserPowerState": ("level", lambda v, cur: {"VCRIGHT_hvacPowerState": "OFF", "VCRIGHT_hvacBlowerSegment": "OFF",
                                                          "VCRIGHT_hvacACRunning": "OFF"} if v == "OFF" else
                                 {"VCRIGHT_hvacPowerState": v, **_blower(cur.get("req:UI_hvacReqBlowerSegment", "AUTO"))}),
    "UI_hvacReqBlowerSegment": ("level", lambda v, cur: _blower(v) if cur.get("req:UI_hvacReqUserPowerState") != "OFF" else {}),
    "UI_hvacReqAirDistributionMode": ("level", lambda v, cur: {
        "VCRIGHT_hvacAirDistributionMode": "PANEL_FLOOR" if v == "AUTO" else v.replace("MANUAL_", "")}),
    "UI_hvacReqRecirc": ("level", lambda v, cur: {"VCRIGHT_hvacRecirc": v}),
    "UI_hvacReqSecondRowState": ("level", lambda v, cur: {"VCRIGHT_hvacSecondRowState": v}),
    "UI_hvacReqACDisable": ("level", lambda v, cur: {"VCRIGHT_hvacACRunning": "OFF" if v == "OFF" else "ON"}),
    "UI_openChargePortDoorRequest": ("edge", lambda v, cur: {"SIM_chargePortDoor": "true"}),
    "UI_closeChargePortDoorRequest": ("edge", lambda v, cur: {"SIM_chargePortDoor": "false"}),
    # the 2026 S/X UI's requests (MCU_* on the gateway's ETH stream); not in the Model 3's database
    "MCU_lockRequest": ("level", lambda v, cur: {"SC_vehicleLockStatus": "RKE_LOCKED"} if v in ("MCU_LOCK", "REMOTE_LOCK")
                        else {"SC_vehicleLockStatus": "RKE_UNLOCKED"} if v in ("MCU_UNLOCK", "REMOTE_UNLOCK") else {}),
    "MCU_requestOpenChargePortDoor": ("level", lambda v, cur: {"CP_chargeDoorOpen": "1"} if v == "OPEN_DOOR_COMMAND" else {}),
    "MCU_requestCloseChargePortDoor": ("level", lambda v, cur: {"CP_chargeDoorOpen": "0"} if v == "CLOSE_DOOR_COMMAND" else {}),
    "MCU_liftgateSwitch": ("level", lambda v, cur: {"SC_rearTrunkStatus": "closed" if str(cur.get("SC_rearTrunkStatus")) in ("1", "1.0", "open")
                                                    else "open"} if v == "PRESSED" else {"SC_rearTrunkStatus": "closed"} if v == "CLOSE" else {}),
    "MCU_parkBrakeRequest": ("edge", lambda v, cur: _sx_epb(str(cur.get("EPB_brakeApplied")) not in ("1", "1.0", "APPLIED"))),
    # the frunk button (MCU_commands, as the liftgate); closing it is by hand (the panel)
    "MCU_frontHoodCommand": ("level", lambda v, cur: {"SC_frontTrunkStatus": "open"} if v == "GOTO_OPEN" else {}),
    # Controls > Lights: the Pektron body controller's lamps (BC_* -> LIGHT_*, the car picture)
    "MCU_lightSwitchToGtw": ("level", lambda v, cur: {
        "ON_LIGHT_SWITCH": _sx_lamps(head=True, park=True), "PARK_ONLY_LIGHT_SWITCH": _sx_lamps(head=False, park=True),
        "OFF_LIGHT_SWITCH": _sx_lamps(head=False, park=False), "AUTO_LIGHT_SWITCH": _sx_lamps(head=False, park=False),
    }.get(v, {})),
    **{"MCU_" + n: ("level", _sx_thc) for n in (
        "OffBtnState_Flag", "AutoBtnState_Flag", "AconBtnState_Flag", "AconMnl_Flag", "BlowerMotorMnlSet_Seg",
        "BlowerMotorMnl_Flag", "ModeActMnlSet_Flag", "ModeActMnl_Flag", "LeftTempSetpoint_DegC",
        "RightTempSetpoint_DegC", "IntakeBtnState_Flag", "IntakeActMnl_Flag", "DefogBtnState_Flag", "DualBtnState_Flag", "HtronBtnState_Flag")},
    "MCU_frontFogSwitch": ("level", lambda v, cur: _sx_pair("BC_fogLight%sStatus", ("FL", "FR"), v == "ON")),
    # the front body controller's wiper defroster, for MCU_frontWiperDefrostReq. The climate panel's
    # wiper heater button (GUI_wiperHeatRequest) toggles without it; QtCar didn't send the request
    # in the sandbox (parked, climate on)
    "MCU_frontWiperDefrostReq": ("level", lambda v, cur: {"BCFRONT_wiperDefrostStatus": "ON" if v == "ON" else "OFF"}),
    # Charging > Stop/Start Charging; the charge current and limit are read by ChargeModelSX
    "MCU_requestChargeEnable": ("level", _sx_charge_enable),
    "MCU_requestLineChargeCurrent": ("level", lambda v, cur: {}),
    "MCU_chargeTerminationPct": ("level", lambda v, cur: {}),
    "MCU_rearFogSwitch": ("level", lambda v, cur: _sx_pair("BC_fogLight%sStatus", ("RL", "RR"), v == "ON")),
}


def decode_signal(frame, s):
    raw = sum(((frame[b // 8] >> (b % 8)) & 1) << k for k, b in enumerate(s["bits"]))
    if s.get("signed") and raw >> (len(s["bits"]) - 1) & 1:
        raw -= 1 << len(s["bits"])
    return s.get("enum", {}).get(str(raw)) or ("%g" % (raw * s["scale"] + s.get("offset", 0)))


class ChargeModel:
    """The battery fills while charging. The simulator keeps its battery level where it is, so
    the charging screen said "+0 mi" (GUI_chargeSessionIdealEnergyAdded compares the energy
    with the session's start). While SIM_chargeState is Charging, SIM_batteryLevel rises by what
    the charge current (BMS_packCurrent, positive = into the pack) delivers at PACK_V into
    PACK_KWH, and the time to full follows; at 100 % the state becomes Complete. The level starts
    from what QtCarVehicle shows."""
    PACK_V, PACK_KWH, EVERY = 360.0, 75.0, 5.0

    def __init__(self, snd):
        self.snd, self.last, self.level = snd, None, None

    def tick(self, now):
        if self.last is not None and now - self.last < self.EVERY:
            return
        dt, self.last = (now - self.last if self.last is not None else 0.0), now
        sim = self.snd.sim.values
        if sim.get("SIM_chargeState") != "Charging":
            self.level = None
            return
        if self.level is None:           # a new session: from the set level, else the car's
            try:
                self.level = float(sim.get("SIM_batteryLevel") or dv_request(4030, "_data_get_value_request_?name=VAPI_batteryLevel", 0.5))
            except (TypeError, ValueError):
                return
            return
        amps = max(0.0, self.snd.values.get("BMS_packCurrent", 0.0))
        self.level = min(100.0, self.level + amps * self.PACK_V * dt / 3600 / 1000 / self.PACK_KWH * 100)
        self.snd.sim.set("SIM_batteryLevel", "%.2f" % self.level)
        if amps > 0:                     # "Time Remaining" (hours) at this rate
            hours = (100 - self.level) / 100 * self.PACK_KWH / (amps * self.PACK_V / 1000)
            self.snd.sim.set("SIM_chargeTimeToFull", "%.2f" % hours)
        if self.level >= 100:
            for k, v in PRESETS["charge-complete"].items():
                self.snd.set(k, v)
            print("car: battery full, charge complete", file=sys.stderr, flush=True)


class ChargeModelSX:
    """The Model S/X charging (no simulator): while GTW_chargeState is CHARGING the charger draws the
    UI's charge current (MCU_requestLineChargeCurrent) up to the station's pilot (CHG_pilotCurrent)
    at CHG_inputVoltage, the battery (X_batteryLevel) fills with that power into the pack's usable
    81 kWh, the time to full follows, and at the UI's charge limit (MCU_chargeTerminationPct) the
    state becomes COMPLETE. Signals and the values they drive: vehicle/README.md."""
    KWH, EFF, EVERY = 81.0, 0.92, 5.0
    CHARGING = 3.0

    def __init__(self, snd, requests):
        self.snd, self.req, self.last, self.on = snd, requests, None, False

    def request(self, name, default, lo, hi):
        try:
            v = float(self.req.get(name, default))
        except ValueError:                      # SNA
            return default
        return v if lo <= v <= hi else default

    def tick(self, now):
        if self.last is not None and now - self.last < self.EVERY:
            return
        dt, self.last = (now - self.last if self.last is not None else 0.0), now
        set_ = self.snd.set
        if self.snd.values.get("GTW_chargeState") != self.CHARGING:
            if self.on:
                self.stop()
            return
        self.on = True
        pilot = self.snd.values.get("CHG_pilotCurrent") or 32.0
        amps = min(pilot, self.request("MCU_requestLineChargeCurrent", pilot, 1, 254))
        kw = amps * (self.snd.values.get("CHG_inputVoltage") or 240.0) / 1000
        limit = self.request("MCU_chargeTerminationPct", 90.0, 50, 100)
        level = min(100.0, float(self.snd.virtual.get("X_batteryLevel", 50)) + kw * self.EFF * dt / 3600 / self.KWH * 100)
        pack_v = 350 + 0.5 * level
        # the UI's current range (GUI_chargeCurrentRequestMax) is the available line current
        for k, x in (("X_batteryLevel", "%.3f" % level), ("CHG_lineCurrent", amps), ("CHG_maxLineCurrent", pilot),
                     ("BMS_chgLineCurrentAvail", pilot), ("CHG_inputPowerUI", round(kw)), ("BMS_packVoltage", pack_v),
                     ("BMS_packCurrent", kw * self.EFF * 1000 / pack_v),
                     ("BMS_chgTimeToFull", min(68, max(0.0, limit - level) / 100 * self.KWH / (kw * self.EFF)) if kw else 0)):
            set_(k, x)
        if level >= limit:
            for k, x in PRESETS["charge-complete"].items():
                set_(k, x)
            self.stop()
            print("car: charge limit %g%% reached, charge complete" % limit, file=sys.stderr, flush=True)

    def stop(self):
        self.on = False
        for k in ("CHG_lineCurrent", "CHG_inputPowerUI", "BMS_packCurrent", "BMS_chgTimeToFull"):
            self.snd.set(k, 0)


class Responder:
    """Listens to QtCarVehicle's outgoing frames (TX_PORT): records them for the panel and, with
    respond, answers the UI requests in RESPONSES."""

    def __init__(self, snd, respond=True):
        self.snd = snd
        self.respond = respond
        self.last = {}
        self.watch = {}          # CAN id -> [(signal name, signal)]
        for name in RESPONSES:
            if name not in snd.sigs:        # the Model 3's signals, not all in another car's database
                continue
            mname, s = snd.sigs[name]
            self.watch.setdefault(snd.db[mname]["id"], []).append((name, s))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.bind(("", TX_PORT))
        self.sock.setblocking(False)

    def poll(self):
        while True:
            try:
                data = self.sock.recv(65536)
            except BlockingIOError:
                return
            if HEADER == 2 and len(data) != 10 or len(data) <= HEADER:
                continue
            cid = int.from_bytes(data[:HEADER], "big")
            if HEADER == 2:
                cid &= 0x7ff
            payload = data[HEADER:].ljust(8, b"\0")
            self.snd.record(cid, payload, "qtcar", time.monotonic())
            for name, s in self.watch.get(cid, ()) if self.respond else ():
                v = decode_signal(payload, s)
                prev = self.last.get(name)
                self.last[name] = v
                if prev is None and RESPONSES[name][0] == "edge" or v == prev:
                    continue
                kind, fn = RESPONSES[name]
                if kind == "edge" and v in ("0", "IDLE", "NONE"):
                    continue
                cur = dict(self.snd.values, **self.snd.sim.values)
                cur.update(("req:" + k, x) for k, x in self.last.items())
                values = fn(v, cur)
                for k, val in values.items():
                    self.snd.set(k, val)
                if values:
                    print("car: %s=%s -> %s" % (name, v, " ".join("%s=%s" % kv for kv in values.items())),
                          file=sys.stderr, flush=True)


def load_db(path):
    global HEADER, TX_PORT, CAR
    if not os.path.exists(path):
        sys.exit("no signal database at %s\nmake it with: vehicle/extract-can-db.py <rootfs>" % path)
    db = json.load(open(path))
    if any("busname" in m for m in db.values()):
        # the S/X 2026 firmware: all buses are in the database, but QtCarVehicle only reads the
        # gateway's ETH stream (the other tables are for the diagnostic CAN viewer)
        db = {n: m for n, m in db.items() if m.get("busname") == "ETH" and "@" not in n}
        HEADER, TX_PORT = 4, 20100
        PRESETS.clear()
        PRESETS.update(PRESETS_SX)
        CONTROLS[:] = CONTROLS_SX
        PANEL_VALUES[:] = PANEL_VALUES_SX
        VIRTUAL.update(VIRTUAL_SX)
        CAR = "Model S/X"
    # signals the extractor found no bit layout for (a few in the 2026 firmware) can't be sent
    for m in db.values():
        m["signals"] = {n: s for n, s in m["signals"].items() if "bits" in s and "base" in s}
    sig = {}
    for mname, m in db.items():
        for sname, s in m["signals"].items():
            sig[sname] = (mname, s)
    return db, sig


def parse_value(s, text):
    enum = s.get("enum", {})
    for k, label in enum.items():
        if label.lower() == text.lower():
            return float(k)
    try:
        return float(text)
    except ValueError:
        sys.exit("bad value %r (enum: %s)" % (text, ", ".join(enum.values()) or "none"))


def encode_into(frame, s, value):
    bits = s["bits"]
    raw = int(round((value - s.get("offset", 0)) / s["scale"])) if s["scale"] else 0
    n = len(bits)
    if s.get("signed"):
        raw = max(-(1 << (n - 1)), min((1 << (n - 1)) - 1, raw)) & ((1 << n) - 1)
    else:
        raw = max(0, min((1 << n) - 1, raw))
    for k, bit in enumerate(bits):
        if raw >> k & 1:
            frame[bit // 8] |= 1 << (bit % 8)
        else:
            frame[bit // 8] &= ~(1 << (bit % 8))


def dv_request(port, query, timeout=2.0):
    """GET a QtCar-framework data value URL (the server keeps the connection open: read by
    Content-Length). Returns the body or None."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as c:
            c.sendall(("GET /%s HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n" % query).encode())
            data = b""
            while True:
                head, sep, body = data.partition(b"\r\n\r\n")
                if sep:
                    n = [int(l.split(b":")[1]) for l in head.split(b"\r\n") if l.lower().startswith(b"content-length:")]
                    if n and len(body) >= n[0]:
                        break
                chunk = c.recv(4096)
                if not chunk:
                    break
                data += chunk
        return data.partition(b"\r\n\r\n")[2].decode(errors="replace").strip()
    except OSError:
        return None


class DvConnection:
    """Data values over one kept-open connection (HTTP/1.1, one request after the other). The
    framework's server keeps a thread and a socket for every connection for minutes after it was
    used: with a connection per value the panel's polling (~25 values every 2 s) took the 2026
    S/X QtCarVehicle past 1024 descriptors, and its select() aborted it ("bit out of range 0 -
    FD_SETSIZE on fd_set")."""

    def __init__(self, port, timeout=1.0):
        self.port, self.timeout = port, timeout
        self.sock, self.buf = None, b""
        self.lock = threading.Lock()

    def get(self, name):
        with self.lock:
            for _ in range(2):          # a connection the server dropped: once more on a new one
                try:
                    if self.sock is None:
                        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=self.timeout)
                        self.buf = b""
                    self.sock.sendall(("GET /_data_get_value_request_?name=%s HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
                                       % urllib.parse.quote(name)).encode())
                    while True:
                        head, sep, rest = self.buf.partition(b"\r\n\r\n")
                        if sep:
                            n = [int(l.split(b":")[1]) for l in head.split(b"\r\n") if l.lower().startswith(b"content-length:")]
                            if n and len(rest) >= n[0]:
                                self.buf = rest[n[0]:]
                                return rest[:n[0]].decode(errors="replace").strip()
                        chunk = self.sock.recv(4096)
                        if not chunk:
                            raise ConnectionResetError
                        self.buf += chunk
                except OSError:
                    if self.sock:
                        self.sock.close()
                    self.sock = None
            return None


def keep_vehicle_values(values, every=10):
    """Data values of QtCarVehicle (:4030) the S/X car would have and the simulation doesn't build
    up, kept set (QtCarVehicle restarts now and then). GUI_canPowerConvenienceFeatures: SoCMonitor
    derives it from a battery history (VAPI_socDataPoints); without it climate keeper, dog and
    camp mode say "battery must be at least 20% charged"."""
    def run():
        while True:
            for k, v in values.items():
                dv_request(4030, "_data_set_value_request_?name=%s&value=%s" % (k, v), 1)
            time.sleep(every)
    threading.Thread(target=run, daemon=True).start()


class SimValues:
    """SIM_* data values of QtCarSimService: sent when they change, and all of them again every
    few seconds (the sim may start later or restart)."""
    PORT = 4190

    def __init__(self):
        self.values = {}
        self.dirty = set()
        self.event = threading.Event()
        threading.Thread(target=self.run, daemon=True).start()

    def set(self, name, value):
        if self.values.get(name) != value:
            self.values[name] = value
            self.dirty.add(name)
            self.event.set()

    def pop(self, name):
        self.values.pop(name, None)

    def run(self):
        while True:
            changed = self.event.wait(3)
            self.event.clear()
            names = list(self.dirty) if changed else list(self.values)
            self.dirty.clear()
            for k in names:
                if k in self.values:
                    dv_request(self.PORT, "_data_set_value_request_?name=%s&value=%s" % (k, urllib.parse.quote(str(self.values[k]))))


class Sender:
    def __init__(self, db, sigs, target, yield_=True, sim_port=None):
        self.db, self.sigs = db, sigs
        self.values = {}         # signal -> value
        self.virtual = {}        # VIRTUAL name -> the value it was set to (for the panel)
        self.lock = threading.Lock()
        self.counters = {}
        self.next_due = {}
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("", 0))
        self.own_port = self.sock.getsockname()[1]
        self.target = target
        self.sim = SimValues()
        self.foreign = {}        # CAN id -> last time another sender sent it
        self.by_id = {m["id"]: n for n, m in db.items()}
        self.selectors = {}
        self.sim_ids = {}        # CAN id -> last time it came from the sim (--sim-port)
        self.seen = {}           # CAN id -> the last frames on the wire (record(), for the panel)
        self.muxinfo = {}
        self.simsock = None      # --sim-port: QtCarSimService's frames come in here and are forwarded
        if sim_port:
            self.simsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.simsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.simsock.bind(("", sim_port))
            self.simsock.setblocking(False)
        self.listen = None
        if yield_:
            try:
                self.listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                self.listen.bind(("", target[1]))
                self.listen.setblocking(False)
            except OSError as e:
                print("can't listen on port %d (%s), not yielding to other senders" % (target[1], e), file=sys.stderr)
                self.listen = None

    def watch(self, now):
        """Note CAN ids other senders are sending."""
        while self.listen:
            try:
                data, addr = self.listen.recvfrom(65536)
            except BlockingIOError:
                return
            if addr[1] != self.own_port and len(data) >= HEADER:
                cid = int.from_bytes(data[:HEADER], "big")
                self.record(cid, data[HEADER:], "other", now)
                if cid not in self.foreign or now - self.foreign[cid] > 2:
                    name = next((n for n, m in self.db.items() if m["id"] == cid), "?")
                    if any(self.sigs[v][0] == name for v in self.values):
                        print("0x%03x %s is sent by someone else now, pausing it" % (cid, name), file=sys.stderr, flush=True)
                self.foreign[cid] = now

    def _set_locked(self, values):
        with self.lock:
            for k, v in values.items():
                self.set(k, v)

    def set(self, name, text):
        if name in VIRTUAL:
            values = VIRTUAL[name](text)
            if "_later" in values:
                delay, later = values.pop("_later")
                threading.Timer(delay, self._set_locked, (later,)).start()
            for k, v in values.items():
                self.set(k, v)
            self.virtual[name] = str(text)
            return
        if name.startswith("SIM_"):
            self.sim.set(name, str(text).lower() if str(text).lower() in ("true", "false") else str(text))
            if name.startswith("SIM_tpmsPressure"):
                self.tpms_warning(name[-2:], float(text))
            return
        if name not in self.sigs:
            raise KeyError("unknown signal %s (try --find)" % name)
        self.values[name] = parse_value(self.sigs[name][1], str(text))

    def tpms_warning(self, wheel, bar):
        """The TPMS module's soft warning for a wheel (and the global one) from its pressure."""
        self.values["TPMS_softWarning" + wheel] = float(bar < TPMS_WARN_BAR)
        self.values["TPMS_c_globalSoftWarning"] = float(any(self.values.get("TPMS_softWarning" + w) == 1
                                                            for w in ("FL", "FR", "RL", "RR")))

    def checksum(self, m, base, frame):
        """Fill in the checksum byte, if the message has one (VehicleUtils::calculateChecksum)."""
        for sname, s in m["signals"].items():
            if (s["base"] == base or s["base"] == "00" * 8) and sname.endswith("Checksum") \
                    and len(s["bits"]) == 8 and s["bits"][0] % 8 == 0:
                idx = s["bits"][0] // 8
                cid = m["id"]
                frame[idx] = ((cid & 0xff) + (cid >> 8) + sum(b for i, b in enumerate(frame[:m["dlc"]]) if i != idx)) & 0xff
                return

    def selector_bits(self, mname):
        """Bits of a multiplexed message that select the branch (empty if not multiplexed)."""
        if mname not in self.selectors:
            sigs = self.db[mname]["signals"].values()
            zero = "00" * 8
            setbits = {b for s in sigs for b in range(64) if bytes.fromhex(s["base"])[b // 8] >> (b % 8) & 1}
            self.selectors[mname] = sorted({b for s in sigs if s["base"] == zero and set(s["bits"]) & setbits
                                            for b in s["bits"]})
        return self.selectors[mname]

    def mux(self, mname):
        """(selector bits, signals common to all branches) of a message; for unmultiplexed ones
        ([], all signals)."""
        if mname not in self.muxinfo:
            sel = self.selector_bits(mname)
            sigs = self.db[mname]["signals"]
            zero = "00" * 8
            branch_bits = {b for s in sigs.values() if s["base"] != zero for b in s["bits"]}
            # a zero-base signal is branch 0's unless it doesn't overlap any branch's bits
            common = {n for n, s in sigs.items() if not sel or s["base"] == zero and not set(s["bits"]) & branch_bits}
            self.muxinfo[mname] = (sel, common)
        return self.muxinfo[mname]

    @staticmethod
    def branch(sel, data):
        """The multiplexer value of a frame (0 if not multiplexed)."""
        return sum((data[b // 8] >> (b % 8) & 1) << k for k, b in enumerate(sel) if b // 8 < len(data))

    def record(self, cid, data, src, now):
        """Remember the last frame of each CAN id (per multiplexer branch), who sent it and when."""
        e = self.seen.get(cid)
        if e is None:
            e = self.seen[cid] = {"frames": {}, "times": collections.deque(maxlen=20)}
        data = bytes(data).ljust(8, b"\0")
        e.update(src=src, t=now, last=data)
        e["times"].append(now)
        mname = self.by_id.get(cid)
        e["frames"][self.branch(self.mux(mname)[0], data) if mname else 0] = data

    def wire(self, ids, now):
        """For the panel: every CAN id seen [age s, frames/s, sender], and for the given ids the
        frames (hex, per branch) and their decoded signals."""
        wire, msgs = {}, {}
        for cid, e in self.seen.items():
            ts = e["times"]
            dt = ts[-1] - ts[0]
            wire[cid] = [round(now - e["t"], 1), round((len(ts) - 1) / dt, 1) if dt > 0 else 0, e["src"]]
        for cid in ids:
            e, mname = self.seen.get(cid), self.by_id.get(cid)
            if not e or not mname:
                continue
            sel, common = self.mux(mname)
            values = {}
            for sname, s in self.db[mname]["signals"].items():
                f = e["last"] if sname in common else e["frames"].get(self.branch(sel, bytes.fromhex(s["base"])))
                if f is not None:
                    values[sname] = decode_signal(f, s)
            msgs[cid] = {"frames": {k: f.hex() for k, f in sorted(e["frames"].items())}, "values": values}
        return {"wire": wire, "msgs": msgs}

    def compact_db(self):
        """The signal database for the panel: messages sorted by id, signals with their range."""
        out = []
        for mname, m in sorted(self.db.items(), key=lambda x: x[1]["id"]):
            sel, common = self.mux(mname)
            sigs = []
            for sname, s in m["signals"].items():
                n = len(s["bits"])
                lo, hi = (-(1 << (n - 1)), (1 << (n - 1)) - 1) if s.get("signed") else (0, (1 << n) - 1)
                off = s.get("offset", 0)
                d = {"n": sname, "len": n, "min": lo * s["scale"] + off, "max": hi * s["scale"] + off, "scale": s["scale"]}
                if s.get("unit"):
                    d["u"] = s["unit"]
                if s.get("enum"):
                    d["e"] = s["enum"]
                if sname not in common:
                    d["m"] = self.branch(sel, bytes.fromhex(s["base"]))
                sigs.append(d)
            out.append({"id": m["id"], "n": mname, "period": m["period_ms"], "dlc": m["dlc"],
                        "sim": m["id"] in SIM_IDS, "mux": bool(sel), "sigs": sigs})
        return out

    def merge(self, now):
        """Forward QtCarSimService's frames (--sim-port) to the target, with the signals set here
        written over the sim's values."""
        while self.simsock:
            try:
                data, addr = self.simsock.recvfrom(65536)
            except BlockingIOError:
                return
            if len(data) < 3:
                continue
            cid = int.from_bytes(data[:2], "big")
            self.sim_ids[cid] = now
            mname = self.by_id.get(cid)
            if mname:
                frame = bytearray(data[2:].ljust(8, b"\0"))
                sel = self.selector_bits(mname)
                hit = False
                for name, v in self.values.items():
                    mn, s = self.sigs[name]
                    if mn != mname:
                        continue
                    base = bytes.fromhex(s["base"])
                    if all((frame[b // 8] ^ base[b // 8]) >> (b % 8) & 1 == 0 for b in sel):
                        encode_into(frame, s, v)
                        hit = True
                if hit:
                    self.checksum(self.db[mname], None, frame)
                    data = data[:2] + bytes(frame[:len(data) - 2])
            self.sock.sendto(data, self.target)
            self.record(cid, data[2:], "sim", now)

    def frames(self, now):
        """(key, message, bytes) for each message branch with signals set that is due now."""
        groups = {}
        for name, v in self.values.items():
            mname, s = self.sigs[name]
            groups.setdefault((mname, s["base"]), []).append((s, v))
        for (mname, base), items in groups.items():
            key = (mname, base)
            cid = self.db[mname]["id"]
            if now < self.next_due.get(key, 0) or now - self.foreign.get(cid, -9) < 1 or now - self.sim_ids.get(cid, -9) < 1:
                continue    # someone else sends it, or the sim does and merge() writes our values into its frames
            period = self.db[mname]["period_ms"] or 1000
            self.next_due[key] = now + min(max(period, 20), 1000) / 1000.0
            m = self.db[mname]
            frame = bytearray.fromhex(base)
            for s, v in items:
                encode_into(frame, s, v)
            cnt = next((s for sname, s in m["signals"].items() if (s["base"] == base or s["base"] == "00" * 8)
                        and sname.endswith("Counter") and sname not in self.values), None)
            if cnt:
                c = self.counters.get(key, 0)
                encode_into(frame, cnt, c)
                self.counters[key] = (c + 1) % (1 << len(cnt["bits"]))
            self.checksum(m, base, frame)
            yield key, m, bytes(frame)

    def tick(self, now):
        self.watch(now)
        self.merge(now)
        for key, m, frame in self.frames(now):
            self.sock.sendto(m["id"].to_bytes(HEADER, "big") + frame[:m["dlc"] if HEADER == 4 else 8], self.target)
            self.record(m["id"], frame, "set", now)


def run_info(log_dir):
    """Services and helpers of the ./tesla run that writes to log_dir (tools/logsummary.py)."""
    if not log_dir:
        return None
    try:
        sys.path.insert(0, os.path.join(HERE, "..", "tools"))
        import logsummary
        services, helpers = logsummary.service_status(log_dir)
    except Exception as e:    # the panel works without it
        return {"error": str(e)}
    logs = sorted((f, os.path.getsize(os.path.join(log_dir, f))) for f in os.listdir(log_dir)
                  if f.endswith((".log", ".err", ".out")) or f == "config")
    return {"dir": os.path.relpath(log_dir, os.path.join(HERE, "..")), "services": services, "helpers": helpers,
            "logs": logs}


def log_text(log_dir, name, full):
    """A log file of the run as text: the last 400 lines unless full. None if there's no such file."""
    if not log_dir or name != os.path.basename(name) or name.startswith("."):
        return None
    if name == "summary":
        r = subprocess.run([sys.executable, os.path.join(HERE, "..", "tools", "logsummary.py"), log_dir],
                           capture_output=True, text=True, timeout=30)
        return (r.stdout + r.stderr).encode()
    path = os.path.join(log_dir, name)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        data = f.read()
    if not full:
        lines = data.splitlines(keepends=True)
        if len(lines) > 400:
            data = b"[... %d earlier lines; ?all=1 for everything ...]\n" % (len(lines) - 400) + b"".join(lines[-400:])
    return data


def serve_panel(snd, port, bind="127.0.0.1", log_dir=None):
    """Web page to change values at runtime: vehicle/panel.html + a small JSON API."""
    sigs = snd.sigs
    live_cache = {}
    vehicle = DvConnection(4030)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")    # an old page would send old controls
            if len(data) > 4096 and "gzip" in self.headers.get("Accept-Encoding", ""):
                data = gzip.compress(data, 5)
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def state(self):
            with snd.lock:
                values = {k: fmt(sigs, k, v).split("=", 1)[1] for k, v in snd.values.items()}
                values.update(snd.sim.values)
                values.update(snd.virtual)
            # QtCarVehicle sometimes takes a while to answer (busy with the frames): keep the last value
            for n in PANEL_VALUES:
                v = vehicle.get(n)
                if v is not None or n not in live_cache:
                    live_cache[n] = v
            live = {n: live_cache[n] for n in PANEL_VALUES}
            return {"values": values, "live": live, "presets": {k: v for k, v in PRESETS.items()},
                    "owners": dict(snd.owner), "run": run_info(log_dir), "car": CAR, "controls": CONTROLS,
                    "arcade": "wheel" if CAR == "Model 3" else "scroll"}

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self.reply(200, open(os.path.join(HERE, "panel.html"), "rb").read(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self.reply(200, self.state())
            elif self.path == "/api/db":
                with snd.lock:
                    self.reply(200, snd.compact_db())
            elif self.path.startswith("/api/can"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                ids = [int(i, 0) for i in ",".join(q.get("ids", [])).split(",") if i.strip()]
                with snd.lock:
                    self.reply(200, snd.wire(ids, time.monotonic()))
            elif self.path.startswith("/logs/"):
                name, _, query = self.path[len("/logs/"):].partition("?")
                data = log_text(log_dir, urllib.parse.unquote(name), "all=1" in query)
                if data is None:
                    self.reply(404, {"error": "no such log"})
                else:
                    self.reply(200, data, "text/plain; charset=utf-8")
            else:
                self.reply(404, {"error": "not found"})

        def do_POST(self):
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                values = dict(PRESETS[req["preset"]]) if req.get("preset") else {}
                values.update(req.get("values", {}))
                with snd.lock:
                    for k, v in values.items():
                        snd.set(k, v)
                        snd.owner.pop(k, None)
                    for k in req.get("unset", []):
                        snd.values.pop(k, None)
                        snd.sim.pop(k)
                if req.get("quiet"):     # the game controls: many small changes, no log, no state
                    self.reply(200, {"ok": True})
                    return
                print("panel: " + " ".join("%s=%s" % kv for kv in values.items()) +
                      "".join(" -%s" % k for k in req.get("unset", [])), file=sys.stderr, flush=True)
                self.reply(200, self.state())
            except (KeyError, ValueError, SystemExit) as e:
                self.reply(400, {"error": str(e)})

    srv = http.server.ThreadingHTTPServer((bind, port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def control(cmd):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    s.sendto(json.dumps(cmd).encode(), CONTROL)
    try:
        return json.loads(s.recv(65536))
    except socket.timeout:
        sys.exit("no running tesla-can.py (control port %d)" % CONTROL[1])


def fmt(sigs, name, v):
    if name not in sigs:
        return "%s=%s" % (name, v)
    s = sigs[name][1]
    label = s.get("enum", {}).get(str(int(v))) if float(v).is_integer() else None
    return "%s=%s" % (name, label or ("%g" % v))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("assign", nargs="*", metavar="SIGNAL=VALUE")
    ap.add_argument("--preset", action="append", default=[], metavar="NAME",
                    help="a preset (--list-presets; the Model S/X database has its own)")
    ap.add_argument("--set", action="store_true", help="send SIGNAL=VALUE to the running instance")
    ap.add_argument("--unset", nargs="+", metavar="SIGNAL")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--list-presets", action="store_true")
    ap.add_argument("--respond", action="store_true",
                    help="answer QtCar's requests (frunk/trunk buttons, locks, light switch, ...: RESPONSES)")
    ap.add_argument("--http", type=int, metavar="PORT", help="web page to change values at runtime (vehicle/panel.html)")
    ap.add_argument("--http-bind", default="127.0.0.1", metavar="ADDR",
                    help="address for --http (default 127.0.0.1; 0.0.0.0 = reachable from the network)")
    ap.add_argument("--log-dir", help="the ./tesla run's log folder: the panel shows its services and logs")
    ap.add_argument("--find", metavar="TEXT")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--target", help="where QtCarVehicle listens (default 127.255.255.255:1234; the Model "
                                     "S/X 2026 firmware: :20101, its --udp in RunQtCar.env)")
    ap.add_argument("--no-yield", action="store_true", help="send even ids someone else is sending")
    ap.add_argument("--all-zero", action="store_true",
                    help="send every message the car's ECUs would send (all but UI_*/MCU_*, the MCU's own), "
                         "each signal 0 unless set otherwise (to see what QtCarVehicle needs)")
    ap.add_argument("--sim-port", type=lambda s: int(s) if s else None, metavar="PORT",
                    help="QtCarSimService sends to this port (its --udp :PORT): forward its frames, with "
                         "the signals set here written over its values")
    args = ap.parse_args()

    if (args.set or args.status) and args.db == DB_PATH:
        # the running instance's database (the S/X has other presets and signals)
        args.db = control({"cmd": "info"}).get("db") or args.db
    db, sigs = load_db(args.db) if args.list_presets and os.path.exists(args.db) else (None, None)
    if args.list_presets:
        for name, values in PRESETS.items():
            print("%-16s %s" % (name, " ".join("%s=%s" % kv for kv in values.items())))
        return
    db, sigs = load_db(args.db)
    for p in args.preset:
        if p not in PRESETS:
            ap.error("no preset %s for this database (%s)" % (p, ", ".join(sorted(PRESETS))))
    if not args.target:
        args.target = "127.255.255.255:%d" % (20101 if HEADER == 4 else 1234)
    if args.find:
        t = args.find.lower()
        for name, (mname, s) in sorted(sigs.items(), key=lambda x: (db[x[1][0]]["id"], x[0])):
            if t in name.lower() or t in mname.lower():
                m = db[mname]
                enum = " [%s]" % " ".join("%s=%s" % kv for kv in s.get("enum", {}).items()) if s.get("enum") else ""
                print("0x%03x %-24s %-36s %s%s%s" % (m["id"], mname + ("*" if HEADER == 2 and m["id"] in SIM_IDS else ""), name,
                                                     s.get("unit", ""), " scale %g" % s["scale"] if s["scale"] != 1 else "", enum))
        print("(* = QtCarSimService sends this message)", file=sys.stderr)
        return
    if args.status:
        r = control({"cmd": "status"})
        for k, v in sorted(r["values"].items()):
            print(fmt(sigs, k, v))
        return
    if args.unset:
        print(control({"cmd": "unset", "names": args.unset}).get("error", "ok"))
        return
    pairs = [a.split("=", 1) for a in args.assign]
    if any(len(p) != 2 for p in pairs):
        ap.error("arguments are SIGNAL=VALUE")
    if args.set:
        values = {k: v for p in args.preset for k, v in PRESETS[p].items()}
        values.update(pairs)
        r = control({"cmd": "set", "values": values})
        print(r.get("error", "ok"))
        return

    host, port = args.target.rsplit(":", 1)
    snd = Sender(db, sigs, (host, int(port)), not args.no_yield, args.sim_port)
    if args.all_zero:
        for sname, (mname, s) in sigs.items():
            if not mname.startswith(("UI_", "MCU_")) and s.get("bits"):
                snd.values[sname] = 0.0
    for p in args.preset:
        for k, v in PRESETS[p].items():
            snd.set(k, v)
    for k, v in pairs:
        try:
            snd.set(k, v)
        except KeyError as e:
            sys.exit(str(e))
    ctl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ctl.bind(CONTROL)
    ctl.setblocking(False)
    print("sending: " + " ".join(fmt(sigs, k, v) for k, v in sorted(dict(snd.values, **snd.sim.values).items())),
          file=sys.stderr, flush=True)
    snd.owner = {}
    if args.http:
        serve_panel(snd, args.http, args.http_bind, args.log_dir)
    # QtCar's outgoing frames: answered with --respond, shown on the panel
    if HEADER == 4:     # the S/X database
        keep_vehicle_values({"GUI_canPowerConvenienceFeatures": "true"})
    responder = Responder(snd, args.respond) if args.respond or args.http else None
    charge = ChargeModelSX(snd, responder.last if responder else {}) if HEADER == 4 else ChargeModel(snd)
    try:
        while True:
            with snd.lock:
                snd.tick(time.monotonic())
                charge.tick(time.monotonic())
                if responder:
                    responder.poll()
            try:
                data, addr = ctl.recvfrom(65536)
                cmd = json.loads(data)
                reply = {}
                try:
                    snd.lock.acquire()
                    if cmd["cmd"] == "set":
                        before = dict(snd.values, **snd.sim.values)
                        items = list(cmd["values"].items())
                        for k, v in items:
                            if cmd.get("source") == "gps" and HEADER == 4:
                                # tesla-gps.py --can speaks the Model 3's signals: on the S/X
                                # skip the simulator's and those this car hasn't; its
                                # DI_vehicleSpeed (DI_torque2) is in mph, the speedometer
                                # is DI_analogSpeed
                                if k.startswith("SIM_") or k not in snd.sigs:
                                    continue
                                if k == "DI_vehicleSpeed":
                                    v = round(float(v) / 1.609344, 2)
                                    items.append(("DI_analogSpeed", v))
                            snd.set(k, v)
                            # who set it last (tesla-gps.py --can: "gps"), shown on the panel
                            if cmd.get("source"):
                                snd.owner[k] = cmd["source"]
                            else:
                                snd.owner.pop(k, None)
                        after = dict(snd.values, **snd.sim.values)
                        changed = [k for k in cmd["values"] if before.get(k) != after.get(k)]
                        if changed:
                            print("set: " + " ".join(fmt(sigs, k, after.get(k)) for k in changed), file=sys.stderr, flush=True)
                    elif cmd["cmd"] == "info":
                        reply["db"] = os.path.abspath(args.db)
                    elif cmd["cmd"] == "unset":
                        for k in cmd["names"]:
                            snd.values.pop(k, None)
                            snd.sim.pop(k)
                    allv = dict(snd.values, **snd.sim.values)
                    # a set gets back what it set; everything (--status) may not fit a datagram
                    # (--all-zero: thousands of signals)
                    keys = list(cmd["values"]) if cmd["cmd"] == "set" else list(allv)
                    if cmd["cmd"] != "info":
                        reply["values"] = {k: allv.get(k) for k in keys}
                except (KeyError, SystemExit) as e:
                    reply["error"] = str(e)
                finally:
                    snd.lock.release()
                data = json.dumps(reply).encode()
                while len(data) > 60000 and reply.get("values"):
                    keep = list(reply["values"])[:len(reply["values"]) // 2]
                    reply["values"] = {k: reply["values"][k] for k in keep}
                    reply["truncated"] = True
                    data = json.dumps(reply).encode()
                ctl.sendto(data, addr)
            except BlockingIOError:
                pass
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
