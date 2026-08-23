export function normalizeLinkHref(value) {
  const href = String(value || "").trim();
  if (!href) return "";
  if (/^[a-z][a-z0-9+.-]*:/i.test(href)) return href;
  if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(href)) return `mailto:${href}`;
  return `https://${href}`;
}
