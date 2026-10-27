const eventDialog = document.querySelector("[data-event-dialog]");

if (eventDialog) {
  const eventForm = eventDialog.querySelector("[data-event-create-form]");
  const dateInput = eventForm?.querySelector("input[name='event_date']");
  const endDateInput = eventForm?.querySelector("input[name='end_date']");
  const titleInput = eventForm?.querySelector("input[name='title']");
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

  const openEventDialog = (date = "", endDate = "") => {
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

  // Right-click a day (tap on touch screens) or drag across days to open the dialog for that date or
  // range. Chips, links and the task checkbox keep their own clicks.
  let dragStart = null;
  let dragEnd = null;
  const interactive = "button, a, input, label, .task-chip";

  const markRange = () => {
    const [first, last] = [dragStart, dragEnd].sort();
    document.querySelectorAll(".calendar-day[data-date]").forEach((day) => {
      day.classList.toggle("is-selected", Boolean(dragStart) && day.dataset.date >= first && day.dataset.date <= last);
    });
  };

  document.addEventListener("contextmenu", (event) => {
    const day = event.target.closest(".calendar-day[data-date]");
    if (!day || event.target.closest(interactive)) {
      return;
    }
    event.preventDefault();
    openEventDialog(day.dataset.date);
  });

  document.addEventListener("mousedown", (event) => {
    const day = event.button === 0 && event.target.closest(".calendar-day[data-date]");
    if (day && !event.target.closest(interactive)) {
      dragStart = day.dataset.date;
      dragEnd = dragStart;
      markRange();
    }
  });

  document.addEventListener("mouseover", (event) => {
    const day = dragStart && event.target.closest(".calendar-day[data-date]");
    if (day && day.dataset.date !== dragEnd) {
      dragEnd = day.dataset.date;
      markRange();
    }
  });

  document.addEventListener("mouseup", () => {
    if (!dragStart) {
      return;
    }
    const [first, last] = [dragStart, dragEnd].sort();
    dragStart = null;
    markRange();
    if (first !== last) {
      openEventDialog(first, last);
    } else if (window.matchMedia("(pointer: coarse)").matches) {
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
