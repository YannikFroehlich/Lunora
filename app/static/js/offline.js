const offlineTheme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
document.documentElement.dataset.theme = offlineTheme;
document.documentElement.style.colorScheme = offlineTheme;

document.addEventListener("DOMContentLoaded", () => {
  document.querySelector("[data-offline-retry]")?.addEventListener("click", () => window.location.reload());
});
