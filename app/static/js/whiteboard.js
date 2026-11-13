(function () {
  const root = document.querySelector("[data-whiteboard-editor]");
  const format = window.LunoraWhiteboardFormat;
  if (!root || !format) return;

  const SVG_NS = "http://www.w3.org/2000/svg";
  const FONT_FAMILY = "Inter, 'Segoe UI', Arial, sans-serif";
  const MIN_ZOOM = 0.1;
  const MAX_ZOOM = 8;
  const HISTORY_LIMIT = 200;
  const MERGE_MS = 1500;
  const SAVE_DELAY = 1200;
  const SIMPLIFY_PIXELS = 0.75;
  const ERASER_PIXELS = 10;
  const FIT_MAX_ZOOM = 4;
  const ERASER_MODE_KEY = "lunora-whiteboard-eraser";
  const HIT_PIXELS = 6;
  const DRAG_PIXELS = 3;
  const MIN_SHAPE_PIXELS = 4;
  const DUPLICATE_PIXELS = 20;
  const SELECT_COLOR = "#b3772f";
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
  const SHAPE_TOOLS = new Set(["line", "arrow", "rect", "ellipse"]);
  const TOOL_KEYS = {
    v: "select",
    p: "pen",
    e: "eraser",
    h: "hand",
    l: "line",
    a: "arrow",
    r: "rect",
    o: "ellipse",
    t: "text",
    g: "fill",
  };

  const payload = JSON.parse(document.getElementById("whiteboard-data").textContent);
  const saveUrl = root.dataset.saveUrl;
  const csrfToken = document.querySelector("meta[name='csrf-token']")?.content || "";
  const svg = root.querySelector("[data-whiteboard-canvas]");
  const canvasWrap = svg.parentElement;
  const titleInput = root.querySelector("[data-whiteboard-title]");
  const statusLabel = root.querySelector("[data-whiteboard-status]");
  const hintLabel = root.querySelector("[data-whiteboard-hint]");
  const zoomLabel = root.querySelector("[data-whiteboard-zoom-label]");
  const colorOptions = root.querySelector("[data-whiteboard-colors]");
  const widthOptions = root.querySelector("[data-whiteboard-widths]");
  const eraserModeSelect = root.querySelector("[data-whiteboard-eraser-mode]");
  const sizeSelect = root.querySelector("[data-whiteboard-text-size]");
  const textInput = root.querySelector("[data-whiteboard-text]");
  const exportMenu = root.querySelector(".uml-export-menu");
  const importInput = root.querySelector("[data-whiteboard-import-input]");
  const defaultHint = hintLabel.textContent;

  const doc = format.normalizeDocument(payload.document);
  const view = { x: 0, y: 0, k: 1 };
  const style = { color: "default", width: 4, size: "m" };
  const nodes = new Map();
  const pointers = new Map();
  const history = { undo: [], redo: [] };
  const saveState = { dirty: false, saving: false, again: false, timer: null };
  let selection = new Set();
  let tool = "pen";
  let spaceHeld = false;
  let interaction = null;
  let editing = null;
  let hintTimer = null;
  let nextId = 0;

  const background = svgEl("rect", { width: "100%", height: "100%", fill: "transparent" });
  const viewport = svgEl("g");
  const selectionLayer = svgEl("g");
  const guideLayer = svgEl("g");
  const overlay = svgEl("g", { "pointer-events": "none" });
  overlay.append(selectionLayer, guideLayer);
  svg.replaceChildren(background, viewport, overlay);

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

  function inkColor(color) {
    return (INK[color] || INK.default).color;
  }

  const measureContext = document.createElement("canvas").getContext("2d");

  function measure(text, size) {
    if (!measureContext) return format.estimateTextWidth(text, size);
    measureContext.font = `${size}px ${FONT_FAMILY}`;
    return measureContext.measureText(text).width;
  }

  function findElement(id) {
    return doc.elements.find((item) => item.id === id);
  }

  // ---- Rendering ------------------------------------------------------------------------------

  function lineAttrs(color, width) {
    return {
      fill: "none",
      stroke: inkColor(color),
      "stroke-width": width,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    };
  }

  function textNode(item) {
    const size = format.TEXT_SIZES[item.size];
    const node = svgEl("text", {
      x: item.x,
      y: item.y,
      fill: inkColor(item.color),
      "font-size": size,
      "font-family": FONT_FAMILY,
    });
    item.text.split("\n").forEach((line, index) => {
      const span = svgEl("tspan", {
        x: item.x,
        dy: index ? size * format.TEXT_LINE_HEIGHT : size * format.TEXT_ASCENT,
      });
      // An empty tspan would collapse; a no-break space keeps blank lines.
      span.textContent = line || " ";
      node.append(span);
    });
    return node;
  }

  function elementNode(item) {
    const attrs = lineAttrs(item.color, item.width);
    if (item.type === "stroke") return svgEl("path", { ...attrs, d: format.strokePath(item.points) });
    if (item.type === "line") return svgEl("line", { ...attrs, x1: item.x1, y1: item.y1, x2: item.x2, y2: item.y2 });
    if (item.type === "arrow") {
      const head = format.arrowHead(item.x1, item.y1, item.x2, item.y2, item.width);
      return svgEl("path", { ...attrs, d: `M${item.x1} ${item.y1}L${item.x2} ${item.y2}${head}` });
    }
    const fill = item.fill ? { fill: inkColor(item.color), "fill-opacity": 0.3 } : {};
    if (item.type === "rect") {
      return svgEl("rect", { ...attrs, ...fill, x: item.x, y: item.y, width: item.w, height: item.h, rx: 2 });
    }
    if (item.type === "ellipse") {
      const rx = item.w / 2;
      const ry = item.h / 2;
      return svgEl("ellipse", { ...attrs, ...fill, cx: item.x + rx, cy: item.y + ry, rx, ry });
    }
    return textNode(item);
  }

  function render() {
    nodes.clear();
    const fragment = document.createDocumentFragment();
    doc.elements.forEach((item) => {
      const node = elementNode(item);
      nodes.set(item.id, node);
      fragment.append(node);
    });
    viewport.replaceChildren(fragment);
    if (editing?.element) nodes.get(editing.element.id)?.setAttribute("visibility", "hidden");
  }

  function drawSelection() {
    const pad = 5 / view.k;
    const boxes = selectedEntries().map(({ element }) => {
      const box = format.elementBounds(element, measure);
      return svgEl("rect", {
        x: box.x1 - pad,
        y: box.y1 - pad,
        width: box.x2 - box.x1 + pad * 2,
        height: box.y2 - box.y1 + pad * 2,
        fill: "none",
        stroke: SELECT_COLOR,
        "stroke-width": 1.5,
        "stroke-dasharray": "5 4",
        "vector-effect": "non-scaling-stroke",
      });
    });
    selectionLayer.replaceChildren(...boxes);
    root
      .querySelectorAll("[data-whiteboard-action='duplicate'], [data-whiteboard-action='delete']")
      .forEach((button) => {
        button.disabled = !selection.size;
      });
  }

  function applyView() {
    const transform = `translate(${view.x} ${view.y}) scale(${view.k})`;
    viewport.setAttribute("transform", transform);
    overlay.setAttribute("transform", transform);
    zoomLabel.textContent = `${Math.round(view.k * 100)} %`;
    if (selection.size) drawSelection();
    positionTextInput();
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

  function visibleCenter() {
    const size = canvasSize();
    return toWorld(size.left + size.width / 2, size.top + size.height / 2);
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

  // Frames the whole drawing. The button zooms in on a small one; opening a board never goes past 100 %.
  function fitView(maxZoom = 1) {
    const bounds = format.documentBounds(doc, measure);
    const size = canvasSize();
    if (!bounds) {
      view.k = 1;
      view.x = size.width / 2;
      view.y = size.height / 2;
    } else {
      const width = bounds.x2 - bounds.x1 + 80;
      const height = bounds.y2 - bounds.y1 + 80;
      view.k = Math.min(maxZoom, clampZoom(Math.min(size.width / width, size.height / height)));
      view.x = size.width / 2 - ((bounds.x1 + bounds.x2) / 2) * view.k;
      view.y = size.height / 2 - ((bounds.y1 + bounds.y2) / 2) * view.k;
    }
    applyView();
  }

  // ---- History --------------------------------------------------------------------------------
  // A change is two lists of {index, element}: what it took out and what it put in. Undo swaps them
  // back, so history never copies the whole board. Elements are never mutated in place; an edit
  // replaces the element with a changed copy at the same index.

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
      nodes.set(into[0].element.id, node);
      viewport.append(node);
    } else if (into.length) {
      render();
    }
    selection = new Set([...selection].filter((id) => nodes.has(id)));
    drawSelection();
    markDirty();
    updateHistoryButtons();
  }

  function record(change) {
    history.undo.push(change);
    if (history.undo.length > HISTORY_LIMIT) history.undo.shift();
    history.redo = [];
    updateHistoryButtons();
  }

  // Changes with the same mergeKey in quick succession (holding an arrow key) become one undo step.
  function commit(removed, added, mergeKey) {
    swap(removed, added);
    const top = history.undo[history.undo.length - 1];
    const now = Date.now();
    if (mergeKey && top?.mergeKey === mergeKey && now - top.time < MERGE_MS && !history.redo.length) {
      top.added = added;
      top.time = now;
      return;
    }
    record({ removed, added, mergeKey, time: now });
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

  // ---- Selection ------------------------------------------------------------------------------

  function selectedEntries() {
    return doc.elements.flatMap((element, index) => (selection.has(element.id) ? [{ index, element }] : []));
  }

  function setSelection(ids) {
    selection = new Set(ids);
    drawSelection();
  }

  function replaceSelected(change, mergeKey) {
    const removed = [];
    const added = [];
    selectedEntries().forEach(({ index, element }) => {
      const next = change(element);
      if (JSON.stringify(next) === JSON.stringify(element)) return;
      removed.push({ index, element });
      added.push({ index, element: next });
    });
    if (removed.length) commit(removed, added, mergeKey);
  }

  function deleteSelection() {
    const removed = selectedEntries();
    if (removed.length) commit(removed, []);
  }

  function duplicateSelection() {
    const entries = selectedEntries();
    if (!entries.length) return;
    const points = entries.reduce((sum, { element }) => sum + (element.points?.length || 0) / 2, 0);
    if (
      doc.elements.length + entries.length > format.LIMITS.elements ||
      format.pointCount(doc) + points > format.LIMITS.totalPoints
    ) {
      showHint("Für eine Kopie ist auf dem Whiteboard kein Platz mehr.");
      return;
    }
    const offset = DUPLICATE_PIXELS / view.k;
    const copies = entries.map(({ element }, order) => ({
      index: doc.elements.length + order,
      element: { ...format.moveElement(element, offset, offset), id: newId() },
    }));
    commit([], copies);
    setSelection(copies.map((entry) => entry.element.id));
  }

  function nudgeSelection(dx, dy) {
    const key = `nudge:${[...selection].sort().join(",")}`;
    replaceSelected((element) => format.moveElement(element, dx, dy), key);
  }

  function topElementAt(world) {
    const reach = HIT_PIXELS / view.k;
    for (let i = doc.elements.length - 1; i >= 0; i -= 1) {
      const element = doc.elements[i];
      if (format.hitsElement(element, world.x, world.y, world.x, world.y, reach, measure)) return element;
    }
    return null;
  }

  // ---- Tools ----------------------------------------------------------------------------------

  function setTool(name) {
    tool = name;
    svg.dataset.tool = name;
    if (name !== "select") setSelection([]);
    root.querySelectorAll("[data-whiteboard-tool]").forEach((button) => {
      const active = button.dataset.whiteboardTool === name;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }

  function canAdd() {
    if (doc.elements.length < format.LIMITS.elements) return true;
    showHint("Das Whiteboard ist voll. Lösche etwas, um weiterzuzeichnen.");
    return false;
  }

  // Adds a finished element, rounded for saving, and selects it so it can be moved or restyled at once.
  function addElement(draft) {
    const element = { ...format.moveElement(draft, 0, 0), id: newId() };
    commit([], [{ index: doc.elements.length, element }]);
    setTool("select");
    setSelection([element.id]);
  }

  function startStroke(world) {
    if (!canAdd()) return null;
    if (format.pointCount(doc) >= format.LIMITS.totalPoints) {
      showHint("Das Whiteboard ist voll. Lösche etwas, um weiterzuzeichnen.");
      return null;
    }
    const node = svgEl("path", { ...lineAttrs(style.color, style.width), d: "" });
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

  function isFillable(element) {
    return element.type === "rect" || element.type === "ellipse";
  }

  // Fills the given shapes, or clears the fill when all of them already have one.
  function toggleFill(elements) {
    const shapes = elements.filter(isFillable);
    const fill = !shapes.every((element) => element.fill);
    const entries = shapes
      .filter((element) => element.fill !== fill)
      .map((element) => ({ index: doc.elements.indexOf(element), element }));
    if (!entries.length) return;
    commit(
      entries,
      entries.map(({ index, element }) => ({ index, element: { ...element, fill } })),
    );
  }

  function fillAt(world) {
    const reach = HIT_PIXELS / view.k;
    const shape = [...doc.elements]
      .reverse()
      .find((element) => isFillable(element) && format.containsPoint(element, world.x, world.y, reach));
    if (shape) toggleFill([shape]);
    else showHint("Füllen lassen sich Rechtecke und Ellipsen: einfach hineinklicken.");
  }

  // "whole" removes every element the eraser touches; "part" cuts the touched bit out of freehand
  // strokes and leaves the rest as separate strokes (shapes and text still go as a whole).
  function eraseAt(eraser, world) {
    const reach = ERASER_PIXELS / view.k;
    const from = eraser.last || world;
    eraser.last = world;
    let changed = false;
    const next = [];
    doc.elements.forEach((element) => {
      if (!format.hitsElement(element, from.x, from.y, world.x, world.y, reach, measure)) {
        next.push(element);
        return;
      }
      changed = true;
      if (eraserModeSelect.value !== "part" || element.type !== "stroke") return;
      const pieces = format.eraseFromStroke(
        element.points,
        from.x,
        from.y,
        world.x,
        world.y,
        reach + element.width / 2,
      );
      // No room for the extra strokes a cut makes: the stroke goes as a whole instead.
      if (doc.elements.length + pieces.length > format.LIMITS.elements) return;
      pieces.forEach((points) => next.push({ ...element, id: newId(), points }));
    });
    if (!changed) return;
    doc.elements = next;
    render();
    markDirty();
  }

  function startErase(world) {
    const eraser = { type: "erase", before: [...doc.elements] };
    eraseAt(eraser, world);
    return eraser;
  }

  // The board already changed while erasing; the difference to how the gesture found it becomes one
  // undo step. Indexes are positions in the old board for what went and in the new one for what came.
  function finishErase(eraser) {
    const kept = new Set(doc.elements);
    const old = new Set(eraser.before);
    const removed = eraser.before.flatMap((element, index) => (kept.has(element) ? [] : [{ index, element }]));
    const added = doc.elements.flatMap((element, index) => (old.has(element) ? [] : [{ index, element }]));
    if (removed.length) record({ removed, added });
  }

  // The element a shape tool drag from `start` to `end` describes. `constrain` (Shift) snaps lines to
  // 45° steps and makes rectangles square and ellipses round.
  function shapeFrom(kind, start, end, constrain) {
    const base = { id: "draft", type: kind, color: style.color, width: style.width };
    if (kind === "line" || kind === "arrow") {
      let { x, y } = end;
      if (constrain) {
        const step = Math.PI / 4;
        const angle = Math.round(Math.atan2(y - start.y, x - start.x) / step) * step;
        const length = Math.hypot(x - start.x, y - start.y);
        x = start.x + Math.cos(angle) * length;
        y = start.y + Math.sin(angle) * length;
      }
      return { ...base, x1: start.x, y1: start.y, x2: x, y2: y };
    }
    return { ...base, ...format.normalizeBox(start.x, start.y, end.x, end.y, constrain), fill: false };
  }

  function finishShape(shape) {
    guideLayer.replaceChildren();
    if (!shape.draft) return;
    const box = format.elementBounds(shape.draft);
    // A click without a drag would leave an invisible element behind.
    if (Math.hypot(box.x2 - box.x1, box.y2 - box.y1) * view.k < MIN_SHAPE_PIXELS) return;
    addElement(shape.draft);
  }

  function startSelect(event, world) {
    const element = topElementAt(world);
    if (!element) {
      if (!event.shiftKey) setSelection([]);
      return { type: "marquee", start: world, base: new Set(selection) };
    }
    if (event.shiftKey) {
      const ids = new Set(selection);
      if (ids.has(element.id)) ids.delete(element.id);
      else ids.add(element.id);
      setSelection(ids);
      return null;
    }
    if (!selection.has(element.id)) setSelection([element.id]);
    return { type: "move", start: world, startX: event.clientX, startY: event.clientY, moved: false, dx: 0, dy: 0 };
  }

  function updateMove(move, event) {
    if (!move.moved && Math.hypot(event.clientX - move.startX, event.clientY - move.startY) < DRAG_PIXELS) return;
    const world = toWorld(event.clientX, event.clientY);
    move.moved = true;
    move.dx = world.x - move.start.x;
    move.dy = world.y - move.start.y;
    // Only a transform while dragging; the elements change once, on release.
    const transform = `translate(${move.dx} ${move.dy})`;
    selection.forEach((id) => nodes.get(id)?.setAttribute("transform", transform));
    selectionLayer.setAttribute("transform", transform);
  }

  function clearMovePreview() {
    selection.forEach((id) => nodes.get(id)?.removeAttribute("transform"));
    selectionLayer.removeAttribute("transform");
  }

  function finishMove(move) {
    clearMovePreview();
    if (move.moved) replaceSelected((element) => format.moveElement(element, move.dx, move.dy));
  }

  function marqueeBox(marquee) {
    return {
      x1: Math.min(marquee.start.x, marquee.current.x),
      y1: Math.min(marquee.start.y, marquee.current.y),
      x2: Math.max(marquee.start.x, marquee.current.x),
      y2: Math.max(marquee.start.y, marquee.current.y),
    };
  }

  function updateMarquee(marquee, world) {
    marquee.current = world;
    const box = marqueeBox(marquee);
    guideLayer.replaceChildren(
      svgEl("rect", {
        x: box.x1,
        y: box.y1,
        width: box.x2 - box.x1,
        height: box.y2 - box.y1,
        fill: "rgba(179, 119, 47, 0.08)",
        stroke: SELECT_COLOR,
        "stroke-dasharray": "4 3",
        "vector-effect": "non-scaling-stroke",
      }),
    );
  }

  function finishMarquee(marquee) {
    guideLayer.replaceChildren();
    if (!marquee.current) return;
    const box = marqueeBox(marquee);
    const inside = doc.elements.filter((element) => {
      const bounds = format.elementBounds(element, measure);
      return bounds.x1 >= box.x1 && bounds.y1 >= box.y1 && bounds.x2 <= box.x2 && bounds.y2 <= box.y2;
    });
    setSelection([...marquee.base, ...inside.map((element) => element.id)]);
  }

  // ---- Text -----------------------------------------------------------------------------------

  function openText(world, element = null) {
    if (!element && !canAdd()) return;
    editing = {
      element,
      x: element ? element.x : world.x,
      y: element ? element.y : world.y,
      size: element ? element.size : style.size,
      color: element ? element.color : style.color,
    };
    if (element) nodes.get(element.id)?.setAttribute("visibility", "hidden");
    textInput.value = element ? element.text : "";
    textInput.hidden = false;
    positionTextInput();
    textInput.focus();
  }

  function positionTextInput() {
    if (!editing) return;
    const svgRect = svg.getBoundingClientRect();
    const wrapRect = canvasWrap.getBoundingClientRect();
    const size = format.TEXT_SIZES[editing.size];
    const fontSize = size * view.k;
    const lines = textInput.value.split("\n");
    const width = Math.max(...lines.map((line) => measure(line, size))) * view.k;
    Object.assign(textInput.style, {
      left: `${svgRect.left - wrapRect.left - canvasWrap.clientLeft + view.x + editing.x * view.k}px`,
      top: `${svgRect.top - wrapRect.top - canvasWrap.clientTop + view.y + editing.y * view.k}px`,
      fontSize: `${fontSize}px`,
      width: `${width + fontSize}px`,
      height: `${lines.length * fontSize * format.TEXT_LINE_HEIGHT}px`,
      color: inkColor(editing.color),
    });
  }

  function closeText() {
    if (!editing) return;
    const current = editing;
    editing = null;
    textInput.hidden = true;
    const text = textInput.value.replace(/\s+$/, "").slice(0, format.LIMITS.text);
    const original = current.element;
    if (original) {
      nodes.get(original.id)?.removeAttribute("visibility");
      const index = doc.elements.indexOf(original);
      if (index === -1) return;
      if (!text.trim()) commit([{ index, element: original }], []);
      else if (text !== original.text)
        commit([{ index, element: original }], [{ index, element: { ...original, text } }]);
      return;
    }
    if (!text.trim()) return;
    addElement({
      id: "draft",
      type: "text",
      color: current.color,
      x: current.x,
      y: current.y,
      size: current.size,
      text,
    });
  }

  textInput.addEventListener("input", positionTextInput);
  textInput.addEventListener("blur", closeText);
  textInput.addEventListener("keydown", (event) => {
    // Enter makes a new line; Escape or Ctrl+Enter finish the text. Closing directly rather than through
    // the blur, which browsers skip while the window itself has no focus.
    if (event.key === "Escape" || (event.key === "Enter" && (event.ctrlKey || event.metaKey))) {
      event.preventDefault();
      closeText();
      svg.focus({ preventScroll: true });
    }
  });

  // ---- Pointer input --------------------------------------------------------------------------

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
    if (interaction?.type === "move") clearMovePreview();
    guideLayer.replaceChildren();
    svg.classList.remove("is-panning");
    interaction = null;
  }

  function onPointerDown(event) {
    if (event.pointerType === "touch") {
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pointers.size === 2) {
        // A second finger turns the gesture into pan/zoom; whatever the first one started is dropped.
        cancelInteraction();
        interaction = startPinch();
        return;
      }
      if (pointers.size > 2) return;
    }
    if (editing) {
      // The first click outside an open text finishes it instead of starting something new.
      closeText();
      return;
    }
    if (event.button === 1 || (event.button === 0 && (tool === "hand" || spaceHeld))) {
      event.preventDefault();
      interaction = startPan(event);
    } else if (event.button !== 0) {
      return;
    } else {
      const world = toWorld(event.clientX, event.clientY);
      if (tool === "pen") interaction = startStroke(world);
      else if (tool === "eraser") interaction = startErase(world);
      else if (tool === "select") interaction = startSelect(event, world);
      // The text field opens on release: focus moves to the canvas on press and would close it again.
      else if (tool === "text") interaction = { type: "text", world };
      else if (tool === "fill") fillAt(world);
      else if (SHAPE_TOOLS.has(tool) && canAdd())
        interaction = { type: "shape", kind: tool, start: world, draft: null };
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
    } else if (interaction.type === "shape") {
      const world = toWorld(event.clientX, event.clientY);
      interaction.draft = shapeFrom(interaction.kind, interaction.start, world, event.shiftKey);
      guideLayer.replaceChildren(elementNode(interaction.draft));
    } else if (interaction.type === "move") {
      updateMove(interaction, event);
    } else if (interaction.type === "marquee") {
      updateMarquee(interaction, toWorld(event.clientX, event.clientY));
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
    if (event.type === "pointercancel") {
      cancelInteraction();
      return;
    }
    interaction = null;
    svg.classList.remove("is-panning");
    if (current.type === "stroke") finishStroke(current);
    else if (current.type === "erase") finishErase(current);
    else if (current.type === "shape") finishShape(current);
    else if (current.type === "move") finishMove(current);
    else if (current.type === "marquee") finishMarquee(current);
    else if (current.type === "text") {
      const element = topElementAt(current.world);
      if (element?.type === "text") openText(null, element);
      // Start the text so the caret sits where the user clicked rather than below it.
      else openText({ x: current.world.x, y: current.world.y - format.TEXT_SIZES[style.size] * 0.6 });
    }
  }

  svg.addEventListener("pointerdown", onPointerDown);
  svg.addEventListener("pointermove", onPointerMove);
  svg.addEventListener("pointerup", onPointerUp);
  svg.addEventListener("pointercancel", onPointerUp);
  svg.addEventListener("dblclick", (event) => {
    if (tool !== "select") return;
    const element = topElementAt(toWorld(event.clientX, event.clientY));
    if (element?.type === "text") openText(null, element);
  });
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

  // ---- Export & import ------------------------------------------------------------------------

  function slug(value) {
    return (
      value
        .normalize("NFKD")
        .replace(/[̀-ͯ]/g, "")
        .replace(/[^A-Za-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .toLowerCase() || "whiteboard"
    );
  }

  function download(filename, blob) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // A standalone SVG of the whole board on white paper, framed by its content plus a margin.
  function buildExportSvg() {
    const bounds = format.documentBounds(doc, measure) || { x1: 0, y1: 0, x2: 200, y2: 120 };
    const margin = 24;
    const x = Math.floor(bounds.x1 - margin);
    const y = Math.floor(bounds.y1 - margin);
    const width = Math.ceil(bounds.x2 - bounds.x1 + margin * 2);
    const height = Math.ceil(bounds.y2 - bounds.y1 + margin * 2);
    const exportSvg = svgEl("svg", { xmlns: SVG_NS, width, height, viewBox: `${x} ${y} ${width} ${height}` });
    exportSvg.append(svgEl("rect", { x, y, width, height, fill: "#ffffff" }));
    doc.elements.forEach((item) => exportSvg.append(elementNode(item)));
    return { markup: new XMLSerializer().serializeToString(exportSvg), width, height };
  }

  function exportPng() {
    const { markup, width, height } = buildExportSvg();
    // Canvases have a maximum size; very large boards are scaled down instead of failing.
    const scale = Math.min(2, 8000 / Math.max(width, height));
    const image = new Image();
    const url = URL.createObjectURL(new Blob([markup], { type: "image/svg+xml" }));
    image.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(width * scale);
      canvas.height = Math.round(height * scale);
      const context = canvas.getContext("2d");
      context.scale(scale, scale);
      context.drawImage(image, 0, 0, width, height);
      URL.revokeObjectURL(url);
      canvas.toBlob((blob) => blob && download(`${slug(titleInput.value)}.png`, blob), "image/png");
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      showHint("Das Bild konnte nicht erstellt werden.");
    };
    image.src = url;
  }

  function runExport(kind) {
    exportMenu.open = false;
    const base = slug(titleInput.value);
    if (kind === "png") exportPng();
    else if (kind === "svg") download(`${base}.svg`, new Blob([buildExportSvg().markup], { type: "image/svg+xml" }));
    else if (kind === "json") {
      const data = { format: "lunora-whiteboard", title: titleInput.value.trim(), document: doc };
      download(`${base}.json`, new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    } else if (kind === "import") {
      importInput.value = "";
      importInput.click();
    }
  }

  root.querySelectorAll("[data-whiteboard-export]").forEach((button) => {
    button.addEventListener("click", () => runExport(button.dataset.whiteboardExport));
  });

  // Accepts the editor's own JSON export and the files from the data export. The server validates on
  // save; here it only has to be drawable, so a broken file fails before it replaces anything.
  importInput.addEventListener("change", async () => {
    const [file] = importInput.files;
    if (!file) return;
    let imported;
    try {
      const data = JSON.parse(await file.text());
      imported = format.normalizeDocument(data?.document ?? data);
      if (imported.elements.length > format.LIMITS.elements) throw new Error("too many elements");
      imported.elements.forEach(elementNode);
    } catch (_error) {
      showHint("Die Datei ist kein gültiges Whiteboard (JSON).");
      return;
    }
    if (!window.confirm("Das aktuelle Whiteboard durch den Import ersetzen? (Rückgängig ist möglich.)")) return;
    setTool("select");
    commit(
      doc.elements.map((element, index) => ({ index, element })),
      imported.elements.map((element, index) => ({ index, element })),
    );
    fitView();
    showHint(`${imported.elements.length} Element(e) importiert.`);
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

  function pressOption(container, attribute, value) {
    container.querySelectorAll("button").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset[attribute] === String(value)));
    });
  }

  function refreshStyleControls() {
    pressOption(colorOptions, "color", style.color);
    pressOption(widthOptions, "width", style.width);
    sizeSelect.value = style.size;
  }

  function restyle(element, changes) {
    const next = { ...element };
    if ("color" in changes) next.color = changes.color;
    if ("width" in changes && element.type !== "text") next.width = changes.width;
    if ("size" in changes && element.type === "text") next.size = changes.size;
    return next;
  }

  // A style choice applies to the selection when there is one, and to everything drawn next.
  function applyStyle(changes) {
    Object.assign(style, changes);
    refreshStyleControls();
    if (tool === "select" && selection.size) replaceSelected((element) => restyle(element, changes));
    else if (["select", "eraser", "hand"].includes(tool) && ("color" in changes || "width" in changes)) setTool("pen");
  }

  colorOptions.addEventListener("click", (event) => {
    const button = event.target.closest("[data-color]");
    if (button) applyStyle({ color: button.dataset.color });
  });
  widthOptions.addEventListener("click", (event) => {
    const button = event.target.closest("[data-width]");
    if (button) applyStyle({ width: Number(button.dataset.width) });
  });
  sizeSelect.addEventListener("change", () => applyStyle({ size: sizeSelect.value }));

  // Enter on the canvas is the keyboard route to every tool that otherwise needs a drag.
  function insertAtCenter() {
    const center = visibleCenter();
    if (tool === "select") {
      const element = selection.size === 1 ? findElement([...selection][0]) : null;
      if (element?.type === "text") openText(null, element);
    } else if (tool === "text") {
      openText(center);
    } else if (SHAPE_TOOLS.has(tool) && canAdd()) {
      const w = 160 / view.k;
      const h = (tool === "line" || tool === "arrow" ? 0 : 100) / view.k;
      const start = { x: center.x - w / 2, y: center.y - h / 2 };
      addElement(shapeFrom(tool, start, { x: start.x + w, y: start.y + h }, false));
    }
  }

  const ACTIONS = {
    undo,
    redo,
    duplicate: duplicateSelection,
    delete: deleteSelection,
    "zoom-in": () => zoomAt(1.2),
    "zoom-out": () => zoomAt(1 / 1.2),
    "zoom-reset": () => zoomAt(1 / view.k),
    fit: () => fitView(FIT_MAX_ZOOM),
  };
  root.querySelectorAll("[data-whiteboard-action]").forEach((button) => {
    button.addEventListener("click", () => ACTIONS[button.dataset.whiteboardAction]?.());
  });
  root.querySelectorAll("[data-whiteboard-tool]").forEach((button) => {
    button.addEventListener("click", () => {
      const name = button.dataset.whiteboardTool;
      // With shapes selected the bucket fills them at once instead of becoming the tool.
      if (name === "fill" && tool === "select" && selectedEntries().some(({ element }) => isFillable(element))) {
        toggleFill(selectedEntries().map(({ element }) => element));
      } else {
        setTool(name);
      }
    });
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
    } else if (modifier && key === "a") {
      event.preventDefault();
      setTool("select");
      setSelection(doc.elements.map((element) => element.id));
    } else if (modifier && key === "d") {
      event.preventDefault();
      duplicateSelection();
    } else if (modifier) {
      return;
    } else if (key === " ") {
      // Only on the canvas: on a focused button the space bar has to keep pressing it.
      if (event.target !== svg && event.target !== document.body) return;
      event.preventDefault();
      spaceHeld = true;
      svg.classList.add("is-space-pan");
    } else if (key === "enter") {
      if (event.target !== svg) return;
      event.preventDefault();
      insertAtCenter();
    } else if ((key === "delete" || key === "backspace") && selection.size) {
      event.preventDefault();
      deleteSelection();
    } else if (key === "escape") {
      setSelection([]);
    } else if (key.startsWith("arrow") && selection.size) {
      event.preventDefault();
      const step = (event.shiftKey ? 20 : 2) / view.k;
      const dx = key === "arrowleft" ? -step : key === "arrowright" ? step : 0;
      const dy = key === "arrowup" ? -step : key === "arrowdown" ? step : 0;
      nudgeSelection(dx, dy);
    } else if (TOOL_KEYS[key]) {
      setTool(TOOL_KEYS[key]);
    } else if (key === "f") {
      fitView(FIT_MAX_ZOOM);
    } else if (key === "+" || key === "=") {
      zoomAt(1.2);
    } else if (key === "-") {
      zoomAt(1 / 1.2);
    } else if (key === "0") {
      zoomAt(1 / view.k);
    }
  });
  document.addEventListener("click", (event) => {
    if (exportMenu.open && !exportMenu.contains(event.target)) exportMenu.open = false;
  });
  document.addEventListener("keyup", (event) => {
    if (event.key !== " ") return;
    spaceHeld = false;
    svg.classList.remove("is-space-pan");
  });

  try {
    if (window.localStorage.getItem(ERASER_MODE_KEY) === "part") eraserModeSelect.value = "part";
  } catch (_error) {
    // Storage can be blocked; the mode then just isn't remembered.
  }
  eraserModeSelect.addEventListener("change", () => {
    setTool("eraser");
    try {
      window.localStorage.setItem(ERASER_MODE_KEY, eraserModeSelect.value);
    } catch (_error) {
      // See above.
    }
  });

  setTool("pen");
  refreshStyleControls();
  updateHistoryButtons();
  render();
  drawSelection();
  fitView();
  // Text widths depend on the web font; measure again once it has loaded.
  document.fonts?.ready.then(() => {
    render();
    drawSelection();
  });
})();
