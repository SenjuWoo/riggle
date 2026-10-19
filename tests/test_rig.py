import math
import unittest

from riggle import rig


BBOX = [-0.72, 0.0, -0.14, 0.72, 1.77, 0.14]


class PresetTests(unittest.TestCase):
    def test_humanoid_is_complete_and_valid(self):
        joints = rig.humanoid(BBOX)
        self.assertEqual(len(joints), 22)
        self.assertEqual(joints[0]["name"], "root")
        self.assertIn("hand.L", [j["name"] for j in joints])
        self.assertIn("toe.R", [j["name"] for j in joints])
        clean = rig.validate(joints)
        for j in clean:
            self.assertGreater(math.dist(j["head"], j["tail"]), 1e-5, j["name"])

    def test_feet_sit_on_the_floor_and_head_near_the_top(self):
        joints = {j["name"]: j for j in rig.humanoid(BBOX)}
        self.assertAlmostEqual(joints["toe.L"]["head"][1], BBOX[1] + 0.02 * 1.77, places=4)
        self.assertAlmostEqual(joints["head"]["tail"][1], BBOX[4] - 0.02 * 1.77, places=4)

    def test_mirror_swaps_sides(self):
        joints = {j["name"]: j for j in rig.humanoid(BBOX)}
        twin = rig.mirror_joint(joints["hand.L"])
        self.assertEqual(twin["name"], "hand.R")
        self.assertAlmostEqual(twin["head"][0], -joints["hand.L"]["head"][0])
        self.assertEqual(twin["head"][1:], joints["hand.L"]["head"][1:])

    def test_symmetrize_keeps_one_of_each_pair(self):
        joints = rig.humanoid(BBOX)
        moved = [dict(j) for j in joints if not j["name"].endswith(".R")]
        for j in moved:
            if j["name"] == "hand.L":
                j["head"] = [0.9, 1.0, 0.0]
        out = rig.symmetrize(moved, "L")
        names = [j["name"] for j in out]
        self.assertIn("hand.R", names)
        hand_r = next(j for j in out if j["name"] == "hand.R")
        self.assertAlmostEqual(hand_r["head"][0], -0.9)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.joints = rig.humanoid(BBOX)

    def test_rejects_duplicate_names(self):
        bad = self.joints + [dict(self.joints[0])]
        with self.assertRaisesRegex(ValueError, "unique"):
            rig.validate(bad)

    def test_rejects_missing_parent(self):
        bad = [dict(j) for j in self.joints]
        bad[3]["parent"] = "nope"
        with self.assertRaisesRegex(ValueError, "missing parent"):
            rig.validate(bad)

    def test_rejects_cycles(self):
        bad = [dict(j) for j in self.joints]
        bad[0]["parent"] = bad[1]["name"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            rig.validate(bad)

    def test_rejects_zero_length(self):
        bad = [dict(j) for j in self.joints]
        bad[-1]["tail"] = list(bad[-1]["head"])
        with self.assertRaisesRegex(ValueError, "zero length"):
            rig.validate(bad)

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            rig.validate([])


class GeometryTests(unittest.TestCase):
    def test_nearest_bone_finds_the_chest(self):
        joints = rig.humanoid(BBOX)
        chest = next(j for j in joints if j["name"] == "chest")
        point = [(chest["head"][0] + chest["tail"][0]) / 2, 1.25, 0.1]
        self.assertEqual(rig.nearest_bone(point, joints), "chest")

    def test_point_segment_distance_clamps_to_ends(self):
        self.assertAlmostEqual(rig.point_segment_distance([2, 0, 0], [0, 0, 0], [1, 0, 0]), 1.0)
        self.assertAlmostEqual(rig.point_segment_distance([0.5, 1, 0], [0, 0, 0], [1, 0, 0]), 1.0)


if __name__ == "__main__":
    unittest.main()
