"""Checks for the publishing path: restore a catalogue, build on top, ship it.

WHY THIS EXISTS. The live gallery served `{"cars": [], "tracks": []}` while
every CI run reported success. CI builds from the checked-in by-author folders;
the bulk catalogue is derived from 4.9 GB of archives that cannot live in the
repo, so those folders are empty and the build had nothing to do. Nothing
failed. Nothing warned. The site was simply blank.

So the checks here are about the JOIN and the GUARDS, not about rendering:

  - an empty build is refused rather than published
  - --base carries a restored catalogue forward, thumbnails and all
  - a locally built entry beats the carried one of the same id
  - --base inside site/ is refused, since site/ is wiped before the build

Every build runs into a temp directory via --out-dir. A test suite that writes
to site/ would destroy the very catalogue it exists to protect -- which is a
mistake this repo has already made once.

    python scripts/check_publish.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = [sys.executable, str(ROOT / "scripts" / "build_manifest.py")]

PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


# The repo's own cars/ and tracks/ hold real submissions now, so a check that
# means "no submissions" must not read them. Every build here runs against a
# temp submissions folder: EMPTY unless a check puts a fixture in it.
SUBMISSIONS = Path(tempfile.mkdtemp(prefix="check_publish_subs_"))


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(BUILD + ["--submissions", str(SUBMISSIONS)] + list(args), cwd=ROOT,
                          capture_output=True, text=True, timeout=1800)


def fake_catalogue(root: Path) -> Path:
    """A minimal stand-in for the published artifact: two entries, two PNGs.

    Deliberately not the real 24 MB one -- these checks are about the join, and
    a fixture that takes half a minute to unpack is a fixture nobody runs.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "thumbnails").mkdir(exist_ok=True)
    png = bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001"
                        "0802000000907753de0000000c4944415408d76360000000"
                        "020001e221bc330000000049454e44ae426082")
    entries = {"cars": [], "tracks": []}
    for kind, ident in (("cars", "fixture/pack.zip/alpha"),
                        ("tracks", "fixture/pack.zip/beta")):
        slug = ident.replace("/", "__")
        (root / "thumbnails" / f"{slug}.png").write_bytes(png)
        entries[kind].append({
            "id": ident, "kind": kind[:-1], "collection": "fixture",
            "name": ident.rsplit("/", 1)[-1],
            "file": ident.rsplit("/", 1)[-1] + (".car" if kind == "cars" else ".tra"),
            "thumbnail": f"thumbnails/{slug}.png",
            "fingerprint": "0" * 64, "render": "carried",
        })
    (root / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")
    return root


def read(out: Path) -> dict:
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="check_publish_"))
    try:
        base = fake_catalogue(tmp / "catalogue")

        # --- the bug that started this -------------------------------------
        out = tmp / "empty"
        r = run("--out-dir", str(out))
        check("an empty build FAILS instead of publishing nothing",
              r.returncode != 0, f"exit {r.returncode}")
        check("...and says why", "no entries at all" in (r.stdout + r.stderr),
              (r.stdout + r.stderr).strip().splitlines()[-1][:60] if (r.stdout + r.stderr).strip() else "")
        check("...and writes no manifest at all",
              not (out / "manifest.json").is_file(),
              "a half-written site is worse than none")

        r = run("--out-dir", str(tmp / "allowed"), "--allow-empty")
        check("--allow-empty still permits a deliberate empty build",
              r.returncode == 0, f"exit {r.returncode}")

        # --- the restore path CI takes -------------------------------------
        out = tmp / "carried"
        r = run("--out-dir", str(out), "--base", str(base))
        check("--base restores a published catalogue", r.returncode == 0,
              f"exit {r.returncode}")
        if r.returncode == 0:
            man = read(out)
            check("every carried entry arrives",
                  len(man["cars"]) == 1 and len(man["tracks"]) == 1,
                  f"{len(man['cars'])} cars, {len(man['tracks'])} tracks")
            # The failure that matters most: entries without their pictures.
            missing = [e["thumbnail"] for e in man["cars"] + man["tracks"]
                       if not (out / e["thumbnail"]).is_file()]
            check("...with its thumbnail, not just its row", not missing,
                  f"{len(missing)} missing")

        # --- the CI failure this cost a deploy on ---------------------------
        # 2,023 entries arrived with 2,021 thumbnails because carry_base
        # skipped a source file it could not find, quietly. The manifest still
        # claimed both, so nothing downstream could tell they were pictureless.
        holed = fake_catalogue(tmp / "holed")
        gone = next((holed / "thumbnails").glob("*.png"))
        name = gone.name
        gone.unlink()
        r = run("--out-dir", str(tmp / "holed_out"), "--base", str(holed))
        check("a carried entry with no thumbnail FAILS the build",
              r.returncode != 0, f"exit {r.returncode}")
        check("...and names the file it could not find",
              name in (r.stdout + r.stderr), name)

        # --- names that have to survive a zip, a URL and a filesystem --------
        rough = fake_catalogue(tmp / "rough")
        r = run("--out-dir", str(tmp / "rough_out"), "--base", str(rough))
        if r.returncode == 0:
            import re as _re
            names = [e["thumbnail"].split("/")[-1]
                     for e in read(tmp / "rough_out")["cars"]
                     + read(tmp / "rough_out")["tracks"]]
            check("carried names are left exactly as the artifact has them",
                  all(_re.match(r"^[A-Za-z0-9._-]+$", n) for n in names),
                  "the fixture's are already plain")

        # --- duplicates fold into one card -----------------------------------
        # The same file shipped in two packs (VRgt's Monza in its normal and its
        # _lowres zip, byte for byte) must be ONE card that remembers both packs;
        # two different files sharing a name stay two, the low-res one labelled.
        twin = fake_catalogue(tmp / "twin")
        man = json.loads((twin / "manifest.json").read_text(encoding="utf-8"))
        base_track = man["tracks"][0]
        png = next((twin / "thumbnails").glob("*.png")).read_bytes()
        extra = []
        for ident, pack, fp in (("fixture/Monza_01_lowres.zip/beta", "Monza_01_lowres.zip", "0" * 64),
                                ("fixture/Big_01.zip/gamma", "Big_01.zip", "1" * 64),
                                ("fixture/Big_01_lowres.zip/gamma", "Big_01_lowres.zip", "2" * 64)):
            e = dict(base_track, id=ident, pack=pack, source_pack="fixture/" + pack, fingerprint=fp,
                     thumbnail=f"thumbnails/{ident.replace('/', '__')}.png",
                     name=ident.rsplit("/", 1)[-1], file=ident.rsplit("/", 1)[-1] + ".tra")
            (twin / e["thumbnail"]).write_bytes(png)
            extra.append(e)
        man["tracks"][0].update(pack="Monza_01.zip", source_pack="fixture/Monza_01.zip")
        man["tracks"] += extra
        (twin / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
        r = run("--out-dir", str(tmp / "twin_out"), "--base", str(twin))
        check("a catalogue with duplicates builds", r.returncode == 0, f"exit {r.returncode}")
        if r.returncode == 0:
            got = read(tmp / "twin_out")["tracks"]
            beta = [e for e in got if e["id"].endswith("/beta")]
            check("the same file in two packs is one card", len(beta) == 1, f"{len(beta)} cards")
            check("...the normal pack's, remembering the low-res one",
                  bool(beta) and beta[0]["id"] == "fixture/pack.zip/beta"
                  and [o["pack"] for o in beta[0].get("also_in", [])] == ["Monza_01_lowres.zip"],
                  str(beta[0].get("also_in") if beta else None))
            gamma = sorted(e["name"] for e in got if e["id"].endswith("/gamma"))
            check("two different files of one name stay two, the low-res one labelled",
                  gamma == ["gamma", "gamma (low-res)"], str(gamma))

        # --- a submission is built on top, and wins -------------------------
        # A real .car, since the point is that the whole derive-and-render
        # path runs on a submission. The repo ships no .car fixture -- one
        # would have to be someone's actual mod -- so this borrows from a
        # local install and skips cleanly when there is none.
        donor = None
        for d in (ROOT.parent / "game-files" / "installs" / "v1.0-RC",
                  ROOT.parent / "game-files" / "viper-racing-usa" / "Data"):
            donor = next((c for c in sorted(d.glob("*.car"))), None)
            if donor:
                break
        if donor is None:
            print("  --    no .car to hand, skipping the submission checks")
        else:
            author = SUBMISSIONS / "cars" / "_checkfixture" / donor.stem
            try:
                author.mkdir(parents=True, exist_ok=True)
                shutil.copy2(donor, author / donor.name)
                (author / "readme.txt").write_text("credits go here" + chr(10), encoding="utf-8")
                out = tmp / "both"
                r = run("--out-dir", str(out), "--base", str(base))
                check("a submission builds on top of the carried catalogue",
                      r.returncode == 0, f"exit {r.returncode}")
                if r.returncode == 0:
                    man = read(out)
                    ids = {e["id"] for e in man["cars"]}
                    check("the submission is in the manifest",
                          f"_checkfixture/{donor.stem}" in ids,
                          f"{len(man['cars'])} cars total")
                    check("and the carried entries survived alongside it",
                          "fixture/pack.zip/alpha" in ids)
                    fresh = [e for e in man["cars"]
                             if e["id"] == f"_checkfixture/{donor.stem}"]
                    check("a submission carries an asset URL, so it downloads",
                          bool(fresh and fresh[0].get("asset")),
                          "the carried corpus entries do not -- they are hosted "
                          "elsewhere")
                    sub = fresh[0] if fresh else {}
                    bundle = out / sub.get("bundle", "missing.zip")
                    names = zipfile.ZipFile(bundle).namelist() if bundle.is_file() else []
                    check("a submission with a readme downloads as a zip holding both",
                          sorted(names) == sorted([donor.name, "readme.txt"]), str(names))
                    check("...and the zip's copy is the hosted asset, byte for byte",
                          bool(names) and zipfile.ZipFile(bundle).read(donor.name)
                          == (out / sub["asset"]).read_bytes())
                    check("submissions sort ahead of the carried bulk",
                          man["cars"][0]["id"].startswith("_checkfixture/"),
                          man["cars"][0]["id"])

                # Same id in both: the local build must win, not be dropped
                # and not duplicated.
                collide = fake_catalogue(tmp / "collide")
                man = json.loads((collide / "manifest.json").read_text(encoding="utf-8"))
                man["cars"][0]["id"] = f"_checkfixture/{donor.stem}"
                man["cars"][0]["name"] = "CARRIED VERSION"
                (collide / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
                out = tmp / "override"
                r = run("--out-dir", str(out), "--base", str(collide))
                if r.returncode == 0:
                    got = [e for e in read(out)["cars"]
                           if e["id"] == f"_checkfixture/{donor.stem}"]
                    check("an id in both appears exactly once", len(got) == 1,
                          f"{len(got)} copies")
                    check("...and it is the freshly built one, not the carried",
                          bool(got) and got[0].get("name") != "CARRIED VERSION",
                          got[0].get("name") if got else "")
                else:
                    check("an id in both appears exactly once", False,
                          f"build failed, exit {r.returncode}")
            finally:
                shutil.rmtree(author.parent, ignore_errors=True)

        # --- the foot-gun --------------------------------------------------
        # Reading the catalogue from the directory the build is about to wipe
        # destroys it and produces nothing. The guard is written against the
        # OUTPUT directory, not the literal site/, so it holds under --out-dir
        # too -- which is the form this can test without wiping site/.
        selfbase = tmp / "selfbase"
        fake_catalogue(selfbase)
        r = run("--out-dir", str(selfbase), "--base", str(selfbase))
        check("--base pointing at the output directory is refused",
              r.returncode != 0, f"exit {r.returncode}")
        check("...and the catalogue it pointed at is still there",
              (selfbase / "manifest.json").is_file(),
              "refused before anything was wiped")

        # --- the packager ---------------------------------------------------
        pkg = [sys.executable, str(ROOT / "scripts" / "package_catalogue.py")]
        r = subprocess.run(pkg + ["--site", str(tmp / "carried"),
                                  "-o", str(tmp / "cat.zip")],
                           cwd=ROOT, capture_output=True, text=True, timeout=600)
        check("the packager zips a real catalogue", r.returncode == 0,
              f"exit {r.returncode}")
        if (tmp / "cat.zip").is_file():
            with zipfile.ZipFile(tmp / "cat.zip") as z:
                names = z.namelist()
            check("...and stamps it with what it holds", "CATALOGUE.json" in names,
                  f"{len(names)} files")

        empty = fake_catalogue(tmp / "nothing")
        (empty / "manifest.json").write_text('{"cars": [], "tracks": []}',
                                             encoding="utf-8")
        r = subprocess.run(pkg + ["--site", str(empty), "-o", str(tmp / "no.zip")],
                           cwd=ROOT, capture_output=True, text=True, timeout=600)
        check("the packager refuses an empty catalogue", r.returncode != 0,
              "publishing one is how the site went blank")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(SUBMISSIONS, ignore_errors=True)

    print(f"\n{PASS}/{PASS + FAIL} passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
