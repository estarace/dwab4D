"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let SETTINGS = { categories: [], age_groups: [] };

// ---------- api ----------
async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch(path, init);
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok) {
    const d = data?.detail;
    const err = new Error(typeof d === "string" ? d : d?.message || `Request failed (${r.status})`);
    err.status = r.status;
    err.detail = d;
    throw err;
  }
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 2200);
}

function notice(el, msg, kind = "err") {
  el.className = `notice ${kind}`;
  el.innerHTML = msg || "";
}

function busy(btn, on, label) {
  if (on) {
    btn.dataset.label = btn.innerHTML;
    btn.innerHTML = `<span class="spinner"></span> ${label || "Working…"}`;
    btn.disabled = true;
  } else {
    btn.innerHTML = btn.dataset.label || btn.innerHTML;
    btn.disabled = false;
  }
}

// ---------- tabs ----------
function show(view) {
  $$("nav.tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  if (view === "inventory") loadInventory();
  if (view === "settings") loadSettingsView();
}
$$("nav.tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

// ---------- shared book form ----------
function sentenceCount(t) {
  return t.trim() ? t.trim().split(/(?<=[.!?…])\s+/).filter(Boolean).length : 0;
}

function renderBookForm(container, b) {
  const cats = [...SETTINGS.categories];
  if (b.category && !cats.includes(b.category)) cats.unshift(b.category);
  const opt = (list, cur) => ['<option value=""></option>', ...list.map((v) => `<option ${v === cur ? "selected" : ""}>${esc(v)}</option>`)].join("");
  container.innerHTML = `
    <div class="grid3">
      <div class="span2"><label>Title</label><input type="text" name="title" value="${esc(b.title)}"></div>
      <div><label>Publication date</label><input type="text" name="published" value="${esc(b.published)}" placeholder="YYYY-MM-DD"></div>
      <div class="span2"><label>Author</label><input type="text" name="authors" value="${esc(b.authors)}"></div>
      <div><label>ISBN-13</label><input type="text" name="isbn13" value="${esc(b.isbn13)}"></div>
      <div><label>Category</label><select name="category">${opt(cats, b.category)}</select></div>
      <div><label>Subgenre</label><input type="text" name="subgenre" value="${esc(b.subgenre)}"></div>
      <div><label>Reader age</label><select name="age_group">${opt(SETTINGS.age_groups, b.age_group)}</select></div>
      <div class="span3">
        <label>Mystery description</label>
        <textarea name="description" rows="4">${esc(b.description)}</textarea>
        <div class="counter"></div>
      </div>
      <div class="span2"><label>Notes <span class="hint">(private — not printed)</span></label><input type="text" name="notes" value="${esc(b.notes)}"></div>
      <div><label>Pages</label><input type="number" name="page_count" value="${esc(b.page_count)}"></div>
    </div>`;
  const ta = $("textarea", container);
  const counter = $(".counter", container);
  const update = () => {
    const n = sentenceCount(ta.value);
    const words = ta.value.trim() ? ta.value.trim().split(/\s+/).length : 0;
    counter.textContent = `${n} sentence${n === 1 ? "" : "s"} · ${words} words`;
    counter.classList.toggle("over", n > 4);
  };
  ta.addEventListener("input", update);
  update();
}

function readBookForm(container) {
  const out = {};
  $$("[name]", container).forEach((el) => {
    let v = el.value.trim();
    if (el.name === "page_count") v = v ? parseInt(v, 10) : null;
    out[el.name] = v === "" ? null : v;
  });
  return out;
}

function setDescription(container, text, generating) {
  const ta = $("textarea[name=description]", container);
  ta.value = text ?? ta.value;
  ta.placeholder = generating ? "Writing a mystery description…" : "";
  ta.disabled = !!generating;
  ta.dispatchEvent(new Event("input"));
}

function coverHtml(url, title) {
  const ph = `<div class="nocover">${esc((title || "?").slice(0, 1))}</div>`;
  return url ? `<img src="${esc(url)}" alt="" onerror="this.outerHTML=this.dataset.ph" data-ph="${esc(ph)}">` : ph;
}

// ---------- add flow ----------
let current = null; // the book being added (lookup data + extras for AI)

function resetAdd() {
  $("#candidates-card").hidden = true;
  $("#result-card").hidden = true;
  notice($("#lookup-msg"), "");
}

async function handleLookupResult(res) {
  const notes = [];
  if (res.read && res.kind === "candidates") {
    notes.push(`From the photo I read: <b>${esc(res.read.title || "")}</b>${res.read.author ? " by " + esc(res.read.author) : ""}`);
  }
  if (res.others) {
    notes.push(`The photo shows ${res.others} more book${res.others === 1 ? "" : "s"} — use the <b>Bulk Add</b> tab to add several at once.`);
  }
  if (notes.length) notice($("#lookup-msg"), notes.join("<br>"), "ok");
  if (res.kind === "candidates") return showCandidates(res.candidates);
  return showBook(res.book, res.existing);
}

$("#lookup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = $("#q").value.trim();
  if (!query) return;
  resetAdd();
  const btn = $("#lookup-btn");
  busy(btn, true, "Looking up…");
  try {
    await handleLookupResult(await api("/api/lookup", { json: { query, author: $("#author").value.trim() || null } }));
  } catch (err) {
    notice($("#lookup-msg"), esc(err.message));
  } finally {
    busy(btn, false);
  }
});

function showCandidates(list) {
  const card = $("#candidates-card");
  const box = $("#candidates");
  if (!list.length) {
    notice($("#lookup-msg"), "No books found with that title. Check the spelling, add the author, or try the ISBN.", "warn");
    return;
  }
  box.innerHTML = list.map((c, i) => `
    <button class="cand" data-i="${i}">
      ${c.cover_url ? `<img src="${esc(c.cover_url)}" alt="" loading="lazy" onerror="this.outerHTML='<div class=nocover></div>'">` : '<div class="nocover"></div>'}
      <span><b>${esc(c.title)}</b><small>${esc(c.authors || "Unknown author")}<br>${c.original_year ? esc(c.original_year) : ""}${c.edition_count ? ` · ${c.edition_count} editions` : ""}</small></span>
    </button>`).join("");
  card.hidden = false;
  $$(".cand", box).forEach((el) =>
    el.addEventListener("click", async () => {
      const c = list[+el.dataset.i];
      el.disabled = true;
      try {
        const res = await api("/api/identify", { json: { work_key: c.work_key, isbn13: c.isbn13, title: c.title, author: c.authors } });
        card.hidden = true;
        showBook({ ...res.book, cover_url: res.book.cover_url || c.cover_url }, res.existing);
      } catch (err) {
        notice($("#lookup-msg"), esc(err.message));
      } finally {
        el.disabled = false;
      }
    })
  );
}

let existingId = null; // set when the looked-up book is already in inventory

// The book is already in inventory: a copy has been added; offer undo / another.
function showExisting(book, added) {
  const box = $("#existing-msg");
  if (!book) { box.hidden = true; return; }
  box.hidden = false;
  box.className = "notice ok";
  box.innerHTML = `<b>Already in inventory</b> as <span class="code">${esc(book.inventory_code)}</span>.
    ${added ? `Added ${added} cop${added === 1 ? "y" : "ies"} —` : ""} now <b>${book.quantity}</b> in stock.
    ${added ? '<button class="small" id="undo-copy">Undo</button>' : ""}
    <button class="small" id="add-copy">+1 more</button>`;
  const change = async (n, note) => {
    const b = await api(`/api/books/${book.id}/quantity`, { json: { change: n, note } });
    showExisting(b, added + n);
  };
  $("#add-copy").onclick = () => change(1, "added copy");
  if (added) $("#undo-copy").onclick = () => change(-added, "undo");
}

function setSaveMode(isExisting) {
  $("#save-btn").textContent = isExisting ? "Save changes" : "Save to inventory";
  $("#save-qty").hidden = $("#save-qty-label").hidden = isExisting;
}

async function showBook(book, existing) {
  current = book;
  existingId = existing ? existing.id : null;
  $("#result-card").hidden = false;
  $("#result-cover").innerHTML = coverHtml(book.cover_url || existing?.cover_url, book.title);
  notice($("#ai-warnings"), "", "warn");
  $("#save-qty").value = 1;
  setSaveMode(!!existing);
  $("#result-card").scrollIntoView({ behavior: "smooth", block: "start" });
  if (existing) {
    // Entering a book we already have counts as another copy.
    renderBookForm($("#result-form"), { ...book, ...existing });
    try {
      const b = await api(`/api/books/${existing.id}/quantity`, { json: { change: 1, note: "entered again" } });
      showExisting(b, 1);
      toast(`${b.inventory_code}: now ${b.quantity} in stock`);
    } catch (err) {
      showExisting(existing, 0);
      notice($("#ai-warnings"), esc(err.message), "err");
    }
    return;
  }
  showExisting(null);
  renderBookForm($("#result-form"), book);
  await generate();
}

async function generate({ nudge = null, previous = null } = {}) {
  const form = $("#result-form");
  const btn = $("#regen-btn");
  const formNow = readBookForm(form);
  setDescription(form, previous ? null : "", true);
  busy(btn, true, "Writing…");
  try {
    const out = await api("/api/generate", { json: { book: { ...current, ...formNow }, nudge, previous } });
    const sel = (name, val) => { const el = $(`[name=${name}]`, form); if (val && el) el.value = val; };
    sel("category", out.category);
    sel("age_group", out.age_group);
    $("[name=subgenre]", form).value = out.subgenre || "";
    setDescription(form, out.description, false);
    showWarnings($("#ai-warnings"), out, form);
  } catch (err) {
    setDescription(form, null, false);
    notice($("#ai-warnings"), esc(err.message), "err");
  } finally {
    busy(btn, false);
  }
}

// Show AI warnings (e.g. a teaser that may give the book away).
function showWarnings(box, out) {
  notice(box, out.warnings.map(esc).join("<br>"), "warn");
}

$("#regen-btn").addEventListener("click", () => {
  const prev = $("#result-form textarea[name=description]").value;
  generate({ nudge: $("#nudge").value.trim() || null, previous: prev || null });
});

function finishAdd() {
  resetAdd();
  existingId = null;
  $("#q").value = "";
  $("#author").value = "";
  $("#q").focus();
}

$("#save-btn").addEventListener("click", async () => {
  const data = readBookForm($("#result-form"));
  if (!data.title) return notice($("#ai-warnings"), "A title is required.", "err");
  const btn = $("#save-btn");
  busy(btn, true, "Saving…");
  try {
    if (existingId) {
      const b = await api(`/api/books/${existingId}`, { method: "PUT", json: data });
      toast(`${b.inventory_code} updated`);
    } else {
      const b = await api("/api/books", { json: {
        ...data,
        work_key: current?.work_key || null,
        cover_url: current?.cover_url || null,
        source_summary: current?.source_summary || null,
        quantity: Math.max(1, parseInt($("#save-qty").value, 10) || 1),
      } });
      toast(b.merged ? `Already in inventory — ${b.inventory_code} now has ${b.quantity}` : `Saved as ${b.inventory_code}`);
    }
    finishAdd();
  } catch (err) {
    notice($("#ai-warnings"), esc(err.message), "err");
  } finally {
    busy(btn, false);
  }
});

// ---------- photo ----------
$("#photo-btn").addEventListener("click", () => $("#photo-input").click());
$("#photo-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  resetAdd();
  const btn = $("#photo-btn");
  busy(btn, true, "Reading photo…");
  try {
    const blob = await shrinkImage(file, 1600);
    const fd = new FormData();
    fd.append("file", blob, "photo.jpg");
    await handleLookupResult(await api("/api/photo", { method: "POST", body: fd }));
  } catch (err) {
    notice($("#lookup-msg"), esc(err.message));
  } finally {
    busy(btn, false);
  }
});

// Downscale big camera photos before upload (keeps requests small and fast).
function shrinkImage(file, maxSide) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * scale);
      c.height = Math.round(img.height * scale);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      c.toBlob((b) => (b ? resolve(b) : reject(new Error("Couldn't read that image."))), "image/jpeg", 0.88);
      URL.revokeObjectURL(img.src);
    };
    img.onerror = () => reject(new Error("Couldn't read that image file."));
    img.src = URL.createObjectURL(file);
  });
}

// ---------- webcam barcode ----------
let scanControls = null;
$("#scan-btn").addEventListener("click", async () => {
  if (!window.ZXingBrowser) {
    return notice($("#lookup-msg"), "The barcode scanner library didn't load (no internet?). Type the ISBN or upload a photo instead.");
  }
  if (!window.isSecureContext) {
    return notice($("#lookup-msg"), "The browser only allows the webcam on <b>localhost</b> or an <b>https://</b> address. Open the app at http://localhost:8000 on this machine, or use “Upload photo”.");
  }
  resetAdd();
  $("#scanner").hidden = false;
  try {
    const reader = new ZXingBrowser.BrowserMultiFormatReader();
    scanControls = await reader.decodeFromVideoDevice(undefined, $("#scan-video"), (result) => {
      if (!result) return;
      const text = result.getText();
      const digits = text.replace(/\D/g, "");
      if (!/^97[89]\d{10}/.test(digits) && digits.length !== 10) return; // ignore non-ISBN codes
      stopScan();
      $("#q").value = digits.slice(0, 13);
      $("#lookup-form").requestSubmit();
    });
  } catch (err) {
    stopScan();
    notice($("#lookup-msg"), "Couldn't start the webcam: " + esc(err.message || err));
  }
});
function stopScan() {
  scanControls?.stop();
  scanControls = null;
  $("#scanner").hidden = true;
}
$("#scan-stop").addEventListener("click", stopScan);

// ---------- bulk add ----------
const bulk = { items: [], seq: 0, paused: false, running: 0, MAX: 3 };

function bulkMsg(html, kind = "err") { notice($("#bulk-msg"), html, kind); }

function queueItems(list, source) {
  for (const it of list) {
    bulk.items.push({ id: ++bulk.seq, source, status: "waiting", quantity: 1, ...it });
  }
  $("#bulk-queue-card").hidden = false;
  renderBulk();
  pumpBulk();
}

async function parseAndQueue(text, source) {
  try {
    const { items } = await api("/api/bulk/parse", { json: { text } });
    queueItems(items, source);
    bulkMsg(`Added ${items.length} entr${items.length === 1 ? "y" : "ies"} to the queue.`, "ok");
    return true;
  } catch (err) {
    bulkMsg(esc(err.message));
    return false;
  }
}

$("#bulk-add-text").addEventListener("click", async () => {
  const text = $("#bulk-text").value;
  if (!text.trim()) return bulkMsg("Paste some titles or ISBNs first.", "warn");
  if (await parseAndQueue(text, "list")) $("#bulk-text").value = "";
});
$("#bulk-file-btn").addEventListener("click", () => $("#bulk-file").click());
$("#bulk-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (file) await parseAndQueue(await file.text(), file.name);
});

// photos: each photo becomes a "photo" job that expands into one row per book found
function queuePhotos(files) {
  const imgs = [...files].filter((f) => f.type.startsWith("image/"));
  if (!imgs.length) return bulkMsg("Those files aren't images.", "warn");
  queueItems(imgs.map((f) => ({ kind: "photo", file: f, query: f.name })), "photo");
  bulkMsg(`Reading ${imgs.length} photo${imgs.length === 1 ? "" : "s"}…`, "ok");
}
$("#bulk-photo-btn").addEventListener("click", () => $("#bulk-photos").click());
$("#bulk-photos").addEventListener("change", (e) => { queuePhotos(e.target.files); e.target.value = ""; });
const drop = $("#bulk-drop");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); queuePhotos(e.dataTransfer.files); });

$("#bulk-pause").addEventListener("click", () => {
  bulk.paused = !bulk.paused;
  $("#bulk-pause").textContent = bulk.paused ? "Resume" : "Pause";
  if (!bulk.paused) pumpBulk();
  renderBulk();
});
$("#bulk-clear").addEventListener("click", () => {
  bulk.items = bulk.items.filter((i) => !["added", "incremented", "undone", "skipped"].includes(i.status));
  renderBulk();
});

function pumpBulk() {
  while (!bulk.paused && bulk.running < bulk.MAX) {
    const item = bulk.items.find((i) => i.status === "waiting");
    if (!item) break;
    bulk.running++;
    item.status = "working";
    renderBulk();
    (item.kind === "photo" ? runPhoto(item) : runItem(item)).finally(() => {
      bulk.running--;
      renderBulk();
      pumpBulk();
    });
  }
}

async function runPhoto(item) {
  try {
    const blob = await shrinkImage(item.file, 1600);
    const fd = new FormData();
    fd.append("file", blob, "photo.jpg");
    const { books } = await api("/api/photo/read", { method: "POST", body: fd });
    if (!books.length) {
      Object.assign(item, { status: "not_found", message: "No readable book in this photo." });
      return;
    }
    const rows = books.map((b) => ({
      id: ++bulk.seq, source: "photo", status: "waiting", quantity: 1,
      query: b.isbn || b.title, author: b.isbn ? null : b.author,
      label: `${b.title || b.isbn}${b.author ? " — " + b.author : ""}`, photo: item.file.name,
    }));
    bulk.items.splice(bulk.items.indexOf(item), 1, ...rows);
  } catch (err) {
    Object.assign(item, { status: "error", message: err.message });
  }
}

async function runItem(item) {
  try {
    const res = await api("/api/bulk/item", { json: {
      query: item.query, author: item.author || null, quantity: item.quantity,
      work_key: item.work_key || null, isbn13: item.isbn13 || null,
    } });
    Object.assign(item, res, { status: res.status });
  } catch (err) {
    Object.assign(item, { status: "error", message: err.message });
  }
}

function bulkStatusHtml(it) {
  const b = it.book;
  switch (it.status) {
    case "waiting": return `<span class="st dim">${bulk.paused ? "Paused" : "Waiting"}</span>`;
    case "working": return `<span class="st dim"><span class="spinner"></span> ${it.kind === "photo" ? "Reading photo…" : "Looking up & writing…"}</span>`;
    case "added": {
      const warn = (it.warnings || []).map((w) => `<small>${esc(w)}</small>`).join("");
      return `<span class="st ok">Added as ${esc(b.inventory_code)}${warn}</span>`;
    }
    case "incremented":
      return `<span class="st inc">Already had it — +${it.change} → ${b.quantity} in stock</span>`;
    case "choose":
      return `<span class="st need">Which book is it?</span>
        <select data-role="pick">${it.candidates.map((c, i) =>
          `<option value="${i}">${esc(c.title)} — ${esc(c.authors || "?")}${c.original_year ? " (" + esc(c.original_year) + ")" : ""}</option>`).join("")}</select>
        <span><button class="small primary" data-act="pick">Add this one</button> <button class="small" data-act="skip">Skip</button></span>`;
    case "not_found":
    case "error":
      return `<span class="st bad">${esc(it.message || "Not found")}</span>
        ${it.kind === "photo" ? "" : `<input type="text" data-role="query" value="${esc(it.query)}">`}
        <span><button class="small" data-act="retry">Retry</button> <button class="small" data-act="skip">Skip</button></span>`;
    case "undone": return '<span class="st dim">Undone</span>';
    case "skipped": return '<span class="st dim">Skipped</span>';
  }
  return "";
}

function renderBulk() {
  const body = $("#bulk-body");
  body.innerHTML = bulk.items.map((it, n) => {
    const b = it.book;
    const entry = it.kind === "photo"
      ? `Photo: ${esc(it.file.name)}`
      : `${esc(it.label || it.query)}${!it.label && it.author ? " — " + esc(it.author) : ""}${it.quantity > 1 ? ` ×${it.quantity}` : ""}${it.photo ? `<br><small>from ${esc(it.photo)}</small>` : ""}`;
    const bookCell = b && ["added", "incremented"].includes(it.status)
      ? `<b>${esc(b.title)}</b><small>${esc(b.authors || "")}${b.published ? " · " + esc(b.published) : ""}</small>
         <small>${esc(b.category || "")}${b.age_group ? " · " + esc(b.age_group) : ""}</small>` : "";
    const actions = ["added", "incremented"].includes(it.status)
      ? `<button class="small" data-act="open">Open</button> <button class="small" data-act="undo">Undo</button>` : "";
    return `<tr data-id="${it.id}">
      <td>${n + 1}</td>
      <td class="entry">${entry}</td>
      <td>${b?.cover_url && bookCell ? `<img src="${esc(b.cover_url)}" alt="" loading="lazy" onerror="this.remove()">` : ""}</td>
      <td class="title">${bookCell}</td>
      <td><div class="status">${bulkStatusHtml(it)}</div></td>
      <td>${actions}</td>
    </tr>`;
  }).join("");
  const count = (st) => bulk.items.filter((i) => i.status === st).length;
  const parts = [
    [count("added"), "added"], [count("incremented"), "already in stock"],
    [count("waiting") + count("working"), "to go"], [count("choose"), "need a choice"],
    [count("not_found") + count("error"), "problem"],
  ].filter(([n]) => n).map(([n, t]) => `${n} ${t === "problem" && n > 1 ? "problems" : t}`);
  $("#bulk-summary").textContent = parts.join(" · ");
}

$("#bulk-body").addEventListener("click", async (e) => {
  const act = e.target.dataset.act;
  if (!act) return;
  const tr = e.target.closest("tr");
  const it = bulk.items.find((i) => i.id === +tr.dataset.id);
  if (!it) return;
  try {
    if (act === "open") return openEditor(it.book.id);
    if (act === "undo") {
      if (it.status === "added") await api(`/api/books/${it.book.id}`, { method: "DELETE" });
      else await api(`/api/books/${it.book.id}/quantity`, { json: { change: -it.change, note: "undo bulk entry" } });
      it.status = "undone";
    } else if (act === "pick") {
      const c = it.candidates[+$("select[data-role=pick]", tr).value];
      Object.assign(it, { work_key: c.work_key, isbn13: c.isbn13, query: c.title, author: c.authors, label: `${c.title} — ${c.authors || "?"}`, status: "waiting" });
    } else if (act === "retry") {
      const q = $("input[data-role=query]", tr);
      if (q) Object.assign(it, { query: q.value.trim(), label: null, work_key: null, isbn13: null });
      it.status = "waiting";
    } else if (act === "skip") {
      it.status = "skipped";
    }
  } catch (err) {
    toast(err.message);
  }
  renderBulk();
  pumpBulk();
});

window.addEventListener("beforeunload", (e) => {
  if (bulk.items.some((i) => ["waiting", "working"].includes(i.status))) e.preventDefault();
});

// ---------- inventory ----------
const selected = new Set();
let invRows = [];

async function loadInventory() {
  const sort = $("#inv-sort").value;
  const q = $("#inv-search").value.trim();
  invRows = await api(`/api/books?sort=${sort}&q=${encodeURIComponent(q)}`);
  const body = $("#inv-body");
  if (!invRows.length) {
    body.innerHTML = `<tr><td colspan="9" class="empty">${q ? "No matches." : "No books yet — add one on the “Add a Book” tab."}</td></tr>`;
  } else {
    body.innerHTML = invRows.map((b) => `
      <tr data-id="${b.id}">
        <td><input type="checkbox" class="pick" ${selected.has(b.id) ? "checked" : ""}></td>
        <td class="code">${esc(b.inventory_code)}</td>
        <td>${b.cover_url ? `<img src="${esc(b.cover_url)}" alt="" loading="lazy" onerror="this.remove()">` : ""}</td>
        <td class="title"><b>${esc(b.title)}</b><small>${esc(b.authors || "")}${b.published ? " · " + esc(b.published) : ""}</small></td>
        <td>${esc(b.category || "")}${b.subgenre ? `<br><small>${esc(b.subgenre)}</small>` : ""}</td>
        <td>${b.age_group ? `<span class="badge">${esc(b.age_group)}</span>` : ""}</td>
        <td><span class="qty"><button class="small" data-q="-1" title="Remove a copy">−</button><span>${b.quantity}</span><button class="small" data-q="1" title="Add a copy">+</button></span></td>
        <td><small>${new Date(b.created_at).toLocaleDateString()}</small></td>
        <td><button class="small edit">Edit</button></td>
      </tr>`).join("");
  }
  updateSelected();
}

function updateSelected() {
  const visible = invRows.filter((b) => selected.has(b.id)).length;
  $("#inv-selected").textContent = selected.size ? `${selected.size} selected` : "";
  $("#delete-btn").disabled = selected.size === 0;
  $("#inv-all").checked = invRows.length > 0 && visible === invRows.length;
}

$("#inv-body").addEventListener("click", async (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  const id = +tr.dataset.id;
  if (e.target.classList.contains("pick")) {
    e.target.checked ? selected.add(id) : selected.delete(id);
    return updateSelected();
  }
  if (e.target.dataset.q) {
    const change = +e.target.dataset.q;
    const b = await api(`/api/books/${id}/quantity`, { json: { change, note: change > 0 ? "added copy" : "removed copy" } });
    $(".qty span", tr).textContent = b.quantity;
    return;
  }
  if (e.target.classList.contains("edit")) openEditor(id);
});

$("#inv-all").addEventListener("change", (e) => {
  invRows.forEach((b) => (e.target.checked ? selected.add(b.id) : selected.delete(b.id)));
  $$(".pick").forEach((c) => (c.checked = e.target.checked));
  updateSelected();
});
$("#inv-sort").addEventListener("change", loadInventory);
let searchT;
$("#inv-search").addEventListener("input", () => { clearTimeout(searchT); searchT = setTimeout(loadInventory, 250); });

function targetIds() {
  return selected.size ? [...selected] : invRows.map((b) => b.id);
}
$("#export-btn").addEventListener("click", () => {
  const ids = targetIds();
  if (!ids.length) return toast("Nothing to export");
  window.location = `/api/export.csv?sort=${$("#inv-sort").value}&ids=${ids.join(",")}`;
});
// ----- bulk delete (checked rows only, always confirmed) -----
let pendingDelete = [];
$("#delete-btn").addEventListener("click", async () => {
  if (!selected.size) return;
  try {
    pendingDelete = await api(`/api/books?ids=${[...selected].join(",")}`);
  } catch (err) {
    return toast(err.message);
  }
  if (!pendingDelete.length) { selected.clear(); updateSelected(); return toast("Those books are already gone"); }
  const n = pendingDelete.length;
  const copies = pendingDelete.reduce((t, b) => t + b.quantity, 0);
  $("#del-title").textContent = `Delete ${n} book${n === 1 ? "" : "s"}?`;
  $("#del-list").innerHTML = pendingDelete.map((b) =>
    `<li><b>${esc(b.title)}</b> <span class="code">${esc(b.inventory_code)}</span> — ${b.quantity} in stock</li>`).join("");
  $("#del-totals").textContent = `${n} book${n === 1 ? "" : "s"}, ${copies} cop${copies === 1 ? "y" : "ies"} in stock.`;
  $("#del-confirm").textContent = `Delete ${n} book${n === 1 ? "" : "s"}`;
  $("#del-dlg").showModal();
  $("#del-cancel").focus(); // safe default if Enter is pressed
});
$("#del-cancel").addEventListener("click", () => $("#del-dlg").close());
$("#del-export").addEventListener("click", () => {
  window.location = `/api/export.csv?ids=${pendingDelete.map((b) => b.id).join(",")}`;
});
$("#del-confirm").addEventListener("click", async () => {
  const btn = $("#del-confirm");
  busy(btn, true, "Deleting…");
  try {
    const { deleted } = await api("/api/books/delete", { json: { ids: pendingDelete.map((b) => b.id) } });
    pendingDelete.forEach((b) => selected.delete(b.id));
    $("#del-dlg").close();
    toast(`Deleted ${deleted} book${deleted === 1 ? "" : "s"}`);
    loadInventory();
  } catch (err) {
    toast(err.message);
  } finally {
    busy(btn, false);
  }
});

$("#labels-btn").addEventListener("click", () => {
  const ids = targetIds();
  if (!ids.length) return toast("Nothing to print");
  window.open(`/labels?ids=${ids.join(",")}&sort=${$("#inv-sort").value}`, "_blank");
});

// ---------- edit dialog ----------
let editing = null;
async function openEditor(id) {
  const b = await api(`/api/books/${id}`);
  editing = b;
  $("#edit-title").innerHTML = `Edit <span class="code">${esc(b.inventory_code)}</span>`;
  renderBookForm($("#edit-form"), b);
  notice($("#edit-warnings"), "", "warn");
  $("#edit-history").innerHTML = `<b>Stock history</b> (current quantity ${b.quantity})<ul>${b.history.map((h) =>
    `<li>${new Date(h.created_at).toLocaleString()} — ${h.change > 0 ? "+" : ""}${h.change}${h.note ? " (" + esc(h.note) + ")" : ""}</li>`).join("")}</ul>`;
  $("#edit-dlg").showModal();
}
$("#edit-cancel").addEventListener("click", () => $("#edit-dlg").close());
$("#edit-save").addEventListener("click", async () => {
  const data = readBookForm($("#edit-form"));
  if (!data.title) return notice($("#edit-warnings"), "A title is required.", "err");
  await api(`/api/books/${editing.id}`, { method: "PUT", json: data });
  $("#edit-dlg").close();
  toast("Saved");
  if (!$("#view-inventory").hidden) loadInventory();
});
$("#edit-regen").addEventListener("click", async () => {
  const form = $("#edit-form");
  const btn = $("#edit-regen");
  const prev = $("textarea[name=description]", form).value;
  busy(btn, true, "Writing…");
  setDescription(form, null, true);
  try {
    const out = await api("/api/generate", { json: { book: { ...editing, ...readBookForm(form) }, previous: prev || null } });
    setDescription(form, out.description, false);
    showWarnings($("#edit-warnings"), out, form);
  } catch (err) {
    setDescription(form, null, false);
    notice($("#edit-warnings"), esc(err.message), "err");
  } finally {
    busy(btn, false);
  }
});
$("#edit-delete").addEventListener("click", async () => {
  if (!confirm(`Delete “${editing.title}” (${editing.inventory_code}) and its history? This can't be undone.`)) return;
  await api(`/api/books/${editing.id}`, { method: "DELETE" });
  selected.delete(editing.id);
  $("#edit-dlg").close();
  toast("Deleted");
  loadInventory();
});

// ---------- settings ----------
function loadSettingsView() {
  $("#cat-list").value = SETTINGS.categories.join("\n");
  $("#age-list").innerHTML = SETTINGS.age_groups.map((a) => `<span class="badge">${esc(a)}</span>`).join(" ");
}
$("#cat-save").addEventListener("click", async () => {
  const categories = $("#cat-list").value.split("\n").map((s) => s.trim()).filter(Boolean);
  try {
    SETTINGS = await api("/api/settings", { method: "PUT", json: { categories } });
    loadSettingsView();
    toast("Categories saved");
  } catch (err) {
    toast(err.message);
  }
});

// ---------- boot ----------
api("/api/settings").then((s) => (SETTINGS = s));
