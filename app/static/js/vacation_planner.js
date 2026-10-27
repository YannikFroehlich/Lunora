(function () {
  const shell = document.querySelector("[data-vacation-preview-url]");
  if (!shell) {
    return;
  }

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const previewUrl = shell.dataset.vacationPreviewUrl;

  function setPreview(form, required, details) {
    const output = form.querySelector("[data-preview-output]");
    if (!output) {
      return;
    }
    output.querySelector("[data-preview-required]").textContent = required;
    output.querySelector("[data-preview-details]").textContent = details;
  }

  function overlapLabel(overlaps) {
    if (!overlaps.length) {
      return "";
    }
    const names = overlaps.map((overlap) => overlap.name).join(", ");
    return ` · Überschneidung: ${names}`;
  }

  async function updatePreview(form) {
    const startDate = form.querySelector('[name="start_date"]')?.value;
    const endDate = form.querySelector('[name="end_date"]')?.value;
    if (!startDate || !endDate) {
      setPreview(form, "-", "Start- und Enddatum wählen");
      return;
    }

    setPreview(form, "Berechne ...", "Feiertage und Wochenenden werden geprüft");
    const vacationId = form.querySelector('[name="period_id"]')?.value || null;
    try {
      const response = await fetch(previewUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify({
          start_date: startDate,
          end_date: endDate,
          vacation_id: vacationId,
        }),
      });
      const data = await response.json();
      if (!data.ok) {
        setPreview(form, "-", data.error || "Vorschau nicht verfügbar");
        return;
      }
      const missingYears = data.missing_years.length ? ` · fehlende Jahre: ${data.missing_years.join(", ")}` : "";
      setPreview(
        form,
        `${data.required_days_label} Tage`,
        `${data.calendar_days} Kalender · ${data.weekend_days} Wochenende · ${data.holiday_count} Feiertage${missingYears}${overlapLabel(data.overlaps)}`,
      );
    } catch (_error) {
      setPreview(form, "-", "Vorschau konnte nicht geladen werden");
    }
  }

  for (const form of document.querySelectorAll("[data-preview-form]")) {
    const trigger = () => updatePreview(form);
    form.querySelector('[name="start_date"]')?.addEventListener("change", trigger);
    form.querySelector('[name="end_date"]')?.addEventListener("change", trigger);
  }

  function focusPlannerArea(kind) {
    const target =
      kind === "year"
        ? document.querySelector("[data-vacation-year-panel]")
        : document.querySelector("[data-vacation-period-panel]");

    if (!target) {
      return;
    }

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    target.scrollIntoView({ block: "start", behavior: reduceMotion ? "auto" : "smooth" });

    const focusTarget =
      kind === "year"
        ? target.querySelector('input[name="allowance_days"], select[name="subdivision"], button[type="submit"]')
        : target.querySelector('input[name="start_date"], select[name="name"], button[type="submit"]:not(:disabled)');

    window.setTimeout(() => focusTarget?.focus({ preventScroll: true }), reduceMotion ? 0 : 220);
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-vacation-focus]");
    if (!trigger) {
      return;
    }

    event.preventDefault();
    focusPlannerArea(trigger.dataset.vacationFocus);
  });

  // Month navigation within the same year only swaps the calendar instead of reloading the page;
  // a different year changes every panel, so that link navigates normally (its #anchor keeps the scroll spot).
  async function showMonth(url) {
    const response = await fetch(url, { headers: { Accept: "text/html" } });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    const fresh = page.querySelector("[data-vacation-calendar]");
    if (!fresh) {
      throw new Error("calendar missing");
    }
    document.querySelector("[data-vacation-calendar]").replaceWith(fresh);
    const month = url.searchParams.get("month");
    for (const input of document.querySelectorAll('input[type="hidden"][name="month"]')) {
      input.value = month;
    }
    history.replaceState(null, "", url);
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest("[data-calendar-nav]");
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) {
      return;
    }
    const url = new URL(link.href);
    if (url.searchParams.get("year") !== shell.dataset.vacationYear) {
      return;
    }
    event.preventDefault();
    clearSelection();
    showMonth(url).catch(() => {
      window.location.href = url.href;
    });
  });

  // Calendar shortcuts: right-click a day, or drag across days, for a menu that opens the page's own
  // forms in a dialog. The forms post as usual; their action gets #vacation-calendar, which the
  // browser keeps across the redirect, so the page comes back to the calendar.
  const menu = document.querySelector("[data-calendar-menu]");
  const dialog = document.querySelector("[data-calendar-dialog]");
  const dialogBody = dialog?.querySelector("[data-calendar-dialog-body]");
  const CALENDAR_ANCHOR = "#vacation-calendar";
  let selection = null;
  let dragStart = null;
  let moved = null;

  function formatDay(iso, withMonth = true) {
    if (!withMonth) {
      return `${Number(iso.slice(8))}.`;
    }
    return new Date(`${iso}T00:00`).toLocaleDateString("de-DE", { day: "numeric", month: "long" });
  }

  function selectionLabel() {
    if (selection.start === selection.end) {
      return formatDay(selection.start);
    }
    const sameMonth = selection.start.slice(0, 7) === selection.end.slice(0, 7);
    return `${formatDay(selection.start, !sameMonth)} – ${formatDay(selection.end)}`;
  }

  function setSelection(start, end) {
    selection = start <= end ? { start, end } : { start: end, end: start };
    for (const day of document.querySelectorAll(".calendar-day[data-date]")) {
      day.classList.toggle("is-selected", day.dataset.date >= selection.start && day.dataset.date <= selection.end);
    }
  }

  function clearSelection() {
    selection = null;
    document.querySelectorAll(".calendar-day.is-selected").forEach((day) => day.classList.remove("is-selected"));
  }

  function hideMenu() {
    if (menu) {
      menu.hidden = true;
    }
  }

  function menuButton(icon, label, action) {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "menuitem");
    button.innerHTML = `<i class="fa-solid ${icon}" aria-hidden="true"></i>`;
    button.append(document.createTextNode(label));
    button.addEventListener("click", () => {
      hideMenu();
      action();
    });
    return button;
  }

  function inSelection(iso) {
    return iso >= selection.start && iso <= selection.end;
  }

  function openMenu(x, y) {
    const items = [];
    const ready = document.querySelector("[data-vacation-calendar]")?.dataset.yearReady === "true";
    if (!ready) {
      items.push(menuButton("fa-sliders", "Jahr zuerst einrichten", () => focusPlannerArea("year")));
    } else {
      items.push(menuButton("fa-plane-departure", "Urlaub anlegen", openNewPeriod));
      if (selection.start === selection.end) {
        items.push(menuButton("fa-star-half-stroke", "Eigenen Feiertag anlegen", openNewHoliday));
      }
    }

    const periodIds = new Set();
    document.querySelectorAll(".calendar-day[data-date]").forEach((day) => {
      if (inSelection(day.dataset.date)) {
        day.querySelectorAll("[data-period-id]").forEach((chip) => periodIds.add(chip.dataset.periodId));
      }
    });
    periodIds.forEach((id) => {
      const article = document.querySelector(`.vacation-item[data-period-id="${id}"]`);
      if (article) {
        const label = `${article.dataset.periodLabel} bearbeiten`;
        items.push(menuButton("fa-pen", label, () => openDetails(article, label)));
      }
    });
    document.querySelectorAll("[data-holiday-date]").forEach((article) => {
      if (inSelection(article.dataset.holidayDate)) {
        const verb = article.dataset.holidayKind === "custom" ? "bearbeiten" : "anpassen";
        const label = `${article.dataset.holidayLabel} ${verb}`;
        items.push(menuButton("fa-calendar-day", label, () => openDetails(article, label)));
      }
    });

    const heading = document.createElement("strong");
    heading.textContent = selectionLabel();
    menu.replaceChildren(heading, ...items);
    menu.hidden = false;
    const { width, height } = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - width - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - height - 8))}px`;
    menu.querySelector("button")?.focus();
  }

  function restoreDialogContent() {
    if (!moved) {
      return;
    }
    moved.node.querySelectorAll("form[data-original-action]").forEach((form) => {
      form.setAttribute("action", form.dataset.originalAction);
      delete form.dataset.originalAction;
    });
    moved.placeholder.replaceWith(moved.node);
    if (moved.node.tagName === "DETAILS") {
      moved.node.open = moved.wasOpen;
    }
    moved = null;
  }

  function openInDialog(node, title, focusSelector) {
    restoreDialogContent();
    // Keep the moved form's height in the page, so the calendar doesn't jump while the dialog is open.
    const placeholder = document.createElement("div");
    placeholder.style.height = `${node.offsetHeight}px`;
    node.before(placeholder);
    moved = { node, placeholder, wasOpen: node.open };
    if (node.tagName === "DETAILS") {
      node.open = true;
    }
    const forms = node.tagName === "FORM" ? [node] : node.querySelectorAll("form");
    forms.forEach((form) => {
      form.dataset.originalAction = form.getAttribute("action");
      form.setAttribute("action", form.dataset.originalAction + CALENDAR_ANCHOR);
    });
    dialogBody.replaceChildren(node);
    dialog.querySelector("[data-calendar-dialog-title]").textContent = title;
    dialog.showModal();
    node.querySelector(focusSelector || "input:not([type='hidden']), select, textarea")?.focus();
  }

  function openNewPeriod() {
    const form = document.querySelector("[data-new-period-form]");
    form.querySelector('[name="start_date"]').value = selection.start;
    const end = form.querySelector('[name="end_date"]');
    end.value = selection.end;
    end.dispatchEvent(new Event("change"));
    openInDialog(form, `Urlaub anlegen · ${selectionLabel()}`, 'select[name="name"]');
  }

  function openNewHoliday() {
    const form = document.querySelector("[data-new-holiday-form]");
    form.querySelector('[name="date"]').value = selection.start;
    openInDialog(form, `Eigener Feiertag · ${selectionLabel()}`, 'input[name="name"]');
  }

  function openDetails(article, title) {
    openInDialog(article.querySelector("details"), title);
  }

  if (menu && dialog) {
    dialog.addEventListener("close", () => {
      restoreDialogContent();
      clearSelection();
    });
    dialog.querySelector("[data-calendar-dialog-close]").addEventListener("click", () => dialog.close());

    document.addEventListener("contextmenu", (event) => {
      const day = event.target.closest(".calendar-day[data-date]");
      if (!day) {
        hideMenu();
        return;
      }
      event.preventDefault();
      if (!selection || !inSelection(day.dataset.date)) {
        setSelection(day.dataset.date, day.dataset.date);
      }
      // The ContextMenu key / Shift+F10 report no pointer position: open beside the focused day.
      const rect = day.getBoundingClientRect();
      const fromKeyboard = event.clientX === 0 && event.clientY === 0;
      openMenu(fromKeyboard ? rect.left + 12 : event.clientX, fromKeyboard ? rect.bottom - 8 : event.clientY);
    });

    document.addEventListener("mousedown", (event) => {
      if (!menu.hidden && !menu.contains(event.target)) {
        hideMenu();
        clearSelection();
      }
      // Left or right button: both can drag a range, the right one ends in the context menu.
      const day = (event.button === 0 || event.button === 2) && event.target.closest(".calendar-day[data-date]");
      if (day) {
        dragStart = day.dataset.date;
        setSelection(dragStart, dragStart);
      }
    });

    document.addEventListener("mouseover", (event) => {
      const day = dragStart && event.target.closest(".calendar-day[data-date]");
      if (day) {
        setSelection(dragStart, day.dataset.date);
      }
    });

    document.addEventListener("mouseup", (event) => {
      if (!dragStart) {
        return;
      }
      dragStart = null;
      if (event.button === 2) {
        // Windows fires contextmenu after this mouseup and opens the menu for the dragged range; where it
        // already fired on mousedown (macOS, Linux), refresh the open menu for the final range.
        if (!menu.hidden) {
          openMenu(event.clientX, event.clientY);
        }
        return;
      }
      // Touch screens have no right-click (iOS not even a long-press menu), so a tap opens the menu.
      if (selection && (selection.start !== selection.end || window.matchMedia("(pointer: coarse)").matches)) {
        openMenu(event.clientX, event.clientY);
      } else {
        clearSelection();
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !menu.hidden) {
        hideMenu();
        clearSelection();
      }
    });
    window.addEventListener("scroll", hideMenu, { passive: true });
    window.addEventListener("resize", hideMenu);
  }
})();
