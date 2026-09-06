# Contributing a mod

The catalog is **generated from the assets** — you don't write any metadata. You
just drop the mod file into a folder named for you and the mod, and CI does the
rest (name, spec sheet, thumbnail, provenance verdict).

## Layout

```
cars/<author>/<name>/<name>.car
tracks/<author>/<name>/<name>.trk         # or .tra
```

- **`<author>`** — your handle (a stable folder; groups all your mods).
- **`<name>`** — the mod's base filename, **without the extension**. It must match
  the archive's real filename inside (`<name>.car`), because in Viper Racing the
  filename *is* the car's identity — renaming a `.car` breaks it.
- The pair **`<author>/<name>`** is the mod's unique id, so two authors can both
  ship a `bowser.car` without colliding.

That's the whole submission — one file. Optionally include the original
distribution's `readme.txt`/`.jpg` in the folder for archival; they're ignored by
the build (the gallery derives everything), but they preserve the original credits.

## What the build derives

`scripts/build_manifest.py` (via `vrmod`) reads, per mod:

- **name + spec** (0‑60, top speed, engine, hp, torque, redline) — from the car's
  own `<prefix>1.tab`.
- **provenance** — `self-contained` (renders anywhere), `portable` (uses only stock
  shared/paint textures), or **`incomplete`** (references textures it doesn't ship —
  it'll render wrong once installed; fix before submitting).
- parts, cockpit, vertex counts; track length + mesh size.
- a **thumbnail**, rendered from the mesh.

## A note on attribution and hosting

Many Viper Racing cars are conversions of models from other games, credited to
their original authors. This gallery **hosts the mod file and shows a derived
preview**; it is not the record of original authorship. If you're submitting a
conversion, make sure you have the right to redistribute it, and keep the
original `readme.txt` (with its credits) in the folder.
