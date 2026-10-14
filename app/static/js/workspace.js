// Workspace: ask a question, show a cited, synthesized answer (or just the best passages), filters, roles, depth, recent searches.
(function () {
  const root = document.getElementById("wsRoot");
  const folder = root.dataset.folder;
  const $ = (id) => document.getElementById(id);
  const state = { role: "scientist", depth: root.dataset.depth, fmt: root.dataset.format, filters: {} };
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };

  // roles, depth, wording
  for (const [sel, key, attr] of [[".role", "role", "role"], [".depth", "depth", "depth"], [".fmt", "fmt", "fmt"]]) {
    for (const b of document.querySelectorAll(sel)) b.addEventListener("click", () => {
      state[key] = b.dataset[attr];
      for (const o of document.querySelectorAll(sel)) o.classList.toggle("on", o === b);
      const pick = b.closest(".pick");
      if (pick) { pick.querySelector(".tval").textContent = b.dataset.word; closeMenus(); }
    });
  }
  // the small menus in the "Answering as ..." sentence
  function closeMenus() {
    for (const m of document.querySelectorAll(".menu")) m.hidden = true;
    for (const t of document.querySelectorAll(".trig")) t.setAttribute("aria-expanded", "false");
  }
  for (const t of document.querySelectorAll(".trig")) t.addEventListener("click", (e) => {
    e.stopPropagation();
    const menu = t.parentElement.querySelector(".menu"), wasHidden = menu.hidden;
    closeMenus();
    menu.hidden = !wasHidden; t.setAttribute("aria-expanded", String(wasHidden));
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".menu")) closeMenus(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenus(); });
  const moreBtn = $("moreFilters");
  if (moreBtn) moreBtn.addEventListener("click", () => {
    for (const x of document.querySelectorAll("[data-extra]")) x.hidden = false;
    moreBtn.hidden = true;
  });

  // filter chips: a panel of the values found by the extraction
  const facets = {};
  const chipRow = $("chipRow");
  if (chipRow) {
    fetch(`/org/${folder}/workspace/facets`).then((r) => r.json()).then((d) => d.facets.forEach((f) => (facets[f.id] = f)));
    for (const chip of chipRow.querySelectorAll(".fchip")) chip.addEventListener("click", () => openFacet(chip));
  }
  function openFacet(chip) {
    const panel = $("facetPanel"), f = facets[chip.dataset.cid];
    if (!panel.hidden && panel.dataset.cid === chip.dataset.cid) { panel.hidden = true; return; }
    panel.dataset.cid = chip.dataset.cid; panel.textContent = ""; panel.hidden = false;
    if (!f || !f.values.length) { panel.appendChild(el("div", "faint", "No values yet: this field fills in as papers are read by the extraction.")); return; }
    for (const v of f.values) {
      const lab = el("label", "facetopt");
      const cb = el("input"); cb.type = "checkbox"; cb.checked = (state.filters[f.id] || []).includes(v.value);
      cb.addEventListener("change", () => {
        const set = new Set(state.filters[f.id] || []);
        cb.checked ? set.add(v.value) : set.delete(v.value);
        if (set.size) state.filters[f.id] = [...set]; else delete state.filters[f.id];
        chip.classList.toggle("on", !!state.filters[f.id]);
        chip.textContent = state.filters[f.id] ? `${chip.dataset.label}: ${state.filters[f.id].length} chosen` : "any " + chip.dataset.label.toLowerCase();
      });
      lab.append(cb, document.createTextNode(` ${v.value} `), el("small", "faint", `${v.papers}`));
      panel.appendChild(lab);
    }
  }

  // asking
  async function go() {
    const q = $("question").value.trim();
    if (!q) return;
    const free = $("freeOnly").checked;
    $("goBtn").disabled = true; $("empty").hidden = true; $("answer").textContent = ""; $("jobResult").textContent = "";
    const body = new URLSearchParams({ question: q, role: state.role, depth: state.depth, fmt: state.fmt, mode: free ? "search" : "answer", filters: JSON.stringify(state.filters) });
    let d;
    try { const r = await fetch(`/org/${folder}/workspace/ask`, { method: "POST", body }); d = await r.json(); if (!r.ok) throw new Error(d.error); }
    catch (e) { $("goBtn").disabled = false; showError(e.message || "Something went wrong."); return; }
    if (d.mode === "search") { $("goBtn").disabled = false; $("jobBox").hidden = true; renderPassages(d.passages); return; }
    watchJob(folder, d.job, {
      onFail: (m) => { $("goBtn").disabled = false; showError(m); },
      onDone: (r) => { $("goBtn").disabled = false; $("jobBox").hidden = true; renderAnswer(r); refreshRecent(r); },
    });
  }
  function showError(msg) { $("jobBox").hidden = true; const c = el("div", "callout warn", msg); $("answer").textContent = ""; $("answer").appendChild(c); }
  $("goBtn").addEventListener("click", go);
  $("question").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  for (const b of document.querySelectorAll(".sg")) b.addEventListener("click", () => { $("question").value = b.textContent; go(); });

  // an answer: question, strength of evidence, cited paragraphs, what is not covered, where sources differ, sources
  const METER = { high: 5, medium: 3, low: 1 };
  function renderAnswer(r) {
    const a = r.answer, box = $("answer");
    box.textContent = "";
    box.appendChild(el("div", "eyebrow link", "Your question"));
    box.appendChild(el("h1", "qshow", r.question));
    const lvl = a.confidence.toLowerCase();
    const strip = el("div", "evstrip");
    strip.appendChild(el("span", "lab2", "Strength of evidence"));
    const meter = el("span", "meter"); meter.setAttribute("role", "img"); meter.setAttribute("aria-label", `${METER[lvl] || 1} of 5: ${lvl}`);
    for (let i = 0; i < 5; i++) meter.appendChild(el("i", i < (METER[lvl] || 1) ? "" : "off"));
    strip.appendChild(meter);
    strip.appendChild(el("span", "level", a.confidence));
    strip.appendChild(el("span", "stats2", [`${a.papers_cited || 0} paper${a.papers_cited === 1 ? "" : "s"}`, Number.isFinite(a.seconds) ? `${Math.round(a.seconds)} s` : "", `$${(a.cost || 0).toFixed(3)}`].filter(Boolean).join(" · ")));
    box.appendChild(strip);
    const body = el("div", "anscard");
    if (!a.paragraphs.length) body.appendChild(el("p", "ansp faint", "Nothing in the library matched this question."));
    for (const p of a.paragraphs) body.appendChild(cited(p.text));
    box.appendChild(body);
    box.appendChild(el("p", "basis", [a.confidence_reason, a.confidence_basis].filter(Boolean).join(" ")));
    if (a.not_covered && a.not_covered.length) {
      const c = el("section", "callout notcov");
      c.appendChild(el("h2", "ctitle", "Not covered by this library"));
      const ul = el("ul"); a.not_covered.forEach((x) => ul.appendChild(el("li", "", x))); c.appendChild(ul); box.appendChild(c);
    }
    if (a.disagreements && a.disagreements.length) {
      const c = el("section", "callout disag");
      c.appendChild(el("h2", "ctitle", "Where the sources differ"));
      const ul = el("ul"); a.disagreements.forEach((x) => ul.appendChild(cited(x, "li"))); c.appendChild(ul); box.appendChild(c);
    }
    renderSources(box, r.sources);
  }
  // text with [n] markers: a run such as [1][2] becomes one small raised "1,2" whose numbers link to the sources
  function jumpTo(n) { const s = $("src-" + n); if (s) { s.scrollIntoView({ behavior: "smooth", block: "center" }); s.classList.add("flash"); setTimeout(() => s.classList.remove("flash"), 1600); } }
  function cited(text, tag) {
    const p = el(tag || "p", tag ? "" : "ansp");
    let last = 0;
    for (const m of text.matchAll(/(?:\[\d+\][,\s]*)+/g)) {
      p.appendChild(document.createTextNode(text.slice(last, m.index)));
      const sup = el("sup", "cites");
      [...m[0].matchAll(/\d+/g)].forEach((n, i) => {
        if (i) sup.appendChild(document.createTextNode(","));
        const a = el("a", "cite", n[0]); a.href = "#src-" + n[0];
        a.addEventListener("click", (e) => { e.preventDefault(); jumpTo(n[0]); });
        sup.appendChild(a);
      });
      p.appendChild(sup);
      if (/\s$/.test(m[0])) p.appendChild(document.createTextNode(" "));
      last = m.index + m[0].length;
    }
    p.appendChild(document.createTextNode(text.slice(last)));
    return p;
  }
  function renderSources(box, sources) {
    if (!sources.length) return;
    const card = el("section", "srccard");
    card.appendChild(el("h2", "srchead", "Sources"));
    for (const s of sources) {
      const row = el("div", "src"); row.id = "src-" + s.no;
      row.appendChild(el("span", "srcno", s.no));
      const body = el("div", "srcbody");
      body.appendChild(el("div", "srctitle", s.title));
      const by = (s.authors || []).join(", ") + (s.authors && s.authors.length >= 3 ? " et al." : "");
      body.appendChild(el("div", "srcmeta", [by, s.journal, s.year].filter(Boolean).join(" · ")));
      const tags = el("div", "tags");
      if (s.text_level === "abstract_only") tags.appendChild(el("span", "tag warn", "Abstract only"));
      for (const x of s.extracted || []) { const [k, ...v] = x.split(": "); tags.appendChild(el("span", "tag" + (k === "Reagents" ? " warn" : ""), (k === "Study" ? "" : k + ": ") + v.join(": ").slice(0, 70))); }
      body.appendChild(tags);
      const links = el("div", "srclinks");
      const noun = `passage${s.passages.length === 1 ? "" : "s"}`;
      const tog = el("button", "linkbtn", `Show the ${s.passages.length} ${noun} ↓`);
      tog.type = "button";
      const pass = el("div", "passages"); pass.hidden = true;
      for (const p of s.passages) { const q = el("blockquote"); q.appendChild(document.createTextNode(p.text)); q.appendChild(el("cite", "", [p.heading, p.page ? "p." + p.page : ""].filter(Boolean).join(", "))); pass.appendChild(q); }
      tog.addEventListener("click", () => { pass.hidden = !pass.hidden; tog.textContent = pass.hidden ? `Show the ${s.passages.length} ${noun} ↓` : `Hide the ${noun} ↑`; });
      links.appendChild(tog);
      if (s.pmid) { const a = el("a", "linkbtn", "PubMed ↗"); a.href = "https://pubmed.ncbi.nlm.nih.gov/" + s.pmid + "/"; a.target = "_blank"; a.rel = "noopener"; links.appendChild(a); }
      if (s.doi) { const a = el("a", "linkbtn", "DOI ↗"); a.href = "https://doi.org/" + s.doi; a.target = "_blank"; a.rel = "noopener"; links.appendChild(a); }
      body.append(links, pass); row.appendChild(body); card.appendChild(row);
    }
    box.appendChild(card);
  }
  // search-only results: passages grouped by paper
  function renderPassages(list) {
    const box = $("answer"); box.textContent = "";
    if (!list.length) { box.appendChild(el("div", "callout warn", "Nothing in the library matched.")); return; }
    const card = el("section", "srccard");
    card.appendChild(el("h2", "srchead", `Best matching passages (${list.length}), no AI used`));
    for (const p of list) {
      const row = el("div", "src");
      const body = el("div", "srcbody");
      body.appendChild(el("div", "srctitle", p.title));
      body.appendChild(el("div", "srcmeta", [p.journal, p.year, p.heading, p.page ? "p." + p.page : ""].filter(Boolean).join(" · ")));
      const q = el("blockquote"); q.textContent = p.text.length > 700 ? p.text.slice(0, 700) + "…" : p.text; body.appendChild(q);
      body.appendChild(el("div", "srcmeta faint", "Found by: " + Object.keys(p.why).join(", ")));
      row.appendChild(body); card.appendChild(row);
    }
    box.appendChild(card);
  }

  // recent searches
  function refreshRecent(r) {
    const box = $("recent");
    const a = el("a", "recent", r.question); a.href = "#"; a.dataset.id = r.id; a.addEventListener("click", open);
    const first = box.querySelector(".faint"); if (first) first.remove();
    box.prepend(a);
    const spent = parseFloat($("spent").textContent.replace("$", "")) + r.answer.cost;
    $("spent").textContent = "$" + spent.toFixed(2);
    const cap = parseFloat(root.dataset.cap) || 0, bar = document.querySelector(".meterbar > div");
    if (bar && cap) bar.style.width = Math.min(100, 100 * spent / cap) + "%";
  }
  async function open(e) {
    e.preventDefault();
    const r = await (await fetch(`/org/${folder}/workspace/answer/${e.currentTarget.dataset.id}`)).json();
    $("empty").hidden = true; $("question").value = r.question; renderAnswer(r);
  }
  for (const a of document.querySelectorAll(".recent")) a.addEventListener("click", open);

  // build the index if it does not exist
  const bi = $("buildIdx");
  if (bi) bi.addEventListener("click", async () => {
    bi.disabled = true;
    const d = await (await fetch(`/org/${folder}/index/build`, { method: "POST" })).json();
    if (d.job) watchJob(folder, d.job, { onFail: (m) => alert(m), onDone: () => location.reload() });
  });
})();
