-- Runs the mod's fillStruct against stubs of the game's globals (for test_bridge.py, with luajit;
-- the game itself never loads this). Prints the packet as hex, and the address it would go to.
local ffi = require("ffi")
electrics = {values = {
  watertemp = 90, fuel = 0.5, engineRunning = 1, highbeam = 1, fog = 0, signal_left_input = 1, signal_right_input = 0,
  hazard_enabled = 0, parkingbrake = 0, absActive = true, escActive = false, tcsActive = 0, wheelspeed = 20,
  steering = 45, throttle = 0.25, brake = 0, rpm = 3000, odometer = 1234, gear = "D", gearIndex = 3, ignitionLevel = 2,
  lights_state = 1, door_L_coupler_notAttached = 1, door_FR_coupler_notAttached = 0, tailgateCoupler_notAttached = 1,
}}
settings = {getValue = function(name) return name == "protocols_outgauge_address" and "10.0.0.7" or nil end}
obj = {
  getPositionXYZ = function() return 1, 2, 3 end,
  getVelocityXYZ = function() return 4, 5, 6 end,
  getDirectionVector = function() return {x = 0.5, y = -0.5} end,
  getEnvTemperature = function() return 293.15 end,
  getGroupPressure = function(_, g) return g + 101325 end,
}
v = {data = {pressureGroups = {pFL = 200000, pRR = 100000}}}
wheels = {wheelCount = 2, wheels = {[0] = {name = "FL", pressureGroup = "pFL"}, [1] = {name = "RR", pressureGroup = "pRR"}}}
powertrain = {getDevicesByCategory = function() return {{outputTorque1 = 100, outputAV1 = 500}, {outputTorque1 = 200, outputAV1 = 100}} end}
vehiclePath = "vehicles/etk800/"

local mod = dofile(arg[1])
mod.init()
ffi.cdef("typedef struct t {" .. mod.getStructDefinition() .. "} t;")
local o = ffi.new("t")
mod.fillStruct(o, 0.1)
local s = ffi.string(o, ffi.sizeof(o))
print((s:gsub(".", function(c) return string.format("%02x", c:byte()) end)))
print(mod.getAddress(), mod.getPort())
