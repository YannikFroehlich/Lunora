(function () {
  const taskList = document.querySelector("[data-task-list]");
  const filterButtons = Array.from(document.querySelectorAll("[data-task-filter]"));
  const sortSelect = document.querySelector("[data-task-sort]");
  const resetButton = document.querySelector("[data-task-reset]");
  const filterEmpty = document.querySelector("[data-task-filter-empty]");
  const resultsStatus = document.querySelector("[data-task-results-status]");
  const dateField = document.querySelector(".task-date-field");
  const dateInput = document.querySelector(".task-date-field input[type='datetime-local']");
  const dateLabel = document.querySelector("[data-task-date-label]");
  const moreMenu = document.querySelector(".task-more-menu");

  let activeFilter = "all";

  function updateDateLabel() {
    if (!dateInput || !dateLabel) return;

    if (!dateInput.value) {
      dateLabel.textContent = "Fälligkeitsdatum";
      return;
    }

    const dueDate = new Date(dateInput.value);
    if (Number.isNaN(dueDate.getTime())) return;

    dateLabel.textContent = new Intl.DateTimeFormat("de-DE", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(dueDate);
  }

  function taskMatchesFilter(row) {
    const state = row.dataset.taskState;
    if (activeFilter === "all") return true;
    if (activeFilter === "open") return state === "open" || state === "overdue";
    return state === activeFilter;
  }

  function dueValue(row) {
    const value = row.dataset.taskDue;
    if (!value) return Number.POSITIVE_INFINITY;
    const timestamp = new Date(value).getTime();
    return Number.isNaN(timestamp) ? Number.POSITIVE_INFINITY : timestamp;
  }

  function createdValue(row) {
    const timestamp = new Date(row.dataset.taskCreated || "").getTime();
    return Number.isNaN(timestamp) ? 0 : timestamp;
  }

  function compareRows(first, second) {
    const sortMode = sortSelect ? sortSelect.value : "due";

    if (sortMode === "title") {
      return (first.dataset.taskTitle || "").localeCompare(
        second.dataset.taskTitle || "",
        "de",
        { sensitivity: "base" }
      );
    }

    if (sortMode === "newest") return createdValue(second) - createdValue(first);
    if (sortMode === "oldest") return createdValue(first) - createdValue(second);

    const firstDone = first.dataset.taskState === "done" ? 1 : 0;
    const secondDone = second.dataset.taskState === "done" ? 1 : 0;
    if (firstDone !== secondDone) return firstDone - secondDone;

    const dueDifference = dueValue(first) - dueValue(second);
    return dueDifference || createdValue(second) - createdValue(first);
  }

  function applyTaskView() {
    if (!taskList) return;

    const rows = Array.from(taskList.querySelectorAll("[data-task-row]"));
    rows.sort(compareRows).forEach((row) => taskList.appendChild(row));

    let visibleCount = 0;
    rows.forEach((row) => {
      const isVisible = taskMatchesFilter(row);
      row.hidden = !isVisible;
      if (isVisible) visibleCount += 1;
    });

    if (filterEmpty) filterEmpty.hidden = visibleCount !== 0;
    if (resultsStatus) {
      resultsStatus.textContent = visibleCount === 1
        ? "Eine Aufgabe wird angezeigt."
        : `${visibleCount} Aufgaben werden angezeigt.`;
    }
  }

  filterButtons.forEach((button) => {
    button.addEventListener("click", () => {
      activeFilter = button.dataset.taskFilter || "all";
      filterButtons.forEach((candidate) => {
        const isActive = candidate === button;
        candidate.classList.toggle("is-active", isActive);
        candidate.setAttribute("aria-pressed", String(isActive));
      });
      applyTaskView();
    });
  });

  if (sortSelect) sortSelect.addEventListener("change", applyTaskView);

  if (resetButton) {
    resetButton.addEventListener("click", () => {
      activeFilter = "all";
      if (sortSelect) sortSelect.value = "due";
      filterButtons.forEach((button) => {
        const isActive = button.dataset.taskFilter === "all";
        button.classList.toggle("is-active", isActive);
        button.setAttribute("aria-pressed", String(isActive));
      });
      if (moreMenu) moreMenu.open = false;
      applyTaskView();
    });
  }

  if (dateInput) {
    if (dateField) {
      dateField.addEventListener("click", () => {
        try {
          if (typeof dateInput.showPicker === "function") dateInput.showPicker();
          else dateInput.focus();
        } catch (error) {
          dateInput.focus();
        }
      });
    }

    dateInput.addEventListener("change", updateDateLabel);
    dateInput.addEventListener("input", updateDateLabel);
    updateDateLabel();
  }

  if (moreMenu) {
    document.addEventListener("click", (event) => {
      if (!moreMenu.contains(event.target)) moreMenu.open = false;
    });
    moreMenu.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        moreMenu.open = false;
        moreMenu.querySelector("summary").focus();
      }
    });
  }

  applyTaskView();
})();
