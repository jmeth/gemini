"""
Camera capture and MJPEG fan-out.

One capture thread per physical device, no matter how many people are watching:
the frame is encoded once and the same bytes go to every viewer. Opening a V4L2
device twice fails, so a second browser tab must never mean a second capture.

Devices are opened lazily on the first viewer and released a few seconds after
the last one leaves, which keeps them free for enumeration without reopening on
every mode switch.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time

import cv2

from backend.vision.devices import device_path
from backend.vision.detector import detector

logger = logging.getLogger(__name__)

# Modest defaults: a Pi 5 re-encodes every frame, and the arm's poll loop has to
# keep its slice of the CPU.
DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 480
DEFAULT_FPS = 10
DEFAULT_QUALITY = 60

# How long a device stays open after the last viewer leaves.
LINGER_S = 8.0
# Give up on a device that has not produced a frame in this long.
STALL_S = 5.0

# Opening a V4L2 device takes the best part of a second. It gets its own small
# pool so it never competes with the motor bus for the default executor.
_open_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="camera-open"
)


class CameraStream:
    """One device: a grab thread, the latest encoded frame, and its viewers."""

    def __init__(self, index: int, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
                 fps=DEFAULT_FPS, quality=DEFAULT_QUALITY) -> None:
        self.index = index
        self.width, self.height, self.fps = width, height, fps
        self.quality = quality

        self._cap: cv2.VideoCapture | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

        self._frame: bytes | None = None
        self._seq = 0
        self._first_frame = threading.Event()
        self._last_frame_at = 0.0

        self._refs = 0
        self._linger: threading.Timer | None = None
        # Held across the device open so two simultaneous viewers cannot each
        # open the same device — V4L2 does not tolerate that.
        self._open_lock = threading.Lock()

        # Object detection. Off unless the operator turns it on for this camera;
        # while on, the loop refreshes at detect_fps (slow) so YOLO and the arm's
        # poll loop are not fighting for the Pi's CPU.
        self.detect_enabled = False
        self.detect_fps = 5
        self.detect_conf = 0.25
        self.detect_imgsz = 416
        self.detect_classes: list[int] | None = None
        self.last_detections: list[dict] = []
        # Set by the app: on_detections(index, detections) — thread-safe fan-out.
        self.on_detections = None
        self._detect_error: str | None = None

        # asyncio.Event per viewer, woken from the grab thread.
        self._waiters: set[asyncio.Event] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

        self.error: str | None = None
        self.actual: dict = {}

    # -- lifecycle ----------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    async def acquire(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register a viewer, opening the device if this is the first one."""
        with self._lock:
            self._refs += 1
            self._loop = loop
            if self._linger:
                self._linger.cancel()
                self._linger = None
            if self.is_open:
                return
        # Off the event loop: opening a camera blocks for ~a second.
        await loop.run_in_executor(_open_pool, self._open_once)

    def _open_once(self) -> None:
        """Open the device exactly once, however many viewers arrive together."""
        with self._open_lock:
            if self.is_open:
                return
            self._open()

    def release(self) -> None:
        with self._lock:
            self._refs = max(0, self._refs - 1)
            if self._refs > 0 or self._linger:
                return
            self._linger = threading.Timer(LINGER_S, self._linger_expired)
            self._linger.daemon = True
            self._linger.start()

    def _linger_expired(self) -> None:
        with self._lock:
            self._linger = None
            if self._refs > 0:
                return
        logger.info("Camera %d idle — releasing device", self.index)
        self.close()

    def _open(self) -> None:
        source = device_path(self.index)
        cap = cv2.VideoCapture(source, cv2.CAP_V4L2 if isinstance(source, str) else cv2.CAP_ANY)

        # Ask the webcam for MJPEG so the USB bus carries compressed frames.
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        # A one-frame buffer keeps the stream live rather than replaying stale frames.
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        if not cap.isOpened():
            cap.release()
            self.error = f"Could not open camera {self.index}"
            logger.warning(self.error)
            return

        self.actual = {
            "width":  int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps":    round(cap.get(cv2.CAP_PROP_FPS) or 0, 1),
        }
        self._cap = cap
        self.error = None
        self._stop.clear()
        self._first_frame.clear()
        self._thread = threading.Thread(
            target=self._grab_loop, name=f"camera-{self.index}", daemon=True
        )
        self._thread.start()
        logger.info(
            "Camera %d open at %dx%d %s fps",
            self.index, self.actual["width"], self.actual["height"], self.actual["fps"],
        )

    def close(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        if self._cap:
            self._cap.release()
            self._cap = None
        self._frame = None
        self._first_frame.clear()

    # -- capture ------------------------------------------------------------

    def _grab_loop(self) -> None:
        params = [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]
        failures = 0

        while not self._stop.is_set():
            started = time.monotonic()
            # Detecting throttles the whole loop to detect_fps: a pick-and-place
            # scene needs a few frames a second, and the slow rate is what keeps
            # inference off the arm's back. Recomputed each frame so the operator
            # can toggle it live without reopening the device.
            detecting = self.detect_enabled
            interval = 1.0 / max(1, self.detect_fps if detecting else self.fps)

            ok, frame = self._cap.read() if self._cap else (False, None)

            if not ok or frame is None:
                failures += 1
                if failures >= 30:
                    self.error = f"Camera {self.index} stopped delivering frames"
                    logger.warning(self.error)
                    break
                time.sleep(0.05)
                continue

            failures = 0

            if detecting:
                self._detect_into(frame)

            ok, buf = cv2.imencode(".jpg", frame, params)
            if not ok:
                continue

            # Encoded once here; every viewer sends these same bytes.
            self._frame = buf.tobytes()
            self._seq += 1
            self._last_frame_at = time.monotonic()
            self._first_frame.set()
            self._wake_waiters()

            elapsed = time.monotonic() - started
            time.sleep(max(0.0, interval - elapsed))

        self._first_frame.set()   # unblock anyone waiting on a dead camera

    def _detect_into(self, frame) -> None:
        """
        Run YOLO on a frame and draw the boxes onto it, in place. Loads the model
        on first use (a few seconds, once); a load failure disables detection for
        this camera and is reported rather than retried every frame.
        """
        if not detector.ready:
            if not detector.ensure_loaded():
                self._detect_error = detector.error
                self.detect_enabled = False   # stop hammering a broken load
                self._emit([])
                return
            self._detect_error = None

        dets = detector.track(
            frame,
            conf=self.detect_conf,
            imgsz=self.detect_imgsz,
            classes=self.detect_classes,
            camera_index=self.index,
        )
        detector.draw(frame, dets)
        self.last_detections = dets
        self._emit(dets)

    def _emit(self, detections: list[dict]) -> None:
        if self.on_detections is not None:
            try:
                self.on_detections(self.index, detections)
            except Exception:   # noqa: BLE001 — a viewer fan-out must not kill capture
                pass

    def _wake_waiters(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        for event in list(self._waiters):
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass   # loop shutting down

    # -- viewers ------------------------------------------------------------

    def snapshot(self, timeout: float = 4.0) -> bytes | None:
        """Latest frame, waiting for the first one if the device just opened."""
        self._first_frame.wait(timeout)
        return self._frame

    async def snapshot_async(self, loop: asyncio.AbstractEventLoop) -> bytes | None:
        """As snapshot(), off the event loop and off the motor bus's executor."""
        return await loop.run_in_executor(_open_pool, self.snapshot)

    async def frames(self):
        """Yield each newly encoded frame, skipping any the viewer was too slow for."""
        event = asyncio.Event()
        self._waiters.add(event)
        last_seq = -1
        try:
            while True:
                if not self.is_open:
                    return
                seq, frame = self._seq, self._frame
                if frame is not None and seq != last_seq:
                    last_seq = seq
                    yield frame
                else:
                    event.clear()
                    try:
                        await asyncio.wait_for(event.wait(), timeout=STALL_S)
                    except asyncio.TimeoutError:
                        if time.monotonic() - self._last_frame_at > STALL_S:
                            return   # camera went quiet; let the client retry
        finally:
            self._waiters.discard(event)

    def apply_detect(self, *, enabled: bool | None = None, fps: int | None = None,
                     conf: float | None = None, imgsz: int | None = None,
                     classes: list[int] | None = ...) -> None:
        """Change detection settings live. The grab loop picks them up next frame."""
        if fps is not None:
            self.detect_fps = max(1, int(fps))
        if conf is not None:
            self.detect_conf = float(conf)
        if imgsz is not None:
            self.detect_imgsz = int(imgsz)
        if classes is not ...:
            self.detect_classes = classes
        if enabled is not None:
            self.detect_enabled = bool(enabled)
            if not enabled:
                self.last_detections = []
                self._emit([])

    def status(self) -> dict:
        return {
            "index": self.index,
            "open": self.is_open,
            "viewers": self._refs,
            "error": self.error,
            "detecting": self.detect_enabled,
            "detect_fps": self.detect_fps,
            "objects": len(self.last_detections),
            "detect_error": self._detect_error,
            **self.actual,
        }


class CameraManager:
    """Keeps one CameraStream per device index."""

    def __init__(self) -> None:
        self._streams: dict[int, CameraStream] = {}
        self._lock = threading.Lock()

    def get(self, index: int) -> CameraStream:
        with self._lock:
            stream = self._streams.get(index)
            if stream is None:
                stream = CameraStream(index)
                self._streams[index] = stream
            return stream

    def set_detection(self, index: int | None, *, on_detections=None, **kw) -> None:
        """
        Route detection to a single camera. Only one stream may detect at once —
        the shared tracker keeps per-object state, so two would cross their tracks.
        Every other open stream is turned off. index=None turns detection off
        everywhere.
        """
        with self._lock:
            # Turn every other stream off first.
            for i, stream in self._streams.items():
                if i != index and stream.detect_enabled:
                    stream.apply_detect(enabled=False)
            if index is None:
                return
            # Ensure the target exists so the setting sticks even before a viewer
            # opens the device (detection then starts with the grab loop).
            target = self._streams.get(index)
            if target is None:
                target = CameraStream(index)
                self._streams[index] = target
            if on_detections is not None:
                target.on_detections = on_detections
            target.apply_detect(**kw)

    def get_detecting(self) -> "CameraStream | None":
        for stream in self._streams.values():
            if stream.detect_enabled:
                return stream
        return None

    def status(self) -> list[dict]:
        return [s.status() for s in self._streams.values()]

    def close_all(self) -> None:
        for stream in self._streams.values():
            if stream._linger:
                stream._linger.cancel()
            stream.close()
        self._streams.clear()
        logger.info("All cameras closed")


manager = CameraManager()
