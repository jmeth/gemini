#!/usr/bin/env python3
"""Monitor a USB arcade encoder (DragonRise / "Zero Delay" style) and print
every button and joystick event so you can map physical controls to IDs.

Uses the raw Linux joystick API (/dev/input/js*) — no external libraries,
no root needed (js0 is world-readable).

Usage:
    ./arcade_monitor.py            # auto-detect first /dev/input/js*
    ./arcade_monitor.py /dev/input/js0
"""

import os
import struct
import sys
import glob

# Joystick event packet: uint32 time, int16 value, uint8 type, uint8 number
EVENT_FORMAT = "IhBB"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

JS_EVENT_BUTTON = 0x01  # button pressed/released
JS_EVENT_AXIS = 0x02    # joystick moved
JS_EVENT_INIT = 0x80    # OR'd in on the initial synthetic events at open


def pick_device(argv):
    if len(argv) > 1:
        return argv[1]
    devices = sorted(glob.glob("/dev/input/js*"))
    if not devices:
        sys.exit("No /dev/input/js* device found. Is the controller plugged in?")
    return devices[0]


def main():
    path = pick_device(sys.argv)
    print(f"Monitoring {path}  (Ctrl-C to quit)\n")
    print("Press each arcade button and push the joystick in every direction.")
    print("Note the BUTTON number or AXIS number+value for each.\n")

    try:
        fd = open(path, "rb")
    except PermissionError:
        sys.exit(f"Permission denied reading {path}. Try: sudo {sys.argv[0]} {path}")
    except FileNotFoundError:
        sys.exit(f"{path} not found.")

    with fd:
        while True:
            packet = fd.read(EVENT_SIZE)
            if not packet:
                break
            _time, value, ev_type, number = struct.unpack(EVENT_FORMAT, packet)

            is_init = bool(ev_type & JS_EVENT_INIT)
            base_type = ev_type & ~JS_EVENT_INIT
            init_tag = "  (initial state)" if is_init else ""

            if base_type == JS_EVENT_BUTTON:
                state = "PRESSED " if value else "released"
                print(f"BUTTON {number:2d}  {state}{init_tag}")
            elif base_type == JS_EVENT_AXIS:
                # Arcade sticks report axes 0 (X) and 1 (Y); value ~ -32767..32767
                if value < -16000:
                    direction = "-  (left / up)"
                elif value > 16000:
                    direction = "+  (right / down)"
                else:
                    direction = "0  (centered)"
                print(f"AXIS   {number:2d}  value={value:6d}  {direction}{init_tag}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDone.")
