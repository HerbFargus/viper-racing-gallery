"""Checks for the corpus reader's stock filter: hash decides, not name.

A modified Viper has to be called Viper.car, and a retextured Sunset Mesa has
to be nfield.trk -- the filename is the identity. Excluding stock NAMES dropped
47 such mods from the gallery. This builds a two-file pack and checks that a
stock-named file is skipped only when its bytes are the game's own.

    python scripts/check_corpus_source.py
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import vrmod  # noqa: F401  (installed in CI)
except ImportError:                                  # a sibling checkout, for local runs
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "viper-mod-manager"))
import corpus_source as cs  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(("  ok    " if ok else "  FAIL  ") + name + (f"  ({detail})" if detail else ""))


def pack_with(root: Path, viper_bytes: bytes) -> dict:
    z = root / "trees" / "cars" / "pack.zip"
    z.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Viper.car", viper_bytes)
        zf.writestr("other.car", b"someone's own car")
    return {"trees": [str(root / "trees" / "cars")],
            "items": [{"path": "cars/pack.zip", "collection": "cars/somebody",
                       "cars": ["Viper.car", "other.car"], "tracks": []}]}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="check_corpus_"))
    saved = set(cs.STOCK_SHA256)
    try:
        stock = b"the game's own viper"
        cs.STOCK_SHA256.add(hashlib.sha256(stock).hexdigest())
        m = pack_with(tmp / "a", stock)
        check("a stock-named file is still a candidate", cs.own_assets(m["items"][0]) == ["Viper.car", "other.car"])
        names = sorted(n for _c, n, _p, _i in cs.iter_assets(m))
        check("...but the game's own bytes are skipped", names == ["other"], str(names))
        m = pack_with(tmp / "b", b"Val's 280 hp build")
        names = sorted(n for _c, n, _p, _i in cs.iter_assets(m))
        check("a MODIFIED Viper.car is catalogued", names == ["Viper", "other"], str(names))
        check("stock names are case-blind", cs.is_stock_name("VIPER.CAR") and cs.is_stock_name("cars\\NField.trk"))
        check("every stock slot has its in-game name", len(cs.SLOT_NAMES) == 8 and cs.SLOT_NAMES["nfield"] == "Sunset Mesa")
    finally:
        cs.STOCK_SHA256.clear(); cs.STOCK_SHA256.update(saved)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
