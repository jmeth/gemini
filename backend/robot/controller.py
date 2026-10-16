"""
Robot controller: manages the motor bus connection and runs the 10 Hz poll loop.
All blocking motor I/O is dispatched to a thread-pool executor so the asyncio
event loop stays responsive.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from typing import Callable

from backend.motors.bus import FeetechBus, MotorState
from backend.motors.tables import MOTOR_NAMES
from backend.robot.calibration import CalibrationStateMachine

logger = logging.getLogger(__name__)

POLL_HZ = 10
POLL_INTERVAL = 1.0 / POLL_HZ

# A single dropped read is normal on a busy bus — teleop shares this port. Only
# a sustained run of failures means the arm is actually gone. Reporting
# connected=False on the first one made the whole UI flicker to "disconnected"
# whenever the arm was being driven hard.
POLL_FAIL_LIMIT = 5


@dataclass
class RobotStatus:
    connected: bool = False
    error: str | None = None


class RobotController:
    """
    Owns the FeetechBus and drives the background poll loop.
    Calls `broadcast_cb` with a JSON-serialisable dict every POLL_HZ.
    """

    def __init__(self, broadcast_cb: Callable[[dict], None]) -> None:
        self._broadcast = broadcast_cb
        self._bus: FeetechBus | None = None
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.status = RobotStatus()
        # Load calibration
        self._calibration = CalibrationStateMachine.load_calibration()
        # Last calibrated position per joint, refreshed every poll. Teleop reads
        # this instead of hitting the bus itself at 50 Hz.
        self.last_positions: dict[str, float] = {}
        # Raw, pre-calibration angles. The calibration wizard records these, and
        # taking them from the poll cache keeps a second reader off the port.
        self.last_raw: dict[str, float] = {}
        self.last_load: dict[str, float] = {}
        self._last_message: dict | None = None
        # How long the bus read takes, and how long teleop can be blocked by it.
        self.poll_ms: float = 0.0
        self.poll_ms_max: float = 0.0
        # Optional callable returning a leader-arm snapshot to ride along on the
        # broadcast frame. Wired up in main.py; None when teleop is idle.
        self.leader_provider: Callable[[], dict | None] | None = None
        # Optional callable returning extra top-level keys for the broadcast
        # frame (e.g. recording / playing state). Wired up in main.py.
        self.frame_extras_provider: Callable[[], dict] | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, port: str) -> None:
        if self._bus and self._bus.is_connected:
            await self.disconnect()
        self._bus = FeetechBus(port)
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._bus.connect)
            # Before torque: the EEPROM write needs the lock released, and
            # set_torque_all below puts the lock back.
            await loop.run_in_executor(None, self._bus.configure_responsive)
            # Enable torque on all motors to take control
            await loop.run_in_executor(None, self._bus.set_torque_all, True)
            self.status = RobotStatus(connected=True)
            logger.info("Follower arm connected on %s — torque enabled", port)
        except Exception as exc:
            self._bus = None
            self.status = RobotStatus(connected=False, error=str(exc))
            raise
        self._loop = loop
        self._task = loop.create_task(self._poll_loop())

    async def disconnect(self) -> None:
        # Disable torque on all motors before disconnecting
        if self._bus:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._bus.set_torque_all, False)
                logger.info("Torque disabled on all motors")
            except Exception as exc:
                logger.warning("Failed to disable torque during disconnect: %s", exc)

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._bus:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._bus.disconnect)
            self._bus = None
        self.status = RobotStatus(connected=False)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def set_position(self, motor_name: str, degrees: float) -> None:
        if not self._bus:
            raise RuntimeError("Not connected")
        # Apply inverse calibration: convert calibrated degrees to raw motor degrees
        cal = self._calibration.joints.get(motor_name)
        raw_degrees = degrees - cal.zero_offset if cal else degrees
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._bus.write_position, motor_name, raw_degrees)

    async def set_positions(self, degrees: dict[str, float]) -> None:
        """
        Command several joints in one bus transaction. Teleop used to await six
        separate writes per frame, which at 50 Hz saturated the port and fought
        the poll loop for it.
        """
        if not self._bus:
            raise RuntimeError("Not connected")
        raw = {}
        for name, deg in degrees.items():
            cal = self._calibration.joints.get(name)
            raw[name] = deg - cal.zero_offset if cal else deg
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._bus.write_positions, raw)

    async def set_torque(self, motor_name: str, enabled: bool) -> None:
        if not self._bus:
            raise RuntimeError("Not connected")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._bus.set_torque, motor_name, enabled)

    async def estop(self) -> None:
        if not self._bus:
            return
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._bus.estop)

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        logger.info("Poll loop started at %d Hz", POLL_HZ)
        failures = 0
        while True:
            t_start = time.monotonic()
            try:
                loop = asyncio.get_event_loop()
                t_read = time.monotonic()
                states: dict[str, MotorState] = await loop.run_in_executor(
                    None, self._bus.read_all
                )
                self.poll_ms = (time.monotonic() - t_read) * 1000.0
                self.poll_ms_max = max(self.poll_ms_max, self.poll_ms)
                failures = 0
                msg = self._build_message(states)
                self._last_message = msg
                self._broadcast(msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                logger.warning("Poll error (%d/%d): %s", failures, POLL_FAIL_LIMIT, exc)
                if failures >= POLL_FAIL_LIMIT:
                    self._broadcast({"error": str(exc), "connected": False})
                elif self._last_message:
                    # Transient: hold the last good frame rather than claiming
                    # the arm vanished. Flagged so the UI can show it is stale.
                    self._broadcast({**self._last_message, "stale": True})

            elapsed = time.monotonic() - t_start
            sleep_for = max(0.0, POLL_INTERVAL - elapsed)
            await asyncio.sleep(sleep_for)

    def _build_message(self, states: dict[str, MotorState]) -> dict:
        joints = {}
        for name, s in states.items():
            # Apply calibration offset to get true position
            cal = self._calibration.joints.get(name)
            pos = s.pos_deg + cal.zero_offset if cal else s.pos_deg
            joints[name] = {
                "pos":  pos,
                "load": s.load_pct,
                "volt": s.voltage_v,
                "temp": s.temp_c,
            }
            self.last_positions[name] = pos
            self.last_raw[name] = s.pos_deg
            self.last_load[name] = s.load_pct

        msg = {"t": time.time(), "connected": True, "joints": joints}

        # The leader's live positions ride along on the same frame, so the UI can
        # draw both arms on one track without a second poll.
        if self.leader_provider is not None:
            snapshot = self.leader_provider()
            if snapshot:
                msg["leader"] = snapshot

        if self.frame_extras_provider is not None:
            extras = self.frame_extras_provider()
            if extras:
                msg.update(extras)

        return msg
