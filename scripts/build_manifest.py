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

import json
import re
import shutil
import sys
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

from vrmod import archive, envelope, carshot, car, track as track_mod, viewer  # noqa: E402

CARS_DIR = ROOT / "cars"
TRACKS_DIR = ROOT / "tracks"
WEB_DIR = ROOT / "web"
SITE = ROOT / "site"


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


def car_entry(author: str, name: str, path: Path) -> dict:
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
        "id": f"{author}/{name}", "kind": "car", "author": author,
        "name": spec.get("name") or name, "file": path.name,
        "spec": {k: v for k, v in spec.items() if k != "name"},
        "parts": sum(1 for _ in mods),
        "cockpit": any(e.name.lower() == "cockpit.tab" for e in entries),
        "peak_vertices": peak,
        "provenance": prov["verdict"],
        "missing_textures": prov["missing"],
    }


def track_entry(author: str, name: str, path: Path) -> dict:
    try:
        miles = track_mod.length_miles(path)
    except Exception:
        miles = None
    mesh = viewer._track_render_mesh(path)
    return {
        "id": f"{author}/{name}", "kind": "track", "author": author,
        "name": name, "file": path.name,
        "miles": round(miles, 2) if miles else None,
        "vertices": len(mesh.vertices), "faces": len(mesh.faces),
    }


def bake_thumbnail(kind: str, path: Path) -> bytes:
    return carshot.to_png(path) if kind == "car" else carshot.track_to_png(path)


def build_vrmod_zip(dest: Path) -> None:
    """The pinned vrmod, zipped for the live client-side viewer (same as Repo A's
    scripts/build_gallery.py -- vrmod runs unchanged in the browser via Pyodide)."""
    src = Path(vrmod.__file__).resolve().parent
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if "__pycache__" in p.parts or p.suffix == ".pyc" or not p.is_file():
                continue
            z.write(p, f"vrmod/{p.relative_to(src).as_posix()}")


def main() -> None:
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "thumbnails").mkdir(parents=True)
    (SITE / "assets").mkdir()

    manifest = {"cars": [], "tracks": []}
    for kind, base, glob, entry_fn in (
        ("car", CARS_DIR, "*.car", car_entry),
        ("track", TRACKS_DIR, "*.tr[ka]", track_entry),
    ):
        for asset in sorted(base.glob(f"*/*/{glob}")):
            author, name = asset.parent.parent.name, asset.parent.name
            slug = f"{author}__{name}"
            try:
                entry = entry_fn(author, name, asset)
                png = bake_thumbnail(kind, asset)
            except Exception as ex:
                print(f"  SKIP {author}/{name}: {type(ex).__name__}: {ex}")
                continue
            (SITE / "thumbnails" / f"{slug}.png").write_bytes(png)
            shutil.copy2(asset, SITE / "assets" / f"{slug}{asset.suffix.lower()}")
            entry["thumbnail"] = f"thumbnails/{slug}.png"
            entry["asset"] = f"assets/{slug}{asset.suffix.lower()}"
            manifest[f"{kind}s"].append(entry)
            print(f"  + {author}/{name}  ({entry.get('name')})")

    (SITE / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for f in WEB_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, SITE / f.name)
    build_vrmod_zip(SITE / "vrmod.zip")

    n = len(manifest["cars"]) + len(manifest["tracks"])
    print(f"\nbuilt site/ -- {len(manifest['cars'])} cars, {len(manifest['tracks'])} tracks "
          f"({n} thumbnails baked); serve with:  python -m http.server -d site 8000")


if __name__ == "__main__":
    main()
