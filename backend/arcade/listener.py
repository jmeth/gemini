"""
Arcade-button input — a DragonRise-style USB encoder read over raw HID.

Raw HID (hidapi) rather than the Linux joystick device, so the same code runs on
macOS and the Pi. The encoder sends 8-byte reports:

    byte 0,1   stick X / Y   (0x00, 0x7f, 0xff)
    byte 2     floating analog axis — jitters constantly, ignored
    byte 5     high nibble = buttons 0-3 (low nibble is the hat switch)
    byte 6     buttons 4-11, one bit each

A press is the rising edge of a button bit. Reading happens on a thread because
hidapi blocks; presses are handed to the event loop, where the app acts on them.
The most recent press is also kept on `status` so the UI can "learn" a button:
press it, and the editor picks up whichever one just fired.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

VENDOR_ID = 0x0079
PRODUCT_ID = 0x0006

REPORT_LEN = 8
READ_TIMEOUT_MS = 100
RETRY_S = 2.0          # how often to look for the encoder while it is unplugged
COOLDOWN_S = 0.2       # ignore a button re-firing this soon (contact bounce)


def parse_buttons(report: list[int] | bytes) -> set[int]:
    """The set of pressed button indices (0-11) in one HID report."""
    if len(report) < 7:
        return set()
    pressed = {i for i in range(4) if report[5] & (0x10 << i)}
    pressed |= {4 + i for i in range(8) if report[6] & (1 << i)}
    return pressed


@dataclass
class ArcadeStatus:
    connected: bool = False
    device: str | None = None
    error: str | None = None
    last_button: int | None = None
    last_press_at: float = 0.0     # wall clock, 0 = never
    press_seq: int = 0             # bumps on every press, so a repeat is still seen


class ArcadeListener:
    def __init__(self, on_press: Callable[[int], Awaitable[None]]) -> None:
        self._on_press = on_press
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.status = ArcadeStatus()

    def start(self) -> None:
        if self._thread:
            return
        self._loop = asyncio.get_event_loop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="arcade", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            import hid
        except Exception as exc:
            self.status.error = f"hidapi is not installed: {exc}"
            logger.warning("Arcade input unavailable: %s", exc)
            return

        while not self._stop.is_set():
            dev = hid.device()
            try:
                dev.open(VENDOR_ID, PRODUCT_ID)
            except Exception as exc:
                self.status.connected = False
                self.status.device = None
                # Not plugged in is normal; only a failure to open is worth saying.
                present = bool(hid.enumerate(VENDOR_ID, PRODUCT_ID))
                self.status.error = str(exc) if present else None
                self._stop.wait(RETRY_S)
                continue

            self.status.connected = True
            self.status.device = (dev.get_product_string() or "Arcade encoder").strip()
            self.status.error = None
            logger.info("Arcade encoder connected: %s", self.status.device)
            try:
                self._read(dev)
            except Exception as exc:
                logger.warning("Arcade read failed, will reconnect: %s", exc)
                self.status.error = str(exc)
            finally:
                self.status.connected = False
                try:
                    dev.close()
                except Exception:
                    pass

    def _read(self, dev) -> None:
        held: set[int] = set()
        fired: dict[int, float] = {}
        while not self._stop.is_set():
            report = dev.read(REPORT_LEN, READ_TIMEOUT_MS)
            if not report:
                # An empty read is a quiet bus; a dead device raises instead.
                continue
            now = time.monotonic()
            pressed = parse_buttons(report)
            for button in sorted(pressed - held):
                if now - fired.get(button, -COOLDOWN_S) < COOLDOWN_S:
                    continue
                fired[button] = now
                self._emit(button)
            held = pressed

    def _emit(self, button: int) -> None:
        self.status.last_button = button
        self.status.last_press_at = time.time()
        self.status.press_seq += 1
        logger.info("Arcade button %d pressed", button)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._on_press(button), self._loop)
