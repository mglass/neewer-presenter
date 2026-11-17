#!/usr/bin/env python3
"""neewer-presenter: use a NEEWER BLE camera remote as a presentation clicker.

The NEEWER RT-110 (and similar) is not a Bluetooth HID keyboard — it speaks a
vendor-specific BLE GATT protocol, so the OS never sees its buttons. This tool
subscribes to the remote's button notifications and injects keystrokes instead.

Protocol (reverse-engineered from an RT-110):
  notify characteristic 69400003-b5a3-f393-e0a9-e50e24dcca99
  payload: 78 10 01 <button> <checksum>, button = 0x01..0x05
    01 = shutter, 02 = A-, 03 = A+, 04 = up arrow, 05 = down arrow

Supported on Linux (Wayland via wtype, X11 via xdotool) and macOS
(AppleScript key codes; grant Accessibility permission to your terminal).

Usage:  python3 neewer_presenter.py [path/to/config.json]
"""

import asyncio
import json
import os
import platform
import subprocess
import sys
import time

from bleak import BleakClient, BleakScanner

NOTIFY_UUID = "69400003-b5a3-f393-e0a9-e50e24dcca99"
NAME_PREFIX = "NEEWER"
DEBOUNCE_S = 0.15

DEFAULT_CONFIG = {
    # "address": "CF:BE:0F:5B:9F:1D",   # optional: pin to one remote
    "buttons": {
        "shutter": "Right",   # next slide
        "a_minus": "l",       # Google Slides: laser pointer
        "a_plus": "b",        # Google Slides: black screen
        "up": "Left",         # previous slide
        "down": "Right",      # next slide
    },
}

BUTTON_CODES = {0x01: "shutter", 0x02: "a_minus", 0x03: "a_plus",
                0x04: "up", 0x05: "down"}

# macOS virtual key codes for AppleScript's `key code`
MAC_KEYCODES = {
    "Right": 124, "Left": 123, "Up": 126, "Down": 125,
    "Escape": 53, "Return": 36, "space": 49, "Tab": 48,
    "Page_Up": 116, "Page_Down": 121, "Home": 115, "End": 119,
    "F5": 96, "b": 11, "l": 37, "s": 1, "w": 13,
}


def load_config():
    paths = sys.argv[1:2] + [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"),
        os.path.expanduser("~/.config/neewer-presenter/config.json"),
    ]
    for p in paths:
        if os.path.isfile(p):
            with open(p) as f:
                cfg = json.load(f)
            print(f"config: {p}")
            merged = dict(DEFAULT_CONFIG)
            merged.update(cfg)
            return merged
    print("config: built-in defaults")
    return DEFAULT_CONFIG


def make_key_presser():
    system = platform.system()
    if system == "Darwin":
        def press(key):
            code = MAC_KEYCODES.get(key)
            if code is None:
                print(f"unknown key for macOS: {key}")
                return
            subprocess.Popen(["osascript", "-e",
                              f'tell application "System Events" to key code {code}'])
        return press
    if system == "Linux":
        from shutil import which
        if os.environ.get("WAYLAND_DISPLAY") and which("wtype"):
            return lambda key: subprocess.Popen(["wtype", "-k", key])
        if which("xdotool"):
            return lambda key: subprocess.Popen(["xdotool", "key", key])
        sys.exit("No key injector found: install wtype (Wayland) or xdotool (X11).")
    sys.exit(f"Unsupported platform: {system}")


async def find_device(address):
    if address:
        dev = await BleakScanner.find_device_by_address(address, timeout=15)
    else:
        dev = await BleakScanner.find_device_by_filter(
            lambda d, ad: (d.name or "").upper().startswith(NAME_PREFIX), timeout=15)
    return dev


async def run():
    cfg = load_config()
    buttons = cfg["buttons"]
    press = make_key_presser()
    last = {}

    def on_notify(_char, data: bytearray):
        if len(data) >= 4 and data[0] == 0x78:
            name = BUTTON_CODES.get(data[3])
            key = buttons.get(name)
            now = time.monotonic()
            if key and now - last.get(name, 0) > DEBOUNCE_S:
                last[name] = now
                print(f"{name} -> {key}")
                press(key)

    while True:
        dev = await find_device(cfg.get("address"))
        if dev is None:
            print("remote not found (is it on and paired?), retrying in 10s...")
            await asyncio.sleep(10)
            continue
        try:
            disconnected = asyncio.Event()
            async with BleakClient(dev, disconnected_callback=lambda c: disconnected.set()) as client:
                await client.start_notify(NOTIFY_UUID, on_notify)
                print(f"connected to {dev.name} ({dev.address}); listening for buttons")
                await disconnected.wait()
            print("remote disconnected, reconnecting...")
        except Exception as e:
            print(f"connection error: {e}; retrying in 5s...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
