"""Persistent settings — loaded from settings.json, updated via POST /settings."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).parent.parent / "settings.json"


class Settings(BaseModel):
    follower_port: Optional[str] = None   # e.g. "/dev/tty.usbmodem..."
    leader_port:   Optional[str] = None
    camera_overhead: Optional[int] = None  # OpenCV device index
    camera_ee:       Optional[int] = None  # end-effector camera
    # Capture tuning — modest by default so the arm's poll loop keeps its CPU
    camera_width:   int = 640
    camera_height:  int = 480
    camera_fps:     int = 15
    camera_quality: int = 70   # JPEG quality, 1-100
    # Voice commands: "browser" runs recognition in the client (Web Speech API);
    # "backend" runs it on this host with Vosk, fed by a local ALSA microphone.
    voice_source: str = "browser"
    voice_mic: Optional[str] = None   # ALSA device for arecord -D, e.g. "plughw:1,0"
    # Restrict the backend recogniser to the command trigger phrases (Vosk
    # grammar). Far more accurate for command-and-control; turn off for
    # open-vocabulary dictation.
    voice_grammar: bool = True
    # Object detection (YOLO26n) — off by default; inference is the Pi's most
    # expensive job, so the operator turns it on only for pick-and-place.
    detect_enabled: bool = False
    detect_camera: Optional[int] = None   # camera index that runs detection; None → overhead
    detect_fps: int = 5                    # slow refresh while detecting saves CPU
    detect_conf: float = 0.25              # confidence floor for a reported box
    detect_imgsz: int = 416                # inference size; smaller = faster, less small-object recall
    detect_classes: Optional[list[int]] = None   # COCO class filter; None = every class


_current: Settings = Settings()


def load() -> Settings:
    global _current
    if SETTINGS_PATH.exists():
        try:
            _current = Settings.model_validate_json(SETTINGS_PATH.read_text())
        except Exception as exc:
            logger.warning("Could not load settings.json: %s", exc)
    return _current


def get() -> Settings:
    return _current


def update(patch: dict) -> Settings:
    global _current
    data = _current.model_dump()
    data.update({k: v for k, v in patch.items() if k in data})
    _current = Settings.model_validate(data)
    SETTINGS_PATH.write_text(_current.model_dump_json(indent=2))
    return _current
