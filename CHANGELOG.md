# Changelog

## 0.1.1 — 2026-10-19

Packaging release; no app behavior changes.

- Portable Windows build (`Riggle-0.1.1-win64.zip`) with a bundled Python 3.11.9
  embeddable runtime, so Python does not need to be installed.
- `START.bat` prefers the bundled runtime and falls back to `py -3` / `python`.
- Added `packaging/build_portable.py` and SHA-256 checksums for release assets.
- Blender 4.5+ is still required for import, rigging, UV and export.

## 0.1.0 — 2026-10-19

First working release.

- Five-step workbench: Import, Rig, Jiggle, Paint, Export.
- Humanoid skeleton preset fitted to model bounds, draggable joints, mirroring.
- Automatic skin weights with a nearest-four-bone fallback.
- Jiggle bone authoring with parent, radius, length and spring parameters;
  live spring preview and shake.
- Smart UV unwrap, island packing, one texture atlas, SVG UV overlay.
- Docked paint board with brush, eraser, fill, opacity, undo and live 3D preview.
- Export to GLB + FBX + atlas PNG + `riggle.json` + engine README.
- Token-protected local server; headless Blender worker queue; per-project logs.
