"""
Calibration state machine for SO-101 arm.

Guides user through:
1. Zero position calibration per joint
2. Min/max range detection per joint
3. Saves calibration to JSON file
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

from backend.motors.tables import MOTOR_NAMES

logger = logging.getLogger(__name__)

CALIBRATION_DIR = Path(__file__).parent.parent.parent / "calibration"

def get_calibration_file(arm_name: str) -> Path:
    """Get calibration file path for a specific arm."""
    return CALIBRATION_DIR / f"calibration_{arm_name}.json"


def legacy_calibration_file(arm_name: str) -> Path:
    """
    The pre-rename filename. Calibrations saved before the scheme changed are
    still perfectly good data, and silently ignoring them means the arm runs on
    raw motor angles with no zero offset — which looks like broken hardware, not
    a missing file.
    """
    return CALIBRATION_DIR / f"{arm_name}_arm.json"


def find_calibration_file(arm_name: str) -> Path | None:
    """Current filename first, then the legacy one. None if neither exists."""
    current = get_calibration_file(arm_name)
    if current.exists():
        return current
    legacy = legacy_calibration_file(arm_name)
    return legacy if legacy.exists() else None


class CalibrationStep(Enum):
    IDLE = "idle"
    MIN_POSITION = "min_position"
    MAX_POSITION = "max_position"
    COMPLETE = "complete"


@dataclass
class JointCalibration:
    zero_offset: float = 0.0  # degrees to add to raw position to get true zero
    min_pos: float = -180.0
    max_pos: float = 180.0


@dataclass
class CalibrationData:
    joints: dict[str, JointCalibration]
    home_positions: dict[str, float] = None  # Custom home position per joint

    def __post_init__(self):
        if self.home_positions is None:
            # Default home: all at 0°
            self.home_positions = {name: 0.0 for name in MOTOR_NAMES}

    def to_dict(self) -> dict:
        return {
            "joints": {name: asdict(cal) for name, cal in self.joints.items()},
            "home_positions": self.home_positions
        }

    @classmethod
    def from_dict(cls, data: dict) -> CalibrationData:
        joints = {
            name: JointCalibration(**cal_data)
            for name, cal_data in data.get("joints", {}).items()
        }
        home_positions = data.get("home_positions", {name: 0.0 for name in MOTOR_NAMES})
        return cls(joints=joints, home_positions=home_positions)

    @classmethod
    def default(cls) -> CalibrationData:
        """Default calibration (no offsets, standard ranges)."""
        joints = {
            name: JointCalibration(
                zero_offset=0.0,
                min_pos=-180.0 if name != "gripper" else 0.0,
                max_pos=180.0 if name != "gripper" else 100.0
            )
            for name in MOTOR_NAMES
        }
        return cls(joints=joints)


class CalibrationStateMachine:
    """
    Manages calibration workflow:
    - For each joint: zero → min → max
    - User manually moves joint to each position, confirms
    - Records raw motor positions
    - Computes offsets and limits
    """

    def __init__(self, arm_name: str = "follower"):
        self.arm_name = arm_name
        self.step = CalibrationStep.IDLE
        self.current_joint_idx = 0
        self.current_joint: Optional[str] = None

        # Temporary storage during calibration
        self._zero_raw: dict[str, float] = {}
        self._min_raw: dict[str, float] = {}
        self._max_raw: dict[str, float] = {}

        # Final calibration data
        self.data = CalibrationData.default()

    @property
    def current_motor(self) -> Optional[str]:
        """Name of motor currently being calibrated."""
        if 0 <= self.current_joint_idx < len(MOTOR_NAMES):
            return MOTOR_NAMES[self.current_joint_idx]
        return None

    @property
    def progress(self) -> tuple[int, int]:
        """Returns (current joint number, total joints)."""
        return (self.current_joint_idx + 1, len(MOTOR_NAMES))

    def start(self) -> dict:
        """Begin calibration sequence."""
        logger.info("Starting calibration")
        self.step = CalibrationStep.MIN_POSITION
        self.current_joint_idx = 0
        self.current_joint = self.current_motor
        self._zero_raw.clear()
        self._min_raw.clear()
        self._max_raw.clear()
        return self.get_status()

    def confirm(self, raw_position: float) -> dict:
        """
        User confirms current position. Record it and advance.
        raw_position: current motor position in degrees (uncalibrated)
        """
        if self.step == CalibrationStep.IDLE or self.step == CalibrationStep.COMPLETE:
            return self.get_status()

        motor = self.current_motor
        if not motor:
            return self.get_status()

        if self.step == CalibrationStep.MIN_POSITION:
            logger.info(f"Calibration: {motor} min at {raw_position:.1f}°")
            self._min_raw[motor] = raw_position
            self.step = CalibrationStep.MAX_POSITION

        elif self.step == CalibrationStep.MAX_POSITION:
            logger.info(f"Calibration: {motor} max at {raw_position:.1f}°")
            self._max_raw[motor] = raw_position

            # Calculate zero as midpoint between min and max
            zero_raw = (self._min_raw[motor] + self._max_raw[motor]) / 2.0
            self._zero_raw[motor] = zero_raw
            logger.info(f"Calibration: {motor} calculated zero at {zero_raw:.1f}° (midpoint)")

            # Compute calibration for this joint
            self._compute_joint_calibration(motor)

            # Move to next joint or finish
            self.current_joint_idx += 1
            if self.current_joint_idx >= len(MOTOR_NAMES):
                self._finish()
            else:
                self.current_joint = self.current_motor
                self.step = CalibrationStep.MIN_POSITION

        return self.get_status()

    def _compute_joint_calibration(self, motor: str):
        """Compute zero offset and min/max limits for a joint."""
        zero_raw = self._zero_raw[motor]
        min_raw = self._min_raw[motor]
        max_raw = self._max_raw[motor]

        # Zero offset: amount to subtract from raw position to make zero_raw = 0
        zero_offset = -zero_raw

        # Calibrated min/max (relative to zero)
        min_calibrated = min_raw + zero_offset
        max_calibrated = max_raw + zero_offset

        self.data.joints[motor] = JointCalibration(
            zero_offset=zero_offset,
            min_pos=min_calibrated,
            max_pos=max_calibrated
        )

    def _finish(self):
        """Complete calibration and save to file."""
        self.step = CalibrationStep.COMPLETE
        self.current_joint = None
        self._save()
        logger.info("Calibration complete and saved")

    def _save(self):
        """Save calibration to JSON file."""
        CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
        cal_file = get_calibration_file(self.arm_name)
        cal_file.write_text(json.dumps(self.data.to_dict(), indent=2))
        logger.info(f"Saved calibration for '{self.arm_name}' to {cal_file}")

    def get_status(self) -> dict:
        """Return current calibration state for UI."""
        return {
            "active": self.step != CalibrationStep.IDLE,
            "step": self.step.value,
            "motor": self.current_motor,
            "progress": self.progress,
            "instruction": self._get_instruction(),
        }

    def _get_instruction(self) -> str:
        """Get instruction text for current step."""
        if self.step == CalibrationStep.IDLE:
            return "Click 'Start Calibration' to begin"

        if self.step == CalibrationStep.COMPLETE:
            return "Calibration complete! Zero positions calculated as midpoints."

        motor = self.current_motor
        if not motor:
            return ""

        motor_display = motor.replace("_", " ").title()

        if self.step == CalibrationStep.MIN_POSITION:
            return f"Move {motor_display} to its minimum/lowest position, then click Confirm"
        elif self.step == CalibrationStep.MAX_POSITION:
            return f"Move {motor_display} to its maximum/highest position, then click Confirm"

        return ""

    @classmethod
    def load_calibration(cls, arm_name: str = "follower") -> CalibrationData:
        """Load saved calibration from file, or return default."""
        cal_file = find_calibration_file(arm_name)
        if cal_file:
            try:
                data = json.loads(cal_file.read_text())
                if cal_file.name != get_calibration_file(arm_name).name:
                    logger.warning(
                        "Using legacy calibration file %s for '%s'. Re-run the "
                        "calibration wizard to save it under the current name (%s).",
                        cal_file.name, arm_name, get_calibration_file(arm_name).name,
                    )
                else:
                    logger.info(f"Loaded calibration for '{arm_name}' from {cal_file}")
                return CalibrationData.from_dict(data)
            except Exception as exc:
                logger.warning(f"Failed to load calibration for '{arm_name}': {exc}")
        else:
            logger.warning(
                "No calibration for '%s' — joints will report raw motor angles, "
                "which run past the nominal travel and make the sliders unusable.",
                arm_name,
            )
        return CalibrationData.default()

    @classmethod
    def save_home_positions(cls, positions: dict[str, float], arm_name: str = "follower") -> None:
        """Save custom home positions to calibration file."""
        cal_data = cls.load_calibration(arm_name)
        cal_data.home_positions = positions
        CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
        cal_file = get_calibration_file(arm_name)
        cal_file.write_text(json.dumps(cal_data.to_dict(), indent=2))
        logger.info(f"Home positions saved for '{arm_name}': {positions}")

    @classmethod
    def list_calibrations(cls) -> list[str]:
        """List all available calibration names."""
        if not CALIBRATION_DIR.exists():
            return []

        calibrations = set()
        for file in CALIBRATION_DIR.glob("calibration_*.json"):
            # "calibration_{arm_name}.json"
            calibrations.add(file.stem.replace("calibration_", ""))
        for file in CALIBRATION_DIR.glob("*_arm.json"):
            # Legacy "{arm_name}_arm.json" — still usable, so still counts as calibrated
            calibrations.add(file.stem[: -len("_arm")])

        return sorted(calibrations)
