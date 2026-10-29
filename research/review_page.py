"""Build the blind review page (an Artifact) for one batch of review charts.

Reads review/<batch>/charts.json (written by research.review_charts --batch) and writes
review/<batch>/index.html: one card per chart with Take / Skip / Wait, a "why" box and
an optional target. Answers are saved in the page's own store (the `db` capability,
collection "answers", one document per chart, id "<batch>-NN"), which Claude reads back.
The page shows no outcomes.

Run from the repo root:   python -m research.review_page batch2
"""

from __future__ import annotations

import json
import sys

from research.journal_study import OUT

TEMPLATE = r"""<title>Blind Chart Review</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600&family=Barlow+Condensed:wght@600;700&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
/* Layout: one column of chart cards under a sticky progress bar, like working down a trade log. */
:root {
  --bg: #f3f5f8; --surface: #ffffff; --ink: #121820; --muted: #566270; --line: #dce2e9;
  --accent: #1c5cab; --take: #13845b; --skip: #c4501f; --wait: #5d6672; --warn-bg: #fff4e5; --warn-ink: #7a3e00;
  --display: "Barlow Condensed", "Arial Narrow", system-ui, sans-serif;
  --body: "Barlow", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "JetBrains Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0e1217; --surface: #161c23; --ink: #e5ebf1; --muted: #9aa7b4; --line: #29323c;
  --accent: #6aa6ec; --take: #34c48f; --skip: #f08356; --wait: #a3adb8; --warn-bg: #3a2a12; --warn-ink: #ffd8a8;
  color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #0e1217; --surface: #161c23; --ink: #e5ebf1; --muted: #9aa7b4; --line: #29323c;
  --accent: #6aa6ec; --take: #34c48f; --skip: #f08356; --wait: #a3adb8; --warn-bg: #3a2a12; --warn-ink: #ffd8a8;
  color-scheme: dark; }
body { background: var(--bg); color: var(--ink); font: 16px/1.5 var(--body); }
body::before { content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 10; background: var(--surface); }
.wrap { max-width: 1040px; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 28px 64px; display: grid; gap: 24px; }
header.intro { display: grid; gap: 10px; }
h1 { font: 700 2.1rem/1.1 var(--display); letter-spacing: 0.01em; margin: 0; text-wrap: balance; }
.eyebrow { font: 600 0.8rem/1 var(--display); letter-spacing: 0.12em; text-transform: uppercase; color: var(--accent); }
.intro p { margin: 0; max-width: 68ch; color: var(--muted); }
.key { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 0.9rem; color: var(--muted); }
.key span b { color: var(--ink); font-weight: 600; }
.bar { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5; background: var(--surface);
  border-bottom: 1px solid var(--line); }
.bar .inner { max-width: 1040px; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 10px; display: flex; flex-wrap: wrap;
  align-items: center; gap: 10px 18px; }
.count { font: 500 0.95rem var(--mono); font-variant-numeric: tabular-nums; }
.meter { flex: 1 1 160px; height: 6px; background: var(--line); border-radius: 3px; overflow: hidden; min-width: 120px; }
.meter i { display: block; height: 100%; width: 0; background: var(--accent); transition: width .3s; }
.toggle { display: inline-flex; align-items: center; gap: 8px; font-size: 0.95rem; cursor: pointer; }
.banner { background: var(--warn-bg); color: var(--warn-ink); border-radius: 8px; padding: 12px 14px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; display: grid; }
.card header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 14px; padding: 14px 16px 10px; }
.num { font: 700 1.5rem/1 var(--display); }
.what { font: 600 1.05rem var(--display); letter-spacing: 0.02em; }
.when { color: var(--muted); font-size: 0.9rem; font-family: var(--mono); }
.chart { background: #fcfcfb; border-block: 1px solid var(--line); }
.chart img { display: block; width: 100%; height: auto; }
.answer { display: grid; gap: 12px; padding: 14px 16px 16px; }
.choices { display: flex; flex-wrap: wrap; gap: 8px; }
.choices button { font: 600 1rem var(--body); padding: 8px 18px; border-radius: 6px; border: 1.5px solid var(--line);
  background: transparent; color: var(--ink); cursor: pointer; min-width: 92px; }
.choices button:hover { border-color: var(--muted); }
.choices button:focus-visible, textarea:focus-visible, input:focus-visible, .toggle input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.choices button[aria-pressed="true"][data-v="take"] { background: var(--take); border-color: var(--take); color: #fff; }
.choices button[aria-pressed="true"][data-v="skip"] { background: var(--skip); border-color: var(--skip); color: #fff; }
.choices button[aria-pressed="true"][data-v="wait"] { background: var(--wait); border-color: var(--wait); color: #fff; }
.fields { display: grid; grid-template-columns: minmax(0, 1fr) 180px; gap: 12px; }
@media (max-width: 640px) { .fields { grid-template-columns: minmax(0, 1fr); } }
label { display: grid; gap: 4px; font-size: 0.85rem; color: var(--muted); }
textarea, input[type=text] { font: 1rem var(--body); color: var(--ink); background: var(--bg); border: 1px solid var(--line);
  border-radius: 6px; padding: 8px 10px; width: 100%; box-sizing: border-box; }
textarea { min-height: 64px; resize: vertical; }
input[type=text] { font-family: var(--mono); }
.status { font-size: 0.85rem; color: var(--muted); min-height: 1.2em; }
.status.err { color: var(--skip); }
.done { text-align: center; color: var(--muted); padding: 24px 0; }
@media (prefers-reduced-motion: reduce) { .meter i { transition: none; } }
</style>

<div class="bar"><div class="inner">
  <span class="count" id="count">0 of 0 answered</span>
  <span class="meter" aria-hidden="true"><i id="meter"></i></span>
  <label class="toggle" for="only-open"><input type="checkbox" id="only-open"> Unanswered only</label>
</div></div>

<main class="wrap">
  <header class="intro">
    <span class="eyebrow">ES order book · 2024 · __BATCH_LABEL__</span>
    <h1>Blind chart review</h1>
    <p>Each chart stops at the moment the bot would enter at a marked level. Pick Take, Skip or Wait, and say why in a few words: what you see in the book, delta or volume that makes the call. Add a target price if you would take it. Answers save as you go, and you can come back later.</p>
    <div class="key">
      <span><b>Shading</b> resting orders (darker = more)</span>
      <span><b>Dashed line</b> POC</span>
      <span><b>Green / orange band</b> demand / supply zone (15 min)</span>
      <span><b>Bars</b> aggressive buys up, sells down (15 s)</span>
      <span><b>Bottom line</b> cumulative delta, aqua rising, orange falling</span>
    </div>
  </header>
  <div class="banner" id="nostore" hidden>This view can't save answers. Open the page on claude.ai while signed in. Anything you enter here stays only in this browser for now.</div>
  <div id="cards" style="display:grid;gap:24px"></div>
  <p class="done" id="alldone" hidden>All charts answered. Thanks. Tell Claude in the chat that you're done.</p>
</main>

<script>
const BATCH = "__BATCH__";
const CHARTS = __CHARTS__;
const state = {};            // chart -> {decision, why, target}
let db = null, onlyOpen = false;
const pad = n => String(n).padStart(2, "0");
const docId = n => `${BATCH}-${pad(n)}`;

function localLoad() { try { return JSON.parse(localStorage.getItem("review-" + BATCH) || "{}"); } catch (e) { return {}; } }
function localSave() { try { localStorage.setItem("review-" + BATCH, JSON.stringify(state)); } catch (e) {} }

function card(c) {
  const el = document.createElement("article");
  el.className = "card"; el.id = "c" + pad(c.review);
  const side = c.side === "long" ? "Long" : "Short";
  el.innerHTML = `
    <header><span class="num">#${c.review}</span><span class="what"></span><span class="when"></span></header>
    <div class="chart"><img alt="" loading="${c.review <= 2 ? "eager" : "lazy"}" width="1130" height="859"></div>
    <div class="answer">
      <div class="choices" role="group">
        <button type="button" data-v="take" aria-pressed="false">Take</button>
        <button type="button" data-v="skip" aria-pressed="false">Skip</button>
        <button type="button" data-v="wait" aria-pressed="false">Wait</button>
      </div>
      <div class="fields">
        <label for="why-${c.review}">Why?<textarea id="why-${c.review}" placeholder="What tips it: delta, aggression, resting orders, the level"></textarea></label>
        <label for="tgt-${c.review}">Target (optional)<input type="text" id="tgt-${c.review}" inputmode="decimal" placeholder="e.g. 5281.00"></label>
      </div>
      <div class="status" aria-live="polite"></div>
    </div>`;
  el.querySelector(".what").textContent = `${side} at a ${c.level} test`;
  el.querySelector(".when").textContent = `${c.contract} · ${c.when}`;
  const img = el.querySelector("img");
  img.src = `review_${pad(c.review)}.png`;
  img.alt = `Chart ${c.review}: ${side.toLowerCase()} at a ${c.level} test, ${c.when}`;
  el.querySelector(".choices").setAttribute("aria-label", `Decision for chart ${c.review}`);
  el.querySelectorAll(".choices button").forEach(b => b.addEventListener("click", () => {
    const s = state[c.review] = state[c.review] || {};
    s.decision = b.dataset.v; paint(c.review); save(c, true);
  }));
  let timer = null;
  const later = () => { clearTimeout(timer); timer = setTimeout(() => save(c, false), 900); };
  el.querySelector("textarea").addEventListener("input", e => { (state[c.review] = state[c.review] || {}).why = e.target.value; later(); });
  el.querySelector("input").addEventListener("input", e => { (state[c.review] = state[c.review] || {}).target = e.target.value; later(); });
  el.querySelector("textarea").addEventListener("blur", () => { clearTimeout(timer); save(c, false); });
  el.querySelector("input").addEventListener("blur", () => { clearTimeout(timer); save(c, false); });
  return el;
}

function paint(n) {
  const el = document.getElementById("c" + pad(n)); if (!el) return;
  const s = state[n] || {};
  el.querySelectorAll(".choices button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.v === s.decision)));
  const ta = el.querySelector("textarea"), inp = el.querySelector("input");
  if (document.activeElement !== ta && ta.value !== (s.why || "")) ta.value = s.why || "";
  if (document.activeElement !== inp && inp.value !== (s.target || "")) inp.value = s.target || "";
  el.hidden = onlyOpen && !!s.decision;
}

function progress() {
  const done = CHARTS.filter(c => state[c.review] && state[c.review].decision).length;
  document.getElementById("count").textContent = `${done} of ${CHARTS.length} answered`;
  document.getElementById("meter").style.width = (100 * done / CHARTS.length) + "%";
  document.getElementById("alldone").hidden = done < CHARTS.length;
}

const busy = {}, again = {};
async function save(c, now) {
  const n = c.review, s = state[n] || {};
  progress(); localSave();
  const status = document.getElementById("c" + pad(n)).querySelector(".status");
  if (!db) { status.textContent = "Kept in this browser only"; return; }
  if (busy[n]) { again[n] = true; return; }           // one write at a time per chart
  busy[n] = true; status.className = "status"; status.textContent = "Saving…";
  try {
    await db.collection("answers").doc(docId(n)).set({
      batch: BATCH, chart: n, side: c.side, decision: s.decision || "", why: s.why || "",
      target: s.target || "", saved_at: new Date().toISOString() });
    status.textContent = "Saved";
  } catch (e) {
    status.className = "status err";
    status.textContent = e && e.code === "invalid_argument" ? "Not saved: this account can't write here." : "Not saved yet. It will retry on your next change.";
  } finally {
    busy[n] = false;
    if (again[n]) { again[n] = false; save(c, false); }
  }
}

const cards = document.getElementById("cards");
Object.assign(state, localLoad());
CHARTS.forEach(c => cards.appendChild(card(c)));
CHARTS.forEach(c => paint(c.review)); progress();
document.getElementById("only-open").addEventListener("change", e => { onlyOpen = e.target.checked; CHARTS.forEach(c => paint(c.review)); });

(async () => {
  db = window.claude && window.claude.use ? await window.claude.use("db") : null;
  if (!db) { document.getElementById("nostore").hidden = false; return; }
  db.collection("answers").where("batch", "==", BATCH).onSnapshot(snap => {
    snap.docs.forEach(d => {
      const v = d.data(); if (!v || !v.chart) return;
      const local = state[v.chart] || {};
      state[v.chart] = { decision: v.decision || local.decision, why: v.why ?? local.why, target: v.target ?? local.target };
      paint(v.chart);
    });
    progress(); localSave();
  }, () => { document.getElementById("nostore").hidden = false; });
})();
</script>
"""


def main() -> None:
    batch = sys.argv[1] if len(sys.argv) > 1 else "batch2"
    d = OUT / "review" / batch
    charts = json.loads((d / "charts.json").read_text())
    label = f"batch {batch.removeprefix('batch')} · {len(charts)} charts" if batch.startswith("batch") else batch
    html = (TEMPLATE.replace("__BATCH_LABEL__", label).replace("__BATCH__", batch)
            .replace("__CHARTS__", json.dumps(charts)))
    (d / "index.html").write_text(html)
    print("wrote", d / "index.html", len(charts), "charts")


if __name__ == "__main__":
    main()
