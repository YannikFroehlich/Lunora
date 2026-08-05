const visibilityForm = document.querySelector("[data-calendar-visibility-form]");

if (visibilityForm) {
  const submitButton = visibilityForm.querySelector(".calendar-visibility-submit");

  if (submitButton) {
    submitButton.hidden = true;
  }

  visibilityForm.querySelectorAll("input[type='checkbox']").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      visibilityForm.requestSubmit();
    });
  });
}

const eventDialog = document.querySelector("[data-event-dialog]");

if (eventDialog) {
  const eventForm = eventDialog.querySelector("[data-event-create-form]");
  const dateInput = eventForm?.querySelector("input[name='event_date']");
  const titleInput = eventForm?.querySelector("input[name='title']");
  const allDayInput = eventForm?.querySelector("input[name='is_all_day']");
  const timeFields = eventForm?.querySelectorAll("[data-event-time-field]") || [];

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

  const openEventDialog = (date = "") => {
    if (date && dateInput) {
      dateInput.value = date;
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

  if (eventDialog.dataset.hasErrors === "true") {
    openEventDialog();
  }
}
