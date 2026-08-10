const THEME_STORAGE_KEY = "lunora-theme";
const SCROLL_RESTORE_STORAGE_KEY = "lunora-pending-scroll-restore";
const SCROLL_RESTORE_MAX_AGE_MS = 60 * 1000;
const isAuthenticated = document.documentElement.dataset.authenticated === "true";
const themeToggleButton = document.querySelector(".theme-toggle");
const themeToggleIcon = themeToggleButton?.querySelector("i");
const themeChoiceButtons = document.querySelectorAll("[data-theme-choice]");

function getCurrentPageKey() {
  return `${window.location.pathname}${window.location.search}`;
}

function saveScrollPosition() {
  try {
    sessionStorage.setItem(
      SCROLL_RESTORE_STORAGE_KEY,
      JSON.stringify({
        page: getCurrentPageKey(),
        left: window.scrollX,
        top: window.scrollY,
        savedAt: Date.now(),
      })
    );
  } catch (error) {
    // Navigating still works normally if session storage is unavailable.
  }
}

function restoreScrollPosition() {
  let savedPosition = null;

  try {
    const storedValue = sessionStorage.getItem(SCROLL_RESTORE_STORAGE_KEY);
    sessionStorage.removeItem(SCROLL_RESTORE_STORAGE_KEY);
    savedPosition = storedValue ? JSON.parse(storedValue) : null;
  } catch (error) {
    return;
  }

  const isRecent = savedPosition
    && Number.isFinite(savedPosition.left)
    && Number.isFinite(savedPosition.top)
    && Date.now() - savedPosition.savedAt <= SCROLL_RESTORE_MAX_AGE_MS;

  if (!isRecent || savedPosition.page !== getCurrentPageKey()) {
    return;
  }

  // Wait until the browser has laid out the newly rendered page before scrolling.
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      window.scrollTo({
        left: savedPosition.left,
        top: savedPosition.top,
        behavior: "auto",
      });
    });
  });
}

restoreScrollPosition();
window.addEventListener("pagehide", saveScrollPosition);

function getInitialTheme() {
  const serverTheme = document.documentElement.dataset.theme;
  if (isAuthenticated && ["light", "dark"].includes(serverTheme)) {
    return serverTheme;
  }
  return readStoredTheme() || serverTheme || getSystemTheme();
}

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
    const input = button.querySelector("input[type='radio']");
    if (input) {
      input.checked = button.dataset.themeChoice === theme;
    }
  });
}

function applyTheme(theme, { persist = false } = {}) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;

  if (persist && !isAuthenticated) {
    saveStoredTheme(theme);
  }

  updateThemeButton(theme);
  updateThemeChoices(theme);
}

function setAccentPreview(color) {
  if (!color) return;
  document.documentElement.style.setProperty("--color-accent", color);
  document.documentElement.style.setProperty("--color-accent-strong", color);
}

function applySoftnessPreview(value) {
  const normalized = Math.max(0, Math.min(100, Number(value))) / 100;
  document.documentElement.style.setProperty("--background-overlay-alpha", (0.14 + normalized * 0.34).toFixed(2));
  document.documentElement.style.setProperty("--background-highlight-alpha", (0.20 + normalized * 0.28).toFixed(2));
  document.documentElement.style.setProperty("--glass-blur", `${18 + normalized * 18}px`);
}

applyTheme(getInitialTheme());

themeToggleButton?.addEventListener("click", () => {
  const nextTheme = getCurrentTheme() === "dark" ? "light" : "dark";
  applyTheme(nextTheme, { persist: true });
});

themeChoiceButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const input = button.querySelector("input[type='radio']");
    if (input) {
      input.checked = true;
    }
    applyTheme(button.dataset.themeChoice, { persist: true });
  });
});

window.addEventListener("pageshow", () => {
  applyTheme(getInitialTheme());
});

window.addEventListener("storage", (event) => {
  if (!isAuthenticated && event.key === THEME_STORAGE_KEY) {
    applyTheme(readStoredTheme() || getSystemTheme());
  }
});

document.querySelectorAll(".switch").forEach((control) => {
  const checkbox = control.querySelector("input[type='checkbox']");
  if (!checkbox) {
    control.addEventListener("click", () => control.classList.toggle("is-on"));
    return;
  }

  const syncSwitchState = () => {
    control.classList.toggle("is-on", checkbox.checked);
  };

  syncSwitchState();
  checkbox.addEventListener("change", syncSwitchState);
});

document.querySelectorAll(".segmented-control").forEach((control) => {
  control.addEventListener("click", (event) => {
    const item = event.target.closest("label, button");
    if (!item) return;

    const input = item.querySelector("input[type='radio']");
    if (input) {
      input.checked = true;
      document.documentElement.dataset.density = input.value;
    }

    control.querySelectorAll("label, button").forEach((option) => option.classList.remove("is-active"));
    item.classList.add("is-active");
  });
});

document.querySelectorAll(".color-dot").forEach((control) => {
  control.addEventListener("click", () => {
    const input = control.querySelector("input[type='radio']");
    if (input) {
      input.checked = true;
    }
    document.querySelectorAll(".color-dot").forEach((dot) => dot.classList.remove("is-active"));
    control.classList.add("is-active");
    const nextColor = getComputedStyle(control).getPropertyValue("--dot-color").trim();
    setAccentPreview(nextColor);
  });
});

document.querySelectorAll(".softness-slider").forEach((slider) => {
  slider.addEventListener("input", () => applySoftnessPreview(slider.value));
});

document.querySelector("#appearance-form")?.addEventListener("reset", () => {
  window.setTimeout(() => window.location.reload(), 0);
});

function initFlashMessages() {
  const stack = document.querySelector("[data-flash-stack]");
  if (!stack) return;

  const removeMessage = (message) => {
    if (!message || message.dataset.flashClosing === "true") return;

    message.dataset.flashClosing = "true";
    message.classList.add("is-leaving");

    window.setTimeout(() => {
      const currentStack = message.closest("[data-flash-stack]");
      message.remove();

      if (currentStack && !currentStack.querySelector("[data-flash-message]")) {
        currentStack.remove();
      }
    }, 220);
  };

  if (stack.dataset.flashReady !== "true") {
    stack.dataset.flashReady = "true";
    stack.addEventListener("click", (event) => {
      const dismissButton = event.target.closest("[data-flash-dismiss]");
      if (!dismissButton) return;

      event.preventDefault();
      removeMessage(dismissButton.closest("[data-flash-message]"));
    });
  }

  stack.querySelectorAll("[data-flash-message]").forEach((message) => {
    if (message.dataset.flashTimerReady === "true") return;

    message.dataset.flashTimerReady = "true";
    const timeout = message.classList.contains("is-error") ? 7000 : 4200;
    window.setTimeout(() => removeMessage(message), timeout);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initFlashMessages);
} else {
  initFlashMessages();
}

window.addEventListener("pageshow", initFlashMessages);

function initDesktopReminderNotifications() {
  const claimUrl = document.documentElement.dataset.notificationClaimUrl;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
  if (!isAuthenticated || !claimUrl || !csrfToken || !("Notification" in window)) return;
  if (Notification.permission !== "granted") return;

  let claimInProgress = false;
  const claimNotifications = async () => {
    if (claimInProgress) return;
    claimInProgress = true;
    try {
      const response = await fetch(claimUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "X-CSRFToken": csrfToken,
        },
      });
      if (!response.ok) return;
      const payload = await response.json();
      (payload.notifications || []).forEach((item) => {
        const notification = new Notification(item.title, {
          body: item.due_label ? `Fällig: ${item.due_label}` : "Eine Lunora-Erinnerung ist fällig.",
          tag: `lunora-reminder-${item.id}`,
        });
        notification.addEventListener("click", () => {
          window.focus();
          window.location.href = item.url;
          notification.close();
        });
      });
    } catch (error) {
      // A later polling cycle retries if the browser or network is temporarily unavailable.
    } finally {
      claimInProgress = false;
    }
  };

  window.setTimeout(claimNotifications, 1500);
  window.setInterval(claimNotifications, 60 * 1000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initDesktopReminderNotifications);
} else {
  initDesktopReminderNotifications();
}
