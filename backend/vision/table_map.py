"""
Camera pixel -> 3D point on the table, in the arm's base frame.

The arm is bolted to the side of a box at an angle, so the table is NOT the arm's
XY plane and nothing here assumes it is. Instead the table plane is measured in
the arm's own frame, from the arm itself:

  1. Put an object on the table, touch its centre with the gripper tip, and record
     two things at that instant: the object's pixel in the camera image, and the
     tip's 3D position from forward kinematics (base frame, metres).
  2. Repeat for several spread-out spots.
  3. The tips all lie on the table, so a plane fitted to them IS the table, however
     it is tilted relative to the arm. Give that plane two in-plane axes (s, t).
  4. A fixed camera looking at a plane sees it through a homography, so fit one from
     pixel -> (s, t). Any later pixel then lands on a point of the plane, and
     c + s*e1 + t*e2 is that point in the base frame.

That also folds in the camera's pose and lens, so no separate camera calibration is
needed. Limits: the camera must not move after calibrating, lens distortion is not
modelled (a small error toward the image edges), and the pixel is assumed to sit on
the table plane. A tall object's bounding-box centre is really above the table, so
`height` shifts the answer along the plane normal; for scissors and other flat
things it is zero.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

CALIBRATION_FILE = Path(__file__).parent.parent.parent / "calibration" / "table_map.json"

MIN_POINTS = 4            # a homography is determined by four; fewer is a guess
RECOMMENDED_POINTS = 8    # more than this and noise in any one touch averages out
MAX_PLANE_RMS_M = 0.010   # tips further than this from a common plane: bad touches


@dataclass
class CalibrationPoint:
    pixel: tuple[float, float]               # where the camera saw the object
    xyz: tuple[float, float, float]          # where the gripper tip was, base frame (m)
    label: str | None = None
    # Unit vector along the gripper fingers at the touch (base frame). When the tip
    # is on the table the fingers point INTO it, which tells us which way is "up".
    approach: tuple[float, float, float] | None = None


@dataclass
class TableMap:
    """The fitted result: enough to turn a pixel into a base-frame point."""
    origin: np.ndarray            # a point on the table plane (m)
    e1: np.ndarray                # in-plane axis 1, unit
    e2: np.ndarray                # in-plane axis 2, unit
    normal: np.ndarray            # unit, pointing out of the table into free space
    H: np.ndarray                 # 3x3, pixel -> (s, t)
    image_size: tuple[int, int]   # (width, height) the pixels were measured at
    rms_in_plane_m: float         # reprojection error over the calibration points
    max_in_plane_m: float
    rms_off_plane_m: float        # how far the touched tips strayed from one plane
    n_points: int
    warnings: list[str] = field(default_factory=list)

    def pixel_to_base(self, px: float, py: float, height: float = 0.0,
                      image_size: tuple[int, int] | None = None) -> np.ndarray:
        """
        Base-frame XYZ (m) of the table point under this pixel, lifted `height`
        metres off the table along the plane normal. `image_size` rescales pixels
        if the stream's resolution differs from what was calibrated at.
        """
        if image_size and tuple(image_size) != tuple(self.image_size):
            px *= self.image_size[0] / image_size[0]
            py *= self.image_size[1] / image_size[1]
        s, t, w = self.H @ np.array([px, py, 1.0])
        if abs(w) < 1e-12:
            raise ValueError("Pixel is on the horizon of the table plane")
        s, t = s / w, t / w
        return self.origin + s * self.e1 + t * self.e2 + height * self.normal

    def to_dict(self) -> dict:
        return {
            "origin": self.origin.tolist(), "e1": self.e1.tolist(),
            "e2": self.e2.tolist(), "normal": self.normal.tolist(),
            "H": self.H.tolist(), "image_size": list(self.image_size),
            "rms_in_plane_m": self.rms_in_plane_m,
            "max_in_plane_m": self.max_in_plane_m,
            "rms_off_plane_m": self.rms_off_plane_m,
            "n_points": self.n_points, "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TableMap":
        return cls(
            origin=np.array(d["origin"]), e1=np.array(d["e1"]), e2=np.array(d["e2"]),
            normal=np.array(d["normal"]), H=np.array(d["H"]),
            image_size=tuple(d["image_size"]),
            rms_in_plane_m=d["rms_in_plane_m"], max_in_plane_m=d["max_in_plane_m"],
            rms_off_plane_m=d["rms_off_plane_m"], n_points=d["n_points"],
            warnings=list(d.get("warnings", [])),
        )


def fit(points: list[CalibrationPoint], image_size: tuple[int, int]) -> TableMap:
    """Fit the table plane and the pixel homography. Raises ValueError if unusable."""
    import cv2

    if len(points) < MIN_POINTS:
        raise ValueError(
            f"Need at least {MIN_POINTS} points, have {len(points)} "
            f"(aim for {RECOMMENDED_POINTS}, spread across the table)"
        )

    P = np.array([p.xyz for p in points], dtype=float)
    pix = np.array([p.pixel for p in points], dtype=float)

    # -- the plane: least squares through the touched tips ---------------------
    centre = P.mean(axis=0)
    _, sing, vt = np.linalg.svd(P - centre)
    if sing[1] < 1e-3:
        # Second singular value ~ spread along the second axis. Near zero means the
        # points are all on a line, which fixes only one direction of the plane.
        raise ValueError("Points are almost in a straight line — spread them across the table")
    normal = vt[2]
    # The SVD leaves the sign arbitrary. Out of the table is opposite to where the
    # fingers pointed when they touched it; failing that, toward the arm base.
    approach = [p.approach for p in points if p.approach is not None]
    if approach:
        up_is_positive = np.dot(np.mean(approach, axis=0), normal) < 0
    else:
        up_is_positive = np.dot(-centre, normal) > 0
    if not up_is_positive:
        normal = -normal

    # In-plane axes: e1 follows the base X axis where it can, so s,t are legible.
    ref = np.array([1.0, 0.0, 0.0])
    if abs(np.dot(ref, normal)) > 0.95:
        ref = np.array([0.0, 1.0, 0.0])
    e1 = ref - np.dot(ref, normal) * normal
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(normal, e1)

    st = np.column_stack(((P - centre) @ e1, (P - centre) @ e2))
    off_plane = (P - centre) @ normal

    # -- the camera: homography pixel -> (s, t) --------------------------------
    method = cv2.RANSAC if len(points) >= 8 else 0
    H, mask = cv2.findHomography(pix, st, method, 0.01) if method else \
        (cv2.findHomography(pix, st, 0)[0], None)
    if H is None:
        raise ValueError("Could not fit a pixel-to-table mapping — are the points degenerate?")

    proj = cv2.perspectiveTransform(pix.reshape(-1, 1, 2), H).reshape(-1, 2)
    err = np.linalg.norm(proj - st, axis=1)

    result = TableMap(
        origin=centre, e1=e1, e2=e2, normal=normal, H=H,
        image_size=tuple(image_size),
        rms_in_plane_m=float(np.sqrt(np.mean(err ** 2))),
        max_in_plane_m=float(err.max()),
        rms_off_plane_m=float(np.sqrt(np.mean(off_plane ** 2))),
        n_points=len(points),
    )

    if len(points) < RECOMMENDED_POINTS:
        result.warnings.append(
            f"Only {len(points)} points; {RECOMMENDED_POINTS}+ gives a steadier fit")
    if result.rms_off_plane_m > MAX_PLANE_RMS_M:
        result.warnings.append(
            f"Touched points sit {result.rms_off_plane_m * 1000:.1f} mm off a common "
            f"plane — some touches may not have reached the table")
    if len(points) == MIN_POINTS:
        result.warnings.append(
            "Exactly 4 points fit perfectly by construction, so the error shown "
            "says nothing about accuracy — add more")
    return result


# -- persistence -----------------------------------------------------------------

def save(points: list[CalibrationPoint], table: TableMap | None, camera: int | None) -> None:
    CALIBRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION_FILE.write_text(json.dumps({
        "camera": camera,
        "points": [{"pixel": list(p.pixel), "xyz": list(p.xyz), "label": p.label,
                    "approach": list(p.approach) if p.approach else None}
                   for p in points],
        "table": table.to_dict() if table else None,
    }, indent=2))


def load() -> tuple[list[CalibrationPoint], TableMap | None, int | None]:
    if not CALIBRATION_FILE.exists():
        return [], None, None
    try:
        d = json.loads(CALIBRATION_FILE.read_text())
        pts = [CalibrationPoint(tuple(p["pixel"]), tuple(p["xyz"]), p.get("label"),
                                tuple(p["approach"]) if p.get("approach") else None)
               for p in d.get("points", [])]
        table = TableMap.from_dict(d["table"]) if d.get("table") else None
        return pts, table, d.get("camera")
    except Exception as exc:
        logger.warning("Could not read %s: %s", CALIBRATION_FILE, exc)
        return [], None, None
