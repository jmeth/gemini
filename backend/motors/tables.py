# STS3215 servo control table
# Adapted from lerobot/motors/feetech/tables.py (Apache 2.0)
# Only what's needed for SO-101: position, load, voltage, temperature, torque

# (address, size_bytes)
CONTROL_TABLE = {
    # EPROM — mostly read-only here, used for diagnostics (Return_Delay_Time is
    # written once on follower connect). A servo enforces its own
    # angle limits, so a joint can refuse to reach a position our calibration
    # thinks is legal, and the only symptom is a tracking error that never closes.
    "Return_Delay_Time":   (7, 1),
    "Min_Angle_Limit":     (9, 2),
    "Max_Angle_Limit":     (11, 2),
    "Max_Torque_Limit":    (16, 2),
    # SRAM
    "Torque_Enable":       (40, 1),
    "Acceleration":        (41, 1),
    "Goal_Position":       (42, 2),
    "Goal_Velocity":       (46, 2),
    "Torque_Limit":        (48, 2),
    "Lock":                (55, 1),
    "Present_Position":    (56, 2),
    "Present_Velocity":    (58, 2),
    "Present_Load":        (60, 2),
    "Present_Voltage":     (62, 1),
    "Present_Temperature": (63, 1),
    "Moving":              (66, 1),
}

RESOLUTION = 4096          # ticks per revolution for STS3215
BAUDRATE   = 1_000_000
PROTOCOL   = 0             # Feetech protocol version

# Sign-magnitude encoding: bit N is the sign, lower bits are magnitude
SIGN_BIT = {
    "Present_Position": 15,
    "Present_Velocity": 15,
    "Present_Load":     10,
    "Goal_Position":    15,
    "Goal_Velocity":    15,
}

# Motor IDs on the SO-101 bus (follower or leader, same layout)
MOTOR_IDS: dict[str, int] = {
    "shoulder_pan":  1,
    "shoulder_lift": 2,
    "elbow_flex":    3,
    "wrist_flex":    4,
    "wrist_roll":    5,
    "gripper":       6,
}

MOTOR_NAMES = list(MOTOR_IDS.keys())
