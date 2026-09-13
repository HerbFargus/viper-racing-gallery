"""Check that an incremental build produces exactly what a cold build does.

WHY THIS EXISTS. Building this cache lost data silently, twice, and neither run
reported anything wrong:

  * flat extraction collapsed Donut.zip's cars/exotic/luigi.car and
    cars/original/luigi.car into one file -- two different cars, one survived;
  * the pack-level skip reused a PARTIAL set of a pack's entries and dropped 25
    assets, while printing "0 rendered, all reused", which reads exactly like a
    healthy cache.

Both were caught only by comparing a total against the previous run. A cache
that quietly returns less than it should is worse than no cache, so the
invariant is asserted here instead of being something somebody remembers to
eyeball: an incremental rebuild must produce the same ids, the same
fingerprints and byte-identical thumbnails as a cold one.

Runs against a --limit slice so it takes seconds; the full corpus was verified
the same way by hand (2,022 entries, all three identical).

    python scripts/check_incremental.py [N]
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# NEVER the real site/. This suite does cold builds, and a build WIPES its
# output directory -- so running the tests used to destroy the published
# catalogue, which costs 25 minutes to regenerate. Its own temp directory costs
# nothing and cannot ruin anyone's afternoon.
SITE = Path(tempfile.mkdtemp(prefix="check_incremental_"))
BUILD = [sys.executable, str(ROOT / "scripts" / "build_manifest.py"),
         "--from-corpus", "--out-dir", str(SITE)]

PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


def build(*extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(BUILD + list(extra), capture_output=True, text=True,
                          cwd=str(ROOT))


def snapshot() -> dict:
    m = json.loads((SITE / "manifest.json").read_text(encoding="utf-8"))
    out = {}
    for e in m["cars"] + m["tracks"]:
        png = SITE / e["thumbnail"]
        out[e["id"]] = {
            "fingerprint": e.get("fingerprint"),
            "thumb": hashlib.sha256(png.read_bytes()).hexdigest() if png.is_file() else None,
            "name": e.get("name"), "author": e.get("author"),
        }
    return out


def main() -> int:
    n = sys.argv[1] if len(sys.argv) > 1 else "60"

    cold = build("--limit", n)
    if cold.returncode != 0 or not (SITE / "manifest.json").is_file():
        print("  cold build failed:\n" + (cold.stderr or cold.stdout)[-800:])
        return 1
    a = snapshot()
    check("the cold build produced entries", bool(a), f"{len(a)} assets")
    check("every entry has a thumbnail on disk",
          all(v["thumb"] for v in a.values()),
          f"{sum(1 for v in a.values() if not v['thumb'])} missing")
    check("every entry has a fingerprint",
          all(v["fingerprint"] for v in a.values()))

    warm = build("--limit", n, "--incremental")
    if warm.returncode != 0:
        print("  incremental build failed:\n" + (warm.stderr or warm.stdout)[-800:])
        return 1
    b = snapshot()

    # The invariant. Count first -- that is what both real bugs showed up as.
    check("incremental produces the SAME NUMBER of assets", len(a) == len(b),
          f"cold {len(a)}, incremental {len(b)}")
    check("  ...the same ids", set(a) == set(b),
          f"only in cold: {sorted(set(a) - set(b))[:3]}" if set(a) - set(b) else "")
    shared = set(a) & set(b)
    check("  ...the same fingerprints",
          all(a[k]["fingerprint"] == b[k]["fingerprint"] for k in shared))
    check("  ...byte-identical thumbnails",
          all(a[k]["thumb"] == b[k]["thumb"] for k in shared),
          f"{sum(1 for k in shared if a[k]['thumb'] != b[k]['thumb'])} differ")
    check("  ...and the same derived metadata",
          all(a[k]["name"] == b[k]["name"] and a[k]["author"] == b[k]["author"]
              for k in shared))

    # It must actually have skipped work, or it is only "correct" by doing the
    # full job again -- which would pass every check above and help nobody.
    check("the incremental run actually reused instead of re-rendering",
          "0 rendered" in warm.stdout,
          next((ln.strip() for ln in warm.stdout.splitlines()
                if "assets --" in ln), "no summary line"))
    check("no duplicate-id warning in either build",
          "WARNING" not in cold.stdout and "WARNING" not in warm.stdout)

    # Structural, not observational. An earlier version compared the real
    # site/ before and after, which failed the moment an unrelated build ran
    # at the same time -- the suite cannot assert that nothing else on the
    # machine writes to a directory. What it CAN assert is that it directed
    # every build somewhere else, which is the actual guarantee.
    check("the suite built somewhere other than the real site/",
          not str(SITE).startswith(str(ROOT / "site")), str(SITE))
    check("  ...and every build command said so explicitly",
          "--out-dir" in BUILD and BUILD[BUILD.index("--out-dir") + 1] == str(SITE))

    shutil.rmtree(SITE, ignore_errors=True)
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
