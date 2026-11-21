// Presentation mode of a note (note_present.html): one slide at a time, arrow keys / Page Up/Down /
// Space / Home / End or a horizontal swipe to move, F for full screen. Without JavaScript the slides
// simply stack, so the page stays readable.
(() => {
  const deck = document.querySelector("[data-note-deck]");
  if (!deck) return;

  const slides = [...deck.querySelectorAll("[data-slide]")];
  const counter = deck.querySelector("[data-deck-counter]");
  const previousButton = deck.querySelector("[data-deck-prev]");
  const nextButton = deck.querySelector("[data-deck-next]");
  const fullscreenButton = deck.querySelector("[data-deck-fullscreen]");
  let index = 0;

  const show = (target) => {
    index = Math.max(0, Math.min(slides.length - 1, target));
    slides.forEach((slide, position) => slide.classList.toggle("is-current", position === index));
    counter.textContent = `${index + 1} / ${slides.length}`;
    previousButton.disabled = index === 0;
    nextButton.disabled = index === slides.length - 1;
    history.replaceState(null, "", `#${index + 1}`);
  };

  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else deck.requestFullscreen?.().catch(() => {});
  };

  previousButton.addEventListener("click", () => show(index - 1));
  nextButton.addEventListener("click", () => show(index + 1));
  fullscreenButton.hidden = !document.fullscreenEnabled;
  fullscreenButton.addEventListener("click", toggleFullscreen);
  document.addEventListener("fullscreenchange", () => {
    const active = Boolean(document.fullscreenElement);
    fullscreenButton.setAttribute("aria-pressed", String(active));
    fullscreenButton.querySelector("i").className = `fa-solid ${active ? "fa-compress" : "fa-expand"}`;
  });

  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented) return;
    if (event.target.closest("input, textarea, select, [contenteditable]")) return;
    const onControl = event.target.closest("button, a");
    const moves = {
      ArrowRight: 1,
      ArrowDown: 1,
      PageDown: 1,
      ArrowLeft: -1,
      ArrowUp: -1,
      PageUp: -1,
    };
    if (event.key in moves) show(index + moves[event.key]);
    else if (event.key === " " && !onControl) show(index + (event.shiftKey ? -1 : 1));
    else if (event.key === "Home") show(0);
    else if (event.key === "End") show(slides.length - 1);
    else if (event.key.toLowerCase() === "f" && !onControl) toggleFullscreen();
    else return;
    event.preventDefault();
  });

  let swipeStart = null;
  deck.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "touch") swipeStart = { x: event.clientX, y: event.clientY };
  });
  deck.addEventListener("pointerup", (event) => {
    if (!swipeStart) return;
    const dx = event.clientX - swipeStart.x;
    const dy = event.clientY - swipeStart.y;
    swipeStart = null;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) show(index + (dx < 0 ? 1 : -1));
  });

  const fromHash = Number.parseInt(window.location.hash.slice(1), 10);
  deck.classList.add("is-ready");
  show(Number.isFinite(fromHash) ? fromHash - 1 : 0);
})();
