// Retro board: live polling, drag-to-group, the presented-card popup, the timer and the
// owner's settings dialog. Forms post normally (soft submit); this only adds the extras.
(function () {
  const POLL_INTERVAL_MS = 3000;
  const region = () => document.querySelector("[data-retro-board]");
  let dragged = null;
  let polling = false;

  function submitForm(form, values) {
    for (const [name, value] of Object.entries(values)) {
      form.querySelector(`[name="${name}"]`).value = value;
    }
    form.requestSubmit();
  }

  // ---- presented card: a modal for everyone; the owner closes it by clicking beside the card.
  function syncPresent() {
    const dialog = document.querySelector("[data-retro-present]");
    if (dialog && !dialog.open) dialog.showModal();
  }

  document.addEventListener("cancel", (event) => {
    const dialog = event.target.closest?.("[data-retro-present]");
    if (!dialog) return;
    event.preventDefault();
    dialog.querySelector("[data-retro-present-close]")?.requestSubmit();
  });

  document.addEventListener("click", (event) => {
    const present = event.target.closest("[data-retro-present]");
    if (present && event.target === present) {
      present.querySelector("[data-retro-present-close]")?.requestSubmit();
      return;
    }
    if (event.target.closest("[data-retro-settings-open]")) {
      document.getElementById("retro-settings-dialog")?.showModal();
    } else if (event.target.closest("[data-retro-settings-close]")) {
      event.target.closest("dialog")?.close();
    } else if (event.target.closest("[data-retro-copy]")) {
      copyInvite(event.target.closest("[data-retro-copy]"));
    }
  });

  document.getElementById("retro-settings-dialog")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) event.currentTarget.close();
  });

  async function copyInvite(button) {
    const input = document.querySelector("[data-retro-invite-url]");
    const label = button.querySelector("[data-retro-copy-label]");
    try {
      await navigator.clipboard.writeText(input.value);
    } catch {
      input.select();
      return;
    }
    label.textContent = "Kopiert";
    setTimeout(() => (label.textContent = "Kopieren"), 2000);
  }

  // ---- new cards: Ctrl/Cmd+Enter submits.
  document.addEventListener("keydown", (event) => {
    const field = event.target.closest?.("[data-retro-add] textarea, .retro-edit-form textarea");
    if (field && event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      field.form.requestSubmit();
    }
  });

  // ---- grouping: drop a card onto another card.
  document.addEventListener("dragstart", (event) => {
    const card = event.target.closest?.("[data-retro-card][draggable]");
    if (!card) return;
    dragged = card;
    card.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.dataset.retroCard);
  });

  document.addEventListener("dragend", () => {
    dragged?.classList.remove("is-dragging");
    document.querySelectorAll(".retro-card.is-drop-target").forEach((card) => card.classList.remove("is-drop-target"));
    dragged = null;
  });

  document.addEventListener("dragover", (event) => {
    const target = event.target.closest?.("[data-retro-card]");
    if (!dragged || !target || target === dragged) return;
    event.preventDefault();
    document
      .querySelectorAll(".retro-card.is-drop-target")
      .forEach((card) => card !== target && card.classList.remove("is-drop-target"));
    target.classList.add("is-drop-target");
  });

  document.addEventListener("drop", (event) => {
    const target = event.target.closest?.("[data-retro-card]");
    if (!dragged || !target || target === dragged) return;
    event.preventDefault();
    const form = dragged.querySelector(".retro-group-form");
    if (form) submitForm(form, { target_id: target.dataset.retroCard });
  });

  // ---- timer
  function tickTimer() {
    const timer = document.querySelector("[data-retro-timer]");
    if (!timer) return;
    const left = Math.max(0, Math.round((new Date(timer.dataset.endsAt) - Date.now()) / 1000));
    const minutes = Math.floor(left / 60);
    const seconds = String(left % 60).padStart(2, "0");
    timer.querySelector("[data-retro-timer-text]").textContent = `${minutes}:${seconds}`;
    timer.classList.toggle("is-over", left === 0);
  }

  // ---- live updates: reload the board when its version changed, unless someone is mid-way.
  function isBusy(board) {
    const active = document.activeElement;
    return (
      dragged ||
      document.visibilityState !== "visible" ||
      document.getElementById("retro-settings-dialog")?.open ||
      board.querySelector("details[open]") ||
      (active &&
        board.contains(active) &&
        active.matches("textarea, input[type=text], select") &&
        (active.value || active.tagName === "SELECT"))
    );
  }

  async function poll() {
    const board = region();
    if (polling || !board?.dataset.pollUrl || isBusy(board)) return;
    polling = true;
    try {
      const answer = await fetch(board.dataset.pollUrl, { credentials: "same-origin", cache: "no-store" });
      if (!answer.ok || (await answer.json()).version === board.dataset.version) return;
      const page = await fetch(window.location.href, { credentials: "same-origin", cache: "no-store" });
      if (page.ok && !page.redirected && !isBusy(board)) {
        window.lunoraSwapSoftRegions(new DOMParser().parseFromString(await page.text(), "text/html"));
      }
    } catch {
      // Offline or a hiccup; the next round tries again.
    } finally {
      polling = false;
    }
  }

  document.addEventListener("lunora:soft-update", () => {
    syncPresent();
    tickTimer();
  });
  syncPresent();
  tickTimer();
  setInterval(tickTimer, 1000);
  setInterval(poll, POLL_INTERVAL_MS);
  document.addEventListener("visibilitychange", poll);
})();
