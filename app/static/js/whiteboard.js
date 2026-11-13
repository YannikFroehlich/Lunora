(function () {
  const root = document.querySelector("[data-whiteboard-editor]");
  const format = window.LunoraWhiteboardFormat;
  if (!root || !format) return;

  const SVG_NS = "http://www.w3.org/2000/svg";
  const MIN_ZOOM = 0.1;
  const MAX_ZOOM = 8;
  const HISTORY_LIMIT = 200;
  const SAVE_DELAY = 1200;
  const SIMPLIFY_PIXELS = 0.75;
  const ERASER_PIXELS = 10;
  // The page is light paper in both themes (like the UML canvas), so the ink colors are fixed.
  const INK = {
    default: { label: "Schwarz", color: "#2b2520" },
    blue: { label: "Blau", color: "#2f6fb5" },
    green: { label: "Grün", color: "#3a8a4e" },
    yellow: { label: "Gelb", color: "#d1a013" },
    red: { label: "Rot", color: "#c0453f" },
    violet: { label: "Violett", color: "#7a55b0" },
    gray: { label: "Grau", color: "#8c8c8c" },
  };
  const WIDTH_LABELS = { 2: "Dünn", 4: "Mittel", 8: "Dick" };
  const TOOL_KEYS = { p: "pen", e: "eraser", h: "hand" };

  const payload = JSON.parse(document.getElementById("whiteboard-data").textContent);
  const saveUrl = root.dataset.saveUrl;
  const csrfToken = document.querySelector("meta[name='csrf-token']")?.content || "";
  const svg = root.querySelector("[data-whiteboard-canvas]");
  const titleInput = root.querySelector("[data-whiteboard-title]");
  const statusLabel = root.querySelector("[data-whiteboard-status]");
  const hintLabel = root.querySelector("[data-whiteboard-hint]");
  const zoomLabel = root.querySelector("[data-whiteboard-zoom-label]");
  const colorOptions = root.querySelector("[data-whiteboard-colors]");
  const widthOptions = root.querySelector("[data-whiteboard-widths]");
  const defaultHint = hintLabel.textContent;

  const doc = format.normalizeDocument(payload.document);
  const view = { x: 0, y: 0, k: 1 };
  const style = { color: "default", width: 4 };
  const nodes = new Map();
  const pointers = new Map();
  const history = { undo: [], redo: [] };
  const saveState = { dirty: false, saving: false, again: false, timer: null };
  let tool = "pen";
  let spaceHeld = false;
  let interaction = null;
  let hintTimer = null;
  let nextId = 0;

  const background = svgEl("rect", { width: "100%", height: "100%", fill: "transparent" });
  const viewport = svgEl("g");
  svg.replaceChildren(background, viewport);

  // ---- Helpers --------------------------------------------------------------------------------

  function svgEl(tag, attrs = {}) {
    const element = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
    return element;
  }

  function newId() {
    const taken = new Set(doc.elements.map((item) => item.id));
    let id;
    do {
      nextId += 1;
      id = `e${Date.now().toString(36)}${nextId.toString(36)}`;
    } while (taken.has(id));
    return id;
  }

  function isTyping(target) {
    return (
      target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))
    );
  }

  function showHint(message, duration = 3500) {
    hintLabel.textContent = message;
    hintLabel.classList.add("is-active");
    window.clearTimeout(hintTimer);
    hintTimer = window.setTimeout(() => {
      hintLabel.textContent = defaultHint;
      hintLabel.classList.remove("is-active");
    }, duration);
  }

  // ---- Rendering ------------------------------------------------------------------------------

  function strokeAttrs(color, width) {
    return {
      fill: "none",
      stroke: (INK[color] || INK.default).color,
      "stroke-width": width,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    };
  }

  function elementNode(item) {
    // Only strokes exist so far; other element types stay in the document untouched.
    if (item.type !== "stroke") return null;
    return svgEl("path", { ...strokeAttrs(item.color, item.width), d: format.strokePath(item.points) });
  }

  function render() {
    nodes.clear();
    const fragment = document.createDocumentFragment();
    doc.elements.forEach((item) => {
      const node = elementNode(item);
      if (!node) return;
      nodes.set(item.id, node);
      fragment.append(node);
    });
    viewport.replaceChildren(fragment);
  }

  function applyView() {
    viewport.setAttribute("transform", `translate(${view.x} ${view.y}) scale(${view.k})`);
    zoomLabel.textContent = `${Math.round(view.k * 100)} %`;
  }

  // ---- View -----------------------------------------------------------------------------------

  function canvasSize() {
    const rect = svg.getBoundingClientRect();
    return { width: rect.width || 800, height: rect.height || 600, left: rect.left, top: rect.top };
  }

  function toWorld(clientX, clientY) {
    const size = canvasSize();
    return { x: (clientX - size.left - view.x) / view.k, y: (clientY - size.top - view.y) / view.k };
  }

  function clampZoom(value) {
    return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
  }

  function zoomAt(factor, clientX, clientY) {
    const size = canvasSize();
    const pointX = clientX ?? size.left + size.width / 2;
    const pointY = clientY ?? size.top + size.height / 2;
    const world = toWorld(pointX, pointY);
    view.k = clampZoom(view.k * factor);
    view.x = pointX - size.left - world.x * view.k;
    view.y = pointY - size.top - world.y * view.k;
    applyView();
  }

  function fitView() {
    const bounds = format.documentBounds(doc);
    const size = canvasSize();
    if (!bounds) {
      view.k = 1;
      view.x = size.width / 2;
      view.y = size.height / 2;
    } else {
      const width = bounds.x2 - bounds.x1 + 80;
      const height = bounds.y2 - bounds.y1 + 80;
      view.k = Math.min(1, clampZoom(Math.min(size.width / width, size.height / height)));
      view.x = size.width / 2 - ((bounds.x1 + bounds.x2) / 2) * view.k;
      view.y = size.height / 2 - ((bounds.y1 + bounds.y2) / 2) * view.k;
    }
    applyView();
  }

  // ---- History --------------------------------------------------------------------------------
  // A change is two lists of {index, element}: what it took out and what it put in. Undo swaps them
  // back, so history never copies the whole board.

  function swap(out, into) {
    const gone = new Set(out.map((entry) => entry.element.id));
    if (gone.size) {
      doc.elements = doc.elements.filter((item) => !gone.has(item.id));
      gone.forEach((id) => {
        nodes.get(id)?.remove();
        nodes.delete(id);
      });
    }
    const appended = into.length === 1 && into[0].index >= doc.elements.length;
    [...into]
      .sort((a, b) => a.index - b.index)
      .forEach((entry) => doc.elements.splice(Math.min(entry.index, doc.elements.length), 0, entry.element));
    if (appended) {
      const node = elementNode(into[0].element);
      if (node) {
        nodes.set(into[0].element.id, node);
        viewport.append(node);
      }
    } else if (into.length) {
      render();
    }
    markDirty();
    updateHistoryButtons();
  }

  function record(change) {
    history.undo.push(change);
    if (history.undo.length > HISTORY_LIMIT) history.undo.shift();
    history.redo = [];
    updateHistoryButtons();
  }

  function commit(removed, added) {
    swap(removed, added);
    record({ removed, added });
  }

  function undo() {
    const change = history.undo.pop();
    if (!change) return;
    history.redo.push(change);
    swap(change.added, change.removed);
  }

  function redo() {
    const change = history.redo.pop();
    if (!change) return;
    history.undo.push(change);
    swap(change.removed, change.added);
  }

  function updateHistoryButtons() {
    root.querySelector("[data-whiteboard-action='undo']").disabled = !history.undo.length;
    root.querySelector("[data-whiteboard-action='redo']").disabled = !history.redo.length;
  }

  // ---- Tools ----------------------------------------------------------------------------------

  function setTool(name) {
    tool = name;
    svg.dataset.tool = name;
    root.querySelectorAll("[data-whiteboard-tool]").forEach((button) => {
      const active = button.dataset.whiteboardTool === name;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }

  function pressOption(container, attribute, value) {
    container.querySelectorAll("button").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset[attribute] === String(value)));
    });
  }

  function startStroke(world) {
    if (doc.elements.length >= format.LIMITS.elements || format.pointCount(doc) >= format.LIMITS.totalPoints) {
      showHint("Das Whiteboard ist voll. Lösche etwas, um weiterzuzeichnen.");
      return null;
    }
    const node = svgEl("path", { ...strokeAttrs(style.color, style.width), d: "" });
    viewport.append(node);
    return { type: "stroke", node, points: [world.x, world.y], d: `M${world.x} ${world.y}` };
  }

  function extendStroke(stroke, event) {
    // Coalesced events carry the positions the browser merged into this frame; without them fast strokes get corners.
    const events = event.getCoalescedEvents?.() || [];
    (events.length ? events : [event]).forEach((entry) => {
      const world = toWorld(entry.clientX, entry.clientY);
      stroke.points.push(world.x, world.y);
      stroke.d += `L${world.x} ${world.y}`;
    });
    stroke.node.setAttribute("d", stroke.d);
  }

  function finishStroke(stroke) {
    stroke.node.remove();
    const room = format.LIMITS.totalPoints - format.pointCount(doc);
    let points = format.simplifyStroke(stroke.points, SIMPLIFY_PIXELS / view.k);
    if (points.length / 2 > room) points = points.slice(0, room * 2);
    const element = { id: newId(), type: "stroke", color: style.color, width: style.width, points };
    commit([], [{ index: doc.elements.length, element }]);
  }

  function eraseAt(eraser, world) {
    const reach = ERASER_PIXELS / view.k;
    const from = eraser.last || world;
    eraser.last = world;
    // Undo reinserts by ascending index, so each entry keeps its position from before the gesture.
    const hit = doc.elements
      .filter(
        (element) =>
          element.type === "stroke" &&
          format.hitsStroke(element.points, from.x, from.y, world.x, world.y, reach + element.width / 2),
      )
      .map((element) => ({ index: eraser.origin.get(element.id), element }));
    if (!hit.length) return;
    eraser.removed.push(...hit);
    swap(hit, []);
  }

  function startErase(world) {
    const origin = new Map(doc.elements.map((element, index) => [element.id, index]));
    const eraser = { type: "erase", origin, removed: [] };
    eraseAt(eraser, world);
    return eraser;
  }

  function finishErase(eraser) {
    // The strokes are already gone; the whole gesture becomes one undo step.
    if (eraser.removed.length) record({ removed: eraser.removed, added: [] });
  }

  function startPan(event) {
    svg.classList.add("is-panning");
    return { type: "pan", startX: event.clientX, startY: event.clientY, originX: view.x, originY: view.y };
  }

  function startPinch() {
    const [a, b] = [...pointers.values()];
    const size = canvasSize();
    const midX = (a.x + b.x) / 2 - size.left;
    const midY = (a.y + b.y) / 2 - size.top;
    return {
      type: "pinch",
      distance: Math.hypot(a.x - b.x, a.y - b.y) || 1,
      zoom: view.k,
      worldX: (midX - view.x) / view.k,
      worldY: (midY - view.y) / view.k,
    };
  }

  function cancelInteraction() {
    if (interaction?.type === "stroke") interaction.node.remove();
    if (interaction?.type === "erase") finishErase(interaction);
    svg.classList.remove("is-panning");
    interaction = null;
  }

  function onPointerDown(event) {
    if (event.pointerType === "touch") {
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pointers.size === 2) {
        // A second finger turns the gesture into pan/zoom; the stroke the first one started is dropped.
        cancelInteraction();
        interaction = startPinch();
        return;
      }
      if (pointers.size > 2) return;
    }
    if (event.button === 1 || (event.button === 0 && (tool === "hand" || spaceHeld))) {
      event.preventDefault();
      interaction = startPan(event);
    } else if (event.button !== 0) {
      return;
    } else if (tool === "pen") {
      interaction = startStroke(toWorld(event.clientX, event.clientY));
    } else if (tool === "eraser") {
      interaction = startErase(toWorld(event.clientX, event.clientY));
    }
    if (interaction) svg.setPointerCapture(event.pointerId);
    svg.focus({ preventScroll: true });
  }

  function onPointerMove(event) {
    if (pointers.has(event.pointerId)) pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (!interaction) return;
    if (interaction.type === "pinch") {
      if (pointers.size < 2) return;
      const [a, b] = [...pointers.values()];
      const size = canvasSize();
      view.k = clampZoom((interaction.zoom * Math.hypot(a.x - b.x, a.y - b.y)) / interaction.distance);
      view.x = (a.x + b.x) / 2 - size.left - interaction.worldX * view.k;
      view.y = (a.y + b.y) / 2 - size.top - interaction.worldY * view.k;
      applyView();
    } else if (interaction.type === "pan") {
      view.x = interaction.originX + event.clientX - interaction.startX;
      view.y = interaction.originY + event.clientY - interaction.startY;
      applyView();
    } else if (interaction.type === "stroke") {
      extendStroke(interaction, event);
    } else if (interaction.type === "erase") {
      eraseAt(interaction, toWorld(event.clientX, event.clientY));
    }
  }

  function onPointerUp(event) {
    pointers.delete(event.pointerId);
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    if (!interaction) return;
    const current = interaction;
    if (current.type === "pinch") {
      // The finger that stays down must not start drawing; the gesture ends when both are up.
      if (!pointers.size) interaction = null;
      return;
    }
    interaction = null;
    svg.classList.remove("is-panning");
    if (current.type === "stroke") {
      if (event.type === "pointercancel") current.node.remove();
      else finishStroke(current);
    } else if (current.type === "erase") {
      finishErase(current);
    }
  }

  svg.addEventListener("pointerdown", onPointerDown);
  svg.addEventListener("pointermove", onPointerMove);
  svg.addEventListener("pointerup", onPointerUp);
  svg.addEventListener("pointercancel", onPointerUp);
  svg.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      if (event.ctrlKey || event.metaKey) {
        zoomAt(Math.exp(-event.deltaY * 0.0022), event.clientX, event.clientY);
      } else {
        view.x -= event.deltaX;
        view.y -= event.deltaY;
        applyView();
      }
    },
    { passive: false },
  );

  // ---- Saving ---------------------------------------------------------------------------------

  function setStatus(message, state) {
    statusLabel.textContent = message;
    statusLabel.dataset.state = state || "";
  }

  function markDirty() {
    saveState.dirty = true;
    setStatus("Ungespeicherte Änderungen", "dirty");
    window.clearTimeout(saveState.timer);
    saveState.timer = window.setTimeout(save, SAVE_DELAY);
  }

  async function save() {
    window.clearTimeout(saveState.timer);
    if (saveState.saving) {
      saveState.again = true;
      return;
    }
    if (!saveState.dirty) return;
    const title = titleInput.value.trim();
    if (!title) {
      setStatus("Titel fehlt", "error");
      return;
    }
    saveState.saving = true;
    saveState.dirty = false;
    setStatus("Speichert …", "saving");
    try {
      const response = await fetch(saveUrl, {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ title, document: doc }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || `Fehler ${response.status}`);
      if (!saveState.dirty) setStatus("Gespeichert", "saved");
      document.title = `${title} | Whiteboard | Lunora`;
    } catch (error) {
      saveState.dirty = true;
      setStatus(`Nicht gespeichert: ${error.message}`, "error");
    } finally {
      saveState.saving = false;
      if (saveState.again) {
        saveState.again = false;
        save();
      }
    }
  }

  titleInput.addEventListener("input", markDirty);
  window.addEventListener("beforeunload", (event) => {
    if (saveState.dirty || saveState.saving) event.preventDefault();
  });

  // ---- Toolbar & keyboard ---------------------------------------------------------------------

  Object.entries(INK).forEach(([key, ink]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "uml-color-swatch";
    button.dataset.color = key;
    button.style.setProperty("--swatch", ink.color);
    button.style.setProperty("--swatch-border", ink.color);
    button.setAttribute("aria-label", ink.label);
    button.title = ink.label;
    colorOptions.append(button);
  });
  format.WIDTHS.forEach((width) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "uml-tool is-icon whiteboard-width";
    button.dataset.width = width;
    button.style.setProperty("--dot", `${width + 2}px`);
    button.setAttribute("aria-label", `Strichstärke: ${WIDTH_LABELS[width]}`);
    button.title = WIDTH_LABELS[width];
    widthOptions.append(button);
  });

  colorOptions.addEventListener("click", (event) => {
    const button = event.target.closest("[data-color]");
    if (!button) return;
    style.color = button.dataset.color;
    pressOption(colorOptions, "color", style.color);
    if (tool !== "pen") setTool("pen");
  });
  widthOptions.addEventListener("click", (event) => {
    const button = event.target.closest("[data-width]");
    if (!button) return;
    style.width = Number(button.dataset.width);
    pressOption(widthOptions, "width", style.width);
    if (tool !== "pen") setTool("pen");
  });

  const ACTIONS = {
    undo,
    redo,
    "zoom-in": () => zoomAt(1.2),
    "zoom-out": () => zoomAt(1 / 1.2),
    "zoom-reset": () => zoomAt(1 / view.k),
    fit: fitView,
  };
  root.querySelectorAll("[data-whiteboard-action]").forEach((button) => {
    button.addEventListener("click", () => ACTIONS[button.dataset.whiteboardAction]?.());
  });
  root.querySelectorAll("[data-whiteboard-tool]").forEach((button) => {
    button.addEventListener("click", () => setTool(button.dataset.whiteboardTool));
  });

  document.addEventListener("keydown", (event) => {
    const modifier = event.ctrlKey || event.metaKey;
    const key = event.key.toLowerCase();
    if (modifier && key === "s") {
      event.preventDefault();
      saveState.dirty = true;
      save();
      return;
    }
    if (isTyping(event.target)) return;

    if (modifier && key === "z") {
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
    } else if (modifier && key === "y") {
      event.preventDefault();
      redo();
    } else if (modifier) {
      return;
    } else if (key === " ") {
      // Only on the canvas: on a focused button the space bar has to keep pressing it.
      if (event.target !== svg && event.target !== document.body) return;
      event.preventDefault();
      spaceHeld = true;
      svg.classList.add("is-space-pan");
    } else if (TOOL_KEYS[key]) {
      setTool(TOOL_KEYS[key]);
    } else if (key === "f") {
      fitView();
    } else if (key === "+" || key === "=") {
      zoomAt(1.2);
    } else if (key === "-") {
      zoomAt(1 / 1.2);
    } else if (key === "0") {
      zoomAt(1 / view.k);
    }
  });
  document.addEventListener("keyup", (event) => {
    if (event.key !== " ") return;
    spaceHeld = false;
    svg.classList.remove("is-space-pan");
  });

  setTool("pen");
  pressOption(colorOptions, "color", style.color);
  pressOption(widthOptions, "width", style.width);
  updateHistoryButtons();
  render();
  fitView();
})();
