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
      <div><label>First published</label><input type="text" name="first_published" value="${esc(b.first_published)}"></div>
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
  if (res.read && res.kind === "candidates") {
    notice($("#lookup-msg"), `From the photo I read: <b>${esc(res.read.title || "")}</b>${res.read.author ? " by " + esc(res.read.author) : ""}`, "ok");
  }
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
      <span><b>${esc(c.title)}</b><small>${esc(c.authors || "Unknown author")}<br>${c.first_published ? "First published " + esc(c.first_published) : ""}${c.edition_count ? ` · ${c.edition_count} editions` : ""}</small></span>
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

function showExisting(existing) {
  const box = $("#existing-msg");
  if (!existing) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `<b>Already in inventory</b> as <span class="code">${esc(existing.inventory_code)}</span> — quantity ${existing.quantity}.
    <button class="small primary" id="add-copy">Add 1 more copy</button>
    <button class="small" id="view-existing">Open it</button>`;
  $("#add-copy").onclick = async () => {
    const b = await api(`/api/books/${existing.id}/quantity`, { json: { change: 1, note: "added copy" } });
    toast(`${b.inventory_code}: quantity is now ${b.quantity}`);
    showExisting(b);
  };
  $("#view-existing").onclick = () => openEditor(existing.id);
}

async function showBook(book, existing) {
  current = book;
  $("#result-card").hidden = false;
  $("#result-cover").innerHTML = coverHtml(book.cover_url, book.title);
  renderBookForm($("#result-form"), book);
  notice($("#ai-warnings"), "", "warn");
  $("#save-qty").value = 1;
  showExisting(existing);
  $("#result-card").scrollIntoView({ behavior: "smooth", block: "start" });
  if (existing) {
    // Re-use what was already written instead of paying for a new description.
    renderBookForm($("#result-form"), { ...book, ...existing });
    return;
  }
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
    if (!$("[name=first_published]", form).value && out.ai_first_published) {
      $("[name=first_published]", form).value = out.ai_first_published;
    }
    setDescription(form, out.description, false);
    notice($("#ai-warnings"), out.warnings.map(esc).join("<br>"), "warn");
  } catch (err) {
    setDescription(form, null, false);
    notice($("#ai-warnings"), esc(err.message), "err");
  } finally {
    busy(btn, false);
  }
}

$("#regen-btn").addEventListener("click", () => {
  const prev = $("#result-form textarea[name=description]").value;
  generate({ nudge: $("#nudge").value.trim() || null, previous: prev || null });
});

$("#save-btn").addEventListener("click", async () => {
  const data = readBookForm($("#result-form"));
  const payload = {
    ...data,
    work_key: current?.work_key || null,
    cover_url: current?.cover_url || null,
    source_summary: current?.source_summary || null,
    quantity: Math.max(1, parseInt($("#save-qty").value, 10) || 1),
  };
  if (!payload.title) return notice($("#ai-warnings"), "A title is required.", "err");
  const btn = $("#save-btn");
  busy(btn, true, "Saving…");
  try {
    const b = await api("/api/books", { json: payload });
    toast(`Saved as ${b.inventory_code}`);
    resetAdd();
    $("#q").value = "";
    $("#author").value = "";
    $("#q").focus();
  } catch (err) {
    if (err.status === 409 && err.detail?.existing) showExisting(err.detail.existing);
    else notice($("#ai-warnings"), esc(err.message), "err");
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
        <td class="title"><b>${esc(b.title)}</b><small>${esc(b.authors || "")}${b.first_published ? " · " + esc(b.first_published) : ""}</small></td>
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
    notice($("#edit-warnings"), out.warnings.map(esc).join("<br>"), "warn");
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
