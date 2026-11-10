// Ticks the "läuft seit" clock of a running task timer. The server sends the elapsed seconds at
// render time; counting from the page's own start keeps it right even if the clocks differ. A soft
// submit (base.js) can start, stop or re-render the banner, so it is looked up on every tick.
(function () {
  let renderedAt = Date.now();
  const pad = (value) => String(value).padStart(2, "0");

  const render = () => {
    const banner = document.querySelector("[data-running-timer]");
    const output = banner?.querySelector("[data-running-elapsed]");
    if (!output) return;
    const seconds = Number(banner.dataset.elapsed || 0) + Math.floor((Date.now() - renderedAt) / 1000);
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    output.textContent = `${hours}:${pad(minutes)}:${pad(seconds % 60)}`;
  };

  document.addEventListener("lunora:soft-update", () => {
    renderedAt = Date.now();
    render();
  });
  render();
  window.setInterval(render, 1000);
})();
