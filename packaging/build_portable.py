#!/usr/bin/env python3
"""Build the portable Windows zip for Riggle.

Riggle is stdlib-only on the Python side, so the only runtime we ship is the
official Python embeddable package. Blender is still a separate requirement:
the worker launches it, it is not bundled.

Usage:
    python packaging/build_portable.py --python-zip python-3.11.9-embed-amd64.zip

Download the embeddable zip from python.org first; this script never downloads
anything, so a release build is explicit and offline-safe.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_FILES = [
    "run.py",
    "START.bat",
    "README.md",
    "LICENSE",
    "THIRD_PARTY.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
]
APP_DIRS = ["riggle", "web", "docs"]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "*.tmp")


def read_version() -> str:
    text = (ROOT / "riggle" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("could not read VERSION from riggle/__init__.py")
    return match.group(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(path: Path, lines: list[str]) -> None:
    """Write LF checksums so `sha256sum -c` works on every platform."""
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def stage_app(stage: Path) -> None:
    for name in APP_FILES:
        source = ROOT / name
        if not source.is_file():
            raise SystemExit(f"missing app file: {source}")
        shutil.copy2(source, stage / name)
    for name in APP_DIRS:
        source = ROOT / name
        if not source.is_dir():
            raise SystemExit(f"missing app folder: {source}")
        shutil.copytree(source, stage / name, ignore=IGNORE)


def stage_python(stage: Path, python_zip: Path) -> None:
    target = stage / "python"
    target.mkdir()
    with zipfile.ZipFile(python_zip) as archive:
        archive.extractall(target)

    # The embeddable runtime restricts sys.path to its ._pth file. Add the
    # app root so `import riggle` works when python.exe sits in python/.
    pth = next(target.glob("*._pth"), None)
    if pth is None:
        raise SystemExit("Python embeddable zip has no ._pth file")
    lines = [line.strip() for line in pth.read_text(encoding="utf-8").splitlines()]
    if ".." not in lines:
        pth.write_text("\n".join(lines + [".."]) + "\n", encoding="utf-8")

    if not (target / "python.exe").is_file():
        raise SystemExit("Python embeddable zip has no python.exe")


def write_readme(stage: Path, version: str) -> None:
    (stage / "PORTABLE.txt").write_text(
        f"""Riggle {version} - portable Windows build
========================================

Double-click START.bat. It uses the bundled Python in python\\ and opens
http://127.0.0.1:8765/ in your browser.

Blender 4.5+ is still required for import, rigging, UV unwrap and export.
Riggle finds common install paths automatically; otherwise set blender.exe in
Settings. If Blender is missing, the app still opens but geometry jobs fail.

Projects go to %USERPROFILE%\\RiggleProjects by default. Your source model is
copied, never modified. Exports go to the folder you choose.

Python 3.11.9 embeddable is bundled under python\\ (PSF license, see
python\\LICENSE.txt). Riggle itself is MIT (see LICENSE). Three.js is MIT (see
THIRD_PARTY.md).

Heavy WIP: the UI, project format and export manifest may change.
""",
        encoding="utf-8",
    )


def build_zip(stage: Path, target: Path, version: str) -> None:
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.write(path, Path(f"Riggle-{version}") / path.relative_to(stage))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the portable Riggle Windows zip")
    parser.add_argument("--python-zip", required=True, type=Path, help="python-3.11.9-embed-amd64.zip")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="output folder (default: dist/)")
    parser.add_argument("--source-zip", type=Path, help="optional source archive to hash too")
    args = parser.parse_args()

    version = read_version()
    python_zip = args.python_zip.expanduser().resolve()
    if not python_zip.is_file():
        raise SystemExit(f"Python zip not found: {python_zip}")

    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    stage = out / f"Riggle-{version}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()

    print(f"[1/5] staging Riggle {version}")
    stage_app(stage)
    print(f"[2/5] extracting {python_zip.name}")
    stage_python(stage, python_zip)
    write_readme(stage, version)

    target = out / f"Riggle-{version}-win64.zip"
    print(f"[3/5] writing {target.name}")
    build_zip(stage, target, version)

    print("[4/5] hashing")
    lines = [f"{sha256(target)}  {target.name}"]
    if args.source_zip:
        source = args.source_zip.expanduser().resolve()
        if not source.is_file():
            raise SystemExit(f"source zip not found: {source}")
        lines.append(f"{sha256(source)}  {source.name}")
    write_checksums(out / "SHA256SUMS.txt", lines)

    print(f"[5/5] done: {target} ({target.stat().st_size / 1024 / 1024:.1f} MiB)")
    print((out / "SHA256SUMS.txt").read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
