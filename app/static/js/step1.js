// Live preview for the branding screen: name, initials, logo and colors update as you type.
(function () {
  const $ = (id) => document.getElementById(id);
  const root = document.documentElement;
  const initials = (name) => {
    const w = name.split(/[^A-Za-z0-9]+/).filter(Boolean);
    return !w.length ? "?" : w.length === 1 ? w[0].slice(0, 2).toUpperCase() : (w[0][0] + w[1][0]).toUpperCase();
  };
  const isHex = (v) => /^#[0-9a-fA-F]{6}$/.test(v);
  let logoUrl = null;
  function paint() {
    const name = $("name").value.trim() || "Your organization";
    $("pvName").textContent = name;
    const p = isHex($("primary").value) ? $("primary").value : null;
    const a = isHex($("accent").value) ? $("accent").value : null;
    if (p) { root.style.setProperty("--primary", p); $("primaryPick").value = p; }
    if (a) { root.style.setProperty("--accent", a); $("accentPick").value = a; }
    for (const id of ["logoAvatar", "pvAvatar"]) {
      const el = $(id);
      if (logoUrl) {
        el.textContent = "";
        const im = document.createElement("img");
        im.src = logoUrl; im.alt = "";
        el.appendChild(im);
      } else if (!el.querySelector("img")) {
        el.textContent = initials(name);
      }
    }
  }
  function showSwatches(list, boxId) {
    const box = $(boxId || "swatches");
    box.textContent = "";
    box.hidden = !list.length;
    for (const c of list) {
      const el = document.createElement("div");
      el.className = "swatch";
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.style.background = c.color;
      const txt = document.createElement("span");
      const code = document.createElement("div");
      code.className = "code";
      code.textContent = c.color;
      const why = document.createElement("div");
      why.className = "why";
      why.textContent = c.why;
      txt.append(code, why);
      el.append(chip, txt);
      for (const [label, target] of [["Primary", "primary"], ["Accent", "accent"]]) {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = label;
        b.addEventListener("click", () => { $(target).value = c.color; paint(); });
        el.appendChild(b);
      }
      box.appendChild(el);
    }
  }
  $("name").addEventListener("input", paint);
  for (const id of ["primary", "accent"]) $(id).addEventListener("input", paint);
  $("primaryPick").addEventListener("input", (e) => { $("primary").value = e.target.value; paint(); });
  $("accentPick").addEventListener("input", (e) => { $("accent").value = e.target.value; paint(); });
  $("logo").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    if (f.size > 2 * 1024 * 1024) {
      $("logoText").textContent = "That file is over 2 MB; please choose a smaller one.";
      e.target.value = "";
      return;
    }
    logoUrl = URL.createObjectURL(f);
    const fd = new FormData();
    fd.append("logo", f);
    fetch("/logo-colors", { method: "POST", body: fd }).then((r) => r.json())
      .then((d) => showSwatches(d.candidates || [], "logoSwatches")).catch(() => {});
    $("logoText").textContent = "Selected: " + f.name + " (saved when you continue)";
    paint();
  });
  $("brand_guide").addEventListener("change", (e) => {
    if (e.target.files[0]) $("guideText").textContent = "Selected: " + e.target.files[0].name;
  });
  $("pullColors").addEventListener("click", async () => {
    const url = $("website").value.trim();
    const hint = $("pullHint");
    if (!url) { hint.textContent = "Paste a website address first."; return; }
    hint.textContent = "Reading the page…";
    try {
      const r = await fetch("/pull-colors", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }),
      });
      const d = await r.json();
      if (d.primary) $("primary").value = d.primary;
      if (d.accent) $("accent").value = d.accent;
      paint();
      hint.textContent = d.message;
      showSwatches(d.candidates || [], "swatches");
    } catch (err) {
      hint.textContent = "Could not read that page. Set the colors by hand below.";
    }
  });
  paint();
  const folder = location.pathname.split("/")[2];
  fetch("/org/" + folder + "/logo-colors").then((r) => r.json())
    .then((d) => showSwatches(d.candidates || [], "logoSwatches")).catch(() => {});
})();
