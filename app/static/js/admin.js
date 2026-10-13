// Admin screen: changing the period or a filter, or turning a page, swaps only the parts that change, so the page never jumps back to the top.
(function () {
  const $ = (id) => document.getElementById(id);
  const folder = $("adminRoot").dataset.folder;
  let timer = null;

  const params = (page) => {
    const p = new URLSearchParams({ days: $("days").value, who: $("who").value, depth: $("depth").value, gaps: $("gapsOnly").checked ? "1" : "0", q: $("q").value, p: page || 1 });
    return p;
  };
  const local = (root) => {
    for (const t of root.querySelectorAll("time[data-ts]")) {
      const d = new Date(t.dataset.ts);
      if (!isNaN(d)) t.textContent = d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    }
  };
  async function part(which, page) {
    const target = which === "top" ? $("adminTop") : $("adminLog");
    target.style.opacity = "0.5";
    try {
      const r = await fetch(`/org/${folder}/admin?${params(page)}&partial=${which}`);
      if (r.status === 401) { location.reload(); return; }
      target.innerHTML = await r.text();
      local(target);
    } finally { target.style.opacity = ""; }
    const csv = params(1); csv.delete("p");
    $("csvLink").href = `/org/${folder}/admin/questions.csv?${csv}`;
  }
  const refreshLog = () => { clearTimeout(timer); timer = setTimeout(() => part("log", 1), 250); };
  $("days").addEventListener("change", () => { part("top"); part("log", 1); });
  for (const id of ["who", "depth", "gapsOnly"]) $(id).addEventListener("change", refreshLog);
  $("q").addEventListener("input", refreshLog);
  $("adminLog").addEventListener("click", (e) => {
    const a = e.target.closest(".pager a");
    if (a) { e.preventDefault(); part("log", a.dataset.page); }
  });
  const csv = params(1); csv.delete("p");
  $("csvLink").href = `/org/${folder}/admin/questions.csv?${csv}`;
  local(document);
})();
