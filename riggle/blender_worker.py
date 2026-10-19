"""Blender-side worker. Runs headless: blender --background --python this -- job.json result.json

Every job opens a saved .blend, changes it, saves it again and writes a small JSON
result. The browser never talks to Blender directly, so a crashed job can never
leave the UI holding a half-edited scene.
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

WARNINGS: list[str] = []


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------
def progress(pct: int, msg: str) -> None:
    print("RIGGLE_PROGRESS " + json.dumps({"pct": int(pct), "msg": msg}), flush=True)


def warn(msg: str) -> None:
    WARNINGS.append(msg)
    print("RIGGLE_WARN " + json.dumps({"msg": msg}), flush=True)


def to_blender(p) -> tuple[float, float, float]:
    """glTF/three.js (x, y, z) -> Blender (x, -z, y)."""
    return (float(p[0]), -float(p[2]), float(p[1]))


def to_gltf(p) -> list[float]:
    """Blender (x, y, z) -> glTF/three.js (x, z, -y)."""
    return [round(float(p[0]), 6), round(float(p[2]), 6), round(-float(p[1]), 6)]


def reset() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def load_blend(path: str) -> None:
    if not Path(path).is_file():
        raise FileNotFoundError(f"missing blend file: {path}")
    bpy.ops.wm.open_mainfile(filepath=path)


def save_blend(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)


def meshes() -> list:
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def armatures() -> list:
    return [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]


def select_only(objects, active=None) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = active or objects[0]


def world_bbox(objects) -> list[float]:
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for o in objects:
        for corner in o.bound_box:
            p = o.matrix_world @ Vector(corner)
            for i in range(3):
                lo[i] = min(lo[i], p[i])
                hi[i] = max(hi[i], p[i])
    if not all(math.isfinite(v) for v in lo + hi):
        raise ValueError("could not measure the imported model")
    return [lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]]


def bbox_gltf(blender_bbox) -> list[float]:
    x0, y0, z0, x1, y1, z1 = blender_bbox
    # y/z swap: glTF Y spans Blender z, glTF Z spans -Blender y.
    return [x0, z0, -y1, x1, z1, -y0]


def triangle_count(objects) -> int:
    total = 0
    for o in objects:
        me = o.data
        me.calc_loop_triangles()
        total += len(me.loop_triangles)
    return total


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------
def import_model(path: str) -> None:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(p))
        return
    reset()
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(p))
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(p))
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=str(p))
    elif ext == ".stl":
        bpy.ops.wm.stl_import(filepath=str(p))
    elif ext == ".ply":
        bpy.ops.wm.ply_import(filepath=str(p))
    elif ext == ".usd" or ext == ".usdz":
        bpy.ops.wm.usd_import(filepath=str(p))
    else:
        raise ValueError(
            f"unsupported format {ext or '(none)'}; use .blend, .fbx, .glb, .gltf, "
            ".obj, .stl, .ply or .usd"
        )


def normalise_scene() -> None:
    """Apply rotation/scale where it is safe, then put the feet on the floor."""
    mesh_objects = meshes()
    if not mesh_objects:
        raise ValueError("the file contains no mesh objects")
    rigged = bool(armatures())
    if not rigged:
        select_only(mesh_objects, mesh_objects[0])
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    box = world_bbox(mesh_objects)
    delta = Vector((
        -(box[0] + box[3]) / 2.0,
        -(box[1] + box[4]) / 2.0,
        -box[2],
    ))
    tops = [o for o in bpy.context.scene.objects if o.parent is None and o.type in {"MESH", "ARMATURE"}]
    for o in tops:
        o.location = o.location + delta
    bpy.context.view_layer.update()


def bone_list() -> list[dict]:
    out = []
    for arm in armatures():
        for bone in arm.data.bones:
            head = arm.matrix_world @ bone.head_local
            tail = arm.matrix_world @ bone.tail_local
            out.append({
                "name": bone.name,
                "parent": bone.parent.name if bone.parent else None,
                "head": to_gltf(head),
                "tail": to_gltf(tail),
                "deform": bool(bone.use_deform),
                "jiggle": bool(bone.get("riggle_jiggle", False)),
            })
    return out


def scene_info() -> dict:
    mesh_objects = meshes()
    box = world_bbox(mesh_objects)
    materials = {m.name for o in mesh_objects for m in o.data.materials if m}
    bones = bone_list()
    return {
        "bbox": bbox_gltf(box),
        "mesh": {
            "objects": len(mesh_objects),
            "verts": sum(len(o.data.vertices) for o in mesh_objects),
            "tris": triangle_count(mesh_objects),
            "materials": len(materials),
        },
        "has_rig": bool(armatures()),
        "joints": bones,
    }


def export_glb(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        export_skins=True,
        export_animations=False,
        export_extras=True,
        export_apply=False,
        export_yup=True,
    )


# ---------------------------------------------------------------------------
# rigging
# ---------------------------------------------------------------------------
def create_armature(joints: list[dict]) -> object:
    for old in armatures():
        for ob in meshes():
            for mod in list(ob.modifiers):
                if mod.type == "ARMATURE" and mod.object == old:
                    ob.modifiers.remove(mod)
        bpy.data.objects.remove(old, do_unlink=True)
    bpy.ops.object.armature_add(enter_editmode=True, location=(0, 0, 0))
    arm = bpy.context.object
    arm.name = "RiggleRig"
    arm.data.name = "RiggleRig"
    edit = arm.data.edit_bones
    for b in list(edit):
        edit.remove(b)

    by_name = {}
    for j in joints:
        bone = edit.new(j["name"])
        bone.head = to_blender(j["head"])
        bone.tail = to_blender(j["tail"])
        bone.use_deform = bool(j.get("deform", True))
        by_name[j["name"]] = bone
    for j in joints:
        if j.get("parent"):
            bone = by_name[j["name"]]
            bone.parent = by_name[j["parent"]]
            bone.use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    arm.show_in_front = True
    return arm


def bind_automatic(arm, mesh_objects) -> None:
    select_only(mesh_objects + [arm], arm)
    try:
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    except RuntimeError as exc:
        warn(f"automatic weights failed ({exc}); using distance weights")
        fallback_weights(arm, mesh_objects)
    for ob in mesh_objects:
        if not any(m.type == "ARMATURE" for m in ob.modifiers):
            mod = ob.modifiers.new("Armature", "ARMATURE")
            mod.object = arm
        if not ob.vertex_groups:
            warn(f"{ob.name}: automatic weights produced no groups; using distance weights")
            fallback_weights(arm, [ob])


def deform_bones(arm) -> list:
    return [b for b in arm.data.bones if b.use_deform]


def fallback_weights(arm, mesh_objects) -> None:
    """Nearest-four-bone weights, used only when Blender's heat solver gives up."""
    bones = deform_bones(arm)
    for ob in mesh_objects:
        groups = {b.name: ob.vertex_groups.get(b.name) or ob.vertex_groups.new(name=b.name) for b in bones}
        for v in ob.data.vertices:
            p = ob.matrix_world @ v.co
            ranked = sorted(
                ((point_segment_distance(p, arm.matrix_world @ b.head_local, arm.matrix_world @ b.tail_local), b)
                 for b in bones),
                key=lambda pair: pair[0],
            )[:4]
            weights = [(1.0 / (d * d + 1e-6), b) for d, b in ranked]
            total = sum(w for w, _ in weights) or 1.0
            for w, b in weights:
                groups[b.name].add([v.index], w / total, "REPLACE")


def point_segment_distance(p, a, b) -> float:
    ab = b - a
    denom = ab.length_squared
    if denom < 1e-12:
        return (p - a).length
    t = max(0.0, min(1.0, (p - a).dot(ab) / denom))
    return (p - (a + ab * t)).length


def weight_report(mesh_objects, arm) -> dict:
    names = {b.name for b in deform_bones(arm)}
    unweighted = 0
    total = 0
    for ob in mesh_objects:
        index_names = {g.index: g.name for g in ob.vertex_groups}
        for v in ob.data.vertices:
            total += 1
            if not any(index_names.get(g.group) in names and g.weight > 1e-5 for g in v.groups):
                unweighted += 1
    return {"verts": total, "unweighted": unweighted}


def add_jiggle_bone(spec: dict, arm) -> dict:
    name = spec["name"]
    parent = spec.get("parent")
    if name in arm.data.bones:
        raise ValueError(f"bone {name!r} already exists")
    if parent and parent not in arm.data.bones:
        raise ValueError(f"parent bone {parent!r} does not exist")
    radius = float(spec.get("radius", 0.1))
    if radius <= 0:
        raise ValueError("jiggle radius must be positive")

    select_only([arm], arm)
    bpy.ops.object.mode_set(mode="EDIT")
    edit = arm.data.edit_bones
    bone = edit.new(name)
    bone.head = to_blender(spec["head"])
    bone.tail = to_blender(spec["tail"])
    bone.use_deform = True
    if parent:
        bone.parent = edit[parent]
        bone.use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")

    data_bone = arm.data.bones[name]
    head_w = arm.matrix_world @ data_bone.head_local
    tail_w = arm.matrix_world @ data_bone.tail_local
    touched = 0
    for ob in meshes():
        group = ob.vertex_groups.get(name) or ob.vertex_groups.new(name=name)
        names = {g.index: g.name for g in ob.vertex_groups}
        for v in ob.data.vertices:
            p = ob.matrix_world @ v.co
            d = point_segment_distance(p, head_w, tail_w)
            if d >= radius:
                continue
            t = d / radius
            w = (1.0 - t * t) ** 2
            if w < 1e-4:
                continue
            touched += 1
            for g in list(v.groups):
                gname = names.get(g.group)
                if gname and gname != name:
                    ob.vertex_groups[gname].add([v.index], g.weight * (1.0 - w), "REPLACE")
            group.add([v.index], w, "REPLACE")
    return {"name": name, "weighted_verts": touched}


# ---------------------------------------------------------------------------
# UV + texture
# ---------------------------------------------------------------------------
def make_atlas(size: int, path: str):
    for img in list(bpy.data.images):
        if img.name == "RiggleAtlas":
            bpy.data.images.remove(img)
    img = bpy.data.images.new("RiggleAtlas", width=size, height=size, alpha=False)
    img.generated_color = (0.82, 0.82, 0.84, 1.0)
    img.colorspace_settings.name = "sRGB"
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    return img


def assign_atlas(img) -> int:
    count = 0
    for ob in meshes():
        for slot in ob.material_slots:
            mat = slot.material
            if not mat:
                continue
            mat.use_nodes = True
            tree = mat.node_tree
            bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if not bsdf:
                continue
            for link in list(tree.links):
                if link.to_node == bsdf and link.to_socket.name == "Base Color":
                    tree.links.remove(link)
            tex = tree.nodes.new("ShaderNodeTexImage")
            tex.name = "Riggle Atlas"
            tex.image = img
            tex.interpolation = "Linear"
            tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            count += 1
    return count


def unwrap(size: int, blend: str, texture: str, svg: str) -> dict:
    mesh_objects = meshes()
    if not mesh_objects:
        raise ValueError("no mesh to unwrap")
    select_only(mesh_objects, mesh_objects[0])
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
    try:
        bpy.ops.uv.pack_islands(rotate=True, margin=0.02)
    except RuntimeError as exc:
        warn(f"pack islands skipped: {exc}")
    bpy.ops.object.mode_set(mode="OBJECT")

    img = make_atlas(size, texture)
    materials = assign_atlas(img)
    if materials == 0:
        warn("no Principled materials found; the atlas is saved but not assigned")
    bpy.ops.uv.export_layout(filepath=svg, export_all=True, mode="SVG", size=(1024, 1024))
    save_blend(blend)
    return {"size": size, "materials": materials, "texture": texture, "svg": svg}


def apply_texture(path: str) -> None:
    if not Path(path).is_file():
        return
    img = bpy.data.images.get("RiggleAtlas")
    if img is None:
        img = bpy.data.images.load(path)
        img.name = "RiggleAtlas"
    else:
        img.filepath = path
        img.reload()
    assign_atlas(img)


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------
def job_import(req: dict) -> dict:
    progress(5, "importing model")
    import_model(req["source"])
    progress(45, "normalising scene")
    normalise_scene()
    info = scene_info()
    blend = req["blend"]
    progress(70, "saving project scene")
    save_blend(blend)
    progress(85, "writing preview")
    export_glb(req["glb"])
    info["glb"] = req["glb"]
    info["blend"] = blend
    return info


def job_bind(req: dict) -> dict:
    load_blend(req["blend"])
    progress(10, "building armature")
    arm = create_armature(req["joints"])
    progress(40, "binding automatic weights")
    bind_automatic(arm, meshes())
    report = weight_report(meshes(), arm)
    if report["unweighted"]:
        warn(f"{report['unweighted']} vertices have no body weight")
    progress(80, "saving rigged scene")
    save_blend(req["out_blend"])
    export_glb(req["glb"])
    return {
        "joints": bone_list(),
        "weights": report,
        "glb": req["glb"],
        "blend": req["out_blend"],
    }


def job_jiggle(req: dict) -> dict:
    load_blend(req["blend"])
    arms = armatures()
    if not arms:
        raise ValueError("this model has no rig; bind a skeleton first")
    arm = arms[0]
    added = []
    for i, spec in enumerate(req["bones"]):
        progress(15 + int(60 * i / max(1, len(req["bones"]))), f"adding {spec['name']}")
        added.append(add_jiggle_bone(spec, arm))
        bone = arm.data.bones.get(spec["name"])
        if bone:
            bone["riggle_jiggle"] = True
            bone["riggle_params"] = json.dumps(spec.get("params") or {})
    progress(85, "saving jiggle rig")
    save_blend(req["out_blend"])
    export_glb(req["glb"])
    return {"added": added, "joints": bone_list(), "glb": req["glb"], "blend": req["out_blend"]}


def job_unwrap(req: dict) -> dict:
    load_blend(req["blend"])
    progress(20, "unwrapping")
    result = unwrap(int(req.get("size", 1024)), req["out_blend"], req["texture"], req["svg"])
    progress(85, "writing preview")
    export_glb(req["glb"])
    result["glb"] = req["glb"]
    result["blend"] = req["out_blend"]
    return result


def job_export(req: dict) -> dict:
    load_blend(req["blend"])
    progress(10, "preparing textures")
    apply_texture(req.get("texture", ""))
    arms = armatures()
    if arms and req.get("manifest"):
        arms[0]["riggle_manifest"] = json.dumps(req["manifest"], separators=(",", ":"))
    save_blend(req["blend"])
    progress(40, "exporting GLB")
    export_glb(req["glb"])
    progress(65, "exporting FBX")
    fbx_args = dict(
        filepath=req["fbx"],
        use_selection=False,
        object_types={"ARMATURE", "MESH"},
        add_leaf_bones=False,
        bake_anim=False,
        path_mode="COPY",
        embed_textures=True,
        mesh_smooth_type="FACE",
    )
    try:
        bpy.ops.export_scene.fbx(**fbx_args)
    except TypeError:
        fbx_args.pop("embed_textures", None)
        bpy.ops.export_scene.fbx(**fbx_args)
    progress(90, "done")
    return {
        "glb": req["glb"],
        "fbx": req["fbx"],
        "glb_bytes": os.path.getsize(req["glb"]),
        "fbx_bytes": os.path.getsize(req["fbx"]),
    }


JOBS = {
    "import": job_import,
    "bind": job_bind,
    "jiggle": job_jiggle,
    "unwrap": job_unwrap,
    "export": job_export,
}


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    if len(argv) != 2:
        print("usage: blender_worker.py -- job.json result.json", file=sys.stderr)
        return 2
    job_path, result_path = argv
    req = json.loads(Path(job_path).read_text(encoding="utf-8"))
    result = {"ok": False, "job": req.get("job"), "warnings": WARNINGS}
    try:
        handler = JOBS.get(req.get("job"))
        if not handler:
            raise ValueError(f"unknown job {req.get('job')!r}")
        result["result"] = handler(req)
        result["ok"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        print("RIGGLE_ERROR " + json.dumps({"error": result["error"]}), flush=True)
    result["warnings"] = WARNINGS
    Path(result_path).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
