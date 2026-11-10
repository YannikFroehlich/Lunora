// "Mira bearbeitet gerade", for the viewers of a note who are typing right now.
export function editingLabel(viewers) {
  const names = viewers.filter((viewer) => viewer.editing).map((viewer) => viewer.name);
  if (names.length === 0) return "";
  if (names.length === 1) return `${names[0]} bearbeitet gerade`;
  if (names.length === 2) return `${names[0]} und ${names[1]} bearbeiten gerade`;
  return `${names.length} Personen bearbeiten gerade`;
}
