"""One background worker, one Blender process at a time.

Blender is heavy and not re-entrant. Jobs queue, stream their progress lines into
the job record the UI polls, and are killed if they overrun their deadline.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import traceback
import uuid
from collections import deque
from pathlib import Path

from . import VERSION
from . import store


class Job:
    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.status = "queued"
        self.pct = 0
        self.msg = "queued"
        self.log = deque(maxlen=400)
        self.result = None
        self.error = ""
        self.created = time.time()
        self.started = 0.0
        self.ended = 0.0

    def add(self, line: str) -> None:
        self.log.append(line.rstrip())

    def public(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "pct": self.pct,
            "msg": self.msg,
            "log": list(self.log)[-120:],
            "result": self.result,
            "error": self.error,
            "elapsed": round((self.ended or time.time()) - (self.started or self.created), 1),
        }


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.queue: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._loop, name="riggle-jobs", daemon=True)
        self.thread.start()

    def submit(self, kind: str, fn) -> Job:
        job = Job(kind)
        self.jobs[job.id] = job
        self.queue.put((job, fn))
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def _loop(self) -> None:
        while True:
            job, fn = self.queue.get()
            job.status = "running"
            job.started = time.time()
            job.msg = "starting"
            try:
                job.result = fn(job)
                job.status = "done"
                job.pct = 100
                job.msg = "done"
            except Exception as exc:
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                job.add(job.error)
                job.add(traceback.format_exc())
                job.msg = "failed"
            finally:
                job.ended = time.time()
                if len(self.jobs) > 60:
                    for key in sorted(self.jobs, key=lambda k: self.jobs[k].created)[:-40]:
                        self.jobs.pop(key, None)


class BlenderRunner:
    """Runs riggle/blender_worker.py in a disposable Blender process."""

    def __init__(self, timeout: int = 900):
        self.timeout = timeout

    def run(self, job: Job, request: dict) -> dict:
        cfg = store.load_config()
        blender = cfg.get("blender") or store.find_blender()
        if not blender or not Path(blender).is_file():
            raise RuntimeError(
                "Blender was not found. Install Blender 4.5+ or set its path in Settings."
            )
        work = Path(request.pop("_dir"))
        work.mkdir(parents=True, exist_ok=True)
        job_path = work / f"job-{job.id}.json"
        result_path = work / f"result-{job.id}.json"
        request["job"] = job.kind
        job_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        if result_path.exists():
            result_path.unlink()

        cmd = [
            blender, "--background", "--factory-startup",
            "--python", str(store.WORKER), "--",
            str(job_path), str(result_path),
        ]
        job.add(f"$ {' '.join(cmd)}")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
        )
        lines: queue.Queue = queue.Queue()

        def pump():
            try:
                for line in proc.stdout:
                    lines.put(line)
            finally:
                lines.put(None)

        threading.Thread(target=pump, daemon=True).start()
        deadline = time.time() + self.timeout
        while True:
            try:
                line = lines.get(timeout=0.5)
            except queue.Empty:
                if proc.poll() is not None and lines.empty():
                    break
                if time.time() > deadline:
                    proc.kill()
                    raise TimeoutError(f"Blender exceeded {self.timeout}s")
                continue
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            if line.startswith("RIGGLE_PROGRESS "):
                data = json.loads(line[len("RIGGLE_PROGRESS "):])
                job.pct = max(job.pct, int(data.get("pct", job.pct)))
                job.msg = data.get("msg", job.msg)
                job.add(f"[{job.pct:3d}%] {job.msg}")
            elif line.startswith("RIGGLE_WARN "):
                data = json.loads(line[len("RIGGLE_WARN "):])
                job.add("warning: " + data.get("msg", ""))
            else:
                job.add(line)
        code = proc.wait(timeout=10)
        if not result_path.is_file():
            raise RuntimeError(f"Blender exited with {code} and wrote no result. See the job log.")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        for message in result.get("warnings", []):
            job.add("warning: " + message)
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "Blender job failed")
        return result["result"]


class FakeRunner:
    """Deterministic stand-in used by tests and UI work without Blender."""

    def run(self, job: Job, request: dict) -> dict:
        job.add("fake runner")
        for pct, msg in ((10, "reading"), (45, "working"), (80, "writing")):
            time.sleep(0.02)
            job.pct, job.msg = pct, msg
        kind = job.kind
        if kind == "import":
            fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "fixture.glb"
            target = Path(request["glb"])
            target.parent.mkdir(parents=True, exist_ok=True)
            if fixture.is_file():
                target.write_bytes(fixture.read_bytes())
            else:
                target.write_bytes(b"glTF")
            Path(request["blend"]).write_bytes(b"BLENDER-fake")
            return {
                "bbox": [-0.72, 0.0, -0.14, 0.72, 1.77, 0.14],
                "mesh": {"objects": 1, "verts": 1533, "tris": 2400, "materials": 1},
                "has_rig": False,
                "joints": [],
            }
        if kind == "bind":
            joints = []
            for j in request["joints"]:
                joints.append({**j, "jiggle": False})
            Path(request["out_blend"]).write_bytes(b"BLENDER-fake")
            return {"joints": joints, "weights": {"verts": 1533, "unweighted": 0},
                    "glb": request["glb"], "blend": request["out_blend"]}
        if kind == "jiggle":
            joints = [dict(j) for j in request.get("_joints", [])]
            for spec in request["bones"]:
                joints.append({k: spec[k] for k in ("name", "parent", "head", "tail")} | {"deform": True, "jiggle": True})
            Path(request["out_blend"]).write_bytes(b"BLENDER-fake")
            return {"added": [{"name": b["name"], "weighted_verts": 10} for b in request["bones"]],
                    "joints": joints, "glb": request["glb"], "blend": request["out_blend"]}
        if kind == "unwrap":
            Path(request["texture"]).write_bytes(_PNG_1PX)
            Path(request["svg"]).write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024">'
                '<polygon points="10,10 500,10 500,500 10,500" fill="none" stroke="black"/></svg>',
                encoding="utf-8",
            )
            Path(request["out_blend"]).write_bytes(b"BLENDER-fake")
            return {"size": request.get("size", 1024), "materials": 1,
                    "texture": request["texture"], "svg": request["svg"], "glb": request["glb"]}
        if kind == "export":
            Path(request["glb"]).write_bytes(b"glTF-fake")
            Path(request["fbx"]).write_bytes(b"FBX-fake")
            return {"glb": request["glb"], "fbx": request["fbx"],
                    "glb_bytes": 9, "fbx_bytes": 8}
        return {"ok": True, "kind": kind}


_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000000020001e221bc330000000049454e44ae426082"
)


def make_runner():
    if os.environ.get("RIGGLE_FAKE"):
        return FakeRunner()
    return BlenderRunner()
