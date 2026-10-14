// Publication Bank screen: tabs, PubMed ID counting, reference-file upload, and progress of background jobs.
(function () {
  const $ = (id) => document.getElementById(id);
  const folder = $("addCard").dataset.folder;

  // tabs
  for (const t of document.querySelectorAll(".tab")) {
    t.addEventListener("click", () => {
      for (const o of document.querySelectorAll(".tab")) o.classList.toggle("on", o === t);
      for (const p of document.querySelectorAll(".panel")) p.hidden = p.id !== "panel-" + t.dataset.tab;
    });
  }

  // PubMed ID list: count IDs as you type
  const countIds = (text) => {
    return idList(text).length;
  };
  // the IDs in a piece of text, each once, in order ("PMID- 123" and "PMID: 123" lines work too)
  const idList = (text) => {
    const cleaned = text.replace(/\b(pmid|pubmed id|pubmed)\s*[:\-]?\s*/gi, "");
    return [...new Set(cleaned.split(/[\s,;]+/).filter((x) => /^[1-9]\d{0,8}$/.test(x)))];
  };
  const minutesFor = (n) => { const m = Math.max(1, Math.round((n * 1.5) / 60)); return m < 90 ? `${m} minute${m === 1 ? "" : "s"}` : `${(m / 60).toFixed(1)} hours`; };
  $("pmidText").addEventListener("input", () => {
    const n = countIds($("pmidText").value);
    $("pmidCount").textContent = n.toLocaleString() + (n === 1 ? " ID detected" : " IDs detected");
    $("pmidGo").disabled = n === 0 || n > 10000;
    $("pmidTime").textContent = n > 10000 ? "That is more than 10,000 IDs; please add them in smaller batches."
      : n ? `Free, no AI used. Fetching and preparing ${n.toLocaleString()} papers takes about ${minutesFor(n)}; keep this window open.` : "";
  });
  // several ID files at once: read them here, merge into the box, remove repeats
  $("pmidFiles").addEventListener("change", async (e) => {
    const files = [...e.target.files];
    if (!files.length) return;
    const per = [];
    const seen = new Set(idList($("pmidText").value));
    const before = seen.size;
    let repeats = 0;
    for (const f of files) {
      const ids = idList(await f.text());
      let fresh = 0;
      for (const id of ids) { if (seen.has(id)) repeats++; else { seen.add(id); fresh++; } }
      per.push(`${f.name}: ${ids.length.toLocaleString()} ID${ids.length === 1 ? "" : "s"}`);
    }
    $("pmidText").value = [...seen].join("\n");
    $("pmidText").dispatchEvent(new Event("input"));
    $("pmidFileNote").textContent = `${files.length} file${files.length === 1 ? "" : "s"} read (${per.join("; ")}). ${seen.size.toLocaleString()} different IDs in total` +
      (repeats ? `; ${repeats.toLocaleString()} repeated ID${repeats === 1 ? "" : "s"} removed` : "") + (before ? ", including what was already in the box" : "") + ".";
    e.target.value = "";            // lets the same file be chosen again
  });
  $("pmidGo").addEventListener("click", async () => {
    const body = new URLSearchParams({ ids: $("pmidText").value });
    start(fetch(`/org/${folder}/bank/pmids`, { method: "POST", body }), [$("pmidGo")]);
  });

  // reference file
  $("refFile").addEventListener("change", (e) => {
    const f = e.target.files[0];
    $("refGo").disabled = !f;
    $("refName").textContent = f ? "Selected: " + f.name : "RIS, BibTeX or CSV, up to 25 MB.";
  });
  $("refGo").addEventListener("click", () => {
    const fd = new FormData();
    fd.append("file", $("refFile").files[0]);
    start(fetch(`/org/${folder}/bank/reference`, { method: "POST", body: fd }), [$("refGo")]);
  });

  // PDFs: pick files or a folder, upload in small batches, then start reading
  let pdfList = [];
  const pickPdfs = (e) => {
    pdfList = [...e.target.files].filter((f) => /\.pdf$/i.test(f.name));
    const mb = Math.round(pdfList.reduce((a, f) => a + f.size, 0) / 1048576);
    $("pdfCount").textContent = pdfList.length ? `${pdfList.length} PDF${pdfList.length === 1 ? "" : "s"} selected (${mb} MB)` : "No PDFs in that selection";
    $("pdfGo").disabled = !pdfList.length;
  };
  $("pdfFiles").addEventListener("change", pickPdfs);
  $("pdfDir").addEventListener("change", pickPdfs);
  $("pdfGo").addEventListener("click", async () => {
    const buttons = [$("pdfGo")];
    buttons.forEach((b) => (b.disabled = true));
    box.hidden = false; box.classList.remove("bad");
    $("jobResult").textContent = "";
    const skipped = [];
    try {
      for (let i = 0; i < pdfList.length; i += 8) {
        const fd = new FormData();
        for (const f of pdfList.slice(i, i + 8)) fd.append("files", f, f.name);
        $("jobStage").textContent = "Uploading PDFs";
        $("jobNums").textContent = `${Math.min(i + 8, pdfList.length)} of ${pdfList.length}`;
        $("jobFill").style.width = Math.max(3, (100 * i) / pdfList.length) + "%";
        const r = await fetch(`/org/${folder}/bank/pdfs/upload`, { method: "POST", body: fd });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || "Upload failed.");
        skipped.push(...d.skipped);
      }
      if (skipped.length) $("jobResult").textContent = "Skipped: " + skipped.slice(0, 5).join("; ") + (skipped.length > 5 ? "…" : "");
      start(fetch(`/org/${folder}/bank/pdfs/process`, { method: "POST" }), buttons);
    } catch (err) { fail(err.message, buttons); }
  });
  // PDFs: a folder already on this computer
  $("folderCheck").addEventListener("click", async () => {
    $("folderInfo").textContent = "Looking…";
    $("folderGo").hidden = true;
    const r = await fetch(`/org/${folder}/bank/pdfs/folder-check`, { method: "POST", body: new URLSearchParams({ path: $("folderPath").value }) });
    const d = await r.json();
    if (!r.ok) { $("folderInfo").textContent = d.error; return; }
    $("folderInfo").textContent = `Found ${d.count} PDFs (${d.megabytes} MB); ${d.new} not in the bank's folder yet.`;
    $("folderGo").hidden = false;
  });
  $("folderGo").addEventListener("click", () => {
    start(fetch(`/org/${folder}/bank/pdfs/folder-add`, { method: "POST", body: new URLSearchParams({ path: $("folderPath").value }) }), [$("folderGo")]);
  });

  // "Prepare for search": only touches papers that have no passages or vectors yet
  const bi = $("buildIdx");
  if (bi) bi.addEventListener("click", () => {
    start(fetch(`/org/${folder}/index/build`, { method: "POST" }), [bi], () => location.reload());
  });

  // jobs
  const box = $("jobBox");
  async function start(request, buttons, after) {
    buttons.forEach((b) => (b.disabled = true));
    box.hidden = false;
    $("jobResult").textContent = "";
    $("jobStage").textContent = "Starting…";
    $("jobFill").style.width = "2%";
    box.classList.remove("bad");
    try {
      const r = await request;
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Something went wrong.");
      poll(d.job, buttons, after);
    } catch (err) {
      fail(err.message, buttons);
    }
  }
  function fail(message, buttons) {
    box.classList.add("bad");
    $("jobStage").textContent = "Could not add these";
    $("jobNums").textContent = "";
    $("jobResult").textContent = message;
    $("jobFill").style.width = "0";
    buttons.forEach((b) => (b.disabled = false));
  }
  function poll(job, buttons, after) {
    watchJob(folder, job, { onFail: (m) => fail(m, buttons), onDone: (x) => (after ? after() : done(x, buttons)) });
  }
  function done(x, buttons) {
    let lines;
    if (x.kind === "pdf") {
      lines = [`${x.added} new PDF${x.added === 1 ? "" : "s"} read. The bank now holds ${x.in_bank} papers.`];
      if (x.duplicate) lines.push(`${x.duplicate} duplicate copies are set aside.`);
      if (x.ocr) lines.push(`${x.ocr} scanned files needing text recognition are set aside.`);
      if (x.setaside) lines.push(`${x.setaside} other items are set aside (see the list for the reason).`);
      if (x.failed) lines.push(`${x.failed} files could not be read.`);
    } else {
      lines = [`Added ${x.added} paper${x.added === 1 ? "" : "s"}: ${x.full_text} with full text, ${x.abstract_only} abstract only, ${x.citation_only} citation only.`];
      if (x.already) lines.push(`${x.already} already in the bank (skipped).`);
      if (x.unmatched) lines.push(`${x.unmatched} reference${x.unmatched === 1 ? " was" : "s were"} not found in PubMed and kept as plain citations.`);
      if (x.not_in_pubmed && x.not_in_pubmed.length) lines.push("Not added: " + x.not_in_pubmed.slice(0, 8).join("; ") + (x.not_in_pubmed.length > 8 ? "…" : ""));
    }
    if (x.added) lines.push(x.indexed === false ? (x.index_error || "They are not searchable yet; use Prepare for search.") : "They are cut into passages and ready for search.");
    $("jobResult").textContent = lines.join(" ");
    const more = document.createElement("a");
    more.href = location.pathname; more.textContent = " Refresh the list →"; more.className = "reload";
    $("jobResult").appendChild(more);
    buttons.forEach((b) => (b.disabled = false));
  }
})();
