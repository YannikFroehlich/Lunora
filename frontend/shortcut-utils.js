export function eventToShortcut(event) {
  const modifiers = [];
  const usesMod = navigator.platform?.toLowerCase().includes("mac") ? event.metaKey : event.ctrlKey;
  if (usesMod) modifiers.push("Mod");
  if (event.ctrlKey && !usesMod) modifiers.push("Ctrl");
  if (event.altKey) modifiers.push("Alt");
  if (event.shiftKey) modifiers.push("Shift");
  let key = event.key;
  if (["Control", "Alt", "Shift", "Meta"].includes(key)) return "";
  const aliases = { " ": "Space", ArrowUp: "ArrowUp", ArrowDown: "ArrowDown", ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight" };
  key = aliases[key] || (key.length === 1 ? key.toUpperCase() : key);
  if (!modifiers.length) return "";
  return [...modifiers, key].join("+");
}

export function shortcutMatches(event, shortcut) {
  if (!shortcut) return false;
  const expected = shortcut.split("+");
  const isMac = navigator.platform?.toLowerCase().includes("mac");
  const needsMod = expected.includes("Mod");
  const modPressed = isMac ? event.metaKey : event.ctrlKey;
  if (needsMod !== modPressed) return false;
  if (expected.includes("Ctrl") !== (event.ctrlKey && !needsMod)) return false;
  if (expected.includes("Alt") !== event.altKey) return false;
  if (expected.includes("Shift") !== event.shiftKey) return false;
  const expectedKey = expected[expected.length - 1].toLowerCase();
  return event.key.toLowerCase() === expectedKey;
}

export function mergeShortcuts(defaults, overrides) {
  return Object.fromEntries(
    Object.entries(defaults).map(([action, config]) => [
      action,
      { ...config, shortcut: Object.prototype.hasOwnProperty.call(overrides || {}, action) ? overrides[action] : config.shortcut },
    ])
  );
}

export function findShortcutConflict(commands, action, shortcut) {
  if (!shortcut) return null;
  return Object.entries(commands).find(([candidate, config]) => candidate !== action && config.shortcut?.toLowerCase() === shortcut.toLowerCase())?.[0] || null;
}
