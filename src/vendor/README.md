# Vendored dependencies (PyAV)

`compositor_worker.py` uses PyAV (decode + compose), bundled here so the plugin
needs no install step (Dispatcharr ships no pip and a static ffmpeg).

## Layout

Per-platform, picked at runtime by `compositor_worker.py` via `platform.machine()`:

```
vendor/
  README.md            (tracked)
  linux-x86_64/        av/  av.libs/  av-*.dist-info/   (amd64 wheel, unpacked)
  linux-aarch64/       av/  av.libs/  av-*.dist-info/   (arm64 wheel, unpacked)
```

Each `linux-*` dir is an unpacked PyAV manylinux wheel (self-contained, bundles
its own ffmpeg shared libraries). They are **not committed to git** (see
`.gitignore`); `package.sh` downloads them at build/package time for every
release, and `dev-deploy.sh vendor` syncs the local copy to the dev container.

## Version / re-vendor

Pinned in `package.sh` (`PYAV_VERSION`, `PYAV_PYTAG`). To refresh or bump:

```
./package.sh --revendor          # re-download all arches at the pinned version
```

Wheels target CPython 3.13 / manylinux2014. If Dispatcharr's Python version
changes, update `PYAV_PYTAG` in `package.sh` (and `_ARCH_DIR` mapping if a new
arch is added). The worker fails loudly if no matching `av` import is found.
See beads `multiview-0kg` (packaging) and `multiview-e9s.11` (version mgmt).
