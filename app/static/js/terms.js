// Terms screen: the Continue button stays off until the box is ticked (the server checks it too).
(function () {
  const box = document.getElementById("agree"), btn = document.getElementById("agreeBtn");
  if (!box || !btn) return;
  const sync = () => { btn.disabled = !box.checked; };
  box.addEventListener("change", sync);
  sync();
})();
