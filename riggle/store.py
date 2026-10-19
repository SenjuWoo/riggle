"""Config, project folders and small file helpers.

One owner folder per project: imports/, work/, logs/. Every write that matters is
atomic, so a killed Blender or a closed browser never leaves half a project.json.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sys
import time
import traceback
from pathlib import Path

from . import APP_NAME, VERSION

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
WORKER = Path(__file__).resolve().parent / "blender_worker.py"

DEFAULT_PROJECTS = Path.home() / "RiggleProjects"
BLENDER_CANDIDATES = [
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe",
    r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe",
    r"C:\Program Files\Steam\steamapps\common\Blender\blender.exe",
]


def local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME")
    return Path(base) if base else Path.home() / ".config"


def config_dir() -> Path:
    override = os.environ.get("RIGGLE_HOME")
    return Path(override) if override else local_appdata() / "Riggle"


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> dict:
    cfg = {
        "blender": "",
        "projects_dir": str(DEFAULT_PROJECTS),
        "last_export_dir": "",
        "last_project": "",
        "debug": bool(os.environ.get("RIGGLE_DEBUG")),
    }
    try:
        saved = json.loads(config_path().read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            cfg.update({k: v for k, v in saved.items() if k in cfg})
    except (OSError, ValueError):
        pass
    if not cfg["blender"]:
        cfg["blender"] = find_blender()
    return cfg


def save_config(cfg: dict) -> dict:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {
        "blender": str(cfg.get("blender", "")),
        "projects_dir": str(cfg.get("projects_dir", DEFAULT_PROJECTS)),
        "last_export_dir": str(cfg.get("last_export_dir", "")),
        "last_project": str(cfg.get("last_project", "")),
        "debug": bool(cfg.get("debug", False)),
    }
    _atomic_write(path, json.dumps(clean, indent=2))
    return clean


def find_blender() -> str:
    env = os.environ.get("RIGGLE_BLENDER")
    if env and Path(env).is_file():
        return str(Path(env))
    for candidate in BLENDER_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("blender")
    if found:
        return found
    # Steam libraries can live on any drive; scan the common library manifests.
    for root in _steam_roots():
        candidate = root / "steamapps" / "common" / "Blender" / "blender.exe"
        if candidate.is_file():
            return str(candidate)
    return ""


def _steam_roots() -> list[Path]:
    roots = [Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Program Files\Steam")]
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        roots.append(Path(f"{letter}:/SteamLibrary"))
        roots.append(Path(f"{letter}:/Steam"))
    return roots


def blender_version(blender: str) -> str:
    if not blender:
        return ""
    try:
        import subprocess
        out = subprocess.run([blender, "--version"], capture_output=True, text=True, timeout=25)
        first = (out.stdout or "").strip().splitlines()
        return first[0].strip() if first else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._ -]+", "", name or "").strip().replace(" ", "-")
    s = re.sub(r"-+", "-", s).strip(".-")
    return (s or "character")[:48]


class ProjectError(RuntimeError):
    pass


class Project:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {}

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def create(cls, name: str, source: Path) -> "Project":
        cfg = load_config()
        base = Path(cfg["projects_dir"]).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        pid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        folder = base / f"{slug(name)}-{pid[-6:]}"
        folder.mkdir(parents=True, exist_ok=False)
        for sub in ("imports", "work", "logs"):
            (folder / sub).mkdir()
        p = cls(folder)
        p.data = {
            "id": pid,
            "name": name or folder.name,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": "",
            "stage": "created",
            "bbox": None,
            "mesh": {},
            "has_rig": False,
            "joints": [],
            "jiggle_bones": [],
            "uv": {},
            "texture": "",
            "export": {},
            "app_version": VERSION,
        }
        p.save()
        return p

    @classmethod
    def open(cls, folder: str | Path) -> "Project":
        path = Path(folder).expanduser().resolve()
        meta = path / "project.json"
        if not meta.is_file():
            raise ProjectError(f"no project.json in {path}")
        p = cls(path)
        try:
            p.data = json.loads(meta.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ProjectError(f"project.json is not valid JSON: {exc}") from exc
        if not isinstance(p.data, dict):
            raise ProjectError("project.json is not an object")
        return p

    def save(self) -> None:
        self.data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _atomic_write(self.path / "project.json", json.dumps(self.data, indent=2))

    # -- paths -------------------------------------------------------------
    def file(self, rel: str) -> Path:
        """Resolve a project-relative path, refusing anything that escapes it."""
        target = (self.path / rel).resolve()
        if target != self.path and self.path not in target.parents:
            raise ProjectError("path escapes the project folder")
        return target

    def rel(self, path: str | Path) -> str:
        return Path(path).resolve().relative_to(self.path).as_posix()

    def work(self, name: str) -> Path:
        return self.file(f"work/{name}")

    def public(self) -> dict:
        d = dict(self.data)
        d["dir"] = str(self.path)
        d["files"] = {
            "model": "/api/file?p=work/model.glb",
            "uv": "/api/file?p=work/uv.svg",
            "texture": "/api/file?p=work/texture.png",
        }
        for key in ("model", "uv", "texture"):
            if not self.work(Path(d["files"][key].split("p=")[1]).name).is_file():
                d["files"].pop(key)
        return d

    # -- logging -----------------------------------------------------------
    def log(self, message: str, *, error: bool = False) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {'ERROR ' if error else ''}{message}"
        try:
            logfile = self.path / "logs" / "riggle.log"
            with logfile.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            if logfile.stat().st_size > 2_000_000:
                logfile.write_text(line + "\n", encoding="utf-8")
        except OSError:
            pass
        if load_config().get("debug") or error:
            print(line, file=sys.stderr if error else sys.stdout, flush=True)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def recent_projects(limit: int = 12) -> list[dict]:
    cfg = load_config()
    base = Path(cfg["projects_dir"]).expanduser()
    if not base.is_dir():
        return []
    items = []
    for meta in base.glob("*/project.json"):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items.append({
            "name": data.get("name") or meta.parent.name,
            "dir": str(meta.parent),
            "stage": data.get("stage", ""),
            "updated": data.get("updated", ""),
            "thumb": "/api/thumb?p=" + str(meta.parent),
        })
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items[:limit]


def log_startup() -> None:
    try:
        cfg = load_config()
        path = config_dir() / "riggle.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} start {APP_NAME} {VERSION} "
                f"python={sys.version.split()[0]} blender={cfg.get('blender') or 'missing'} "
                f"projects={cfg.get('projects_dir')}\n"
            )
    except Exception:
        traceback.print_exc()
