(function () {
  const GERMAN_WEEKDAY_NAMES = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];

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
    const dateFormat = panel.dataset.clockDateFormat || "de_numeric";

    const timeEl = panel.querySelector("[data-clock-time]");
    const weekdayEl = panel.querySelector("[data-clock-weekday]");
    const dayEl = panel.querySelector("[data-clock-day]");
    const monthEl = panel.querySelector("[data-clock-month]");
    const yearEl = panel.querySelector("[data-clock-year]");
    const todayEl = panel.querySelector("[data-clock-today]");
    const momentLabelEl = panel.querySelector("[data-clock-moment-label]");
    const momentIconEl = panel.querySelector("[data-clock-moment-icon]");

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
    } catch (_error) {
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

    function formatToday(dayPadded, monthPadded, year) {
      if (dateFormat === "de_long") {
        return `${Number(dayPadded)}. ${GERMAN_MONTH_NAMES[Number(monthPadded) - 1]} ${year}`;
      }
      if (dateFormat === "iso") {
        return `${year}-${monthPadded}-${dayPadded}`;
      }
      if (dateFormat === "us_numeric") {
        return `${monthPadded}/${dayPadded}/${year}`;
      }
      return `${dayPadded}.${monthPadded}.${year}`;
    }

    function momentInfo(hour24) {
      let label;
      if (hour24 >= 5 && hour24 < 11) {
        label = "Ruhiger Start";
      } else if (hour24 >= 11 && hour24 < 17) {
        label = "Fokussierter Tag";
      } else if (hour24 >= 17 && hour24 < 22) {
        label = "Ruhiger Abend";
      } else {
        label = "Nachtruhe";
      }

      let icon;
      if (hour24 >= 5 && hour24 < 17) {
        icon = "fa-regular fa-sun";
      } else if (hour24 >= 17 && hour24 < 22) {
        icon = "fa-solid fa-cloud-sun";
      } else {
        icon = "fa-regular fa-moon";
      }

      return { label, icon };
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
      if (todayEl) {
        todayEl.textContent = formatToday(lookup.day, lookup.month, lookup.year);
      }
      const moment = momentInfo(Number(lookup.hour));
      if (momentLabelEl) {
        momentLabelEl.textContent = moment.label;
      }
      if (momentIconEl) {
        momentIconEl.className = moment.icon;
      }
    }

    update();
    // Hidden tabs skip ticks; visibilitychange below catches up on return.
    window.setInterval(() => !document.hidden && update(), 15000);
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

    // Server state digest from the last poll; unchanged state gets a tiny reply without a count.
    let liveFingerprint = "";

    const refreshUnreadMessages = async () => {
      const liveUrl = liveRegion.dataset.homeMessagesLiveUrl;
      if (!liveUrl) {
        return;
      }

      const url = new URL(liveUrl, window.location.origin);
      url.searchParams.set("_", Date.now().toString());
      if (liveFingerprint) {
        url.searchParams.set("fp", liveFingerprint);
      }

      try {
        const response = await fetch(url, {
          method: "GET",
          credentials: "same-origin",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            Accept: "application/json",
          },
        });

        if (!response.ok) {
          return;
        }

        const data = await response.json();
        if (!data.ok) {
          return;
        }

        liveFingerprint = data.fingerprint || "";
        if (!data.unchanged) {
          setBadgeCount(data.unread_total);
        }
      } catch (_error) {
        // Lokale Netzwerk-/Reload-Unterbrechungen sollen die Home-Seite nicht stören.
      }
    };

    window.setInterval(() => !document.hidden && refreshUnreadMessages(), 5000);
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
    } catch (_error) {
      return null;
    }
  }

  function initDashboardWeather() {
    const widget = document.querySelector('[data-dashboard-widget][data-widget-id="weather"]');
    if (!widget) {
      return;
    }

    const status = widget.querySelector("[data-dashboard-weather-status]");
    const setText = (selector, value, suffix = "") => {
      const element = widget.querySelector(selector);
      if (element && value !== undefined && value !== null) {
        element.textContent = `${value}${suffix}`;
      }
    };
    const setIcon = (selector, icon) => {
      const element = widget.querySelector(selector);
      if (element && /^fa-[a-z0-9-]+$/.test(icon || "")) {
        element.className = `fa-solid ${icon} weather-icon`;
      }
    };

    async function loadWeather() {
      const hiddenByUser = widget.hidden || widget.classList.contains("is-hidden-by-user");
      const loadedAt = Number(widget.dataset.dashboardWeatherLoadedAt || 0);
      const isFresh = loadedAt && Date.now() - loadedAt < 10 * 60 * 1000;
      if (
        hiddenByUser ||
        isFresh ||
        widget.dataset.dashboardWeatherState === "loading" ||
        !widget.dataset.dashboardWeatherUrl
      ) {
        return;
      }

      widget.dataset.dashboardWeatherState = "loading";
      widget.setAttribute("aria-busy", "true");
      try {
        const response = await fetch(widget.dataset.dashboardWeatherUrl, {
          method: "GET",
          credentials: "same-origin",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            Accept: "application/json",
          },
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok || !data.weather) {
          throw new Error("Wetterdaten konnten nicht geladen werden.");
        }

        const today = data.weather.today || {};
        const tomorrow = data.weather.tomorrow || {};
        setIcon("[data-dashboard-weather-today-icon]", today.icon);
        setText("[data-dashboard-weather-today-temperature]", today.temperature, "°");
        setText("[data-dashboard-weather-today-description]", today.description);
        setText("[data-dashboard-weather-today-feels-like]", today.feels_like);
        setText("[data-dashboard-weather-tomorrow-day]", tomorrow.day);
        setIcon("[data-dashboard-weather-tomorrow-icon]", tomorrow.icon);
        setText("[data-dashboard-weather-tomorrow-high]", tomorrow.high, "°");
        setText("[data-dashboard-weather-tomorrow-description]", tomorrow.description);
        setText("[data-dashboard-weather-tomorrow-low]", tomorrow.low);
        setText("[data-dashboard-weather-tomorrow-rain]", tomorrow.rain);

        widget.dataset.dashboardWeatherState = "loaded";
        widget.dataset.dashboardWeatherLoadedAt = Date.now().toString();
        if (status) {
          status.textContent = "Wetterdaten aktualisiert.";
        }
      } catch (_error) {
        widget.dataset.dashboardWeatherState = "error";
        setText("[data-dashboard-weather-today-description]", "Wetter derzeit nicht verfügbar.");
        setText("[data-dashboard-weather-tomorrow-description]", "Vorhersage derzeit nicht verfügbar.");
        if (status) {
          status.textContent = "Wetterdaten konnten nicht geladen werden.";
        }
      } finally {
        widget.removeAttribute("aria-busy");
      }
    }

    loadWeather();
    document.addEventListener("dashboard:layout-saved", loadWeather);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        loadWeather();
      }
    });
  }

  function activateStatsPeriod(widget, period) {
    widget.querySelectorAll("[data-stats-period-toggle]").forEach((button) => {
      const isActive = button.dataset.statsPeriodToggle === period;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-selected", String(isActive));
    });
    widget.querySelectorAll("[data-stats-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.statsPanel !== period;
    });
  }

  function initDashboardStatsPeriodToggle() {
    const widget = document.querySelector('[data-dashboard-widget][data-widget-id="stats"]');
    if (!widget) {
      return;
    }
    widget.querySelectorAll("[data-stats-period-toggle]").forEach((button) => {
      button.addEventListener("click", () => activateStatsPeriod(widget, button.dataset.statsPeriodToggle));
    });
  }

  // Packs widgets like a masonry wall on multi-column widths: each widget spans as many tiny
  // grid rows as it is tall, so short widgets no longer stretch to their row's tallest neighbour.
  function initDashboardMasonry(grid) {
    const ROW_HEIGHT = 2;
    const multiColumn = window.matchMedia("(min-width: 760px)");
    let frame = 0;

    function layout() {
      frame = 0;
      const enabled = multiColumn.matches;
      grid.classList.toggle("is-masonry", enabled);
      Array.from(grid.children).forEach((child) => {
        if (!enabled || child.hidden) {
          child.style.gridRowEnd = "";
          return;
        }
        const gap = parseFloat(window.getComputedStyle(child).marginBottom) || 0;
        const height = child.getBoundingClientRect().height;
        child.style.gridRowEnd = `span ${Math.max(1, Math.ceil((height + gap) / ROW_HEIGHT))}`;
      });
    }

    function schedule() {
      if (!frame) {
        frame = window.setTimeout(layout, 16);
      }
    }

    if ("ResizeObserver" in window) {
      const observer = new ResizeObserver(schedule);
      Array.from(grid.children).forEach((child) => observer.observe(child));
    }
    multiColumn.addEventListener("change", schedule);
    window.addEventListener("load", schedule, { once: true });
    schedule();
    return schedule;
  }

  const RECENT_SECTIONS_STORAGE_KEY = "lunora-recent-sections";
  const VISIBLE_RECENT_TILES = 4;

  function initRecentTools() {
    const container = document.querySelector("[data-recent-tools]");
    if (!container) {
      return;
    }
    let recent = [];
    try {
      recent = JSON.parse(localStorage.getItem(RECENT_SECTIONS_STORAGE_KEY) || "[]");
    } catch (_error) {
      recent = [];
    }
    if (!Array.isArray(recent) || !recent.length) {
      return;
    }
    const tiles = Array.from(container.querySelectorAll("[data-recent-path]"));
    const rank = (tile) => {
      const index = recent.indexOf(tile.dataset.recentPath);
      return index === -1 ? Infinity : index;
    };
    tiles
      .map((tile, index) => ({ tile, index, rank: rank(tile) }))
      .sort((a, b) => a.rank - b.rank || a.index - b.index)
      .forEach(({ tile }, position) => {
        tile.hidden = position >= VISIBLE_RECENT_TILES;
        container.appendChild(tile);
      });
  }

  const WIDGET_SIZES = ["small", "medium", "large", "full"];
  const WIDGET_SIZE_LABELS = { small: "klein", medium: "mittel", large: "groß", full: "volle Breite" };
  const WIDGET_TONES = ["sand", "blue", "green", "violet", "red"];

  function initDashboardCustomizer(relayout) {
    const customizer = document.querySelector("[data-dashboard-customizer]");
    const grid = document.querySelector("[data-dashboard-grid]");
    if (!customizer || !grid) {
      return;
    }

    const editButton = customizer.querySelector("[data-dashboard-edit]");
    const emptyEditButton = document.querySelector("[data-dashboard-empty-edit]");
    const actionBar = customizer.querySelector("[data-dashboard-edit-actions]");
    const editPanel = customizer.querySelector("[data-dashboard-visibility-panel]");
    const saveButton = customizer.querySelector("[data-dashboard-save]");
    const cancelButton = customizer.querySelector("[data-dashboard-cancel]");
    const resetButton = customizer.querySelector("[data-dashboard-reset]");
    const liveRegion = customizer.querySelector("[data-dashboard-live]");
    const initialLayout = readJsonScript("dashboard-layout-data");
    const defaultLayout = readJsonScript("dashboard-default-layout-data");
    const presets = readJsonScript("dashboard-presets-data") || [];
    const widgets = Array.from(grid.querySelectorAll("[data-dashboard-widget]"));
    const availableIds = widgets.map((widget) => widget.dataset.widgetId);
    const availableIdSet = new Set(availableIds);
    const toggleInputs = Array.from(editPanel.querySelectorAll("input[type='checkbox']"));
    const csrfToken = document.querySelector("meta[name='csrf-token']")?.content || "";

    if (!initialLayout || !defaultLayout || !availableIds.length) {
      return;
    }

    let editing = false;
    let savedLayout = cloneLayout(initialLayout);
    let draftLayout = cloneLayout(initialLayout);
    let dragId = null;

    function cloneLayout(layout) {
      return JSON.parse(JSON.stringify(layout));
    }

    function visibleOrder(layout) {
      return layout.order.filter((id) => availableIdSet.has(id));
    }

    function setAnnouncement(message) {
      if (liveRegion) {
        liveRegion.textContent = message;
      }
    }

    function labelFor(widgetId) {
      return widgetById(widgetId)?.dataset.widgetLabel || "Widget";
    }

    function widgetById(widgetId) {
      return widgets.find((widget) => widget.dataset.widgetId === widgetId) || null;
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
      setAnnouncement(`${labelFor(widgetId)} wurde ${direction < 0 ? "nach vorne" : "nach hinten"} verschoben.`);
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

    // Preview a widget's draft settings in place: size and tone classes, list limits,
    // weather's second day, the stats period and which quick-action tiles show.
    function applyWidgetSettings(widget) {
      const settings = draftLayout.widgets[widget.dataset.widgetId];
      if (!settings) {
        return;
      }
      WIDGET_SIZES.forEach((size) => widget.classList.toggle(`dashboard-widget--${size}`, settings.size === size));
      WIDGET_TONES.forEach((tone) => widget.classList.toggle(`tone-${tone}`, settings.tone === tone));
      widget.classList.toggle("has-tone", Boolean(settings.tone));

      if ("limit" in settings) {
        widget.querySelectorAll("[data-dashboard-item]").forEach((item, index) => {
          item.hidden = index >= settings.limit;
        });
      }
      if ("show_tomorrow" in settings) {
        const tomorrow = widget.querySelector("[data-dashboard-weather-tomorrow]");
        if (tomorrow) {
          tomorrow.hidden = !settings.show_tomorrow;
        }
      }
      if ("period" in settings) {
        activateStatsPeriod(widget, settings.period);
      }
      if ("hidden_tiles" in settings) {
        const hiddenTiles = new Set(settings.hidden_tiles);
        widget.querySelectorAll("[data-quick-tile]").forEach((tile) => {
          tile.hidden = hiddenTiles.has(tile.dataset.quickTile);
        });
      }

      widget.querySelectorAll("[data-dashboard-setting]").forEach((input) => {
        const key = input.dataset.dashboardSetting;
        if (key === "size" || key === "tone") {
          input.checked = settings[key] === input.value;
        } else if (key === "limit" || key === "period") {
          input.value = String(settings[key]);
        } else if (key === "show_tomorrow") {
          input.checked = Boolean(settings.show_tomorrow);
        } else if (key === "tile") {
          input.checked = !settings.hidden_tiles.includes(input.value);
        }
      });
    }

    function updateSetting(widget, input) {
      const widgetId = widget.dataset.widgetId;
      const settings = draftLayout.widgets[widgetId];
      const key = input.dataset.dashboardSetting;
      if (!settings) {
        return;
      }
      if (key === "size" || key === "tone" || key === "period") {
        settings[key] = input.value;
      } else if (key === "limit") {
        settings.limit = Number(input.value);
      } else if (key === "show_tomorrow") {
        settings.show_tomorrow = input.checked;
      } else if (key === "tile") {
        settings.hidden_tiles = Array.from(widget.querySelectorAll("[data-dashboard-setting='tile']"))
          .filter((tileInput) => !tileInput.checked)
          .map((tileInput) => tileInput.value);
      }
      applyWidgetSettings(widget);
      relayout();
      if (key === "size") {
        setAnnouncement(`${labelFor(widgetId)} ist jetzt ${WIDGET_SIZE_LABELS[settings.size] || settings.size}.`);
      } else {
        setAnnouncement(`${labelFor(widgetId)} angepasst.`);
      }
    }

    function closeSettingsPanels() {
      grid.querySelectorAll("[data-dashboard-widget-settings]").forEach((panel) => {
        panel.hidden = true;
      });
      grid.querySelectorAll("[data-dashboard-settings-toggle]").forEach((button) => {
        button.setAttribute("aria-expanded", "false");
      });
    }

    function renderDraft({ reorder = true } = {}) {
      const fragment = document.createDocumentFragment();
      const hiddenIds = new Set(draftLayout.hidden.filter((id) => availableIdSet.has(id)));
      visibleOrder(draftLayout).forEach((id) => {
        const widget = widgetById(id);
        if (!widget) {
          return;
        }
        const isHidden = hiddenIds.has(id);
        widget.hidden = isHidden && !editing;
        widget.classList.toggle("is-hidden-by-user", isHidden);
        applyWidgetSettings(widget);
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
      if (!editing) {
        closeSettingsPanels();
      }
      relayout();
    }

    function setEditing(nextEditing) {
      editing = nextEditing;
      grid.classList.toggle("is-editing", editing);
      editButton.hidden = editing;
      actionBar.hidden = !editing;
      editPanel.hidden = !editing;
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
            Accept: "application/json",
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
        document.dispatchEvent(new CustomEvent("dashboard:layout-saved"));
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

    editPanel.querySelectorAll("[data-dashboard-preset]").forEach((button) => {
      button.addEventListener("click", () => {
        const preset = presets.find((item) => item.id === button.dataset.dashboardPreset);
        if (!preset) {
          return;
        }
        draftLayout = cloneLayout(preset.layout);
        renderDraft();
        setAnnouncement(`Vorlage „${preset.label}“ als Entwurf geladen. Speichern übernimmt sie.`);
      });
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
        draftLayout.hidden = draftLayout.order.filter((id) => hidden.has(id));
        renderDraft();
      });
    });

    grid.addEventListener("change", (event) => {
      const input = event.target.closest("[data-dashboard-setting]");
      const widget = input?.closest("[data-dashboard-widget]");
      if (editing && input && widget) {
        updateSetting(widget, input);
      }
    });

    grid.addEventListener(
      "click",
      (event) => {
        if (!editing) {
          return;
        }
        const moveButton = event.target.closest("[data-dashboard-move]");
        if (moveButton) {
          const widget = moveButton.closest("[data-dashboard-widget]");
          moveAvailableWidget(widget.dataset.widgetId, Number(moveButton.dataset.dashboardMove));
          return;
        }
        const settingsButton = event.target.closest("[data-dashboard-settings-toggle]");
        if (settingsButton) {
          const panel = document.getElementById(settingsButton.getAttribute("aria-controls"));
          const opening = panel?.hidden;
          if (panel) {
            panel.hidden = !opening;
            settingsButton.setAttribute("aria-expanded", String(opening));
            relayout();
          }
          return;
        }
        // Widget content (links, task/notification forms) stays inert while editing.
        if (!event.target.closest("[data-dashboard-widget-settings]") && event.target.closest("a, button")) {
          event.preventDefault();
        }
      },
      true,
    );

    // Dragging starts from a widget's edit toolbar only, so the settings controls stay usable.
    grid.addEventListener("pointerdown", (event) => {
      const widget = event.target.closest("[data-dashboard-widget]");
      if (!widget) {
        return;
      }
      const fromToolbar =
        editing && event.target.closest("[data-dashboard-widget-controls]") && !event.target.closest("button");
      widget.setAttribute("draggable", fromToolbar ? "true" : "false");
    });

    grid.addEventListener("dragstart", (event) => {
      const widget = event.target.closest("[data-dashboard-widget]");
      if (!editing || !widget) {
        event.preventDefault();
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
      grid.querySelectorAll("[data-dashboard-widget]").forEach((widget) => widget.setAttribute("draggable", "false"));
      dragId = null;
    });

    document.addEventListener("keydown", (event) => {
      if (editing && event.key === "Escape") {
        cancelEditing();
      }
    });

    renderDraft({ reorder: false });
  }

  const dashboardGrid = document.querySelector("[data-dashboard-grid]");
  const relayoutDashboard = dashboardGrid ? initDashboardMasonry(dashboardGrid) : () => {};

  initUnreadMessagesBadge();
  initDashboardWeather();
  initDashboardStatsPeriodToggle();
  initRecentTools();
  initDashboardCustomizer(relayoutDashboard);
})();
