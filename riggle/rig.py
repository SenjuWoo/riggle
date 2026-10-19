"""Skeleton presets and validation, shared by the server, the browser and Blender.

All joint positions in this module are in glTF/three.js space: +X right, +Y up,
+Z toward the viewer. The Blender worker converts once at the boundary so the UI
never has to think about Blender's Z-up world.
"""

from __future__ import annotations

import math
from copy import deepcopy

HUMAN = "humanoid"

# Jiggle defaults are exported verbatim to riggle.json and to the armature's
# glTF extras. They are deliberately engine-neutral numbers in 0..1.
JIGGLE_DEFAULTS = {
    "stiffness": 0.55,
    "damping": 0.35,
    "gravity": 0.25,
    "mass": 0.5,
}


def _p(x: float, y: float, z: float) -> list[float]:
    return [round(x, 6), round(y, 6), round(z, 6)]


def humanoid(bbox: list[float]) -> list[dict]:
    """Build a fitted humanoid skeleton from a model bounding box.

    bbox = [minx, miny, minz, maxx, maxy, maxz]. The preset is a starting point:
    every joint is draggable in the viewport, so the fit only has to be close.
    """
    x0, y0, z0, x1, y1, z1 = [float(v) for v in bbox]
    w, h, d = x1 - x0, y1 - y0, z1 - z0
    if w <= 0 or h <= 0 or d <= 0:
        raise ValueError("model bounding box is empty")
    cx, cz = (x0 + x1) / 2.0, (z0 + z1) / 2.0

    def at(fx: float, fy: float, fz: float = 0.0) -> list[float]:
        return _p(cx + fx * w, y0 + fy * h, cz + fz * d)

    joints: list[dict] = []

    def add(name, parent, head, tail=None, deform=True):
        joints.append({
            "name": name,
            "parent": parent,
            "head": head,
            "tail": tail if tail is not None else head,
            "deform": bool(deform),
        })

    add("root", None, at(0, 0), at(0, 0.52))
    add("hips", "root", at(0, 0.52), at(0, 0.60))
    add("spine", "hips", at(0, 0.60), at(0, 0.68))
    add("chest", "spine", at(0, 0.68), at(0, 0.78))
    add("neck", "chest", at(0, 0.78), at(0, 0.84))
    add("head", "neck", at(0, 0.84), at(0, 0.98))

    for side, sx in (("L", 1.0), ("R", -1.0)):
        add(f"shoulder.{side}", "chest", at(sx * 0.10, 0.74), at(sx * 0.18, 0.74))
        add(f"upper_arm.{side}", f"shoulder.{side}", at(sx * 0.18, 0.74), at(sx * 0.33, 0.74))
        add(f"forearm.{side}", f"upper_arm.{side}", at(sx * 0.33, 0.74), at(sx * 0.46, 0.74))
        add(f"hand.{side}", f"forearm.{side}", at(sx * 0.46, 0.74), at(sx * 0.52, 0.74))
        add(f"thigh.{side}", "hips", at(sx * 0.055, 0.50), at(sx * 0.055, 0.27))
        add(f"shin.{side}", f"thigh.{side}", at(sx * 0.055, 0.27), at(sx * 0.055, 0.045))
        add(f"foot.{side}", f"shin.{side}", at(sx * 0.055, 0.045), at(sx * 0.055, 0.02, 0.08))
        add(f"toe.{side}", f"foot.{side}", at(sx * 0.055, 0.02, 0.08), at(sx * 0.055, 0.02, 0.13))

    return compute_tails(joints)


def compute_tails(joints: list[dict]) -> list[dict]:
    """Fill empty tails from the first child's head, and give leaves a stub.

    A bone with a zero-length tail is invalid in Blender and produces no usable
    twist axis, so every joint ends up with a real direction.
    """
    out = deepcopy(joints)
    children: dict[str, list[dict]] = {}
    for j in out:
        children.setdefault(j.get("parent") or "", []).append(j)
    for j in out:
        head = j["head"]
        tail = j.get("tail") or head
        if _dist(head, tail) < 1e-6:
            kids = [k for k in children.get(j["name"], []) if k is not j]
            if kids:
                j["tail"] = list(kids[0]["head"])
            else:
                j["tail"] = [head[0], head[1] + 0.04, head[2]]
        else:
            j["tail"] = list(tail)
    return out


def validate(joints: list[dict], *, max_bones: int = 512) -> list[dict]:
    """Normalise and validate a skeleton. Raises ValueError on unusable input."""
    if not isinstance(joints, list) or not joints:
        raise ValueError("skeleton has no joints")
    if len(joints) > max_bones:
        raise ValueError(f"skeleton has more than {max_bones} joints")

    names = [str(j.get("name", "")).strip() for j in joints]
    if any(not n for n in names):
        raise ValueError("every joint needs a name")
    if len(set(names)) != len(names):
        raise ValueError("joint names must be unique")

    out = []
    for j in joints:
        head = [float(v) for v in j.get("head", [])]
        tail = [float(v) for v in j.get("tail", head)]
        if len(head) != 3 or len(tail) != 3 or not all(math.isfinite(v) for v in head + tail):
            raise ValueError(f"joint {j['name']!r} has an invalid position")
        out.append({
            "name": str(j["name"]).strip(),
            "parent": (str(j["parent"]).strip() if j.get("parent") else None),
            "head": head,
            "tail": tail,
            "deform": bool(j.get("deform", True)),
            "jiggle": bool(j.get("jiggle", False)),
        })

    by_name = {j["name"]: j for j in out}
    for j in out:
        if j["parent"]:
            if j["parent"] not in by_name:
                raise ValueError(f"joint {j['name']!r} has missing parent {j['parent']!r}")
            if j["parent"] == j["name"]:
                raise ValueError(f"joint {j['name']!r} cannot parent itself")
        if _dist(j["head"], j["tail"]) < 1e-6:
            raise ValueError(f"joint {j['name']!r} has zero length")

    # Walk each chain to the root; a repeat means a cycle.
    for j in out:
        seen, cur = set(), j
        while cur and cur["parent"]:
            if cur["name"] in seen:
                raise ValueError(f"skeleton contains a cycle at {cur['name']!r}")
            seen.add(cur["name"])
            cur = by_name[cur["parent"]]
    return out


def mirror_name(name: str) -> str:
    if name.endswith(".L"):
        return name[:-2] + ".R"
    if name.endswith(".R"):
        return name[:-2] + ".L"
    return name


def mirror_joint(joint: dict) -> dict:
    out = deepcopy(joint)
    out["name"] = mirror_name(joint["name"])
    out["parent"] = mirror_name(joint["parent"]) if joint.get("parent") else None
    for key in ("head", "tail"):
        out[key] = [-joint[key][0], joint[key][1], joint[key][2]]
    return out


def symmetrize(joints: list[dict], source_side: str = "L") -> list[dict]:
    """Copy one side of a skeleton onto the other, keeping non-sided bones."""
    src_suffix = ".L" if source_side.upper() == "L" else ".R"
    out = []
    for j in joints:
        if j["name"].endswith(src_suffix):
            out.append(deepcopy(j))
            twin = mirror_joint(j)
            if not any(x["name"] == twin["name"] for x in joints):
                out.append(twin)
        elif not j["name"].endswith(".L") and not j["name"].endswith(".R"):
            out.append(deepcopy(j))
    return out


def nearest_bone(point: list[float], joints: list[dict], *, deform_only: bool = True) -> str | None:
    """Name of the bone segment closest to a point, used to parent jiggle bones."""
    best, best_d = None, float("inf")
    for j in joints:
        if deform_only and not j.get("deform", True):
            continue
        d = point_segment_distance(point, j["head"], j["tail"])
        if d < best_d:
            best, best_d = j["name"], d
    return best


def point_segment_distance(p, a, b) -> float:
    ax, ay, az = a
    bx, by, bz = b
    px, py, pz = p
    abx, aby, abz = bx - ax, by - ay, bz - az
    denom = abx * abx + aby * aby + abz * abz
    if denom < 1e-12:
        return math.dist(p, a)
    t = ((px - ax) * abx + (py - ay) * aby + (pz - az) * abz) / denom
    t = max(0.0, min(1.0, t))
    return math.dist(p, (ax + abx * t, ay + aby * t, az + abz * t))


def bbox_center_size(bbox: list[float]) -> tuple[list[float], float]:
    x0, y0, z0, x1, y1, z1 = bbox
    return [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2], max(x1 - x0, y1 - y0, z1 - z0)


def _dist(a, b) -> float:
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))
