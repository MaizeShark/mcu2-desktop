# Next session: after the Model S/X merge

Continue in `~/TeslaMCU`, branch `main` (`model-s` was merged on 2026-10-27). Read `CLAUDE.md`,
`README.md`, `docs/` (above all `features.md`), `notes/README.md`, `notes/model-s-parity.md` (the audit, the agreed order and the
progress: this is the work list) and `notes/session15-2026-10-27.md` first, in full; session 14's
notes and the older Model S notes (sessions 9-13) where an item needs them. The PC runs Fedora 44
(KDE + Niri, Wayland, SELinux enforcing). Both firmwares are on `main`.

## Rules
- Never commit or push firmware files, extracted binaries, keys, certificates, VINs, credentials,
  tokens, device addresses or private details (addresses, local network). `github` only when the
  user asks, after checking; `origin` often. Don't change the PC's networking (no `--wifi` here).
- You can't sudo; the user runs sudo commands (`build-image`, `start`). Test what you can in the
  sandbox (`./sandbox.sh --root sandbox/rootfs-s ... dbus qtcar-dvserver qtcar-vehicle` for
  vehicle data in QtCar; tesla-can.py on the host with `--db vehicle/work/can-db-models.json
  --all-zero --preset parked --http 8099 --respond`). Stop the sandbox before the user starts a
  run (shared ports). Never modify `mcu2-original/` or `mcu2s-original/`.
- Never run the car's escalator on the system; the S profile's `escalator-stub` stands in for it.
- Don't invent tokens, extract embedded secrets or credentials, or ask the user to paste passwords.
- Work until the context is at about 90 %, without stopping in the middle of a task, then update
  the docs, the session notes, `model-s-parity.md` and this prompt.

## 0. State at the end of session 15 (2026-10-27)
The Model S/X is at the Model 3's level; `model-s` was fast-forwarded into `main` and pushed to
origin and GitHub. Confirmed in the user's real runs: charging (cluster figures), MAME scroll
wheels, USB music, Bluetooth music through the S's audiod, and everything of session 14. The user
doesn't need `--native` on Wayland. The kit changed slightly since the user's last build-image
(a comment, the sv wrapper's pid-file message): `./tesla check` warns until the next rebuild.

## 1. Open (ask the user what they want)
From model-s-parity.md: confirm the route line on the elevation tiles (`build-image --maps US`,
a Bay Area route), the stick with the S on real hardware (8), roof colour / Model X look / badge
(optional). Report before big changes.

Write session notes (`notes/session16-*.md`, index in `notes/README.md`), update
`model-s-parity.md`, and replace this prompt at the end.
