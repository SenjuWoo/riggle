"""Local HTTP API and static file server.

The app binds 127.0.0.1 only and mints a token at startup. The page gets the token
in its HTML; every API call and media URL must present it, which keeps other
browser tabs from driving the local Blender worker.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import APP_NAME, VERSION
from . import jobs, rig, store

ALLOWED_SOURCE = {".blend", ".fbx", ".glb", ".gltf", ".obj", ".stl", ".ply", ".usd", ".usdz"}
MIME = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".fbx": "application/octet-stream",
    ".blend": "application/octet-stream",
}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class App:
    def __init__(self, runner=None):
        self.token = secrets.token_urlsafe(24)
        self.jobs = jobs.JobManager()
        self.runner = runner or jobs.make_runner()
        self.lock = threading.RLock()
        self.cfg = store.load_config()
        self.project: store.Project | None = None
        self._open_last()

    def _open_last(self) -> None:
        last = self.cfg.get("last_project")
        if last and (Path(last) / "project.json").is_file():
            try:
                self.project = store.Project.open(last)
            except store.ProjectError:
                self.project = None

    def state(self) -> dict:
        cfg = dict(self.cfg)
        blender = cfg.get("blender") or ""
        return {
            "app": APP_NAME,
            "version": VERSION,
            "token": self.token,
            "blender": {
                "path": blender,
                "found": bool(blender and Path(blender).is_file()),
                "version": store.blender_version(blender) if blender else "",
            },
            "projects_dir": cfg.get("projects_dir"),
            "last_export_dir": cfg.get("last_export_dir", ""),
            "default_export_dir": str(Path(cfg.get("projects_dir") or store.DEFAULT_PROJECTS).parent / "RiggleExports"),
            "debug": bool(cfg.get("debug")),
            "fake": isinstance(self.runner, jobs.FakeRunner),
            "project": self.project.public() if self.project else None,
            "recent": store.recent_projects(),
        }

    # -- project lifecycle -------------------------------------------------
    def create_project(self, name: str, source: Path) -> store.Project:
        if not source.is_file():
            raise ApiError("uploaded file is missing")
        if source.suffix.lower() not in ALLOWED_SOURCE:
            raise ApiError(f"unsupported model format: {source.suffix or '(none)'}")
        project = store.Project.create(name or source.stem, source)
        dest = project.file(f"imports/{source.name}")
        try:
            shutil.move(str(source), dest)
        except OSError:
            shutil.copy2(source, dest)
        project.data["source"] = project.rel(dest)
        project.data["source_name"] = source.name
        project.save()
        with self.lock:
            self.project = project
            self.cfg["last_project"] = str(project.path)
            store.save_config(self.cfg)
        return project

    def start_import(self, project: store.Project) -> jobs.Job:
        source = project.file(project.data["source"])
        request = {
            "source": str(source),
            "blend": str(project.work("base.blend")),
            "glb": str(project.work("model.glb")),
            "_dir": str(project.path / "logs" / "jobs"),
        }

        def run(job):
            result = self.runner.run(job, request)
            with self.lock:
                project.data.update({
                    "bbox": result["bbox"],
                    "mesh": result["mesh"],
                    "has_rig": result["has_rig"],
                    "joints": result.get("joints") or (
                        [] if result["has_rig"] else rig.humanoid(result["bbox"])
                    ),
                    "stage": "imported",
                })
                if result["has_rig"]:
                    project.data["jiggle_bones"] = [
                        {"name": j["name"], "params": dict(rig.JIGGLE_DEFAULTS)}
                        for j in project.data["joints"] if j.get("jiggle")
                    ]
                project.save()
                project.log(f"import {project.data['source_name']} verts={result['mesh']['verts']}")
            return {"project": project.public(), "result": result}

        return self.jobs.submit("import", run)

    # -- job helpers -------------------------------------------------------
    def _require_project(self) -> store.Project:
        if not self.project:
            raise ApiError("no project is open")
        return self.project

    def _submit(self, kind: str, request: dict, apply) -> jobs.Job:
        project = self._require_project()

        def run(job):
            try:
                result = self.runner.run(job, request)
            except Exception as exc:
                project.log(f"{kind} failed: {type(exc).__name__}: {exc}", error=True)
                raise
            with self.lock:
                apply(project, result)
                project.save()
                project.log(f"{kind} ok")
            return {"project": project.public(), "result": result}

        return self.jobs.submit(kind, run)

    def bind(self, joints: list[dict]) -> jobs.Job:
        project = self._require_project()
        if project.data.get("has_rig"):
            raise ApiError("this model already has a rig; add jiggle bones instead")
        if not project.data.get("bbox"):
            raise ApiError("import a model first")
        clean = rig.validate(joints)
        request = {
            "blend": str(project.work("base.blend")),
            "out_blend": str(project.work("rigged.blend")),
            "glb": str(project.work("model.glb")),
            "joints": clean,
            "_dir": str(project.path / "logs" / "jobs"),
        }

        def apply(p, result):
            p.data["joints"] = result["joints"]
            p.data["has_rig"] = True
            p.data["stage"] = "rigged"
            p.data["weights"] = result.get("weights", {})

        return self._submit("bind", request, apply)

    def preset(self) -> list[dict]:
        project = self._require_project()
        if not project.data.get("bbox"):
            raise ApiError("import a model first")
        return rig.humanoid(project.data["bbox"])

    def set_jiggle_params(self, name: str, params: dict) -> dict:
        project = self._require_project()
        clean = {}
        for key in ("stiffness", "damping", "gravity", "mass"):
            if key in params:
                try:
                    clean[key] = max(0.0, min(1.0, float(params[key])))
                except (TypeError, ValueError) as exc:
                    raise ApiError(f"{key} must be a number") from exc
        for bone in project.data.get("jiggle_bones", []):
            if bone["name"] == name:
                bone.setdefault("params", {}).update(clean)
                project.save()
                return project.public()
        raise ApiError(f"no jiggle bone named {name!r}", 404)

    def add_jiggle(self, bones: list[dict]) -> jobs.Job:
        project = self._require_project()
        if not project.data.get("joints"):
            raise ApiError("rig the model before adding jiggle bones")
        existing = {j["name"] for j in project.data["joints"]}
        clean = []
        for spec in bones:
            name = str(spec.get("name", "")).strip()
            if not name or not re.match(r"^[A-Za-z0-9_.-]{1,63}$", name):
                raise ApiError(f"invalid bone name {name!r}")
            if name in existing:
                raise ApiError(f"bone {name!r} already exists")
            parent = spec.get("parent")
            if parent and parent not in existing:
                raise ApiError(f"parent bone {parent!r} does not exist")
            head, tail = spec.get("head"), spec.get("tail")
            if not (isinstance(head, list) and isinstance(tail, list) and len(head) == 3 and len(tail) == 3):
                raise ApiError(f"bone {name!r} needs head and tail positions")
            if sum((float(a) - float(b)) ** 2 for a, b in zip(head, tail)) < 1e-8:
                raise ApiError(f"bone {name!r} has zero length")
            radius = float(spec.get("radius", 0.1))
            if not (0 < radius <= 10):
                raise ApiError("jiggle radius must be between 0 and 10 metres")
            clean.append({
                "name": name,
                "parent": parent,
                "head": [float(v) for v in head],
                "tail": [float(v) for v in tail],
                "radius": radius,
                "params": {**rig.JIGGLE_DEFAULTS, **(spec.get("params") or {})},
            })
        blend = project.work("rigged.blend")
        if not blend.is_file():
            blend = project.work("base.blend")
        request = {
            "blend": str(blend),
            "out_blend": str(project.work("rigged.blend")),
            "glb": str(project.work("model.glb")),
            "bones": clean,
            "_joints": project.data["joints"],
            "_dir": str(project.path / "logs" / "jobs"),
        }

        def apply(p, result):
            p.data["joints"] = result["joints"]
            p.data.setdefault("jiggle_bones", []).extend(
                {
                    "name": b["name"],
                    "parent": b["parent"],
                    "head": b["head"],
                    "tail": b["tail"],
                    "radius": b["radius"],
                    "params": b["params"],
                }
                for b in clean
            )
            p.data["stage"] = "jiggled"

        return self._submit("jiggle", request, apply)

    def unwrap(self, size: int) -> jobs.Job:
        project = self._require_project()
        if size not in (512, 1024, 2048, 4096):
            raise ApiError("texture size must be 512, 1024, 2048 or 4096")
        blend = project.work("rigged.blend")
        if not blend.is_file():
            blend = project.work("base.blend")
        if not blend.is_file():
            raise ApiError("import a model first")
        texture = project.work("texture.png")
        request = {
            "blend": str(blend),
            "out_blend": str(blend),
            "glb": str(project.work("model.glb")),
            "texture": str(texture),
            "svg": str(project.work("uv.svg")),
            "size": size,
            "_dir": str(project.path / "logs" / "jobs"),
        }

        def apply(p, result):
            p.data["uv"] = {"size": size, "materials": result.get("materials", 0)}
            p.data["texture"] = "work/texture.png"
            p.data["stage"] = "unwrapped"

        return self._submit("unwrap", request, apply)

    def save_texture(self, data_url: str) -> dict:
        project = self._require_project()
        if not project.data.get("uv"):
            raise ApiError("run Auto UV before painting")
        match = re.match(r"^data:image/png;base64,(.+)$", data_url or "", re.S)
        if not match:
            raise ApiError("texture must be a base64 PNG data URL")
        try:
            raw = base64.b64decode(match.group(1), validate=True)
        except ValueError as exc:
            raise ApiError("texture is not valid base64") from exc
        if len(raw) > 80 * 1024 * 1024:
            raise ApiError("texture is larger than 80 MB")
        target = project.work("texture.png")
        tmp = target.with_suffix(".png.tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, target)
        with self.lock:
            project.data["texture"] = "work/texture.png"
            project.save()
        return {"path": str(target), "bytes": len(raw)}

    def export(self, out_dir: str, name: str, formats: list[str] | None = None) -> jobs.Job:
        project = self._require_project()
        if not out_dir:
            raise ApiError("choose an export folder")
        target = Path(out_dir).expanduser()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ApiError(f"cannot create export folder: {exc}") from exc
        safe = re.sub(r"[^A-Za-z0-9._ -]+", "", name or project.data.get("name", "character")).strip()
        safe = (safe or "character")[:64]
        blend = project.work("rigged.blend")
        if not blend.is_file():
            blend = project.work("base.blend")
        if not blend.is_file():
            raise ApiError("nothing to export yet")
        manifest = self.manifest(project)
        texture = project.work("texture.png")
        request = {
            "blend": str(blend),
            "texture": str(texture) if texture.is_file() else "",
            "glb": str(target / f"{safe}.glb"),
            "fbx": str(target / f"{safe}.fbx"),
            "manifest": manifest,
            "_dir": str(project.path / "logs" / "jobs"),
        }

        def apply(p, result):
            files = [result["glb"], result["fbx"]]
            if texture.is_file():
                tex_dir = target / "textures"
                tex_dir.mkdir(exist_ok=True)
                dest = tex_dir / "basecolor.png"
                shutil.copy2(texture, dest)
                files.append(str(dest))
            manifest_path = target / "riggle.json"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            files.append(str(manifest_path))
            readme = target / "README.txt"
            readme.write_text(export_readme(project, safe), encoding="utf-8")
            files.append(str(readme))
            p.data["export"] = {
                "dir": str(target),
                "name": safe,
                "files": files,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            p.data["stage"] = "exported"
            self.cfg["last_export_dir"] = str(target)
            store.save_config(self.cfg)

        return self._submit("export", request, apply)

    def manifest(self, project: store.Project) -> dict:
        by_name = {j["name"]: j for j in project.data.get("joints", [])}
        jiggle = {}
        for b in project.data.get("jiggle_bones", []):
            joint = by_name.get(b["name"], {})
            jiggle[b["name"]] = {
                "parent": b.get("parent") or joint.get("parent"),
                "head": b.get("head") or joint.get("head"),
                "tail": b.get("tail") or joint.get("tail"),
                "radius": b.get("radius") or 0.1,
                **(b.get("params") or {}),
            }
        return {
            "app": APP_NAME,
            "version": VERSION,
            "manifest_version": 1,
            "name": project.data.get("name"),
            "created": project.data.get("created"),
            "source": project.data.get("source_name", ""),
            "units": "meters",
            "coordinate_system": "glTF Y-up",
            "joints": [
                {k: j.get(k) for k in ("name", "parent", "head", "tail", "deform")}
                for j in project.data.get("joints", [])
            ],
            "jiggle": jiggle,
            "jiggle_order": [b["name"] for b in project.data.get("jiggle_bones", [])],
            "uv": project.data.get("uv", {}),
            "texture": "textures/basecolor.png" if project.work("texture.png").is_file() else "",
        }


def export_readme(project: store.Project, name: str) -> str:
    jiggle = [b["name"] for b in project.data.get("jiggle_bones", [])]
    return f"""{name} — exported by Riggle {VERSION}

Files
  {name}.glb          rigged, skinned model with textures embedded
  {name}.fbx          same rig for Blender, Unity or Unreal
  textures/           painted base colour atlas (if you used Auto UV)
  riggle.json         skeleton, jiggle parameters and UV settings

Jiggle bones
  {", ".join(jiggle) if jiggle else "(none)"}

riggle.json lists each jiggle bone's parent, rest position and tuning
(stiffness, damping, gravity, mass). The same manifest is stored on the
armature node in the GLB as the "riggle_manifest" extra.

Wiring the jiggle
  three.js   read the bone list and drive each bone with a damped spring.
  Unity      add a Dynamic Bone / PhysBone component to the named transforms.
  Unreal     add AnimDynamics or a physics constraint to the named bones.
  Blender    open the .fbx; bones keep their names, so constraints map 1:1.

The mesh is skinned to the skeleton. Do not rename bones unless you update
riggle.json as well.
"""


class Handler(BaseHTTPRequestHandler):
    server_version = f"Riggle/{VERSION}"
    app: App

    def log_message(self, fmt, *args):  # keep the console for job output
        if self.app.cfg.get("debug"):
            super().log_message(fmt, *args)

    # -- helpers -----------------------------------------------------------
    def _token_ok(self, query: dict) -> bool:
        supplied = self.headers.get("X-Riggle-Token") or (query.get("token", [""])[0])
        return bool(supplied) and secrets.compare_digest(supplied, self.app.token)

    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 16 * 1024 * 1024:
            raise ApiError("request body is too large", 413)
        return self.rfile.read(length)

    def _json_body(self) -> dict:
        raw = self._body()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ApiError("body is not valid JSON") from exc
        if not isinstance(data, dict):
            raise ApiError("body must be a JSON object")
        return data

    def _serve_file(self, path: Path, *, download: bool = False) -> None:
        if not path.is_file():
            self._error("file not found", 404)
            return
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(path.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        with path.open("rb") as fh:
            shutil.copyfileobj(fh, self.wfile, 1024 * 256)

    # -- GET ---------------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self._index()
                return
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if parsed.path.startswith("/static/"):
                rel = parsed.path[len("/static/"):]
                target = (store.WEB / rel).resolve()
                if store.WEB not in target.parents:
                    self._error("forbidden", 403)
                    return
                self._serve_file(target)
                return
            if not parsed.path.startswith("/api/"):
                self._error("not found", 404)
                return
            if not self._token_ok(query):
                self._error("bad token", 403)
                return
            if parsed.path == "/api/state":
                self._json(self.app.state())
                return
            if parsed.path == "/api/job":
                job = self.app.jobs.get(query.get("id", [""])[0])
                if not job:
                    self._error("job not found", 404)
                    return
                self._json(job.public())
                return
            if parsed.path == "/api/file":
                project = self.app._require_project()
                rel = query.get("p", [""])[0]
                self._serve_file(project.file(rel))
                return
            self._error("not found", 404)
        except ApiError as exc:
            self._error(str(exc), exc.status)
        except store.ProjectError as exc:
            self._error(str(exc), 400)
        except Exception as exc:
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def _index(self):
        html = (store.WEB / "index.html").read_text(encoding="utf-8")
        html = html.replace("__RIGGLE_TOKEN__", self.app.token).replace("__RIGGLE_VERSION__", VERSION)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # -- POST --------------------------------------------------------------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if not self._token_ok(query):
                self._error("bad token", 403)
                return
            route = parsed.path
            if route == "/api/config":
                self._config()
            elif route == "/api/upload":
                self._upload()
            elif route == "/api/project":
                self._create()
            elif route == "/api/project/open":
                self._open()
            elif route == "/api/import":
                project = self.app._require_project()
                self._json({"job": self.app.start_import(project).public()})
            elif route == "/api/bind":
                self._json({"job": self.app.bind(self._json_body().get("joints") or []).public()})
            elif route == "/api/preset":
                self._json({"joints": self.app.preset()})
            elif route == "/api/jiggle/params":
                body = self._json_body()
                self._json({"project": self.app.set_jiggle_params(body.get("name", ""), body.get("params") or {})})
            elif route == "/api/jiggle":
                self._json({"job": self.app.add_jiggle(self._json_body().get("bones") or []).public()})
            elif route == "/api/unwrap":
                self._json({"job": self.app.unwrap(int(self._json_body().get("size", 1024))).public()})
            elif route == "/api/texture":
                self._json(self.app.save_texture(self._json_body().get("data", "")))
            elif route == "/api/export":
                body = self._json_body()
                job = self.app.export(
                    body.get("dir", ""), body.get("name", ""), body.get("formats")
                )
                self._json({"job": job.public()})
            elif route == "/api/dialog/folder":
                self._json({"path": choose_folder(self._json_body().get("initial", ""))})
            elif route == "/api/reveal":
                reveal(self._json_body().get("path", ""))
                self._json({"ok": True})
            else:
                self._error("not found", 404)
        except ApiError as exc:
            self._error(str(exc), exc.status)
        except store.ProjectError as exc:
            self._error(str(exc), 400)
        except Exception as exc:
            traceback.print_exc()
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def _config(self):
        body = self._json_body()
        blender = str(body.get("blender", "")).strip()
        projects = str(body.get("projects_dir", "")).strip()
        if blender:
            if not Path(blender).is_file():
                raise ApiError("that Blender executable does not exist")
            self.app.cfg["blender"] = blender
        if projects:
            Path(projects).expanduser().mkdir(parents=True, exist_ok=True)
            self.app.cfg["projects_dir"] = projects
        store.save_config(self.app.cfg)
        self._json(self.app.state())

    def _upload(self):
        name = urllib.parse.unquote(self.headers.get("X-Riggle-Filename", "model.bin"))
        name = Path(name.replace("\\", "/")).name
        safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip() or "model.bin"
        ext = Path(safe).suffix.lower()
        if ext in {".ccproject", ".ccavatar", ".iavatar", ".ccacc", ".ccprops"}:
            raise ApiError(
                "Character Creator project files are proprietary — export FBX or GLB "
                "from CC5 and drop that instead"
            )
        if ext not in ALLOWED_SOURCE:
            raise ApiError(f"unsupported model format: {ext or '(none)'}")
        incoming = store.config_dir() / "incoming" / secrets.token_hex(8)
        incoming.mkdir(parents=True, exist_ok=True)
        target = incoming / safe
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ApiError("empty upload")
        if length > 4 * 1024 ** 3:
            raise ApiError("upload is larger than 4 GB", 413)
        remaining = length
        with target.open("wb") as fh:
            while remaining > 0:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        if remaining:
            target.unlink(missing_ok=True)
            raise ApiError("upload ended early")
        self._json({"path": str(target), "name": safe, "bytes": length})

    def _create(self):
        body = self._json_body()
        source = Path(body.get("source", ""))
        name = body.get("name") or source.stem
        project = self.app.create_project(name, source)
        job = self.app.start_import(project)
        self._json({"project": project.public(), "job": job.public()})

    def _open(self):
        folder = self._json_body().get("dir", "")
        project = store.Project.open(folder)
        with self.app.lock:
            self.app.project = project
            self.app.cfg["last_project"] = str(project.path)
            store.save_config(self.app.cfg)
        self._json({"project": project.public()})


def choose_folder(initial: str = "") -> str:
    if os.name != "nt":
        return ""
    start = initial or str(Path.home())
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$d.SelectedPath = '{start.replace(chr(39), chr(39) * 2)}';"
        "$d.Description = 'Choose a folder for the exported model';"
        "if ($d.ShowDialog() -eq 'OK') { Write-Output $d.SelectedPath }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True, text=True, timeout=240,
        )
        return (out.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def reveal(path: str) -> None:
    target = Path(path).expanduser()
    if os.name == "nt":
        if target.is_file():
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            subprocess.Popen(["explorer", str(target)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True):
    store.log_startup()
    app = App()
    Handler.app = app
    httpd = None
    for candidate in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer((host, candidate), Handler)
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit("no free port between 8765 and 8784")
    url = f"http://{host}:{httpd.server_port}/"
    print(f"{APP_NAME} {VERSION} -> {url}")
    if app.cfg.get("blender"):
        print(f"Blender: {app.cfg['blender']}")
    else:
        print("Blender: not found (set it in Settings)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.server_close()
