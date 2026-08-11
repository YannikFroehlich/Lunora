(function () {
  const GERMAN_WEEKDAY_NAMES = [
    "Sonntag",
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
  ];

  const GERMAN_MONTH_NAMES = [
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
  ];

  function initDashboardClock() {
    const panel = document.querySelector("[data-home-clock]");
    if (!panel) {
      return;
    }

    const timeZone = panel.dataset.clockTimezone || undefined;
    const timeFormat = panel.dataset.clockTimeFormat || "24h";

    const timeEl = panel.querySelector("[data-clock-time]");
    const weekdayEl = panel.querySelector("[data-clock-weekday]");
    const dayEl = panel.querySelector("[data-clock-day]");
    const monthEl = panel.querySelector("[data-clock-month]");
    const yearEl = panel.querySelector("[data-clock-year]");

    let partsFormatter;
    try {
      partsFormatter = new Intl.DateTimeFormat("en-US", {
        timeZone,
        hourCycle: "h23",
        hour: "2-digit",
        minute: "2-digit",
        weekday: "short",
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
      });
    } catch (error) {
      // Unknown/unsupported timezone identifier: fall back to the browser's local time.
      partsFormatter = new Intl.DateTimeFormat("en-US", {
        hourCycle: "h23",
        hour: "2-digit",
        minute: "2-digit",
        weekday: "short",
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
      });
    }

    const WEEKDAY_SHORT_TO_INDEX = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

    function formatTime(hour24, minute) {
      if (timeFormat === "12h") {
        const hour12 = hour24 % 12 === 0 ? 12 : hour24 % 12;
        const period = hour24 < 12 ? "AM" : "PM";
        return `${hour12}:${minute} ${period}`;
      }
      return `${String(hour24).padStart(2, "0")}:${minute}`;
    }

    function update() {
      const parts = partsFormatter.formatToParts(new Date());
      const lookup = {};
      parts.forEach((part) => {
        lookup[part.type] = part.value;
      });

      if (timeEl) {
        timeEl.textContent = formatTime(Number(lookup.hour), lookup.minute);
      }
      if (weekdayEl) {
        weekdayEl.textContent = GERMAN_WEEKDAY_NAMES[WEEKDAY_SHORT_TO_INDEX[lookup.weekday]];
      }
      if (dayEl) {
        dayEl.textContent = lookup.day;
      }
      if (monthEl) {
        monthEl.textContent = GERMAN_MONTH_NAMES[Number(lookup.month) - 1];
      }
      if (yearEl) {
        yearEl.textContent = lookup.year;
      }
    }

    update();
    window.setInterval(update, 15000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        update();
      }
    });
  }

  initDashboardClock();

  const liveRegion = document.querySelector("[data-home-messages-live-url]");
  const badge = document.querySelector("[data-home-messages-badge]");

  if (!liveRegion || !badge) {
    return;
  }

  const setBadgeCount = (count) => {
    const unreadCount = Math.max(0, Number(count || 0));
    badge.textContent = unreadCount.toString();
    badge.setAttribute("aria-label", `${unreadCount} ungelesene Nachrichten`);
    badge.classList.toggle("is-hidden", unreadCount === 0);
  };

  const refreshUnreadMessages = async () => {
    const liveUrl = liveRegion.dataset.homeMessagesLiveUrl;
    if (!liveUrl) {
      return;
    }

    const url = new URL(liveUrl, window.location.origin);
    url.searchParams.set("_", Date.now().toString());

    try {
      const response = await fetch(url, {
        method: "GET",
        credentials: "same-origin",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "Accept": "application/json",
        },
      });

      if (!response.ok) {
        return;
      }

      const data = await response.json();
      if (!data.ok) {
        return;
      }

      setBadgeCount(data.unread_total);
    } catch (error) {
      // Lokale Netzwerk-/Reload-Unterbrechungen sollen die Home-Seite nicht stören.
    }
  };

  window.setInterval(refreshUnreadMessages, 5000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      refreshUnreadMessages();
    }
  });
})();
