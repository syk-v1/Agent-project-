/* AI Council — frontend logic
   - loads free models into two rosters
   - streams the council via fetch() + SSE and stages the proceedings
*/
"use strict";

const $ = (sel) => document.querySelector(sel);

const state = {
  models: [],
  assembly: new Set(),
  governing: new Set(),
  running: false,
  proposals: [],          // {label, model, answer}
  labelByModel: new Map(),
};

/* ---------- theme ---------- */
(function initTheme() {
  const saved = localStorage.getItem("council-theme");
  const root = document.documentElement;
  if (saved) root.setAttribute("data-theme", saved);
  const btn = $("#theme-toggle");
  const sync = () => {
    const dark = getComputedStyle(root).getPropertyValue("--bg").trim().startsWith("#1");
    btn.textContent = dark ? "Daylight" : "Torchlight";
  };
  sync();
  btn.addEventListener("click", () => {
    const current = root.getAttribute("data-theme");
    const next = current === "dark" ? "light" : current === "light" ? "dark"
      : (window.matchMedia("(prefers-color-scheme: dark)").matches ? "light" : "dark");
    root.setAttribute("data-theme", next);
    localStorage.setItem("council-theme", next);
    sync();
  });
})();

/* ---------- model loading ---------- */
async function loadModels() {
  const notice = $("#notice");
  try {
    const res = await fetch("/api/models");
    const data = await res.json();
    if (data.error) {
      notice.innerHTML = data.error.replace(
        /https:\/\/openrouter\.ai\/keys/,
        '<a href="https://openrouter.ai/keys" target="_blank" rel="noopener">openrouter.ai/keys</a>'
      );
      return;
    }
    state.models = data.models || [];
    if (state.models.length === 0) {
      notice.textContent = "No free models were returned by OpenRouter right now.";
      return;
    }
    seedDefaults();
    renderRoster("assembly");
    renderRoster("governing");
    updateControls();
  } catch (err) {
    notice.textContent = "Could not reach the server to load models.";
  }
}

const FAMILIES = ["deepseek", "llama", "mistral", "qwen", "gemini", "gemma", "phi", "nemo", "zephyr", "openchat"];

function familyOf(id) {
  const lower = id.toLowerCase();
  return FAMILIES.find((f) => lower.includes(f)) || null;
}

/* pre-pick a diverse set: 3 distinct families for the Assembly,
   2 different distinct families for the Governing Body */
function seedDefaults() {
  const usedFam = new Set();
  const takeDistinct = (target, n, avoidIds) => {
    for (const m of state.models) {
      if (target.size >= n) break;
      if (avoidIds && avoidIds.has(m.id)) continue;
      const fam = familyOf(m.id);
      if (fam && !usedFam.has(fam)) { usedFam.add(fam); target.add(m.id); }
    }
  };
  takeDistinct(state.assembly, 3, null);
  takeDistinct(state.governing, 2, state.assembly);
  // fallbacks if the catalog lacked recognised families
  for (const m of state.models) {
    if (state.assembly.size >= 2) break;
    if (!state.governing.has(m.id)) state.assembly.add(m.id);
  }
  for (const m of state.models) {
    if (state.governing.size >= 1) break;
    if (!state.assembly.has(m.id)) state.governing.add(m.id);
  }
}

function renderRoster(which) {
  const list = $(`#${which}-list`);
  const search = $(`#${which}-search`).value.trim().toLowerCase();
  const selected = state[which];
  const other = which === "assembly" ? state.governing : state.assembly;
  list.innerHTML = "";

  for (const m of state.models) {
    if (search && !(m.name.toLowerCase().includes(search) || m.id.toLowerCase().includes(search))) continue;
    const inOther = other.has(m.id);
    const opt = document.createElement("label");
    opt.className = "opt" + (inOther ? " disabled" : "");
    opt.title = inOther ? "Already serving in the other chamber" : m.id;

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = selected.has(m.id);
    cb.disabled = inOther;
    cb.addEventListener("change", () => {
      if (cb.checked) selected.add(m.id); else selected.delete(m.id);
      // reflect the cross-chamber lock in the opposite list
      renderRoster(which === "assembly" ? "governing" : "assembly");
      updateControls();
    });

    const nm = document.createElement("span");
    nm.className = "nm";
    nm.textContent = m.name;

    opt.append(cb, nm);
    list.append(opt);
  }
}

function updateControls() {
  $("#assembly-count").textContent = `${state.assembly.size} selected`;
  $("#governing-count").textContent = `${state.governing.size} selected`;
  const ok = state.assembly.size >= 2 && state.governing.size >= 1 &&
    $("#question").value.trim().length > 0 && !state.running;
  $("#convene").disabled = !ok;
}

$("#assembly-search").addEventListener("input", () => renderRoster("assembly"));
$("#governing-search").addEventListener("input", () => renderRoster("governing"));
$("#question").addEventListener("input", updateControls);

/* ---------- running the council ---------- */
const STAGE_TEXT = {
  proposals: "The assembly proposes",
  debate: "The assembly debates",
  vote: "The governing body deliberates",
};

$("#convene").addEventListener("click", runCouncil);

async function runCouncil() {
  if (state.running) return;
  state.running = true;
  state.proposals = [];
  state.labelByModel.clear();
  updateControls();

  // reset the stage
  $("#stelae").innerHTML = "";
  $("#bench").innerHTML = "";
  $("#verdict").innerHTML = "";
  $("#err-log").innerHTML = "";
  $("#assembly-chamber").classList.add("hidden");
  $("#governing-chamber").classList.add("hidden");
  $("#verdict-chamber").classList.add("hidden");
  setStage("proposals");

  const body = {
    question: $("#question").value.trim(),
    assembly: [...state.assembly],
    governing: [...state.governing],
  };

  try {
    const res = await fetch("/api/council", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      logError(detail.detail || `Request failed (HTTP ${res.status}).`);
      finish();
      return;
    }
    await consumeStream(res.body);
  } catch (err) {
    logError("Lost connection to the council.");
  }
  finish();
}

function finish() {
  state.running = false;
  $("#stage-banner").classList.remove("on");
  updateControls();
}

async function consumeStream(stream) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop();               // keep the trailing partial block
    for (const block of blocks) {
      const line = block.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      let ev;
      try { ev = JSON.parse(line.slice(6)); } catch { continue; }
      handleEvent(ev);
    }
  }
}

function setStage(name) {
  const banner = $("#stage-banner");
  banner.classList.add("on");
  $("#stage-text").textContent = STAGE_TEXT[name] || "The council convenes";
}

function handleEvent(ev) {
  switch (ev.type) {
    case "stage": setStage(ev.name); break;
    case "proposal": addProposal(ev); break;
    case "debate": addDebate(ev); break;
    case "vote": addVote(ev); break;
    case "result": showResult(ev); break;
    case "model_error": logError(`${short(ev.model)} withdrew during ${ev.stage}: ${ev.message}`); break;
    case "aborted": logError(ev.message); break;
    case "done": break;
  }
}

/* ---------- renderers ---------- */
function short(id) {
  return id.includes("/") ? id.split("/")[1].replace(/:free$/, "") : id;
}

function addProposal(ev) {
  $("#assembly-chamber").classList.remove("hidden");
  state.proposals.push({ label: ev.label, model: ev.model, answer: ev.answer });
  state.labelByModel.set(ev.model, ev.label);

  const card = document.createElement("article");
  card.className = "stele";
  card.id = `stele-${ev.label}`;
  card.style.animationDelay = `${(state.proposals.length - 1) * 0.09}s`;
  card.innerHTML = `
    <div class="medallion">${ev.label}</div>
    <div class="who">${escapeHtml(short(ev.model))}</div>
    <div class="answer"></div>
    <div class="debate hidden"></div>
    <div class="tally-badge hidden"><span class="n"></span><span class="bar"><i></i></span></div>`;
  card.querySelector(".answer").innerHTML = renderMarkdown(ev.answer);
  $("#stelae").append(card);
}

function addDebate(ev) {
  const label = state.labelByModel.get(ev.model);
  const card = label && $(`#stele-${label}`);
  if (!card) return;
  const box = card.querySelector(".debate");
  box.classList.remove("hidden");
  box.innerHTML = `<span class="lbl">In debate</span><div class="prose">${renderMarkdown(ev.critique)}</div>`;
}

function addVote(ev) {
  $("#governing-chamber").classList.remove("hidden");
  const seat = document.createElement("div");
  seat.className = "seat";
  const abstain = !ev.vote;
  seat.innerHTML = `
    <div class="judge">${escapeHtml(short(ev.judge))}</div>
    <div class="cast">
      <span class="ostrakon ${abstain ? "abstain" : ""}">${abstain ? "∅" : ev.vote}</span>
      <span class="reason"></span>
    </div>`;
  seat.querySelector(".reason").textContent = abstain ? "abstained" : ev.reason || "";
  $("#bench").append(seat);
}

function showResult(ev) {
  // fill in per-stele tallies + bars
  const total = ev.total_votes || 0;
  for (const p of state.proposals) {
    const card = $(`#stele-${p.label}`);
    if (!card) continue;
    const votes = ev.tally[p.label] || 0;
    const badge = card.querySelector(".tally-badge");
    badge.classList.remove("hidden");
    badge.querySelector(".n").textContent = `${votes} vote${votes === 1 ? "" : "s"}`;
    const pct = total > 0 ? Math.round((votes / total) * 100) : 0;
    // let the transition run on the next frame
    requestAnimationFrame(() => { card.querySelector(".bar > i").style.width = `${pct}%`; });
    if (p.label === ev.winner_label) card.classList.add("win");
  }

  const v = $("#verdict");
  $("#verdict-chamber").classList.remove("hidden");
  v.className = "verdict" + (ev.ratified ? "" : " unratified");
  const winner = state.proposals.find((p) => p.label === ev.winner_label);
  const name = winner ? short(winner.model) : ev.winner_label;
  v.innerHTML = makeLaurel();
  const stamp = document.createElement("div");
  stamp.className = "stamp";
  stamp.textContent = ev.ratified
    ? `Answer ${ev.winner_label} is ratified`
    : `No proposal reached a vote`;
  const decree = document.createElement("div");
  decree.className = "decree";
  decree.textContent = ev.ratified
    ? `Championed by ${name} · ${ev.total_votes} vote${ev.total_votes === 1 ? "" : "s"} cast`
    : `The governing body cast no valid votes; Answer ${ev.winner_label} stands by precedence.`;
  v.append(stamp, decree);
}

/* laurel wreath — two stems of leaves rising from the base, open at the top.
   Generated so we don't hand-author long static path data. */
function makeLaurel() {
  const cx = 100, cy = 74, r = 54;
  const pt = (deg) => {
    const t = (deg * Math.PI) / 180;
    return [cx + r * Math.cos(t), cy + r * Math.sin(t)];
  };
  const leaf = (deg, out) => {
    const [x, y] = pt(deg);
    // leaf points radially outward (out=+1) then tilts toward the crown
    const rot = deg + (out > 0 ? 18 : -18);
    return `<path d="M0 0 C6 -4 15 -3 20 0 C15 3 6 4 0 0 Z"
      transform="translate(${x.toFixed(1)} ${y.toFixed(1)}) rotate(${rot.toFixed(1)})"/>`;
  };
  const berry = (deg) => {
    const [x, y] = pt(deg);
    return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.4" opacity="0.85"/>`;
  };

  const leaves = [], berries = [];
  // right stem: base (95°) sweeping up the right side to the top gap (-40°)
  for (let d = 95; d >= -40; d -= 15) leaves.push(leaf(d, +1));
  for (let d = 80; d >= -25; d -= 30) berries.push(berry(d));
  // left stem: mirror
  for (let d = 85; d <= 220; d += 15) leaves.push(leaf(d, -1));
  for (let d = 100; d <= 205; d += 30) berries.push(berry(d));

  // stems, drawn as two arcs from the base up each side
  const [rx0, ry0] = pt(95), [rx1, ry1] = pt(-40);
  const [lx0, ly0] = pt(85), [lx1, ly1] = pt(220);
  const stems =
    `<path fill="none" stroke="var(--bronze)" stroke-width="2" stroke-linecap="round"
       d="M${rx0.toFixed(1)} ${ry0.toFixed(1)} A${r} ${r} 0 0 0 ${rx1.toFixed(1)} ${ry1.toFixed(1)}"/>` +
    `<path fill="none" stroke="var(--bronze)" stroke-width="2" stroke-linecap="round"
       d="M${lx0.toFixed(1)} ${ly0.toFixed(1)} A${r} ${r} 0 0 1 ${lx1.toFixed(1)} ${ly1.toFixed(1)}"/>`;

  return `<svg class="laurel" viewBox="0 0 200 130" role="img" aria-label="laurel wreath" fill="var(--bronze)">
    ${stems}${leaves.join("")}${berries.join("")}
  </svg>`;
}

function logError(msg) {
  const div = document.createElement("div");
  div.className = "err";
  div.textContent = msg;
  $("#err-log").append(div);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* Minimal, dependency-free, XSS-safe markdown -> HTML.
   Input is escaped FIRST, so model text can never inject live tags; only the
   markup we add below becomes real HTML. Handles the subset models actually
   emit: headings, bold, italics, inline code, and unordered/ordered lists. */
function inlineMd(text) {
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*(?!\s)([^*]+?)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/(^|[^_])_(?!\s)([^_]+?)_(?!_)/g, "$1<em>$2</em>");
}

function renderMarkdown(raw) {
  const src = escapeHtml(String(raw || "").trim());
  const lines = src.split(/\r?\n/);
  const html = [];
  let list = null;                        // "ul" | "ol" | null
  const closeList = () => { if (list) { html.push(`</${list}>`); list = null; } };

  let para = [];
  const flushPara = () => {
    if (para.length) { html.push(`<p>${inlineMd(para.join(" "))}</p>`); para = []; }
  };

  for (const line of lines) {
    const t = line.trim();
    if (!t) { flushPara(); closeList(); continue; }

    let m;
    if ((m = t.match(/^#{1,6}\s+(.*)$/))) {          // heading -> styled eyebrow
      flushPara(); closeList();
      html.push(`<h4>${inlineMd(m[1])}</h4>`);
    } else if ((m = t.match(/^[-*•]\s+(.*)$/))) {    // unordered item
      flushPara();
      if (list !== "ul") { closeList(); html.push("<ul>"); list = "ul"; }
      html.push(`<li>${inlineMd(m[1])}</li>`);
    } else if ((m = t.match(/^\d+[.)]\s+(.*)$/))) {  // ordered item
      flushPara();
      if (list !== "ol") { closeList(); html.push("<ol>"); list = "ol"; }
      html.push(`<li>${inlineMd(m[1])}</li>`);
    } else {                                         // paragraph text
      closeList();
      para.push(t);
    }
  }
  flushPara(); closeList();
  return html.join("");
}

loadModels();
