function localDayKey(date) {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${String(date.getDate()).padStart(2, "0")}`;
}

// The HTML attributes of a checklist item with a due date ("YYYY-MM-DD"): the date itself, a short
// German label for the chip (the year only when it isn't this year's) and whether it is overdue,
// due today, upcoming or done. Anything that isn't a date renders nothing.
export function dueDateAttributes(dueDate, checked, today = new Date()) {
  if (typeof dueDate !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(dueDate)) return {};
  const [year, month, day] = dueDate.split("-");
  const label = Number(year) === today.getFullYear() ? `${day}.${month}.` : `${day}.${month}.${year}`;
  const todayKey = localDayKey(today);
  let state = "upcoming";
  if (checked) state = "done";
  else if (dueDate < todayKey) state = "overdue";
  else if (dueDate === todayKey) state = "today";
  return { "data-due-date": dueDate, "data-due-label": label, "data-due-state": state };
}

// Checklist progress shown on note list cards; the counts come from the server (Note.checklist_*).
export function checklistProgress(done, total) {
  const safeTotal = Number.isInteger(total) && total > 0 ? total : 0;
  const safeDone = Number.isInteger(done) ? Math.min(Math.max(done, 0), safeTotal) : 0;
  return {
    done: safeDone,
    total: safeTotal,
    label: safeTotal ? `${safeDone}/${safeTotal}` : "",
    complete: safeTotal > 0 && safeDone === safeTotal,
  };
}
