(function () {
  const stream = document.getElementById("message-stream");
  // A search hit links to #hit-<id> (not the element's own id, so the browser doesn't scroll the
  // whole page); center it in the stream and flash it instead of jumping to the newest message.
  const linkedMessage = window.location.hash.startsWith("#hit-")
    ? document.getElementById(`message-${window.location.hash.slice(5)}`)
    : null;
  if (linkedMessage && stream) {
    const offset = linkedMessage.getBoundingClientRect().top - stream.getBoundingClientRect().top;
    stream.scrollTop += offset - (stream.clientHeight - linkedMessage.offsetHeight) / 2;
    linkedMessage.classList.add("is-highlighted");
    window.setTimeout(() => linkedMessage.classList.remove("is-highlighted"), 1400);
  } else if (stream) {
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

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-open-new-chat]");
    if (!trigger) {
      return;
    }

    const newChatCard = document.querySelector("[data-new-chat-card]");
    if (!newChatCard) {
      return;
    }

    event.preventDefault();
    newChatCard.open = true;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    newChatCard.scrollIntoView({
      block: "start",
      behavior: reduceMotion ? "auto" : "smooth",
    });

    const focusTarget = newChatCard.querySelector(
      'input[name="recipient"]:not(:disabled), textarea:not(:disabled), button[type="submit"]:not(:disabled)',
    );
    window.setTimeout(() => focusTarget?.focus({ preventScroll: true }), reduceMotion ? 0 : 220);
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

    if (currentUrl.searchParams.has("around")) {
      params.set("around", currentUrl.searchParams.get("around"));
    }

    params.set("_", Date.now().toString());
    return params;
  };

  const isNearBottom = (element) => element.scrollHeight - element.scrollTop - element.clientHeight < 130;

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

  // Last server-rendered HTML per live region, so unchanged regions skip the DOM swap.
  const lastLiveHtml = {};
  const liveHtmlChanged = (key, html) => {
    if (typeof html !== "string" || lastLiveHtml[key] === html) {
      return false;
    }
    lastLiveHtml[key] = html;
    return true;
  };
  let refreshInFlight = false;
  // Server state digest from the last poll; an unchanged digest gets a tiny "unchanged" reply.
  let liveFingerprint = "";

  const refreshMessages = async () => {
    // Hidden tabs skip polling (and implicit mark-as-read); visibilitychange refreshes on return.
    if (!chatPanel || document.hidden || refreshInFlight) {
      return;
    }

    const liveUrl = chatPanel.dataset.messagesLiveUrl;
    if (!liveUrl) {
      return;
    }

    const url = new URL(liveUrl, window.location.origin);
    url.search = currentUrlParams().toString();
    if (liveFingerprint) {
      url.searchParams.set("fp", liveFingerprint);
    }

    refreshInFlight = true;
    try {
      const response = await fetch(url, {
        method: "GET",
        credentials: "same-origin",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          Accept: "application/json",
        },
      });

      if (!response.ok) {
        return;
      }

      const data = await response.json();
      if (!data.ok) {
        return;
      }
      liveFingerprint = data.fingerprint || "";
      if (data.unchanged) {
        return;
      }

      updateUnreadFilterBadge(Number(data.unread_total || 0));
      if (liveHtmlChanged("contacts", data.contact_list_html)) {
        replaceOuterHtml("#messages-contact-list", data.contact_list_html);
      }

      const typingIndicator = document.getElementById("typing-indicator");
      if (typingIndicator) {
        typingIndicator.textContent = data.typing_label || "";
      }

      const overview = document.getElementById("messages-overview");
      if (overview && liveHtmlChanged("overview", data.overview_html)) {
        overview.outerHTML = data.overview_html;
      }

      const scheduledRegion = document.getElementById("scheduled-messages-region");
      if (scheduledRegion && liveHtmlChanged("scheduled", data.scheduled_messages_html)) {
        scheduledRegion.innerHTML = data.scheduled_messages_html;
      }

      const pinnedRegion = document.getElementById("pinned-messages-region");
      if (pinnedRegion && liveHtmlChanged("pinned", data.pinned_messages_html)) {
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
      if (messageStream && liveHtmlChanged("stream", data.message_stream_html)) {
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
    } catch (_error) {
      // Beim lokalen Entwickeln soll ein kurzer Verbindungsfehler den Chat nicht stören.
    } finally {
      refreshInFlight = false;
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

  // "+" menu in the compose bar: polls (group chats) and "Später senden".
  const composeMoreMenu = () => document.getElementById("compose-more-menu");

  const setComposeMoreOpen = (open) => {
    const menu = composeMoreMenu();
    const button = document.getElementById("compose-more-button");
    if (!menu || !button) {
      return;
    }
    menu.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    if (open) {
      menu.querySelector("button")?.focus();
    }
  };

  const setScheduleActive = (active) => {
    const strip = document.getElementById("compose-schedule-strip");
    const input = document.getElementById("compose-send-at");
    const sendButton = document.querySelector(".compose-bar .send-button");
    if (!strip || !input) {
      return;
    }
    strip.hidden = !active;
    input.disabled = !active;
    if (active) {
      // The suggestion comes from the server in the profile timezone the field is read in.
      if (!input.value) {
        input.value = input.dataset.default || "";
      }
      input.focus();
    } else {
      input.value = "";
    }
    if (sendButton) {
      sendButton.setAttribute("aria-label", active ? "Planen" : "Senden");
      sendButton.querySelector("i")?.classList.toggle("fa-paper-plane", !active);
      sendButton.querySelector("i")?.classList.toggle("fa-clock", active);
    }
  };

  const pollDialog = document.getElementById("poll-create-dialog");
  const POLL_MAX_OPTIONS = 10;

  const openPollDialog = () => {
    if (!pollDialog) {
      return;
    }
    pollDialog.showModal();
    pollDialog.querySelector("input[name='question']")?.focus();
  };

  pollDialog?.querySelectorAll("[data-poll-close]").forEach((button) => {
    button.addEventListener("click", () => pollDialog.close());
  });
  pollDialog?.addEventListener("close", () => document.getElementById("compose-more-button")?.focus());
  pollDialog?.querySelector("[data-poll-add-option]")?.addEventListener("click", (event) => {
    const fields = pollDialog.querySelector("[data-poll-options]");
    const count = fields.querySelectorAll("input[name='option']").length;
    if (count >= POLL_MAX_OPTIONS) {
      return;
    }
    const input = document.createElement("input");
    input.type = "text";
    input.name = "option";
    input.maxLength = 120;
    input.autocomplete = "off";
    input.placeholder = `Antwort ${count + 1}`;
    input.setAttribute("aria-label", `Antwort ${count + 1}`);
    fields.appendChild(input);
    input.focus();
    event.currentTarget.hidden = count + 1 >= POLL_MAX_OPTIONS;
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest("#compose-more-button")) {
      setComposeMoreOpen(composeMoreMenu()?.hidden ?? false);
      return;
    }
    const composeAction = event.target.closest("[data-compose-action]");
    if (composeAction) {
      setComposeMoreOpen(false);
      if (composeAction.dataset.composeAction === "poll") {
        openPollDialog();
      } else if (composeAction.dataset.composeAction === "schedule") {
        setScheduleActive(true);
      }
      return;
    }
    if (event.target.closest("#cancel-schedule-button")) {
      setScheduleActive(false);
      document.querySelector(".compose-bar textarea")?.focus();
      return;
    }
    const menu = composeMoreMenu();
    if (menu && !menu.hidden && !event.target.closest(".compose-more")) {
      setComposeMoreOpen(false);
    }
  });

  document.addEventListener("keydown", (event) => {
    const menu = composeMoreMenu();
    if (!menu || menu.hidden || !event.target.closest?.(".compose-more")) {
      return;
    }
    const items = [...menu.querySelectorAll("button")];
    const index = items.indexOf(document.activeElement);
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setComposeMoreOpen(false);
      document.getElementById("compose-more-button")?.focus();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(index + step + items.length) % items.length]?.focus();
    }
  });

  // @ suggestions for group members while typing in the compose field.
  const mentionCandidates = (() => {
    try {
      return JSON.parse(document.getElementById("chat-mention-candidates")?.textContent || "[]");
    } catch {
      return [];
    }
  })();
  const mentionState = { matches: [], active: 0, start: -1 };

  const mentionList = () => document.getElementById("mention-suggestions");

  const hideMentions = () => {
    const list = mentionList();
    const textarea = document.querySelector(".compose-bar textarea");
    mentionState.matches = [];
    mentionState.start = -1;
    if (list) {
      list.hidden = true;
      list.replaceChildren();
    }
    if (textarea) {
      textarea.setAttribute("aria-expanded", "false");
      textarea.removeAttribute("aria-activedescendant");
    }
  };

  const renderMentions = () => {
    const list = mentionList();
    const textarea = document.querySelector(".compose-bar textarea");
    if (!list || !textarea) {
      return;
    }
    list.replaceChildren(
      ...mentionState.matches.map((candidate, index) => {
        const option = document.createElement("button");
        option.type = "button";
        option.id = `mention-option-${index}`;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", String(index === mentionState.active));
        option.dataset.mentionIndex = String(index);
        option.textContent = candidate.id === 0 ? "@alle – ganze Gruppe" : `@${candidate.name}`;
        return option;
      }),
    );
    list.hidden = false;
    textarea.setAttribute("aria-controls", "mention-suggestions");
    textarea.setAttribute("aria-expanded", "true");
    textarea.setAttribute("aria-activedescendant", `mention-option-${mentionState.active}`);
  };

  const updateMentions = (textarea) => {
    if (!mentionCandidates.length) {
      return;
    }
    const before = textarea.value.slice(0, textarea.selectionStart);
    const match = before.match(/(^|[^\p{L}\p{N}_@])@([^@\n]{0,40})$/u);
    if (!match) {
      hideMentions();
      return;
    }
    const query = match[2].toLocaleLowerCase();
    const matches = mentionCandidates.filter((candidate) => candidate.name.toLocaleLowerCase().startsWith(query));
    if (!matches.length) {
      hideMentions();
      return;
    }
    mentionState.matches = matches.slice(0, 8);
    mentionState.active = 0;
    mentionState.start = before.length - match[2].length - 1;
    renderMentions();
  };

  const insertMention = (index) => {
    const textarea = document.querySelector(".compose-bar textarea");
    const candidate = mentionState.matches[index];
    if (!textarea || !candidate || mentionState.start < 0) {
      return;
    }
    const insert = `@${candidate.name} `;
    const after = textarea.value.slice(textarea.selectionStart);
    textarea.value = textarea.value.slice(0, mentionState.start) + insert + after;
    const caret = mentionState.start + insert.length;
    textarea.setSelectionRange(caret, caret);
    hideMentions();
    textarea.focus();
  };

  document.addEventListener("input", (event) => {
    if (event.target.matches?.(".compose-bar textarea")) {
      updateMentions(event.target);
    }
  });

  // Capture phase, so Enter picks a suggestion before the compose field's own Enter-to-send runs.
  document.addEventListener(
    "keydown",
    (event) => {
      if (!mentionState.matches.length || !event.target.matches?.(".compose-bar textarea")) {
        return;
      }
      const count = mentionState.matches.length;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        mentionState.active = (mentionState.active + (event.key === "ArrowDown" ? 1 : -1) + count) % count;
        renderMentions();
      } else if ((event.key === "Enter" && !event.shiftKey) || event.key === "Tab") {
        insertMention(mentionState.active);
      } else if (event.key === "Escape") {
        hideMentions();
      } else {
        return;
      }
      event.preventDefault();
      event.stopImmediatePropagation();
    },
    true,
  );

  document.addEventListener("mousedown", (event) => {
    if (event.target.closest?.("[data-mention-index]")) {
      // Keep the caret in the compose field while a suggestion is clicked.
      event.preventDefault();
    }
  });

  document.addEventListener("click", (event) => {
    const option = event.target.closest?.("[data-mention-index]");
    if (option) {
      insertMention(Number(option.dataset.mentionIndex));
    } else if (mentionState.matches.length && !event.target.closest?.(".compose-bar textarea")) {
      hideMentions();
    }
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
  const forwardButton = contextMenu.querySelector('[data-menu-action="forward"]');
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

    if (forwardButton) {
      forwardButton.hidden = bubble.dataset.canForward !== "true";
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

    // Enter on the focused bubble itself (not on a link inside it) opens the menu as well.
    const isEnterOnBubble = event.key === "Enter" && event.target === bubble;
    if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10") && !isEnterOnBubble) {
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

    if (action === "forward") {
      const dialog = document.getElementById("message-forward-dialog");
      if (dialog) {
        dialog.querySelector("[data-forward-message-id]").value = bubble.dataset.messageId;
        const preview = dialog.querySelector("[data-forward-preview]");
        if (preview) preview.textContent = bubble.dataset.preview || "";
        forwardReturnFocus = bubble;
        dialog.showModal();
        dialog.querySelector("input[name='target_conversation_id']:checked")?.focus();
      }
      return;
    }

    if (action === "prefill") {
      // tasks.js / calendar.js put the text into their quick-add field, where a date can be added.
      try {
        sessionStorage.setItem(
          "lunora-chat-prefill",
          JSON.stringify({ target: button.dataset.prefillTarget, text: bubble.dataset.preview || "" }),
        );
      } catch {
        // Storage blocked: open the page empty.
      }
      window.location.href = button.dataset.prefillUrl;
      return;
    }

    submitMessageAction(bubble.dataset.messageId, action, button.dataset.emoji || "");
  });

  let forwardReturnFocus = null;
  const forwardDialog = document.getElementById("message-forward-dialog");
  forwardDialog?.querySelectorAll("[data-forward-close]").forEach((button) => {
    button.addEventListener("click", () => forwardDialog.close());
  });
  forwardDialog?.addEventListener("close", () => {
    forwardReturnFocus?.focus();
    forwardReturnFocus = null;
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
    if (event.key === "Escape" && !contextMenu.hidden) {
      // Return keyboard focus to the message the menu was opened for.
      const bubble = activeBubble;
      hideContextMenu();
      bubble?.focus();
    }
  });

  window.addEventListener("resize", hideContextMenu);
  window.addEventListener("scroll", hideContextMenu, true);

  // Voice messages: MediaRecorder records into memory, the finished recording is put into the
  // normal attachment input and the compose form is submitted like any other attachment, so the
  // server path (validation, storage, live updates) stays the same as for files.
  const VOICE_MAX_SECONDS = 300;
  const VOICE_FORMATS = [
    ["audio/webm;codecs=opus", "webm"],
    ["audio/webm", "webm"],
    ["audio/ogg;codecs=opus", "ogg"],
    ["audio/mp4", "m4a"],
  ];
  const voiceSupported = Boolean(navigator.mediaDevices?.getUserMedia) && typeof window.MediaRecorder === "function";
  let voiceRecording = null;

  function pickVoiceFormat() {
    return VOICE_FORMATS.find(([mimeType]) => MediaRecorder.isTypeSupported?.(mimeType)) || null;
  }

  function formatVoiceElapsed(seconds) {
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function syncVoiceButton() {
    const button = document.getElementById("compose-voice-button");
    if (button) {
      button.hidden = !voiceSupported || !pickVoiceFormat();
    }
  }

  function setVoiceStripVisible(visible) {
    const strip = document.getElementById("voice-recorder-strip");
    const form = document.querySelector(".compose-bar");
    if (strip) {
      strip.hidden = !visible;
    }
    if (form) {
      form.hidden = visible;
    }
  }

  function finishVoiceRecording(send) {
    if (!voiceRecording) {
      return;
    }
    voiceRecording.send = send;
    window.clearInterval(voiceRecording.timer);
    if (voiceRecording.recorder.state !== "inactive") {
      voiceRecording.recorder.stop();
    }
  }

  async function startVoiceRecording() {
    const format = pickVoiceFormat();
    if (!format || voiceRecording) {
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      window.alert("Ohne Zugriff auf das Mikrofon kann keine Sprachnachricht aufgenommen werden.");
      return;
    }
    const [mimeType, extension] = format;
    const recorder = new MediaRecorder(stream, { mimeType });
    const chunks = [];
    const startedAt = Date.now();
    voiceRecording = { recorder, send: false, timer: 0 };

    recorder.addEventListener("dataavailable", (event) => {
      if (event.data?.size) {
        chunks.push(event.data);
      }
    });
    recorder.addEventListener("stop", () => {
      stream.getTracks().forEach((track) => track.stop());
      const { send } = voiceRecording;
      voiceRecording = null;
      setVoiceStripVisible(false);
      const seconds = Math.min(VOICE_MAX_SECONDS, Math.round((Date.now() - startedAt) / 1000));
      if (!send || !chunks.length || seconds < 1) {
        return;
      }
      const file = new File(chunks, `Sprachnachricht.${extension}`, { type: mimeType.split(";")[0] });
      const input = document.getElementById("compose-attachment-input");
      const durationInput = document.getElementById("compose-voice-duration");
      const form = document.querySelector(".compose-bar");
      if (!input || !form || typeof DataTransfer !== "function") {
        return;
      }
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
      if (durationInput) {
        durationInput.value = String(seconds);
      }
      form.requestSubmit();
    });

    const elapsedLabel = document.querySelector("[data-voice-elapsed]");
    voiceRecording.timer = window.setInterval(() => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000);
      if (elapsedLabel) {
        elapsedLabel.textContent = formatVoiceElapsed(seconds);
      }
      if (seconds >= VOICE_MAX_SECONDS) {
        finishVoiceRecording(true);
      }
    }, 250);
    if (elapsedLabel) {
      elapsedLabel.textContent = "0:00";
    }
    recorder.start(1000);
    setVoiceStripVisible(true);
    document.querySelector("[data-voice-stop]")?.focus();
  }

  document.addEventListener("click", (event) => {
    if (event.target.closest("#compose-voice-button")) {
      startVoiceRecording();
    } else if (event.target.closest("[data-voice-stop]")) {
      finishVoiceRecording(true);
    } else if (event.target.closest("[data-voice-cancel]")) {
      finishVoiceRecording(false);
      document.getElementById("compose-voice-button")?.focus();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && voiceRecording) {
      finishVoiceRecording(false);
    }
  });

  syncVoiceButton();
  new MutationObserver(syncVoiceButton).observe(document.getElementById("message-compose-region") || document.body, {
    childList: true,
  });
})();
