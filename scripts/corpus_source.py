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

import hashlib
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

from vrmod import carpack

# The file NAMES the game itself ships. A pack carrying one of these is not
# necessarily carrying the stock file: in Viper Racing the filename is the
# car's (or the slot's) identity, so a modified Viper has to be called
# Viper.car and a retextured Sunset Mesa has to be nfield.trk. Excluding by
# name dropped 47 distinct modified files -- Val's 23 Viper builds, a plane,
# and all 23 stock-track retextures -- against only 9 genuine stock copies.
# So a stock NAME only makes a file a candidate; STOCK_SHA256 decides.
STOCK_ASSETS = {
    "viper.car", "exotic.car", "plane.car", "sedan.car", "sports.car",
    "bemidji.trk", "dundas.trk", "hastings.trk", "heaven.trk", "kenyon.trk",
    "limbo.trk", "nfield.trk", "uptown.trk",
}

# sha256 of every stock car and track, from a retail v1.1 Data folder; the v1.0
# disc's copies are byte-identical (reference-files/stock-assets/INDEX.json).
# A file with one of these hashes rides along in a pack without being anyone's
# work, and crediting it to the pack's author would be wrong.
STOCK_SHA256 = {
    "6b33d975798136cc56cfb2bf3a8aaf51da4e118212d0da3bda8c7874f245518a",  # exotic.car
    "985f586668c8710b8a9181be36bb4847bd10ba9cdc2216e78a347c3af43d2865",  # plane.car
    "c2ff55e6721c48db8e9d04bbc68ad67ece53f2636fb83e77699097fce6d5fbe3",  # sedan.car
    "59769da4342f2c4678fd6b65279bef573edda084d84a36c05193810a852d5638",  # sports.car
    "840ae64a76dab82d81cdcdd91547664ccac25fd265a21220cac4f43a4f8d175a",  # viper.car
    "ae3cb0c9318c487f6cfe217edf65f2bbbe4b7b44de7f6bfea78e473552944464",  # bemidji.trk
    "075737c9357324ab4984fcc5c157c2e0126c3ef05edb25583fb7b1673109f3c5",  # dundas.trk
    "4aa9e5fae7098cb6f42bbbfd75411244a59f0968ea62f2e1d3f5fafdedf49eaa",  # hastings.trk
    "db19ca1046deb84d48b502060d022a075c08eccb3f2f6335537e2ef36843ce6a",  # heaven.trk
    "48c0d4ce9f7a4cbf7dd52c6e2248371bf80b291075d2f06f137cde66c237825d",  # kenyon.trk
    "d2f517d8362e13cf2d60ba0602d373e23926553ad9cf9d933ccdf10ef71a1d20",  # limbo.trk
    "cd37020edea0c26bf5e71b5ec9e7eee861a812c7e8a9c13365bb32d8c4a3b0ce",  # nfield.trk
    "4334fd3e4c5513bf4dd9ce3fbfdc0b2bf09450385064b7a2aa650f27b6133c7b",  # uptown.trk
}

# What a stock slot is called in game, for naming the mods that reuse it.
SLOT_NAMES = {"bemidji": "Bemidji", "dundas": "Dundas", "hastings": "Ridge Valley", "heaven": "Castlegreen",
              "kenyon": "Rock Island", "limbo": "Dayton", "nfield": "Sunset Mesa", "uptown": "Silverdale"}


def is_stock_name(member: str) -> bool:
    return Path(member.replace("\\", "/")).name.lower() in STOCK_ASSETS


def is_stock_copy(member: str, path: Path) -> bool:
    """A stock NAME and a stock HASH: the game's own file, riding along."""
    return is_stock_name(member) and hashlib.sha256(path.read_bytes()).hexdigest() in STOCK_SHA256


def pack_roots(manifest: dict) -> dict[str, Path]:
    """Map each tree's leading path segment back to where it lives on disk."""
    return {Path(t).name: Path(t).parent for t in manifest.get("trees", ())}


def own_assets(item: dict) -> list[str]:
    """Every asset a pack MIGHT contribute. Stock-named ones are included: only
    their hash, known after extraction, says whether they are the game's own
    (see iter_assets and is_stock_copy)."""
    return [a for a in (item.get("cars") or []) + (item.get("tracks") or [])]


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
                if is_stock_copy(member, f):
                    continue                                    # the game's own file, not the author's
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
