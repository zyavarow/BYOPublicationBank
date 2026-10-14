// Live preview for the branding screen: name, initials, logo and colors update as you type.
(function () {
  const $ = (id) => document.getElementById(id);
  const root = document.body;   // the page's brand variables live on body, so the preview must set them there
  const initials = (name) => {
    const w = name.split(/[^A-Za-z0-9]+/).filter(Boolean);
    return !w.length ? "?" : w.length === 1 ? w[0].slice(0, 2).toUpperCase() : (w[0][0] + w[1][0]).toUpperCase();
  };
  const isHex = (v) => /^#[0-9a-fA-F]{6}$/.test(v);
  let logoUrl = null;
  const INK = "#1E1C18", PAPER = "#F7F4EC";
  const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const lum = (h) => { const [r, g, b] = rgb(h).map((c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };
  const mix = (a, b, t) => "#" + rgb(a).map((v, i) => Math.round(v * (1 - t) + rgb(b)[i] * t).toString(16).padStart(2, "0")).join("").toUpperCase();
  const readable = (c) => { let t = 0; while (contrast(mix(c, INK, t), PAPER) < 4.5 && t < 1) t += 0.05; return mix(c, INK, t); };
  function checkColors(p, a) {
    const box = $("colorCheck"); if (!box) return;
    const cp = contrast(p, "#FFFFFF"), ca = contrast(a, "#FFFFFF");
    const notes = [];
    if (cp < 4.5) notes.push("Your main color is light, so buttons will use dark text.");
    if (contrast(p, PAPER) < 3) notes.push("Your main color is very close to the page background; links will be darkened so they stay readable.");
    box.classList.toggle("warn", notes.length > 0);
    const b = document.createElement("b"); b.textContent = notes.length ? "Heads up" : "Readable";
    box.textContent = ""; box.append(b, document.createTextNode(notes.length ? notes.join(" ") : "Text on both colors is easy to read."));
  }
  function paint() {
    const name = $("name").value.trim() || "Your organization";
    $("pvName").textContent = name;
    const p = isHex($("primary").value) ? $("primary").value : null;
    const a = isHex($("accent").value) ? $("accent").value : null;
    if (p) { root.style.setProperty("--primary", p); $("primaryPick").value = p; }
    if (a) { root.style.setProperty("--accent", a); $("accentPick").value = a; }
    const pp = p || getComputedStyle(root).getPropertyValue("--primary").trim() || "#1E1C18";
    const aa = a || getComputedStyle(root).getPropertyValue("--accent").trim() || "#B5592E";
    // keep text readable on any brand color (the server does the same for every other page)
    root.style.setProperty("--on-primary", contrast(pp, "#FFFFFF") >= 4.5 ? "#FFFFFF" : INK);
    root.style.setProperty("--on-accent", contrast(aa, "#FFFFFF") >= 4.5 ? "#FFFFFF" : INK);
    root.style.setProperty("--link", readable(pp));
    root.style.setProperty("--accent-text", readable(aa));
    checkColors(pp, aa);
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
  $("brand_guide").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    $("guideText").textContent = "Reading the colors in " + f.name + "…";
    const fd = new FormData();
    fd.append("guide", f);
    try {
      const d = await (await fetch("/guide-colors", { method: "POST", body: fd })).json();
      if (d.primary) $("primary").value = d.primary;
      if (d.accent) $("accent").value = d.accent;
      paint();
      showSwatches(d.candidates || [], "guideSwatches");
      $("guideText").textContent = f.name + ": " + d.message + " (The file is saved when you continue.)";
    } catch (err) {
      $("guideText").textContent = "Could not read that file. Set the colors by hand below.";
    }
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
  fetch("/org/" + folder + "/guide-colors").then((r) => r.json()).then((d) => showSwatches(d.candidates || [], "guideSwatches")).catch(() => {});
  fetch("/org/" + folder + "/logo-colors").then((r) => r.json())
    .then((d) => showSwatches(d.candidates || [], "logoSwatches")).catch(() => {});
})();
