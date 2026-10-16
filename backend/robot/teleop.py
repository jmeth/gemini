"""
Teleoperation: Leader arm controls follower arm in real-time.

Leader arm:
- Connected with torque disabled (passive, read-only)
- Read positions at 50Hz

Follower arm:
- Commanded to match leader positions
- Speed limit, calibrated-range clamping and a tracking-error cut-out applied

The loop also keeps a snapshot of what it last saw and last commanded. The
follower's 10 Hz broadcast picks that up so the UI can draw leader and follower
on the same track and show where they diverge.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from backend.motors.bus import FeetechBus
from backend.motors.tables import MOTOR_NAMES
from backend.robot.calibration import CalibrationStateMachine

logger = logging.getLogger(__name__)

TELEOP_HZ = 50
TELEOP_INTERVAL = 1.0 / TELEOP_HZ

# The cut-out is for a joint that is stuck, not one that is still catching up.
# At engage the two arms are in different poses, so a large error is normal and
# takes about a second to converge — tripping on size alone stops every run.
# A joint must stay past the threshold AND stop making progress for this long.
ERROR_TRIP_FRAMES = 50            # 1 s at 50 Hz
# A joint counts as stuck only if it has barely moved across the whole window.
# Being *behind* is not a fault: a servo tracking a moving command settles at a
# following error proportional to speed, and that error stays constant for as
# long as the operator keeps moving. Judging by error alone stopped every
# sustained movement.
STALL_MOVE_DEG = 2.0
ENGAGE_GRACE_S = 2.0              # no cut-out at all just after engaging


@dataclass
class TeleopStatus:
    leader_connected: bool = False
    mirroring: bool = False
    speed_limit: float = 0.3        # 0.0 to 1.0; conservative by default
    clutched: bool = False          # reading the leader, not driving the follower
    max_error: float = 10.0         # degrees; 0 disables the cut-out
    stop_on_error: bool = True
    # Map the leader's fraction of ITS travel onto the follower's travel, rather
    # than passing degrees straight through. The two arms are calibrated
    # separately and their recorded travels differ — the gripper by 31 degrees —
    # so a 1:1 mapping cannot reach the follower's extremes.
    match_ranges: bool = True
    error: str | None = None


@dataclass
class TeleopTelemetry:
    """What the loop last saw. Read by the follower broadcast, never mutated by it."""
    positions: dict[str, float] = field(default_factory=dict)  # calibrated leader angles
    targets: dict[str, float] = field(default_factory=dict)    # where the leader points, unlimited
    commands: dict[str, float] = field(default_factory=dict)   # what the follower was actually told
    clamped: list[str] = field(default_factory=list)           # leader beyond follower's range
    hz: float = 0.0
    dropped: int = 0
    # Worst cycle overrun seen recently, in ms. A 50 Hz loop has a
    # 20 ms budget; sustained overruns are what jerky motion looks like.
    jitter_ms: float = 0.0
    # Follower load per joint, so a stall reports whether the motor was straining.
    load: dict[str, float] = field(default_factory=dict)


class TeleopController:
    """
    Manages leader arm connection and position mirroring.
    Runs a high-frequency loop (50Hz) to read leader and command follower.
    """

    def __init__(self, follower_controller) -> None:
        self._follower = follower_controller
        self._leader_bus: FeetechBus | None = None
        self._task: asyncio.Task | None = None
        self.status = TeleopStatus()
        self.telemetry = TeleopTelemetry()
        self._leader_calibration = None
        self._follower_calibration = None
        # Per-joint clutch offset: subtracted from the leader angle before it is
        # sent on, so re-engaging after repositioning the leader doesn't jump.
        self._offsets: dict[str, float] = {name: 0.0 for name in MOTOR_NAMES}
        self._trip_count: dict[str, int] = {name: 0 for name in MOTOR_NAMES}
        # Where the joint was when it first went past the threshold, so we can
        # tell "moving but behind" from "not moving at all".
        self._trip_from: dict[str, float | None] = {name: None for name in MOTOR_NAMES}
        self._engaged_at: float = 0.0

    @property
    def leader_bus(self) -> FeetechBus | None:
        """The leader's bus, for callers that must address it directly."""
        return self._leader_bus

    async def connect_leader(self, port: str) -> None:
        """Connect to leader arm (torque disabled for passive reading)."""
        if self._leader_bus and self._leader_bus.is_connected:
            await self.disconnect_leader()

        self._leader_bus = FeetechBus(port)
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._leader_bus.connect)
            # Disable torque on all leader motors (passive mode)
            await loop.run_in_executor(None, self._leader_bus.set_torque_all, False)
            self.status.leader_connected = True
            self.status.error = None
            logger.info("Leader arm connected on %s (torque disabled)", port)
        except Exception as exc:
            self._leader_bus = None
            self.status.leader_connected = False
            self.status.error = str(exc)
            raise

    async def disconnect_leader(self) -> None:
        """Disconnect from leader arm."""
        if self._task:
            await self.stop_mirroring()

        if self._leader_bus:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._leader_bus.disconnect)
            self._leader_bus = None

        self.status.leader_connected = False
        self.telemetry = TeleopTelemetry()
        logger.info("Leader arm disconnected")

    def _check_calibrations(self) -> None:
        """Load and verify both leader and follower calibrations exist."""
        from backend.robot.calibration import find_calibration_file

        # find_calibration_file also accepts the pre-rename filename; the strict
        # name alone would reject an arm that is calibrated.
        missing = [
            arm for arm in ("leader", "follower")
            if find_calibration_file(arm) is None
        ]
        if missing:
            raise RuntimeError(
                f"Missing calibration for: {', '.join(missing)}. "
                f"Please calibrate both arms in the Calibrate wizard before using teleop."
            )

        self._leader_calibration = CalibrationStateMachine.load_calibration(arm_name="leader")
        self._follower_calibration = CalibrationStateMachine.load_calibration(arm_name="follower")
        logger.info("Loaded calibrations for leader and follower")

    async def start_mirroring(self) -> None:
        """Start 50Hz teleop loop: read leader → write follower."""
        if not self._leader_bus or not self._leader_bus.is_connected:
            raise RuntimeError("Leader arm not connected")

        if not self._follower.status.connected:
            raise RuntimeError("Follower arm not connected")

        # Check calibrations before starting
        self._check_calibrations()

        if self._task:
            return  # Already running

        self.status.mirroring = True
        self.status.clutched = False
        self.status.error = None
        self._reset_trip()
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._teleop_loop())
        logger.info("Teleop mirroring started at %d Hz", TELEOP_HZ)

    async def stop_mirroring(self) -> None:
        """Stop teleop loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        self.status.mirroring = False
        self.status.clutched = False
        self.telemetry.hz = 0.0
        logger.info("Teleop mirroring stopped")

    def set_speed_limit(self, limit: float) -> None:
        """Set speed limit (0.0 to 1.0)."""
        self.status.speed_limit = max(0.0, min(1.0, limit))

    def set_limits(self, max_error: float | None = None, stop_on_error: bool | None = None,
                   match_ranges: bool | None = None) -> None:
        """Configure the tracking-error cut-out and the leader->follower mapping."""
        if max_error is not None:
            self.status.max_error = max(0.0, float(max_error))
        if stop_on_error is not None:
            self.status.stop_on_error = bool(stop_on_error)
        if match_ranges is not None:
            self.status.match_ranges = bool(match_ranges)

    def _map_to_follower(self, name: str, leader_deg: float) -> float:
        """
        Leader angle -> follower angle.

        Both arms zero at the midpoint of their own recorded travel, but those
        travels are not the same, so equal degrees are not equal poses. Matching
        by fraction-of-travel makes the extremes line up: full leader deflection
        produces full follower deflection.
        """
        if not self.status.match_ranges:
            return leader_deg

        lcal = self._leader_calibration.joints.get(name) if self._leader_calibration else None
        fcal = self._follower_calibration.joints.get(name) if self._follower_calibration else None
        if not lcal or not fcal:
            return leader_deg

        lspan = lcal.max_pos - lcal.min_pos
        fspan = fcal.max_pos - fcal.min_pos
        if lspan <= 0 or fspan <= 0:
            return leader_deg

        fraction = (leader_deg - lcal.min_pos) / lspan
        return fcal.min_pos + fraction * fspan

    def set_clutch(self, engaged: bool) -> None:
        """
        Clutch in: keep reading the leader but stop driving the follower, so the
        leader can be repositioned to a comfortable pose.
        Clutch out: re-zero the offsets against where both arms now sit, so the
        follower picks up from its current position instead of snapping.
        """
        if engaged:
            self.status.clutched = True
            logger.info("Teleop clutched — follower held")
            return

        self._rezero_offsets()
        self._reset_trip()
        self.status.clutched = False
        logger.info("Teleop re-engaged with offsets %s", self._offsets)

    def _reset_trip(self) -> None:
        """Clear the cut-out and restart its grace period."""
        self._trip_count = {name: 0 for name in MOTOR_NAMES}
        self._trip_from = {name: None for name in MOTOR_NAMES}
        self._engaged_at = time.monotonic()

    def _rezero_offsets(self) -> None:
        """Offset = leader now − follower now, so the next frame is a no-op."""
        follower = self._follower.last_positions
        for name in MOTOR_NAMES:
            lead = self.telemetry.positions.get(name)
            foll = follower.get(name)
            if lead is not None and foll is not None:
                self._offsets[name] = lead - foll

    def clear_offsets(self) -> None:
        """Drop the clutch offsets — leader angles map straight through again."""
        self._offsets = {name: 0.0 for name in MOTOR_NAMES}
        logger.info("Teleop offsets cleared")

    def snapshot(self) -> dict | None:
        """
        Leader-arm view for the follower's broadcast frame. None when there is
        nothing to draw, which keeps the frame unchanged for non-teleop clients.
        """
        if not self.status.leader_connected or not self.telemetry.positions:
            return None
        return {
            "positions": {n: round(v, 2) for n, v in self.telemetry.positions.items()},
            "targets":   {n: round(v, 2) for n, v in self.telemetry.targets.items()},
            "commands":  {n: round(v, 2) for n, v in self.telemetry.commands.items()},
            "clamped":   self.telemetry.clamped,
            "mirroring": self.status.mirroring,
            "clutched":  self.status.clutched,
            "hz":        round(self.telemetry.hz, 1),
            "dropped":   self.telemetry.dropped,
            "jitter_ms": round(self.telemetry.jitter_ms, 1),
        }

    async def _teleop_loop(self) -> None:
        """High-frequency loop: read leader positions and command follower."""
        logger.info("Teleop loop running at %d Hz", TELEOP_HZ)
        loop = asyncio.get_event_loop()

        # Track previous commanded positions for speed limiting
        prev_positions: dict[str, float] = dict(self._follower.last_positions)
        self.telemetry.dropped = 0

        while True:
            t_start = time.monotonic()
            try:
                # Positions only — read_all would put four times the traffic on
                # this bus at 50 Hz, for load/voltage/temperature teleop never uses.
                leader_raw = await loop.run_in_executor(
                    None, self._leader_bus.read_positions)

                positions: dict[str, float] = {}
                targets: dict[str, float] = {}
                commands: dict[str, float] = {}
                clamped: list[str] = []

                for name in MOTOR_NAMES:
                    if name not in leader_raw:
                        continue

                    # Raw leader position → calibrated leader angle
                    leader_cal = self._leader_calibration.joints.get(name)
                    raw = leader_raw[name]
                    calibrated_pos = raw + leader_cal.zero_offset if leader_cal else raw
                    positions[name] = calibrated_pos

                    # Onto the follower's own travel, then the clutch offset
                    target = self._map_to_follower(name, calibrated_pos)
                    target -= self._offsets.get(name, 0.0)

                    # Clamp into the follower's own calibrated travel. The two arms
                    # are not calibrated identically, so the leader can ask for
                    # angles this joint cannot reach.
                    foll_cal = self._follower_calibration.joints.get(name)
                    if foll_cal:
                        limited = min(max(target, foll_cal.min_pos), foll_cal.max_pos)
                        if abs(limited - target) > 0.5:
                            clamped.append(name)
                        target = limited

                    targets[name] = target

                    if self.status.clutched:
                        # Reading only — hold the follower where it is.
                        prev_positions[name] = self._follower.last_positions.get(
                            name, prev_positions.get(name, target)
                        )
                        continue

                    # Apply speed limit (limit max change per frame)
                    # Ramp from the last commanded value. It is already bounded
                    # by the leader target, so it cannot run away. Clamping it
                    # against the follower's measured position instead would
                    # quantise a 50 Hz command stream to the 10 Hz poll rate and
                    # step the arm along in visible staircases.
                    # At 100% there is no limit: capping at 180 deg/s there still
                    # lagged any brisk hand movement.
                    if self.status.speed_limit < 1.0:
                        prev_pos = prev_positions.get(name, target)
                        max_change = 180.0 * self.status.speed_limit * TELEOP_INTERVAL
                        delta = target - prev_pos
                        if abs(delta) > max_change:
                            target = prev_pos + (max_change if delta > 0 else -max_change)

                    # Collected and written as one packet after the loop.
                    commands[name] = target
                    prev_positions[name] = target

                # One sync-write for the whole arm, not six round trips.
                if commands:
                    await self._follower.set_positions(commands)

                self.telemetry.positions = positions
                self.telemetry.targets = targets
                self.telemetry.commands = commands
                self.telemetry.clamped = clamped
                self.telemetry.load = dict(self._follower.last_load)

                # Checked against what the follower was actually COMMANDED, not
                # against the raw leader target. The speed limit makes the command
                # lag the leader on purpose; measuring against the leader turned
                # that intended lag into a stall and stopped every brisk movement.
                if not self.status.clutched and self._check_error_cutout(commands):
                    break

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.telemetry.dropped += 1
                logger.warning("Teleop loop error: %s", exc)
                # Continue running despite errors

            # Maintain 50Hz rate
            elapsed = time.monotonic() - t_start
            sleep_for = max(0.0, TELEOP_INTERVAL - elapsed)
            await asyncio.sleep(sleep_for)
            # Measured over the whole cycle, including the sleep — measuring only
            # the work made a 50 Hz loop report 85 Hz.
            cycle = time.monotonic() - t_start
            self.telemetry.hz = 1.0 / cycle if cycle > 0 else float(TELEOP_HZ)
            over = (cycle - TELEOP_INTERVAL) * 1000.0
            # Decay, so a single hitch fades instead of pinning the reading.
            self.telemetry.jitter_ms = max(over, self.telemetry.jitter_ms * 0.95)

    def _check_error_cutout(self, commands: dict[str, float]) -> bool:
        """
        Stop mirroring when a joint is stuck, not merely behind. Returns True
        when the loop should exit.

        `commands` is what the follower was actually told to do this frame, after
        speed limiting — not where the leader is. Those differ by design whenever
        the leader is moved faster than the speed limit allows, and treating that
        intended lag as a fault stops the run every time the operator moves
        briskly.

        A joint trips only if it stays past the threshold for ERROR_TRIP_FRAMES
        *without* closing the gap.
        """
        if not self.status.stop_on_error or self.status.max_error <= 0:
            return False

        # Engaging always starts with a gap; give it time to close.
        if time.monotonic() - self._engaged_at < ENGAGE_GRACE_S:
            return False

        follower = self._follower.last_positions
        if not follower:
            return False

        for name, target in commands.items():
            actual = follower.get(name)
            if actual is None:
                continue

            error = abs(target - actual)

            if error <= self.status.max_error:
                self._trip_count[name] = 0
                self._trip_from[name] = None
                continue

            # First frame past the threshold: remember where the joint was.
            if self._trip_from[name] is None:
                self._trip_from[name] = actual
                self._trip_count[name] = 1
                continue

            moved = abs(actual - self._trip_from[name])
            if moved >= STALL_MOVE_DEG:
                # It is moving. Behind, but working — restart the observation.
                self._trip_from[name] = actual
                self._trip_count[name] = 0
                continue

            self._trip_count[name] += 1

            if self._trip_count[name] >= ERROR_TRIP_FRAMES:
                load = self.telemetry.load.get(name)
                msg = (
                    f"Stopped: {name.replace('_', ' ')} is not moving — "
                    f"commanded {target:.1f}\u00b0, stuck at {actual:.1f}\u00b0 "
                    f"({error:.1f}\u00b0 behind, moved {moved:.1f}\u00b0 in "
                    f"{ERROR_TRIP_FRAMES * TELEOP_INTERVAL:.0f}s)"
                    + (f", load {load:.0f}%" if load is not None else "")
                    + (" — joint is at its clamp" if name in self.telemetry.clamped else "")
                )
                logger.warning(msg)
                self._reset_trip()
                self.status.error = msg
                self.status.mirroring = False
                self.status.clutched = False
                self._task = None
                return True

        return False
