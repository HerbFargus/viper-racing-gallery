---
pretty_name: Viper Racing community mods
license: other
license_name: mixed-see-description
size_categories:
  - 1K<n<10K
tags:
  - viper-racing
  - game-mods
  - sim-racing
  - preservation
  - "1998"
---

# Viper Racing community mods

Twenty-odd years of community-made cars and tracks for **Viper Racing** (MGI /
Sierra, 1998), preserved as their authors distributed them.

Each file here is an original distribution archive — the same zip someone
uploaded to a fan site, with its readme, its screenshots and whatever else the
author put in it. Nothing has been repacked, renamed, or tidied. That is the
point: the archive *is* the record, and once it is edited it stops being one.

Browse them with thumbnails, spec sheets and attribution at
**[the Viper Racing Community Gallery](https://herbfargus.github.io/viper-racing-gallery/)**.

## Layout

```
cars/frankscars/      155 packs     78 MB
cars/otherscars/        1 pack       1 MB
cars/valscars/      1,511 packs  2,264 MB
cars/vrgt/             82 packs    312 MB
tracks/herbstracks/     3 packs      7 MB
tracks/otherstracks/   54 packs    210 MB
tracks/retextured/     42 packs     94 MB
tracks/valstracks/    160 packs    458 MB
tracks/vrgt/           15 packs     41 MB
```

**2,023 packs, 3.38 GB.** The folders are the collections the community itself
filed these under, mostly by author. `vrgt` appears under both `cars/` and
`tracks/` and holds different mods in each, which is why the tree prefix is
part of the path.

A pack usually contains one `.car` or one `.tra`/`.trk`, but not always — some
hold several, and a few ship a car alongside the track it was made for.

## Getting files

A single pack, by URL:

```
https://huggingface.co/datasets/herbfargus/viper-racing-mods/resolve/main/cars/frankscars/250gto.zip
```

These URLs send CORS headers, so a browser page can `fetch()` them directly —
which is how the gallery renders a car in 3D without anyone installing
anything.

A collection, or the lot:

```python
from huggingface_hub import snapshot_download

snapshot_download("herbfargus/viper-racing-mods", repo_type="dataset",
                  allow_patterns="cars/frankscars/*")
```

There is also a **[volume-split copy on
GitHub](https://github.com/HerbFargus/viper-racing-gallery/releases)** for
grabbing the whole corpus in ten files instead of two thousand requests.

## What is in a pack, and where the metadata lives

Nothing here is annotated — the files are untouched. Everything derived from
them lives in the gallery's
[`manifest.json`](https://herbfargus.github.io/viper-racing-gallery/manifest.json):
name, spec sheet, part counts, vertex counts, a rendered thumbnail, the author
where a readme names one, the game a model was converted from, and the pack's
`sha256`. It joins back to this dataset on the pack path.

The reading is done by [`vrmod`](https://github.com/HerbFargus/viper-racing-modding),
a pure-Python toolkit for Viper Racing's formats. If you want to open a `.car`
yourself, start there.

## Attribution, and what this is not

These mods were made by many people over about two decades, and a great number
of the cars are **conversions of models from other games**, credited in the
readme inside each pack to their original authors.

This dataset preserves and redistributes those archives. It is **not** a claim
of authorship over any of them, and it is not a license grant: each pack
carries whatever terms its author gave it, which is why the license field above
says "other" rather than naming one. If you are the author of something here
and would rather it were not, please open a discussion on this dataset — it
will be removed.

Where a readme names a person, the gallery credits them. About a third of the
assets are files that several packs ship in common — usually a stock game file
riding along inside a retexture — and those are deliberately left unattributed
rather than guessed at.

## Integrity

Every pack's `sha256` was recorded before upload and verified after. A pack
downloaded from here hashes identically to the archive that was indexed. The
hashes are in the gallery manifest as `source_sha256`.
