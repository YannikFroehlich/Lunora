(() => {
  // Case- and accent-insensitive, so "apfel" finds "Äpfel".
  function normalize(text) {
    return text.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().trim();
  }

  // Drops a German plural ending, so singular and plural meet: "gurken"/"gurke" -> "gurk",
  // "kiwis" -> "kiwi". Short words keep their s ("reis", "mais", "eis").
  function singular(word) {
    if (word.length > 4 && /[aeiou]s$/.test(word)) return word.slice(0, -1);
    const match = word.match(/^(.{3,}?)(en|n|e)$/);
    return match ? match[1] : word;
  }

  // The same product in singular or plural first, then names starting with the query, a later
  // word starting with it, anywhere; then the same for the query without its plural ending
  // ("Gurken" still finds "Salatgurke"). Alphabetical within each rank. All matches by default;
  // the panel scrolls.
  function rankSuggestions(suggestions, query, limit = Infinity) {
    const needle = normalize(query);
    if (!needle) return [];
    const needleStem = singular(needle);
    const ranked = [];
    suggestions.forEach((suggestion) => {
      const name = normalize(suggestion.name);
      let rank;
      if (singular(name) === needleStem) {
        rank = -1;
      } else {
        let offset = 0;
        let position = name.indexOf(needle);
        if (position < 0 && needleStem !== needle) {
          offset = 3;
          position = name.indexOf(needleStem);
        }
        if (position < 0) return;
        rank = offset + (position === 0 ? 0 : /\s/.test(name[position - 1]) ? 1 : 2);
      }
      ranked.push({ rank, suggestion });
    });
    ranked.sort((a, b) => a.rank - b.rank || a.suggestion.name.localeCompare(b.suggestion.name, "de"));
    return ranked.slice(0, limit).map((entry) => entry.suggestion);
  }

  globalThis.LunoraShopping = { normalize, singular, rankSuggestions };

  if (typeof document === "undefined") return;
  initScanner();
  initLiveUpdates();
  initOfflineToggles();
  const root = document.querySelector("[data-shopping-suggest]");
  const dataElement = document.getElementById("shopping-suggestion-data");
  if (!root || !dataElement) return;

  const input = root.querySelector("input");
  const panel = root.querySelector("[role=listbox]");
  const quantityInput = input.form.querySelector('input[name="quantity"]');
  const suggestions = JSON.parse(dataElement.textContent);
  let shown = [];
  // -1: nothing highlighted, so Enter adds exactly what was typed.
  let active = -1;

  function hide() {
    panel.hidden = true;
    panel.replaceChildren();
    shown = [];
    active = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function setActive(index) {
    active = index;
    panel.querySelectorAll("[role=option]").forEach((option, optionIndex) => {
      const isActive = optionIndex === active;
      option.setAttribute("aria-selected", String(isActive));
      if (isActive) {
        input.setAttribute("aria-activedescendant", option.id);
        option.scrollIntoView({ block: "nearest" });
      }
    });
    if (active < 0) input.removeAttribute("aria-activedescendant");
  }

  function choose(suggestion) {
    input.value = suggestion.name;
    hide();
    // Next step is usually the amount; Enter there adds the item.
    (quantityInput || input).focus();
  }

  function render() {
    shown = rankSuggestions(suggestions, input.value);
    if (!shown.length) {
      hide();
      return;
    }
    panel.replaceChildren(
      ...shown.map((suggestion, index) => {
        const option = document.createElement("div");
        const emoji = document.createElement("span");
        const name = document.createElement("strong");
        const aisle = document.createElement("small");
        option.id = `shopping-suggestion-${index}`;
        option.className = "shopping-suggestion";
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");
        emoji.className = "shopping-emoji";
        emoji.setAttribute("aria-hidden", "true");
        emoji.textContent = suggestion.emoji;
        name.textContent = suggestion.name;
        aisle.textContent = suggestion.aisle;
        option.append(emoji, name, aisle);
        option.addEventListener("click", () => choose(suggestion));
        return option;
      }),
    );
    panel.hidden = false;
    input.setAttribute("aria-expanded", "true");
    setActive(-1);
  }

  // mousedown on an option or the scrollbar would blur the input (and close the panel) first.
  panel.addEventListener("mousedown", (event) => event.preventDefault());
  input.addEventListener("input", render);
  input.addEventListener("blur", hide);
  input.addEventListener("keydown", (event) => {
    if (panel.hidden) {
      if (event.key === "ArrowDown" && input.value) {
        render();
        event.preventDefault();
      }
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const step = event.key === "ArrowDown" ? 1 : -1;
      // Wraps through "nothing highlighted" so the typed text stays reachable.
      setActive(((active + 1 + step + shown.length + 1) % (shown.length + 1)) - 1);
      event.preventDefault();
    } else if (event.key === "Enter" && active >= 0) {
      choose(shown[active]);
      event.preventDefault();
    } else if (event.key === "Escape") {
      hide();
      event.preventDefault();
    }
  });

  // On a shared list, asks every few seconds whether anyone changed it and then swaps in the new
  // list. Only the list region is replaced, so the add form keeps what is being typed.
  function initLiveUpdates() {
    const region = document.querySelector("[data-shopping-live][data-poll-url]");
    if (!region) return;
    let checking = false;

    async function check() {
      if (checking || document.visibilityState !== "visible" || document.querySelector("dialog[open]")) return;
      checking = true;
      try {
        const response = await fetch(region.dataset.pollUrl, { credentials: "same-origin", cache: "no-store" });
        if (!response.ok) return;
        const { version } = await response.json();
        if (version === region.dataset.version) return;
        await reloadList();
      } catch {
        // Offline or a hiccup; the next round tries again.
      } finally {
        checking = false;
      }
    }

    setInterval(check, 5000);
    document.addEventListener("visibilitychange", check);
  }

  // Swaps in the list as the server has it now, with changes still waiting to be sent on top.
  async function reloadList() {
    const region = document.querySelector("[data-shopping-live]");
    const page = await fetch(window.location.href, { credentials: "same-origin", cache: "no-store" });
    if (!region || !page.ok) return;
    const fresh = new DOMParser().parseFromString(await page.text(), "text/html").querySelector("[data-shopping-live]");
    if (!fresh) return;
    region.replaceChildren(...fresh.childNodes);
    region.dataset.version = fresh.dataset.version;
    applyQueued();
  }

  // Ticking items off works without a connection (a supermarket basement): the change shows on the
  // page at once, waits in localStorage and is sent once the network is back. A toggle posts the
  // state it wants, so sending it late (or twice) can't flip an item back.
  const QUEUE_KEY = "lunora.shoppingQueue";
  let flushing = false;

  // The list's address without a #aisle anchor, so a queued change matches the page it came from.
  function listUrl(href) {
    const url = new URL(href, window.location.href);
    url.hash = "";
    return url.href;
  }

  function readQueue() {
    try {
      const queue = JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]");
      return Array.isArray(queue) ? queue : [];
    } catch {
      return [];
    }
  }

  function writeQueue(queue) {
    try {
      localStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
    } catch {
      // Blocked storage: the change still goes out while the page stays open.
    }
  }

  function showToggle(itemId, done) {
    const row = document.getElementById(`item-${itemId}`);
    const form = row?.querySelector('input[name="form_name"][value="shopping_toggle"]')?.form;
    if (!form) return;
    row.classList.toggle("is-done", done);
    form.elements.done.value = done ? "0" : "1";
    form.querySelector(".shopping-toggle")?.setAttribute("aria-pressed", String(done));
  }

  function showPending(count) {
    let note = document.querySelector("[data-shopping-offline]");
    if (!count) {
      note?.remove();
      return;
    }
    if (!note) {
      note = document.createElement("p");
      note.className = "shopping-offline";
      note.dataset.shoppingOffline = "";
      note.setAttribute("role", "status");
      document.querySelector("[data-shopping-live]")?.before(note);
    }
    note.textContent = `Kein Netz: ${count} ${count === 1 ? "Änderung wird" : "Änderungen werden"} gesendet, sobald du wieder online bist.`;
  }

  function applyQueued() {
    const queue = readQueue().filter((entry) => entry.url === listUrl(window.location.href));
    queue.forEach((entry) => showToggle(entry.itemId, entry.done));
    showPending(readQueue().length);
  }

  async function flush() {
    const queue = readQueue();
    if (flushing || !queue.length) return;
    flushing = true;
    let sent = 0;
    try {
      while (queue.length) {
        const entry = queue[0];
        const body = new FormData();
        body.append("csrfmiddlewaretoken", document.querySelector("[name=csrfmiddlewaretoken]")?.value || "");
        body.append("form_name", "shopping_toggle");
        body.append("item_id", entry.itemId);
        body.append("done", entry.done ? "1" : "0");
        const response = await fetch(entry.url, { method: "POST", body, credentials: "same-origin" });
        // A removed item (404) can't be ticked anymore; anything else is tried again later.
        if (!response.ok && response.status !== 404) break;
        queue.shift();
        writeQueue(queue);
        sent += 1;
      }
    } catch {
      // Still offline; the next attempt follows on "online" or the next round.
    } finally {
      flushing = false;
    }
    showPending(queue.length);
    if (sent && !queue.length) {
      const focusedId = document.activeElement?.closest?.(".shopping-item")?.id;
      await reloadList().catch(() => {});
      if (focusedId) document.querySelector(`#${CSS.escape(focusedId)} .shopping-toggle`)?.focus();
    }
  }

  function initOfflineToggles() {
    if (!document.querySelector("[data-shopping-live]")) return;
    // Capture phase, so this runs before base.js's soft submit, which would need the network.
    document.addEventListener(
      "submit",
      (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || form.elements.form_name?.value !== "shopping_toggle") return;
        event.preventDefault();
        event.stopPropagation();
        const entry = {
          url: listUrl(form.action),
          itemId: form.elements.item_id.value,
          done: form.elements.done.value === "1",
        };
        const queue = readQueue().filter((queued) => queued.url !== entry.url || queued.itemId !== entry.itemId);
        queue.push(entry);
        writeQueue(queue);
        showToggle(entry.itemId, entry.done);
        if (!navigator.onLine) showPending(queue.length);
        flush();
      },
      { capture: true },
    );
    window.addEventListener("online", flush);
    setInterval(flush, 15000);
    applyQueued();
    flush();
  }

  // Barcode scanner dialog: the camera through the browser's BarcodeDetector where there is one
  // (Chrome on Android), typing the number everywhere. Each code goes to the server, which looks
  // the product up and ticks off or adds the matching item; the list reloads when it closes.
  function initScanner() {
    const dialog = document.querySelector("[data-shopping-scanner]");
    const opener = document.querySelector("[data-shopping-scan-open]");
    if (!dialog || !opener) return;
    const video = dialog.querySelector("video");
    const status = dialog.querySelector("[data-scan-status]");
    const result = dialog.querySelector("[data-scan-result]");
    const manualForm = dialog.querySelector("[data-scan-manual]");
    const nameForm = dialog.querySelector("[data-scan-name]");
    const csrfToken = dialog.querySelector("input[name=csrfmiddlewaretoken]").value;
    const formats = ["ean_13", "ean_8", "upc_a"];
    let stream = null;
    let timer = null;
    let busy = false;
    let changed = false;
    let last = { code: "", at: 0 };

    function mode() {
      return dialog.querySelector("input[name=scan_mode]:checked").value;
    }

    function showProduct(product) {
      result.hidden = !product;
      if (!product) return;
      const name = document.createElement("strong");
      const details = document.createElement("small");
      name.textContent = product.name;
      details.textContent = [product.brand, product.package].filter(Boolean).join(" · ");
      const text = document.createElement("span");
      text.append(name, details);
      const parts = [];
      if (product.nutri_score) {
        const score = document.createElement("span");
        score.className = `nutri-score nutri-score-${product.nutri_score.toLowerCase()}`;
        score.title = `Nutri-Score ${product.nutri_score}`;
        score.textContent = product.nutri_score;
        parts.push(score);
      }
      result.replaceChildren(...parts, text);
    }

    async function send(code, name = "") {
      busy = true;
      const body = new FormData();
      body.append("csrfmiddlewaretoken", csrfToken);
      body.append("barcode", code);
      body.append("mode", mode());
      if (name) body.append("name", name);
      status.textContent = "Suche …";
      try {
        const response = await fetch(dialog.dataset.scanUrl, { method: "POST", body, credentials: "same-origin" });
        const data = await response.json();
        status.textContent = data.message || "Das hat nicht geklappt.";
        showProduct(data.product);
        changed ||= Boolean(data.changed);
        if (data.changed) navigator.vibrate?.(80);
        nameForm.hidden = data.status !== "unknown";
        if (data.status === "unknown") {
          nameForm.elements.barcode.value = data.barcode;
          nameForm.elements.name.value = "";
          nameForm.elements.name.focus();
        }
      } catch {
        status.textContent = "Keine Verbindung. Versuch es noch einmal.";
      } finally {
        busy = false;
      }
    }

    function detected(code) {
      // The camera sees the same code many times a second; one send per product in view.
      const now = Date.now();
      // A product being named stays put until it's saved or another code comes along.
      if (busy || (!nameForm.hidden && code === nameForm.elements.barcode.value)) return;
      if (code === last.code && now - last.at < 4000) return;
      last = { code, at: now };
      send(code);
    }

    async function startCamera() {
      const supported = "BarcodeDetector" in window && navigator.mediaDevices && navigator.mediaDevices.getUserMedia;
      if (!supported) {
        status.textContent = "Dieser Browser erkennt keine Strichcodes. Tipp die Nummer darunter ein.";
        return;
      }
      try {
        const available = await window.BarcodeDetector.getSupportedFormats();
        const detector = new window.BarcodeDetector({ formats: formats.filter((f) => available.includes(f)) });
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
        if (!dialog.open) {
          stopCamera();
          return;
        }
        video.srcObject = stream;
        video.hidden = false;
        await video.play();
        status.textContent = "Halte den Strichcode vor die Kamera.";
        timer = setInterval(async () => {
          if (busy || video.readyState < 2) return;
          try {
            const codes = await detector.detect(video);
            if (codes.length) detected(codes[0].rawValue);
          } catch {
            // A frame the detector couldn't read; the next one will do.
          }
        }, 300);
      } catch {
        stopCamera();
        status.textContent = "Kein Zugriff auf die Kamera. Du kannst die Nummer auch eintippen.";
      }
    }

    function stopCamera() {
      clearInterval(timer);
      timer = null;
      stream?.getTracks().forEach((track) => track.stop());
      stream = null;
      video.srcObject = null;
      video.hidden = true;
    }

    opener.addEventListener("click", () => {
      changed = false;
      last = { code: "", at: 0 };
      status.textContent = "";
      showProduct(null);
      nameForm.hidden = true;
      dialog.showModal();
      startCamera();
    });
    dialog.querySelector("[data-shopping-scan-close]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => {
      stopCamera();
      if (changed) window.location.reload();
    });
    manualForm.addEventListener("submit", (event) => {
      event.preventDefault();
      if (busy) return;
      send(manualForm.elements.barcode.value);
      manualForm.reset();
    });
    nameForm.addEventListener("submit", (event) => {
      event.preventDefault();
      if (busy) return;
      send(nameForm.elements.barcode.value, nameForm.elements.name.value);
    });
  }
})();
