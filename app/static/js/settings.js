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

function setDesktopNotificationStatus(message, { isError = false } = {}) {
  if (!desktopNotificationStatus) return;
  desktopNotificationStatus.textContent = message;
  desktopNotificationStatus.classList.toggle("is-error", isError);
}

function syncDesktopNotificationSwitch() {
  const switchLabel = desktopNotificationInput?.closest(".switch");
  switchLabel?.classList.toggle("is-on", Boolean(desktopNotificationInput?.checked));
}

if (desktopNotificationInput) {
  if (!("Notification" in window)) {
    desktopNotificationPermissionButton?.setAttribute("hidden", "");
    setDesktopNotificationStatus("Dieser Browser unterstützt keine Desktop-Benachrichtigungen.", { isError: true });
  } else if (Notification.permission === "denied") {
    desktopNotificationPermissionButton?.setAttribute("hidden", "");
    setDesktopNotificationStatus("Desktop-Benachrichtigungen sind im Browser blockiert.", { isError: true });
  } else if (Notification.permission === "granted" && desktopNotificationInput.checked) {
    desktopNotificationPermissionButton?.setAttribute("hidden", "");
    setDesktopNotificationStatus("Desktop-Benachrichtigungen sind für dieses Gerät aktiv.");
  }

  desktopNotificationInput.addEventListener("change", async () => {
    if (!desktopNotificationInput.checked) {
      setDesktopNotificationStatus("Desktop-Benachrichtigungen werden nach dem Speichern deaktiviert.");
      return;
    }

    if (!("Notification" in window)) {
      desktopNotificationInput.checked = false;
      syncDesktopNotificationSwitch();
      setDesktopNotificationStatus("Dieser Browser unterstützt keine Desktop-Benachrichtigungen.", { isError: true });
      return;
    }

    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      desktopNotificationInput.checked = false;
      syncDesktopNotificationSwitch();
      setDesktopNotificationStatus("Ohne Browserfreigabe können keine Desktop-Hinweise zugestellt werden.", { isError: true });
      return;
    }

    setDesktopNotificationStatus("Desktop-Benachrichtigungen sind für dieses Gerät freigegeben.");
    desktopNotificationPermissionButton?.setAttribute("hidden", "");
  });

  desktopNotificationPermissionButton?.addEventListener("click", async () => {
    if (!("Notification" in window)) return;

    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      setDesktopNotificationStatus("Die Browserfreigabe wurde nicht erteilt.", { isError: true });
      return;
    }

    desktopNotificationInput.checked = true;
    syncDesktopNotificationSwitch();
    desktopNotificationPermissionButton.setAttribute("hidden", "");
    setDesktopNotificationStatus("Browserfreigabe erteilt. Bitte die Benachrichtigungseinstellungen noch speichern.");
  });
}

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
