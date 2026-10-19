"""Keep the version in one place: code, package metadata, changelog and WIP banner."""

import unittest
from pathlib import Path

from riggle import VERSION

ROOT = Path(__file__).resolve().parent.parent


class VersionTests(unittest.TestCase):
    def test_pyproject_matches(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{VERSION}"', text)

    def test_changelog_has_entry(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## {VERSION} ", text)

    def test_readme_wip_banner_matches(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"Heavy WIP — {VERSION}", text)


if __name__ == "__main__":
    unittest.main()
