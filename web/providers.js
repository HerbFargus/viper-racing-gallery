// The community gallery's asset provider. Everything to BROWSE comes from a
// static manifest.json + pre-baked thumbnail PNGs -- no Pyodide, no rendering,
// instant. Pyodide (running vrmod via WASM) loads LAZILY, only the first time
// someone opens a car/track in the live 3D viewer.
//
// This is the RemoteGalleryProvider the personal Library (Repo A) left stubbed:
// same viewer, different source. There the bytes are your local Data folder;
// here they're same-origin files this Pages site hosts.

const LIBRARIAN_GLUE = `
from pathlib import Path
from vrmod import viewer
DATA = Path('/data'); DATA.mkdir(exist_ok=True)

def car_html(fname):
    # Community cars ship without race.res, so the viewer degrades gracefully
    # (body + own textures, no shared wheels) -- see viewer.build_shell_html.
    return viewer.build_shell_html(str(DATA / fname), title=Path(fname).stem, view_only=True)

def track_html(fname):
    return viewer.build_track_viewer_html(str(DATA / fname), view_only=True)

def unpack(zip_name, wanted):
    """Pull one asset out of a distribution pack, and return its filename.

    The hosted files are the packs as their authors uploaded them -- a zip with
    the .car or .tra inside, next to a readme and screenshots. That is the right
    thing for the download button and the wrong thing to hand a parser, which is
    a distinction the first version of this missed: the viewer got a zip and
    reported "bad archive magic" for every car in the catalogue.

    Matched case-insensitively on the basename, because a pack may file its
    asset under a folder (cars/exotic/luigi.car) and Windows-authored archives
    are casual about case.
    """
    import zipfile
    want = wanted.lower()
    with zipfile.ZipFile(DATA / zip_name) as z:
        names = [n for n in z.namelist() if not n.endswith('/')]
        hit = next((n for n in names if n.rsplit('/', 1)[-1].lower() == want), None)
        if hit is None:
            # A pack whose asset was renamed, or one holding several: fall back
            # to the first file with the extension we are looking for.
            ext = '.' + want.rsplit('.', 1)[-1]
            hit = next((n for n in names if n.lower().endswith(ext)), None)
        if hit is None:
            raise ValueError(f"{wanted} is not in {zip_name}")
        out = DATA / hit.rsplit('/', 1)[-1]
        out.write_bytes(z.read(hit))
        return out.name
`;

class RemoteGalleryProvider {
  constructor(manifestUrl = "manifest.json"){
    this.manifestUrl = manifestUrl;
    this.manifest = null;
    this.py = null;
    this.glue = {};
    this._pyLoading = null;
  }

  async init(){ /* nothing upfront -- browsing is Pyodide-free */ }

  async load(){
    this.manifest = await (await fetch(this.manifestUrl)).json();
  }

  list(){
    const m = this.manifest || {cars: [], tracks: []};
    // `author` is a PERSON and may be absent; `collection` is the folder the
    // community filed it under and is always there. They are different things,
    // so the byline prefers the person and falls back rather than conflating
    // them -- showing "frankscars" as an author is what this replaced.
    const by = i => i.author || i.collection || "unattributed";
    const cars = (m.cars || []).map(c => ({
      kind: "car", id: c.id, name: c.name,
      author: c.author, collection: c.collection,
      file: c.file, asset: c.asset, src: c.download || c.asset, thumb: c.thumbnail,
      verdict: c.provenance,
      sub: `${by(c)}${c.spec && c.spec.hp ? " · " + c.spec.hp + " hp" : ""}${c.spec && c.spec.top_speed ? " · " + c.spec.top_speed + " mph" : ""}`,
    }));
    const tracks = (m.tracks || []).map(t => ({
      kind: "track", id: t.id, name: t.name,
      author: t.author, collection: t.collection,
      file: t.file, asset: t.asset, src: t.download || t.asset, thumb: t.thumbnail,
      sub: `${by(t)}${t.miles ? " · " + t.miles + " mi" : ""}`,
    }));
    return {cars, tracks};
  }

  // Thumbnails are pre-baked PNGs -- just their URL, no render.
  thumbnail(item){ return item.thumb; }

  // Live 3D viewer: load Pyodide+vrmod once (lazily), fetch the hosted asset,
  // hand it to build_shell_html / build_track_viewer_html.
  async _ensurePyodide(onStatus){
    if (this.py) return;
    if (!this._pyLoading) this._pyLoading = (async () => {
      onStatus && onStatus("Loading the 3D viewer (first time only)…");
      this.py = await loadPyodide();
      const zip = await (await fetch("vrmod.zip")).arrayBuffer();
      this.py.unpackArchive(zip, "zip");
      this.py.runPython("import sys; sys.path.insert(0, '/home/pyodide')");
      await this.py.runPythonAsync(LIBRARIAN_GLUE);
      this.glue.car = this.py.globals.get("car_html");
      this.glue.track = this.py.globals.get("track_html");
      this.glue.unpack = this.py.globals.get("unpack");
    })();
    await this._pyLoading;
  }

  async viewerHTML(item, onStatus){
    await this._ensurePyodide(onStatus);
    // `src` over `asset`: a mod contributed to this repo has its bytes copied
    // into site/assets/ and is same-origin, while the 2,023 catalogued from the
    // corpus are hosted elsewhere and only have `download`. Fetching the hosted
    // one works only because that host sends CORS headers -- archive.org and
    // GitHub releases both send none, which is why neither could serve this
    // viewer however well they served the download button.
    if (!item.src) throw new Error("this mod is catalogued but not hosted yet");
    const res = await fetch(item.src);
    if (!res.ok) throw new Error(`${res.status} fetching ${item.src}`);
    const bytes = new Uint8Array(await res.arrayBuffer());
    this.py.FS.mkdirTree("/data");

    // Is this the asset itself, or the pack it shipped in? A mod contributed to
    // this repo is copied in bare; a catalogued one is hosted as the author's
    // original distribution zip. Sniff rather than infer from which field it
    // came from -- the bytes are the only thing that actually knows.
    const isZip = bytes[0] === 0x50 && bytes[1] === 0x4B;
    let name = item.file;
    if (isZip){
      const tmp = "_pack_" + item.file.replace(/[^\w.]+/g, "_") + ".zip";
      this.py.FS.writeFile("/data/" + tmp, bytes);
      name = this.glue.unpack(tmp, item.file);
    } else {
      this.py.FS.writeFile("/data/" + item.file, bytes);
    }
    return item.kind === "track" ? this.glue.track(name) : this.glue.car(name);
  }
}
