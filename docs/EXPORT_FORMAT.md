# Export format

Every export writes the same five things into the folder you choose:

```
<name>.glb                 skinned mesh + skeleton + embedded PNG
<name>.fbx                 same rig, textures embedded
textures/basecolor.png     the painted atlas (only if Auto UV ran)
riggle.json                engine-neutral manifest
README.txt                 short wiring notes
```

## GLB extras

The GLB is a normal glTF 2.0 binary. On top of the standard skin and texture it
carries three custom properties:

| Node | Extra | Value |
|---|---|---|
| armature (`RiggleRig` or the imported rig) | `riggle_manifest` | JSON string of `riggle.json` |
| each jiggle bone | `riggle_jiggle` | `true` |
| each jiggle bone | `riggle_params` | JSON string of the four spring values |

Engines that ignore extras still get a fully skinned, textured model.

## `riggle.json`

```json
{
  "app": "Riggle",
  "version": "0.1.1",
  "manifest_version": 1,
  "name": "My Hero",
  "created": "2026-10-19T15:24:53",
  "source": "hero.fbx",
  "units": "meters",
  "coordinate_system": "glTF Y-up",
  "joints": [
    {
      "name": "hips",
      "parent": "root",
      "head": [0.0, 0.92, 0.0],
      "tail": [0.0, 1.06, 0.0],
      "deform": true
    }
  ],
  "jiggle": {
    "jiggle_breast.L": {
      "parent": "chest",
      "head": [0.10, 1.24, 0.12],
      "tail": [0.10, 1.14, 0.17],
      "radius": 0.26,
      "stiffness": 0.55,
      "damping": 0.35,
      "gravity": 0.25,
      "mass": 0.5
    }
  },
  "jiggle_order": ["jiggle_breast.L"],
  "uv": { "size": 1024, "materials": 1 },
  "texture": "textures/basecolor.png"
}
```

### Spring parameters

All four are normalised `0..1`, so they can be mapped to any engine's units:

| Key | Meaning | Typical |
|---|---|---|
| `stiffness` | spring constant / pull back to rest | `0.55` |
| `damping` | velocity loss per step | `0.35` |
| `gravity` | constant droop along -Y | `0.25` |
| `mass` | inertia; higher = slower, heavier | `0.5` |

The viewport preview uses a damped quaternion spring:

```
force      = (rest - current) * stiffness
force     += gravity * down
velocity   = (velocity + force / mass) * (1 - damping)
current   += velocity * dt
```

That is intentionally simple; it is a feel reference, not a physics solver.

## Engine wiring

### three.js

```js
const manifest = JSON.parse(gltf.parser.json.nodes
  .find(n => n.name === 'RiggleRig').extras.riggle_manifest);

for (const [name, cfg] of Object.entries(manifest.jiggle)) {
  const bone = skeleton.getBoneByName(name);
  springs.push({ bone, rest: bone.quaternion.clone(), cfg, vel: new THREE.Vector3() });
}
```

`web/js/viewport.js` contains the working version used by the live preview.

### Unity

Add a Dynamic Bone or PhysBone component to each transform named in
`manifest.jiggle`. Map `stiffness` → spring, `damping` → damping, `gravity` →
gravity, `mass` → inertia. The bones are already skinned, so no re-rigging.

### Unreal

Add AnimDynamics or a physics constraint to the named bones. `radius` is the
weight radius used when the bone was authored, useful for collision capsules.

### Blender

Import the FBX. Bone names and hierarchy are preserved; add a `Limit Rotation`
or spring constraint per jiggle bone.

## Notes

- The mesh is skinned to the skeleton. Renaming bones requires updating the
  manifest.
- FBX units follow the Blender scene (metres); glTF is metres by definition.
- Existing animations are not exported.
- The GLB texture is embedded; the PNG is written alongside for engines that
  prefer external textures.
