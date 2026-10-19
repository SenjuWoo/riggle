"""Build a small humanoid test model. Run inside Blender:
blender --background --factory-startup --python tests/make_fixture.py -- out.glb
"""

import sys
import bpy

out = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)

parts = []


def sphere(name, loc, scale):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=16, radius=1, location=loc)
    ob = bpy.context.object
    ob.name = name
    ob.scale = scale
    parts.append(ob)


def limb(name, loc, radius, depth, axis):
    bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=radius, depth=depth, location=loc)
    ob = bpy.context.object
    ob.name = name
    if axis == "X":
        ob.rotation_euler[1] = 1.5707963
    parts.append(ob)


sphere("Torso", (0, 0, 1.08), (0.22, 0.14, 0.42))
sphere("Hips", (0, 0, 0.72), (0.18, 0.14, 0.16))
sphere("Head", (0, 0, 1.62), (0.13, 0.13, 0.15))
for side, sx in (("L", 1), ("R", -1)):
    limb(f"Arm.{side}", (sx * 0.38, 0, 1.35), 0.05, 0.68, "X")
    limb(f"Leg.{side}", (sx * 0.10, 0, 0.42), 0.07, 0.84, "Z")

bpy.ops.object.select_all(action="DESELECT")
for ob in parts:
    ob.select_set(True)
bpy.context.view_layer.objects.active = parts[0]
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
bpy.ops.object.join()
body = bpy.context.object
body.name = "FixtureBody"
bpy.ops.object.shade_smooth()
mat = bpy.data.materials.new("FixtureMat")
mat.use_nodes = True
body.data.materials.append(mat)

bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_apply=True)
print("FIXTURE_OK", out)
