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

  function dragEnabled() {
    return sortSelect ? sortSelect.value === "manual" : false;
  }

  function syncDragAffordance() {
    const enabled = dragEnabled();
    document.querySelectorAll("[data-task-row]").forEach((row) => {
      row.classList.toggle("is-drag-enabled", enabled);
    });
  }

  function compareRows(first, second) {
    const sortMode = sortSelect ? sortSelect.value : "due";

    if (sortMode === "manual") return 0;

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

    syncDragAffordance();
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

  if (sortSelect) {
    sortSelect.addEventListener("change", () => {
      // Manual mode reorders relative to the true saved position order, which can differ
      // from whatever sort was on screen a moment ago — reload so the display (and thus
      // what dragging appears to operate on) matches that true order before anyone drags.
      if (sortSelect.value === "manual" && sortSelect.dataset.initialSort !== "manual") {
        window.location.href = `${window.location.pathname}?sort=manual`;
        return;
      }
      applyTaskView();
    });
  }

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

  const editDialog = document.querySelector("[data-task-edit-dialog]");

  if (editDialog) {
    const editForm = editDialog.querySelector("[data-task-edit-form]");
    const idInput = editForm?.querySelector("[data-task-edit-id-input]");
    const titleInput = editForm?.querySelector("input[name='title']");
    const dueInput = editForm?.querySelector("input[name='due_at']");
    const listSelect = editForm?.querySelector("select[name='task_list']");
    const prioritySelect = editForm?.querySelector("select[name='priority']");
    const recurrenceSelect = editForm?.querySelector("select[name='recurrence_rule']");
    const parentInput = editForm?.querySelector("input[name='parent']");
    const labelInputs = editForm?.querySelectorAll("input[name='labels']") || [];

    const openEditDialog = (data) => {
      if (idInput) idInput.value = data.taskId || "";
      if (titleInput) titleInput.value = data.title || "";
      if (dueInput) dueInput.value = data.due || "";
      if (listSelect) listSelect.value = data.list || "";
      if (prioritySelect) prioritySelect.value = data.priority || "none";
      if (recurrenceSelect) recurrenceSelect.value = data.recurrence || "none";
      if (parentInput) parentInput.value = data.parent || "";

      const labelIds = (data.labels || "").split(",").filter(Boolean);
      labelInputs.forEach((input) => {
        input.checked = labelIds.includes(input.value);
      });

      if (!editDialog.open) {
        editDialog.showModal();
      }

      window.setTimeout(() => titleInput?.focus(), 0);
    };

    document.querySelectorAll("[data-task-edit-open]").forEach((button) => {
      button.addEventListener("click", () =>
        openEditDialog({
          taskId: button.dataset.taskId,
          title: button.dataset.editTitle,
          due: button.dataset.editDue,
          list: button.dataset.editList,
          priority: button.dataset.editPriority,
          recurrence: button.dataset.editRecurrence,
          labels: button.dataset.editLabels,
          parent: button.dataset.editParent,
        })
      );
    });

    editDialog.querySelectorAll("[data-task-edit-dialog-close]").forEach((button) => {
      button.addEventListener("click", () => editDialog.close());
    });

    editDialog.addEventListener("click", (event) => {
      if (event.target === editDialog) {
        editDialog.close();
      }
    });

    if (editDialog.dataset.hasErrors === "true") {
      if (!editDialog.open) {
        editDialog.showModal();
      }
      window.setTimeout(() => titleInput?.focus(), 0);
    }
  }

  if (sortSelect && sortSelect.dataset.initialSort === "manual") {
    sortSelect.value = "manual";
  }

  let draggedTaskRow = null;

  document.addEventListener("dragstart", (event) => {
    const row = event.target.closest("[data-task-row]");
    if (!row) return;
    const handle = event.target.closest(".task-drag-handle");
    if (!handle || !dragEnabled()) {
      event.preventDefault();
      return;
    }
    draggedTaskRow = row;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", row.dataset.taskId || "");
  });

  document.addEventListener("dragend", () => {
    draggedTaskRow = null;
    document.querySelectorAll(".is-drop-before, .is-drop-after").forEach((el) => {
      el.classList.remove("is-drop-before", "is-drop-after");
    });
  });

  let draggedTaskListItem = null;

  document.addEventListener("dragstart", (event) => {
    const item = event.target.closest("[data-task-list-item]");
    if (!item) return;
    const handle = event.target.closest(".task-drag-handle");
    if (!handle) {
      event.preventDefault();
      return;
    }
    draggedTaskListItem = item;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", item.dataset.taskListId || "");
  });

  document.addEventListener("dragend", () => {
    draggedTaskListItem = null;
    document.querySelectorAll(".is-drop-before, .is-drop-after").forEach((el) => {
      el.classList.remove("is-drop-before", "is-drop-after");
    });
  });

  document.addEventListener("dragover", (event) => {
    if (draggedTaskRow) {
      const row = event.target.closest("[data-task-row]");
      if (!row || row === draggedTaskRow || row.dataset.taskParent !== draggedTaskRow.dataset.taskParent) return;
      event.preventDefault();
      const rect = row.getBoundingClientRect();
      const isBefore = event.clientY < rect.top + rect.height / 2;
      document.querySelectorAll(".is-drop-before, .is-drop-after").forEach((el) => {
        if (el !== row) el.classList.remove("is-drop-before", "is-drop-after");
      });
      row.classList.toggle("is-drop-before", isBefore);
      row.classList.toggle("is-drop-after", !isBefore);
      return;
    }

    if (draggedTaskListItem) {
      const item = event.target.closest("[data-task-list-item]");
      if (!item || item === draggedTaskListItem) return;
      event.preventDefault();
      const rect = item.getBoundingClientRect();
      const isBefore = event.clientY < rect.top + rect.height / 2;
      document.querySelectorAll(".is-drop-before, .is-drop-after").forEach((el) => {
        if (el !== item) el.classList.remove("is-drop-before", "is-drop-after");
      });
      item.classList.toggle("is-drop-before", isBefore);
      item.classList.toggle("is-drop-after", !isBefore);
    }
  });

  document.addEventListener("drop", (event) => {
    if (draggedTaskRow) {
      const row = event.target.closest("[data-task-row]");
      if (row && row !== draggedTaskRow && row.dataset.taskParent === draggedTaskRow.dataset.taskParent) {
        event.preventDefault();
        const placement = row.classList.contains("is-drop-before") ? "before" : "after";
        const reorderForm = document.getElementById("task-reorder-form");
        if (reorderForm) {
          reorderForm.querySelector("[data-reorder-task-id]").value = draggedTaskRow.dataset.taskId;
          reorderForm.querySelector("[data-reorder-target-id]").value = row.dataset.taskId;
          reorderForm.querySelector("[data-reorder-placement]").value = placement;
          reorderForm.submit();
        }
      }
      return;
    }

    if (draggedTaskListItem) {
      const item = event.target.closest("[data-task-list-item]");
      if (item && item !== draggedTaskListItem) {
        event.preventDefault();
        const placement = item.classList.contains("is-drop-before") ? "before" : "after";
        const reorderForm = document.getElementById("task-list-reorder-form");
        if (reorderForm) {
          reorderForm.querySelector("[data-list-reorder-task-list-id]").value = draggedTaskListItem.dataset.taskListId;
          reorderForm.querySelector("[data-list-reorder-target-id]").value = item.dataset.taskListId;
          reorderForm.querySelector("[data-list-reorder-placement]").value = placement;
          reorderForm.submit();
        }
      }
    }
  });

  applyTaskView();
})();
