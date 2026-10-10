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
    const cleaned = text.replace(/\b(pmid|pubmed id|pubmed)\s*:?\s*/gi, "");
    return new Set(cleaned.split(/[\s,;]+/).filter((x) => /^[1-9]\d{0,8}$/.test(x))).size;
  };
  $("pmidText").addEventListener("input", () => {
    const n = countIds($("pmidText").value);
    $("pmidCount").textContent = n + (n === 1 ? " ID detected" : " IDs detected");
    $("pmidGo").disabled = n === 0;
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

  // jobs
  const box = $("jobBox");
  async function start(request, buttons) {
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
      poll(d.job, buttons);
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
  async function poll(job, buttons) {
    try {
      const r = await fetch(`/org/${folder}/bank/jobs/${job}`);
      const j = await r.json();
      if (j.error && !j.finished) throw new Error(j.error);
      $("jobStage").textContent = j.stage;
      $("jobNums").textContent = j.total ? `${j.done} of ${j.total}` : "";
      $("jobFill").style.width = j.finished ? "100%" : Math.max(4, j.total ? (100 * j.done) / j.total : 4) + "%";
      if (!j.finished) { setTimeout(() => poll(job, buttons), 1000); return; }
      if (j.error) { fail(j.error, buttons); return; }
      const x = j.result;
      if (x.kind === "pdf") {
        const bits = [`${x.added} new PDF${x.added === 1 ? "" : "s"} read. The bank now holds ${x.in_bank} papers.`];
        if (x.duplicate) bits.push(`${x.duplicate} duplicate copies are set aside.`);
        if (x.ocr) bits.push(`${x.ocr} scanned files needing text recognition are set aside.`);
        if (x.setaside) bits.push(`${x.setaside} other items are set aside (see the list for the reason).`);
        if (x.failed) bits.push(`${x.failed} files could not be read.`);
        $("jobResult").textContent = bits.join(" ");
        const more = document.createElement("a");
        more.href = location.pathname; more.textContent = " Refresh the list →"; more.className = "reload";
        $("jobResult").appendChild(more);
        buttons.forEach((b) => (b.disabled = false));
        return;
      }
      const lines = [`Added ${x.added} paper${x.added === 1 ? "" : "s"}: ${x.full_text} with full text, ${x.abstract_only} abstract only, ${x.citation_only} citation only.`];
      if (x.already) lines.push(`${x.already} already in the bank (skipped).`);
      if (x.unmatched) lines.push(`${x.unmatched} reference${x.unmatched === 1 ? " was" : "s were"} not found in PubMed and kept as plain citations.`);
      if (x.not_in_pubmed && x.not_in_pubmed.length) lines.push("Not added: " + x.not_in_pubmed.slice(0, 8).join("; ") + (x.not_in_pubmed.length > 8 ? "…" : ""));
      $("jobResult").textContent = lines.join(" ");
      const more = document.createElement("a");
      more.href = location.pathname;
      more.textContent = " Refresh the list →";
      more.className = "reload";
      $("jobResult").appendChild(more);
      buttons.forEach((b) => (b.disabled = false));
    } catch (err) {
      fail(err.message, buttons);
    }
  }
})();
