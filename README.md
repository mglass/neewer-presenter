# neewer-presenter

Use a **NEEWER RT-110** Bluetooth camera remote as a presentation clicker on
Linux or macOS.

The RT-110 is not a Bluetooth keyboard: it speaks a vendor-specific BLE GATT
protocol, so pairing it does nothing by itself. This small daemon subscribes
to the remote's button notifications and injects configurable keystrokes —
by default, keys that drive Google Slides, but it works for any app that
takes keyboard shortcuts.

## Protocol

Reverse-engineered from an RT-110. Button presses arrive as notifications on
characteristic `69400003-b5a3-f393-e0a9-e50e24dcca99`:

```
78 10 01 <button> <checksum>
```

| code | physical button |
|------|-----------------|
| 0x01 | shutter (big button) |
| 0x02 | A− |
| 0x03 | A+ |
| 0x04 | up arrow |
| 0x05 | down arrow |

The power button is internal only and sends nothing. Other NEEWER BLE remotes
may use the same protocol — reports welcome.

## Install

Requires Python 3.9+ and [bleak](https://github.com/hbldh/bleak).

```sh
git clone https://github.com/mglass/neewer-presenter
cd neewer-presenter
python3 -m venv .venv && .venv/bin/pip install bleak
```

Key injection backends:

- **Linux / Wayland**: `wtype` (e.g. `pacman -S wtype`, `apt install wtype`)
- **Linux / X11**: `xdotool`
- **macOS**: built-in AppleScript — but you must grant **Accessibility**
  permission to whatever runs the script (Terminal, iTerm, or
  `/usr/bin/osascript`) in System Settings → Privacy & Security → Accessibility.

Pair the remote first (turn it on, then pair `NEEWER-RT-110` in your OS
Bluetooth settings — or with `bluetoothctl` on Linux).

## Run

```sh
.venv/bin/python neewer_presenter.py
```

It scans for a device whose name starts with `NEEWER`, connects, and
reconnects automatically when the remote sleeps or goes out of range.

## Configure

Copy `config.example.json` to `config.json` (next to the script) or to
`~/.config/neewer-presenter/config.json`:

```json
{
  "address": "CF:BE:0F:5B:9F:1D",
  "buttons": {
    "shutter": "Right",
    "a_minus": "l",
    "a_plus": "b",
    "up": "Left",
    "down": "Right"
  }
}
```

`address` is optional (pins to one specific remote). Key names are X11/xkb
keysym names (`Right`, `Left`, `Escape`, `Page_Down`, `b`, …); on macOS they
are translated to AppleScript key codes (see `MAC_KEYCODES` in the script).

Default mapping is for **Google Slides** presentation mode: shutter/down =
next slide, up = previous, A+ = black screen, A− = laser pointer.

## Run at login

**Linux (systemd user service):** edit the path in
`systemd/neewer-presenter.service`, then

```sh
cp systemd/neewer-presenter.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now neewer-presenter
```

**macOS (launchd):** edit the paths in
`launchd/com.github.neewer-presenter.plist`, then

```sh
cp launchd/com.github.neewer-presenter.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.github.neewer-presenter.plist
```

## License

MIT
