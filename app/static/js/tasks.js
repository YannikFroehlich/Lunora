(function () {
  const taskList = document.querySelector("[data-task-list]");
  const filterButtons = Array.from(document.querySelectorAll("[data-task-filter]"));
  const viewLinks = Array.from(document.querySelectorAll("[data-task-view-link]"));
  const sortSelect = document.querySelector("[data-task-sort]");
  const resetButton = document.querySelector("[data-task-reset]");
  const filterEmpty = document.querySelector("[data-task-filter-empty]");
  const resultsStatus = document.querySelector("[data-task-results-status]");
  const dateField = document.querySelector(".task-date-field");
  const dateInput = document.querySelector(".task-date-field input[type='datetime-local']");
  const dateLabel = document.querySelector("[data-task-date-label]");
  const moreMenu = document.querySelector(".task-more-menu");

  let activeFilter = "all";
  let activeView = "all";
  let activeListId = "";

  const PRIORITY_RANK = { high: 3, medium: 2, low: 1, none: 0 };

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

  function taskMatchesView(row) {
    if (activeView === "all") return true;
    if (activeView === "list") return (row.dataset.taskList || "") === activeListId;
    return row.dataset.taskView === activeView;
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

  function priorityValue(row) {
    return PRIORITY_RANK[row.dataset.taskPriority] ?? 0;
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

    if (sortMode === "priority") {
      const priorityDifference = priorityValue(second) - priorityValue(first);
      if (priorityDifference) return priorityDifference;
    }

    const dueDifference = dueValue(first) - dueValue(second);
    return dueDifference || createdValue(second) - createdValue(first);
  }

  function applyTaskView() {
    if (!taskList) return;

    const groups = Array.from(taskList.querySelectorAll("[data-task-group]"));
    groups.sort((firstGroup, secondGroup) =>
      compareRows(firstGroup.querySelector("[data-task-row]"), secondGroup.querySelector("[data-task-row]"))
    ).forEach((group) => taskList.appendChild(group));

    let visibleCount = 0;
    groups.forEach((group) => {
      const parentRow = group.querySelector("[data-task-row]:not([data-task-subtask])");
      const subtaskRows = Array.from(group.querySelectorAll("[data-task-row][data-task-subtask]"));
      subtaskRows.sort(compareRows).forEach((row) => group.insertBefore(row, group.querySelector(".task-subtask-add-form")));

      const parentVisible = taskMatchesFilter(parentRow) && taskMatchesView(parentRow);
      let anySubtaskVisible = false;
      subtaskRows.forEach((row) => {
        const isVisible = parentVisible || (taskMatchesFilter(row) && taskMatchesView(row));
        row.hidden = !isVisible;
        if (isVisible) anySubtaskVisible = true;
      });

      const groupVisible = parentVisible || anySubtaskVisible;
      group.hidden = !groupVisible;
      // Keep the parent row shown whenever a subtask is visible, even if the parent's own
      // state doesn't match the filter, so a visible subtask never appears without context.
      parentRow.hidden = !groupVisible;
      if (groupVisible) visibleCount += 1;
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

  viewLinks.forEach((link) => {
    link.addEventListener("click", () => {
      activeView = link.dataset.taskViewLink || "all";
      activeListId = activeView === "list" ? link.dataset.taskViewList || "" : "";
      viewLinks.forEach((candidate) => candidate.classList.toggle("is-active", candidate === link));
      applyTaskView();
    });
  });

  if (sortSelect) sortSelect.addEventListener("change", applyTaskView);

  if (resetButton) {
    resetButton.addEventListener("click", () => {
      activeFilter = "all";
      activeView = "all";
      activeListId = "";
      if (sortSelect) sortSelect.value = "due";
      filterButtons.forEach((button) => {
        const isActive = button.dataset.taskFilter === "all";
        button.classList.toggle("is-active", isActive);
        button.setAttribute("aria-pressed", String(isActive));
      });
      viewLinks.forEach((link) => link.classList.toggle("is-active", link.dataset.taskViewLink === "all"));
      if (moreMenu) moreMenu.open = false;
      applyTaskView();
    });
  }

  document.querySelectorAll("[data-task-subtask-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const group = button.closest("[data-task-group]");
      if (!group) return;
      const collapsed = group.classList.toggle("is-collapsed");
      button.setAttribute("aria-expanded", String(!collapsed));
    });
  });

  document.querySelectorAll(".task-color-grid").forEach((grid) => {
    const dots = Array.from(grid.querySelectorAll(".task-color-dot"));
    const syncActive = () => {
      dots.forEach((dot) => dot.classList.toggle("is-active", dot.querySelector("input")?.checked ?? false));
    };
    dots.forEach((dot) => dot.querySelector("input")?.addEventListener("change", syncActive));
    syncActive();
  });

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
