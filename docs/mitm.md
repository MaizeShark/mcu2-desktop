# Watching the car's HTTPS traffic (MITM)

The car's programs check TLS certificates against their own CA store, so an intercepting proxy
such as [mitmproxy](https://mitmproxy.org) only works when its CA is trusted. `--mitm-cert FILE`
(`MITM_CERT=` in tesla.conf) installs a CA certificate for one run. **Mainly for the Model 3**
(2019.20.4.2); see the end of this page for the Model S/X.

## What `--mitm-cert` does

Nothing is written into the image. A run without the option trusts only the firmware's own CAs.

- **OpenSSL, Qt 4.8 (QtCar's own requests), curl, Go:** a copy of the image's `/etc/ssl/certs`
  with the CA added (as a hashed link `<hash>.0` and at the end of `ca-certificates.crt`) is
  bind-mounted read-only over the original.
- **The browser** (Model 3: CEF, Chrome 73, inside QtCar as user `tesla`) uses NSS, not OpenSSL.
  Its database `/home/tesla/.pki/nssdb` gets a tmpfs with a fresh database that trusts the CA.
  Mozilla's roots are built into `libnssckbi`, so normal sites still work. This needs the host's
  `certutil` (Fedora `nss-tools`, Debian `libnss3-tools`). Without it you get a warning and only
  the browser is left out.
- **Tesla's own clients** (`QtCarServer`, `QtCarConnMan`, `QtCarTMServer`; also `hermes_proxy`,
  which isn't in the Model 3 dump) don't trust `/etc/ssl/certs` for the backend: they pin
  specific CA files (`/etc/ca.crt`, `/etc/tesla_motors-ca.crt`, `/etc/hermes-eng-ca/products-ca.crt`
  and `root-ca.crt`). The CA is **appended** to each of those that exists (a bind-mounted copy
  = original + CA), so a server certificate the CA signs is accepted on those calls and the real
  pins keep working. See the limits below for why you still may not see every backend request.
- Only the certificate is copied, never a private key: mitmproxy's `mitmproxy-ca.pem` (key and
  certificate) works too, but `mitmproxy-ca-cert.pem` is the one meant for this.
- PEM or DER. `./tesla check` shows the CA's name and warns when it isn't a CA or has expired.
- Logs: `mitm.log` and the `mitm-cert:` line in `events.log` (it records the pinned anchors the
  CA was appended to); the files are in `logs/<run>/mitm/`.

## Setting up mitmproxy

The chroot shares the PC's network, so the easiest way is mitmproxy in **transparent mode**,
with the car's traffic sent to it by a NAT rule that matches the car's users only. The PC's own
traffic isn't touched.

1. Install mitmproxy (`sudo dnf install mitmproxy`, `sudo apt install mitmproxy`, or
   `pipx install mitmproxy`) and start it once as your own user, not as root, so it creates its CA:

   ```sh
   mitmproxy --mode transparent --showhost --listen-port 8080
   ```

   (`mitmweb` instead of `mitmproxy` gives the same in a browser tab on http://127.0.0.1:8081.)

2. Point tesla.conf at its CA (or pass `--mitm-cert` on each start):

   ```sh
   MITM_CERT=~/.mitmproxy/mitmproxy-ca-cert.pem
   ```

3. Send the car's TCP 80/443 to it. The rules match the chroot's service users by uid: QtCar and
   its browser run as `tesla` (1111); the other services' users are between 1001 and 2999
   (`awk -F: '$3>1000' chroot/etc/passwd`). Use the range only if no other user of the PC has a
   uid there (`getent passwd {1001..2999}` prints nothing). uid 0 isn't included: the chroot's root
   is the PC's root.

   ```sh
   sudo iptables -t nat -N TESLA-MITM
   sudo iptables -t nat -A TESLA-MITM -p tcp -m multiport --dports 80,443 -j REDIRECT --to-ports 8080
   sudo iptables -t nat -A OUTPUT -m owner --uid-owner 1001-2999 -j TESLA-MITM
   # Chromium also speaks QUIC (UDP 443), which would bypass the proxy: refuse it, it falls back to TCP
   sudo iptables -A OUTPUT -p udp --dport 443 -m owner --uid-owner 1001-2999 -j REJECT
   ```

   For IPv6 do the same with `ip6tables`, or the car's IPv6 connections go around the proxy.
   mitmproxy runs as your user (uid 1000), which isn't matched, so its own connections go
   straight out.

4. `./tesla start`. The flows show up in mitmproxy: QtCar's own requests (e.g. Google's map
   tiles) and the browser's.

5. Undo the rules when you're done (a reboot drops them too):

   ```sh
   sudo iptables -t nat -D OUTPUT -m owner --uid-owner 1001-2999 -j TESLA-MITM
   sudo iptables -t nat -F TESLA-MITM && sudo iptables -t nat -X TESLA-MITM
   sudo iptables -D OUTPUT -p udp --dport 443 -m owner --uid-owner 1001-2999 -j REJECT
   ```

To look at one program only, use its uid instead of the range (e.g. `--uid-owner 1111` for QtCar
and the browser). The proxy can also run on another machine: there, its regular (non-transparent)
mode works only for programs that take a proxy setting. The Model 3's QtCar takes none.

## Seeing the backend's own requests

`--mitm-cert` appends the CA to the pinned anchors (above), so Tesla's clients accept a server
certificate it signs. Two things still stand between you and the plaintext:

- **Mutual TLS.** `QtCarServer` and the hermes clients present the car's own client certificate
  (`/var/lib/car_creds/car.crt` + `car.key`), which isn't in the dump (`/opt/hermes` is empty too,
  so `hermes_proxy` itself is absent on the Model 3). Where the endpoint *requires* the client
  certificate, the handshake fails before the request is sent. Where it doesn't (configure your
  local endpoint not to ask for one), the request is visible. The project never supplies another
  car's credentials: that would be presenting that car's identity to Tesla.
- **No real upstream.** The backend hostnames resolve to loopback (`mothership.vn.teslamotors.com`,
  already in the image's `/etc/hosts`) or to real Tesla DNS (`*.vn.tesla.services`). To read the
  request you point the host at your own endpoint — a stub that logs, or `mitmproxy` in reverse
  mode with a cert your CA signed — rather than relaying to Tesla.

So this gets you what the firmware *tries* to send (the hostname, port, and the request body for
calls that don't demand the client cert), not a live decrypted session with Tesla's servers.

## Limits

- **Pinning done in a binary**, not in a CA file, would still refuse the proxy. The clients here
  pin by file path, which is why appending to those files works; a client that pinned a leaf or a
  public-key hash internally would not be covered. mitmproxy's `--ignore-hosts REGEX` passes a
  host through unchanged if you want to leave one alone.
- **Tested** (Model 3, 2026-11-03) with a test CA and a local TLS server: the firmware's curl and
  OpenSSL (`-CApath /etc/ssl/certs`) accept it only with the option, and so does the browser
  (`./sandbox.sh --browser`: the test page loads as "secure", a self-signed site is still refused,
  normal sites still work). QtCar's Qt 4.8 requests read the same `/etc/ssl/certs` but weren't
  tested on their own. The pinned-anchor append was checked with `openssl verify` (the test leaf
  verifies against `/etc/ca.crt` + CA, the originals still parse); the real Tesla clients
  weren't exercised (their binaries need the car credentials, absent here).
- The host's newer NSS (3.11x and later, e.g. Fedora's) writes PKCS#11 3.2 trust objects that the
  firmware's NSS 3.39 doesn't read: the CA is found but not trusted (`qtcar.log`:
  `CERT_PKIXVerifyCert ... err=-8172`). `./tesla start` runs certutil with `NSS_TRUST_TYPE=NSS`
  for that.
- **Model S/X** (2026.8.3): the same `/etc/ssl/certs` copy, plus the NSS database for `tesla`
  and the `chromium*` users' homes. Untested: its Chromium may keep its database elsewhere.
