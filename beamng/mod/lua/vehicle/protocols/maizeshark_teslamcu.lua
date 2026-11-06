-- BeamNG.drive -> Tesla MCU2 UI (https://github.com/MaizeShark/mcu2-desktop, beamng/README.md).
-- A UDP protocol module (https://go.beamng.com/protocols): sends the seated vehicle's state to
-- beamng/beamng-bridge.py, more than the stock OutGauge and MotionSim have.
-- Written after the game's lua/vehicle/protocols/outgauge.lua.
local M = {}

-- install-mod.py --target HOST puts the UI host's IP address here. Empty: the address of the
-- game's OutGauge setting (Options > Other > Protocols, default 127.0.0.1). Must be an IP address,
-- the game's udp:sendto() doesn't resolve names.
local FIXED_ADDRESS = ""

local TIRE_PERIOD = 0.5     -- seconds between readings of the tire pressures

-- bits of "flags"
local F_RUNNING, F_HIGHBEAM, F_FOG, F_SIGNAL_L, F_SIGNAL_R, F_HAZARD = 1, 2, 4, 8, 16, 32
local F_PARKBRAKE, F_ABS, F_ESC, F_TCS = 64, 128, 256, 512
local F_DOOR_FL, F_DOOR_FR, F_DOOR_RL, F_DOOR_RR, F_TRUNK, F_HOOD = 65536, 131072, 262144, 524288, 1048576, 2097152

-- advancedCouplerControl names in the vehicles: 4 doors, 2 doors, trunk or tailgate, hood
local COUPLERS = {
  {F_DOOR_FL, {"door_FL_coupler", "door_L_coupler"}},
  {F_DOOR_FR, {"door_FR_coupler", "door_R_coupler"}},
  {F_DOOR_RL, {"door_RL_coupler"}},
  {F_DOOR_RR, {"door_RR_coupler"}},
  {F_TRUNK, {"trunkCoupler", "tailgateCoupler"}},
  {F_HOOD, {"hoodLatchCoupler", "hoodLatchCouplerL", "hoodLatchCouplerR", "hoodCatchCoupler"}},
}

local engines
local tires = {FL = -1, FR = -1, RL = -1, RR = -1}
local tireTimer = 0

local function isOn(x)
  return x ~= nil and x ~= false and x ~= 0
end

local function init()
  engines = nil
  tireTimer = TIRE_PERIOD
  tires.FL, tires.FR, tires.RL, tires.RR = -1, -1, -1, -1
end

local function reset()
  init()
end

local function getAddress()
  return FIXED_ADDRESS ~= "" and FIXED_ADDRESS or settings.getValue("protocols_outgauge_address")
end
local function getPort()           return 4444 end
local function getMaxUpdateRate()  return 30 end
local function isPhysicsStepUsed() return false end

-- little endian, no padding: beamng/beamng-bridge.py's TMC1 format
local function getStructDefinition()
  return [[
    char           format[4];       // "TMC1"
    unsigned       flags;           // F_x
    float          posX, posY, posZ;  // world position, m
    float          velX, velY, velZ;  // m/s
    float          dirX, dirY;      // forward direction in the world
    float          speed;           // m/s
    float          steering;        // steering wheel, degrees; positive = left
    float          throttle;        // 0 to 1
    float          brake;           // 0 to 1
    float          rpm;
    float          fuel;            // 0 to 1, fuel or battery
    float          odometer;        // m
    float          envTemp;         // C
    float          waterTemp;       // C
    float          power;           // kW at the engines' or motors' outputs
    float          tireFL, tireFR, tireRL, tireRR;  // Pa over the atmosphere, -1 = unknown
    signed char    gearIndex;       // reverse < 0, neutral 0, forward > 0
    char           gear;            // first letter of the gear's name, 0 = none
    unsigned char  ignition;        // 0 off, 1 accessory, 2 on, 3 starting
    unsigned char  lights;          // 0 off, 1 low beam, 2 high beam
    char           vehicle[16];
  ]]
end

local function updateTires(dt)
  tireTimer = tireTimer + dt
  if tireTimer < TIRE_PERIOD then return end
  tireTimer = 0
  local groups = v.data.pressureGroups
  if not groups then return end
  for i = 0, wheels.wheelCount - 1 do
    local wd = wheels.wheels[i]
    if wd and tires[wd.name] and wd.pressureGroup and groups[wd.pressureGroup] then
      tires[wd.name] = math.max(obj:getGroupPressure(groups[wd.pressureGroup]) - 101325, 0)
    end
  end
end

local function drivePower()
  engines = engines or powertrain.getDevicesByCategory("engine")
  local watts = 0
  for _, d in ipairs(engines) do
    watts = watts + (d.outputTorque1 or 0) * (d.outputAV1 or 0)
  end
  return watts / 1000
end

local function fillStruct(o, dtSim)
  local e = electrics.values
  if not e.watertemp and not e.fuel then
    return -- vehicle not completely initialized: a zeroed packet goes out, the bridge drops it
  end

  local flags = 0
  if isOn(e.engineRunning) then flags = flags + F_RUNNING end
  if isOn(e.highbeam)      then flags = flags + F_HIGHBEAM end
  if isOn(e.fog)           then flags = flags + F_FOG end
  if isOn(e.signal_left_input)  then flags = flags + F_SIGNAL_L end
  if isOn(e.signal_right_input) then flags = flags + F_SIGNAL_R end
  if isOn(e.hazard_enabled) then flags = flags + F_HAZARD end
  if isOn(e.parkingbrake)  then flags = flags + F_PARKBRAKE end
  if isOn(e.absActive)     then flags = flags + F_ABS end
  if isOn(e.escActive)     then flags = flags + F_ESC end
  if isOn(e.tcsActive)     then flags = flags + F_TCS end
  for _, c in ipairs(COUPLERS) do
    for _, name in ipairs(c[2]) do
      if isOn(e[name .. "_notAttached"]) then
        flags = flags + c[1]
        break
      end
    end
  end

  updateTires(dtSim)

  o.format = "TMC1"
  o.flags = flags
  o.posX, o.posY, o.posZ = obj:getPositionXYZ()
  o.velX, o.velY, o.velZ = obj:getVelocityXYZ()
  local dir = obj:getDirectionVector()
  o.dirX, o.dirY = dir.x, dir.y
  o.speed = e.wheelspeed or e.airspeed or 0
  o.steering = e.steering or 0
  o.throttle = e.throttle or 0
  o.brake = e.brake or 0
  o.rpm = e.rpm or 0
  o.fuel = e.fuel or 0
  o.odometer = e.odometer or 0
  o.envTemp = obj:getEnvTemperature() - 273.15
  o.waterTemp = e.watertemp or 0
  o.power = drivePower()
  o.tireFL, o.tireFR, o.tireRL, o.tireRR = tires.FL, tires.FR, tires.RL, tires.RR
  o.gearIndex = math.max(-127, math.min(127, e.gearIndex or 0))
  o.gear = type(e.gear) == "string" and e.gear:byte(1) or 0
  o.ignition = e.ignitionLevel or 0
  o.lights = e.lights_state or 0
  o.vehicle = (vehiclePath and vehiclePath:match("vehicles/([^/]+)") or ""):sub(1, 15)
end

M.init = init
M.reset = reset
M.getAddress = getAddress
M.getPort = getPort
M.getMaxUpdateRate = getMaxUpdateRate
M.getStructDefinition = getStructDefinition
M.fillStruct = fillStruct
M.isPhysicsStepUsed = isPhysicsStepUsed

return M
