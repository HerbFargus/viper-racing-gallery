# Viper Racing Community Gallery

A browser gallery for **previewing and downloading community car and track mods**
for *Viper Racing* (1998). Every mod renders in **live 3D in your browser** — no
install, nothing uploaded — and the whole catalog is **generated from the assets
themselves**, not hand-maintained.

It's the community-facing companion to the [`viper-racing-modding`](https://github.com/HerbFargus/viper-racing-modding)
toolkit, which it reuses (`vrmod`) to read the mods.

## How it works

- Contributors add a mod under a **by-author folder** (see below).
- CI runs **`scripts/build_manifest.py`**, which walks the folders and — using
  `vrmod` — derives every field, bakes a thumbnail, and emits a static
  `manifest.json`. Nothing is entered by hand.
- The site (`web/` + the generated `manifest.json`, `thumbnails/`, `assets/`) is
  published to GitHub Pages. **Browsing needs no runtime** — it's a static
  manifest and pre-baked PNGs. The live 3D viewer (`vrmod` via Pyodide/WebAssembly)
  loads **lazily, only when you open a mod**.

Everything in a catalog entry comes from the asset + its folder path:

| Field | Source |
|---|---|
| `id` = `collection/name` | the folder path |
| `collection` | the folder the community filed it under (`valscars`, `frankscars`…) |
| `name`, spec (0‑60, top speed, hp, torque…) | the car's own `<prefix>1.tab` garage sheet |
| `provenance` (self‑contained / portable / **incomplete**) | `vrmod` texture analysis |
| `parts`, `cockpit`, vertices, `miles` (tracks) | the archive |
| `thumbnail` | baked by `vrmod` (`carshot`) |
| **`author`**, `author_raw`, `dated`, `converted_from` | the original pack's readme, via the corpus index |
| **`source_pack`**, `source_sha256` | the archive the asset was extracted from |

### Where credits come from

Credits and source game used to be the one thing this *couldn't* derive. They now
come from the **corpus index** — `index_carpacks.py` in the
[toolkit repo](https://github.com/HerbFargus/viper-racing-modding) reads all ~2,000
original `.rar`/`.zip` packs in place and records who wrote each readme, when, and
which game the car was converted from. This builder joins to it on the asset
filename; 93% of assets are shipped by exactly one pack, so most carry a real
name.

Two distinctions the manifest keeps deliberately separate:

- **`author` is a person** (`Val`, `Frank P. Wolf`) and may be **absent**.
  **`collection` is a folder** (`valscars`) and always exists. The old manifest
  used `author` for the folder, which read as though "frankscars" were somebody's
  name.
- An asset shipped by **several** packs gets **no** author rather than a guessed
  one. Those are nearly always a stock file riding along inside a retexture —
  `viper.car` appears in 31 packs — and guessing would credit MGI's own car to
  whoever repacked it last.

Build without the corpus and everything still works; entries simply carry no
author, and the build says so rather than leaving a column silently blank.

## Adding a mod

See [CONTRIBUTING.md](CONTRIBUTING.md). In short:

```
cars/<author>/<name>/<name>.car
tracks/<author>/<name>/<name>.trk        (or .tra)
```

## Building locally

```bash
python -m venv .venv && .venv/Scripts/pip install capstone
# vrmod: install it, or put the viper-mod-manager checkout beside this repo
.venv/Scripts/python scripts/build_manifest.py
python -m http.server -d site 8000        # then open http://127.0.0.1:8000/
```

`site/` is a build artifact (gitignored); CI regenerates it on every deploy, so
the catalog can never drift from the assets.

## Status

Scaffold. Folder convention, manifest builder, frontend (`RemoteGalleryProvider`),
and Pages CI are in place; catalog population is deliberate and ongoing.
