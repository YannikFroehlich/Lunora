// Cooking mode on the recipe page: one preparation step at a time, large, with the screen kept on
// through the Screen Wake Lock API where the browser has it. The page is a soft region, so every
// element is looked up again when used and events are delegated to the document.
(function () {
  let index = 0;
  let wakeLock = null;

  function dialog() {
    return document.querySelector("[data-cook-dialog]");
  }

  function steps() {
    try {
      return JSON.parse(document.getElementById("recipe-cook-steps")?.textContent || "[]");
    } catch (_error) {
      return [];
    }
  }

  function render() {
    const element = dialog();
    const all = steps();
    if (!element || !all.length) return;
    index = Math.min(Math.max(index, 0), all.length - 1);
    element.querySelector("[data-cook-step]").textContent = all[index];
    element.querySelector("[data-cook-progress]").textContent = `Schritt ${index + 1} von ${all.length}`;
    element.querySelector("[data-cook-prev]").disabled = index === 0;
    const next = element.querySelector("[data-cook-next]");
    const last = index === all.length - 1;
    next.replaceChildren(
      document.createTextNode(last ? "Fertig " : "Weiter "),
      icon(last ? "fa-check" : "fa-arrow-right"),
    );
  }

  function icon(name) {
    const element = document.createElement("i");
    element.className = `fa-solid ${name}`;
    element.setAttribute("aria-hidden", "true");
    return element;
  }

  async function keepAwake() {
    if (!("wakeLock" in navigator) || document.hidden || !dialog()?.open) return;
    try {
      wakeLock = await navigator.wakeLock.request("screen");
      const badge = dialog()?.querySelector("[data-cook-wake]");
      if (badge) badge.hidden = false;
      wakeLock.addEventListener("release", () => {
        wakeLock = null;
      });
    } catch (_error) {
      // Battery saver or a denied permission: cooking works anyway, the screen may just dim.
    }
  }

  function open() {
    const element = dialog();
    if (!element) return;
    index = 0;
    render();
    element.showModal();
    keepAwake();
  }

  function close() {
    dialog()?.close();
  }

  function move(step) {
    const all = steps();
    if (step > 0 && index === all.length - 1) {
      close();
      return;
    }
    index += step;
    render();
  }

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-cook-open]")) open();
    else if (event.target.closest("[data-cook-close]")) close();
    else if (event.target.closest("[data-cook-prev]")) move(-1);
    else if (event.target.closest("[data-cook-next]")) move(1);
  });

  document.addEventListener("keydown", (event) => {
    if (!dialog()?.open || event.target.closest("summary")) return;
    if (event.key === "ArrowRight" || event.key === "PageDown") {
      event.preventDefault();
      move(1);
    } else if (event.key === "ArrowLeft" || event.key === "PageUp") {
      event.preventDefault();
      move(-1);
    }
  });

  // The dialog's own close (Escape, the button, the last step) lets the screen dim again.
  document.addEventListener(
    "close",
    (event) => {
      if (!event.target.matches?.("[data-cook-dialog]")) return;
      wakeLock?.release().catch(() => {});
      wakeLock = null;
    },
    true,
  );

  // Browsers drop the lock when the tab is hidden; take it again on return.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !wakeLock) keepAwake();
  });
})();
