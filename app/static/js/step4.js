// Extraction Schema screen: edit field cards, add custom fields, upload material, run the AI proposal, review and edit it, approve.
(function () {
  const root = document.getElementById("schemaRoot");
  const folder = root.dataset.folder;
  const $ = (id) => document.getElementById(id);
  const err = $("schemaErr");
  const show = (msg) => { err.textContent = msg; err.hidden = !msg; if (msg) err.scrollIntoView({ block: "nearest" }); };
  async function post(path, params, files) {
    show("");
    let body = new URLSearchParams(params || {});
    if (files) { body = new FormData(); for (const [k, v] of Object.entries(params || {})) body.append(k, v); for (const f of files) body.append("files", f, f.name); }
    const r = await fetch(`/org/${folder}/schema/${path}`, { method: "POST", body });
    const d = await r.json().catch(() => ({ error: "Something went wrong." }));
    if (!r.ok) { show(d.error || "Something went wrong."); return null; }
    return d;
  }
  const lines = (text) => text.split("\n").map((x) => x.trim()).filter(Boolean);

  const start = $("startBtn");
  if (start) start.addEventListener("click", async () => { if (await post("start", { disease: $("startDisease").value })) location.reload(); });
  if (!$("disease")) return;

  // project description, notes, uploads
  $("saveCtx").addEventListener("click", async () => {
    const d = await post("context", { disease: $("disease").value });
    if (d) $("ctxNote").textContent = "Saved. The sample check will ask you to read again.";
  });
  $("notes").addEventListener("change", () => post("notes", { notes: $("notes").value }));
  $("docFiles").addEventListener("change", async (e) => {
    const files = [...e.target.files];
    if (!files.length) return;
    $("docNote").textContent = "Reading…";
    const d = await post("upload", {}, files);
    if (d) { if (d.skipped.length) $("docNote").textContent = "Skipped: " + d.skipped.join("; "); setTimeout(() => location.reload(), d.skipped.length ? 2500 : 300); }
    else $("docNote").textContent = "";
  });
  for (const b of document.querySelectorAll("[data-del]")) b.addEventListener("click", async () => { if (await post("upload-delete", { name: b.dataset.del })) location.reload(); });

  // running the AI (first proposal, or a revision with feedback)
  async function runProposal(params, button, confirmBox) {
    button.disabled = true;
    $("jobBox").hidden = false;
    $("jobStage").textContent = "Starting…"; $("jobResult").textContent = ""; $("jobNums").textContent = "";
    await post("notes", { notes: $("notes").value });
    const d = await post("propose", { confirm: "yes", ...params });
    if (!d) { $("jobBox").hidden = true; confirmBox.checked = false; return; }
    watchJob(folder, d.job, {
      onFail: (m) => { $("jobResult").textContent = m; },
      onDone: (x) => {
        $("jobResult").textContent = `Done for $${x.cost.toFixed(2)}: ${x.changes} field changes and ${x.new_fields} new fields proposed.`;
        setTimeout(() => location.reload(), 1200);
      },
    });
  }
  const prop = $("propBtn");
  $("propConfirm").addEventListener("change", (e) => (prop.disabled = !(e.target.checked && prop.dataset.ready === "1")));
  prop.addEventListener("click", () => runProposal({}, prop, $("propConfirm")));

  // the proposal as the person has edited it
  const data = $("propData");
  const base = data ? JSON.parse(data.textContent) : null;
  function edited() {
    const p = JSON.parse(JSON.stringify(base));
    p.disease = $("pDisease").value.trim();
    p.topic_terms = $("pTerms").value.split(";").map((x) => x.trim()).filter(Boolean);
    p.gene_focus_options = lines($("pFocus").value);
    p.genes = [...document.querySelectorAll("#pGenes tr")].slice(1).map((tr) => {
      const g = (k) => tr.querySelector(`[data-g=${k}]`).value.trim();
      return { symbol: g("symbol"), aliases: g("aliases").split(",").map((x) => x.trim()).filter(Boolean), role: g("role"), note: g("note") };
    }).filter((g) => g.symbol);
    for (const ch of p.core_changes) {
      const box = document.querySelector(`[data-corebox="${ch.id}"]`);
      if (!box || ch.action !== "edit") continue;
      ch.add_options = lines(box.querySelector("[data-co=add_options]").value);
      ch.counts_addition = box.querySelector("[data-co=counts_addition]").value.trim();
    }
    p.new_fields.forEach((f, i) => {
      const box = document.querySelector(`[data-newbox="${i}"]`);
      const v = (k) => box.querySelector(`[data-nf=${k}]`).value;
      f.label = v("label").trim(); f.kind = v("kind"); f.options = lines(v("options")); f.counts = v("counts").trim(); f.not_counts = v("not_counts").trim();
    });
    return p;
  }
  if (base) {
    $("addGene").addEventListener("click", () => {
      const row = document.querySelector("#pGenes tr:nth-child(2)").cloneNode(true);
      for (const el of row.querySelectorAll("input")) el.value = "";
      document.getElementById("pGenes").appendChild(row);
      wireGeneDelete(row);
    });
    const wireGeneDelete = (row) => row.querySelector("[data-gdel]").addEventListener("click", () => row.remove());
    document.querySelectorAll("#pGenes tr").forEach((r, i) => { if (i) wireGeneDelete(r); });
    $("applyBtn").addEventListener("click", async () => {
      const acc = { core: [], new: [] };
      for (const c of document.querySelectorAll("#proposal input[type=checkbox][data-k], #proposal input[type=checkbox][data-core], #proposal input[type=checkbox][data-new]")) {
        if (!c.checked) continue;
        if (c.dataset.k) acc[c.dataset.k] = true;
        if (c.dataset.core) acc.core.push(c.dataset.core);
        if (c.dataset.new !== undefined) acc.new.push(parseInt(c.dataset.new, 10));
      }
      $("applyBtn").disabled = true;
      const d = await post("apply", { accept: JSON.stringify(acc), proposal: JSON.stringify(edited()) });
      if (d) { alert("Applied:\n- " + (d.done.join("\n- ") || "nothing selected")); location.reload(); } else $("applyBtn").disabled = false;
    });
    const rev = $("reviseBtn");
    $("reviseConfirm").addEventListener("change", (e) => (rev.disabled = !(e.target.checked && rev.dataset.ready === "1" && $("feedback").value.trim())));
    $("feedback").addEventListener("input", () => (rev.disabled = !($("reviseConfirm").checked && rev.dataset.ready === "1" && $("feedback").value.trim())));
    rev.addEventListener("click", () => runProposal({ feedback: $("feedback").value, current: JSON.stringify(edited()) }, rev, $("reviseConfirm")));
  }

  // field cards
  for (const card of document.querySelectorAll(".fcard[data-cid]")) {
    const cid = card.dataset.cid;
    const save = card.querySelector(".save-field");
    if (save) save.addEventListener("click", async () => {
      const p = { cid };
      for (const el of card.querySelectorAll("[data-f]")) p[el.dataset.f] = el.type === "checkbox" ? (el.checked ? "yes" : "no") : el.value;
      save.disabled = true;
      const d = await post("category", p);
      save.disabled = false;
      if (d) { card.querySelector(".fsaved").textContent = "Saved."; setTimeout(() => location.reload(), 500); }
    });
  }
  for (const b of document.querySelectorAll("[data-remove]")) b.addEventListener("click", async () => {
    const custom = b.closest(".fcard").querySelector(".mine");
    if (!confirm(custom ? "Delete this custom field?" : "Remove this field? You can add it back later.")) return;
    if (await post("include", { cid: b.dataset.remove, include: "no" })) location.reload();
  });
  for (const b of document.querySelectorAll("[data-addback]")) b.addEventListener("click", async () => { if (await post("include", { cid: b.dataset.addback, include: "yes" })) location.reload(); });

  // add a custom field
  const kind = $("afKind");
  const syncKind = () => ($("afOptWrap").hidden = kind.value === "text_list");
  kind.addEventListener("change", syncKind); syncKind();
  $("afSave").addEventListener("click", async () => {
    const d = await post("add", { label: $("afLabel").value, kind: kind.value, options: $("afOptions").value, counts: $("afCounts").value,
                                  not_counts: $("afNot").value, all_papers: $("afAll").checked ? "yes" : "no" });
    if (d) location.reload();
  });

  // approval
  $("approveBtn").addEventListener("click", async () => { if (await post("approve", {})) location.href = `/org/${folder}/step/5`; });
})();
