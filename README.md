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
| `id` = `author/name` | the folder path |
| `name`, spec (0‑60, top speed, hp, torque…) | the car's own `<prefix>1.tab` garage sheet |
| `provenance` (self‑contained / portable / **incomplete**) | `vrmod` texture analysis |
| `parts`, `cockpit`, vertices, `miles` (tracks) | the archive |
| `thumbnail` | baked by `vrmod` (`carshot`) |

The only thing *not* derived is original-author **credits / source game** — those
live in the historic mod archives, which stay the record of original packaging.

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
