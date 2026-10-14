// Shared progress bar for background jobs: stage, count, time elapsed and an estimate of the time left.
(function () {
  const $ = (id) => document.getElementById(id);
  const clock = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  const words = (s) => (s < 90 ? "under a minute" : s < 5400 ? `about ${Math.round(s / 60)} min` : `about ${(s / 3600).toFixed(1)} hours`);

  // watchJob(folder, jobId, {onDone(result), onFail(message)}) polls the job and keeps #jobStage, #jobNums, #jobFill and #jobResult current.
  window.watchJob = function (folder, job, o) {
    const box = $("jobBox"), bar = box.querySelector(".bar");
    box.hidden = false; box.classList.remove("bad");
    let started = Date.now(), last = null, samples = [], samplesStage = "";
    // Time left comes from the speed of the CURRENT stage over the last minute or so (not the whole job, whose earlier stages ran at other speeds)
    const secondsLeft = (j) => {
      const now = Date.now() / 1000;
      if (j.stage !== samplesStage) { samples = []; samplesStage = j.stage; }
      if (!samples.length || samples[samples.length - 1].done !== j.done) samples.push({ t: now, done: j.done });
      samples = samples.filter((s) => now - s.t <= 90 || s === samples[samples.length - 1]);
      const first = samples[0], lastS = samples[samples.length - 1];
      let rate = lastS.t > first.t && lastS.done > first.done ? (lastS.done - first.done) / (lastS.t - first.t) : 0;
      if (!rate && j.stage_elapsed > 0 && j.done > 0) rate = j.done / j.stage_elapsed;
      return rate ? Math.round((j.total - j.done) / rate) : null;
    };
    const paint = (j) => {
      const elapsed = Math.max(j.elapsed || 0, Math.round((Date.now() - started) / 1000));
      const counted = j.total > 1 && j.done > 0;
      let detail = [];
      if (j.total > 1) detail.push(`${j.done} of ${j.total}${j.unit ? " " + j.unit : ""}`);
      detail.push(`${clock(elapsed)} elapsed`);
      if (counted && j.done < j.total) {
        const left = secondsLeft(j);
        detail.push(left === null ? "calculating time left…" : `${words(left)} left`);
      } else if (!j.finished) {
        // a stage that has not reported a count yet: never drop the estimate silently
        detail.push(j.total > 1 ? (j.done >= j.total ? "finishing this step…" : "calculating time left…") : (j.hint ? `usually ${j.hint}` : "working…"));
      }
      $("jobNums").textContent = detail.join(" · ");
      $("jobStage").textContent = (j.step && !j.finished ? `Step ${j.step} of ${j.steps}: ` : "") + j.stage;
      let note = $("jobNote");
      if (!note) { note = document.createElement("div"); note.id = "jobNote"; note.className = "jobnote"; box.querySelector(".jobhead").after(note); }
      note.textContent = j.finished ? "" : (j.note || "");
      bar.classList.toggle("indet", !counted && !j.finished);
      $("jobFill").style.width = j.finished ? "100%" : counted ? Math.max(4, (100 * j.done) / j.total) + "%" : "";
    };
    const tick = async () => {
      let j;
      try { j = await (await fetch(`/org/${folder}/bank/jobs/${job}`)).json(); }
      catch (e) { setTimeout(tick, 3000); return; }          // the page may have lost the connection for a moment
      last = j;
      paint(j);
      if (!j.finished) { setTimeout(tick, j.total > 1 ? 1500 : 1000); return; }
      bar.classList.remove("indet");
      if (j.error) { box.classList.add("bad"); o.onFail && o.onFail(j.error); return; }
      o.onDone && o.onDone(j.result);
    };
    tick();
    // keep the clock moving between polls
    const t = setInterval(() => { if (last && !last.finished) paint(last); else if (last) clearInterval(t); }, 1000);
  };
})();
