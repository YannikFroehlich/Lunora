(function () {
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
