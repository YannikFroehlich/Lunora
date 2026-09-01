export const SLASH_MENU_ITEMS = [
  { action: "heading1", label: "Überschrift 1", iconText: "H1", keywords: ["heading", "titel", "h1"] },
  { action: "heading2", label: "Überschrift 2", iconText: "H2", keywords: ["heading", "titel", "h2"] },
  { action: "heading3", label: "Überschrift 3", iconText: "H3", keywords: ["heading", "titel", "h3"] },
  {
    action: "bulletList",
    label: "Aufzählung",
    iconClass: "fa-solid fa-list-ul",
    keywords: ["liste", "bullet", "list"],
  },
  {
    action: "orderedList",
    label: "Nummerierte Liste",
    iconClass: "fa-solid fa-list-ol",
    keywords: ["liste", "numbered", "ordered"],
  },
  {
    action: "taskList",
    label: "Checkliste",
    iconClass: "fa-solid fa-list-check",
    keywords: ["todo", "task", "checkbox"],
  },
  {
    action: "horizontalRule",
    label: "Trennlinie",
    iconClass: "fa-solid fa-minus",
    keywords: ["divider", "hr", "linie", "line"],
  },
  { action: "codeBlock", label: "Codeblock", iconClass: "fa-solid fa-code", keywords: ["code", "programmcode"] },
  {
    action: "insertTable",
    label: "Tabelle",
    iconClass: "fa-solid fa-table-cells-large",
    keywords: ["table", "tabelle"],
  },
  { action: "image", label: "Bild", iconClass: "fa-regular fa-image", keywords: ["image", "foto", "photo", "picture"] },
  {
    action: "attachment",
    label: "Datei",
    iconClass: "fa-solid fa-paperclip",
    keywords: ["file", "anhang", "attachment"],
  },
  {
    action: "insertMath",
    label: "Formel",
    iconClass: "fa-solid fa-square-root-variable",
    keywords: ["math", "formula", "gleichung"],
  },
  { action: "link", label: "Link", iconClass: "fa-solid fa-link", keywords: ["url", "hyperlink"] },
];

export function filterSlashMenuItems(items, query) {
  const normalized = String(query || "")
    .trim()
    .toLowerCase();
  if (!normalized) return items;
  return items.filter((item) => {
    if (item.label.toLowerCase().includes(normalized)) return true;
    return (item.keywords || []).some((keyword) => keyword.toLowerCase().includes(normalized));
  });
}
