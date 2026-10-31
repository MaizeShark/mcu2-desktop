#!/usr/bin/env python3
"""Feed the host's internet reachability, and its cellular modem, into QtCar's data values.

Model S/X (default): through D-Bus (the chroot's dbus-send, run as root) to DvServer and
CenterDisplay. The host route is presented as a Wi-Fi link (see publish_wifi).

--set NAME=VARIANT keeps more CenterDisplay data values at a value (dbus-send variant syntax),
for what the car's hardware would report, e.g. GUI_audioReady=variant:boolean:true: QtCar sets
it from the car's audio DSP (AUDIO_dspState), which doesn't run here; without it the arcade and
the fireplace stay at "Audio Initializing". --copy VAPI_NAME=GUI_NAME keeps a CenterDisplay value
at what QtCarVehicle (:4030) has, for values QtCar doesn't take over itself: VAPI_chargeAveragePower
-> GUI_chargeAveragePower, the charge rate (mi/hr) on the cluster. --no-link: no connectivity
values (--wifi runs: the car's ConnMan publishes them).

--cellular: while the host's default route goes through a ModemManager modem (mmcli), the link
is presented as cellular instead: the status bar's cell icon with the network type (LTE, 3G, E),
the signal bars and the operator, as the car's QtCarConnMan would publish them from its modem.
--cellular-only: the same, but nothing is published while the host isn't online over a modem
(the firmware's own values stay: Model 3, --wifi runs).
--http URL: publish through QtCar's HTTP data values instead of D-Bus (the Model 3's QtCar,
http://127.0.0.1:4220; no root needed); only with --cellular-only."""

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


PROBE_URL = "https://connectivitycheck.gstatic.com/generate_204"
VEHICLE_URL = "http://127.0.0.1:4030/_data_get_value_request_?name="
DBUS_DESTINATIONS = ("com.tesla.DvServer", "com.tesla.CenterDisplay")


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


class DBusValues:
    """Model S/X: DvServer and CenterDisplay over D-Bus."""

    def __init__(self, rootfs):
        self.rootfs = rootfs

    def set(self, name, value):
        if isinstance(value, bool):
            variant = f"variant:boolean:{str(value).lower()}"
        elif isinstance(value, int):
            variant = f"variant:int32:{value}"
        else:
            variant = f"variant:string:{value}"
        return all([set_value(self.rootfs, destination, name, variant)
                    for destination in DBUS_DESTINATIONS])

    def get(self, name):
        """CenterDisplay's value as text, None when it can't be read."""
        result = subprocess.run(
            ["chroot", self.rootfs, "/usr/bin/dbus-send", "--system", "--print-reply=literal",
             "--dest=com.tesla.CenterDisplay", "/LocalDataValue",
             "com.tesla.LocalDataValue.DataGetValueRequest", f"string:{name}"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        words = result.stdout.split(None, 2)
        if result.returncode or words[:2] != ["boolean", "true"]:
            return None
        return words[2].strip() if len(words) > 2 else ""


class HttpValues:
    """Model 3: QtCar's HTTP data values (:4220)."""

    def __init__(self, url):
        self.url = url.rstrip("/")

    def request(self, path):
        try:
            with urllib.request.urlopen(self.url + path, timeout=3) as response:
                return response.read().decode().strip()
        except (OSError, urllib.error.URLError):
            return None

    def set(self, name, value):
        text = str(value).lower() if isinstance(value, bool) else str(value)
        query = urllib.parse.urlencode({"name": name, "value": text})
        return self.request(f"/_data_set_value_request_?{query}") is not None

    def get(self, name):
        return self.request(f"/_data_get_value_request_?{urllib.parse.urlencode({'name': name})}")


def publish_wifi(values, connected):
    # On the PC there is no car Wi-Fi or cellular modem. Present the host route as a logical
    # Wi-Fi link, then report whether it can actually reach the internet. CenterDisplay
    # computes GUI_canUseInternet from LINK_linkState and CONN_connectedToInternet; MediaV2
    # independently requires LINK_wifiState == "online" for its internetAvailable state.
    # ChromiumAdapter separately sends WifiConnectedChanged from CONN_wifiConnected;
    # without it the web apps show a Premium Connectivity gate despite this Wi-Fi link.
    # SpotifyServer reads from DvServer and matches "wifi" case-sensitively. Publish to both
    # services so QtCar and Spotify see the same host connection state.
    link = ("LINK_linkState", "wifi" if connected else "")
    wifi = ("LINK_wifiState", "online" if connected else "offline")
    wifi_connected = ("CONN_wifiConnected", connected)
    internet = ("CONN_connectedToInternet", connected)
    items = (link, wifi, wifi_connected, internet) if connected else (internet, wifi_connected, wifi, link)
    return all([values.set(*item) for item in items])


# --- the host's modem (ModemManager) ------------------------------------------------------------

# ModemManager's access technologies (most capable first) -> CONN_cellSystemMode, which the status
# bar turns into its label (LTE, 3G, E: QtCar's NetworkStatusBarItem::getCellNetworkLabel; there
# is no 5G label), and the S/X's CONN_cellNetwork enum (libQtCarGUI's CellNetworkNameMap: 0 Unknown,
# 1 GPRS, 2 EDGE, 3 UMTS, 4 HSDPA, 5 HSUPA, 6 HSPA, 7 LTE, 8 GSM, 9 CDMA, 10 WCDMA, 11 LTE5GCN,
# 12 5GNSA, 13 5GSA, 14 NGRAN, 15 SNA).
ACCESS_TECHNOLOGIES = (
    ("5gnr", "LTE", 13),
    ("lte", "LTE", 7),
    ("hspa-plus", "HSPA", 6),
    ("hspa", "HSPA", 6),
    ("hsupa", "HSUPA", 5),
    ("hsdpa", "HSDPA", 4),
    ("umts", "UMTS", 3),
    ("edge", "EDGE", 2),
    ("gprs", "GPRS", 1),
    ("gsm", "GSM", 8),
    ("evdo", "CDMA", 9),
    ("1xrtt", "CDMA", 9),
)


def run_json(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


def known(value):
    """mmcli writes "--" for what it doesn't know."""
    return "" if value in (None, "--") else value


def default_route_interface():
    routes = run_json(["ip", "-j", "route", "get", "1.1.1.1"])
    return routes[0].get("dev") if routes else None


def modem_interfaces(generic):
    """The modem's network interfaces: its net ports and its connected bearers' interfaces
    (ppp0 for modems that dial over AT commands)."""
    interfaces = {port.split()[0] for port in generic.get("ports", []) if port.endswith("(net)")}
    for bearer in generic.get("bearers", []):
        status = (run_json(["mmcli", "-b", bearer, "-J"]) or {}).get("bearer", {}).get("status", {})
        if status.get("connected") == "yes" and known(status.get("interface")):
            interfaces.add(status["interface"])
    return interfaces


def network_type(technologies):
    """(CONN_cellSystemMode, S/X CONN_cellNetwork) for ModemManager's access technologies."""
    technologies = [technology.lower() for technology in technologies]
    if "5gnr" in technologies and any(t.startswith("lte") for t in technologies):
        return "LTE", 12   # 5G NSA: LTE anchor plus NR
    for name, mode, network in ACCESS_TECHNOLOGIES:   # lte-cat-m, gsm-compact, evdoa, ...
        if any(t.startswith(name) for t in technologies):
            return mode, network
    return "", 0


def signal_bars(quality):
    """ModemManager's signal quality (0-100 %) as the status bar's 0-5 (0 and 1: just the dot,
    5: all four bars)."""
    if quality <= 0:
        return 0
    return max(1, min(5, (quality + 10) // 20))


def modem_on_route():
    """The modem the host's default route goes through, as a dict, or None."""
    interface = default_route_interface()
    if not interface:
        return None
    for path in (run_json(["mmcli", "-L", "-J"]) or {}).get("modem-list", []):
        modem = (run_json(["mmcli", "-m", path, "-J"]) or {}).get("modem", {})
        generic = modem.get("generic", {})
        if interface not in modem_interfaces(generic):
            continue
        threegpp = modem.get("3gpp", {})
        try:
            quality = int(known(generic.get("signal-quality", {}).get("value")) or 0)
        except ValueError:
            quality = 0
        mode, network = network_type(generic.get("access-technologies", []))
        return {
            "interface": interface,
            "model": " ".join(filter(None, (known(generic.get("manufacturer")), known(generic.get("model"))))),
            "operator": known(threegpp.get("operator-name")) or known(threegpp.get("operator-code")),
            "mode": mode,
            "network": network,
            "quality": quality,
            "bars": signal_bars(quality),
        }
    return None


class Cellular:
    """The cellular values, as the car's QtCarConnMan publishes them from its modem."""

    def __init__(self, values, sx):
        self.values = values
        self.sx = sx
        self.shown = False
        self.data_plan = None

    def publish(self, modem, connected):
        values = self.values
        items = [
            ("CONN_cellConnected", True),
            ("CONN_cellActive", True),
            ("CONN_cellSystemMode", modem["mode"]),
            ("CONN_cellSignalBars", modem["bars"]),
            ("CONN_cellOperator", modem["operator"]),
            ("CONN_connectedToInternet", connected),
            # the status bar: cell icon (QtCar takes "Wifi"/"wifi" for Wi-Fi, anything else as cellular)
            ("LINK_linkState", "cellular"),
        ]
        if self.sx:
            # CenterDisplay gives a cellular link internet only with the car's data plan (Premium
            # Connectivity: OPTION_dataPlan, "None" here); the Model 3's is "Full" already.
            # MediaV2 and ChromiumAdapter only look at the Wi-Fi values (see publish_wifi), so they
            # stay open as well; the status bar shows the cell icon from LINK_linkState.
            if not self.shown:
                self.data_plan = values.get("OPTION_dataPlan")
            items[:0] = [("CONN_cellNetwork", modem["network"]), ("OPTION_dataPlan", "Full")]
            items += [("LINK_wifiState", "online" if connected else "offline"),
                      ("CONN_wifiConnected", connected)]
        ok = all([values.set(*item) for item in items])
        self.shown = True
        return ok

    def withdraw(self):
        """The host isn't online over the modem any more."""
        if not self.shown:
            return True
        items = [("CONN_cellConnected", False), ("CONN_cellActive", False), ("LINK_linkState", "")]
        if self.sx and self.data_plan not in (None, "", "Full", "<invalid>"):
            items.append(("OPTION_dataPlan", self.data_plan))
        ok = all([self.values.set(*item) for item in items])
        self.shown = False
        return ok


def describe(modem):
    bars = f"{modem['bars']}/5 ({modem['quality']} %)"
    return f"{modem['model'] or 'modem'} on {modem['interface']}: {modem['operator'] or '?'}, " \
           f"{modem['mode'] or '?'}, signal {bars}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rootfs", help="the mounted chroot/ directory")
    parser.add_argument("--interval", type=float, default=15, help="seconds between probes")
    parser.add_argument("--once", action="store_true", help="probe and publish once, then exit")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VARIANT",
                        help="keep a CenterDisplay data value, e.g. GUI_audioReady=variant:boolean:true")
    parser.add_argument("--copy", action="append", default=[], metavar="SRC=DEST",
                        help="keep CenterDisplay value DEST at QtCarVehicle's number SRC")
    parser.add_argument("--no-link", action="store_true", help="don't publish the connectivity values")
    parser.add_argument("--cellular", action="store_true",
                        help="present the host's modem (ModemManager) as the car's cellular link")
    parser.add_argument("--cellular-only", action="store_true",
                        help="like --cellular, but publish nothing while the host isn't online over a modem")
    parser.add_argument("--http", metavar="URL",
                        help="QtCar's HTTP data values (Model 3: http://127.0.0.1:4220) instead of D-Bus")
    args = parser.parse_args()
    if args.http and (args.set or args.copy or not args.cellular_only):
        parser.error("--http works with --cellular-only (and without --set/--copy)")
    values = HttpValues(args.http) if args.http else DBusValues(args.rootfs)
    cellular = Cellular(values, sx=not args.http) if args.cellular or args.cellular_only else None
    link_only_cellular = args.no_link or args.cellular_only
    extra = [item.split("=", 1) for item in args.set]
    copies = [item.split("=", 1) for item in args.copy]
    last = None
    last_modem = None
    failed_probes = 0
    published = False
    while True:
        modem = modem_on_route() if cellular else None
        if (modem and describe(modem)) != (last_modem and describe(last_modem)):
            if modem:
                print(f"host online over a modem: {describe(modem)}", flush=True)
            elif last_modem:
                print("host not online over a modem any more", flush=True)
            last_modem = modem
        probing = modem or not link_only_cellular
        detected = online() if probing else False
        failed_probes = 0 if detected else failed_probes + 1
        # Avoid putting media to sleep after one transient failed probe. A fresh run reports
        # offline immediately; a real loss is reported after two consecutive failures.
        connected = detected or (last is True and failed_probes < 2)
        if probing and connected != last:
            print(f"host internet: {'online' if connected else 'offline'}", flush=True)
            last = connected
        try:
            if modem:
                ok = cellular.publish(modem, connected)
            else:
                ok = cellular.withdraw() if cellular else True
                if not link_only_cellular:
                    ok = publish_wifi(values, connected) and ok
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
