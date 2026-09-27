"""
Trajectory recording and playback.

Recording samples the follower's calibrated joint positions at 10 Hz straight
from the poll cache — no extra bus traffic — and writes a timestamped JSON file.
By default a take is a teleop demonstration: starting a recording also starts
leader->follower mirroring if it isn't already running, so the operator drives
the motion from the leader and the mirrored follower joints are what gets
sampled. Passing teleop=False instead leaves the follower to the operator, who
releases its torque and walks it through the motion by hand.

Playback owns the follower for its duration: it stops teleop mirroring (the two
loops would otherwise both write the arm at 50 Hz) and re-enables follower
torque, so a take still replays after a demo was given by hand. It interpolates
linearly between recorded frames at 50 Hz and sync-writes the whole arm each
step, clamped to the follower's calibrated travel so a recording made against a
different calibration cannot drive a joint into its end stop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.motors.tables import MOTOR_NAMES

logger = logging.getLogger(__name__)

RECORDINGS_DIR = Path(__file__).parent.parent.parent / "recordings"

RECORD_HZ = 10
RECORD_INTERVAL = 1.0 / RECORD_HZ
PLAYBACK_HZ = 50
PLAYBACK_INTERVAL = 1.0 / PLAYBACK_HZ

# A take whose widest joint swing is under this is almost certainly a mistake —
# the follower was holding position the whole time (teleop not mirroring, torque
# not released). Saved anyway, but flagged so it doesn't fail silently.
STATIC_SPAN_DEG = 1.5


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or "take"


def _safe_id(rec_id: str) -> str:
    """Strip anything that could walk out of the recordings directory."""
    return re.sub(r"[^A-Za-z0-9_.-]", "", rec_id or "")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class RecorderStatus:
    recording: bool = False
    playing: bool = False
    name: str | None = None
    frames: int = 0
    elapsed_s: float = 0.0
    play_id: str | None = None
    play_progress: float = 0.0   # 0..1
    error: str | None = None


class RecordingController:
    """Owns the record loop and the playback loop; only one of each runs at a time."""

    def __init__(self, follower_controller, teleop_controller=None) -> None:
        self._follower = follower_controller
        # Optional: lets a recording default to a teleop demo and lets playback
        # take the follower back from the mirror loop. Wired up in main.py.
        self._teleop = teleop_controller
        self._rec_task: asyncio.Task | None = None
        self._play_task: asyncio.Task | None = None
        self._frames: list[dict] = []
        self._rec_name: str = ""
        self._rec_started: float = 0.0
        # Teleop speed limit to put back after a teleop-demo recording. The take
        # is captured at full tracking speed so the speed-limit lag between
        # leader and follower isn't baked into the frames.
        self._saved_speed_limit: float | None = None
        self.status = RecorderStatus()
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Snapshot for the follower's broadcast frame
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        return {"recording": self.status.recording, "playing": self.status.playing}

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    async def start_recording(self, name: str, teleop: bool = True) -> None:
        if self.status.recording:
            raise RuntimeError("Already recording")
        if self._play_task:
            raise RuntimeError("Stop playback before recording")
        if not self._follower.status.connected:
            raise RuntimeError("Follower arm not connected")

        # Default: the take is a teleop demonstration. Start mirroring if it
        # isn't already running so the sampled follower joints actually move.
        # start_mirroring raises a clear RuntimeError if the leader isn't
        # connected or either arm isn't calibrated.
        if teleop and self._teleop is not None:
            if not self._teleop.status.mirroring:
                await self._teleop.start_mirroring()
            # Record at full tracking speed. Under the normal speed limit the
            # follower trails the leader, and that lag would be recorded as part
            # of the trajectory. Restored in stop_recording.
            self._saved_speed_limit = self._teleop.status.speed_limit
            self._teleop.set_speed_limit(1.0)

        self._frames = []
        self._rec_name = (name or "").strip() or "untitled take"
        self._rec_started = time.monotonic()
        self.status = RecorderStatus(recording=True, name=self._rec_name)
        loop = asyncio.get_event_loop()
        self._rec_task = loop.create_task(self._record_loop())
        logger.info("Recording '%s' started", self._rec_name)

    async def _record_loop(self) -> None:
        try:
            while True:
                t = time.monotonic() - self._rec_started
                positions = self._follower.last_positions
                frame = {"t": round(t, 3)}
                for n in MOTOR_NAMES:
                    if n in positions:
                        frame[n] = round(positions[n], 2)
                if len(frame) > 1:
                    self._frames.append(frame)
                self.status.frames = len(self._frames)
                self.status.elapsed_s = round(t, 1)
                await asyncio.sleep(RECORD_INTERVAL)
        except asyncio.CancelledError:
            raise

    async def stop_recording(self) -> dict | None:
        """Cancel the record loop and save what was captured. None if empty."""
        task = self._rec_task
        self._rec_task = None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.status.recording = False

        # Put the operator's teleop speed limit back, whatever the outcome.
        if self._saved_speed_limit is not None and self._teleop is not None:
            self._teleop.set_speed_limit(self._saved_speed_limit)
        self._saved_speed_limit = None

        if not self._frames:
            logger.info("Recording '%s' captured no frames — nothing saved", self._rec_name)
            return None

        duration = self._frames[-1]["t"]
        span = self._max_joint_span()
        warning: str | None = None
        if span < STATIC_SPAN_DEG:
            warning = (
                f"Take saved, but the arm barely moved ({span:.1f}° on its "
                f"widest joint). Was the leader driving the follower? Start teleop "
                f"mirroring, or record with 'by hand' and release the follower's torque."
            )
            logger.warning("Recording '%s': %s", self._rec_name, warning)
        self.status.error = warning

        rec_id = f"{_slug(self._rec_name)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        doc = {
            "id": rec_id,
            "name": self._rec_name,
            "created_at": _utc_now(),
            "duration_s": round(duration, 2),
            "hz": RECORD_HZ,
            "frames": self._frames,
        }
        (RECORDINGS_DIR / f"{rec_id}.json").write_text(json.dumps(doc, indent=2))
        logger.info("Saved recording %s (%d frames, %.1fs, span %.1f°)",
                    rec_id, len(self._frames), duration, span)
        self._frames = []
        out = {k: doc[k] for k in ("id", "name", "created_at", "duration_s", "hz")}
        if warning:
            out["warning"] = warning
        return out

    def _max_joint_span(self) -> float:
        """Widest single-joint swing across the whole take, in degrees."""
        widest = 0.0
        for n in MOTOR_NAMES:
            vals = [f[n] for f in self._frames if n in f]
            if vals:
                widest = max(widest, max(vals) - min(vals))
        return widest

    # ------------------------------------------------------------------
    # Listing / deletion
    # ------------------------------------------------------------------

    @staticmethod
    def list_recordings() -> list[dict]:
        if not RECORDINGS_DIR.exists():
            return []
        out: list[dict] = []
        for f in RECORDINGS_DIR.glob("*.json"):
            try:
                d = json.loads(f.read_text())
            except Exception as exc:
                logger.warning("Skipping unreadable recording %s: %s", f.name, exc)
                continue
            out.append({
                "id": d.get("id", f.stem),
                "name": d.get("name", f.stem),
                "created_at": d.get("created_at"),
                "duration_s": d.get("duration_s", 0),
                "hz": d.get("hz", RECORD_HZ),
                "frames": len(d.get("frames", [])),
            })
        out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return out

    @staticmethod
    def _path(rec_id: str) -> Path:
        return RECORDINGS_DIR / f"{_safe_id(rec_id)}.json"

    def delete_recording(self, rec_id: str) -> bool:
        p = self._path(rec_id)
        if p.exists():
            p.unlink()
            logger.info("Deleted recording %s", rec_id)
            return True
        return False

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    async def play(self, rec_id: str, speed: float = 1.0) -> None:
        if self.status.recording:
            raise RuntimeError("Stop recording before playback")
        if self._play_task:
            raise RuntimeError("Already playing a recording")
        if not self._follower.status.connected:
            raise RuntimeError("Follower arm not connected")

        p = self._path(rec_id)
        if not p.exists():
            raise RuntimeError(f"Recording '{rec_id}' not found")
        frames = json.loads(p.read_text()).get("frames", [])
        if len(frames) < 2:
            raise RuntimeError("Recording has too few frames to play")

        speed = max(0.1, min(4.0, float(speed)))

        # Playback drives the follower for its whole duration. Teleop's 50 Hz
        # loop would fight the 50 Hz playback loop for the same arm, so hand the
        # follower over cleanly, then make sure it is actually holding torque —
        # a demo given by hand leaves it released.
        if self._teleop is not None and self._teleop.status.mirroring:
            logger.info("Stopping teleop mirroring for playback")
            await self._teleop.stop_mirroring()
        bus = getattr(self._follower, "_bus", None)
        if bus is not None:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, bus.set_torque_all, True)

        self.status.playing = True
        self.status.play_id = rec_id
        self.status.play_progress = 0.0
        self.status.error = None
        loop = asyncio.get_event_loop()
        self._play_task = loop.create_task(self._play_loop(frames, speed))
        logger.info("Playing %s at %.2fx", rec_id, speed)

    async def _play_loop(self, frames: list[dict], speed: float) -> None:
        cal = self._follower._calibration
        names = [n for n in MOTOR_NAMES if n in frames[0]]
        t_end = frames[-1]["t"]
        start = time.monotonic()
        idx = 0
        try:
            while True:
                pt = (time.monotonic() - start) * speed
                if pt >= t_end:
                    await self._follower.set_positions(
                        self._clamp({n: frames[-1][n] for n in names}, cal))
                    break
                while idx < len(frames) - 2 and frames[idx + 1]["t"] < pt:
                    idx += 1
                a, b = frames[idx], frames[idx + 1]
                span = b["t"] - a["t"]
                f = 0.0 if span <= 0 else (pt - a["t"]) / span
                cmd = {n: a[n] + (b[n] - a[n]) * f for n in names}
                await self._follower.set_positions(self._clamp(cmd, cal))
                self.status.play_progress = round(pt / t_end, 3)
                await asyncio.sleep(PLAYBACK_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Playback error: %s", exc)
            self.status.error = str(exc)
        finally:
            self.status.playing = False
            self.status.play_id = None
            self.status.play_progress = 1.0
            self._play_task = None
            logger.info("Playback finished")

    @staticmethod
    def _clamp(cmd: dict[str, float], cal) -> dict[str, float]:
        out: dict[str, float] = {}
        for n, v in cmd.items():
            jc = cal.joints.get(n) if cal else None
            if jc:
                v = min(max(v, jc.min_pos), jc.max_pos)
            out[n] = v
        return out

    async def stop_play(self) -> None:
        task = self._play_task
        self._play_task = None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.status.playing = False
        self.status.play_id = None

    async def cancel_all(self) -> None:
        """E-stop hook: abort playback, finalize any in-progress recording."""
        await self.stop_play()
        if self._rec_task:
            await self.stop_recording()
