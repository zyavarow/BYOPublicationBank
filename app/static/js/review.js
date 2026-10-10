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
  function poll(job) {
    watchJob(folder, job, {
      onFail: fail,
      onDone: (x) => {
        $("jobResult").textContent = `Checked ${x.checked} papers for $${x.cost.toFixed(2)}: ${x.off_topic} off-topic, ${x.borderline} worth a look.` +
          (x.errors ? ` ${x.errors} could not be checked.` : "") + (x.stopped_at_cap ? " Stopped early at the cost limit." : "");
        const more = document.createElement("a");
        more.href = "?g=offtopic"; more.textContent = " Review them →"; more.className = "reload";
        $("jobResult").appendChild(more);
      },
    });
  }

  // Keep in bank / Exclude / Undo buttons. One handler on the list, so it keeps working after a tab switch.
  const list = $("reviewList");
  list.addEventListener("click", async (e) => {
    const b = e.target.closest(".dec");
    if (b) {
      const item = b.closest(".item");
      const next = b.dataset.d;
      const r = await post("decision", new URLSearchParams({ paper_id: item.dataset.id, decision: next }));
      const d = await r.json();
      if (!r.ok) { alert(d.error); return; }
      item.dataset.decision = next;
      const box = item.querySelector(".decide");
      const defaultIn = box.dataset.default === "in";
      const over = (defaultIn && next === "Exclude") || (!defaultIn && next === "Keep");
      box.innerHTML = over
        ? `<span class="cannot">${next === "Exclude" ? "You excluded this" : "You kept this"}</span><button type="button" class="dec" data-d="">Undo</button>`
        : defaultIn ? `<button type="button" class="dec" data-d="Exclude">Exclude</button>` : `<button type="button" class="dec" data-d="Keep">Keep in bank</button>`;
      const st = item.querySelector("[data-role=state]");
      st.textContent = d.in_bank ? "In bank" : "Set aside";
      st.className = "badge " + (d.in_bank ? "ok" : "gray");
      $("bankSummary").firstElementChild.textContent = d.bank_total;
      return;
    }
    // tabs and page links: swap the list in place instead of reloading the page
    const a = e.target.closest("a.tab, .pager a");
    if (a) {
      e.preventDefault();
      const url = new URL(a.getAttribute("href"), location.href);
      list.style.opacity = "0.5";
      try {
        const html = await (await fetch(url.pathname + url.search + (url.search ? "&" : "?") + "partial=1")).text();
        list.innerHTML = html;
        history.replaceState(null, "", url.pathname + url.search);
      } catch (err) { location.href = url; }
      list.style.opacity = "";
    }
  });

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
