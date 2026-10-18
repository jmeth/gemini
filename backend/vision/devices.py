"""
Camera enumeration.

Probing OpenCV indices blindly reports things that are not cameras. A UVC webcam
publishes a metadata node next to its capture node (the C920 gives /dev/video0 for
video and /dev/video1 for metadata), and a Raspberry Pi publishes a dozen codec and
ISP nodes on top. Both open cleanly and neither ever yields a frame, so on Linux we
ask V4L2 what each node actually is instead of guessing.
"""

from __future__ import annotations

import ctypes
import fcntl
import glob
import logging
import platform
import re
import threading
import time

logger = logging.getLogger(__name__)

# VIDIOC_QUERYCAP = _IOR('V', 0, struct v4l2_capability) — the struct is 104 bytes
VIDIOC_QUERYCAP = 0x80685600

V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_CAP_META_CAPTURE  = 0x00800000
V4L2_CAP_DEVICE_CAPS   = 0x80000000


class _V4L2Capability(ctypes.Structure):
    _fields_ = [
        ("driver",       ctypes.c_char * 16),
        ("card",         ctypes.c_char * 32),
        ("bus_info",     ctypes.c_char * 32),
        ("version",      ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("device_caps",  ctypes.c_uint32),
        ("reserved",     ctypes.c_uint32 * 3),
    ]


def _index_of(path: str) -> int:
    match = re.search(r"(\d+)$", path)
    return int(match.group(1)) if match else -1


def _query(path: str) -> dict | None:
    """Ask a V4L2 node what it is. None if it is not a video capture device."""
    try:
        with open(path, "rb", buffering=0) as fh:
            cap = _V4L2Capability()
            fcntl.ioctl(fh, VIDIOC_QUERYCAP, cap)
    except Exception:
        return None   # busy, permission denied, or not a V4L2 node

    caps = cap.device_caps if cap.capabilities & V4L2_CAP_DEVICE_CAPS else cap.capabilities
    if not caps & V4L2_CAP_VIDEO_CAPTURE:
        return None   # metadata node, codec node, or output-only device

    return {
        "index":  _index_of(path),
        "path":   path,
        "label":  cap.card.decode(errors="replace").strip() or path,
        "driver": cap.driver.decode(errors="replace").strip(),
    }


def _list_linux() -> list[dict]:
    found = []
    seen_labels: dict[str, int] = {}
    for path in sorted(glob.glob("/dev/video*"), key=_index_of):
        info = _query(path)
        if not info:
            continue
        # Two capture nodes with the same card name are two physical cameras;
        # number them so they can be told apart in the picker.
        label = info["label"]
        seen_labels[label] = seen_labels.get(label, 0) + 1
        if seen_labels[label] > 1:
            info["label"] = f"{label} #{seen_labels[label]}"
        found.append(info)
    return found


# Set by the app: returns the camera indices that are streaming right now.
# Probing opens a device, and opening one that is already capturing is at best
# wasted work and at worst disturbs the live stream, so those are not probed.
busy_indices = None

# Cameras need a moment after opening before the first frame arrives — a single
# read() straight after open() fails on a perfectly good camera, which made it
# look as if the device did not exist.
PROBE_WARMUP_READS = 10
PROBE_WARMUP_S = 0.1


def _list_probing() -> list[dict]:
    """macOS and anything else without V4L2: fall back to opening indices."""
    import cv2

    busy = set(busy_indices()) if busy_indices else set()
    found = []
    for idx in range(6):
        entry = {"index": idx, "path": str(idx), "label": f"Camera {idx}", "driver": ""}
        if idx in busy:
            found.append(entry)      # streaming, so it plainly exists
            continue
        cap = cv2.VideoCapture(idx)
        try:
            if not cap.isOpened():
                continue             # no such index
            for _ in range(PROBE_WARMUP_READS):
                if cap.read()[0]:
                    break
                time.sleep(PROBE_WARMUP_S)
            else:
                # The device opens but never yields a frame: present, but stuck or
                # held by another app. Say so rather than pretend it is not there.
                entry["label"] = f"Camera {idx} (not delivering frames)"
            found.append(entry)
        finally:
            cap.release()
    return found


# Enumeration is slow on macOS (it opens every index) and the header asks about
# camera validity every couple of seconds. Probing that often blocked the event
# loop for ~1 s per poll and stalled teleop, so results are reused briefly.
CAMERA_CACHE_TTL = 30.0
_cache_lock = threading.Lock()
_cache: tuple[float, list[dict]] | None = None


def list_cameras(refresh: bool = False) -> list[dict]:
    """
    Video capture devices only, as {index, path, label, driver}.

    Cached for CAMERA_CACHE_TTL seconds; pass refresh=True (a user opening the
    camera picker) to rescan now. Blocking — call from a thread, not the event loop.
    """
    global _cache
    with _cache_lock:
        if not refresh and _cache and time.monotonic() - _cache[0] < CAMERA_CACHE_TTL:
            return list(_cache[1])
        try:
            found = _list_linux() if platform.system() == "Linux" else _list_probing()
        except Exception as exc:
            logger.warning("Camera enumeration failed: %s", exc)
            return list(_cache[1]) if _cache else []
        _cache = (time.monotonic(), found)
        return list(found)


def device_path(index: int) -> str | int:
    """What to hand OpenCV for a stored camera index."""
    return f"/dev/video{index}" if platform.system() == "Linux" else index


def is_capture_device(index: int) -> bool:
    """True if this index is still a real capture device."""
    return any(c["index"] == index for c in list_cameras())
