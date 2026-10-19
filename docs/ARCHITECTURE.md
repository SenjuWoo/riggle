# Architecture

Riggle is deliberately three small pieces instead of one big app.

```
┌──────────────────────────────┐
│ web/  (Three.js + canvas)    │  UI, viewport, paint board
└──────────────┬───────────────┘
               │ HTTP, 127.0.0.1, X-Riggle-Token
┌──────────────▼───────────────┐
│ riggle/server.py             │  stdlib ThreadingHTTPServer
│ riggle/jobs.py               │  one background Blender at a time
└──────────────┬───────────────┘
               │ job.json → blender --background --python blender_worker.py
┌──────────────▼───────────────┐
│ riggle/blender_worker.py     │  import, rig, weights, UV, export
└──────────────────────────────┘
```

## Modules

| File | Responsibility |
|---|---|
| `run.py` | CLI launcher (`--port`, `--no-browser`, `--version`). |
| `riggle/server.py` | HTTP routes, token auth, static files, project state, manifest. |
| `riggle/jobs.py` | Job queue, subprocess lifecycle, progress parsing, deadlines, `FakeRunner`. |
| `riggle/blender_worker.py` | All `bpy` work. Reads a job JSON, writes a result JSON, prints progress lines. |
| `riggle/rig.py` | Humanoid preset, joint validation, mirroring, jiggle defaults. |
| `riggle/store.py` | Config, project folders, atomic writes, Blender discovery, logs. |
| `web/js/app.js` | UI state machine, API calls, job polling, panels, shortcuts. |
| `web/js/viewport.js` | Three.js scene, orbit/transform controls, joints, picking, spring preview. |
| `web/js/paint.js` | Atlas canvas, UV overlay, brush/eraser/undo, `CanvasTexture`. |

## Project layout

```
~/RiggleProjects/<name>-<id>/
  project.json          state: stage, bbox, mesh stats, joints, jiggle, uv, export
  imports/<source>      untouched copy of the uploaded file
  work/base.blend       imported + normalised scene
  work/rigged.blend     after bind / jiggle / unwrap
  work/model.glb        preview + final GLB source
  work/texture.png      painted atlas
  work/uv.svg           vector UV layout
  logs/riggle.log
  logs/jobs/*.json      request + result for every Blender run
```

`project.json` writes are atomic (`tmp` + `os.replace`). A killed Blender never
leaves a half-written project.

## Job protocol

`jobs.py` writes a request JSON, starts:

```
blender --background --factory-startup --python blender_worker.py -- job.json result.json
```

The worker prints `RIGGLE_PROGRESS {"pct": 45, "msg": "..."}` lines and
`RIGGLE_WARN` / `RIGGLE_ERROR`. The queue parses those into the job object the UI
polls at `/api/job?id=...`. Only one Blender process runs at a time; jobs queue.

Job kinds and their inputs:

| Kind | Input | Output |
|---|---|---|
| `import` | source, base.blend, model.glb | bbox, mesh stats, existing joints |
| `bind` | joints, base.blend, rigged.blend, model.glb | joints, weight report |
| `jiggle` | bone specs, rigged.blend, model.glb | added bones, full joint list |
| `unwrap` | size, blend, texture.png, uv.svg, model.glb | atlas stats |
| `export` | blend, texture, GLB/FBX paths, manifest | file sizes |

## Coordinates

The app speaks glTF everywhere: **+X right, +Y up, +Z toward the viewer**.
Conversion happens only at the Blender boundary:

```python
to_blender(p) = (p.x, -p.z, p.y)
to_gltf(p)    = (p.x, p.z, -p.y)
```

`normalise_scene()` applies rotation/scale for unrigged meshes and translates the
top-level objects so the feet sit on Y=0. Rigged imports skip the transform apply
so the existing bind pose is preserved.

## Security

- Binds `127.0.0.1` only.
- Mints a random token at startup, injects it into `index.html`, and requires it
  on every `/api/*` call and media URL.
- `/api/file` resolves inside the open project only; `/static/*` resolves inside
  `web/` only.
- Uploads are renamed, extension-checked, capped at 4 GB, and stored in a
  per-upload incoming folder.
- Blender runs with `--factory-startup`; imported files are copied, never opened
  in place.

## Testing

- `tests/test_rig.py` — preset shape, fit, validation, mirroring.
- `tests/test_store.py` — project roundtrip, path containment, recent ordering.
- `tests/test_server.py` — token auth, upload validation, full workflow with
  `FakeRunner`, path traversal, texture/export.
- `tests/blender_smoke.py` — real Blender end-to-end.
- `tests/make_fixture.py` — builds the humanoid GLB fixture.

`FakeRunner` mimics the worker's JSON/progress contract, so the HTTP and UI layers
can be tested without launching Blender. Keep its result shape in sync with
`blender_worker.py` when adding a job kind.

## Adding a job kind

1. Add a handler to `JOBS` in `blender_worker.py`.
2. Add a method on `App` in `server.py` that builds the request and submits it
   with `_submit(kind, request, apply)`.
3. Add the route in `Handler.do_POST`.
4. Teach `FakeRunner` the same kind for tests.
5. Add a UI action and poll it with `waitJob()`.

Keep every Blender mutation inside the worker; the server never imports `bpy`.
