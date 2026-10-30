const eventDialog = document.querySelector("[data-event-dialog]");

if (eventDialog) {
  const eventForm = eventDialog.querySelector("[data-event-create-form]");
  const dateInput = eventForm?.querySelector("input[name='event_date']");
  const endDateInput = eventForm?.querySelector("input[name='end_date']");
  const titleInput = eventForm?.querySelector("input[name='title']");
  const startInput = eventForm?.querySelector("input[name='start_time']");
  const endInput = eventForm?.querySelector("input[name='end_time']");
  const allDayInput = eventForm?.querySelector("input[name='is_all_day']");
  const timeFields = eventForm?.querySelectorAll("[data-event-time-field]") || [];
  const repeatSelect = eventForm?.querySelector("select[name='repeat']");
  const repeatFields = eventForm?.querySelectorAll("[data-event-repeat-field]") || [];

  const syncTimeFields = () => {
    const isAllDay = Boolean(allDayInput?.checked);

    timeFields.forEach((field) => {
      const input = field.querySelector("input");
      field.classList.toggle("is-disabled", isAllDay);
      if (input) {
        input.disabled = isAllDay;
      }
    });
  };

  const syncRepeatFields = () => {
    const isRepeating = Boolean(repeatSelect?.value) && repeatSelect.value !== "none";

    repeatFields.forEach((field) => {
      const input = field.querySelector("input");
      field.classList.toggle("is-disabled", !isRepeating);
      if (input) {
        input.disabled = !isRepeating;
      }
    });
  };

  const openEventDialog = (date = "", endDate = "", times = null) => {
    if (date && dateInput) {
      dateInput.value = date;
      if (endDateInput) {
        endDateInput.value = endDate;
      }
    }
    // A range dragged across days becomes an all-day event over those days.
    if (endDate && allDayInput) {
      allDayInput.checked = true;
      syncTimeFields();
    }
    // A slot picked on the week grid becomes a timed event.
    if (times) {
      if (allDayInput) {
        allDayInput.checked = false;
      }
      syncTimeFields();
      if (startInput) startInput.value = times.start;
      if (endInput) endInput.value = times.end;
    }

    if (!eventDialog.open) {
      eventDialog.showModal();
    }

    window.setTimeout(() => titleInput?.focus(), 0);
  };

  document.querySelectorAll("[data-event-dialog-open]").forEach((button) => {
    button.addEventListener("click", () => openEventDialog());
  });

  document.querySelectorAll("[data-event-date]").forEach((button) => {
    button.addEventListener("click", () => openEventDialog(button.dataset.eventDate));
  });

  eventDialog.querySelectorAll("[data-event-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => eventDialog.close());
  });

  eventDialog.addEventListener("click", (event) => {
    if (event.target === eventDialog) {
      eventDialog.close();
    }
  });

  allDayInput?.addEventListener("change", syncTimeFields);
  syncTimeFields();

  repeatSelect?.addEventListener("change", syncRepeatFields);
  syncRepeatFields();

  if (eventDialog.dataset.hasErrors === "true") {
    openEventDialog();
  }

  // Right-click a day (tap on touch screens) or drag across days with either button to open the dialog
  // for that date or range. Chips, links and the task checkbox keep their own clicks. The week view's
  // all-day row works the same way; its time grid picks a time slot instead (below).
  let dragStart = null;
  let dragEnd = null;
  // Windows fires contextmenu after the mouseup of a right-button drag; that range already opened the dialog.
  let swallowContextMenu = false;
  const interactive = "button, a, input, label, .task-chip, .week-event";
  const dayCell = ".calendar-day[data-date], .week-all-day-cell[data-date]";

  const markRange = () => {
    const [first, last] = [dragStart, dragEnd].sort();
    document.querySelectorAll(dayCell).forEach((day) => {
      day.classList.toggle("is-selected", Boolean(dragStart) && day.dataset.date >= first && day.dataset.date <= last);
    });
  };

  // Week grid: half-hour slots in a day column, dragged vertically for a time range.
  const SLOT_MINUTES = 30;
  const DAY_MINUTES = 24 * 60;
  let slotDrag = null;
  let slotSelection = null;

  const slotAt = (column, clientY) => {
    const rect = column.getBoundingClientRect();
    const minute = ((clientY - rect.top) / rect.height) * DAY_MINUTES;
    const slot = Math.floor(minute / SLOT_MINUTES) * SLOT_MINUTES;
    return Math.min(Math.max(slot, 0), DAY_MINUTES - SLOT_MINUTES);
  };

  const clock = (minute) => {
    // The time input has no 24:00, so a range up to midnight ends one minute before it.
    const value = Math.min(minute, DAY_MINUTES - 1);
    return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  };

  const slotTimes = (start, end) => ({ start: clock(start), end: clock(Math.min(end, DAY_MINUTES)) });

  const markSlots = () => {
    slotSelection?.remove();
    slotSelection = null;
    if (!slotDrag) {
      return;
    }
    const [first, last] = [slotDrag.from, slotDrag.to].sort((a, b) => a - b);
    slotSelection = document.createElement("span");
    slotSelection.className = "week-selection";
    slotSelection.style.setProperty("--start", first);
    slotSelection.style.setProperty("--duration", last - first + SLOT_MINUTES);
    slotDrag.column.append(slotSelection);
  };

  document.addEventListener("mousemove", (event) => {
    if (!slotDrag) {
      return;
    }
    const slot = slotAt(slotDrag.column, event.clientY);
    if (slot !== slotDrag.to) {
      slotDrag.to = slot;
      markSlots();
    }
  });

  document.addEventListener("contextmenu", (event) => {
    if (swallowContextMenu) {
      swallowContextMenu = false;
      event.preventDefault();
      return;
    }
    if (event.target.closest(interactive)) {
      return;
    }
    const column = event.target.closest("[data-week-column]");
    if (column) {
      event.preventDefault();
      // macOS and Linux fire contextmenu on mousedown; the right-click itself opens the dialog.
      slotDrag = null;
      markSlots();
      const start = slotAt(column, event.clientY);
      openEventDialog(column.dataset.date, "", slotTimes(start, start + 60));
      return;
    }
    const day = event.target.closest(dayCell);
    if (!day) {
      return;
    }
    event.preventDefault();
    openEventDialog(day.dataset.date);
  });

  document.addEventListener("mousedown", (event) => {
    swallowContextMenu = false;
    if ((event.button !== 0 && event.button !== 2) || event.target.closest(interactive)) {
      return;
    }
    const column = event.target.closest("[data-week-column]");
    if (column) {
      slotDrag = { column, from: slotAt(column, event.clientY) };
      slotDrag.to = slotDrag.from;
      markSlots();
      return;
    }
    const day = event.target.closest(dayCell);
    if (day) {
      dragStart = day.dataset.date;
      dragEnd = dragStart;
      markRange();
    }
  });

  document.addEventListener("mouseover", (event) => {
    const day = dragStart && event.target.closest(dayCell);
    if (day && day.dataset.date !== dragEnd) {
      dragEnd = day.dataset.date;
      markRange();
    }
  });

  document.addEventListener("mouseup", (event) => {
    if (slotDrag) {
      const [first, last] = [slotDrag.from, slotDrag.to].sort((a, b) => a - b);
      const date = slotDrag.column.dataset.date;
      slotDrag = null;
      markSlots();
      if (first !== last) {
        openEventDialog(date, "", slotTimes(first, last + SLOT_MINUTES));
        swallowContextMenu = event.button === 2;
      } else if (event.button === 0 && window.matchMedia("(pointer: coarse)").matches) {
        openEventDialog(date, "", slotTimes(first, first + 60));
      }
      return;
    }
    if (!dragStart) {
      return;
    }
    const [first, last] = [dragStart, dragEnd].sort();
    dragStart = null;
    markRange();
    if (first !== last) {
      openEventDialog(first, last);
      swallowContextMenu = event.button === 2;
    } else if (event.button === 0 && window.matchMedia("(pointer: coarse)").matches) {
      openEventDialog(first);
    }
  });
}

const editDialog = document.querySelector("[data-event-edit-dialog]");

if (editDialog) {
  const editForm = editDialog.querySelector("[data-event-edit-form]");
  const idInput = editForm?.querySelector("[data-event-edit-id-input]");
  const titleInput = editForm?.querySelector("input[name='title']");
  const dateInput = editForm?.querySelector("input[name='event_date']");
  const endDateInput = editForm?.querySelector("input[name='end_date']");
  const startInput = editForm?.querySelector("input[name='start_time']");
  const endInput = editForm?.querySelector("input[name='end_time']");
  const allDayInput = editForm?.querySelector("input[name='is_all_day']");
  const locationInput = editForm?.querySelector("input[name='location']");
  const categorySelect = editForm?.querySelector("select[name='category']");
  const attendeeInputs = editForm?.querySelectorAll("input[name='attendees']") || [];
  const timeFields = editForm?.querySelectorAll("[data-event-edit-time-field]") || [];

  const syncEditTimeFields = () => {
    const isAllDay = Boolean(allDayInput?.checked);

    timeFields.forEach((field) => {
      const input = field.querySelector("input");
      field.classList.toggle("is-disabled", isAllDay);
      if (input) {
        input.disabled = isAllDay;
      }
    });
  };

  const openEditDialog = (data) => {
    if (idInput) idInput.value = data.eventId || "";
    if (titleInput) titleInput.value = data.title || "";
    if (dateInput) dateInput.value = data.date || "";
    if (endDateInput) endDateInput.value = data.endDate || "";
    if (startInput) startInput.value = data.start || "";
    if (endInput) endInput.value = data.end || "";
    if (allDayInput) allDayInput.checked = data.allDay === "true";
    if (locationInput) locationInput.value = data.location || "";
    if (categorySelect) categorySelect.value = data.category || "other";

    const attendeeIds = (data.attendees || "").split(",").filter(Boolean);
    attendeeInputs.forEach((input) => {
      input.checked = attendeeIds.includes(input.value);
    });

    syncEditTimeFields();

    if (!editDialog.open) {
      editDialog.showModal();
    }

    window.setTimeout(() => titleInput?.focus(), 0);
  };

  document.querySelectorAll("[data-event-edit-open]").forEach((button) => {
    button.addEventListener("click", () =>
      openEditDialog({
        eventId: button.dataset.eventId,
        title: button.dataset.editTitle,
        date: button.dataset.editDate,
        endDate: button.dataset.editEndDate,
        start: button.dataset.editStart,
        end: button.dataset.editEnd,
        allDay: button.dataset.editAllDay,
        category: button.dataset.editCategory,
        location: button.dataset.editLocation,
        attendees: button.dataset.editAttendees,
      }),
    );
  });

  editDialog.querySelectorAll("[data-event-edit-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => editDialog.close());
  });

  editDialog.addEventListener("click", (event) => {
    if (event.target === editDialog) {
      editDialog.close();
    }
  });

  allDayInput?.addEventListener("change", syncEditTimeFields);
  syncEditTimeFields();

  if (editDialog.dataset.hasErrors === "true") {
    if (!editDialog.open) {
      editDialog.showModal();
    }
    window.setTimeout(() => titleInput?.focus(), 0);
  }
}

// Week view: open the time grid at the current hour (or the morning), and keep the "now" line moving.
const weekView = document.querySelector("[data-week-view]");
const weekBody = weekView?.querySelector("[data-week-body]");

if (weekView && weekBody) {
  const column = weekBody.querySelector("[data-week-column]");
  const hourHeight = column ? column.getBoundingClientRect().height / 24 : 0;
  weekBody.scrollTop = ((Number(weekView.dataset.scrollMinute) || 0) / 60) * hourHeight;

  const nowLine = weekBody.querySelector("[data-week-now]");
  if (nowLine) {
    const startMinute = Number(nowLine.style.getPropertyValue("--start")) || 0;
    const loadedAt = Date.now();
    window.setInterval(() => {
      const minute = startMinute + Math.floor((Date.now() - loadedAt) / 60000);
      nowLine.hidden = minute >= 24 * 60;
      nowLine.style.setProperty("--start", minute);
    }, 60000);
  }
}
