// Ticks the "läuft seit" clock of a running task timer. The server sends the elapsed seconds at
// render time; counting from the page's own start keeps it right even if the clocks differ.
(function () {
  const banner = document.querySelector("[data-running-timer]");
  const output = banner?.querySelector("[data-running-elapsed]");
  if (!banner || !output) {
    return;
  }
  const elapsedAtLoad = Number(banner.dataset.elapsed || 0);
  const loadedAt = Date.now();

  const pad = (value) => String(value).padStart(2, "0");
  const render = () => {
    const seconds = elapsedAtLoad + Math.floor((Date.now() - loadedAt) / 1000);
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    output.textContent = `${hours}:${pad(minutes)}:${pad(seconds % 60)}`;
  };

  render();
  window.setInterval(render, 1000);
})();
