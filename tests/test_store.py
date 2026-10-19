import json
import os
import tempfile
import unittest
from pathlib import Path

from riggle import store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="riggle-store-")
        self.old = os.environ.get("RIGGLE_HOME")
        os.environ["RIGGLE_HOME"] = self.tmp.name
        self.cfg = store.load_config()
        self.cfg["projects_dir"] = str(Path(self.tmp.name) / "projects")
        store.save_config(self.cfg)
        self.source = Path(self.tmp.name) / "hero.glb"
        self.source.write_bytes(b"glTF")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("RIGGLE_HOME", None)
        else:
            os.environ["RIGGLE_HOME"] = self.old
        self.tmp.cleanup()

    def test_create_open_and_roundtrip(self):
        p = store.Project.create("Hero", self.source)
        p.data["stage"] = "imported"
        p.save()
        again = store.Project.open(p.path)
        self.assertEqual(again.data["name"], "Hero")
        self.assertEqual(again.data["stage"], "imported")

    def test_paths_stay_inside_the_project(self):
        p = store.Project.create("Hero", self.source)
        self.assertTrue(str(p.file("work/model.glb")).startswith(str(p.path)))
        with self.assertRaises(store.ProjectError):
            p.file("../escape.txt")

    def test_atomic_save_leaves_valid_json(self):
        p = store.Project.create("Hero", self.source)
        p.save()
        raw = (p.path / "project.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(raw)["name"], "Hero")
        self.assertFalse((p.path / "project.json.tmp").exists())

    def test_recent_projects_lists_newest_first(self):
        a = store.Project.create("A", self.source)
        b = store.Project.create("B", self.source)
        # Project.save() stamps "now", so set the stored timestamps directly.
        for project, stamp in ((a, "2026-01-01T00:00:00"), (b, "2026-01-02T00:00:00")):
            meta = project.path / "project.json"
            data = json.loads(meta.read_text(encoding="utf-8"))
            data["updated"] = stamp
            meta.write_text(json.dumps(data), encoding="utf-8")
        names = [r["name"] for r in store.recent_projects()]
        self.assertEqual(names[:2], ["B", "A"])


if __name__ == "__main__":
    unittest.main()
