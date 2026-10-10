// Check a sample: pick papers, run the read, then mark each answer and watch the scorecard.
(function () {
  const root = document.getElementById("sampleRoot");
  const folder = root.dataset.folder, state = root.dataset.state, sample = root.dataset.sample;
  const $ = (id) => document.getElementById(id);
  const post = (path, body) => fetch(`/org/${folder}/${path}`, { method: "POST", body });

  const again = $("againBtn");
  if (again) again.addEventListener("click", async () => {
    if (state === "done" && !confirm("Start a new sample? Your marks on this one stay saved, but the new sample is read again and costs again.")) return;
    const r = await post("sample/pick", new URLSearchParams({ n: "15" }));
    if (r.ok) location.href = `/org/${folder}/step/5`;
  });

  // choosing
  const pick = $("pickBtn");
  if (pick) pick.addEventListener("click", async () => {
    pick.disabled = true;
    const r = await post("sample/pick", new URLSearchParams({ n: $("sampleN").value }));
    const d = await r.json();
    if (!r.ok) { alert(d.error); pick.disabled = false; return; }
    location.reload();
  });
  for (const b of document.querySelectorAll(".swap")) b.addEventListener("click", async () => {
    b.disabled = true;
    await post("sample/swap", new URLSearchParams({ sample_id: sample, paper_id: b.dataset.paper }));
    location.reload();
  });

  // running
  function watch(job) {
    watchJob(folder, job, {
      onFail: (m) => { $("jobResult").textContent = m; },
      onDone: (x) => {
        $("jobResult").textContent = `Read ${x.done} papers for $${x.cost.toFixed(2)}.` + (x.failed ? ` ${x.failed} could not be read.` : "") +
          (x.stopped_at_cap ? " Stopped early at the cost limit." : "");
        setTimeout(() => location.reload(), 1200);
      },
    });
  }
  const run = $("runBtn");
  if (run) {
    if (!run.dataset.nokey) $("runConfirm").addEventListener("change", (e) => (run.disabled = !e.target.checked));
    run.addEventListener("click", async () => {
      run.disabled = true;
      const r = await post("sample/run", new URLSearchParams({ sample_id: sample, confirm: "yes" }));
      const d = await r.json();
      if (!r.ok) { alert(d.error); $("runConfirm").checked = false; return; }
      location.reload();
    });
  }
  if (state === "running") {
    (async () => {
      const d = await (await fetch(`/org/${folder}/sample/status`)).json();
      if (d.job) watch(d.job); else setTimeout(() => location.reload(), 1500);
    })();
  }

  // marking
  function renderScore(s) {
    const row = (l, bold) => `<tr class="${bold ? "tot" : ""}"><td>${l.name}</td><td>${l.reviewed}</td><td>${l.pct === null ? "–" : l.pct + "%"}</td></tr>`;
    const o = s.overall;
    $("scoreBody").innerHTML = `<div class="big">${o.pct === null ? "–" : o.pct + "%"}<small> correct</small></div>` +
      `<div class="hint">${o.reviewed} answers marked: ${o.Correct} correct, ${o["Correct but incomplete"]} incomplete, ${o["Partly wrong"]} partly wrong, ${o.Wrong} wrong, ${o.Unsure} unsure</div>` +
      (s.categories.length ? `<table class="stbl"><tr><th>Field</th><th>Marked</th><th>Correct</th></tr>${s.categories.map((c) => row(c)).join("")}</table>` : "");
  }
  const data = $("scoreData");
  if (data) renderScore(JSON.parse(data.textContent));
  const send = async (card, fields) => {
    const body = new URLSearchParams({ sample_id: sample, paper_id: card.dataset.paper, category: card.dataset.cat, ...fields });
    const r = await post("sample/verdict", body);
    if (r.ok) renderScore((await r.json()).score);
  };
  for (const card of document.querySelectorAll(".acard")) {
    for (const b of card.querySelectorAll(".v")) b.addEventListener("click", () => {
      const next = b.classList.contains("on") ? "" : b.dataset.v;
      for (const x of card.querySelectorAll(".v")) {
        x.classList.toggle("on", x.dataset.v === next);
        x.classList.toggle("bad", x.dataset.v === next && (next === "Wrong" || next === "Partly wrong"));
      }
      send(card, { verdict: next || "__none__" });
      const side = document.querySelector(".pitem.on .pc");
      if (side) {
        const n = card.parentElement.querySelectorAll(".v.on").length;
        side.textContent = `${n}/${side.textContent.split("/")[1]}`;
      }
    });
    const note = card.querySelector(".note");
    if (note) note.addEventListener("change", () => send(card, { note: note.value || "__none__" }));
  }

  // spreadsheet
  const chk = $("chkFile");
  if (chk) chk.addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    $("chkNote").textContent = "Reading…";
    const fd = new FormData();
    fd.append("file", f);
    const r = await post(`sample/${sample}/import`, fd);
    const d = await r.json();
    if (!r.ok) { $("chkNote").textContent = d.error; return; }
    $("chkNote").textContent = `Applied ${d.applied} marks.`;
    setTimeout(() => location.reload(), 1200);
  });
})();
