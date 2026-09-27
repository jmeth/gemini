"""
YOLO26 object detection and tracking.

The current best fit for a Raspberry Pi 5 doing pick-and-place is Ultralytics
YOLO26 nano exported to NCNN: NMS-free end-to-end inference, ~67 ms/frame on the
Pi 5 CPU (NCNN is the fastest export backend there), and a built-in ByteTrack
tracker that hands back a stable integer id per object across frames.

Inference is the expensive part and it runs on the Pi no matter what, so the
operator turns detection on only when they need it (see the /vision endpoints),
and the camera loop it rides in refreshes slowly while it is on — a few frames a
second is plenty for a pick-and-place scene and leaves the arm's poll loop its
CPU.

Ultralytics (and the torch it pulls in) is heavy and is imported lazily, on the
first enable, so a rig that never turns detection on never pays for it — and the
backend still starts if the package is not installed. The NCNN weights are built
once from the .pt on first use and cached under models/.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Where the .pt is downloaded and the NCNN model is exported/cached.
MODELS_DIR = Path(__file__).parent.parent.parent / "models"

# Nano is the only scale that keeps a real-time-ish rate on the Pi 5 CPU.
DEFAULT_MODEL = "yolo26n"

# A box colour per COCO class, in BGR. Derived from the class id so the same
# class always gets the same colour without shipping an 80-entry table.
def _colour_for(cls_id: int) -> tuple[int, int, int]:
    hue = int((cls_id * 47) % 180)   # spread hues around the wheel
    px = cv2.cvtColor(np.array([[[hue, 200, 255]]], dtype=np.uint8),
                      cv2.COLOR_HSV2BGR)[0][0]
    return int(px[0]), int(px[1]), int(px[2])


class Detection(dict):
    """One tracked object. A plain dict so it serialises straight to JSON."""


class Detector:
    """
    One shared YOLO26 tracker. Bound to a single camera at a time: ByteTrack keeps
    per-object state between calls (persist=True), so feeding it two interleaved
    camera streams would cross their tracks. The camera manager enforces that only
    one stream detects at once.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self.model_name = model_name
        self._model = None
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()

        self.ready = False
        self.loading = False
        self.error: str | None = None
        self.names: dict[int, str] = {}
        self.backend = "ncnn"
        # Which camera the current track history belongs to; a change resets it.
        self._bound_index: int | None = None

    # -- model lifecycle ----------------------------------------------------

    @property
    def ncnn_path(self) -> Path:
        return MODELS_DIR / f"{self.model_name}_ncnn_model"

    @property
    def pt_path(self) -> Path:
        return MODELS_DIR / f"{self.model_name}.pt"

    def ensure_loaded(self) -> bool:
        """
        Load the NCNN model, building it from the .pt the first time. Safe to call
        from any thread; the heavy work happens once behind a lock. Returns True
        once the model is usable, False with self.error set otherwise.
        """
        if self.ready:
            return True
        with self._load_lock:
            if self.ready:
                return True
            self.loading = True
            self.error = None
            try:
                self._load()
                self.ready = True
                return True
            except Exception as exc:   # noqa: BLE001 — surfaced to the operator
                self.error = str(exc)
                logger.warning("Detector load failed: %s", exc)
                return False
            finally:
                self.loading = False

    def _load(self) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "ultralytics is not installed — run `pip install ultralytics` "
                "on the Pi to enable object detection"
            ) from exc

        MODELS_DIR.mkdir(parents=True, exist_ok=True)

        if not self.ncnn_path.exists():
            # Build the NCNN model from the PyTorch weights once. Ultralytics
            # downloads the .pt to this path if it is missing, then writes the
            # exported model next to it. Needs torch + network the first time.
            logger.info("Exporting %s to NCNN (first run) …", self.model_name)
            pt = YOLO(str(self.pt_path))
            pt.export(format="ncnn", imgsz=640)
            if not self.ncnn_path.exists():
                raise RuntimeError(
                    f"NCNN export did not produce {self.ncnn_path.name}"
                )
            logger.info("NCNN model ready at %s", self.ncnn_path)

        self._model = YOLO(str(self.ncnn_path), task="detect")
        self.names = dict(self._model.names)

    # -- inference ----------------------------------------------------------

    def track(
        self,
        frame,
        conf: float = 0.25,
        imgsz: int = 416,
        classes: list[int] | None = None,
        camera_index: int | None = None,
    ) -> list[Detection]:
        """
        Run one tracked inference on a BGR frame and return the objects found.

        Returns [] (not an error) when the model is not ready or the frame has no
        objects. Coordinates are in the frame's own pixel space.
        """
        if not self.ready or self._model is None:
            return []

        with self._lock:
            # A new source of frames must not inherit the previous camera's track
            # history, or ids jump. Reset the tracker when the bound camera changes.
            persist = camera_index == self._bound_index
            self._bound_index = camera_index
            try:
                results = self._model.track(
                    frame,
                    persist=persist,
                    conf=conf,
                    imgsz=imgsz,
                    classes=classes,
                    tracker="bytetrack.yaml",
                    verbose=False,
                )
            except Exception as exc:   # noqa: BLE001
                self.error = f"inference failed: {exc}"
                logger.warning("Detector inference failed: %s", exc)
                return []

        if not results:
            return []
        return self._to_detections(results[0])

    def _to_detections(self, result) -> list[Detection]:
        boxes = getattr(result, "boxes", None)
        if boxes is None or boxes.xyxy is None:
            return []

        xyxy = boxes.xyxy.tolist()
        confs = boxes.conf.tolist() if boxes.conf is not None else []
        clss = boxes.cls.tolist() if boxes.cls is not None else []
        ids = boxes.id.tolist() if getattr(boxes, "id", None) is not None else []

        out: list[Detection] = []
        for i, box in enumerate(xyxy):
            x1, y1, x2, y2 = (int(round(v)) for v in box)
            cls_id = int(clss[i]) if i < len(clss) else -1
            out.append(Detection(
                id=int(ids[i]) if i < len(ids) else None,
                cls=cls_id,
                label=self.names.get(cls_id, str(cls_id)),
                conf=round(float(confs[i]), 3) if i < len(confs) else None,
                box=[x1, y1, x2, y2],
                center=[(x1 + x2) // 2, (y1 + y2) // 2],
            ))
        return out

    # -- drawing ------------------------------------------------------------

    @staticmethod
    def draw(frame, detections: list[Detection]) -> None:
        """Draw boxes, labels and track ids onto a BGR frame, in place."""
        for det in detections:
            x1, y1, x2, y2 = det["box"]
            colour = _colour_for(det["cls"] if det["cls"] >= 0 else 0)
            cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 2)

            tid = det.get("id")
            conf = det.get("conf")
            tag = det["label"]
            if tid is not None:
                tag = f"#{tid} {tag}"
            if conf is not None:
                tag = f"{tag} {conf:.2f}"

            (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            ly = max(0, y1 - th - 6)
            cv2.rectangle(frame, (x1, ly), (x1 + tw + 6, ly + th + 6), colour, -1)
            cv2.putText(
                frame, tag, (x1 + 3, ly + th + 1),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA,
            )

    def status(self) -> dict:
        return {
            "model": self.model_name,
            "backend": self.backend,
            "ready": self.ready,
            "loading": self.loading,
            "error": self.error,
            "classes": len(self.names),
        }


# One detector for the whole process; loaded on first enable.
detector = Detector()
