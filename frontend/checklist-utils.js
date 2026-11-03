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
