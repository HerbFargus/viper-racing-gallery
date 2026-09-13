"""Package the built catalogue as a release artifact.

WHY THIS EXISTS. The catalogue is derived from ~2,000 archives totalling 4.9 GB
that cannot live in the repo, and rendering it takes about half an hour. CI has
neither the archives nor the time, so it cannot rebuild the catalogue -- it has
to restore one that was built here. That makes the catalogue a build PRODUCT,
published like any other: a versioned artifact with a hash, not something
regenerated on every push.

The alternative was committing 29 MB of PNGs and re-committing them on every
re-render. A release asset keeps the repo something you can still clone.

    python scripts/package_catalogue.py                  # -> dist/catalogue.zip
    gh release create catalogue-2026-09-13 dist/catalogue.zip

CI then downloads the asset, unpacks it, and runs build_manifest.py --base
against it, so contributed mods are built fresh on top of the restored bulk.

The zip carries CATALOGUE.json -- counts, the sha256 of the manifest, when it
was built and from which vrmod -- so a deployed site can be traced back to the
build that made it rather than merely asserted to be current.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def vrmod_revision() -> str | None:
    """Which vrmod rendered these thumbnails. A catalogue is only reproducible
    if you know what drew it, and the renderer is in the other repo."""
    for candidate in (ROOT.parent / "viper-mod-manager", ROOT / "_vrmod"):
        if not (candidate / ".git").exists():
            continue
        try:
            out = subprocess.run(["git", "-C", str(candidate), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, timeout=30)
            if out.returncode == 0:
                return out.stdout.strip()
        except Exception:
            pass
    return None


def describe(site: Path) -> dict:
    manifest = json.loads((site / "manifest.json").read_text(encoding="utf-8"))
    cars, tracks = manifest.get("cars", []), manifest.get("tracks", [])
    recipes = sorted({e.get("render") for e in cars + tracks if e.get("render")})
    return {
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cars": len(cars),
        "tracks": len(tracks),
        "thumbnails": sum(1 for _ in (site / "thumbnails").glob("*.png")),
        "render_recipes": recipes,
        "manifest_sha256": sha256_file(site / "manifest.json"),
        "vrmod_revision": vrmod_revision(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--site", type=Path, default=SITE,
                    help="the built site to package (default: site/)")
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "dist" / "catalogue.zip")
    args = ap.parse_args()

    site = args.site
    if not (site / "manifest.json").is_file():
        raise SystemExit(f"error: {site} has no manifest.json -- build it first")

    info = describe(site)
    total = info["cars"] + info["tracks"]
    if not total:
        raise SystemExit(
            "error: refusing to package an empty catalogue. Publishing one is "
            "how the live site came to serve {\"cars\": [], \"tracks\": []} "
            "while every CI run reported success.")
    # Every entry names a thumbnail, so a shortfall means files went missing
    # between the build and here -- exactly what packaging must not ship.
    if info["thumbnails"] < total:
        raise SystemExit(
            f"error: {total:,} entries but only {info['thumbnails']:,} "
            f"thumbnails on disk. Something was lost after the build.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    files = [p for p in sorted(site.rglob("*")) if p.is_file()]
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(site).as_posix())
        z.writestr("CATALOGUE.json", json.dumps(info, indent=1))

    # Read it back. A packager that writes a truncated zip and says "done" is
    # the same failure this whole pipeline keeps producing in other forms.
    with zipfile.ZipFile(args.out) as z:
        bad = z.testzip()
        if bad is not None:
            raise SystemExit(f"error: {args.out} is corrupt at {bad}")
        written = len(z.namelist()) - 1          # less CATALOGUE.json
    if written != len(files):
        raise SystemExit(f"error: packed {written:,} of {len(files):,} files")

    size = args.out.stat().st_size
    print(f"  {info['cars']:,} cars, {info['tracks']:,} tracks, "
          f"{info['thumbnails']:,} thumbnails")
    print(f"  recipes: {', '.join(info['render_recipes'])}")
    print(f"  vrmod:   {info['vrmod_revision'] or 'unknown'}")
    print(f"  wrote    {args.out}  ({size / 1024 / 1024:.1f} MB, "
          f"{written:,} files)")
    print(f"  sha256   {sha256_file(args.out)}")
    print(f"\n  publish with:\n"
          f"    gh release create catalogue-{time.strftime('%Y-%m-%d')} "
          f"{args.out} --title 'Catalogue {time.strftime('%Y-%m-%d')}' "
          f"--notes '{info['cars']:,} cars, {info['tracks']:,} tracks'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
