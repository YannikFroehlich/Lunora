// Week grid: a day column is split into half-hour slots.
const SLOT_MINUTES = 30;
const DAY_MINUTES = 24 * 60;

const slotAt = (column, clientY) => {
  const rect = column.getBoundingClientRect();
  const minute = ((clientY - rect.top) / rect.height) * DAY_MINUTES;
  const slot = Math.floor(minute / SLOT_MINUTES) * SLOT_MINUTES;
  return Math.min(Math.max(slot, 0), DAY_MINUTES - SLOT_MINUTES);
};

// Drag-and-drop results are posted as an ordinary form; data-soft lets base.js send it in the
// background and re-render only the calendar. The form is removed again once the post is under way.
const postSoft = (action, fields) => {
  const form = document.createElement("form");
  form.method = "post";
  form.action = action;
  form.dataset.soft = "";
  const values = { csrfmiddlewaretoken: document.querySelector("meta[name='csrf-token']")?.content || "", ...fields };
  Object.entries(values).forEach(([name, value]) => {
    form.append(Object.assign(document.createElement("input"), { type: "hidden", name, value }));
  });
  document.body.append(form);
  form.requestSubmit();
  form.remove();
};

const clock = (minute) => {
  // The time input has no 24:00, so a range up to midnight ends one minute before it.
  const value = Math.min(minute, DAY_MINUTES - 1);
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
};

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

  // Delegated: day buttons are re-rendered when an action updates the calendar in place.
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-event-dialog-open]")) {
      openEventDialog();
      return;
    }
    const dayButton = event.target.closest("[data-event-date]");
    if (dayButton) openEventDialog(dayButton.dataset.eventDate);
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
  let slotDrag = null;
  let slotSelection = null;

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
  const reminderSelect = editForm?.querySelector("select[name='reminder_minutes']");
  const attendeeInputs = editForm?.querySelectorAll("input[name='attendees']") || [];
  const timeFields = editForm?.querySelectorAll("[data-event-edit-time-field]") || [];
  const scopeFieldset = editForm?.querySelector("[data-event-edit-scope]");
  const scopeInputs = editForm?.querySelectorAll("input[name='scope']") || [];
  const seriesInput = editForm?.querySelector("[data-event-edit-series-input]");
  const deleteButton = editForm?.querySelector(".event-delete-button");
  const singleDeleteConfirm = deleteButton?.dataset.confirm || "";

  // Series occurrences ask which events an edit or delete covers; the delete prompt follows that choice.
  const syncDeleteConfirm = () => {
    if (!deleteButton) return;
    const scope = [...scopeInputs].find((input) => input.checked)?.value || "this";
    const coversSeries = !scopeFieldset?.hidden && scope !== "this";
    deleteButton.dataset.confirm = coversSeries ? deleteButton.dataset.seriesConfirm : singleDeleteConfirm;
  };

  const setSeries = (isSeries, keepScope = false) => {
    if (scopeFieldset) scopeFieldset.hidden = !isSeries;
    if (!keepScope) {
      scopeInputs.forEach((input) => {
        input.checked = input.value === "this";
      });
    }
    syncDeleteConfirm();
  };

  scopeInputs.forEach((input) => input.addEventListener("change", syncDeleteConfirm));

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
    if (reminderSelect) reminderSelect.value = data.reminder || "";
    const detailLink = editDialog.querySelector("[data-event-detail-link]");
    if (detailLink) {
      detailLink.hidden = !data.detailUrl;
      detailLink.href = data.detailUrl || "#";
    }

    const attendeeIds = (data.attendees || "").split(",").filter(Boolean);
    attendeeInputs.forEach((input) => {
      input.checked = attendeeIds.includes(input.value);
    });

    syncEditTimeFields();
    setSeries(data.series === "true");

    if (!editDialog.open) {
      editDialog.showModal();
    }

    window.setTimeout(() => titleInput?.focus(), 0);
  };

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-event-edit-open]");
    if (!button) return;
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
      reminder: button.dataset.editReminder,
      series: button.dataset.editSeries,
      detailUrl: button.dataset.detailUrl,
    });
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
    setSeries(seriesInput?.value === "true", true);
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

  // The line is looked up each minute: an in-place update renders it anew at the server's minute.
  window.setInterval(() => {
    const nowLine = document.querySelector("[data-week-now]");
    if (!nowLine) return;
    if (!nowLine.dataset.baseMinute) {
      nowLine.dataset.baseMinute = Number(nowLine.style.getPropertyValue("--start")) || 0;
      nowLine.dataset.baseAt = Date.now() - 60000;
    }
    const minute = Number(nowLine.dataset.baseMinute) + Math.floor((Date.now() - nowLine.dataset.baseAt) / 60000);
    nowLine.hidden = minute >= DAY_MINUTES;
    nowLine.style.setProperty("--start", minute);
  }, 60000);
}

// Drag a task chip onto another day to move its due date (keeping the time), or onto the week's time
// grid to give it that time too. The drop posts a normal task_reschedule form that returns here.
{
  const dropTarget = "[data-week-column], .calendar-day[data-date], .week-all-day-cell[data-date]";
  let draggedTask = null;
  let dropMark = null;

  const clearDropMark = () => {
    dropMark?.classList.remove("is-selected");
    dropMark = null;
    document.querySelector(".week-selection.is-task-drop")?.remove();
  };

  const markDrop = (target, clientY) => {
    if (target.matches("[data-week-column]")) {
      let preview = target.querySelector(".week-selection.is-task-drop");
      if (dropMark !== target) {
        clearDropMark();
        preview = document.createElement("span");
        preview.className = "week-selection is-task-drop";
        preview.style.setProperty("--duration", SLOT_MINUTES);
        target.append(preview);
      }
      preview.style.setProperty("--start", slotAt(target, clientY));
    } else if (dropMark !== target) {
      clearDropMark();
      target.classList.add("is-selected");
    }
    dropMark = target;
  };

  document.addEventListener("dragstart", (event) => {
    const chip = event.target.closest?.("[data-task-drag]");
    if (!chip) return;
    draggedTask = chip;
    chip.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", chip.dataset.taskDrag);
  });
  document.addEventListener("dragend", () => {
    if (!draggedTask) return;
    draggedTask.classList.remove("is-dragging");
    draggedTask = null;
    clearDropMark();
  });

  document.addEventListener("dragover", (event) => {
    const target = draggedTask && event.target.closest(dropTarget);
    if (!target) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    markDrop(target, event.clientY);
  });

  document.addEventListener("drop", (event) => {
    const target = draggedTask && event.target.closest(dropTarget);
    if (!target) {
      return;
    }
    event.preventDefault();
    const fields = {
      form_name: "task_reschedule",
      task_id: draggedTask.dataset.taskDrag,
      due_date: target.dataset.date,
      return_to: `${window.location.pathname}${window.location.search}`,
    };
    if (target.matches("[data-week-column]")) {
      fields.due_time = clock(slotAt(target, event.clientY));
    }
    const unchanged = target.dataset.date === draggedTask.dataset.taskDate && !fields.due_time;
    clearDropMark();
    if (unchanged) {
      return;
    }
    postSoft(draggedTask.action, fields);
  });
}

// Drag an own event with the mouse onto another day to move it there, or within the time grid to a new
// time; the strip at the bottom of a timed event stretches its end. Moves keep the event's length and a
// multi-day event moves by as many days as it was dragged. A plain click still opens the edit dialog.
// Touch screens keep scrolling and edit through the dialog instead.
{
  const MOVE_STEP = 15;
  const DRAG_THRESHOLD = 4;
  const dayTargets = ".calendar-day[data-date], .week-all-day-cell[data-date]";
  let drag = null;
  let suppressClick = false;

  const dayNumber = (isoDate) => {
    const [year, month, day] = isoDate.split("-").map(Number);
    return Date.UTC(year, month - 1, day) / 86400000;
  };

  const isoDate = (number) => new Date(number * 86400000).toISOString().slice(0, 10);

  const toMinutes = (value) => {
    const [hours, minutes] = (value || "00:00").split(":").map(Number);
    return hours * 60 + minutes;
  };

  const minuteIn = (column, clientY) => {
    const rect = column.getBoundingClientRect();
    return Math.round((((clientY - rect.top) / rect.height) * DAY_MINUTES) / MOVE_STEP) * MOVE_STEP;
  };

  const clearPreview = () => {
    drag?.preview?.remove();
    drag?.marked?.classList.remove("is-selected");
    if (drag) {
      drag.preview = null;
      drag.marked = null;
    }
  };

  const showGridPreview = (column, start, duration) => {
    if (drag.marked) {
      drag.marked.classList.remove("is-selected");
      drag.marked = null;
    }
    if (!drag.preview || drag.preview.parentElement !== column) {
      drag.preview?.remove();
      drag.preview = document.createElement("span");
      drag.preview.className = `week-selection is-event-drop ${drag.toneClass}`;
      column.append(drag.preview);
    }
    drag.preview.style.setProperty("--start", start);
    drag.preview.style.setProperty("--duration", duration);
  };

  const showDayPreview = (cell) => {
    if (drag.marked !== cell) {
      clearPreview();
      cell.classList.add("is-selected");
      drag.marked = cell;
    }
  };

  // Where the event would land for the pointer position: the fields of the move form, or null.
  const dropAt = (clientX, clientY) => {
    const { chip } = drag;
    if (drag.mode === "resize") {
      const end = Math.min(Math.max(minuteIn(drag.column, clientY), drag.segmentStart + MOVE_STEP), DAY_MINUTES);
      showGridPreview(drag.column, drag.segmentStart, end - drag.segmentStart);
      return { date: drag.column.dataset.date, end_minute: end };
    }
    const target = document.elementFromPoint(clientX, clientY);
    const column = target?.closest("[data-week-column]");
    const cell = target?.closest(dayTargets);
    const dayShift = (date) => dayNumber(date) - dayNumber(chip.dataset.moveDate);
    if (column && chip.dataset.moveAllDay !== "true") {
      const top = Math.min(Math.max(minuteIn(column, clientY - drag.grabOffset), 0), DAY_MINUTES - MOVE_STEP);
      showGridPreview(column, top, drag.duration);
      // The event's real start moves by as much as the dragged part did, even from a later day's part.
      const start =
        dayNumber(chip.dataset.editDate) * DAY_MINUTES +
        toMinutes(chip.dataset.editStart) +
        dayShift(column.dataset.date) * DAY_MINUTES +
        top -
        drag.segmentStart;
      return { date: isoDate(Math.floor(start / DAY_MINUTES)), start_minute: start % DAY_MINUTES };
    }
    const day = cell || column;
    if (!day) {
      clearPreview();
      return null;
    }
    showDayPreview(day);
    return { date: isoDate(dayNumber(chip.dataset.editDate) + dayShift(day.dataset.date)) };
  };

  const submitMove = (fields) => {
    postSoft(`${window.location.pathname}${window.location.search}`, {
      form_name: "calendar_event_move",
      event_id: drag.chip.dataset.eventMove,
      ...fields,
    });
  };

  const stopDrag = () => {
    if (!drag) {
      return;
    }
    clearPreview();
    drag.chip.classList.remove("is-dragging");
    document.body.classList.remove("is-event-dragging", "is-event-resizing");
    drag = null;
  };

  document.addEventListener("pointerdown", (event) => {
    const chip = event.button === 0 && event.pointerType !== "touch" && event.target.closest("[data-event-move]");
    if (!chip) {
      return;
    }
    const column = chip.closest("[data-week-column]");
    const resizing = Boolean(column && event.target.closest("[data-event-resize]"));
    const segmentStart = Number(chip.dataset.moveStart) || 0;
    drag = {
      chip,
      column,
      mode: resizing ? "resize" : "move",
      x: event.clientX,
      y: event.clientY,
      active: false,
      fields: null,
      segmentStart,
      duration: Number(chip.style.getPropertyValue("--duration")) || 60,
      // How far below the event's top it was grabbed, so it doesn't jump to the pointer.
      grabOffset: column ? event.clientY - chip.getBoundingClientRect().top : 0,
      toneClass: [...chip.classList].find((name) => name.startsWith("tone-")) || "",
    };
    if (resizing) {
      // Don't let the press start a move of the event the handle sits in.
      event.preventDefault();
    }
  });

  document.addEventListener("pointermove", (event) => {
    if (!drag) {
      return;
    }
    if (!drag.active) {
      if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < DRAG_THRESHOLD) {
        return;
      }
      drag.active = true;
      drag.chip.classList.toggle("is-dragging", drag.mode === "move");
      document.body.classList.add(drag.mode === "resize" ? "is-event-resizing" : "is-event-dragging");
    }
    drag.fields = dropAt(event.clientX, event.clientY);
  });

  document.addEventListener("pointerup", () => {
    if (!drag) {
      return;
    }
    const { active, fields } = drag;
    if (active) {
      // The release would also click the event and open its edit dialog.
      suppressClick = true;
      window.setTimeout(() => {
        suppressClick = false;
      }, 0);
    }
    const { editDate, editStart } = drag.chip.dataset;
    const unchanged =
      drag.mode === "move" &&
      fields?.date === editDate &&
      (fields.start_minute === undefined || fields.start_minute === toMinutes(editStart));
    if (active && fields && !unchanged) {
      submitMove(fields);
    }
    stopDrag();
  });

  document.addEventListener("pointercancel", stopDrag);

  document.addEventListener(
    "click",
    (event) => {
      if (suppressClick) {
        event.preventDefault();
        event.stopPropagation();
        suppressClick = false;
      }
    },
    true,
  );

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      stopDrag();
    }
  });
}

// A chat message sent here with "Als Termin" (messages.js) lands in the quick-add field, where a
// date like "morgen 14 Uhr" can be added before saving.
try {
  const prefill = JSON.parse(sessionStorage.getItem("lunora-chat-prefill") || "null");
  const quickAddInput = document.querySelector(".calendar-quick-add [name='text']");
  if (prefill?.target === "event" && quickAddInput) {
    sessionStorage.removeItem("lunora-chat-prefill");
    quickAddInput.value = `${prefill.text.slice(0, 230)} `;
    quickAddInput.focus();
  }
} catch {
  // Storage blocked: nothing to take over.
}

// Location: a web address links to the meeting, anything else to a map search (same rule as
// location_link in calendar_service.py, which renders the links outside the dialogs).
const locationLinkFor = (value) => {
  const location = value.trim();
  if (!location) return null;
  if (/^https?:\/\/\S+$/i.test(location)) {
    try {
      const url = new URL(location);
      return { href: url.href, label: "Online-Termin öffnen", icon: "fa-video" };
    } catch {
      // Not a valid URL after all: treat it as an address.
    }
  }
  return {
    href: `https://www.openstreetmap.org/search?query=${encodeURIComponent(location)}`,
    label: "Auf der Karte zeigen",
    icon: "fa-map-location-dot",
  };
};

// Attendee availability: the dialog's own fields go to /calendar/availability/ as a query string,
// and each ticked person gets a free/busy/away badge. Only that status comes back, never details.
document.querySelectorAll("form.event-create-form").forEach((form) => {
  const dialog = form.closest("dialog");
  const fieldset = form.querySelector("[data-availability-url]");
  const hint = form.querySelector("[data-availability-hint]");
  const locationInput = form.querySelector("input[name='location']");
  const locationLink = form.querySelector("[data-location-link]");
  const eventIdInput = form.querySelector("[data-event-edit-id-input]");
  let timer = 0;
  let requestNumber = 0;

  const syncLocationLink = () => {
    if (!locationInput || !locationLink) return;
    const link = locationLinkFor(locationInput.value);
    locationLink.hidden = !link;
    if (!link) return;
    locationLink.href = link.href;
    locationLink.replaceChildren();
    const icon = document.createElement("i");
    icon.className = `fa-solid ${link.icon}`;
    icon.setAttribute("aria-hidden", "true");
    locationLink.append(icon, ` ${link.label}`);
  };

  const badgeFor = (input) => {
    const label = input.closest("label");
    let badge = label?.querySelector(".availability-badge");
    if (label && !badge) {
      badge = document.createElement("span");
      badge.className = "availability-badge";
      label.append(badge);
    }
    return badge;
  };

  const clearBadges = () => {
    form.querySelectorAll(".availability-badge").forEach((badge) => badge.remove());
  };

  // The dialog's date/time fields and ticked people, as both endpoints read them.
  const timeParams = () => {
    const params = new URLSearchParams();
    ["event_date", "end_date", "start_time", "end_time"].forEach((name) => {
      const value = form.querySelector(`[name='${name}']`)?.value;
      if (value) params.set(name, value);
    });
    if (form.querySelector("input[name='is_all_day']")?.checked) params.set("is_all_day", "on");
    if (eventIdInput?.value) params.set("exclude_event", eventIdInput.value);
    form
      .querySelectorAll("input[name='attendees']:checked")
      .forEach((input) => params.append("attendees", input.value));
    return params;
  };

  const refreshAvailability = async () => {
    if (!fieldset) return;
    const checked = [...form.querySelectorAll("input[name='attendees']:checked")];
    const current = ++requestNumber;
    if (!checked.length) {
      clearBadges();
      if (hint) hint.textContent = "";
      return;
    }
    const params = timeParams();
    try {
      const response = await fetch(`${fieldset.dataset.availabilityUrl}?${params}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (current !== requestNumber) return;
      const data = await response.json();
      if (!response.ok || !data.ok) {
        clearBadges();
        if (hint) hint.textContent = "Verfügbarkeit erscheint, sobald Datum und Uhrzeit vollständig sind.";
        return;
      }
      clearBadges();
      const byId = new Map(data.users.map((user) => [String(user.id), user]));
      let conflicts = 0;
      checked.forEach((input) => {
        const user = byId.get(input.value);
        const badge = user && badgeFor(input);
        if (!badge) return;
        badge.classList.add(`is-${user.status}`);
        badge.textContent = user.label;
        if (user.status !== "free") conflicts += 1;
      });
      if (hint) {
        hint.textContent = conflicts
          ? `${conflicts} von ${checked.length} Eingeladenen ${conflicts === 1 ? "ist" : "sind"} zu dieser Zeit nicht frei.`
          : "Alle Eingeladenen sind zu dieser Zeit frei.";
      }
    } catch {
      // Offline or server error: the dialog still works without the hint.
    }
  };

  const scheduleRefresh = () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(refreshAvailability, 250);
  };

  // "Freien Termin finden": the next times at which the user and everyone ticked are free, for the
  // length entered; picking one fills the date and times, and the badges refresh.
  const slotFinder = form.querySelector("[data-slot-finder]");
  const slotList = form.querySelector("[data-slot-list]");
  const slotMessage = (text) => {
    const message = document.createElement("small");
    message.textContent = text;
    slotList.replaceChildren(message);
  };
  const applySlot = (slot) => {
    const set = (name, value) => {
      const input = form.querySelector(`[name='${name}']`);
      if (input) input.value = value;
    };
    set("event_date", slot.date);
    set("end_date", "");
    set("start_time", slot.start_time);
    set("end_time", slot.end_time);
    const allDay = form.querySelector("input[name='is_all_day']");
    if (allDay?.checked) {
      allDay.checked = false;
      allDay.dispatchEvent(new Event("change", { bubbles: true }));
    }
    slotList.replaceChildren();
    scheduleRefresh();
    form.querySelector("[name='start_time']")?.focus();
  };
  slotFinder?.addEventListener("click", async () => {
    slotMessage("Suche freie Termine …");
    try {
      const response = await fetch(`${slotFinder.dataset.slotsUrl}?${timeParams()}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        slotMessage("Trag zuerst Datum und Uhrzeit ein; die Länge des Termins zählt.");
        return;
      }
      if (!data.slots.length) {
        slotMessage(
          form.querySelector("input[name='is_all_day']")?.checked
            ? "Für ganztägige Termine gibt es keine Vorschläge."
            : "In den nächsten zwei Wochen ist werktags zwischen 8 und 18 Uhr nichts gemeinsam frei.",
        );
        return;
      }
      slotList.replaceChildren(
        ...data.slots.map((slot) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "ghost-button event-slot";
          button.textContent = slot.label;
          button.addEventListener("click", () => applySlot(slot));
          return button;
        }),
      );
    } catch {
      slotMessage("Vorschläge konnten nicht geladen werden.");
    }
  });

  form.addEventListener("input", (event) => {
    if (event.target === locationInput) {
      syncLocationLink();
    } else {
      scheduleRefresh();
    }
  });
  form.addEventListener("change", scheduleRefresh);

  // The dialogs are filled programmatically (edit data, dragged ranges) before they open.
  if (dialog) {
    new MutationObserver(() => {
      if (dialog.open) {
        syncLocationLink();
        scheduleRefresh();
      }
    }).observe(dialog, { attributes: true, attributeFilter: ["open"] });
  }
  syncLocationLink();
});
