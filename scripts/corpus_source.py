"""Source gallery assets from the archive corpus instead of checked-in folders.

WHY THIS EXISTS. The by-author folders assume the assets live in the repo. They
cannot: extracting the community corpus is **6.2 GB** of .car files, against
1.8 GB of archives, and GitHub Pages is not the place for either. Measured, not
guessed.

So the catalogue is built straight from the packs. Each one is extracted to a
temp folder, analysed, a thumbnail is baked, and the extraction is thrown away --
peak disk is one car, and the published output is the manifest plus ~21 MB of
thumbnails. The bytes themselves are hosted elsewhere and linked.

WHAT THIS MEANS FOR THE LIVE VIEWER. Browsing needs nothing but the manifest and
the baked PNGs, so it works for the whole corpus. The 3D viewer reads the asset
in JS, which is subject to CORS -- and archive.org serves file downloads WITHOUT
an Access-Control-Allow-Origin header (its metadata API does send one; downloads
do not). So remote assets can be downloaded but not previewed in 3D, and the
live viewer stays a feature of whatever subset is served same-origin.

Entries carry `source_pack` and `source_sha256`, so a download URL can be
derived from wherever the packs end up without rebuilding the catalogue.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

from vrmod import carpack

# The stock assets the game itself ships. A retexture or add-on that bundles one
# is not the author's contribution, and crediting it to them would be wrong --
# viper.car alone rides along in 31 packs.
STOCK_ASSETS = {
    "viper.car", "exotic.car", "plane.car", "sedan.car", "sports.car",
    "bemidji.trk", "dundas.trk", "hastings.trk", "heaven.trk", "kenyon.trk",
    "limbo.trk", "nfield.trk", "uptown.trk",
}


def pack_roots(manifest: dict) -> dict[str, Path]:
    """Map each tree's leading path segment back to where it lives on disk."""
    return {Path(t).name: Path(t).parent for t in manifest.get("trees", ())}


def own_assets(item: dict) -> list[str]:
    """The assets a pack actually contributes, stock ones excluded."""
    return [a for a in (item.get("cars") or []) + (item.get("tracks") or [])
            if Path(a).name.lower() not in STOCK_ASSETS]


def _extract(pack: Path, names: list[str], dest: Path) -> list[tuple[Path, str]]:
    """Extract the named members, PRESERVING their folders. Returns (path, member).

    Flattening loses data: Donut.zip ships cars/exotic/luigi.car AND
    cars/original/luigi.car, two different cars, and extracting both to
    luigi.car silently keeps one. 7-Zip's `x` keeps the tree where `e` flattens
    it, and zipfile is given the full relative path for the same reason.
    """
    out: list[tuple[Path, str]] = []
    if pack.suffix.lower() == ".zip":
        with zipfile.ZipFile(pack) as z:
            for n in names:
                target = dest / n.replace("\\", "/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(n))
                out.append((target, n))
    else:
        exe = carpack.sevenzip()
        if exe is None:
            raise RuntimeError("no 7-Zip, so .rar packs cannot be read")
        subprocess.run([str(exe), "x", "-y", f"-o{dest}", str(pack)] + names,
                       capture_output=True, timeout=180)
        for n in names:
            target = dest / n.replace("\\", "/")
            if target.is_file():
                out.append((target, n))
    return sorted(out, key=lambda t: t[1])


def iter_assets(manifest: dict, limit: int | None = None,
                skip=None, missing: list | None = None
                ) -> Iterator[tuple[str, str, Path, dict]]:
    """Yield (collection, name, extracted_path, pack_item) for every asset.

    `name` carries any folder the member sat in inside the pack, so the two
    luigi.car in Donut.zip stay distinguishable as exotic_luigi and
    original_luigi rather than one silently replacing the other.

    The extracted file is valid only until the next iteration -- copy it if you
    need it to outlive that. This is what keeps peak disk at one car instead of
    6.2 GB.

    `skip(item)` lets a caller decline a whole pack before it is extracted.
    That is where the real time goes: reusing a thumbnail still costs an
    extract-and-analyse per asset, so a no-op rebuild took 3m40 even when
    nothing rendered. A pack whose sha256 has not moved cannot have produced
    different assets, so the caller can answer from its previous manifest and
    never touch the archive.
    """
    roots = pack_roots(manifest)
    seen = 0
    for item in manifest.get("items", ()):
        own = own_assets(item)
        if not own:
            continue
        if skip and skip(item):
            # A skipped pack still counts toward `limit`, so a limited run
            # covers the same slice of the corpus whether or not the cache is
            # warm. Counting only extracted assets made --limit --incremental
            # run PAST the cold run's stopping point and then add the cached
            # entries on top: 59 assets cold, 118 warm.
            seen += len(own)
            if limit and seen >= limit:
                return
            continue
        root = roots.get(item["path"].split("/")[0])
        pack = (root / item["path"]) if root else Path(item["path"])
        if not pack.is_file():
            # Do NOT pass over this quietly. A manifest naming a pack that has
            # moved is exactly what a reorganisation produces, and the symptom
            # is a catalogue that is silently short: folding the VRgt packs into
            # the community trees dropped 98 assets from a build that then
            # reported success.
            if missing is not None:
                missing.append(item["path"])
            continue
        tmp = Path(tempfile.mkdtemp(prefix="gallery_src_"))
        try:
            for f, member in _extract(pack, own, tmp):
                # "valscars", not "viper-racing-community-cars/valscars" -- the
                # tree prefix is plumbing, the collection is what people know.
                coll = (item.get("collection") or "").split("/")[-1] or "unsorted"
                folder = "_".join(Path(member.replace("\\", "/")).parent.parts)
                name = f"{folder}_{f.stem}" if folder and folder != "." else f.stem
                yield coll, name, f, item
                seen += 1
                if limit and seen >= limit:
                    return
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
