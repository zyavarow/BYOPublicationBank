// Clean-up screen: topic checks, Keep / Exclude buttons and the spreadsheet round trip.
(function () {
  const $ = (id) => document.getElementById(id);
  const folder = $("reviewTop").dataset.folder;
  const post = (path, body) => fetch(`/org/${folder}/bank/${path}`, { method: "POST", body });
  const esc = (s) => { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; };

  // keyword check: preview, then apply
  async function keywords(apply) {
    $("kwInfo").textContent = "Checking…";
    const r = await post("topic/keywords", new URLSearchParams({ terms: $("terms").value, apply: apply ? "yes" : "no" }));
    const d = await r.json();
    if (!r.ok) { $("kwInfo").textContent = d.error; return; }
    $("kwInfo").textContent = apply
      ? `Applied: ${d.flagged} of ${d.checked} papers flagged as possibly off-topic.`
      : `Preview: ${d.flagged} of ${d.checked} papers would be flagged.`;
    $("kwList").innerHTML = d.examples.length
      ? "<div class='hint'>For example:</div><ul>" + d.examples.map((e) => `<li>${esc(e.title)}<span> — ${esc(e.reason)}</span></li>`).join("") + "</ul>"
      : "";
    if (apply) setTimeout(() => (location.search = "?g=offtopic"), 1200);
  }
  $("kwPreview").addEventListener("click", () => keywords(false));
  $("kwApply").addEventListener("click", () => keywords(true));

  // AI check: needs the cost box ticked
  const go = $("aiGo");
  if (!go.dataset.nokey) {
    $("aiConfirm").addEventListener("change", (e) => (go.disabled = !e.target.checked));
  }
  const box = $("jobBox");
  go.addEventListener("click", async () => {
    go.disabled = true;
    box.hidden = false; box.classList.remove("bad");
    $("jobResult").textContent = ""; $("jobStage").textContent = "Starting…"; $("jobFill").style.width = "3%";
    const r = await post("topic/ai", new URLSearchParams({ confirm: "yes" }));
    const d = await r.json();
    if (!r.ok) return fail(d.error);
    poll(d.job);
  });
  function fail(msg) {
    box.classList.add("bad");
    $("jobStage").textContent = "Could not run";
    $("jobResult").textContent = msg;
    $("aiConfirm").checked = false;
  }
  async function poll(job) {
    const r = await fetch(`/org/${folder}/bank/jobs/${job}`);
    const j = await r.json();
    $("jobStage").textContent = j.stage;
    $("jobNums").textContent = j.total ? `${j.done} of ${j.total}` : "";
    $("jobFill").style.width = j.finished ? "100%" : Math.max(4, j.total ? (100 * j.done) / j.total : 4) + "%";
    if (!j.finished) { setTimeout(() => poll(job), 1500); return; }
    if (j.error) return fail(j.error);
    const x = j.result;
    $("jobResult").textContent = `Checked ${x.checked} papers for $${x.cost.toFixed(2)}: ${x.off_topic} off-topic, ${x.borderline} worth a look.` +
      (x.errors ? ` ${x.errors} could not be checked.` : "") + (x.stopped_at_cap ? " Stopped early at the cost limit." : "");
    const more = document.createElement("a");
    more.href = "?g=offtopic"; more.textContent = " Review them →"; more.className = "reload";
    $("jobResult").appendChild(more);
  }

  // Keep / Exclude buttons (clicking the chosen one again clears it)
  for (const item of document.querySelectorAll(".item[data-id]")) {
    for (const b of item.querySelectorAll(".dec")) {
      b.addEventListener("click", async () => {
        const current = item.dataset.decision;
        const next = current === b.dataset.d ? "" : b.dataset.d;
        const r = await post("decision", new URLSearchParams({ paper_id: item.dataset.id, decision: next }));
        const d = await r.json();
        if (!r.ok) { alert(d.error); return; }
        item.dataset.decision = next;
        for (const x of item.querySelectorAll(".dec")) {
          x.classList.toggle("on", x.dataset.d === next);
          x.classList.toggle("bad", x.dataset.d === "Exclude" && next === "Exclude");
        }
        const st = item.querySelector("[data-role=state]");
        st.textContent = d.in_bank ? "In bank" : "Set aside";
        st.className = "badge " + (d.in_bank ? "ok" : "gray");
        $("bankSummary").firstElementChild.textContent = d.bank_total;
      });
    }
  }

  // spreadsheet upload
  $("decFile").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    $("decNote").textContent = "Reading…";
    const fd = new FormData();
    fd.append("file", f);
    const r = await post("decisions-upload", fd);
    const d = await r.json();
    if (!r.ok) { $("decNote").textContent = d.error; return; }
    $("decNote").textContent = `Applied ${d.applied} decisions` + (d.ignored ? ` (${d.ignored} rows ignored).` : ".");
    setTimeout(() => location.reload(), 1500);
  });
})();
