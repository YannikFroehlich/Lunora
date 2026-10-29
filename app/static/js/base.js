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
      }),
    );
  } catch (_error) {
    // Navigating still works normally if session storage is unavailable.
  }
}

function restoreScrollPosition() {
  let savedPosition = null;

  try {
    const storedValue = sessionStorage.getItem(SCROLL_RESTORE_STORAGE_KEY);
    sessionStorage.removeItem(SCROLL_RESTORE_STORAGE_KEY);
    savedPosition = storedValue ? JSON.parse(storedValue) : null;
  } catch (_error) {
    return;
  }

  const isRecent =
    savedPosition &&
    Number.isFinite(savedPosition.left) &&
    Number.isFinite(savedPosition.top) &&
    Date.now() - savedPosition.savedAt <= SCROLL_RESTORE_MAX_AGE_MS;

  if (!isRecent || savedPosition.page !== getCurrentPageKey()) {
    return;
  }

  const applyScroll = () => {
    window.scrollTo({
      left: savedPosition.left,
      top: savedPosition.top,
      behavior: "auto",
    });
  };

  // The script runs at the end of <body>, so the DOM is already parsed and
  // laid out - but pages with images/fonts can still grow after that, so the
  // scroll is re-applied once everything has finished loading. This avoids
  // requestAnimationFrame, which browsers throttle or never fire for
  // background/non-composited tabs.
  applyScroll();
  if (document.readyState !== "complete") {
    window.addEventListener("load", applyScroll, { once: true });
  }
}

let scrollSaveTimeoutId = null;

function scheduleScrollSave() {
  if (scrollSaveTimeoutId !== null) {
    return;
  }
  scrollSaveTimeoutId = window.setTimeout(() => {
    scrollSaveTimeoutId = null;
    saveScrollPosition();
  }, 200);
}

restoreScrollPosition();
// pagehide/beforeunload timing around redirects (e.g. a form save that
// bounces back to the same page) isn't reliable across browsers, so the
// position is kept fresh continuously rather than only at the last moment.
window.addEventListener("scroll", scheduleScrollSave, { passive: true });
document.addEventListener("submit", saveScrollPosition, { capture: true });
window.addEventListener("pagehide", saveScrollPosition);

// Signed-in users choose light, dark or "system" in their profile; anonymous visitors keep a
// per-browser choice in localStorage and otherwise follow the system.
function getThemePreference() {
  if (isAuthenticated) {
    return document.documentElement.dataset.themePreference || "light";
  }
  return readStoredTheme() || "system";
}

function getInitialTheme() {
  const preference = getThemePreference();
  return preference === "system" ? getSystemTheme() : preference;
}

function readStoredTheme() {
  try {
    const theme = localStorage.getItem(THEME_STORAGE_KEY);
    return ["light", "dark"].includes(theme) ? theme : null;
  } catch (_error) {
    return null;
  }
}

function saveStoredTheme(theme) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch (_error) {
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
  themeToggleButton.setAttribute("aria-label", isDarkMode ? "Switch to light mode" : "Switch to dark mode");
}

function updateThemeChoices() {
  const preference = getThemePreference();
  themeChoiceButtons.forEach((button) => {
    const isActive = button.dataset.themeChoice === preference;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
    const input = button.querySelector("input[type='radio']");
    if (input) {
      input.checked = isActive;
    }
  });
}

// Mobile browsers tint their toolbar with theme-color; follow the page background token.
// A fresh probe element reads it, since <body> may still be mid-transition right after a switch,
// and a canvas normalizes the color-mix()/color(srgb …) result to plain rgb.
function syncThemeColorMeta() {
  const meta = document.querySelector("meta[name='theme-color']");
  const context = document.createElement("canvas").getContext("2d");
  if (!meta || !context) return;

  const probe = document.createElement("span");
  probe.hidden = true;
  probe.style.backgroundColor = "var(--bg-base)";
  document.body.append(probe);
  const background = getComputedStyle(probe).backgroundColor;
  probe.remove();

  const sentinel = "#010203";
  context.fillStyle = sentinel;
  context.fillStyle = background;
  if (context.fillStyle === sentinel) return;

  context.fillRect(0, 0, 1, 1);
  const [red, green, blue] = context.getImageData(0, 0, 1, 1).data;
  meta.setAttribute("content", `rgb(${red}, ${green}, ${blue})`);
}

function applyTheme(theme, { persist = false } = {}) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;

  if (persist && !isAuthenticated) {
    saveStoredTheme(theme);
  }

  updateThemeButton(theme);
  updateThemeChoices();
  syncThemeColorMeta();
}

function setAccentPreview(color) {
  if (!color) return;
  document.documentElement.style.setProperty("--color-accent", color);
}

function applySoftnessPreview(value) {
  const normalized = Math.max(0, Math.min(100, Number(value))) / 100;
  document.documentElement.style.setProperty("--background-overlay-alpha", (0.14 + normalized * 0.34).toFixed(2));
  document.documentElement.style.setProperty("--background-highlight-alpha", (0.2 + normalized * 0.28).toFixed(2));
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
    if (isAuthenticated) {
      document.documentElement.dataset.themePreference = button.dataset.themeChoice;
      applyTheme(getInitialTheme());
    } else {
      applyTheme(button.dataset.themeChoice, { persist: true });
    }
  });
});

window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (getThemePreference() === "system") {
    applyTheme(getInitialTheme());
  }
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
    syncThemeColorMeta();
  });
});

document.querySelectorAll("[data-color-theme-choice]").forEach((control) => {
  control.addEventListener("click", () => {
    const input = control.querySelector("input[type='radio']");
    if (input) {
      input.checked = true;
    }
    document
      .querySelectorAll("[data-color-theme-choice]")
      .forEach((choice) => choice.classList.toggle("is-active", choice === control));
    document.documentElement.dataset.colorTheme = control.dataset.colorThemeChoice;
    syncThemeColorMeta();
  });
});

document.querySelectorAll(".theme-strength-slider").forEach((slider) => {
  slider.addEventListener("input", () => {
    const ratio = Math.max(0, Math.min(100, Number(slider.value))) / 100;
    document.documentElement.style.setProperty("--theme-strength", ratio.toFixed(2));
    syncThemeColorMeta();
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

async function hasActiveWebPushSubscription() {
  const pushUrl = document.documentElement.dataset.webPushSubscriptionUrl;
  if (!pushUrl || !("serviceWorker" in navigator) || !("PushManager" in window)) return false;
  try {
    const registration = await Promise.race([
      navigator.serviceWorker.ready,
      new Promise((resolve) => window.setTimeout(() => resolve(null), 4000)),
    ]);
    if (!registration) return false;
    return Boolean(await registration.pushManager.getSubscription());
  } catch (_error) {
    return false;
  }
}

async function initDesktopReminderNotifications() {
  const claimUrl = document.documentElement.dataset.notificationClaimUrl;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
  if (!isAuthenticated || !claimUrl || !csrfToken || !("Notification" in window)) return;
  if (Notification.permission !== "granted") return;
  if (await hasActiveWebPushSubscription()) return;

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
          body: item.body || (item.due_label ? `Fällig: ${item.due_label}` : "Eine Lunora-Erinnerung ist fällig."),
          tag: `lunora-notification-${item.id}`,
        });
        notification.addEventListener("click", () => {
          window.focus();
          window.location.href = item.url;
          notification.close();
        });
      });
    } catch (_error) {
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

const pwaInstallState = {
  deferredPrompt: null,
  installed: window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true,
};

function announcePwaState() {
  window.dispatchEvent(
    new CustomEvent("lunora:pwa-state-change", {
      detail: {
        installable: Boolean(pwaInstallState.deferredPrompt),
        installed: pwaInstallState.installed,
      },
    }),
  );
}

window.lunoraPwa = {
  isInstalled() {
    return pwaInstallState.installed;
  },
  isInstallable() {
    return Boolean(pwaInstallState.deferredPrompt);
  },
  async install() {
    if (!pwaInstallState.deferredPrompt) {
      return { outcome: "unavailable" };
    }

    const prompt = pwaInstallState.deferredPrompt;
    await prompt.prompt();
    const choice = await prompt.userChoice;
    pwaInstallState.deferredPrompt = null;
    announcePwaState();
    return choice;
  },
};

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  pwaInstallState.deferredPrompt = event;
  announcePwaState();
});

window.addEventListener("appinstalled", () => {
  pwaInstallState.deferredPrompt = null;
  pwaInstallState.installed = true;
  announcePwaState();
});

function normalizeCommandText(value) {
  return value.toLowerCase().normalize("NFD").replace(/\p{M}/gu, "");
}

// Ctrl/Cmd+K opens a palette of pages and settings; typing filters it, Enter opens the
// highlighted entry or falls back to the global search for the typed text.
function initCommandPalette() {
  const dialog = document.querySelector("[data-command-palette]");
  if (!dialog) return;
  const form = dialog.querySelector("[data-command-form]");
  const input = dialog.querySelector("[data-command-input]");
  const searchOption = dialog.querySelector("[data-command-search]");
  const queryLabel = dialog.querySelector("[data-command-query]");
  const items = Array.from(dialog.querySelectorAll("[data-command-item]")).map((element) => ({
    element,
    text: normalizeCommandText(`${element.textContent} ${element.dataset.keywords || ""}`),
  }));
  let visible = [];
  let activeIndex = 0;

  function setActive(index) {
    activeIndex = index;
    const active = visible[index];
    // Clear every entry, not just visible ones, so a filtered-out item never keeps the highlight.
    [...items.map((item) => item.element), searchOption].forEach((element) =>
      element.classList.toggle("is-active", element === active),
    );
    if (active) {
      input.setAttribute("aria-activedescendant", active.id);
      active.scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function filter() {
    const query = input.value.trim();
    const words = normalizeCommandText(query).split(/\s+/).filter(Boolean);
    const matches = [];
    items.forEach((item) => {
      const match = words.every((word) => item.text.includes(word));
      item.element.hidden = !match;
      if (match) matches.push(item.element);
    });
    searchOption.hidden = !query;
    queryLabel.textContent = query;
    searchOption.href = `${form.action}?${new URLSearchParams({ q: query })}`;
    // Commands come first; the search entry stays reachable at the end of the list.
    visible = query ? [...matches, searchOption] : matches;
    setActive(0);
  }

  function open() {
    input.value = "";
    filter();
    dialog.showModal();
    input.focus();
  }

  input.addEventListener("input", filter);
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!visible.length) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((activeIndex + step + visible.length) % visible.length);
    } else if (event.key === "Enter" && visible[activeIndex]) {
      event.preventDefault();
      window.location.href = visible[activeIndex].href;
    }
  });
  // A click on the backdrop lands on the dialog element itself, outside the panel.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key.toLowerCase() !== "k" || !(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    if (dialog.open) {
      dialog.close();
    } else {
      open();
    }
  });
}

initCommandPalette();

function registerLunoraServiceWorker() {
  const serviceWorkerUrl = document.documentElement.dataset.serviceWorkerUrl;
  if (!serviceWorkerUrl || !("serviceWorker" in navigator) || !window.isSecureContext) return;

  const register = () => {
    navigator.serviceWorker
      .register(serviceWorkerUrl, {
        scope: "/",
        updateViaCache: "none",
      })
      .catch(() => {
        // The app remains fully usable online if registration is blocked.
      });
  };

  if (document.readyState === "complete") {
    register();
  } else {
    window.addEventListener("load", register, { once: true });
  }
}

registerLunoraServiceWorker();

// Forms carrying data-confirm ask before submitting (e.g. destructive delete buttons).
document.addEventListener("submit", (event) => {
  const message = event.target.dataset?.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});

// Remember the last visited app sections in this browser, newest first, so the dashboard's
// "Zuletzt genutzt" widget (home.js) can order its tiles without any server-side tracking.
const RECENT_SECTIONS_STORAGE_KEY = "lunora-recent-sections";

function rememberRecentSection() {
  const section = window.location.pathname.split("/")[1];
  if (!isAuthenticated || !section || section === "home") return;
  try {
    const path = `/${section}/`;
    const stored = JSON.parse(localStorage.getItem(RECENT_SECTIONS_STORAGE_KEY) || "[]");
    const recent = Array.isArray(stored) ? stored.filter((entry) => typeof entry === "string" && entry !== path) : [];
    localStorage.setItem(RECENT_SECTIONS_STORAGE_KEY, JSON.stringify([path, ...recent].slice(0, 12)));
  } catch {
    // Storage can be unavailable (private mode, blocked site data); the widget then keeps its default order.
  }
}

rememberRecentSection();
