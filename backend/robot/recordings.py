"""
Trajectory recording and playback.

Recording samples the follower's calibrated joint positions at 10 Hz straight
from the poll cache — no extra bus traffic — and writes a timestamped JSON file.
By default a take is a teleop demonstration: starting a recording also starts
leader->follower mirroring if it isn't already running, so the operator drives
the motion from the leader and the mirrored follower joints are what gets
sampled. Passing teleop=False instead records both arms moved by hand: the follower's
torque is released for the take (and restored, holding its pose, when it stops)
and the leader is sampled alongside it.

Playback owns the follower for its duration: it stops teleop mirroring (the two
loops would otherwise both write the arm at 50 Hz) and re-enables follower
torque, so a take still replays after a demo was given by hand. It interpolates
linearly between recorded frames at 50 Hz and sync-writes the whole arm each
step, clamped to the follower's calibrated travel so a recording made against a
different calibration cannot drive a joint into its end stop.

So that takes chain together, playback skips the idle lead-in (it begins where
the motion does) and finishes by gliding the arm to its saved home pose.
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
from backend.robot.calibration import CalibrationStateMachine

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

# Playback skips the idle lead-in of a take: it starts where the first joint
# moves more than this far from the take's opening pose. Smaller than that is
# servo jitter, not the operator beginning the motion.
MOTION_START_DEG = 1.5
# Moves between takes — to the opening pose before a take, and home after it —
# run at this speed so a take can be chained to the next without a jolt. Short
# hops are floored at RAMP_MIN_S so they still look deliberate; anything closer
# than RAMP_SKIP_DEG is already there.
RAMP_DEG_PER_S = 90.0
RAMP_MIN_S = 0.4
RAMP_SKIP_DEG = 2.0


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
        # By-hand takes also capture the leader arm; its calibration is loaded
        # when the take starts. None for a teleop demo, where the leader is
        # already the source of the follower's motion.
        self._leader_cal = None
        # True while a by-hand take has the follower limp, so stopping knows to
        # take it back.
        self._follower_released = False
        # Where the leader was last commanded during playback.
        self._leader_now: dict[str, float] = {}
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

        # By hand: both arms are moved by the operator, so both are sampled.
        # Refuse up front rather than save a take that silently lacks the leader.
        self._leader_cal = None
        if not teleop and self._teleop is not None:
            if self._teleop.leader_bus is None or not self._teleop.status.leader_connected:
                raise RuntimeError(
                    "Leader arm not connected — connect it to record both arms by hand"
                )
            self._leader_cal = CalibrationStateMachine.load_calibration("leader")

        # By hand also means the follower must be free to move. Mirroring would
        # drive it against the operator's hands, and torque would hold it still.
        if not teleop:
            if self._teleop is not None and self._teleop.status.mirroring:
                await self._teleop.stop_mirroring()
            bus = getattr(self._follower, "_bus", None)
            if bus is not None:
                await asyncio.get_event_loop().run_in_executor(None, bus.set_torque_all, False)
                self._follower_released = True
                logger.info("Follower torque released for a by-hand take")

        self._frames = []
        self._rec_name = (name or "").strip() or "untitled take"
        self._rec_started = time.monotonic()
        self.status = RecorderStatus(recording=True, name=self._rec_name)
        loop = asyncio.get_event_loop()
        self._rec_task = loop.create_task(self._record_loop())
        logger.info("Recording '%s' started", self._rec_name)

    async def _read_leader(self) -> dict[str, float] | None:
        """Calibrated leader angles, or None if the read failed this frame."""
        bus = self._teleop.leader_bus if self._teleop is not None else None
        if bus is None or self._leader_cal is None:
            return None
        try:
            raw = await asyncio.get_event_loop().run_in_executor(None, bus.read_positions)
        except Exception as exc:
            logger.warning("Leader read failed while recording: %s", exc)
            return None
        out: dict[str, float] = {}
        for n, v in raw.items():
            jc = self._leader_cal.joints.get(n)
            out[n] = round(v + jc.zero_offset if jc else v, 2)
        return out

    async def _record_loop(self) -> None:
        try:
            while True:
                t = time.monotonic() - self._rec_started
                positions = self._follower.last_positions
                frame = {"t": round(t, 3)}
                for n in MOTOR_NAMES:
                    if n in positions:
                        frame[n] = round(positions[n], 2)
                if self._leader_cal is not None:
                    leader = await self._read_leader()
                    if leader:
                        frame["leader"] = leader
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

        await self._reengage_follower()

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
                f"widest joint). Was the leader driving the follower, or were the "
                f"arms actually being moved by hand?"
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
            "arms": ["follower", "leader"] if self._leader_cal is not None else ["follower"],
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

    async def _reengage_follower(self) -> None:
        """
        Take the follower back after a by-hand take. Its goal register still
        holds the pose from before it was released, so turning torque on cold
        would snap it there. Command where it is now first, then hold.
        """
        if not self._follower_released:
            return
        self._follower_released = False
        bus = getattr(self._follower, "_bus", None)
        if bus is None:
            return
        try:
            if self._follower.last_positions:
                await self._follower.set_positions(dict(self._follower.last_positions))
            await asyncio.get_event_loop().run_in_executor(None, bus.set_torque_all, True)
            logger.info("Follower torque restored after a by-hand take")
        except Exception as exc:
            logger.warning("Could not restore follower torque: %s", exc)

    def _max_joint_span(self) -> float:
        """Widest single-joint swing across the whole take, in degrees."""
        widest = 0.0
        for n in MOTOR_NAMES:
            vals = [f[n] for f in self._frames if n in f]
            if vals:
                widest = max(widest, max(vals) - min(vals))
            lvals = [f["leader"][n] for f in self._frames if n in f.get("leader", {})]
            if lvals:
                widest = max(widest, max(lvals) - min(lvals))
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
                "leader": bool(d.get("frames")) and "leader" in d["frames"][0],
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

    async def play(self, rec_id: str, speed: float = 1.0, trim_start: bool = True,
                   return_home: bool = True) -> None:
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

        # A take with leader frames drives both arms — if the leader is there to
        # drive. A take recorded with the leader unplugged elsewhere is still a
        # perfectly good follower take, so say so and carry on.
        use_leader = all("leader" in f for f in frames)
        leader_warning: str | None = None
        if use_leader and (self._teleop is None or self._teleop.leader_bus is None
                           or not self._teleop.status.leader_connected):
            use_leader = False
            leader_warning = "Leader arm not connected — played the follower only"

        # Playback drives the follower for its whole duration. Teleop's 50 Hz
        # loop would fight the 50 Hz playback loop for the same arm, so hand the
        # follower over cleanly, then make sure it is actually holding torque —
        # a demo given by hand leaves it released.
        if self._teleop is not None and self._teleop.status.mirroring:
            logger.info("Stopping teleop mirroring for playback")
            await self._teleop.stop_mirroring()
        bus = getattr(self._follower, "_bus", None)
        loop = asyncio.get_event_loop()
        if bus is not None:
            await loop.run_in_executor(None, bus.set_torque_all, True)

        if use_leader:
            # The leader's goal register holds whatever it was last told, so
            # turning torque on cold can throw the arm. Hold where it is first.
            try:
                await loop.run_in_executor(None, self._hold_and_energise_leader)
            except Exception as exc:
                use_leader = False
                leader_warning = f"Could not power the leader ({exc}) — played the follower only"
                logger.warning("%s", leader_warning)

        self.status.playing = True
        self.status.play_id = rec_id
        self.status.play_progress = 0.0
        self.status.error = leader_warning
        self._play_task = loop.create_task(
            self._play_loop(frames, speed, trim_start, return_home, use_leader))
        logger.info("Playing %s at %.2fx (trim_start=%s, return_home=%s, leader=%s)",
                    rec_id, speed, trim_start, return_home, use_leader)

    def _hold_and_energise_leader(self) -> None:
        """Command the leader to its current pose, then turn its torque on."""
        bus = self._teleop.leader_bus
        self._leader_cal = CalibrationStateMachine.load_calibration("leader")
        raw = bus.read_positions()
        if not raw:
            raise RuntimeError("no reading from the leader")
        bus.write_positions(raw)
        bus.set_torque_all(True)
        self._leader_now = {
            n: v + (self._leader_cal.joints[n].zero_offset if n in self._leader_cal.joints else 0.0)
            for n, v in raw.items()
        }

    def _release_leader(self) -> None:
        """Back to passive, so the leader can be moved by hand for teleop."""
        bus = self._teleop.leader_bus if self._teleop is not None else None
        if bus is not None and bus.is_connected:
            try:
                bus.set_torque_all(False)
            except Exception as exc:
                logger.warning("Could not release the leader's torque: %s", exc)

    async def _leader_write(self, cmd: dict[str, float]) -> None:
        cal = self._leader_cal
        clamped = self._clamp(cmd, cal)
        raw = {n: v - (cal.joints[n].zero_offset if n in cal.joints else 0.0)
               for n, v in clamped.items()}
        self._leader_now.update(clamped)
        bus = self._teleop.leader_bus
        await asyncio.get_event_loop().run_in_executor(None, bus.write_positions, raw)

    @staticmethod
    def _motion_start(frames: list[dict], names: list[str]) -> int:
        """
        Index of the last frame at rest before the take starts moving, so the
        interpolation begins from the opening pose. 0 if it moves immediately
        or never moves. Either arm's motion counts.
        """
        first = frames[0]
        for i, f in enumerate(frames):
            moved = any(abs(f[n] - first[n]) > MOTION_START_DEG for n in names)
            if not moved and "leader" in f and "leader" in first:
                moved = any(abs(f["leader"][n] - first["leader"][n]) > MOTION_START_DEG
                            for n in f["leader"] if n in first["leader"])
            if moved:
                return max(0, i - 1)
        return 0

    async def _ramp_to(self, target: dict[str, float], cal,
                       leader_target: dict[str, float] | None = None) -> None:
        """Glide both arms from where they are now to the targets, at a bounded speed."""
        current = {n: self._follower.last_positions.get(n) for n in target}
        known = {n: v for n, v in current.items() if v is not None}
        lknown = {n: self._leader_now[n] for n in (leader_target or {}) if n in self._leader_now}
        if not known and not lknown:
            await self._follower.set_positions(self._clamp(target, cal))
            return
        dist = max([abs(target[n] - v) for n, v in known.items()]
                   + [abs(leader_target[n] - v) for n, v in lknown.items()] + [0.0])
        if dist < RAMP_SKIP_DEG:
            return
        duration = max(RAMP_MIN_S, dist / RAMP_DEG_PER_S)
        start = time.monotonic()
        while True:
            f = min(1.0, (time.monotonic() - start) / duration)
            f = f * f * (3.0 - 2.0 * f)   # smoothstep: no jolt at either end
            cmd = {n: (known[n] + (target[n] - known[n]) * f) if n in known else target[n]
                   for n in target}
            await self._follower.set_positions(self._clamp(cmd, cal))
            if leader_target:
                await self._leader_write({
                    n: (lknown[n] + (leader_target[n] - lknown[n]) * f) if n in lknown
                    else leader_target[n]
                    for n in leader_target})
            if f >= 1.0:
                return
            await asyncio.sleep(PLAYBACK_INTERVAL)

    async def _play_loop(self, frames: list[dict], speed: float,
                         trim_start: bool = True, return_home: bool = True,
                         use_leader: bool = False) -> None:
        cal = self._follower._calibration
        names = [n for n in MOTOR_NAMES if n in frames[0]]
        lnames = [n for n in MOTOR_NAMES if n in frames[0].get("leader", {})] if use_leader else []
        t_end = frames[-1]["t"]
        idx = self._motion_start(frames, names) if trim_start else 0
        t_begin = frames[idx]["t"]
        total = max(t_end - t_begin, 1e-6)
        try:
            # Get to the opening pose first. After the previous take's return
            # home this is already true and costs nothing.
            await self._ramp_to(
                {n: frames[idx][n] for n in names}, cal,
                {n: frames[idx]["leader"][n] for n in lnames} if use_leader else None)

            start = time.monotonic()
            while True:
                pt = t_begin + (time.monotonic() - start) * speed
                if pt >= t_end:
                    await self._follower.set_positions(
                        self._clamp({n: frames[-1][n] for n in names}, cal))
                    if use_leader:
                        await self._leader_write({n: frames[-1]["leader"][n] for n in lnames})
                    break
                while idx < len(frames) - 2 and frames[idx + 1]["t"] < pt:
                    idx += 1
                a, b = frames[idx], frames[idx + 1]
                span = b["t"] - a["t"]
                f = 0.0 if span <= 0 else (pt - a["t"]) / span
                cmd = {n: a[n] + (b[n] - a[n]) * f for n in names}
                await self._follower.set_positions(self._clamp(cmd, cal))
                if use_leader:
                    await self._leader_write({
                        n: a["leader"][n] + (b["leader"][n] - a["leader"][n]) * f
                        for n in lnames})
                self.status.play_progress = round((pt - t_begin) / total, 3)
                await asyncio.sleep(PLAYBACK_INTERVAL)

            if return_home and cal and cal.home_positions:
                # Playing stays true until home is reached, so a chained take
                # or voice command cannot start mid-glide.
                self.status.play_progress = 1.0
                leader_home = None
                if use_leader and self._leader_cal and self._leader_cal.home_positions:
                    leader_home = {n: v for n, v in self._leader_cal.home_positions.items()
                                   if n in MOTOR_NAMES}
                await self._ramp_to(
                    {n: v for n, v in cal.home_positions.items() if n in MOTOR_NAMES},
                    cal, leader_home)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Playback error: %s", exc)
            self.status.error = str(exc)
        finally:
            if use_leader:
                self._release_leader()
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
