const THEME_STORAGE_KEY = "lunora-theme";
const themeToggleButton = document.querySelector(".theme-toggle");
const themeToggleIcon = themeToggleButton?.querySelector("i");
const themeChoiceButtons = document.querySelectorAll("[data-theme-choice]");

function readStoredTheme() {
  try {
    const theme = localStorage.getItem(THEME_STORAGE_KEY);
    return ["light", "dark"].includes(theme) ? theme : null;
  } catch (error) {
    return null;
  }
}

function saveStoredTheme(theme) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch (error) {
    // Theme still changes for this page even if storage is unavailable.
  }
}

function getSystemTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function getCurrentTheme() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function updateThemeButton(theme) {
  if (!themeToggleButton || !themeToggleIcon) return;

  const isDarkMode = theme === "dark";
  themeToggleIcon.classList.toggle("fa-moon", !isDarkMode);
  themeToggleIcon.classList.toggle("fa-sun", isDarkMode);
  themeToggleButton.setAttribute(
    "aria-label",
    isDarkMode ? "Switch to light mode" : "Switch to dark mode"
  );
}

function updateThemeChoices(theme) {
  themeChoiceButtons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.themeChoice === theme);
    button.setAttribute("aria-pressed", String(button.dataset.themeChoice === theme));
  });
}

function applyTheme(theme, { persist = false } = {}) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;

  if (persist) {
    saveStoredTheme(theme);
  }

  updateThemeButton(theme);
  updateThemeChoices(theme);
}

applyTheme(readStoredTheme() || getCurrentTheme() || getSystemTheme());

themeToggleButton?.addEventListener("click", () => {
  const nextTheme = getCurrentTheme() === "dark" ? "light" : "dark";
  applyTheme(nextTheme, { persist: true });
});

themeChoiceButtons.forEach((button) => {
  button.addEventListener("click", () => {
    applyTheme(button.dataset.themeChoice, { persist: true });
  });
});

window.addEventListener("pageshow", () => {
  applyTheme(readStoredTheme() || getSystemTheme());
});

window.addEventListener("storage", (event) => {
  if (event.key === THEME_STORAGE_KEY) {
    applyTheme(readStoredTheme() || getSystemTheme());
  }
});

document.querySelectorAll(".switch").forEach((button) => {
  button.addEventListener("click", () => button.classList.toggle("is-on"));
});

document.querySelectorAll(".segmented-control").forEach((control) => {
  control.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;

    control.querySelectorAll("button").forEach((item) => item.classList.remove("is-active"));
    button.classList.add("is-active");
  });
});

document.querySelectorAll(".color-dot").forEach((button) => {
  button.addEventListener("click", () => {
    const nextColor = getComputedStyle(button).getPropertyValue("--dot-color").trim();
    document.documentElement.style.setProperty("--color-accent", nextColor);
  });
});
