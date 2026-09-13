"""Upload the community pack corpus to a file host, and record where it went.

WHY. The gallery catalogues 2,023 assets it does not host. Every entry carries
the pack it came from and that pack's sha256, but no URL, so the Download button
has nothing to point at and the live 3D viewer -- the thing the gallery is for
-- can fetch nothing.

WHY IT LIVES HERE. The corpus tools in the toolkit repo -- index_carpacks.py,
repack_zip.py -- answer "what IS this corpus". This answers "how does someone
GET it", which is this repo's job: the URLs it produces go straight into this
repo's manifest, and the gallery is the thing that breaks without them.

WHY A HOST ABSTRACTION RATHER THAN ONE HOST. The first version of this targeted
archive.org alone, and archive.org removed the uploader's earlier car files. A
corpus that took months to assemble should not be one policy decision away from
having nowhere to live. Each host is a small class here; the plan, the
resume logic and the URL map are shared.

    huggingface   the default. Free, no card, built for GB-scale public files,
                  git-LFS backed so uploads resume. Its `resolve` URLs send
                  CORS headers -- measured against this gallery's own origin --
                  so the in-browser viewer works, which no other candidate
                  managed.
    archive       archive.org. One item per collection. Downloads only: file
                  responses carry no CORS header.

    python scripts/upload_packs.py --repo you/viper-racing-mods
    python scripts/upload_packs.py --repo you/viper-racing-mods --execute
    python scripts/upload_packs.py --repo ... --urls PACK-URLS.json

DRY RUN BY DEFAULT, because publishing 3.4 GB of other people's work is not a
thing to do by accident. IDEMPOTENT, because at 3.4 GB an interrupted run has
to be resumable rather than restartable: every run lists what is already on the
host and uploads only the difference.

CREDENTIALS are the host's own business and never this script's. It reads
whatever `huggingface-cli login` or `ia configure` already wrote, and stops
with an instruction if there is nothing there. It does not prompt for, accept
or store a password or token.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

CC = Path(__file__).resolve().parent.parent.parent
TREES = {
    "cars": CC / "viper-racing-community-cars",
    "tracks": CC / "viper-racing-community-tracks",
}

# Not uploaded unless asked for: `original` holds the retail game's own track
# data, extracted. MGI's files, not community work -- a different question from
# redistributing someone's mod, and the gallery does not need it.
DEFAULT_EXCLUDE = {"original"}


def plan(exclude: set[str]) -> list[tuple[Path, str]]:
    """[(local path, path on the host), ...], mirroring the corpus layout.

    `cars/valscars/foo.zip` rather than a flattened name: `vrgt` exists in both
    trees and holds different mods in each, and the tree/collection split is
    how the community filed these in the first place.
    """
    out: list[tuple[Path, str]] = []
    for tree, root in sorted(TREES.items()):
        if not root.is_dir():
            raise SystemExit(f"error: no such tree: {root}")
        for p in sorted(root.rglob("*.zip")):
            rel = p.relative_to(root)
            if len(rel.parts) < 2 or rel.parts[0] in exclude:
                continue
            out.append((p, f"{tree}/{rel.as_posix()}"))
    return out


def source_key(local: Path) -> str:
    """The manifest's `source_pack` value for this file, which is what the
    catalogue joins on."""
    for tree, root in TREES.items():
        if root in local.parents:
            return f"{root.name}/{local.relative_to(root).as_posix()}"
    return local.name


class Host:
    name = "?"

    def existing(self) -> set[str]:
        """Whatever the host already holds, in whatever form it names things.
        Empty is always safe -- it only means everything is offered again, and
        both hosts treat a re-upload of an identical file as a no-op."""
        raise NotImplementedError

    def has(self, remote: str, have: set[str]) -> bool:
        """Is this planned file already up? Each host names things its own way
        -- Hugging Face by repo path, archive.org by item plus bare filename --
        so the comparison belongs beside the naming, not in the caller."""
        return remote in have

    def url(self, remote: str) -> str:
        raise NotImplementedError

    def upload(self, items: list[tuple[Path, str]]) -> tuple[int, int]:
        raise NotImplementedError


class HuggingFace(Host):
    name = "huggingface"

    def __init__(self, repo: str, revision: str = "main"):
        self.repo, self.revision = repo, revision

    def _api(self):
        try:
            from huggingface_hub import HfApi
        except ImportError:
            raise SystemExit("error: pip install huggingface_hub")
        return HfApi()

    def existing(self) -> set[str]:
        # Not having the library is not an error here: the whole plan, and the
        # URL map that follows from it, are computable without ever talking to
        # the host. Only --execute genuinely needs huggingface_hub.
        try:
            from huggingface_hub import HfApi
            from huggingface_hub.utils import RepositoryNotFoundError
        except ImportError:
            print("  (huggingface_hub not installed -- planning as if the repo "
                  "were empty; `pip install huggingface_hub` before --execute)")
            return set()
        try:
            return set(HfApi().list_repo_files(self.repo, repo_type="dataset",
                                               revision=self.revision))
        except RepositoryNotFoundError:
            return set()
        except Exception as ex:                                  # noqa: BLE001
            print(f"  (could not list {self.repo}: {type(ex).__name__}: {ex})")
            return set()

    def url(self, remote: str) -> str:
        from urllib.parse import quote
        return (f"https://huggingface.co/datasets/{self.repo}/resolve/"
                f"{self.revision}/{quote(remote)}")

    def upload(self, items: list[tuple[Path, str]]) -> tuple[int, int]:
        from huggingface_hub import HfApi
        from huggingface_hub.utils import HfHubHTTPError
        api = self._api()
        if not _hf_token():
            raise SystemExit(
                "error: not logged in to Hugging Face. Run `huggingface-cli "
                "login` (or `hf auth login`) and paste a WRITE token from "
                "https://huggingface.co/settings/tokens. This script never "
                "handles the token itself.")
        api.create_repo(self.repo, repo_type="dataset", exist_ok=True)

        # Commit in batches. One commit per file would be 2,023 commits, and
        # one commit for everything would mean a 3.4 GB all-or-nothing push
        # that cannot be resumed if it drops.
        from huggingface_hub import CommitOperationAdd
        done = failed = 0
        started = time.time()
        BATCH = 50
        for i in range(0, len(items), BATCH):
            chunk = items[i:i + BATCH]
            ops = [CommitOperationAdd(path_in_repo=remote,
                                      path_or_fileobj=str(local))
                   for local, remote in chunk]
            try:
                api.create_commit(
                    self.repo, repo_type="dataset", operations=ops,
                    commit_message=f"Add {len(chunk)} packs "
                                   f"({i + len(chunk)}/{len(items)})")
                done += len(chunk)
            except (HfHubHTTPError, Exception) as ex:            # noqa: BLE001
                failed += len(chunk)
                print(f"    FAILED batch at {i}: {type(ex).__name__}: "
                      f"{str(ex)[:160]}")
            rate = (done + failed) / max(time.time() - started, 1e-9)
            print(f"    {done + failed:>5}/{len(items)}  {rate:5.2f} files/s  "
                  f"{failed} failed")
        return done, failed


class ArchiveOrg(Host):
    name = "archive"

    def __init__(self, prefix: str = "viper-racing"):
        self.prefix = prefix

    def _item(self, remote: str) -> str:
        tree, collection, _ = remote.split("/", 2)
        if collection == "vrgt":            # exists in both trees, different mods
            return f"{self.prefix}-vrgt-{tree}"
        return f"{self.prefix}-{collection}"

    def existing(self) -> set[str]:
        import urllib.request
        have: set[str] = set()
        seen: set[str] = set()
        for _, remote in plan(DEFAULT_EXCLUDE):
            ident = self._item(remote)
            if ident in seen:
                continue
            seen.add(ident)
            try:
                with urllib.request.urlopen(
                        f"https://archive.org/metadata/{ident}", timeout=60) as r:
                    meta = json.loads(r.read().decode("utf-8"))
            except Exception:                                    # noqa: BLE001
                continue
            for f in meta.get("files", []):
                have.add(f"{ident}::{f['name']}")
        return have

    def has(self, remote: str, have: set[str]) -> bool:
        return f"{self._item(remote)}::{remote.rsplit('/', 1)[-1]}" in have

    def url(self, remote: str) -> str:
        return (f"https://archive.org/download/{self._item(remote)}/"
                f"{remote.rsplit('/', 1)[-1]}")

    def upload(self, items: list[tuple[Path, str]]) -> tuple[int, int]:
        try:
            from internetarchive import get_item
        except ImportError:
            raise SystemExit("error: pip install internetarchive")
        cfg = Path.home() / ".config" / "internetarchive" / "ia.ini"
        if not cfg.is_file():
            raise SystemExit(f"error: no credentials at {cfg}. Run "
                             f"`ia configure` first.")
        done = failed = 0
        for local, remote in items:
            ident = self._item(remote)
            try:
                get_item(ident).upload_file(
                    str(local), key=remote.rsplit("/", 1)[-1],
                    metadata={"collection": "opensource", "mediatype": "software",
                              "title": f"Viper Racing community mods -- {ident}"},
                    verify=True, retries=3)
                done += 1
            except Exception as ex:                              # noqa: BLE001
                failed += 1
                print(f"    FAILED {ident}/{remote}: {type(ex).__name__}: {ex}")
        return done, failed


def _hf_token() -> str | None:
    try:
        from huggingface_hub import get_token
        return get_token()
    except Exception:                                            # noqa: BLE001
        for p in (Path.home() / ".cache" / "huggingface" / "token",
                  Path.home() / ".huggingface" / "token"):
            if p.is_file() and p.read_text().strip():
                return p.read_text().strip()
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="huggingface",
                    choices=("huggingface", "archive"))
    ap.add_argument("--repo", default=None,
                    help="Hugging Face dataset repo, as <user>/<name>")
    ap.add_argument("--execute", action="store_true",
                    help="actually upload. Without it nothing is sent")
    ap.add_argument("--only", default=None,
                    help="restrict to one collection (e.g. frankscars), to put "
                         "a real one up and look at it before the rest")
    ap.add_argument("--include-original", action="store_true",
                    help="also upload `original` -- the retail game's own track "
                         "data rather than community work")
    ap.add_argument("--urls", type=Path, default=None,
                    help="write the source_pack -> download URL map here")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    if args.host == "huggingface" and not args.repo:
        raise SystemExit("error: --repo <user>/<name> is required for "
                         "huggingface (it is where the files go)")
    host: Host = (HuggingFace(args.repo) if args.host == "huggingface"
                  else ArchiveOrg())

    exclude = set() if args.include_original else set(DEFAULT_EXCLUDE)
    items = plan(exclude)
    if args.only:
        items = [(p, r) for p, r in items if r.split("/")[1] == args.only]
        if not items:
            raise SystemExit(f"error: --only {args.only} matched nothing")

    have = host.existing()
    urls = {source_key(p): host.url(r) for p, r in items}
    todo = [(p, r) for p, r in items if not host.has(r, have)]
    if args.limit:
        todo = todo[:args.limit]

    size = sum(p.stat().st_size for p, _ in items)
    left = sum(p.stat().st_size for p, _ in todo)
    print(f"  host      {host.name}" + (f"  ({args.repo})" if args.repo else ""))
    print(f"  planned   {len(items):,} packs, {size / 1024**3:.2f} GB")
    print(f"  already   {len(items) - len(todo):,} on the host")
    print(f"  to send   {len(todo):,} packs, {left / 1024**3:.2f} GB")
    if items:
        print(f"  example   {urls[source_key(items[0][0])]}")

    if args.urls:
        args.urls.write_text(json.dumps({
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "host": host.name,
            "cors": host.name == "huggingface",
            "note": "source_pack -> download URL. `cors` says whether the "
                    "in-browser viewer can fetch these, or only the download "
                    "button can use them.",
            "urls": urls,
        }, indent=1), encoding="utf-8")
        print(f"  wrote     {args.urls}  ({len(urls):,} URLs)")

    if not args.execute:
        print("\n  DRY RUN -- nothing uploaded. Re-run with --execute.")
        return 0
    if not todo:
        print("\n  nothing to do: the host already has everything planned.")
        return 0

    print(f"\n  uploading {len(todo):,} packs to {host.name}")
    done, failed = host.upload(todo)
    print(f"\n  uploaded {done:,}, failed {failed:,}")
    print("  re-run to resume -- anything already there is skipped.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
