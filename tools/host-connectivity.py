#!/usr/bin/env python3
"""Feed the host's internet reachability into the Model S/X QtCar data values.

--set NAME=VARIANT keeps more CenterDisplay data values at a value (dbus-send variant syntax),
for what the car's hardware would report, e.g. GUI_audioReady=variant:boolean:true: QtCar sets
it from the car's audio DSP (AUDIO_dspState), which doesn't run here; without it the arcade and
the fireplace stay at "Audio Initializing". --copy VAPI_NAME=GUI_NAME keeps a CenterDisplay value
at what QtCarVehicle (:4030) has, for values QtCar doesn't take over itself: VAPI_chargeAveragePower
-> GUI_chargeAveragePower, the charge rate (mi/hr) on the cluster. --no-link: no connectivity
values (--wifi runs: the car's ConnMan publishes them)."""

import argparse
import subprocess
import time
import urllib.error
import urllib.request


PROBE_URL = "https://connectivitycheck.gstatic.com/generate_204"
VEHICLE_URL = "http://127.0.0.1:4030/_data_get_value_request_?name="


def online():
    try:
        request = urllib.request.Request(PROBE_URL, headers={"User-Agent": "mcu2-desktop/1"})
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status == 204
    except (OSError, urllib.error.URLError):
        return False


def set_value(rootfs, destination, name, value):
    result = subprocess.run(
        ["chroot", rootfs, "/usr/bin/dbus-send", "--system", "--print-reply=literal",
         f"--dest={destination}", "/LocalDataValue",
         "com.tesla.LocalDataValue.DataSetValueRequest", f"string:{name}", value],
        capture_output=True, text=True, timeout=5, check=False,
    )
    return result.returncode == 0 and "boolean true" in result.stdout


def vehicle_value(name):
    """A QtCarVehicle data value as a number, None while it's invalid or not running."""
    try:
        with urllib.request.urlopen(VEHICLE_URL + name, timeout=2) as response:
            return float(response.read().decode().strip())
    except (OSError, ValueError, urllib.error.URLError):
        return None


def publish(rootfs, connected):
    # On the PC there is no car Wi-Fi or cellular modem. Present the host route as a logical
    # Wi-Fi link, then report whether it can actually reach the internet. CenterDisplay
    # computes GUI_canUseInternet from LINK_linkState and CONN_connectedToInternet; MediaV2
    # independently requires LINK_wifiState == "online" for its internetAvailable state.
    # ChromiumAdapter separately sends WifiConnectedChanged from CONN_wifiConnected;
    # without it the web apps show a Premium Connectivity gate despite this Wi-Fi link.
    # SpotifyServer reads from DvServer and matches "wifi" case-sensitively. Publish to both
    # services so QtCar and Spotify see the same host connection state.
    link = ("LINK_linkState", "variant:string:wifi" if connected else "variant:string:")
    wifi = ("LINK_wifiState", "variant:string:online" if connected else "variant:string:offline")
    wifi_connected = ("CONN_wifiConnected", f"variant:boolean:{str(connected).lower()}")
    internet = ("CONN_connectedToInternet", f"variant:boolean:{str(connected).lower()}")
    values = (link, wifi, wifi_connected, internet) if connected else (internet, wifi_connected, wifi, link)
    return all(
        set_value(rootfs, destination, *item)
        for item in values
        for destination in ("com.tesla.DvServer", "com.tesla.CenterDisplay")
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rootfs", help="the mounted chroot/ directory")
    parser.add_argument("--interval", type=float, default=15, help="seconds between probes")
    parser.add_argument("--once", action="store_true", help="probe and publish once, then exit")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VARIANT",
                        help="keep a CenterDisplay data value, e.g. GUI_audioReady=variant:boolean:true")
    parser.add_argument("--copy", action="append", default=[], metavar="SRC=DEST",
                        help="keep CenterDisplay value DEST at QtCarVehicle's number SRC")
    parser.add_argument("--no-link", action="store_true", help="don't publish the connectivity values")
    args = parser.parse_args()
    extra = [item.split("=", 1) for item in args.set]
    copies = [item.split("=", 1) for item in args.copy]
    last = None
    failed_probes = 0
    published = False
    while True:
        detected = False if args.no_link else online()
        failed_probes = 0 if detected else failed_probes + 1
        # Avoid putting media to sleep after one transient failed probe. A fresh run reports
        # offline immediately; a real loss is reported after two consecutive failures.
        connected = detected or (last is True and failed_probes < 2)
        if connected != last and not args.no_link:
            print(f"host internet: {'online' if connected else 'offline'}", flush=True)
            last = connected
        try:
            ok = args.no_link or publish(args.rootfs, connected)
            ok = all([set_value(args.rootfs, "com.tesla.CenterDisplay", name, value)
                      for name, value in extra]) and ok
            for src, dest in copies:
                value = vehicle_value(src)
                if value is not None:
                    set_value(args.rootfs, "com.tesla.CenterDisplay", dest, f"variant:double:{value}")
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if ok != published:
            print(f"Data values: {'updated' if ok else 'waiting for firmware services'}", flush=True)
            published = ok
        if args.once:
            return 0 if ok else 1
        time.sleep(args.interval if ok else 2)


if __name__ == "__main__":
    raise SystemExit(main())
