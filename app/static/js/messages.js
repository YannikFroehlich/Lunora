(function () {
  const stream = document.getElementById("message-stream");
  if (stream) {
    stream.scrollTop = stream.scrollHeight;
  }

  const focusKey = "lunoraMessagesFocusCompose";
  const composeTextarea = document.querySelector(".compose-bar textarea");

  if (sessionStorage.getItem(focusKey) === "1") {
    sessionStorage.removeItem(focusKey);

    if (composeTextarea) {
      composeTextarea.focus();
      const caretPosition = composeTextarea.value.length;
      composeTextarea.setSelectionRange(caretPosition, caretPosition);
    }
  }

  const textareas = document.querySelectorAll(".compose-bar textarea, .new-chat-card textarea");

  textareas.forEach((textarea) => {
    textarea.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.isComposing) {
        return;
      }

      if (event.shiftKey) {
        return;
      }

      event.preventDefault();

      const form = textarea.closest("form");
      if (!form) {
        return;
      }

      const formName = form.querySelector('input[name="form_name"]')?.value;
      if (formName === "message" && textarea.value.trim() === "") {
        textarea.focus();
        return;
      }

      sessionStorage.setItem(focusKey, "1");

      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
      } else {
        form.submit();
      }
    });
  });

  const chatPanel = document.querySelector("[data-messages-live-url]");

  const currentUrlParams = () => {
    const currentUrl = new URL(window.location.href);
    const params = new URLSearchParams();

    if (currentUrl.searchParams.has("q")) {
      params.set("q", currentUrl.searchParams.get("q"));
    }

    if (currentUrl.searchParams.has("filter")) {
      params.set("filter", currentUrl.searchParams.get("filter"));
    }

    if (currentUrl.searchParams.has("before")) {
      params.set("before", currentUrl.searchParams.get("before"));
    }

    params.set("_", Date.now().toString());
    return params;
  };

  const isNearBottom = (element) => (
    element.scrollHeight - element.scrollTop - element.clientHeight < 130
  );

  const replaceOuterHtml = (selector, html) => {
    const element = document.querySelector(selector);
    if (element && typeof html === "string") {
      element.outerHTML = html;
    }
  };

  const updateUnreadFilterBadge = (unreadTotal) => {
    const unreadLink = document.querySelector('.filter-row a[href*="filter=unread"]');
    if (!unreadLink) {
      return;
    }

    let badge = unreadLink.querySelector("span");
    if (unreadTotal > 0) {
      if (!badge) {
        badge = document.createElement("span");
        unreadLink.appendChild(badge);
      }
      badge.textContent = unreadTotal;
    } else if (badge) {
      badge.remove();
    }
  };

  const refreshMessages = async () => {
    if (!chatPanel) {
      return;
    }

    const liveUrl = chatPanel.dataset.messagesLiveUrl;
    if (!liveUrl) {
      return;
    }

    const url = new URL(liveUrl, window.location.origin);
    url.search = currentUrlParams().toString();

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

      updateUnreadFilterBadge(Number(data.unread_total || 0));
      replaceOuterHtml("#messages-contact-list", data.contact_list_html);

      const typingIndicator = document.getElementById("typing-indicator");
      if (typingIndicator) {
        typingIndicator.textContent = data.typing_label || "";
      }

      const overview = document.getElementById("messages-overview");
      if (overview && typeof data.overview_html === "string") {
        overview.outerHTML = data.overview_html;
      }

      const pinnedRegion = document.getElementById("pinned-messages-region");
      if (pinnedRegion && typeof data.pinned_messages_html === "string") {
        pinnedRegion.innerHTML = data.pinned_messages_html;
      }

      const composeRegion = document.getElementById("message-compose-region");
      if (composeRegion && typeof data.compose_html === "string") {
        const isBlocked = String(Boolean(data.compose_blocked));
        if (composeRegion.dataset.composeBlocked !== isBlocked) {
          composeRegion.innerHTML = data.compose_html;
          composeRegion.dataset.composeBlocked = isBlocked;
        }
      }

      const messageStream = document.getElementById("message-stream");
      if (messageStream && typeof data.message_stream_html === "string") {
        const shouldScrollDown = isNearBottom(messageStream);
        const previousHeight = messageStream.scrollHeight;
        const previousTop = messageStream.scrollTop;
        messageStream.innerHTML = data.message_stream_html;

        if (shouldScrollDown) {
          messageStream.scrollTop = messageStream.scrollHeight;
        } else {
          messageStream.scrollTop = previousTop + (messageStream.scrollHeight - previousHeight);
        }
      }
    } catch (error) {
      // Beim lokalen Entwickeln soll ein kurzer Verbindungsfehler den Chat nicht stören.
    }
  };

  if (chatPanel) {
    const refreshInterval = 2500;
    window.setInterval(refreshMessages, refreshInterval);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        refreshMessages();
      }
    });
  }

  const csrfToken = () => document.querySelector('meta[name="csrf-token"]')?.content;
  let lastTypingPingAt = 0;
  const TYPING_PING_INTERVAL_MS = 3000;

  document.addEventListener("input", (event) => {
    if (!event.target.matches?.(".compose-bar textarea")) {
      return;
    }

    const typingUrl = chatPanel?.dataset.messagesTypingUrl;
    if (!typingUrl) {
      return;
    }

    const now = Date.now();
    if (now - lastTypingPingAt < TYPING_PING_INTERVAL_MS) {
      return;
    }
    lastTypingPingAt = now;

    fetch(typingUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrfToken(),
      },
    }).catch(() => {
      // Ein verpasster Tippt-Indikator-Ping ist unkritisch, der naechste folgt in Kuerze.
    });
  });

  const actionForm = document.getElementById("message-action-form");
  const contextMenu = document.getElementById("message-context-menu");

  if (!actionForm || !contextMenu) {
    return;
  }

  // Move the custom menu out of the chat panel. Some glass/scroll containers create
  // their own stacking context, which can hide fixed elements even though the
  // right-click event was handled correctly.
  if (contextMenu.parentElement !== document.body) {
    document.body.appendChild(contextMenu);
  }
  if (actionForm.parentElement !== document.body) {
    document.body.appendChild(actionForm);
  }

  const messageIdInput = actionForm.querySelector('input[name="message_id"]');
  const actionInput = actionForm.querySelector('input[name="action"]');
  const emojiInput = actionForm.querySelector('input[name="emoji"]');
  const deleteButton = contextMenu.querySelector('[data-menu-action="delete"]');
  const pinButton = contextMenu.querySelector('[data-menu-action="pin"]');
  const pinLabel = contextMenu.querySelector("[data-menu-pin-label]");
  const replyButton = contextMenu.querySelector('[data-menu-action="reply"]');
  const reactionGroup = contextMenu.querySelector("[data-context-reactions]");
  let activeBubble = null;

  const startReplyTo = (bubble) => {
    const strip = document.getElementById("reply-preview-strip");
    const replyToInput = document.getElementById("compose-reply-to-id");
    if (!strip || !replyToInput) {
      return;
    }

    strip.querySelector("[data-reply-sender]").textContent = bubble.dataset.senderName || "";
    strip.querySelector("[data-reply-text]").textContent = bubble.dataset.preview || "";
    replyToInput.value = bubble.dataset.messageId;
    strip.hidden = false;

    document.querySelector(".compose-bar textarea")?.focus();
  };

  const cancelReply = () => {
    const strip = document.getElementById("reply-preview-strip");
    const replyToInput = document.getElementById("compose-reply-to-id");
    if (strip) {
      strip.hidden = true;
    }
    if (replyToInput) {
      replyToInput.value = "";
    }
  };

  const hideContextMenu = () => {
    contextMenu.hidden = true;
    contextMenu.style.left = "";
    contextMenu.style.top = "";
    activeBubble = null;
  };

  const submitMessageAction = (messageId, action, emoji = "") => {
    messageIdInput.value = messageId;
    actionInput.value = action;
    emojiInput.value = emoji;
    sessionStorage.setItem(focusKey, "1");

    if (typeof actionForm.requestSubmit === "function") {
      actionForm.requestSubmit();
    } else {
      actionForm.submit();
    }
  };

  const placeContextMenu = (point) => {
    contextMenu.hidden = false;
    contextMenu.style.left = "0px";
    contextMenu.style.top = "0px";

    const menuRect = contextMenu.getBoundingClientRect();
    const padding = 10;
    const left = Math.min(point.clientX, window.innerWidth - menuRect.width - padding);
    const top = Math.min(point.clientY, window.innerHeight - menuRect.height - padding);

    contextMenu.style.left = `${Math.max(padding, left)}px`;
    contextMenu.style.top = `${Math.max(padding, top)}px`;
  };

  const openContextMenuForBubble = (bubble, event, point) => {
    const isDeleted = bubble.dataset.isDeleted === "true";
    const canDelete = bubble.dataset.canDelete === "true";
    const canReact = bubble.dataset.canReact === "true";
    const isPinned = bubble.dataset.isPinned === "true";

    // Deleted messages should not swallow the browser menu when no custom action is useful.
    if (isDeleted || (!canDelete && !canReact)) {
      hideContextMenu();
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    activeBubble = bubble;

    if (deleteButton) {
      deleteButton.hidden = !canDelete;
    }

    if (reactionGroup) {
      reactionGroup.hidden = !canReact;
    }

    if (pinButton) {
      pinButton.hidden = isDeleted;
    }

    if (pinLabel) {
      pinLabel.textContent = isPinned ? "Loslösen" : "Anpinnen";
    }

    if (replyButton) {
      replyButton.hidden = bubble.dataset.canReply !== "true";
    }

    placeContextMenu(point || event);
    contextMenu.querySelector("button:not([hidden])")?.focus();
  };

  document.addEventListener("contextmenu", (event) => {
    const bubbleContent = event.target.closest?.(".bubble-content");
    if (!bubbleContent) {
      hideContextMenu();
      return;
    }

    const bubble = bubbleContent.closest(".bubble[data-message-id]");
    if (!bubble) {
      hideContextMenu();
      return;
    }

    openContextMenuForBubble(bubble, event);
  });

  document.addEventListener("keydown", (event) => {
    const bubble = event.target.closest?.(".bubble[data-message-id]");
    if (!bubble) {
      return;
    }

    if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) {
      return;
    }

    const rect = bubble.getBoundingClientRect();
    openContextMenuForBubble(bubble, event, {
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + Math.min(rect.height, 44),
    });
  });

  contextMenu.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-menu-action]");
    if (!button || !activeBubble) {
      return;
    }

    const action = button.dataset.menuAction;
    const bubble = activeBubble;
    hideContextMenu();

    if (action === "reply") {
      startReplyTo(bubble);
      return;
    }

    submitMessageAction(bubble.dataset.messageId, action, button.dataset.emoji || "");
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest("#cancel-reply-button")) {
      cancelReply();
      return;
    }

    if (event.target.closest("#cancel-attachment-button")) {
      const chip = document.getElementById("attachment-preview-chip");
      const input = document.getElementById("compose-attachment-input");
      if (input) {
        input.value = "";
      }
      if (chip) {
        chip.hidden = true;
      }
      return;
    }

    const replyQuote = event.target.closest?.(".reply-quote[data-reply-jump]");
    if (replyQuote) {
      const target = document.getElementById(`message-${replyQuote.dataset.replyJump}`);
      if (target) {
        event.preventDefault();
        target.scrollIntoView({ behavior: "smooth", block: "center" });
        target.classList.add("is-highlighted");
        window.setTimeout(() => target.classList.remove("is-highlighted"), 1400);
      }
    }
  });

  document.addEventListener("change", (event) => {
    if (!event.target.matches?.("#compose-attachment-input")) {
      return;
    }

    const chip = document.getElementById("attachment-preview-chip");
    const file = event.target.files?.[0];
    if (!chip) {
      return;
    }

    if (file) {
      chip.querySelector("[data-attachment-preview-name]").textContent = file.name;
      chip.hidden = false;
    } else {
      chip.hidden = true;
    }
  });

  document.addEventListener("click", (event) => {
    const reactionButton = event.target.closest?.(".reaction-pill[data-message-id][data-emoji]");
    if (reactionButton) {
      submitMessageAction(reactionButton.dataset.messageId, "reaction", reactionButton.dataset.emoji);
      return;
    }

    if (!contextMenu.hidden && !contextMenu.contains(event.target)) {
      hideContextMenu();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      hideContextMenu();
    }
  });

  window.addEventListener("resize", hideContextMenu);
  window.addEventListener("scroll", hideContextMenu, true);
})();
