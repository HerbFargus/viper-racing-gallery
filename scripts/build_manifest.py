"""Build the community gallery: walk the by-author asset folders, derive every
field with vrmod, bake a thumbnail per asset, and assemble a deployable site/.

Nothing is hand-entered. Everything in the manifest comes from the asset itself
plus its folder path (author/name) -- see the project's data-standard notes:
  - name + spec        <- the car's own <prefix>1.tab garage sheet (+ .cf)
  - provenance         <- car.texture_provenance (self-contained/portable/incomplete)
  - parts, cockpit ... <- the archive
  - thumbnail          <- carshot (car) / trackmap+mesh (track), pre-baked here so
                          BROWSING needs no Pyodide; the live viewer only loads it
                          on click.

Layout consumed:
    cars/<author>/<name>/<name>.car
    tracks/<author>/<name>/<name>.trk|.tra
Layout produced (site/, gitignored, what Pages serves):
    site/index.html, app.js, providers.js        <- copied from web/
    site/vrmod.zip                                <- the pinned vrmod, for the live viewer
    site/manifest.json                            <- this
    site/thumbnails/<author>__<name>.png          <- baked
    site/assets/<author>__<name>.car|.trk         <- the hosted files (same-origin)

vrmod: imported if installed; otherwise a sibling ../viper-mod-manager checkout is
used (local dev). CI pins vrmod via pip -- see the workflow.

    python scripts/build_manifest.py
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- locate vrmod (installed, else a sibling Repo A checkout) ----------------
try:
    import vrmod  # noqa: F401
except ImportError:
    sibling = ROOT.parent / "viper-mod-manager"
    if (sibling / "vrmod").is_dir():
        sys.path.insert(0, str(sibling))
    import vrmod  # re-raise if truly unavailable

from vrmod import (archive, carpack, envelope, carshot, car,  # noqa: E402
                   track as track_mod, viewer)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus_source  # noqa: E402

CARS_DIR = ROOT / "cars"
TRACKS_DIR = ROOT / "tracks"
WEB_DIR = ROOT / "web"
SITE = ROOT / "site"

# The corpus index (Repo A, scripts/index_carpacks.py) describes every ARCHIVE
# the community ever shipped: hash, author, date, which game it was converted
# from. This builder describes every extracted ASSET: what it is, what it costs
# to render, what it looks like. Neither subsumes the other, so the asset
# inherits its provenance from the corpus rather than this deriving a second,
# weaker version of it.
# A stock Data folder, for resolving shared textures and the paint slot while
# baking. Nothing is read from it but .res archives.
DATA_CANDIDATES = (
    ROOT.parent / "game-files" / "installs" / "v1.0-RC",
    ROOT.parent / "game-files" / "viper-racing-usa" / "Data",
)

CORPUS_CANDIDATES = (
    ROOT.parent / "viper-racing-community-cars" / "MANIFEST.json",
    ROOT / "MANIFEST.json",
)


# --- the <prefix>1.tab garage spec sheet: readable field/value text ----------
_SPEC_KEYS = {
    "name": "name", "0-60": "zero_to_60", "0-100": "zero_to_100",
    "q time": "quarter_time", "q speed": "quarter_speed", "top speed": "top_speed",
    "engine": "engine", "e size": "engine_cc", "power max": "hp",
    "power rpm": "hp_rpm", "torque max": "torque", "torque rpm": "torque_rpm",
    "redline": "redline",
}


def parse_spec_tab(entries) -> dict:
    """Pull the Name + performance sheet out of a car's <prefix>1.tab, which stores
    them as printable key/value text (see the data-standard notes)."""
    prefix = car.body_prefix(entries)
    tab = next((e for e in entries if prefix and e.name.lower() == f"{prefix.lower()}1.tab"), None)
    if tab is None:
        return {}
    runs = [s.decode("ascii", "replace").strip()
            for s in re.findall(rb"[\x20-\x7e]{2,}", tab.payload)]
    spec = {}
    for i in range(0, len(runs) - 1, 2):
        key = _SPEC_KEYS.get(runs[i].strip().lower())
        if key:
            spec[key] = runs[i + 1].strip()
    return spec


def car_entry(collection: str, name: str, path: Path) -> dict:
    entries = archive.read(path)
    spec = parse_spec_tab(entries)
    prov = car.texture_provenance(path)
    mods = [e for e in entries if e.name.lower().endswith(".mod")]
    peak = 0
    for e in mods:
        try:
            m = __import__("vrmod.mod", fromlist=["parse"]).parse(
                envelope.build(e.tag, e.version, e.payload))
            peak = max(peak, len(m.vertices))
        except Exception:
            pass
    return {
        "id": f"{collection}/{name}", "kind": "car", "collection": collection,
        "name": spec.get("name") or name, "file": path.name,
        "spec": {k: v for k, v in spec.items() if k != "name"},
        "parts": sum(1 for _ in mods),
        "cockpit": any(e.name.lower() == "cockpit.tab" for e in entries),
        "peak_vertices": peak,
        "provenance": prov["verdict"],
        "missing_textures": prov["missing"],
    }


def track_entry(collection: str, name: str, path: Path) -> dict:
    try:
        miles = track_mod.length_miles(path)
    except Exception:
        miles = None
    mesh = viewer._track_render_mesh(path)
    return {
        "id": f"{collection}/{name}", "kind": "track", "collection": collection,
        "name": name, "file": path.name,
        "miles": round(miles, 2) if miles else None,
        "vertices": len(mesh.vertices), "faces": len(mesh.faces),
    }


def bake_thumbnail(kind: str, path: Path, data_dir: Path | None,
                   style: str = "textured") -> bytes:
    """A thumbnail of the asset as it actually looks.

    Two things matter here and both were previously left at their defaults:

    STYLE. Both to_png and track_to_png default to "wire", so the gallery was
    baking wireframes. A wireframe uses no textures at all, which is why
    pointing this at a Data folder changed nothing until the style changed too.
    Tracks stayed wireframe even after that fix, because the style was only
    being passed on the car branch.

    DATA FOLDER. A lone .car has no shared .res archives beside it, so shared
    materials -- and, more visibly, the runtime paint slot that many community
    cars keep their colour in -- resolve to nothing and the car renders as the
    grey shell it literally is. shared_dir points the resolver at a real
    install without copying race.res next to every asset. Tracks need none of
    it: a track's textures are all inside its own archive.
    """
    if kind == "car":
        return carshot.to_png(path, style=style, shared_dir=data_dir)
    return carshot.track_to_png(path, style=style)


def build_vrmod_zip(dest: Path) -> None:
    """The pinned vrmod, zipped for the live client-side viewer (same as Repo A's
    scripts/build_gallery.py -- vrmod runs unchanged in the browser via Pyodide)."""
    src = Path(vrmod.__file__).resolve().parent
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if "__pycache__" in p.parts or p.suffix == ".pyc" or not p.is_file():
                continue
            z.write(p, f"vrmod/{p.relative_to(src).as_posix()}")


# Bump a kind's number when a renderer change alters the pixels IT produces.
# The incremental build reuses a thumbnail whose ASSET is unchanged, which is
# only sound while the renderer is unchanged too -- switching the tracks from
# wireframe to textured changed no asset at all, so without this every track
# would have kept its wireframe through a rebuild that reported success.
#
# Per kind, because the two renderers move independently: three changes to
# track framing should not re-render 1,770 cars that nobody touched. Measured
# by ageing the track entries by one version and rebuilding: 253 rendered,
# 1,770 reused, 7m49 against a full bake of everything.
#   car   1  textured, resolved against a stock Data folder
#   track 1  wireframe, framed on the whole mesh
#         2  textured, framed on the racing line
#         3  ...and on the collision solids where they disagree
RENDER_VERSION = {"car": 1, "track": 3}


def render_recipe(kind: str, style: str, data_dir: Path | None) -> str:
    """How a thumbnail was drawn, recorded alongside what it was drawn from.

    `fingerprint` answers "is this still the same asset?"; this answers "would
    we draw it the same way?". Both have to hold before a cached image can be
    reused, and only the first of them used to be checked.
    """
    where = "data" if data_dir else "nodata"
    return f"{kind}/{style}/{where}/{RENDER_VERSION.get(kind, 0)}"


def fingerprint(entry: dict, path: Path) -> str:
    """What this thumbnail was baked from, so a rebuild can tell if it moved.

    Always the ASSET's own hash, never the pack's: a pack can hold several
    assets, and an asset shipped by several packs has no single pack hash at
    all. Recording it in the entry means the next build needs nothing but the
    previous manifest to decide what to re-render -- no side-car state file
    that can drift out of step with what was actually published.

    It also makes real duplicates visible: the same car distributed in three
    packs has one fingerprint under three ids.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reuse_thumbnail(cache: dict, cache_dir: Path | None, entry: dict,
                    slug: str, fp: str, recipe: str) -> bool:
    """Copy the previous thumbnail across when neither the asset nor the way it
    gets drawn has changed."""
    if cache_dir is None:
        return False
    old = cache.get(entry["id"])
    if not old or old.get("fingerprint") != fp:
        return False
    if old.get("render") != recipe:
        return False
    src = cache_dir / "thumbnails" / f"{slug}.png"
    if not src.is_file():
        return False
    shutil.copy2(src, SITE / "thumbnails" / f"{slug}.png")
    return True


def load_corpus(explicit: Path | None) -> tuple[dict, Path | None]:
    """The provenance index, from an explicit path or a sibling checkout.

    Missing is not fatal: the gallery still builds, every entry simply has no
    author or date. It says so loudly rather than leaving the operator to
    notice that a whole column quietly went blank.
    """
    for cand in ([explicit] if explicit else list(CORPUS_CANDIDATES)):
        if cand and cand.is_file():
            return carpack.provenance_index(carpack.load_manifest(cand)), cand
    return {}, None


def merge_provenance(entry: dict, index: dict) -> dict:
    """Attach who made this and where it came from, when the corpus knows.

    `author` stays absent rather than falling back to the folder name: the
    folder is a collection ("frankscars"), not a person, and conflating those
    two is exactly what made the old `author` field misleading.
    """
    prov = carpack.provenance_for(index, entry["file"]) if index else None
    if not prov:
        return entry
    entry["author"] = prov.get("author")
    entry["author_raw"] = prov.get("author_raw")
    entry["dated"] = prov.get("dated")
    entry["converted_from"] = prov.get("converted_from")
    entry["source_pack"] = prov.get("path")
    entry["source_sha256"] = prov.get("sha256")
    # The garage sheet is the better name when it has one; the pack readme's
    # title is the next best, and beats a bare filename.
    if entry.get("name") == Path(entry["file"]).stem and prov.get("title"):
        entry["name"] = prov["title"]
    return entry


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", type=Path, default=None,
                    help="a stock Data folder, so shared textures and the paint "
                         "slot resolve when baking thumbnails (default: a "
                         "pristine install if one is to hand)")
    ap.add_argument("--style", default="textured",
                    choices=("wire", "shaded", "textured"),
                    help="thumbnail style (default: textured; 'wire' ignores "
                         "textures entirely, so --data-dir then does nothing)")
    ap.add_argument("--from-corpus", action="store_true",
                    help="build straight from the archive corpus instead of the "
                         "checked-in by-author folders. Extracted assets are 6.2 GB "
                         "and cannot live in the repo, so this bakes the catalogue "
                         "(manifest + thumbnails, ~25 MB) and leaves the bytes where "
                         "they are; entries carry source_pack/source_sha256 so a "
                         "download URL can be derived later")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="where to build (default: site/). Anything that is not "
                         "the real site/ -- a staging build, the test suite -- "
                         "should pass this: the build WIPES its output directory, "
                         "so writing to site/ destroys a catalogue that costs 25 "
                         "minutes to regenerate")
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after N assets, for a quick look")
    ap.add_argument("--incremental", action="store_true",
                    help="reuse thumbnails from the previous build for assets whose "
                         "content fingerprint is unchanged. Rendering dominates the "
                         "build, so this is what makes adding a few mods cheap")
    ap.add_argument("--corpus", type=Path, default=None,
                    help="the corpus MANIFEST.json from Repo A's "
                         "index_carpacks.py (default: a sibling checkout)")
    args = ap.parse_args()

    global SITE
    if args.out_dir:
        SITE = args.out_dir

    data_dir = args.data_dir
    if data_dir is None:
        data_dir = next((d for d in DATA_CANDIDATES if (d / "race.res").is_file()), None)
    if data_dir:
        print(f"shared textures: {data_dir}")
    else:
        print("shared textures: NO Data FOLDER -- shared materials and the paint "
              "slot will not resolve, so cars that keep their colour there bake "
              "as grey shells. Pass --data-dir.")

    recipes = {k: render_recipe(k, args.style, data_dir) for k in ("car", "track")}
    print("render recipe: " + ", ".join(sorted(recipes.values())))

    index, corpus_path = load_corpus(args.corpus)
    if corpus_path:
        print(f"corpus: {corpus_path}  ({len(index):,} asset names)")
    else:
        print("corpus: NONE FOUND -- entries will carry no author, date or "
              "source pack. Point --corpus at a MANIFEST.json to fix that.")

    # Keep the previous run's thumbnails and manifest before wiping site/, so
    # an unchanged asset does not get re-rendered. Rendering is the expensive
    # step by a wide margin -- a full corpus bake is ~25 minutes against ~2
    # minutes to index -- and almost all of it is redone for nothing when a
    # handful of new mods arrive.
    cache_dir, cache = None, {}
    if args.incremental and (SITE / "manifest.json").is_file():
        try:
            prev = json.loads((SITE / "manifest.json").read_text(encoding="utf-8"))
            cache = {e["id"]: e for e in prev.get("cars", []) + prev.get("tracks", [])
                     if e.get("fingerprint")}
            cache_dir = Path(tempfile.mkdtemp(prefix="thumb_cache_"))
            if (SITE / "thumbnails").is_dir():
                shutil.copytree(SITE / "thumbnails", cache_dir / "thumbnails")
            print(f"  incremental: {len(cache):,} entries from the previous build")
        except Exception as ex:
            cache, cache_dir = {}, None
            print(f"  incremental: ignoring the previous build "
                  f"({type(ex).__name__}: {ex})")

    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "thumbnails").mkdir(parents=True)
    (SITE / "assets").mkdir()

    manifest = {"cars": [], "tracks": []}

    if args.from_corpus:
        if not corpus_path:
            raise SystemExit("error: --from-corpus needs a corpus manifest; run "
                             "index_carpacks.py or pass --corpus")
        corpus = carpack.load_manifest(corpus_path)

        # Entries from the previous build, grouped by the pack they came from.
        # A pack whose sha256 is unchanged cannot have produced different
        # assets, so its entries are reused wholesale and the archive is never
        # opened -- which is the difference between a 3m40 no-op and a 4s one.
        prev_by_pack: dict[str, list[dict]] = {}
        for e in cache.values():
            if e.get("source_pack") and e.get("pack_sha256"):
                prev_by_pack.setdefault(e["source_pack"], []).append(e)

        untouched: dict[str, list[dict]] = {}

        def pack_unchanged(item: dict) -> bool:
            got = prev_by_pack.get(item["path"])
            if not got or any(e["pack_sha256"] != item.get("sha256") for e in got):
                return False
            # An unchanged pack still needs re-rendering if the RENDERER
            # changed. This skip carries the previous thumbnails across
            # wholesale without ever opening the archive, so it is much the
            # wider of the two doors a stale image can come through.
            if any(e.get("render") != recipes.get(e.get("kind")) for e in got):
                return False
            # Belt and braces: only skip when the previous build recorded EVERY
            # asset this pack contributes. Reusing a partial set is how 25
            # assets went missing, and a count is cheap next to an extraction.
            if len(got) != len(corpus_source.own_assets(item)):
                return False
            untouched[item["path"]] = got
            return True

        n = skipped = kept = 0
        gate = pack_unchanged if (args.incremental and cache_dir) else None
        gone: list[str] = []
        for coll, name, path, item in corpus_source.iter_assets(
                corpus, args.limit, skip=gate, missing=gone):
            kind = "car" if path.suffix.lower() == ".car" else "track"
            entry_fn = car_entry if kind == "car" else track_entry
            # The id carries the PACK, not just the asset name. In Viper Racing
            # the filename IS the car's identity, so variants necessarily reuse
            # it: fordt.car ships in bfordt/gfordt/rfordt -- blue, green and red
            # Ford Ts, three different cars with one name. Keying on
            # collection/name collided 80 times, silently overwriting
            # thumbnails and emitting duplicate ids. A pack cannot contain two
            # files of the same name, so collection/pack/name is unique by
            # construction and does not shift when other packs are added.
            # The pack's FILENAME, extension included: valscars holds both
            # jet.rar and jet.zip, and stripping the extension collided them.
            pack_stem = Path(item["path"]).name
            ident = f"{coll}/{pack_stem}/{name}"
            slug = f"{coll}__{pack_stem}__{name}"
            try:
                entry = merge_provenance(entry_fn(coll, name, path), index)
                entry["id"] = ident
                entry["pack"] = pack_stem
                # The PACK's hash, so the next build can skip this pack whole
                # without extracting it. Distinct from `fingerprint`, which is
                # the asset's own hash and needs the extraction to compute.
                entry["pack_sha256"] = item.get("sha256")
                # Set unconditionally here, NOT left to merge_provenance: that
                # only fills it when the corpus join resolves, so a pack with a
                # mix of resolved and unresolved assets recorded only some of
                # them against itself. The pack-level skip then reused a partial
                # set and silently dropped 25 assets. In corpus mode we know
                # exactly which pack this came out of -- say so.
                entry["source_pack"] = item["path"]
                entry["fingerprint"] = fp = fingerprint(entry, path)
                entry["render"] = recipe = recipes[kind]
                if reuse_thumbnail(cache, cache_dir, entry, slug, fp, recipe):
                    kept += 1
                else:
                    (SITE / "thumbnails" / f"{slug}.png").write_bytes(
                        bake_thumbnail(kind, path, data_dir, args.style))
            except Exception as ex:
                skipped += 1
                print(f"  SKIP {coll}/{name}: {type(ex).__name__}: "
                      f"{str(ex)[:70]}")
                continue
            entry["thumbnail"] = f"thumbnails/{slug}.png"
            # No local copy: the asset is hosted wherever the packs are. The
            # entry already carries source_pack and source_sha256.
            manifest[f"{kind}s"].append(entry)
            n += 1
            if n % 100 == 0:
                print(f"  {n:>5} done ({kept} reused), {skipped} skipped")
        # Fold the untouched packs' entries back in, with their thumbnails.
        carried = 0
        for entries in untouched.values():
            for e in entries:
                src = cache_dir / "thumbnails" / Path(e["thumbnail"]).name
                if not src.is_file():
                    continue                      # thumbnail lost: let it rebuild next time
                shutil.copy2(src, SITE / "thumbnails" / src.name)
                manifest[f"{e['kind']}s"].append(e)
                carried += 1
        print(f"  {n + carried:,} assets -- {n - kept:,} rendered, "
              f"{kept + carried:,} reused ({carried:,} from packs never opened), "
              f"{skipped} skipped")
        if gone:
            print(f"\n  WARNING: {len(gone)} pack(s) named in the corpus manifest "
                  f"are not on disk, so their assets are MISSING from this "
                  f"build. Re-run index_carpacks.py if the trees have moved.")
            for g in gone[:5]:
                print(f"    {g}")
    else:
      for kind, base, glob, entry_fn in (
          ("car", CARS_DIR, "*.car", car_entry),
          ("track", TRACKS_DIR, "*.tr[ka]", track_entry),
      ):
          for asset in sorted(base.glob(f"*/*/{glob}")):
              author, name = asset.parent.parent.name, asset.parent.name
              slug = f"{author}__{name}"
              try:
                  entry = merge_provenance(entry_fn(author, name, asset), index)
                  entry["fingerprint"] = fp = fingerprint(entry, asset)
                  entry["render"] = recipe = recipes[kind]
                  if not reuse_thumbnail(cache, cache_dir, entry, slug, fp, recipe):
                      (SITE / "thumbnails" / f"{slug}.png").write_bytes(
                          bake_thumbnail(kind, asset, data_dir, args.style))
              except Exception as ex:
                  print(f"  SKIP {author}/{name}: {type(ex).__name__}: {ex}")
                  continue
              shutil.copy2(asset, SITE / "assets" / f"{slug}{asset.suffix.lower()}")
              entry["thumbnail"] = f"thumbnails/{slug}.png"
              entry["asset"] = f"assets/{slug}{asset.suffix.lower()}"
              manifest[f"{kind}s"].append(entry)
              who = entry.get("author") or "no author known"
              print(f"  + {author}/{name}  ({entry.get('name')}) -- {who}")

    if cache_dir is not None:
        shutil.rmtree(cache_dir, ignore_errors=True)

    (SITE / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for f in WEB_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, SITE / f.name)
    build_vrmod_zip(SITE / "vrmod.zip")

    items = manifest["cars"] + manifest["tracks"]
    n = len(items)
    attributed = sum(1 for e in items if e.get("author"))
    ids = {e["id"] for e in items}
    if len(ids) != n:
        print(f"\n  WARNING: {n - len(ids)} duplicate id(s) -- thumbnails "
              f"will have overwritten each other")
    prints = collections.Counter(e.get("fingerprint") for e in items if e.get("fingerprint"))
    same = sum(c - 1 for c in prints.values() if c > 1)
    print(f"\nbuilt site/ -- {len(manifest['cars'])} cars, "
          f"{len(manifest['tracks'])} tracks ({n} thumbnails baked)")
    print(f"  attributed to a person: {attributed}/{n}"
          + ("" if not n or attributed == n else
             "  -- the rest are assets several packs ship (usually a stock file "
             "riding along in a retexture), which the corpus will not guess at"))
    if same:
        print(f"  byte-identical duplicates: {same} entr(ies) share an asset with "
              f"another, under a different pack -- the same car redistributed, "
              f"not a variant. Merging them is a curation call, not a build one.")
    print("  serve with:  python -m http.server -d site 8000")


if __name__ == "__main__":
    main()
