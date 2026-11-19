"use strict";

// Kanban board page: card dialog, drag and drop between cells, keyboard moves and, on shared
// boards, reloading when someone else changed something. All handlers are delegated, because soft
// submits swap the whole board region.
(() => {
  const POLL_INTERVAL_MS = 5000;
  let dragged = null;
  let focusAfterSwap = null;

  const region = () => document.querySelector("[data-kanban-board]");
  const dialog = () => document.getElementById("kanban-card-dialog");

  function openCard(card) {
    const form = dialog()?.querySelector("form");
    if (!card || !form) return;
    const { cardId, title, description, color, due, assignee, column, lane } = card.dataset;
    const values = { card_id: cardId, title, description, color, due_date: due, assignee, column, lane };
    Object.entries(values).forEach(([name, value]) => {
      const field = form.elements.namedItem(name);
      if (field) field.value = value ?? "";
    });
    dialog().showModal();
    form.elements.namedItem("title")?.focus();
  }

  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-kanban-card-open]");
    if (opener) openCard(opener.closest("[data-kanban-card]"));
    else if (event.target.closest("[data-kanban-dialog-close]")) dialog()?.close();
  });

  // Puts the card into `cell` in front of `before` (null = at the end), shows it there at once and
  // sends the move; the answer swaps in the board as the server has it.
  function move(card, cell, before) {
    const form = document.getElementById("kanban-move-form");
    if (!form) return;
    const fields = { card_id: card.dataset.cardId, column_id: cell.dataset.columnId, lane_id: cell.dataset.laneId };
    fields.before_id = before?.dataset.cardId || "";
    Object.entries(fields).forEach(([name, value]) => (form.elements.namedItem(name).value = value));
    cell.insertBefore(card, before || cell.querySelector(".kanban-add"));
    form.requestSubmit();
  }

  // The card the dragged one would land in front of, from the pointer's height; null = at the end.
  function cardAt(cell, y) {
    const cards = [...cell.querySelectorAll("[data-kanban-card]:not(.is-dragging)")];
    return (
      cards.find((card) => {
        const box = card.getBoundingClientRect();
        return y < box.top + box.height / 2;
      }) || null
    );
  }

  function clearDropMarkers() {
    document
      .querySelectorAll(".kanban-cell.is-drop-target, .kanban-card.is-drop-before")
      .forEach((element) => element.classList.remove("is-drop-target", "is-drop-before"));
  }

  document.addEventListener("dragstart", (event) => {
    const card = event.target.closest?.("[data-kanban-card]");
    if (!card) return;
    dragged = card;
    card.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.dataset.cardId);
  });

  document.addEventListener("dragend", () => {
    dragged?.classList.remove("is-dragging");
    dragged = null;
    clearDropMarkers();
  });

  document.addEventListener("dragover", (event) => {
    const cell = dragged && event.target.closest?.("[data-kanban-cell]");
    if (!cell) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    clearDropMarkers();
    cell.classList.add("is-drop-target");
    cardAt(cell, event.clientY)?.classList.add("is-drop-before");
  });

  document.addEventListener("drop", (event) => {
    const cell = dragged && event.target.closest?.("[data-kanban-cell]");
    if (!cell) return;
    event.preventDefault();
    const card = dragged;
    card.classList.remove("is-dragging");
    clearDropMarkers();
    move(card, cell, cardAt(cell, event.clientY));
  });

  // Keyboard route for dragging: Alt+↑/↓ inside the cell, Alt+←/→ into the neighbouring column.
  document.addEventListener("keydown", (event) => {
    if (!event.altKey || !event.target.matches?.("[data-kanban-card-open]")) return;
    const card = event.target.closest("[data-kanban-card]");
    const cell = card.closest("[data-kanban-cell]");
    const cards = [...cell.querySelectorAll("[data-kanban-card]")];
    const index = cards.indexOf(card);
    let target = null;
    let before = null;
    if (event.key === "ArrowUp" && index > 0) {
      target = cell;
      before = cards[index - 1];
    } else if (event.key === "ArrowDown" && index < cards.length - 1) {
      target = cell;
      before = cards[index + 2] || null;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const row = [...document.querySelectorAll(`[data-kanban-cell][data-lane-id="${cell.dataset.laneId}"]`)];
      target = row[row.indexOf(cell) + (event.key === "ArrowLeft" ? -1 : 1)] || null;
    }
    if (!target) return;
    event.preventDefault();
    focusAfterSwap = card.id;
    move(card, target, before);
    card.querySelector("[data-kanban-card-open]").focus();
  });

  document.addEventListener("lunora:soft-update", () => {
    if (!focusAfterSwap) return;
    document.getElementById(focusAfterSwap)?.querySelector("[data-kanban-card-open]")?.focus();
    focusAfterSwap = null;
  });

  // Shared boards: reload when the board's version changed, unless the user is in the middle of
  // something (dragging, a dialog or menu, typing) that a swap would throw away.
  function isBusy(board) {
    const active = document.activeElement;
    return (
      dragged ||
      document.visibilityState !== "visible" ||
      document.querySelector("dialog[open]") ||
      board.querySelector("details[open]") ||
      board.querySelector("form[aria-busy=true]") ||
      (board.contains(active) && active.matches("input, textarea, select")) ||
      [...board.querySelectorAll("input[name=title]:not([value])")].some((input) => input.value)
    );
  }

  let polling = false;
  async function poll() {
    const board = region();
    if (polling || !board?.dataset.pollUrl || isBusy(board)) return;
    polling = true;
    try {
      const answer = await fetch(board.dataset.pollUrl, { credentials: "same-origin", cache: "no-store" });
      if (!answer.ok || (await answer.json()).version === board.dataset.version) return;
      const page = await fetch(window.location.href, { credentials: "same-origin", cache: "no-store" });
      if (page.ok && !isBusy(board)) {
        window.lunoraSwapSoftRegions(new DOMParser().parseFromString(await page.text(), "text/html"));
      }
    } catch {
      // Offline or a hiccup; the next round tries again.
    } finally {
      polling = false;
    }
  }

  setInterval(poll, POLL_INTERVAL_MS);
  document.addEventListener("visibilitychange", poll);
})();
