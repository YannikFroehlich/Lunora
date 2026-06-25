const themeToggleButton = document.querySelector(".theme-toggle");
const themeToggleIcon = themeToggleButton?.querySelector("i");
const themeChoiceButtons = document.querySelectorAll("[data-theme-choice]");

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
  });
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("lunora-theme", theme);
  updateThemeButton(theme);
  updateThemeChoices(theme);
}

setTheme(getCurrentTheme());

themeToggleButton?.addEventListener("click", () => {
  const nextTheme = getCurrentTheme() === "dark" ? "light" : "dark";
  setTheme(nextTheme);
});

themeChoiceButtons.forEach((button) => {
  button.addEventListener("click", () => setTheme(button.dataset.themeChoice));
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
