/* Magpie web UI: Visit / Family / Quilt. Plain JS, no build step. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d = null) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch(path, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
  return data;
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), err ? 5000 : 2600);
}

const S = { state: null, session: null, busy: false, items: [], selectedItem: null };
const who = () => $("#contributor").value || "Priya";

/* ============================================================ init */

async function init() {
  S.state = await api("/api/state");
  const p = S.state.persona;
  $$("#pname, .pname").forEach((el) => (el.textContent = p.preferred_name || "Rose"));
  $("#persona").innerHTML = `${esc(p.name)} · born ${esc(String(p.born || "").slice(0, 4))} <span class="chip">${esc(p.label || "Demo persona")}</span>`;
  $("#provider-line").textContent =
    `Session agent: ${S.state.models.session}${S.state.live_grounding ? " · every line checked against their confirmed stories" : ""}`;

  const sel = $("#contributor");
  const family = S.state.people.filter((x) => !["husband", "mother", "mother-in-law"].includes(x.relation));
  sel.innerHTML = family.map((x) => `<option value="${esc(x.id)}">${esc(x.name.split(" ")[0])} (${esc(x.relation)})</option>`).join("");
  sel.value = store.get("magpie.who", "priya");
  sel.onchange = () => store.set("magpie.who", sel.value);

  $("#speak-toggle").checked = store.get("magpie.speak", "1") === "1";
  $("#speak-toggle").onchange = (e) => store.set("magpie.speak", e.target.checked ? "1" : "0");
  $("#trace-toggle").checked = store.get("magpie.trace", "1") === "1";
  document.body.classList.toggle("no-trace", !$("#trace-toggle").checked);
  $("#trace-toggle").onchange = (e) => {
    store.set("magpie.trace", e.target.checked ? "1" : "0");
    document.body.classList.toggle("no-trace", !e.target.checked);
  };

  $$(".tab").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  $$(".sub").forEach((b) => (b.onclick = () => showSub(b.dataset.sub)));
  bindVisit();
  bindFamily();
  bindQuilt();
  refreshReviewCount();
  if ("speechSynthesis" in window) speechSynthesis.getVoices();
}

function showTab(name) {
  $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "family") showSub($(".sub.active").dataset.sub);
  if (name === "quilt") loadQuilt();
}

function showSub(name) {
  $$(".sub").forEach((b) => b.classList.toggle("active", b.dataset.sub === name));
  $$(".subpanel").forEach((p) => p.classList.toggle("active", p.id === `sub-${name}`));
  ({ keepsakes: loadItems, add: loadAdd, review: loadReview, archive: loadArchive, visits: loadVisits, cards: loadCards }[name] || (() => {}))();
}

/* ============================================================ voice in/out */

function pickVoice() {
  const voices = speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en"));
  const prefer = ["Samantha", "Serena", "Google UK English Female", "Karen", "Moira", "Tessa", "Microsoft Libby", "Microsoft Sonia", "Microsoft Aria"];
  for (const n of prefer) { const v = voices.find((x) => x.name.includes(n)); if (v) return v; }
  return voices[0];
}

function speak(text) {
  return new Promise((resolve) => {
    if (!$("#speak-toggle").checked || !("speechSynthesis" in window)) return setTimeout(resolve, 400);
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 0.88; u.pitch = 1.0;
    const v = pickVoice(); if (v) u.voice = v;
    const guard = setTimeout(resolve, 1500 + text.length * 90);
    u.onend = u.onerror = () => { clearTimeout(guard); resolve(); };
    speechSynthesis.speak(u);
  });
}

function playClip(url) {
  return new Promise((resolve) => {
    const a = $("#clip");
    a.src = url;
    const done = () => { a.onended = a.onerror = null; resolve(); };
    a.onended = a.onerror = done;
    a.play().catch(done);
  });
}

/** Record from the mic; returns a controller with stop() -> Promise<Blob>. */
async function startRecording() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const rec = new MediaRecorder(stream);
  const chunks = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  rec.start();
  return {
    stop: () => new Promise((resolve) => {
      rec.onstop = () => { stream.getTracks().forEach((t) => t.stop()); resolve(new Blob(chunks, { type: rec.mimeType || "audio/webm" })); };
      rec.stop();
    }),
  };
}

/* ============================================================ visit */

const MODE_LABEL = { ask: "asking", hint: "a gentle clue", tell: "telling the story", reflect: "listening back", switch: "something else", close: "closing gently", voice: "family voice" };

function bindVisit() {
  $$("[data-start]").forEach((b) => (b.onclick = () => startVisit(b.dataset.start)));
  $("#reply-form").onsubmit = (e) => { e.preventDefault(); sendReply($("#reply-text").value); };
  $("#btn-switch").onclick = () => sessionAction("switch");
  $("#btn-end").onclick = () => sessionAction("end");
  $("#btn-again").onclick = () => { $("#visit-done").classList.add("hidden"); $("#visit-start").classList.remove("hidden"); };
  $("#btn-camera").onclick = openCamera;
  $("#btn-camera-close").onclick = closeCamera;
  $("#btn-snap").onclick = snapKeepsake;

  let rec = null;
  $("#mic").onclick = async () => {
    const btn = $("#mic");
    if (!rec) {
      try { rec = await startRecording(); } catch { return toast("Microphone not available. Type their words instead.", true); }
      btn.classList.add("recording"); btn.querySelector("span").textContent = "Done";
      return;
    }
    const blob = await rec.stop(); rec = null;
    btn.classList.remove("recording"); btn.querySelector("span").textContent = "Speak";
    const fd = new FormData(); fd.append("file", blob, "reply.webm");
    setThinking(true);
    try {
      const { text } = await api("/api/transcribe", { method: "POST", body: fd });
      if (text) sendReply(text); else { setThinking(false); toast("I didn't catch that. Try again or type it."); }
    } catch (e) { setThinking(false); toast(e.message, true); }
  };
}

function setThinking(on) {
  S.busy = on;
  $("#thinking").classList.toggle("hidden", !on);
  $$("#reply-form button, .controls button").forEach((b) => (b.disabled = on));
}

function showKeepsake(item) {
  if (!item) return;
  const img = $("#keepsake-img");
  img.src = item.image_url || "data:image/svg+xml," + encodeURIComponent(`<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><rect width='10' height='10' fill='#ece2cf'/></svg>`);
  img.alt = item.title;
  $("#keepsake-title").textContent = item.title;
  const lab = $("#keepsake-label");
  lab.textContent = item.label;
  lab.className = "chip" + (/AI-generated/.test(item.label) ? " ai" : "");
}

function groundChip(g) {
  if (!g) return "";
  if (g.fallback) return `<span class="mode ground">used the family's own words</span>`;
  if (g.invented === null || g.invented === undefined) return `<span class="mode ground">not checked</span>`;
  const caught = g.caught ? ` · ${g.caught} draft${g.caught > 1 ? "s" : ""} corrected` : "";
  return `<span class="mode ground">✓ ${g.checked} fact${g.checked === 1 ? "" : "s"} traced${caught}</span>`;
}

function addLine(t) {
  const c = $("#conversation");
  const div = document.createElement("div");
  if (t.role === "rose") {
    div.className = "line rose";
    div.textContent = t.text;
  } else if (t.mode === "voice") {
    div.className = "line voice";
    div.innerHTML = `<span class="wave"><i></i><i></i><i></i></span><span>${esc(t.text)}</span>`;
  } else {
    div.className = "line magpie";
    div.innerHTML = `${esc(t.text)}<div class="meta"><span class="mode ${esc(t.mode)}">${esc(MODE_LABEL[t.mode] || t.mode)}</span>${groundChip(t.grounding)}</div>`;
  }
  c.appendChild(div);
  div.scrollIntoView({ behavior: "smooth", block: "end" });
  return div;
}

async function playTurns(turns) {
  for (const t of turns) {
    if (t.role !== "magpie") { addLine(t); continue; }
    if (t.item_id && S.session && t.item_id !== S.shownItem) {
      S.shownItem = t.item_id;
      try { showKeepsake((await api(`/api/items/${t.item_id}`)).item); } catch { /* keep current */ }
    }
    const el = addLine(t);
    if (t.mode === "voice" && t.audio_url) { await playClip(t.audio_url); el.classList.add("done"); }
    else await speak(t.text);
  }
}

async function startVisit(mode) {
  $("#visit-start").classList.add("hidden");
  $("#visit").classList.remove("hidden");
  $("#conversation").innerHTML = "";
  setThinking(true);
  try {
    const s = await api("/api/session/start", { json: { mode } });
    S.session = s; S.shownItem = s.current_item?.id;
    showKeepsake(s.current_item);
    $("#visit-theme").textContent = `Today: ${s.theme}` + (s.plan_reason && mode === "agent" ? ` · ${s.plan_reason}` : "");
    setThinking(false);
    await playTurns(s.turns);
    $("#reply-text").focus();
  } catch (e) {
    setThinking(false);
    $("#visit").classList.add("hidden"); $("#visit-start").classList.remove("hidden");
    toast(e.message, true);
  }
}

async function sendReply(text) {
  text = (text || "").trim();
  if (!text || !S.session || S.busy) return;
  $("#reply-text").value = "";
  addLine({ role: "rose", text });
  setThinking(true);
  try {
    const r = await api(`/api/session/${S.session.id}/reply`, { json: { text } });
    setThinking(false);
    S.session = r.session;
    await playTurns(r.turns);
    if (r.session.ended) finishVisit(r.session);
  } catch (e) { setThinking(false); toast(e.message, true); }
}

async function sessionAction(action, body = {}) {
  if (!S.session || S.busy) return;
  setThinking(true);
  try {
    const r = await api(`/api/session/${S.session.id}/${action}`, { json: body });
    setThinking(false);
    S.session = r.session;
    await playTurns(r.turns);
    if (r.session.ended) finishVisit(r.session);
  } catch (e) { setThinking(false); toast(e.message, true); }
}

function finishVisit(s) {
  setTimeout(() => {
    $("#visit").classList.add("hidden");
    $("#visit-done").classList.remove("hidden");
    $("#visit-summary").textContent = s.summary || "";
    S.session = null;
    refreshReviewCount();
  }, 1200);
}

/* ---- hold up a keepsake (stretch) ---- */
let camStream = null;
async function openCamera() {
  try {
    camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    $("#camera").srcObject = camStream;
    $("#camera-status").textContent = "Magpie will look for a keepsake it knows.";
    $("#camera-dialog").showModal();
  } catch { toast("Camera not available.", true); }
}
function closeCamera() {
  camStream?.getTracks().forEach((t) => t.stop()); camStream = null;
  $("#camera-dialog").close();
}
async function snapKeepsake() {
  const v = $("#camera");
  const c = document.createElement("canvas");
  c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext("2d").drawImage(v, 0, 0);
  $("#camera-status").textContent = "Looking…";
  try {
    const m = await api("/api/objects/match", { json: { image: c.toDataURL("image/jpeg", 0.85) } });
    if (!m.item_id) { $("#camera-status").textContent = `I see ${m.seen || "something lovely"}, but I don't know its story yet.`; return; }
    closeCamera();
    toast(`That's ${m.item.title.toLowerCase()}.`);
    await sessionAction("jump", { item_id: m.item_id });
  } catch (e) { $("#camera-status").textContent = e.message; }
}

/* ============================================================ family */

function bindFamily() {
  $("#upload-form").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    fd.append("contributor", who());
    try {
      const it = await api("/api/items", { method: "POST", body: fd });
      e.target.reset(); toast("Keepsake added.");
      await loadItems(); selectItem(it.id);
    } catch (err) { toast(err.message, true); }
  };

  $("#story-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const btn = f.querySelector("button"); btn.disabled = true; btn.textContent = "Magpie is reading…";
    try {
      const r = await api("/api/stories", { json: { text: f.text.value, contributor: who(), item_id: f.item_id.value || null } });
      f.reset(); showAddResult(r);
    } catch (err) { toast(err.message, true); }
    btn.disabled = false; btn.textContent = "Save story";
  };

  let vrec = null, vblob = null;
  $("#voice-rec").onclick = async () => {
    if (!vrec) {
      try { vrec = await startRecording(); } catch { return toast("Microphone not available.", true); }
      $("#voice-rec").textContent = "■ Stop"; $("#voice-status").textContent = "Recording…"; return;
    }
    vblob = await vrec.stop(); vrec = null;
    $("#voice-rec").textContent = "● Record again"; $("#voice-status").textContent = "Recorded. Ready to save.";
  };
  $("#voice-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const fd = new FormData();
    const file = f.file.files[0];
    if (vblob) fd.append("file", vblob, "voice.webm"); else if (file) fd.append("file", file); else return toast("Record or choose a voice note first.");
    fd.append("speaker", who()); fd.append("story_id", f.story_id.value); fd.append("play_in_tell", f.play_in_tell.checked);
    const btn = f.querySelector("button.primary"); btn.disabled = true; btn.textContent = "Listening…";
    try {
      const r = await api("/api/voice", { method: "POST", body: fd });
      vblob = null; f.reset(); $("#voice-status").textContent = "or choose a file";
      showAddResult(r); loadVoice();
    } catch (err) { toast(err.message, true); }
    btn.disabled = false; btn.textContent = "Save voice note";
  };
}

async function refreshReviewCount() {
  try {
    const r = await api("/api/review");
    const n = r.stories.length + r.facts.length + r.fragments.length;
    $("#review-count").textContent = n ? n : "";
  } catch { /* ignore */ }
}

function factHtml(f) {
  const sugg = f.status === "suggested";
  return `<div class="fact ${sugg ? "suggested" : ""}" data-id="${f.id}">
    <div class="text">${esc(f.text)}</div>
    <div class="src">${esc(f.source)}</div>
    ${sugg ? `<div class="acts">
      <button data-act="confirm" class="primary">Confirm</button>
      <button data-act="edit">Edit</button>
      <button data-act="reject" class="danger">Reject</button></div>` : ""}
  </div>`;
}

function bindFacts(root, after) {
  $$(".fact", root).forEach((el) => {
    const id = el.dataset.id;
    el.querySelectorAll("[data-act]").forEach((b) => (b.onclick = async () => {
      const act = b.dataset.act;
      if (act === "edit") {
        const t = $(".text", el);
        t.innerHTML = `<input type="text" value="${esc(t.textContent)}">`;
        $(".acts", el).innerHTML = `<button data-save class="primary">Save and confirm</button>`;
        $("[data-save]", el).onclick = async () => {
          await api(`/api/facts/${id}/edit`, { json: { text: $("input", t).value, by: who() } });
          toast("Saved."); after(); refreshReviewCount();
        };
        return;
      }
      await api(`/api/facts/${id}/${act}`, { json: { by: who() } });
      toast(act === "confirm" ? "Confirmed." : "Rejected."); after(); refreshReviewCount();
    }));
  });
}

async function loadItems() {
  S.items = await api("/api/items");
  $("#items-grid").innerHTML = S.items.map((i) => `
    <button class="item-tile card ${S.selectedItem === i.id ? "active" : ""}" data-id="${esc(i.id)}">
      ${i.image_url ? `<img src="${esc(i.image_url)}" alt="" loading="lazy">` : `<div class="noimg">No picture yet<br><code>data/images/${esc(i.id)}.jpg</code></div>`}
      <div class="t">${esc(i.title)}</div>
      <div class="s">
        ${i.suggested ? `<span class="chip wait">${i.suggested} to check</span>` : ""}
        ${i.story ? `<span class="chip ok">${esc(i.story.title)}</span>` : ""}
      </div>
    </button>`).join("");
  $$(".item-tile").forEach((b) => (b.onclick = () => selectItem(b.dataset.id)));
  if (S.selectedItem) selectItem(S.selectedItem, false);
}

async function selectItem(id, highlight = true) {
  S.selectedItem = id;
  if (highlight) $$(".item-tile").forEach((b) => b.classList.toggle("active", b.dataset.id === id));
  const d = await api(`/api/items/${id}`);
  const it = d.item;
  const box = $("#item-detail");
  box.innerHTML = `
    ${it.image_url ? `<img src="${esc(it.image_url)}" alt="">` : ""}
    <div class="row between" style="margin-top:10px">
      <h3>${esc(it.title)}</h3><span class="chip ${/AI-generated/.test(it.label) ? "ai" : ""}">${esc(it.label)}</span>
    </div>
    ${it.ai_description ? `<p class="muted small">Magpie sees: ${esc(it.ai_description)}</p>` : ""}
    <div class="row">
      <input type="text" id="extract-note" placeholder="Anything Magpie should know? (optional)">
      <button id="btn-extract" class="primary" ${it.image_url ? "" : "disabled"}>Ask Magpie to read it</button>
    </div>
    <div id="item-facts"></div>
    ${d.story ? `<h3 style="margin-top:18px">Story: ${esc(d.story.title)}</h3>
      <p class="small muted">${esc(d.story.source)}</p>
      ${d.story_facts.map(factHtml).join("")}` : ""}`;
  const facts = d.facts;
  $("#item-facts").innerHTML = facts.length ? facts.map(factHtml).join("") : `<p class="muted small">No facts about this keepsake yet.</p>`;
  bindFacts(box, () => { selectItem(id, false); loadItems(); });
  $("#btn-extract").onclick = async () => {
    const b = $("#btn-extract"); b.disabled = true; b.textContent = "Reading…";
    try {
      const r = await api(`/api/items/${id}/extract`, { json: { note: $("#extract-note").value || null } });
      toast(`Magpie suggested ${r.facts.length} facts. Please check them.`);
      selectItem(id, false); loadItems(); refreshReviewCount();
    } catch (e) { toast(e.message, true); b.disabled = false; b.textContent = "Ask Magpie to read it"; }
  };
}

async function loadAdd() {
  const [items, stories] = await Promise.all([api("/api/items"), api("/api/stories?status=confirmed")]);
  $$(".item-select").forEach((s) => (s.innerHTML = `<option value="">None</option>` + items.map((i) => `<option value="${esc(i.id)}">${esc(i.title)}</option>`).join("")));
  $$(".story-select").forEach((s) => (s.innerHTML = `<option value="">A new story</option>` + stories.map((x) => `<option value="${esc(x.id)}">${esc(x.title)}</option>`).join("")));
  loadVoice();
}

function storyCard(s, facts, suggested) {
  return `<div class="card story-card" data-story="${esc(s.id)}">
    <div class="row between"><h3>${esc(s.title)}</h3><span class="chip">${esc(s.decade || "")}</span></div>
    <p class="story-text">${esc(s.text)}</p>
    <p class="small muted">${esc(s.source)}</p>
    ${(facts || []).map(factHtml).join("")}
    ${suggested ? `<div class="row" style="margin-top:10px">
      <button class="primary" data-story-act="confirm">Confirm story</button>
      <button class="danger" data-story-act="reject">Not right</button></div>` : ""}
  </div>`;
}

function bindStoryActs(root, after) {
  $$("[data-story-act]", root).forEach((b) => (b.onclick = async () => {
    const id = b.closest("[data-story]").dataset.story;
    await api(`/api/stories/${id}/${b.dataset.storyAct}`, { json: { by: who() } });
    toast(b.dataset.storyAct === "confirm" ? "Story confirmed. Magpie can share it now." : "Story set aside.");
    after(); refreshReviewCount();
  }));
}

function showAddResult(r) {
  const box = $("#add-result");
  const facts = r.facts || [];
  box.innerHTML = (r.transcript ? `<div class="card"><h3>What Magpie heard</h3><p>${esc(r.transcript)}</p></div>` : "") +
    (r.story ? storyCard(r.story, facts.filter((f) => f.story_id === r.story.id), true)
      : `<div class="card"><h3>Suggested facts</h3>${facts.map(factHtml).join("") || `<p class="muted">No new facts.</p>`}</div>`);
  bindFacts(box, () => { loadReview(); box.innerHTML = `<p class="muted">Thank you. Saved.</p>`; });
  bindStoryActs(box, () => (box.innerHTML = `<p class="muted">Thank you. Saved.</p>`));
  refreshReviewCount();
}

async function loadVoice() {
  const notes = await api("/api/voice");
  $("#voice-list").innerHTML = notes.map((v) => `
    <div class="fact" data-vid="${esc(v.id)}">
      <div class="row between"><strong>${esc(v.speaker_name)}'s voice note</strong>
        <span class="chip ${v.play_in_tell ? "ok" : ""}">${v.play_in_tell ? "plays in Tell mode" : "for the archive"}</span></div>
      ${v.has_audio ? `<audio controls src="${esc(v.audio_url)}"></audio>` : `<p class="muted small">No recording yet. Save it as <code>${esc(v.audio_path)}</code></p>`}
      ${v.transcript ? `<p class="small">“${esc(v.transcript)}”</p>` : v.has_audio ? `<button class="soft" data-transcribe>Transcribe and suggest facts</button>` : ""}
    </div>`).join("") || `<p class="muted">No voice notes yet.</p>`;
  $$("[data-transcribe]").forEach((b) => (b.onclick = async () => {
    b.disabled = true; b.textContent = "Listening…";
    try { showAddResult(await api(`/api/voice/${b.closest("[data-vid]").dataset.vid}/transcribe`, { method: "POST" })); loadVoice(); }
    catch (e) { toast(e.message, true); b.disabled = false; b.textContent = "Transcribe and suggest facts"; }
  }));
}

async function loadReview() {
  const r = await api("/api/review");
  const box = $("#review");
  const parts = [];
  if (r.stories.length) {
    parts.push(`<h3>New stories</h3>`);
    r.stories.forEach((s) => parts.push(storyCard(s, s.facts, true)));
  }
  if (r.facts.length) parts.push(`<div class="card"><h3>Facts to check</h3>${r.facts.map(factHtml).join("")}</div>`);
  if (r.fragments.length) parts.push(`<div class="card"><h3>Their words from visits</h3>
    <p class="muted small">Pieces they said that Magpie kept. Keep them in the story, or let them go.</p>
    ${r.fragments.map((f) => `<div class="fact" data-frag="${f.id}">
      <div class="text her-words">“${esc(f.text)}”</div>
      <div class="src">while looking at ${esc(f.item_title || "a keepsake")}, ${esc(new Date(f.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" }))}</div>
      <div class="acts"><button class="primary" data-frag-act="keep">Keep in the story</button><button data-frag-act="dismiss">Let it go</button></div>
    </div>`).join("")}</div>`);
  box.innerHTML = parts.join("") || `<div class="card"><p>Nothing waiting. Everything has been checked. 🌿</p></div>`;
  bindFacts(box, loadReview);
  bindStoryActs(box, loadReview);
  $$("[data-frag-act]", box).forEach((b) => (b.onclick = async () => {
    await api(`/api/fragments/${b.closest("[data-frag]").dataset.frag}`, { json: { action: b.dataset.fragAct, by: who() } });
    loadReview(); refreshReviewCount();
  }));
}

async function loadArchive() {
  const stories = await api("/api/archive");
  let dec = null;
  $("#archive").innerHTML = stories.map((s) => {
    const head = s.decade !== dec ? `<div class="decade">${esc((dec = s.decade) || "Undated")}</div>` : "";
    return head + `<div class="card story-card">
      <div class="row between"><h3>${esc(s.title)}</h3><span class="small muted">${esc(s.source)}</span></div>
      ${s.items.length ? `<div class="thumbs">${s.items.filter((i) => i.image_url).map((i) => `<img src="${esc(i.image_url)}" alt="${esc(i.title)}" title="${esc(i.title)}">`).join("")}</div>` : ""}
      ${s.facts.map((f) => `<div class="fact"><div class="text">${esc(f.text)}</div><div class="src">${esc(f.source)}</div></div>`).join("")}
      ${s.her_words.map((w) => `<p class="her-words">In their words: “${esc(w.text)}”</p>`).join("")}
      ${s.voice_notes.filter((v) => v.has_audio).map((v) => `<p class="small">${esc(v.speaker_name)}'s voice note <audio controls src="${esc(v.audio_url)}"></audio></p>`).join("")}
    </div>`;
  }).join("") || `<p class="muted">No confirmed stories yet.</p>`;
}

async function loadVisits() {
  const [g, visits] = await Promise.all([api("/api/grounding"), api("/api/sessions")]);
  const L = g.live, R = g.report?.stats;
  const tile = (n, l, good) => `<div class="tile ${good ? "good" : ""}"><div class="n">${n}</div><div class="l">${l}</div></div>`;
  $("#grounding-tiles").innerHTML =
    tile(L.invented_spoken, "invented facts spoken in visits", L.invented_spoken === 0) +
    tile(L.claims_traced, "facts traced to their confirmed stories") +
    tile(L.drafts_caught, "drafts caught and rewritten before speaking") +
    (R ? tile(`${R.invented_spoken} / ${g.report.sessions_run}`, `invented facts across simulated visits (${esc(g.report.provider)})`, R.invented_spoken === 0) : "");
  $("#visits").innerHTML = visits.map((v) => `<div class="card">
      <div class="row between"><strong>${esc(v.theme)}</strong><span class="small muted">${esc(new Date(v.started_at).toLocaleString())}</span></div>
      <p>${esc(v.summary || "Still going, or ended without a summary.")}</p></div>`).join("") || `<p class="muted">No visits yet.</p>`;
}

async function loadCards() {
  const cards = await api("/api/cards");
  renderCards(cards);
  $("#btn-cards").onclick = async () => {
    const b = $("#btn-cards"); b.disabled = true; b.textContent = "Writing cards…";
    try { await api("/api/cards/generate", { method: "POST" }); renderCards(await api("/api/cards")); }
    catch (e) { toast(e.message, true); }
    b.disabled = false; b.textContent = "Make cards";
  };
}

function renderCards(cards) {
  $("#cards").innerHTML = cards.map((c) => `<div class="talk-card">
    <span class="chip">${esc(c.story_title)} · ${esc(c.decade || "")}</span>
    <p class="opener">${esc(c.opener)}</p>
    <strong class="small">Things you can mention</strong>
    <ul>${(c.details || []).map((d) => `<li>${esc(d)}</li>`).join("")}</ul>
    <p class="tip">${esc(c.tip)}</p></div>`).join("") || `<p class="muted">No cards yet. Make some from their confirmed stories.</p>`;
}

/* ============================================================ quilt */

function bindQuilt() {
  $("#btn-export").onclick = async () => {
    const b = $("#btn-export"); b.disabled = true;
    try {
      const r = await api("/api/quilt/export", { method: "POST" });
      $("#export-link").innerHTML = `Saved. <a href="${esc(r.url)}" target="_blank" download="memory-quilt.png">Open the quilt image</a>`;
    } catch (e) { toast(e.message, true); }
    b.disabled = false;
  };
}

async function loadQuilt() {
  const q = await api("/api/quilt");
  renderQuilt(q);
}

function renderQuilt(q) {
  $("#quilt-meta").textContent = `${q.filled} of ${q.stories} stories stitched · ${q.label}`;
  $("#quilt").innerHTML = q.rows.map((r) => `<div class="quilt-row"><div class="dec">${esc(r.decade)}</div>${r.squares.map((sq) => {
    if (sq.url) return `<div class="square"><img src="${esc(sq.url)}" alt="${esc(sq.title)}"><div class="label">${esc(sq.title)}</div></div>`;
    if (sq.story_id) return `<div class="square pending"><span>${esc(sq.title)}</span><button class="soft" data-stitch="${esc(sq.story_id)}">Stitch this square</button></div>`;
    return `<div class="square empty ${q.empty_url ? "bg" : ""}" ${q.empty_url ? `style="background-image:url('${esc(q.empty_url)}')"` : ""}>${q.empty_url ? "" : "A story still to come"}</div>`;
  }).join("")}</div>`).join("") || `<p class="muted">Confirm a story to start the quilt.</p>`;
  if (q.export_url) $("#export-link").innerHTML = `<a href="${esc(q.export_url)}" target="_blank">Open the last saved quilt image</a>`;
  $$("[data-stitch]").forEach((b) => (b.onclick = async () => {
    b.disabled = true; b.textContent = "Stitching…";
    try { renderQuilt(await api(`/api/quilt/${b.dataset.stitch}/stitch`, { method: "POST" })); toast("A new square is stitched in."); }
    catch (e) { toast(e.message, true); b.disabled = false; b.textContent = "Stitch this square"; }
  }));
}

init().catch((e) => toast(e.message, true));
