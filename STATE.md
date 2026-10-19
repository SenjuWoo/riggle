# Riggle — state

**Goal:** a local, open-source Character Creator alternative for the useful 20%:
import a model, fit a skeleton, author jiggle bones, unwrap + paint a texture
atlas, export a fully rigged GLB/FBX into a folder you choose.

**Version:** 0.1.0
**Repo:** <https://github.com/SenjuWoo/riggle> — public, **heavy WIP**
**Protection:** `main` requires the `tests` workflow on Windows + Ubuntu, a pull
request, and linear history; force pushes and deletions are blocked.
**Status:** feature-complete for the five-step workflow; verified end-to-end with
real Blender 5.2 and a real HTTP client. Interfaces and project format may change.

## Done

- Five-step UI: Import → Rig → Jiggle → Paint → Export.
- Import: `.blend`, `.fbx`, `.glb`, `.gltf`, `.obj`, `.stl`, `.ply`, `.usd`,
  `.usdz`; scene normalisation; feet-on-floor.
- Rig: fitted 22-joint humanoid preset, drag joints, mirroring, automatic weights
  with nearest-bone fallback, existing-rig preservation.
- Jiggle: click-to-place bones, parent/radius/length controls, four spring
  parameters, weight transfer, live spring preview, shake.
- Paint: smart UV unwrap + island packing, one atlas, SVG UV overlay, docked
  canvas with live 3D texture updates, brush/eraser/fill/undo, autosave on export.
- Export: GLB (skin + texture + extras), FBX (embedded textures), atlas PNG,
  `riggle.json`, `README.txt`; manifest on the armature node.
- Local server: `127.0.0.1`, startup token, path containment, 4 GB upload cap.
- Tests: 19 unit/API tests with a fake Blender runner; real Blender smoke test.

## Verified

- `python -m unittest discover -s tests -p "test_*.py"` — pass.
- `python tests/blender_smoke.py` — pass against Blender 5.2.
- Real HTTP pipeline (upload → import → bind → jiggle → unwrap → paint → export)
  — pass; exported GLB has 24 joints, 2 jiggle bones, 1 PNG texture, manifest.
- Browser UI (headless Chromium over CDP): import → bind → place jiggle bone →
  unwrap → paint stroke → save → export — pass; no console exceptions.
- Live paint preview: filling the atlas pink changed 32,965 model pixels.
- Live spring preview: shaking changed both jiggle-bone quaternions.

## Run

```bash
cd "Z:/Backup/Ai Apps/Apps/Riggle"
python run.py                 # http://127.0.0.1:8765/
python run.py --port 8788 --no-browser
```

Windows: double-click `START.bat`.

## Logs

- App: `%LOCALAPPDATA%\Riggle\riggle.log`
- Project: `<project>/logs/riggle.log`
- Blender jobs: `<project>/logs/jobs/*.json`
- `RIGGLE_DEBUG=1` for verbose output.

## Known gaps / next steps

- No manual weight painting; automatic weights only (with distance fallback).
- One atlas per character; multi-material source textures are replaced in the
  project copy.
- Jiggle needs an engine-side spring component; only the preview simulates it.
- No animation export; no ARKit/VRM/UE mannequin preset.
- Humanoid preset only; no quadruped/creature presets.
- No project delete/rename UI (files can be managed on disk).

## Last good version

`0.1.0` — tag `v0.1.0` (pre-release), built from protected `main`. Demo project:
**Riggle Demo** (`~/RiggleProjects/fixture-9a6287`) with 24 joints, 2 jiggle bones
and a painted atlas.
