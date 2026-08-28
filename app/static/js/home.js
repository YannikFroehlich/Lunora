(function () {
  const GERMAN_WEEKDAY_NAMES = [
    "Sonntag",
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
  ];

  const GERMAN_MONTH_NAMES = [
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
  ];

  function initDashboardClock(panel) {
    const timeZone = panel.dataset.clockTimezone || undefined;
    const timeFormat = panel.dataset.clockTimeFormat || "24h";

    const timeEl = panel.querySelector("[data-clock-time]");
    const weekdayEl = panel.querySelector("[data-clock-weekday]");
    const dayEl = panel.querySelector("[data-clock-day]");
    const monthEl = panel.querySelector("[data-clock-month]");
    const yearEl = panel.querySelector("[data-clock-year]");

    let partsFormatter;
    try {
      partsFormatter = new Intl.DateTimeFormat("en-US", {
        timeZone,
        hourCycle: "h23",
        hour: "2-digit",
        minute: "2-digit",
        weekday: "short",
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
      });
    } catch (error) {
      // Unknown/unsupported timezone identifier: fall back to the browser's local time.
      partsFormatter = new Intl.DateTimeFormat("en-US", {
        hourCycle: "h23",
        hour: "2-digit",
        minute: "2-digit",
        weekday: "short",
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
      });
    }

    const WEEKDAY_SHORT_TO_INDEX = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

    function formatTime(hour24, minute) {
      if (timeFormat === "12h") {
        const hour12 = hour24 % 12 === 0 ? 12 : hour24 % 12;
        const period = hour24 < 12 ? "AM" : "PM";
        return `${hour12}:${minute} ${period}`;
      }
      return `${String(hour24).padStart(2, "0")}:${minute}`;
    }

    function update() {
      const parts = partsFormatter.formatToParts(new Date());
      const lookup = {};
      parts.forEach((part) => {
        lookup[part.type] = part.value;
      });

      if (timeEl) {
        timeEl.textContent = formatTime(Number(lookup.hour), lookup.minute);
      }
      if (weekdayEl) {
        weekdayEl.textContent = GERMAN_WEEKDAY_NAMES[WEEKDAY_SHORT_TO_INDEX[lookup.weekday]];
      }
      if (dayEl) {
        dayEl.textContent = lookup.day;
      }
      if (monthEl) {
        monthEl.textContent = GERMAN_MONTH_NAMES[Number(lookup.month) - 1];
      }
      if (yearEl) {
        yearEl.textContent = lookup.year;
      }
    }

    update();
    window.setInterval(update, 15000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        update();
      }
    });
  }

  document.querySelectorAll("[data-home-clock]").forEach(initDashboardClock);

  function initUnreadMessagesBadge() {
    const liveRegion = document.querySelector("[data-home-messages-live-url]");
    const badge = document.querySelector("[data-home-messages-badge]");

    if (!liveRegion || !badge) {
      return;
    }

    const setBadgeCount = (count) => {
      const unreadCount = Math.max(0, Number(count || 0));
      badge.textContent = unreadCount.toString();
      badge.setAttribute("aria-label", `${unreadCount} ungelesene Nachrichten`);
      badge.classList.toggle("is-hidden", unreadCount === 0);
    };

    const refreshUnreadMessages = async () => {
      const liveUrl = liveRegion.dataset.homeMessagesLiveUrl;
      if (!liveUrl) {
        return;
      }

      const url = new URL(liveUrl, window.location.origin);
      url.searchParams.set("_", Date.now().toString());

      try {
        const response = await fetch(url, {
          method: "GET",
          credentials: "same-origin",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
          },
        });

        if (!response.ok) {
          return;
        }

        const data = await response.json();
        if (!data.ok) {
          return;
        }

        setBadgeCount(data.unread_total);
      } catch (error) {
        // Lokale Netzwerk-/Reload-Unterbrechungen sollen die Home-Seite nicht stören.
      }
    };

    window.setInterval(refreshUnreadMessages, 5000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        refreshUnreadMessages();
      }
    });
  }

  function readJsonScript(id) {
    const el = document.getElementById(id);
    if (!el) {
      return null;
    }
    try {
      return JSON.parse(el.textContent);
    } catch (error) {
      return null;
    }
  }

  function initDashboardCustomizer() {
    const customizer = document.querySelector("[data-dashboard-customizer]");
    const grid = document.querySelector("[data-dashboard-grid]");
    if (!customizer || !grid) {
      return;
    }

    const editButton = customizer.querySelector("[data-dashboard-edit]");
    const emptyEditButton = document.querySelector("[data-dashboard-empty-edit]");
    const actionBar = customizer.querySelector("[data-dashboard-edit-actions]");
    const visibilityPanel = customizer.querySelector("[data-dashboard-visibility-panel]");
    const saveButton = customizer.querySelector("[data-dashboard-save]");
    const cancelButton = customizer.querySelector("[data-dashboard-cancel]");
    const resetButton = customizer.querySelector("[data-dashboard-reset]");
    const liveRegion = customizer.querySelector("[data-dashboard-live]");
    const initialLayout = readJsonScript("dashboard-layout-data");
    const defaultLayout = readJsonScript("dashboard-default-layout-data");
    const widgets = Array.from(grid.querySelectorAll("[data-dashboard-widget]"));
    const availableIds = widgets.map((widget) => widget.dataset.widgetId);
    const availableIdSet = new Set(availableIds);
    const toggleInputs = Array.from(visibilityPanel.querySelectorAll("input[type='checkbox']"));
    const csrfToken = document.querySelector("meta[name='csrf-token']")?.content || "";

    if (!initialLayout || !defaultLayout || !availableIds.length) {
      return;
    }

    let editing = false;
    let savedLayout = cloneLayout(initialLayout);
    let draftLayout = cloneLayout(initialLayout);
    let dragId = null;

    function cloneLayout(layout) {
      return {
        version: 1,
        order: Array.isArray(layout.order) ? layout.order.slice() : [],
        hidden: Array.isArray(layout.hidden) ? layout.hidden.slice() : [],
      };
    }

    function visibleOrder(layout) {
      return layout.order.filter((id) => availableIdSet.has(id));
    }

    function hiddenSet(layout) {
      return new Set(layout.hidden.filter((id) => availableIdSet.has(id)));
    }

    function setAnnouncement(message) {
      if (liveRegion) {
        liveRegion.textContent = message;
      }
    }

    function mergeAvailableOrder(layout, orderedAvailableIds) {
      const queue = orderedAvailableIds.slice();
      const used = new Set();
      const merged = layout.order.map((id) => {
        if (!availableIdSet.has(id)) {
          return id;
        }
        const nextId = queue.shift();
        used.add(nextId);
        return nextId;
      });
      orderedAvailableIds.forEach((id) => {
        if (!used.has(id)) {
          merged.push(id);
        }
      });
      layout.order = merged;
    }

    function moveAvailableWidget(widgetId, direction) {
      const order = visibleOrder(draftLayout);
      const index = order.indexOf(widgetId);
      const targetIndex = index + direction;
      if (index === -1 || targetIndex < 0 || targetIndex >= order.length) {
        return;
      }
      order.splice(index, 1);
      order.splice(targetIndex, 0, widgetId);
      mergeAvailableOrder(draftLayout, order);
      renderDraft();
      const label = labelFor(widgetId);
      setAnnouncement(`${label} wurde ${direction < 0 ? "nach vorne" : "nach hinten"} verschoben.`);
    }

    function moveBefore(draggedId, targetId) {
      if (!draggedId || draggedId === targetId) {
        return;
      }
      const order = visibleOrder(draftLayout).filter((id) => id !== draggedId);
      const targetIndex = order.indexOf(targetId);
      order.splice(targetIndex === -1 ? order.length : targetIndex, 0, draggedId);
      mergeAvailableOrder(draftLayout, order);
      renderDraft();
      setAnnouncement(`${labelFor(draggedId)} wurde verschoben.`);
    }

    function labelFor(widgetId) {
      return widgetById(widgetId)?.dataset.widgetLabel || "Widget";
    }

    function widgetById(widgetId) {
      return widgets.find((widget) => widget.dataset.widgetId === widgetId) || null;
    }

    function renderDraft({ reorder = true } = {}) {
      const fragment = document.createDocumentFragment();
      const hiddenIds = hiddenSet(draftLayout);
      visibleOrder(draftLayout).forEach((id) => {
        const widget = widgetById(id);
        if (!widget) {
          return;
        }
        const isHidden = hiddenIds.has(id);
        widget.hidden = isHidden && !editing;
        widget.classList.toggle("is-hidden-by-user", isHidden);
        widget.setAttribute("draggable", editing ? "true" : "false");
        if (reorder) {
          fragment.appendChild(widget);
        }
      });
      const emptyState = grid.querySelector("[data-dashboard-empty-state]");
      if (reorder) {
        grid.prepend(fragment);
      }
      const allHidden = availableIds.every((id) => hiddenIds.has(id));
      if (emptyState) {
        emptyState.hidden = editing || !allHidden;
      }
      toggleInputs.forEach((input) => {
        input.checked = !hiddenIds.has(input.value);
      });
      grid.querySelectorAll("[data-dashboard-widget-controls]").forEach((controls) => {
        controls.hidden = !editing;
      });
    }

    function setEditing(nextEditing) {
      editing = nextEditing;
      grid.classList.toggle("is-editing", editing);
      editButton.hidden = editing;
      actionBar.hidden = !editing;
      visibilityPanel.hidden = !editing;
      renderDraft();
      setAnnouncement(editing ? "Bearbeitungsmodus aktiviert." : "Bearbeitungsmodus beendet.");
    }

    function cancelEditing() {
      draftLayout = cloneLayout(savedLayout);
      setEditing(false);
      setAnnouncement("Änderungen verworfen.");
    }

    async function saveDraft() {
      saveButton.disabled = true;
      try {
        const response = await fetch(customizer.dataset.dashboardLayoutUrl, {
          method: "PATCH",
          credentials: "same-origin",
          headers: {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken,
            "X-Requested-With": "XMLHttpRequest",
          },
          body: JSON.stringify(draftLayout),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          throw new Error(data.error || "Layout konnte nicht gespeichert werden.");
        }
        savedLayout = cloneLayout(data.layout);
        draftLayout = cloneLayout(data.layout);
        setEditing(false);
        setAnnouncement("Dashboard gespeichert.");
      } catch (error) {
        setAnnouncement(error.message || "Dashboard konnte nicht gespeichert werden.");
      } finally {
        saveButton.disabled = false;
      }
    }

    editButton.addEventListener("click", () => setEditing(true));
    if (emptyEditButton) {
      emptyEditButton.addEventListener("click", () => setEditing(true));
    }
    cancelButton.addEventListener("click", cancelEditing);
    saveButton.addEventListener("click", saveDraft);
    resetButton.addEventListener("click", () => {
      draftLayout = cloneLayout(defaultLayout);
      renderDraft();
      setAnnouncement("Standardanordnung als Entwurf wiederhergestellt.");
    });

    toggleInputs.forEach((input) => {
      input.addEventListener("change", () => {
        const hidden = new Set(draftLayout.hidden);
        if (input.checked) {
          hidden.delete(input.value);
          setAnnouncement(`${labelFor(input.value)} eingeblendet.`);
        } else {
          hidden.add(input.value);
          setAnnouncement(`${labelFor(input.value)} ausgeblendet.`);
        }
        draftLayout.hidden = savedLayout.order.filter((id) => hidden.has(id));
        renderDraft();
      });
    });

    grid.addEventListener("click", (event) => {
      if (!editing) {
        return;
      }
      const moveButton = event.target.closest("[data-dashboard-move]");
      if (moveButton) {
        const widget = moveButton.closest("[data-dashboard-widget]");
        moveAvailableWidget(widget.dataset.widgetId, Number(moveButton.dataset.dashboardMove));
        return;
      }
      if (event.target.closest("a")) {
        event.preventDefault();
      }
    }, true);

    grid.addEventListener("dragstart", (event) => {
      if (!editing) {
        event.preventDefault();
        return;
      }
      const widget = event.target.closest("[data-dashboard-widget]");
      if (!widget) {
        return;
      }
      dragId = widget.dataset.widgetId;
      widget.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", dragId);
    });

    grid.addEventListener("dragover", (event) => {
      if (editing && event.target.closest("[data-dashboard-widget]")) {
        event.preventDefault();
      }
    });

    grid.addEventListener("drop", (event) => {
      if (!editing) {
        return;
      }
      event.preventDefault();
      const target = event.target.closest("[data-dashboard-widget]");
      moveBefore(dragId || event.dataTransfer.getData("text/plain"), target?.dataset.widgetId);
    });

    grid.addEventListener("dragend", () => {
      grid.querySelectorAll(".is-dragging").forEach((widget) => widget.classList.remove("is-dragging"));
      dragId = null;
    });

    document.addEventListener("keydown", (event) => {
      if (editing && event.key === "Escape") {
        cancelEditing();
      }
    });

    renderDraft({ reorder: false });
  }

  initUnreadMessagesBadge();
  initDashboardCustomizer();
})();
