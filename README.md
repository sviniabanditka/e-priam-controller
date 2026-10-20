# Cybex e-Priam BLE Controller

A terminal-UI controller for the **Cybex e-Priam** baby stroller, talking
to the stroller's electronics over Bluetooth Low Energy. Lets you toggle
the rocking feature with custom intensity / duration, switch the
e-mobility drive mode, and watch live status (battery, rocking timer,
errors) without touching the official mobile app.

> **Disclaimer.** This project is not affiliated with, endorsed by, or
> supported by Cybex GmbH. The BLE protocol was figured out by
> reverse-engineering the official app's traffic; behavior may change in
> future firmware revisions. Use at your own risk — especially when a
> child is in the stroller.

---

## Features

- **Split-screen TUI**: live status on the left, scrollable event log on
  the right. No more notification spam clobbering your input.
- **Rocking control**: pick intensity (HIGH / MED / LOW / OFF), set
  duration in **minutes** (1–500), toggle "continue on disconnect" and
  "auto-renew" (see below).
- **Auto-renew**: optional toggle that re-issues the rocking command
  when the timer drops to ≤1 minute, effectively making the rocking
  session run forever (until you stop it or the BLE link drops with
  "continue on disconnect" off).
- **Drive mode**: switch between ECO / TOUR / BOOST (BOOST is
  unofficial — accepted by the firmware but not exposed in the Cybex
  app).
- **Live status panel**: battery %, voltage, rocking intensity,
  remaining / total time, error flags, drive mode, connection state.
- **Smart logging**: only state *changes* go to the log, not every
  notification tick.
- **Manual reconnect** (`c`) without restarting the program.
- **Optional file log** (`--log-file PATH`) capturing every raw
  notification plus events, for debugging or later inspection.

---

## How it works

The Cybex e-Priam advertises itself over BLE with manufacturer ID
`1933` and exposes three GATT characteristics that this tool talks to:

| UUID                                   | Direction | Purpose                                |
| -------------------------------------- | --------- | -------------------------------------- |
| `a1fc0102-78d3-40c2-9b6f-3c5f7b2797df` | notify    | Battery status (4 bytes)               |
| `a1fc0103-78d3-40c2-9b6f-3c5f7b2797df` | r/w/notify| Drive mode (1 byte: 1=ECO, 2=TOUR, 3=BOOST) |
| `a1fc0104-78d3-40c2-9b6f-3c5f7b2797df` | r/w/notify| Rocking control (5 bytes)              |

The rocking packet layout (status notify):

```
byte 0      : flags  — low nibble = intensity (0=OFF, 1=HIGH, 2=MED, 3=LOW),
                       bit 4 = "continue on disconnect",
                       bit 7 + low byte 20 = "brake not engaged" error
bytes 1–2   : time_left (uint16, little-endian, seconds)
bytes 3–4   : set_time  (uint16, little-endian, seconds)
```

To start rocking, write the same 5-byte layout back. To stop, write
five zero bytes.

The TUI scans for an advertising e-Priam, connects via
[`bleak`](https://github.com/hbldh/bleak), subscribes to all three
characteristics, performs an initial GATT read of each (so the panel
fills in immediately instead of waiting for the next push), and renders
everything with [Textual](https://textual.textualize.io/).

---

## Prerequisites

- **Python 3.9+**
- A computer with a working **Bluetooth LE** adapter
  - Linux: BlueZ ≥ 5.43
  - macOS: 10.13+
  - Windows: 10 (build 16299) or later
- **Cybex e-Priam** stroller, powered on and within Bluetooth range
- A modern terminal that can render Unicode + emoji (most do)

---

## Installation

```bash
git clone https://github.com/<you>/python-priam.git
cd python-priam
python3 -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

The two runtime dependencies are `bleak` (BLE) and `textual` (TUI).

---

## Quick start

```bash
python3 main.py
```

The TUI launches, scans for the stroller for ~15 seconds, and connects
automatically. From there:

| Key       | Action                                    |
| --------- | ----------------------------------------- |
| `r`       | Open the rocking dialog                   |
| `s`       | Stop rocking immediately                  |
| `d`       | Open the drive-mode dialog                |
| `c`       | Reconnect (cancels the current connection)|
| `q` / `Ctrl+C` | Quit                                 |

### Rocking dialog

- **Intensity** — `HIGH` / `MED` / `LOW` / `OFF`
- **Duration** — in minutes, 1 to 500 (see FAQ for why 500)
- **Continue on disconnect** — if checked, rocking keeps running after
  the BLE link drops
- **Auto-renew** — if checked, the controller re-sends the same
  command whenever `time_left` drops to ≤1 minute, so the session
  effectively never ends. Auto-renew is automatically cancelled if you
  hit Stop or if the firmware reports `intensity = OFF` for any other
  reason.

### Drive dialog

- **ECO** / **TOUR** / **BOOST**. ECO and TOUR match the official app.
  BOOST is the firmware accepting `level=3`; it produces stronger motor
  assistance but is not surfaced in the Cybex app.

### Logging to a file

```bash
python3 main.py --log-file run.log
```

The log file gets every raw notification (`STATUS`, `DRIVE`, `ROCKING`
+ hex payload) plus all events that appear in the TUI panel. Without
the flag, nothing is written to disk.

---

## Architecture

```
priam_ble.py        # Pure protocol: UUIDs, dataclasses, parsers, builders, scanner.
                    # No I/O state, no UI — easy to import for tests/scripts.
priam_client.py     # PriamClient — owns the BleakClient, runs the notify pump,
                    # parses frames, diff-logs state changes, exposes
                    # write_rocking / stop_rocking / write_drive. Talks to UI
                    # via the PriamObserver Protocol.
priam_tui.py        # PriamApp + StatusPanel + ControlsPanel + RockingScreen +
                    # DriveScreen. Implements PriamObserver: events from the
                    # client land in reactive state and the RichLog.
priam_styles.tcss   # Textual CSS (split layout, modal dialogs, status lines).
main.py             # argparse + PriamApp(...).run().
```

The split keeps each layer testable in isolation: `priam_ble` is
pure-functional, `priam_client` is BLE-only (no Textual), `priam_tui`
is UI-only (no `bleak`).

---

## FAQ

### Why is the duration limit 500 minutes?

The 16-bit duration field can technically encode 1–1092 minutes, and
the stroller will happily echo back any `set_time` you write. But the
firmware's *internal* `time_left` counter clamps at roughly 30 410
seconds (≈ 506 min, 8 h 27 min). Above that, the live countdown wraps
modulo 30 410 and shows nonsense (e.g. you ask for 600 min and see
`93/600`). 500 keeps you safely below the wrap.

### "Stroller not found"

The scan window is 15 s. Make sure the stroller is powered on (try
unfolding/refolding it), within ~5 m, and that nothing else is already
connected (the official Cybex app holds a single BLE connection — close
it first).

### What is BOOST and is it safe?

BOOST = `level=3` written to the drive characteristic. The firmware
accepts it and the motors respond more aggressively than in TOUR.
Cybex didn't expose it in their app, so it isn't formally supported.
Treat it as a curiosity, not a daily-use feature. Don't use it on
slopes or with heavy loads.

### "Rocking error: brake not engaged"

The stroller refuses to rock if the parking brake isn't on. Engage the
brake and re-issue the command.

### Battery percentage doesn't show up right after connecting

It does now — the client performs an initial GATT read of all three
characteristics right after subscribing, so the panel populates without
waiting for the next push. If you still see `🔋 —`, the read probably
failed — there should be a yellow `⚠ initial read STATUS failed: …`
line in the log.

### Can I rock the stroller indefinitely?

Yes — enable **Auto-renew** in the rocking dialog. The 500-min limit
still applies to a single command, but the controller will keep
re-issuing the same preset as the timer ticks down. Practically:

- Auto-renew needs the BLE link to stay up — if the laptop disconnects
  or the program is closed, no more renewals happen. Pair it with
  "continue on disconnect" so the stroller doesn't stop the moment you
  walk away, but understand that without re-issues it will still time
  out at the end of the current set duration.
- If the firmware reports rocking has stopped for any reason
  (manual `s`, error, end of duration not caught in time), auto-renew
  switches off — it won't restart a session you didn't ask for.
- You'll see `🔁 Auto-renew triggered` in the log every time it fires
  (roughly once per `duration` minutes).

### Can I use this without the TUI, e.g. from a script?

Yes. `priam_ble.py` is pure protocol (parsers + command builders) and
can be imported standalone:

```python
from priam_ble import build_rocking_cmd
cmd = build_rocking_cmd(intensity=2, duration_minutes=30, continue_on_disconnect=True)
# write `cmd` to a1fc0104-… on the stroller via your own BLE client
```

---

## Contributing

Issues and PRs welcome — especially:

- Captures of the official Cybex app's BLE traffic (helps verify and
  extend the protocol notes above).
- Confirmations / counter-examples for other firmware revisions.
- Support for related Cybex e-mobility models that may share the
  protocol.

---

## License

MIT (or whatever you prefer — set this before publishing).

"Cybex" and "e-Priam" are trademarks of their respective owners. This
project is independent and unofficial.

---

## Web / PWA version (`docs/`)

A dependency-free Web Bluetooth port of the same controller: `docs/index.html`
(UI + client), `docs/priam_ble.js` (protocol, mirrors `priam_ble.py`),
`manifest.json` + `sw.js` for installation. Serve the folder over HTTPS
(GitHub Pages works) and open it in a browser with Web Bluetooth:

- **Android**: Chrome / Edge / Samsung Internet, installable as PWA.
- **iOS**: Safari has no Web Bluetooth. Use the
  [Bluefy](https://apps.apple.com/app/bluefy-web-ble-browser/id1492822055) browser.
- **Desktop**: Chrome / Edge.

Local check without a stroller: `python3 -m http.server -d docs 8765`, then
`http://localhost:8765`. Protocol self-test: `node docs/test.mjs`.

Web Bluetooth needs the **GATT service UUID** containing the three
characteristics; bleak never needed it, so it is guessed at runtime
(`SERVICE_UUID_CANDIDATES`). If connecting fails with "no service with ROCKING
characteristic", discover it once next to the stroller and paste it into
*Advanced → Service UUID*:

```bash
python3 -c "
import asyncio; from bleak import BleakClient; from priam_ble import find_stroller
async def m():
    d = await find_stroller()
    async with BleakClient(d.address) as c:
        for s in c.services: print(s.uuid, [ch.uuid for ch in s.characteristics])
asyncio.run(m())"
```

Limitations vs. the TUI: the page must stay in the foreground (Screen Wake
Lock is requested while connected); auto-renew stops when the browser freezes
the tab. "Continue on disconnect" keeps the stroller rocking after a link drop.
