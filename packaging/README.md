# Packaging

The portable Windows build bundles the official Python embeddable runtime and the
app source. Blender is **not** bundled; the worker launches the user's Blender.

## Build

1. Download `python-3.11.9-embed-amd64.zip` from
   <https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip>.
2. Run:

```bash
python packaging/build_portable.py --python-zip /path/to/python-3.11.9-embed-amd64.zip
```

The output is:

```
dist/Riggle-<version>-win64.zip
dist/SHA256SUMS.txt
```

`SHA256SUMS.txt` also covers a source zip if you pass `--source-zip`.

## What the zip contains

- `START.bat` — prefers `python\python.exe`, falls back to `py -3` / `python`
- `run.py`, `riggle/`, `web/`, `docs/`
- `python/` — Python 3.11.9 embeddable, with `..` added to `python311._pth`
  so `import riggle` resolves from the app root
- `PORTABLE.txt`, `README.md`, `LICENSE`, `THIRD_PARTY.md`, `CHANGELOG.md`

## Verify a build

Extract the zip to a clean folder and run:

```bat
python\python.exe run.py --version
START.bat
```

The version must match `riggle/__init__.py`, and
`http://127.0.0.1:8765/` must load. A real import still needs Blender installed.
