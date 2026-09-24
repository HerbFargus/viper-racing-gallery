// Community gallery UI: loads a static manifest, renders a searchable grid of
// pre-baked thumbnails (no Pyodide to browse), and spins up the live vrmod viewer
// only when a card is opened.

const $ = id => document.getElementById(id);
function status(msg, cls){ const s = $("status"); s.textContent = msg; s.className = cls || ""; }
function overlay(show, msg, sub){
  if (show){ if (msg != null) $("overlay-msg").textContent = msg; if (sub != null) $("overlay-sub").textContent = sub; $("overlay").hidden = false; }
  else $("overlay").hidden = true;
}

let provider, library = {cars: [], tracks: []}, selCard = null;

async function boot(){
  try{
    provider = new RemoteGalleryProvider("manifest.json");
    await provider.init();
    await provider.load();
    library = provider.list();
    render();
    status(`${library.cars.length} cars · ${library.tracks.length} tracks`, "ok");
  }catch(e){ status("couldn't load the catalog:\n" + e.message, "err"); }
}

function matches(item, q){
  if (!q) return true;
  q = q.toLowerCase();
  // Search the person, the collection and the name: someone looking for
  // "valscars" and someone looking for "Val" should both find the same cars.
  return [item.name, item.author, item.collection]
    .some(v => (v || "").toLowerCase().includes(q));
}

function render(){
  const q = $("search").value.trim();
  const grid = $("grid"); grid.innerHTML = ""; selCard = null;
  const cars = library.cars.filter(i => matches(i, q));
  const tracks = library.tracks.filter(i => matches(i, q));
  if (!cars.length && !tracks.length){
    grid.innerHTML = '<div class="empty">Nothing matches. The catalog is built from the by-author folders by scripts/build_manifest.py.</div>';
    return;
  }
  if (cars.length){ section(grid, "Cars", cars.length); cars.forEach(i => card(grid, i)); }
  if (tracks.length){ section(grid, "Tracks", tracks.length); tracks.forEach(i => card(grid, i)); }
}

function section(grid, label, n){
  const h = document.createElement("div"); h.className = "sectionhdr"; h.textContent = `${label} (${n})`; grid.appendChild(h);
}

function card(grid, item){
  const el = document.createElement("div"); el.className = "card";
  const chip = item.verdict === "incomplete"
    ? ` <span class="vchip incomplete" title="References textures it doesn't ship — renders wrong once installed">⚠</span>` : "";
  el.innerHTML =
    `<div class="shot">${item.thumb ? `<img src="${item.thumb}" loading="lazy" alt="">` : "…"}</div>` +
    `<div class="meta"><div class="name">${item.name}${chip}</div><div class="sub">${item.sub}</div></div>`;
  if (item.alsoIn && item.alsoIn.length) el.title = "Also shipped in: " + item.alsoIn.join(", ");
  el.onclick = () => openItem(item, el);
  grid.appendChild(el);
}

function selectCard(el){ if (selCard) selCard.classList.remove("sel"); selCard = el; if (el) el.classList.add("sel"); }

async function openItem(item, el){
  selectCard(el);
  $("viewer-empty").hidden = true; $("frame").hidden = false; $("viewerbar").hidden = false;
  $("v-title").textContent = item.name;
  // `download` on an anchor only forces a filename for a SAME-ORIGIN href; a
  // cross-origin one is navigated to instead, which for these is the host's own
  // download page. That is the right behaviour anyway -- it gives the visitor
  // somewhere to see what they are getting.
  const dl = $("v-download");
  if (item.dl){
    dl.href = item.dl; dl.hidden = false;
    if (item.dlName) dl.setAttribute("download", item.dlName); else dl.removeAttribute("download");
  } else {
    dl.hidden = true;
  }
  $("v-stat").textContent = "building viewer…";
  try{
    const t0 = performance.now();
    const html = await provider.viewerHTML(item, sub => overlay(true, "Preparing the 3D viewer", sub));
    overlay(false);
    $("frame").srcdoc = html;
    $("v-stat").textContent = `${((performance.now() - t0) / 1000).toFixed(1)}s`;
  }catch(e){ overlay(false); $("v-stat").textContent = "failed: " + e.message; }
}

$("search").addEventListener("input", render);
boot();
