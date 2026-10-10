// Shared progress bar for background jobs: stage, count, time elapsed and an estimate of the time left.
(function () {
  const $ = (id) => document.getElementById(id);
  const clock = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  const words = (s) => (s < 90 ? "under a minute" : s < 5400 ? `about ${Math.round(s / 60)} min` : `about ${(s / 3600).toFixed(1)} hours`);

  // watchJob(folder, jobId, {onDone(result), onFail(message)}) polls the job and keeps #jobStage, #jobNums, #jobFill and #jobResult current.
  window.watchJob = function (folder, job, o) {
    const box = $("jobBox"), bar = box.querySelector(".bar");
    box.hidden = false; box.classList.remove("bad");
    let started = Date.now(), last = null;
    const paint = (j) => {
      const elapsed = Math.max(j.elapsed || 0, Math.round((Date.now() - started) / 1000));
      const counted = j.total > 1 && j.done > 0;
      let detail = [];
      if (j.total > 1) detail.push(`${j.done} of ${j.total}`);
      detail.push(`${clock(elapsed)} elapsed`);
      if (counted && j.done < j.total) detail.push(`${words(Math.round((elapsed / j.done) * (j.total - j.done)))} left`);
      else if (!counted && j.hint && !j.finished) detail.push(elapsed > 120 ? "taking longer than usual, still working" : `usually ${j.hint}`);
      $("jobNums").textContent = detail.join(" · ");
      $("jobStage").textContent = j.stage;
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
