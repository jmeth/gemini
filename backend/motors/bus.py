"""
Minimal Feetech STS3215 motor bus driver for SO-101.
Uses scservo_sdk (feetech-servo-sdk on PyPI) directly.
Adapted from lerobot/motors/feetech/ (Apache 2.0).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import scservo_sdk as scs

# Voltage and temperature are read this often, not every poll.
SLOW_READ_INTERVAL = 1.0

from .tables import (
    BAUDRATE,
    CONTROL_TABLE,
    MOTOR_IDS,
    MOTOR_NAMES,
    PROTOCOL,
    RESOLUTION,
    SIGN_BIT,
)

logger = logging.getLogger(__name__)


@dataclass
class MotorState:
    pos_deg: float       # degrees, uncalibrated (raw ticks -> degrees)
    load_pct: float      # load percentage, signed (-100 to +100)
    voltage_v: float     # volts
    temp_c: float        # celsius


def _decode_sign_magnitude(value: int, sign_bit: int) -> int:
    sign = (value >> sign_bit) & 1
    magnitude = value & ((1 << sign_bit) - 1)
    return -magnitude if sign else magnitude


def _encode_sign_magnitude(value: int, sign_bit: int) -> int:
    if value < 0:
        return (1 << sign_bit) | (-value)
    return value


def _patch_packet_timeout(port_handler: scs.PortHandler) -> None:
    """
    Monkeypatch to fix incorrect packet timeouts in the scservo_sdk PyPI package.
    The bug is fixed upstream but not yet on PyPI.
    Source: lerobot/motors/feetech/feetech.py
    """
    def setPacketTimeout(self, packet_length):  # noqa: N802
        self.packet_start_time = self.getCurrentTime()
        self.packet_timeout = (
            (self.tx_time_per_byte * packet_length)
            + (self.tx_time_per_byte * 3.0)
            + 50
        )

    port_handler.setPacketTimeout = setPacketTimeout.__get__(port_handler, scs.PortHandler)


class FeetechBus:
    """
    Synchronous driver for a bus of STS3215 motors.
    All public methods are blocking; run them in a thread-pool executor
    when calling from async code.
    """

    def __init__(self, port: str, baudrate: int = BAUDRATE) -> None:
        self._lock = threading.RLock()
        self._slow_at = 0.0
        self._slow_cache: dict[str, dict[str, int]] = {}
        self.port = port
        self.baudrate = baudrate
        self._port_handler: scs.PortHandler | None = None
        self._packet_handler: scs.PacketHandler | None = None
        self._connected = False

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        if self._connected:
            return
        ph = scs.PortHandler(self.port)
        _patch_packet_timeout(ph)
        if not ph.openPort():
            raise ConnectionError(f"Failed to open port {self.port}")
        if not ph.setBaudRate(self.baudrate):
            ph.closePort()
            raise ConnectionError(f"Failed to set baudrate {self.baudrate} on {self.port}")
        self._port_handler = ph
        self._packet_handler = scs.PacketHandler(PROTOCOL)
        self._connected = True
        logger.info("Connected to motor bus on %s", self.port)

    def disconnect(self) -> None:
        if self._port_handler and self._connected:
            self._port_handler.closePort()
        self._port_handler = None
        self._packet_handler = None
        self._connected = False
        logger.info("Disconnected from motor bus")

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    # The follower's 10 Hz poll loop and the 50 Hz teleop loop run in different
    # executor threads but share one serial port, and the SDK's port handler is
    # not thread-safe. Interleaved transactions corrupt each other — the symptom
    # is "Port is in use!" on writes and empty reads. Every public operation
    # takes this lock so one transaction completes before the next begins.

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def _sync_read(self, data_name: str) -> dict[str, int]:
        """Read the same register from all 6 motors in one transaction."""
        addr, length = CONTROL_TABLE[data_name]
        reader = scs.GroupSyncRead(self._port_handler, self._packet_handler, addr, length)
        for mid in MOTOR_IDS.values():
            reader.addParam(mid)
        result = reader.txRxPacket()
        if result != scs.COMM_SUCCESS:
            logger.warning("sync_read %s failed: %s", data_name,
                           self._packet_handler.getTxRxResult(result))
            return {}

        out: dict[str, int] = {}
        for name, mid in MOTOR_IDS.items():
            if reader.isAvailable(mid, addr, length):
                out[name] = reader.getData(mid, addr, length)
            else:
                logger.warning("No data for motor %s (%d) on %s", name, mid, data_name)
        reader.clearParam()
        return out

    def read_all(self) -> dict[str, MotorState]:
        """Read position, load, voltage, temperature for all motors."""
        if not self._connected:
            raise RuntimeError("Not connected")

        # One lock per transaction, not one across all four. A sync-read is
        # atomic on its own; holding the port for the whole set blocks the 50 Hz
        # teleop write for the entire read.
        raw_pos = self._locked_read("Present_Position")
        raw_load = self._locked_read("Present_Load")
        if self._slow_due():
            self._slow_cache["volt"] = self._locked_read("Present_Voltage")
            self._slow_cache["temp"] = self._locked_read("Present_Temperature")
        raw_volt = self._slow_cache.get("volt", {})
        raw_temp = self._slow_cache.get("temp", {})

        states: dict[str, MotorState] = {}
        for name in MOTOR_NAMES:
            pos_raw  = raw_pos.get(name, 0)
            load_raw = raw_load.get(name, 0)

            pos_decoded  = _decode_sign_magnitude(pos_raw,  SIGN_BIT["Present_Position"])
            load_decoded = _decode_sign_magnitude(load_raw, SIGN_BIT["Present_Load"])

            pos_deg   = pos_decoded * 360.0 / RESOLUTION
            load_pct  = load_decoded * 100.0 / 1023   # 10-bit magnitude
            voltage_v = raw_volt.get(name, 0) * 0.1
            temp_c    = float(raw_temp.get(name, 0))

            states[name] = MotorState(
                pos_deg=round(pos_deg, 2),
                load_pct=round(load_pct, 1),
                voltage_v=round(voltage_v, 1),
                temp_c=temp_c,
            )
        return states

    def _locked_read(self, data_name: str) -> dict[str, int]:
        with self._lock:
            return self._sync_read(data_name)

    def _slow_due(self) -> bool:
        """
        Voltage and temperature change slowly; reading them every poll doubles
        the time the port is busy for data nothing looks at ten times a second.
        """
        now = time.monotonic()
        if now - self._slow_at >= SLOW_READ_INTERVAL:
            self._slow_at = now
            return True
        return not self._slow_cache

    def read_positions(self) -> dict[str, float]:
        """
        Positions only, in one transaction.

        read_all costs four sync-reads (position, load, voltage, temperature).
        The 50 Hz teleop loop uses position alone, so calling read_all there put
        four times the necessary traffic on the bus and starved it.
        """
        if not self._connected:
            raise RuntimeError("Not connected")
        with self._lock:
            raw = self._sync_read("Present_Position")
        out: dict[str, float] = {}
        for name, value in raw.items():
            decoded = _decode_sign_magnitude(value, SIGN_BIT["Present_Position"])
            out[name] = round(decoded * 360.0 / RESOLUTION, 2)
        return out

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def _write(self, motor_id: int, data_name: str, value: int) -> None:
        addr, length = CONTROL_TABLE[data_name]
        if data_name in SIGN_BIT:
            value = _encode_sign_magnitude(value, SIGN_BIT[data_name])
        if length == 1:
            result, error = self._packet_handler.write1ByteTxRx(
                self._port_handler, motor_id, addr, value)
        else:
            result, error = self._packet_handler.write2ByteTxRx(
                self._port_handler, motor_id, addr, value)
        if result != scs.COMM_SUCCESS:
            logger.warning("write %s to motor %d failed: %s", data_name, motor_id,
                           self._packet_handler.getTxRxResult(result))

    def write_position(self, motor_name: str, degrees: float) -> None:
        """Command a motor to move to the given position in degrees."""
        if not self._connected:
            raise RuntimeError("Not connected")
        mid = MOTOR_IDS[motor_name]
        raw = round(degrees * RESOLUTION / 360.0)
        with self._lock:
            self._write(mid, "Goal_Position", raw)

    def write_positions(self, positions: dict[str, float]) -> None:
        """Sync-write positions to multiple motors in one packet."""
        if not self._connected:
            raise RuntimeError("Not connected")
        addr, length = CONTROL_TABLE["Goal_Position"]
        with self._lock:
            writer = scs.GroupSyncWrite(self._port_handler, self._packet_handler, addr, length)
            for name, deg in positions.items():
                mid = MOTOR_IDS[name]
                raw = round(deg * RESOLUTION / 360.0)
                raw = _encode_sign_magnitude(raw, SIGN_BIT["Goal_Position"])
                data = [scs.SCS_LOBYTE(raw), scs.SCS_HIBYTE(raw)]
                writer.addParam(mid, data)
            result = writer.txPacket()
            if result != scs.COMM_SUCCESS:
                logger.warning("sync write positions failed: %s",
                               self._packet_handler.getTxRxResult(result))
            writer.clearParam()

    def configure_responsive(self) -> None:
        """
        Make the follower track commands promptly.

        Acceleration 254 removes the servo's soft-start ramp, which otherwise
        reads as lag however fast commands arrive. Return_Delay_Time 0 drops the
        default 500 us wait before every status reply, which adds up across the
        reads on this bus. The delay lives in EEPROM, so the lock is released
        for the write; the caller re-applies torque (and the lock) afterwards.
        """
        if not self._connected:
            raise RuntimeError("Not connected")
        with self._lock:
            for name in MOTOR_NAMES:
                mid = MOTOR_IDS[name]
                self._write(mid, "Lock", 0)
                self._write(mid, "Return_Delay_Time", 0)
                self._write(mid, "Acceleration", 254)

    def set_torque(self, motor_name: str, enabled: bool) -> None:
        mid = MOTOR_IDS[motor_name]
        with self._lock:
            self._write(mid, "Torque_Enable", 1 if enabled else 0)
            self._write(mid, "Lock", 1 if enabled else 0)

    def set_torque_all(self, enabled: bool) -> None:
        # Held across all six so nothing interleaves mid-stop.
        with self._lock:
            for name in MOTOR_NAMES:
                self.set_torque(name, enabled)

    def estop(self) -> None:
        """Emergency stop: disable torque on all motors immediately."""
        try:
            self.set_torque_all(False)
        except Exception as exc:
            logger.error("E-stop error: %s", exc)
