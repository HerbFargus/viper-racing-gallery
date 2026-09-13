"""Bundle the pack corpus into volumes small enough to publish as release assets.

WHY, ALONGSIDE upload_packs.py. That script puts every pack up individually, so
the gallery can link one car. This one is for the other kind of user: someone who
wants the whole set, once, without a crawler -- and for the project, as a
self-contained copy that does not depend on any one host's policy staying put.
The two are complements, not alternatives.

WHY IT LIVES IN THE GALLERY REPO rather than with the corpus tools that produced
the packs. Those tools -- index_carpacks.py, repack_zip.py -- answer "what IS
this corpus". These two answer "how does someone GET it", which is this repo's
whole job, and it is this repo's releases they publish into, next to the
catalogue that package_catalogue.py builds. Reading the corpus from a sibling
checkout is something the builder here already does (see corpus_source.py).

VOLUMES, BECAUSE OF ONE COLLECTION. GitHub caps a single release asset at 2 GiB
and valscars is 2,264 MB on its own, so a bundle-per-collection cannot be a rule
without an exception. Collections are bundled whole where they fit and split into
numbered parts where they do not, which keeps "I just want Frank's cars" a single
78 MB download instead of making everyone take 3.4 GB.

STORED, NOT DEFLATED. Every member is already a .zip; recompressing them buys
nothing and costs the better part of an hour.

VERIFIED BY COUNT AND BY HASH. The volumes are read back and every member's
sha256 compared against the source, and the set of members across all volumes
must equal the set of packs exactly -- no pack in two volumes, none in none.
This corpus has produced four silent-loss bugs so far and every one of them was
caught by a count rather than by a per-item check.

    python scripts/bundle_packs.py                    # -> dist/bundles/
    python scripts/bundle_packs.py --max-mb 1900
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import zipfile
from pathlib import Path

CC = Path(__file__).resolve().parent.parent.parent
TREES = {
    "cars": CC / "viper-racing-community-cars",
    "tracks": CC / "viper-racing-community-tracks",
}
DEFAULT_EXCLUDE = {"original"}

# GitHub's hard limit is 2 GiB per release asset. The margin covers the zip's
# own central directory and leaves room for a collection to grow before the
# split rule has to change.
MAX_MB = 1900


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def collect(exclude: set[str]) -> dict[str, list[tuple[Path, str]]]:
    """{collection key: [(local path, name inside the bundle), ...]}."""
    out: dict[str, list[tuple[Path, str]]] = {}
    for tree, root in sorted(TREES.items()):
        if not root.is_dir():
            raise SystemExit(f"error: no such tree: {root}")
        for p in sorted(root.rglob("*.zip")):
            rel = p.relative_to(root)
            if len(rel.parts) < 2 or rel.parts[0] in exclude:
                continue
            # `vrgt` is in both trees and holds different mods in each.
            key = f"{rel.parts[0]}-{tree}" if rel.parts[0] == "vrgt" else rel.parts[0]
            out.setdefault(key, []).append((p, rel.as_posix()))
    return out


def volumes(files: list[tuple[Path, str]], max_bytes: int
            ) -> list[list[tuple[Path, str]]]:
    """Split a collection into volumes that each fit. Greedy in sorted order,
    so a pack stays in the volume its neighbours are in and re-running produces
    the same split."""
    out: list[list[tuple[Path, str]]] = [[]]
    running = 0
    for path, name in files:
        size = path.stat().st_size
        if out[-1] and running + size > max_bytes:
            out.append([])
            running = 0
        out[-1].append((path, name))
        running += size
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    # dist/ in this repo, which .gitignore already covers: these are release
    # assets, not repo content -- 3.4 GB of them.
    ap.add_argument("-o", "--out", type=Path,
                    default=Path(__file__).resolve().parent.parent / "dist" / "bundles")
    ap.add_argument("--max-mb", type=int, default=MAX_MB)
    ap.add_argument("--include-original", action="store_true",
                    help="also bundle `original` -- the retail game's own track "
                         "data rather than community work")
    ap.add_argument("--only", default=None, help="one collection key")
    args = ap.parse_args()

    exclude = set() if args.include_original else set(DEFAULT_EXCLUDE)
    groups = collect(exclude)
    if args.only:
        groups = {k: v for k, v in groups.items() if k == args.only}
        if not groups:
            raise SystemExit(f"error: --only {args.only} matched nothing")

    max_bytes = args.max_mb * 1024 * 1024
    args.out.mkdir(parents=True, exist_ok=True)

    # Hash every source once, up front. This is what the read-back is checked
    # against, and doing it before any writing means a mismatch cannot be
    # blamed on something that changed underneath the run.
    want: dict[str, str] = {}
    for key, files in groups.items():
        for path, name in files:
            want[f"{key}/{name}"] = sha256_file(path)

    print(f"  {len(want):,} packs into bundles of at most {args.max_mb:,} MB\n")
    written: list[dict] = []
    for key, files in sorted(groups.items()):
        parts = volumes(files, max_bytes)
        for i, part in enumerate(parts, 1):
            stem = key if len(parts) == 1 else f"{key}.part{i}"
            dest = args.out / f"viper-racing-{stem}.zip"
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
                for path, name in part:
                    z.write(path, name)
            size = dest.stat().st_size
            if size > 2 * 1024**3:
                raise SystemExit(
                    f"error: {dest.name} is {size/1024**3:.2f} GB, over GitHub's "
                    f"2 GiB asset limit. Lower --max-mb.")
            written.append({"file": dest.name, "collection": key, "part": i,
                            "of": len(parts), "packs": len(part), "bytes": size})
            print(f"  {dest.name:44s} {len(part):>5,} packs {size/1024/1024:>8,.0f} MB")

    # --- verification -------------------------------------------------------
    print("\n  verifying")
    got: dict[str, str] = {}
    dupes: list[str] = []
    for w in written:
        key = w["collection"]
        with zipfile.ZipFile(args.out / w["file"]) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                ident = f"{key}/{info.filename}"
                if ident in got:
                    dupes.append(ident)
                got[ident] = hashlib.sha256(z.read(info)).hexdigest()

    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    changed = sorted(k for k in set(got) & set(want) if got[k] != want[k])
    print(f"    packs in           {len(want):,}")
    print(f"    packs out          {len(got):,}")
    print(f"    missing            {len(missing):,}")
    print(f"    unexpected         {len(extra):,}")
    print(f"    in two volumes     {len(dupes):,}")
    print(f"    content changed    {len(changed):,}")
    for label, rows in (("missing", missing), ("unexpected", extra),
                        ("duplicated", dupes), ("changed", changed)):
        for r in rows[:5]:
            print(f"      {label}: {r}")
    if missing or extra or dupes or changed:
        print("\n  FAILED -- do not publish these.")
        return 1

    sums = args.out / "SHA256SUMS"
    sums.write_text("".join(
        f"{sha256_file(args.out / w['file'])}  {w['file']}\n" for w in written),
        encoding="utf-8")
    (args.out / "BUNDLES.json").write_text(json.dumps({
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "packs": len(want),
        "volumes": written,
        "note": "Complete community pack corpus, as distributed. Split only to "
                "fit GitHub's 2 GiB per-asset limit; each volume is a plain zip "
                "of whole packs and is usable on its own.",
    }, indent=1), encoding="utf-8")

    total = sum(w["bytes"] for w in written)
    print(f"\n  {len(written)} volumes, {total/1024**3:.2f} GB, all verified")
    print(f"  wrote {sums} and BUNDLES.json")
    print(f"\n  publish with:\n"
          f"    gh release create packs-{time.strftime('%Y-%m-%d')} "
          f"{args.out}/*.zip {args.out}/SHA256SUMS \\\n"
          f"      --title 'Community pack corpus {time.strftime('%Y-%m-%d')}' \\\n"
          f"      --notes '{len(want):,} packs, {total/1024**3:.2f} GB, "
          f"{len(written)} volumes'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
