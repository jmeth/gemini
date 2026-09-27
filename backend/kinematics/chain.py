"""
Kinematic chain, read from the SO-101 URDF.

The 3D view previously used hand-copied link offsets with guessed rotation axes
(Z, Y, Y, Y, X, Y in one shared frame). The offsets were close, but every joint's
`origin rpy` was thrown away — and four of them are +/-90 degree frame rotations.
The URDF rotates every joint about its own local Z; the rpy is what orients that
Z. Dropping it puts the rendered arm in a pose the real one never adopts.

Serving the parsed chain keeps the URDF the single source of truth, so the
viewer cannot drift from the hardware description again.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

VENDORED_URDF = Path(__file__).parent.parent.parent / "vendor" / "so101_new_calib.urdf"


@dataclass
class Joint:
    name: str
    type: str
    parent: str
    child: str
    xyz: list[float]      # translation from parent frame
    rpy: list[float]      # fixed rotation establishing this joint's frame
    axis: list[float]     # rotation axis, in the joint frame
    lower: float | None   # radians
    upper: float | None


def _floats(text: str | None, default: tuple[float, ...]) -> list[float]:
    if not text:
        return list(default)
    try:
        return [float(v) for v in text.split()]
    except ValueError:
        return list(default)


def parse_urdf(path: Path) -> dict:
    """Parse a URDF into a root-to-tip chain of joints."""
    root = ET.parse(path).getroot()

    joints: dict[str, Joint] = {}
    children: dict[str, list[str]] = {}
    has_parent: set[str] = set()

    for el in root.findall("joint"):
        parent = el.find("parent").get("link")
        child = el.find("child").get("link")
        origin = el.find("origin")
        axis_el = el.find("axis")
        limit = el.find("limit")

        joint = Joint(
            name=el.get("name"),
            type=el.get("type"),
            parent=parent,
            child=child,
            xyz=_floats(origin.get("xyz") if origin is not None else None, (0, 0, 0)),
            rpy=_floats(origin.get("rpy") if origin is not None else None, (0, 0, 0)),
            axis=_floats(axis_el.get("xyz") if axis_el is not None else None, (0, 0, 1)),
            lower=float(limit.get("lower")) if limit is not None and limit.get("lower") else None,
            upper=float(limit.get("upper")) if limit is not None and limit.get("upper") else None,
        )
        joints[joint.name] = joint
        children.setdefault(parent, []).append(joint.name)
        has_parent.add(child)

    links = {el.get("name") for el in root.findall("link")}
    roots = [l for l in links if l not in has_parent]
    base = roots[0] if roots else "base_link"

    # Walk the serial chain from the base. Where a link branches (the gripper's
    # moving jaw), prefer the branch that continues the arm.
    chain: list[Joint] = []
    link = base
    while link in children:
        options = [joints[n] for n in children[link]]
        nxt = max(options, key=lambda j: len(children.get(j.child, [])))
        chain.append(nxt)
        link = nxt.child

    main = {j.name for j in chain}
    return {
        "root": base,
        "tip": chain[-1].child if chain else base,
        # Every joint, so the viewer can walk the real tree — the gripper's
        # moving jaw hangs off the wrist and is not on the main chain.
        "joints": [asdict(j) for j in joints.values()],
        # The arm proper, base to tool tip, in order.
        "chain": [j.name for j in chain],
        "actuated": [j.name for j in joints.values() if j.type == "revolute"],
        "branches": [j.name for j in joints.values() if j.name not in main],
    }


_cache: dict | None = None


def get_chain(path: Path | None = None) -> dict:
    """Parsed chain, cached. Returns an empty chain if the URDF is unreadable."""
    global _cache
    if _cache is not None and path is None:
        return _cache

    urdf = path or VENDORED_URDF
    try:
        chain = parse_urdf(urdf)
        chain["source"] = str(urdf)
        logger.info("Loaded kinematic chain from %s (%d joints)", urdf, len(chain["joints"]))
    except Exception as exc:
        logger.warning("Could not parse URDF %s: %s", urdf, exc)
        chain = {"root": "base_link", "tip": "base_link", "joints": [],
                 "actuated": [], "source": str(urdf), "error": str(exc)}

    if path is None:
        _cache = chain
    return chain
