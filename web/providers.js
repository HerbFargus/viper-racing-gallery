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
      file: c.file, asset: c.asset, thumb: c.thumbnail,
      verdict: c.provenance,
      sub: `${by(c)}${c.spec && c.spec.hp ? " · " + c.spec.hp + " hp" : ""}${c.spec && c.spec.top_speed ? " · " + c.spec.top_speed + " mph" : ""}`,
    }));
    const tracks = (m.tracks || []).map(t => ({
      kind: "track", id: t.id, name: t.name,
      author: t.author, collection: t.collection,
      file: t.file, asset: t.asset, thumb: t.thumbnail,
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
    })();
    await this._pyLoading;
  }

  async viewerHTML(item, onStatus){
    await this._ensurePyodide(onStatus);
    const bytes = new Uint8Array(await (await fetch(item.asset)).arrayBuffer());
    this.py.FS.mkdirTree("/data");
    this.py.FS.writeFile("/data/" + item.file, bytes);
    return item.kind === "track" ? this.glue.track(item.file) : this.glue.car(item.file);
  }
}
