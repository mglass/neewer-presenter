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
import functools
import json
import os
import platform
import subprocess
import sys
import time

print = functools.partial(print, flush=True)  # survive piped/logged stdout

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


def make_notify_handler(buttons, press):
    last = {}

    def on_notify(data):
        if len(data) >= 4 and data[0] == 0x78:
            name = BUTTON_CODES.get(data[3])
            key = buttons.get(name)
            now = time.monotonic()
            if key and now - last.get(name, 0) > DEBOUNCE_S:
                last[name] = now
                print(f"{name} -> {key}")
                press(key)

    return on_notify


async def run_mac(cfg, on_notify):
    """macOS: find the remote by scanning (or address) and hold the connection."""
    from bleak import BleakClient, BleakScanner

    while True:
        address = cfg.get("address")
        if address:
            dev = await BleakScanner.find_device_by_address(address, timeout=15)
        else:
            dev = await BleakScanner.find_device_by_filter(
                lambda d, ad: (d.name or "").upper().startswith(NAME_PREFIX),
                timeout=15)
        if dev is None:
            print("remote not found (is it on and paired?), retrying in 10s...")
            await asyncio.sleep(10)
            continue
        try:
            disconnected = asyncio.Event()
            async with BleakClient(
                    dev, disconnected_callback=lambda c: disconnected.set()) as client:
                await client.start_notify(NOTIFY_UUID,
                                          lambda _c, data: on_notify(data))
                print(f"connected to {dev.name} ({dev.address}); listening for buttons")
                await disconnected.wait()
            print("remote disconnected, reconnecting...")
        except Exception as e:
            print(f"connection error: {e}; retrying in 5s...")
            await asyncio.sleep(5)


async def run_bluez(cfg, on_notify):
    """Linux: talk to BlueZ over D-Bus directly.

    A bonded remote auto-reconnects to BlueZ and stops advertising, so a BLE
    scan can never find it — instead we watch BlueZ's object tree for the
    device and subscribe whenever its services are resolved.
    """
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    address = cfg.get("address")
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    intro = await bus.introspect("org.bluez", "/")
    obj = bus.get_proxy_object("org.bluez", "/", intro)
    objmgr = obj.get_interface("org.freedesktop.DBus.ObjectManager")

    async def get_iface(path, iface):
        node = await bus.introspect("org.bluez", path)
        return bus.get_proxy_object("org.bluez", path, node).get_interface(iface)

    def find_remote(objects):
        for path, ifaces in objects.items():
            dev = ifaces.get("org.bluez.Device1")
            if not dev:
                continue
            addr = dev["Address"].value
            name = (dev.get("Name") or dev.get("Alias"))
            name = name.value if name else ""
            if (address and addr.upper() == address.upper()) or \
               (not address and name.upper().startswith(NAME_PREFIX)):
                return path
        return None

    def find_notify_char(objects, dev_path):
        for path, ifaces in objects.items():
            ch = ifaces.get("org.bluez.GattCharacteristic1")
            if ch and path.startswith(dev_path) and \
               ch["UUID"].value.lower() == NOTIFY_UUID:
                return path
        return None

    subscribed_path = None

    async def try_subscribe():
        nonlocal subscribed_path
        objects = await objmgr.call_get_managed_objects()
        dev_path = find_remote(objects)
        if not dev_path:
            print("remote not found in BlueZ (is it paired?)")
            return
        dev = await get_iface(dev_path, "org.bluez.Device1")
        if not await dev.get_connected():
            try:
                await dev.call_connect()  # bonded remotes accept direct connects
            except Exception as e:
                print(f"connect failed (remote asleep?): {e}")
                return
        if not await dev.get_services_resolved():
            return  # wait for the ServicesResolved property change
        char_path = find_notify_char(objects, dev_path)
        if not char_path:
            return
        char = await get_iface(char_path, "org.bluez.GattCharacteristic1")

        def on_props(iface_name, changed, _invalidated):
            if "Value" in changed:
                on_notify(bytes(changed["Value"].value))

        props = await get_iface(char_path, "org.freedesktop.DBus.Properties")
        props.on_properties_changed(on_props)
        await char.call_start_notify()
        subscribed_path = char_path
        print(f"connected via BlueZ ({dev_path.rsplit('/', 1)[-1]}); listening for buttons")

    def on_dev_props(iface_name, changed, _invalidated):
        nonlocal subscribed_path
        if iface_name != "org.bluez.Device1":
            return
        if "ServicesResolved" in changed:
            if changed["ServicesResolved"].value:
                asyncio.get_event_loop().create_task(try_subscribe())
            else:
                subscribed_path = None
                print("remote disconnected, waiting for it to return...")

    # watch every present and future NEEWER device for (re)connection
    async def watch_device(dev_path):
        props = await get_iface(dev_path, "org.freedesktop.DBus.Properties")
        props.on_properties_changed(on_dev_props)

    objects = await objmgr.call_get_managed_objects()
    dev_path = find_remote(objects)
    if dev_path:
        await watch_device(dev_path)

    def on_added(path, ifaces):
        if "org.bluez.Device1" in ifaces and find_remote({path: ifaces}):
            asyncio.get_event_loop().create_task(watch_device(path))

    objmgr.on_interfaces_added(on_added)

    while True:
        if subscribed_path is None:
            await try_subscribe()
            await asyncio.sleep(10)
        else:
            await asyncio.sleep(30)


async def run():
    cfg = load_config()
    on_notify = make_notify_handler(cfg["buttons"], make_key_presser())
    if platform.system() == "Linux":
        await run_bluez(cfg, on_notify)
    else:
        await run_mac(cfg, on_notify)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
