# Contributing

Riggle is a small app on purpose: Python stdlib on the server, vendored Three.js
in the browser, Blender headless for geometry. Keep it that way.

## Setup

- Python 3.10+
- Blender 4.5+ (found automatically, or set it in **Settings**)
- No pip install, no build step

```bash
python run.py
```

## Before a pull request

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python tests/blender_smoke.py          # needs Blender; runs the real pipeline
node --input-type=module --check < web/js/app.js
node --input-type=module --check < web/js/viewport.js
node --input-type=module --check < web/js/paint.js
```

The unit/API suite uses `FakeRunner`, so it runs without Blender. The smoke test
imports a generated humanoid, binds it, adds a jiggle bone, unwraps, paints and
exports both GLB and FBX.

## Rules

- **No new runtime dependency** unless the standard library or a platform API
  cannot do the job. The browser stays build-free; Three.js stays vendored.
- **Local only.** The server binds `127.0.0.1` and requires its per-run token.
  Do not add a network listener, telemetry or cloud calls.
- **Preserve user files.** Imports are copied into the project; exports go to a
  folder the user chooses. Writes that matter are atomic.
- **One behavior, one test.** If it branches, loops, parses or touches money or
  security, add a small test or an assert-based self-check.
- **No model libraries.** Riggle is the rigging/painting/export tool, not a
  content store.
- Keep the UI's five-step shape: Import → Rig → Jiggle → Paint → Export.

## Layout

| Path | What |
|---|---|
| `riggle/server.py` | HTTP API, static files, workflow routes |
| `riggle/jobs.py` | single Blender job queue |
| `riggle/blender_worker.py` | all geometry work inside Blender |
| `riggle/rig.py` | humanoid preset, jiggle defaults, mirroring |
| `riggle/store.py` | config and project folders |
| `web/js/app.js` | UI state and API calls |
| `web/js/viewport.js` | Three.js viewport, handles, spring preview |
| `web/js/paint.js` | texture atlas painting |
| `docs/` | architecture and export schema |

## Reporting bugs

Include the Riggle version, Blender version, OS, the model format, and the job
log from the footer (**Log**) or `logs/riggle.log` in the project folder. Do not
attach private models; a reduced fixture is better.
