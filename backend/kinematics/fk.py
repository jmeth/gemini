"""
Forward kinematics from the SO-101 URDF.

Answers "where is the gripper tip, in the arm's own base frame?" — the one piece
of arm geometry the camera-to-arm calibration needs. Uses the same convention as
the 3D viewer (Arm3D.svelte), so the two cannot disagree about the pose:

    T_child = T_parent * Translate(origin.xyz) * RPY(origin.rpy) * Rot(axis, q)

with the URDF's rpy as fixed-axis XYZ (R = Rz(yaw) Ry(pitch) Rx(roll)), q in
radians, and the calibrated joint degrees fed in directly — the viewer does the
same for every joint on the path to the tool.

Units are metres, as in the URDF.
"""

from __future__ import annotations

import math

import numpy as np

from backend.kinematics.chain import get_chain

# The tool centre point: a fixed frame on the gripper body that does not move
# when the jaw opens, so grasp targets do not shift with the gripper state.
TOOL_LINK = "gripper_frame_link"


def _rpy(roll: float, pitch: float, yaw: float) -> np.ndarray:
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return rz @ ry @ rx


def _axis_angle(axis: list[float], angle: float) -> np.ndarray:
    a = np.asarray(axis, dtype=float)
    n = np.linalg.norm(a)
    if n == 0:
        return np.eye(3)
    x, y, z = a / n
    c, s = math.cos(angle), math.sin(angle)
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) * c + s * k + (1 - c) * np.outer([x, y, z], [x, y, z])


def _path_to(link: str, joints: list[dict]) -> list[dict]:
    """Joints from the base down to `link`, in order."""
    by_child = {j["child"]: j for j in joints}
    path: list[dict] = []
    while link in by_child:
        j = by_child[link]
        path.append(j)
        link = j["parent"]
    return list(reversed(path))


def link_pose(joint_deg: dict[str, float], link: str = TOOL_LINK,
              chain: dict | None = None) -> np.ndarray:
    """
    4x4 pose of `link` in the base frame, for the given calibrated joint angles
    (degrees, keyed by joint name). A joint missing from the dict sits at zero.
    """
    chain = chain or get_chain()
    joints = chain.get("joints", [])
    if not joints:
        raise RuntimeError(chain.get("error") or "No kinematic chain loaded")

    path = _path_to(link, joints)
    if not path:
        raise ValueError(f"Link '{link}' is not in the kinematic chain")

    T = np.eye(4)
    for j in path:
        step = np.eye(4)
        step[:3, 3] = j["xyz"]
        step[:3, :3] = _rpy(*j["rpy"])
        if j["type"] in ("revolute", "continuous"):
            q = math.radians(joint_deg.get(j["name"], 0.0))
            rot = np.eye(4)
            rot[:3, :3] = _axis_angle(j["axis"], q)
            step = step @ rot
        T = T @ step
    return T


def tool_position(joint_deg: dict[str, float]) -> np.ndarray:
    """XYZ of the gripper tool point in the base frame, in metres."""
    return link_pose(joint_deg)[:3, 3]
