const themeToggleButton = document.querySelector(".theme-toggle");
const themeToggleIcon = themeToggleButton?.querySelector("i");

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

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("lunora-theme", theme);
  updateThemeButton(theme);
}

updateThemeButton(getCurrentTheme());

themeToggleButton?.addEventListener("click", () => {
  const nextTheme = getCurrentTheme() === "dark" ? "light" : "dark";
  setTheme(nextTheme);
});
