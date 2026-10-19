"""End-to-end proof for the Blender worker: import -> rig -> jiggle -> UV -> export.

Run with the app's Python (not Blender):
    python tests/blender_smoke.py
It shells out to the installed Blender, so it is slow but real.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from riggle import rig, store  # noqa: E402

BLENDER = os.environ.get("RIGGLE_BLENDER") or store.find_blender()
WORKER = ROOT / "riggle" / "blender_worker.py"
FIXTURE = ROOT / "tests" / "make_fixture.py"


def run_blender(script: str, args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    cmd = [BLENDER, "--background", "--factory-startup", "--python", str(script), "--", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def worker(job: dict, tmp: Path) -> dict:
    job_path = tmp / f"{job['job']}.json"
    result_path = tmp / f"{job['job']}.result.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    proc = subprocess.run(
        [BLENDER, "--background", "--factory-startup", "--python", str(WORKER), "--",
         str(job_path), str(result_path)],
        capture_output=True, text=True, timeout=900,
    )
    if not result_path.is_file():
        raise AssertionError(f"worker produced no result\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not result.get("ok"):
        raise AssertionError(f"{job['job']} failed: {result.get('error')}\n{result.get('traceback', '')}")
    result["_stdout"] = proc.stdout
    return result


def glb_json(path: Path) -> dict:
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67 and version == 2, "not a GLB2 file"
    chunk_len, chunk_type = struct.unpack_from("<II", data, 12)
    assert chunk_type == 0x4E4F534A, "first chunk is not JSON"
    return json.loads(data[20:20 + chunk_len].decode("utf-8").rstrip("\x00 "))


def main() -> int:
    if not BLENDER:
        print("SKIP: Blender not found")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="riggle-smoke-"))
    print("workdir", tmp)
    fixture = tmp / "fixture.glb"
    made = run_blender(FIXTURE, [str(fixture)])
    assert fixture.is_file(), f"fixture failed\n{made.stdout}\n{made.stderr}"
    print("fixture", fixture.stat().st_size, "bytes")

    blend = tmp / "work" / "base.blend"
    glb = tmp / "work" / "model.glb"
    blend.parent.mkdir()
    imported = worker({"job": "import", "source": str(fixture), "blend": str(blend), "glb": str(glb)}, tmp)
    info = imported["result"]
    assert info["mesh"]["verts"] > 100, info
    assert not info["has_rig"], info
    bbox = info["bbox"]
    print("import ok", info["mesh"], "bbox", [round(v, 2) for v in bbox])
    assert glb.is_file() and glb.stat().st_size > 1000

    joints = rig.validate(rig.humanoid(bbox))
    rigged_blend = tmp / "work" / "rigged.blend"
    bound = worker({
        "job": "bind",
        "blend": str(blend),
        "out_blend": str(rigged_blend),
        "glb": str(glb),
        "joints": joints,
    }, tmp)
    weights = bound["result"]["weights"]
    print("bind ok", weights, "warnings", bound["warnings"])
    assert weights["unweighted"] == 0, weights

    gltf = glb_json(glb)
    assert gltf.get("skins"), "GLB has no skin"
    joint_count = len(gltf["skins"][0]["joints"])
    assert joint_count == len(joints), (joint_count, len(joints))
    assert any("JOINTS_0" in p.get("attributes", {}) for m in gltf["meshes"] for p in m["primitives"])
    print("glb skin ok", joint_count, "joints")

    height = bbox[4] - bbox[1]
    jiggle_bone = {
        "name": "jiggle_test.L",
        "parent": "chest",
        "head": [0.08, bbox[1] + height * 0.66, 0.10],
        "tail": [0.08, bbox[1] + height * 0.58, 0.16],
        "radius": height * 0.18,
    }
    jiggled = worker({
        "job": "jiggle",
        "blend": str(rigged_blend),
        "out_blend": str(rigged_blend),
        "glb": str(glb),
        "bones": [jiggle_bone],
    }, tmp)
    print("jiggle ok", jiggled["result"]["added"], "warnings", jiggled["warnings"])
    assert jiggled["result"]["added"][0]["weighted_verts"] > 0
    gltf = glb_json(glb)
    names = [gltf["nodes"][i].get("name") for i in gltf["skins"][0]["joints"]]
    assert "jiggle_test.L" in names, names
    print("jiggle bone exported", len(names), "joints")

    unwrapped = worker({
        "job": "unwrap",
        "blend": str(rigged_blend),
        "out_blend": str(rigged_blend),
        "glb": str(glb),
        "texture": str(tmp / "work" / "texture.png"),
        "svg": str(tmp / "work" / "uv.svg"),
        "size": 512,
    }, tmp)
    assert (tmp / "work" / "uv.svg").is_file()
    assert (tmp / "work" / "texture.png").is_file()
    print("unwrap ok", unwrapped["result"]["materials"], "materials")

    out = tmp / "export"
    out.mkdir()
    exported = worker({
        "job": "export",
        "blend": str(rigged_blend),
        "texture": str(tmp / "work" / "texture.png"),
        "glb": str(out / "fixture.glb"),
        "fbx": str(out / "fixture.fbx"),
        "manifest": {"app": "Riggle", "jiggle": [jiggle_bone]},
    }, tmp)
    print("export ok", exported["result"])
    assert (out / "fixture.fbx").stat().st_size > 1000
    final = glb_json(out / "fixture.glb")
    assert final.get("skins")
    assert final.get("images"), "texture was not embedded in the GLB"
    arm_node = next((n for n in final["nodes"] if n.get("extras", {}).get("riggle_manifest")), None)
    assert arm_node, "rig manifest missing from GLB extras"
    print("PASS: full pipeline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
