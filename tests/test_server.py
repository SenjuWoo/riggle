"""HTTP-level test of the whole workflow using the deterministic fake Blender runner."""

import base64
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from riggle import store
from riggle.jobs import FakeRunner
from riggle.server import App, Handler

PNG = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000000020001e221bc330000000049454e44ae426082"
)).decode()


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="riggle-api-")
        os.environ["RIGGLE_HOME"] = cls.tmp.name
        cfg = store.load_config()
        cfg["projects_dir"] = str(Path(cls.tmp.name) / "projects")
        store.save_config(cfg)
        cls.app = App(runner=FakeRunner())
        Handler.app = cls.app
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    # -- helpers -----------------------------------------------------------
    def request(self, path, method="GET", data=None, headers=None, token=True):
        hdrs = dict(headers or {})
        if token:
            hdrs["X-Riggle-Token"] = self.app.token
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers=hdrs
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def json(self, path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        status, raw = self.request(path, method, data, headers)
        return status, json.loads(raw or b"{}")

    def wait(self, job_id):
        for _ in range(400):
            status, job = self.json(f"/api/job?id={job_id}")
            self.assertEqual(status, 200)
            if job["status"] in ("done", "error"):
                self.assertEqual(job["status"], "done", job.get("error") or job.get("log"))
                return job
            time.sleep(0.02)
        self.fail("job did not finish")

    # -- tests -------------------------------------------------------------
    def test_01_index_and_token(self):
        status, raw = self.request("/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.app.token.encode(), raw)
        status, _ = self.request("/api/state", token=False)
        self.assertEqual(status, 403)
        status, state = self.json("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["app"], "Riggle")

    def test_02_full_workflow(self):
        # upload
        status, raw = self.request(
            "/api/upload", "POST", b"glTF-test-bytes",
            {"X-Riggle-Filename": "hero.glb", "Content-Type": "application/octet-stream"},
        )
        self.assertEqual(status, 200, raw)
        upload = json.loads(raw)
        self.assertEqual(upload["name"], "hero.glb")

        # create project + import job
        status, created = self.json("/api/project", "POST", {"source": upload["path"], "name": "Hero"})
        self.assertEqual(status, 200, created)
        self.wait(created["job"]["id"])
        _, state = self.json("/api/state")
        project = state["project"]
        self.assertEqual(project["stage"], "imported")
        self.assertEqual(len(project["joints"]), 22)
        self.assertTrue((Path(project["dir"]) / "work" / "model.glb").is_file())

        # bind
        status, bound = self.json("/api/bind", "POST", {"joints": project["joints"]})
        self.assertEqual(status, 200, bound)
        self.wait(bound["job"]["id"])
        _, state = self.json("/api/state")
        project = state["project"]
        self.assertTrue(project["has_rig"])
        self.assertEqual(project["stage"], "rigged")

        # add a jiggle bone
        chest = next(j for j in project["joints"] if j["name"] == "chest")
        bone = {
            "name": "jiggle_test.L",
            "parent": chest["name"],
            "head": [0.1, 1.2, 0.1],
            "tail": [0.1, 1.1, 0.16],
            "radius": 0.2,
        }
        status, jig = self.json("/api/jiggle", "POST", {"bones": [bone]})
        self.assertEqual(status, 200, jig)
        self.wait(jig["job"]["id"])
        _, state = self.json("/api/state")
        project = state["project"]
        self.assertEqual(len(project["jiggle_bones"]), 1)
        self.assertEqual(project["jiggle_bones"][0]["name"], "jiggle_test.L")
        self.assertEqual(project["stage"], "jiggled")

        # tune it
        status, tuned = self.json("/api/jiggle/params", "POST",
                                  {"name": "jiggle_test.L", "params": {"stiffness": 0.9, "bogus": 4}})
        self.assertEqual(status, 200, tuned)
        self.assertEqual(tuned["project"]["jiggle_bones"][0]["params"]["stiffness"], 0.9)
        self.assertNotIn("bogus", tuned["project"]["jiggle_bones"][0]["params"])

        # unwrap
        status, unwrapped = self.json("/api/unwrap", "POST", {"size": 512})
        self.assertEqual(status, 200, unwrapped)
        self.wait(unwrapped["job"]["id"])
        _, state = self.json("/api/state")
        project = state["project"]
        self.assertEqual(project["uv"]["size"], 512)
        self.assertTrue((Path(project["dir"]) / "work" / "uv.svg").is_file())

        # save a painted texture
        status, saved = self.json("/api/texture", "POST", {"data": f"data:image/png;base64,{PNG}"})
        self.assertEqual(status, 200, saved)
        self.assertGreater(saved["bytes"], 50)

        # export
        out = Path(self.tmp.name) / "export"
        status, exported = self.json("/api/export", "POST", {"dir": str(out), "name": "Hero"})
        self.assertEqual(status, 200, exported)
        self.wait(exported["job"]["id"])
        for name in ("Hero.glb", "Hero.fbx", "riggle.json", "README.txt", "textures/basecolor.png"):
            self.assertTrue((out / name).is_file(), name)
        manifest = json.loads((out / "riggle.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["app"], "Riggle")
        self.assertIn("jiggle_test.L", manifest["jiggle"])
        self.assertEqual(len(manifest["joints"]), 23)

        # the preview file is downloadable with the token
        status, body = self.request("/api/file?p=work/model.glb")
        self.assertEqual(status, 200)
        self.assertTrue(body)

    def test_03_rejects_bad_input(self):
        status, data = self.json("/api/bind", "POST", {"joints": [{"name": "x"}]})
        self.assertEqual(status, 400)
        self.assertIn("error", data)
        status, _ = self.request("/api/upload", "POST", b"x", {"X-Riggle-Filename": "bad.exe"})
        self.assertEqual(status, 400)

    def test_04_path_traversal_is_blocked(self):
        _, state = self.json("/api/state")
        project = state["project"]
        status, _ = self.request(f"/api/file?p={urllib.parse.quote('../project.json')}")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
