"""Discover available serial ports and USB cameras on the host system."""

from __future__ import annotations

import platform
import serial.tools.list_ports

from backend.vision.devices import list_cameras as _list_cameras


def list_serial_ports() -> list[str]:
    """Return all serial port device paths."""
    ports = serial.tools.list_ports.comports()
    result = [p.device for p in ports]
    # On macOS, prefer /dev/tty.* over /dev/cu.* (same device, different behaviour)
    if platform.system() == "Darwin":
        result = [p for p in result if "/dev/tty." in p] or result
    return sorted(result)


def list_cameras(refresh: bool = False) -> list[dict]:
    """
    Video capture devices, as {index, path, label, driver}.

    Delegates to backend.vision.devices, which asks V4L2 what each node is
    rather than opening every index and hoping. Metadata nodes and the Pi's
    codec/ISP nodes open fine but never produce a frame, so they are excluded.
    """
    return _list_cameras(refresh=refresh)
