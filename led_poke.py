#!/usr/bin/env python3
"""Probe the DragonRise arcade encoder's HID output report to see whether it
drives the button LEDs (or anything at all).

The device advertises a single 7-byte vendor-defined Output report. This script
writes a series of patterns to it, pausing between each so you can watch the
buttons and note any change (LEDs turning on/off, brightness, rumble, etc.).

Safe to interrupt at any time. Unplugging/replugging resets the device.
Needs root because /dev/hidraw0 is root-only:

    sudo ./led_poke.py            # defaults to /dev/hidraw0
    sudo ./led_poke.py /dev/hidraw0
"""

import os
import sys
import time

REPORT_LEN = 7  # from the HID descriptor: Report Size 8 x Report Count 7


def build_patterns():
    """A sweep designed to reveal any per-bit or per-byte LED mapping."""
    patterns = []

    patterns.append(("all OFF  (0x00 x7)", bytes(REPORT_LEN)))
    patterns.append(("all ON   (0xFF x7)", bytes([0xFF] * REPORT_LEN)))

    # Each byte fully on, one at a time — reveals which byte(s) map to LEDs.
    for i in range(REPORT_LEN):
        buf = bytearray(REPORT_LEN)
        buf[i] = 0xFF
        patterns.append((f"byte {i} = 0xFF (others 0)", bytes(buf)))

    # Walking single bit across all 56 bits — reveals per-LED addressing.
    for i in range(REPORT_LEN):
        for bit in range(8):
            buf = bytearray(REPORT_LEN)
            buf[i] = 1 << bit
            patterns.append((f"byte {i} bit {bit} (0x{1 << bit:02X})", bytes(buf)))

    patterns.append(("all OFF  (restore)", bytes(REPORT_LEN)))
    return patterns


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/dev/hidraw0"

    try:
        fd = os.open(path, os.O_WRONLY)
    except PermissionError:
        sys.exit(f"Permission denied on {path}. Re-run with sudo.")
    except FileNotFoundError:
        sys.exit(f"{path} not found. Check `ls /dev/hidraw*`.")

    patterns = build_patterns()
    print(f"Writing to {path}  ({REPORT_LEN}-byte output report)")
    print(f"{len(patterns)} patterns. Watch the buttons after each write.\n")
    print("Press Enter to advance, or type 'a' + Enter to auto-run (0.4s each),")
    print("or Ctrl-C to stop.\n")

    auto = False
    try:
        for label, data in patterns:
            try:
                n = os.write(fd, data)
                status = f"wrote {n} bytes"
            except OSError as e:
                status = f"WRITE FAILED: {e}"

            hexed = " ".join(f"{b:02X}" for b in data)
            print(f"[{hexed}]  {label:26s} -> {status}")

            if auto:
                time.sleep(0.4)
            else:
                resp = input("    (Enter = next, a = auto, q = quit) ").strip().lower()
                if resp == "q":
                    break
                if resp == "a":
                    auto = True
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        # Best-effort: leave the report cleared.
        try:
            os.write(fd, bytes(REPORT_LEN))
        except OSError:
            pass
        os.close(fd)
        print("\nDone. If nothing on the buttons changed across any pattern,")
        print("the LEDs are power-only (not software-controllable via this encoder).")


if __name__ == "__main__":
    main()
