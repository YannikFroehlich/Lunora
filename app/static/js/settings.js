const settingsShell = document.querySelector(".settings-shell");
const settingsTabsContainer = document.querySelector("[data-settings-tabs]");
const settingsTabList = settingsTabsContainer?.querySelector("[role='tablist']");
const settingsTabs = Array.from(settingsTabsContainer?.querySelectorAll("[data-settings-tab]") || []);
const settingsPanels = Array.from(document.querySelectorAll("[data-settings-panel]"));

function settingsSectionFromHash() {
  const prefix = "#settings-";
  if (!window.location.hash.startsWith(prefix)) return null;
  const section = window.location.hash.slice(prefix.length);
  return settingsTabs.some((tab) => tab.dataset.settingsTab === section) ? section : null;
}

function activateSettingsSection(section, { updateHash = false, focusTab = false } = {}) {
  const activeTab = settingsTabs.find((tab) => tab.dataset.settingsTab === section);
  if (!activeTab) return;

  settingsTabs.forEach((tab) => {
    const isActive = tab === activeTab;
    tab.classList.toggle("is-active", isActive);
    tab.setAttribute("aria-selected", String(isActive));
    tab.tabIndex = isActive ? 0 : -1;
  });

  settingsPanels.forEach((panel) => {
    const isActive = panel.dataset.settingsPanel === section;
    panel.hidden = !isActive;
    panel.classList.toggle("is-active", isActive);
  });

  if (updateHash) {
    const url = new URL(window.location.href);
    url.hash = `settings-${section}`;
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }

  if (focusTab) {
    activeTab.focus();
    activeTab.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
  }
}

if (settingsShell && settingsTabsContainer && settingsTabList && settingsTabs.length && settingsPanels.length) {
  const errorPanel = settingsPanels.find((panel) => panel.querySelector(".errorlist, .form-errors"));
  const initialSection = errorPanel?.dataset.settingsPanel || settingsSectionFromHash() || "appearance";

  settingsShell.classList.add("settings-tabs-ready");
  settingsTabsContainer.hidden = false;
  activateSettingsSection(initialSection);

  settingsTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      activateSettingsSection(tab.dataset.settingsTab, { updateHash: true });
    });
  });

  settingsTabList.addEventListener("keydown", (event) => {
    const currentIndex = settingsTabs.indexOf(document.activeElement);
    if (currentIndex === -1) return;

    let nextIndex = currentIndex;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIndex = (currentIndex + 1) % settingsTabs.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (currentIndex - 1 + settingsTabs.length) % settingsTabs.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = settingsTabs.length - 1;
    } else {
      return;
    }

    event.preventDefault();
    activateSettingsSection(settingsTabs[nextIndex].dataset.settingsTab, {
      updateHash: true,
      focusTab: true,
    });
  });

  window.addEventListener("hashchange", () => {
    const section = settingsSectionFromHash();
    if (section) activateSettingsSection(section);
  });
}

// Keep the custom profile image picker label in sync with the selected file.
document.querySelectorAll("[data-file-input]").forEach((input) => {
  const fileNameLabel = document.querySelector(`[data-file-name-for="${input.id}"]`);

  if (!fileNameLabel) {
    return;
  }

  input.addEventListener("change", () => {
    const fileName = input.files && input.files.length > 0
      ? input.files[0].name
      : "Keine neue Datei ausgewählt";

    fileNameLabel.textContent = fileName;
    fileNameLabel.classList.toggle("has-file", Boolean(input.files && input.files.length));
  });
});

// Keep custom switches visually in sync before the preferences form is saved.
document.querySelectorAll(".switch input[type='checkbox']").forEach((input) => {
  const switchLabel = input.closest(".switch");

  if (!switchLabel) {
    return;
  }

  const syncSwitchState = () => {
    switchLabel.classList.toggle("is-on", input.checked);
  };

  syncSwitchState();
  input.addEventListener("change", syncSwitchState);
});

const desktopNotificationInput = document.querySelector("#id_notify_desktop");
const desktopNotificationStatus = document.querySelector("[data-desktop-notification-status]");
const desktopNotificationPermissionButton = document.querySelector("[data-request-notification-permission]");
const webPushButtonLabel = document.querySelector("[data-web-push-button-label]");
const webPushPublicKey = document.documentElement.dataset.webPushPublicKey;
const webPushSubscriptionUrl = document.documentElement.dataset.webPushSubscriptionUrl;
const webPushTestUrl = document.documentElement.dataset.webPushTestUrl;
const serviceWorkerUrl = document.documentElement.dataset.serviceWorkerUrl;
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
const webPushTestButton = document.querySelector("[data-send-test-web-push]");
let activeWebPushSubscription = null;

function setDesktopNotificationStatus(message, { isError = false } = {}) {
  if (!desktopNotificationStatus) return;
  desktopNotificationStatus.textContent = message;
  desktopNotificationStatus.classList.toggle("is-error", isError);
}

function syncDesktopNotificationSwitch() {
  const switchLabel = desktopNotificationInput?.closest(".switch");
  switchLabel?.classList.toggle("is-on", Boolean(desktopNotificationInput?.checked));
}

function supportsWebPush() {
  return window.isSecureContext
    && "Notification" in window
    && "serviceWorker" in navigator
    && "PushManager" in window;
}

function applicationServerKeyBytes(value) {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
  const decoded = window.atob(base64);
  return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
}

function subscriptionUsesCurrentKey(subscription) {
  const currentKey = subscription?.options?.applicationServerKey;
  if (!currentKey) return true;
  const subscribedBytes = new Uint8Array(currentKey);
  const configuredBytes = applicationServerKeyBytes(webPushPublicKey);
  return subscribedBytes.length === configuredBytes.length
    && subscribedBytes.every((value, index) => value === configuredBytes[index]);
}

async function getServiceWorkerRegistration() {
  await navigator.serviceWorker.register(serviceWorkerUrl, { scope: "/", updateViaCache: "none" });
  return navigator.serviceWorker.ready;
}

async function saveWebPushSubscription(subscription) {
  const response = await fetch(webPushSubscriptionUrl, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-CSRFToken": csrfToken,
    },
    body: JSON.stringify(subscription.toJSON()),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "Das Gerät konnte nicht registriert werden.");
}

async function removeWebPushSubscription(subscription) {
  const response = await fetch(webPushSubscriptionUrl, {
    method: "DELETE",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-CSRFToken": csrfToken,
    },
    body: JSON.stringify({ endpoint: subscription.endpoint }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "Das Gerät konnte nicht abgemeldet werden.");
}

function syncWebPushButton() {
  if (!desktopNotificationPermissionButton || !webPushButtonLabel) return;
  desktopNotificationPermissionButton.hidden = false;
  webPushButtonLabel.textContent = activeWebPushSubscription
    ? "Auf diesem Gerät deaktivieren"
    : "Auf diesem Gerät aktivieren";
  if (webPushTestButton) webPushTestButton.hidden = !activeWebPushSubscription;
}

async function activateWebPush() {
  const registration = await getServiceWorkerRegistration();
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("Die Browserfreigabe wurde nicht erteilt.");
  }

  let subscription = await registration.pushManager.getSubscription();
  let newlyCreated = false;
  if (subscription && !subscriptionUsesCurrentKey(subscription)) {
    await subscription.unsubscribe();
    subscription = null;
  }
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: applicationServerKeyBytes(webPushPublicKey),
    });
    newlyCreated = true;
  }

  try {
    await saveWebPushSubscription(subscription);
  } catch (error) {
    if (newlyCreated) await subscription.unsubscribe().catch(() => false);
    throw error;
  }

  activeWebPushSubscription = subscription;
  desktopNotificationInput.checked = true;
  syncDesktopNotificationSwitch();
  syncWebPushButton();
  setDesktopNotificationStatus("Web Push ist auf diesem Gerät aktiv. Bitte die Einstellung noch speichern.");
}

async function deactivateWebPush() {
  if (!activeWebPushSubscription) return;
  await removeWebPushSubscription(activeWebPushSubscription);
  await activeWebPushSubscription.unsubscribe();
  activeWebPushSubscription = null;
  syncWebPushButton();
  setDesktopNotificationStatus("Dieses Gerät erhält keine Web-Push-Benachrichtigungen mehr.");
}

async function sendWebPushTest() {
  if (!activeWebPushSubscription || !webPushTestUrl) return;
  const response = await fetch(webPushTestUrl, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-CSRFToken": csrfToken,
    },
    body: JSON.stringify({ endpoint: activeWebPushSubscription.endpoint }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "Die Testbenachrichtigung konnte nicht gesendet werden.");
  setDesktopNotificationStatus(payload.message || "Testbenachrichtigung wurde gesendet.");
}

async function initializeWebPushSettings() {
  if (!desktopNotificationInput) return;
  if (!supportsWebPush()) {
    desktopNotificationPermissionButton?.setAttribute("hidden", "");
    setDesktopNotificationStatus("Dieser Browser unterstützt Web Push nicht oder die Verbindung ist nicht sicher.", { isError: true });
    return;
  }
  if (!webPushPublicKey || !webPushSubscriptionUrl || !serviceWorkerUrl || !csrfToken) {
    desktopNotificationPermissionButton?.setAttribute("hidden", "");
    setDesktopNotificationStatus("Web Push ist auf dem Server noch nicht eingerichtet.", { isError: true });
    return;
  }
  if (Notification.permission === "denied") {
    desktopNotificationPermissionButton?.setAttribute("hidden", "");
    setDesktopNotificationStatus("Web-Push-Benachrichtigungen sind im Browser blockiert.", { isError: true });
    return;
  }

  try {
    const registration = await getServiceWorkerRegistration();
    activeWebPushSubscription = await registration.pushManager.getSubscription();
    if (activeWebPushSubscription && !subscriptionUsesCurrentKey(activeWebPushSubscription)) {
      activeWebPushSubscription = null;
      syncWebPushButton();
      setDesktopNotificationStatus("Der Push-Schlüssel wurde geändert. Aktiviere dieses Gerät erneut.");
      return;
    }
    if (activeWebPushSubscription && desktopNotificationInput.checked) {
      await saveWebPushSubscription(activeWebPushSubscription);
    }
  } catch (error) {
    setDesktopNotificationStatus("Der Benachrichtigungsdienst konnte nicht geladen werden.", { isError: true });
    return;
  }

  syncWebPushButton();
  if (activeWebPushSubscription) {
    setDesktopNotificationStatus(
      desktopNotificationInput.checked
        ? "Web Push ist auf diesem Gerät aktiv."
        : "Dieses Gerät ist registriert; die Zustellung ist im Konto deaktiviert."
    );
  } else {
    setDesktopNotificationStatus("Dieses Gerät ist noch nicht für Web Push registriert.");
  }
}

if (desktopNotificationInput) {
  desktopNotificationInput.addEventListener("change", () => {
    if (!desktopNotificationInput.checked) {
      setDesktopNotificationStatus("Die Web-Push-Zustellung wird nach dem Speichern kontoweit pausiert.");
    } else if (activeWebPushSubscription) {
      setDesktopNotificationStatus("Web Push wird nach dem Speichern wieder aktiviert.");
    } else {
      setDesktopNotificationStatus("Aktiviere zusätzlich dieses Gerät für Web Push.");
    }
  });

  desktopNotificationPermissionButton?.addEventListener("click", async () => {
    desktopNotificationPermissionButton.disabled = true;
    try {
      if (activeWebPushSubscription) {
        await deactivateWebPush();
      } else {
        await activateWebPush();
      }
    } catch (error) {
      setDesktopNotificationStatus(error.message || "Web Push konnte nicht geändert werden.", { isError: true });
    } finally {
      desktopNotificationPermissionButton.disabled = false;
    }
  });

  webPushTestButton?.addEventListener("click", async () => {
    webPushTestButton.disabled = true;
    try {
      await sendWebPushTest();
    } catch (error) {
      setDesktopNotificationStatus(error.message || "Die Testbenachrichtigung konnte nicht gesendet werden.", { isError: true });
    } finally {
      webPushTestButton.disabled = false;
    }
  });
}

initializeWebPushSettings();

const notificationQuietToggle = document.querySelector("[data-notification-quiet-toggle]");
const notificationQuietInputs = document.querySelectorAll("[data-notification-quiet-times] input");

function syncNotificationQuietHours() {
  notificationQuietInputs.forEach((input) => {
    input.disabled = !notificationQuietToggle?.checked;
  });
}

notificationQuietToggle?.addEventListener("change", syncNotificationQuietHours);
syncNotificationQuietHours();

const pwaInstallPanel = document.querySelector("[data-pwa-install-panel]");
const pwaInstallButton = pwaInstallPanel?.querySelector("[data-pwa-install]");
const pwaInstallStatus = pwaInstallPanel?.querySelector("[data-pwa-install-status]");

function setPwaInstallStatus(message, { isError = false } = {}) {
  if (!pwaInstallStatus) return;
  pwaInstallStatus.textContent = message;
  pwaInstallStatus.classList.toggle("is-error", isError);
}

function syncPwaInstallState() {
  if (!pwaInstallPanel || !pwaInstallButton || !pwaInstallStatus) return;

  if (window.lunoraPwa?.isInstalled()) {
    pwaInstallButton.hidden = true;
    setPwaInstallStatus("Auf diesem Gerät installiert.");
    return;
  }

  if (window.lunoraPwa?.isInstallable()) {
    pwaInstallButton.hidden = false;
    setPwaInstallStatus("Installation verfügbar.");
    return;
  }

  pwaInstallButton.hidden = true;
  if (!("serviceWorker" in navigator) || !window.isSecureContext) {
    setPwaInstallStatus("Installation in diesem Browser nicht verfügbar.", { isError: true });
  } else {
    setPwaInstallStatus("Im Browser geöffnet.");
  }
}

pwaInstallButton?.addEventListener("click", async () => {
  pwaInstallButton.disabled = true;
  setPwaInstallStatus("Installation wird geöffnet …");

  try {
    const result = await window.lunoraPwa?.install();
    if (result?.outcome === "accepted") {
      setPwaInstallStatus("Installation wird abgeschlossen …");
    } else if (result?.outcome === "dismissed") {
      setPwaInstallStatus("Installation nicht gestartet.");
    } else {
      setPwaInstallStatus("Installation aktuell nicht verfügbar.", { isError: true });
    }
  } catch (error) {
    setPwaInstallStatus("Installation konnte nicht geöffnet werden.", { isError: true });
  } finally {
    pwaInstallButton.disabled = false;
    if (!window.lunoraPwa?.isInstallable()) {
      pwaInstallButton.hidden = true;
    }
  }
});

window.addEventListener("lunora:pwa-state-change", syncPwaInstallState);
window.addEventListener("appinstalled", syncPwaInstallState);
syncPwaInstallState();
