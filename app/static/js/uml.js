(function () {
  const root = document.querySelector("[data-uml-editor]");
  const format = window.LunoraUmlFormat;
  if (!root || !format) return;

  const SVG_NS = "http://www.w3.org/2000/svg";
  const FONT_FAMILY = "Inter, 'Segoe UI', Arial, sans-serif";
  const FONT_SIZE = 13;
  const LINE_HEIGHT = 18;
  const PAD_X = 10;
  const PAD_Y = 6;
  const MIN_CLASS_WIDTH = 150;
  const NOTE_WRAP_WIDTH = 200;
  const GRID = 20;
  const SNAP = 10;
  const PARALLEL_GAP = 16;
  const MIN_ZOOM = 0.2;
  const MAX_ZOOM = 3;
  const HISTORY_LIMIT = 200;
  const SAVE_DELAY = 1200;
  const LINE_COLOR = "#4a3f33";
  const SELECT_COLOR = "#b3772f";
  const PAPER_COLOR = "#fbfaf7";

  const PALETTE = {
    default: { label: "Standard", body: "#fffdf8", header: "#f1e7d5", stroke: "#6d5a42" },
    blue: { label: "Blau", body: "#f3f7fc", header: "#d7e5f5", stroke: "#3d5f86" },
    green: { label: "Grün", body: "#f2f8f2", header: "#d5ead8", stroke: "#3f6b48" },
    yellow: { label: "Gelb", body: "#fefaea", header: "#f6e9b5", stroke: "#7a6326" },
    red: { label: "Rot", body: "#fcf2f2", header: "#f3d4d4", stroke: "#8a3f3f" },
    violet: { label: "Violett", body: "#f7f3fb", header: "#e2d6f1", stroke: "#5d4a82" },
    gray: { label: "Grau", body: "#f7f7f7", header: "#e2e2e2", stroke: "#555555" },
  };
  const KIND_LABELS = { class: "Klasse", abstract: "Abstrakte Klasse", interface: "Interface", enum: "Enum" };
  const DEFAULT_NAMES = { class: "Klasse", abstract: "AbstrakteKlasse", interface: "Interface", enum: "Enum" };
  const KIND_STEREOTYPES = { abstract: "abstract", interface: "interface", enum: "enumeration" };
  const RELATION_LABELS = {
    association: "Assoziation",
    directed_association: "Gerichtete Assoziation",
    aggregation: "Aggregation",
    composition: "Komposition",
    inheritance: "Vererbung",
    realization: "Realisierung",
    dependency: "Abhängigkeit",
    note_link: "Notizverbindung",
  };
  const RELATION_HELP = {
    association: "Einfache Verbindung zwischen zwei Klassen.",
    directed_association: "Die Quelle kennt das Ziel (Pfeil zeigt zum Ziel).",
    aggregation: "Das Ziel ist das Ganze (leere Raute), die Quelle ein Teil davon.",
    composition: "Das Ziel ist das Ganze (gefüllte Raute), die Quelle existiert nur mit ihm.",
    inheritance: "Die Quelle erbt vom Ziel (Oberklasse).",
    realization: "Die Quelle implementiert das Interface am Ziel.",
    dependency: "Die Quelle benutzt das Ziel.",
    note_link: "Verbindet eine Notiz mit einem Element.",
  };
  const DASHED_KINDS = new Set(["realization", "dependency", "note_link"]);
  const MARKERS = {
    directed_association: "arrow",
    aggregation: "diamond",
    composition: "diamond-filled",
    inheritance: "triangle",
    realization: "triangle",
    dependency: "arrow",
  };

  const payload = JSON.parse(document.getElementById("uml-diagram-data").textContent);
  const saveUrl = root.dataset.saveUrl;
  const csrfToken = document.querySelector("meta[name='csrf-token']")?.content || "";
  const svg = root.querySelector("[data-uml-canvas]");

  const titleInput = root.querySelector("[data-uml-title]");
  const statusLabel = root.querySelector("[data-uml-status]");
  const hintLabel = root.querySelector("[data-uml-hint]");
  const zoomLabel = root.querySelector("[data-uml-zoom-label]");
  const relationKindSelect = root.querySelector("[data-uml-relation-kind]");
  const connectButton = root.querySelector("[data-uml-action='connect']");
  const gridButton = root.querySelector("[data-uml-action='grid']");
  const exportMenu = root.querySelector(".uml-export-menu");
  const importInput = root.querySelector("[data-uml-import-input]");
  const panels = Object.fromEntries(
    Array.from(root.querySelectorAll("[data-uml-panel]")).map((panel) => [panel.dataset.umlPanel, panel]),
  );
  const fields = Object.fromEntries(
    Array.from(root.querySelectorAll("[data-uml-field]")).map((field) => [field.dataset.umlField, field]),
  );
  const outline = root.querySelector("[data-uml-outline]");
  const colorOptions = root.querySelector("[data-uml-colors]");
  const memberError = root.querySelector("[data-uml-member-error]");
  const dialog = root.querySelector("[data-uml-dialog]");
  const defaultHint = hintLabel.textContent;

  let doc = format.normalizeDocument(payload.document);
  const view = { x: 0, y: 0, k: 1 };
  let selection = new Set();
  let snapEnabled = true;
  let connectMode = false;
  let connectSource = null;
  let interaction = null;
  let layouts = new Map();
  let hintTimer = null;
  const history = { undo: [], redo: [], lastKey: null, lastTime: 0 };
  const saveState = { dirty: false, saving: false, again: false, timer: null };

  // ---- Helpers --------------------------------------------------------------------------------

  function svgEl(tag, attrs = {}, children = []) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== false) node.setAttribute(key, String(value));
    });
    children.forEach((child) => child && node.append(child));
    return node;
  }

  function svgText(content, x, y, options = {}) {
    const node = svgEl("text", {
      x,
      y,
      fill: options.fill || LINE_COLOR,
      "font-family": FONT_FAMILY,
      "font-size": options.size || FONT_SIZE,
      "font-weight": options.weight,
      "font-style": options.italic ? "italic" : undefined,
      "text-decoration": options.underline ? "underline" : undefined,
      "text-anchor": options.anchor,
      "paint-order": options.halo ? "stroke" : undefined,
      stroke: options.halo ? PAPER_COLOR : undefined,
      "stroke-width": options.halo ? 4 : undefined,
      "stroke-linejoin": options.halo ? "round" : undefined,
    });
    node.textContent = content;
    return node;
  }

  const measureContext = document.createElement("canvas").getContext("2d");
  function textWidth(content, options = {}) {
    measureContext.font = `${options.italic ? "italic " : ""}${options.weight || 400} ${options.size || FONT_SIZE}px ${FONT_FAMILY}`;
    return measureContext.measureText(content).width;
  }

  function findClass(id) {
    return doc.classes.find((item) => item.id === id);
  }

  function findNote(id) {
    return doc.notes.find((item) => item.id === id);
  }

  function findNode(id) {
    return findClass(id) || findNote(id);
  }

  function findRelation(id) {
    return doc.relations.find((item) => item.id === id);
  }

  function nodeType(id) {
    if (findClass(id)) return "class";
    if (findNote(id)) return "note";
    if (findRelation(id)) return "relation";
    return null;
  }

  function snap(value, force = snapEnabled) {
    return force ? Math.round(value / SNAP) * SNAP : Math.round(value);
  }

  function isTyping(target) {
    return Boolean(target?.closest?.("input, textarea, select, [contenteditable='true']"));
  }

  function showHint(message, duration = 3500) {
    window.clearTimeout(hintTimer);
    hintLabel.textContent = message;
    hintLabel.classList.add("is-active");
    if (duration) {
      hintTimer = window.setTimeout(() => {
        hintLabel.classList.remove("is-active");
        updateConnectHint();
      }, duration);
    }
  }

  function updateConnectHint() {
    if (!connectMode) {
      hintLabel.textContent = defaultHint;
      hintLabel.classList.remove("is-active");
      return;
    }
    const kindLabel = RELATION_LABELS[relationKindSelect.value];
    hintLabel.textContent = connectSource
      ? `${kindLabel}: jetzt das Ziel anklicken (Esc bricht ab).`
      : `${kindLabel}: zuerst die Quelle anklicken.`;
    hintLabel.classList.add("is-active");
  }

  function slug(value) {
    return (
      value
        .normalize("NFKD")
        .replace(/[̀-ͯ]/g, "")
        .replace(/[^A-Za-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .toLowerCase() || "diagramm"
    );
  }

  // ---- Layout ---------------------------------------------------------------------------------

  function classLayout(item) {
    const stereotypes = [KIND_STEREOTYPES[item.kind], item.stereotype.trim()].filter(Boolean);
    const header = [];
    if (stereotypes.length) header.push({ text: `«${stereotypes.join(", ")}»`, size: 11 });
    header.push({ text: item.name || "Unbenannt", weight: 700, italic: item.kind === "abstract" });

    const compartments = [];
    const attributes = item.attributes.map((entry) => ({
      text: format.formatAttribute(entry).replace("{static} ", ""),
      underline: entry.is_static,
    }));
    const methods = item.methods.map((entry) => ({
      text: format.formatMethod(entry).replace("{static} ", "").replace("{abstract} ", ""),
      underline: entry.is_static,
      italic: entry.is_abstract,
    }));
    if (item.kind === "enum") {
      compartments.push(item.literals.map((literal) => ({ text: literal })));
      if (attributes.length) compartments.push(attributes);
      if (methods.length) compartments.push(methods);
    } else if (item.kind === "interface") {
      if (attributes.length) compartments.push(attributes);
      compartments.push(methods);
    } else {
      compartments.push(attributes, methods);
    }

    let width = MIN_CLASS_WIDTH;
    header.forEach((line) => (width = Math.max(width, textWidth(line.text, line) + PAD_X * 2 + 8)));
    compartments.flat().forEach((line) => (width = Math.max(width, textWidth(line.text, line) + PAD_X * 2)));
    width = Math.ceil(width);

    const headerHeight = PAD_Y * 2 + header.reduce((sum, line) => sum + (line.size ? 15 : LINE_HEIGHT), 0);
    const compartmentHeights = compartments.map((lines) =>
      lines.length ? PAD_Y * 2 + lines.length * LINE_HEIGHT : 14,
    );
    const height = headerHeight + compartmentHeights.reduce((sum, value) => sum + value, 0);
    return { type: "class", x: item.x, y: item.y, w: width, h: height, header, headerHeight, compartments };
  }

  function wrapNoteText(content) {
    const lines = [];
    (content || " ").split("\n").forEach((paragraph) => {
      let current = "";
      paragraph.split(/\s+/).forEach((word) => {
        const candidate = current ? `${current} ${word}` : word;
        if (current && textWidth(candidate) > NOTE_WRAP_WIDTH) {
          lines.push(current);
          current = word;
        } else {
          current = candidate;
        }
      });
      lines.push(current);
    });
    return lines;
  }

  function noteLayout(item) {
    const lines = wrapNoteText(item.text);
    const width = Math.ceil(Math.max(110, ...lines.map((line) => textWidth(line) + 34)));
    return { type: "note", x: item.x, y: item.y, w: width, h: lines.length * LINE_HEIGHT + 20, lines };
  }

  function computeLayouts() {
    layouts = new Map();
    doc.classes.forEach((item) => layouts.set(item.id, classLayout(item)));
    doc.notes.forEach((item) => layouts.set(item.id, noteLayout(item)));
  }

  function contentBounds() {
    let bounds = null;
    layouts.forEach((box) => {
      const next = { x1: box.x, y1: box.y, x2: box.x + box.w, y2: box.y + box.h };
      bounds = bounds
        ? {
            x1: Math.min(bounds.x1, next.x1),
            y1: Math.min(bounds.y1, next.y1),
            x2: Math.max(bounds.x2, next.x2),
            y2: Math.max(bounds.y2, next.y2),
          }
        : next;
    });
    return bounds;
  }

  // ---- Relation geometry ----------------------------------------------------------------------

  function boxCenter(box) {
    return { x: box.x + box.w / 2, y: box.y + box.h / 2 };
  }

  // Where a ray from an inside point p in direction d leaves the box.
  function exitPoint(box, point, direction) {
    const tx =
      direction.x > 0
        ? (box.x + box.w - point.x) / direction.x
        : direction.x < 0
          ? (box.x - point.x) / direction.x
          : Infinity;
    const ty =
      direction.y > 0
        ? (box.y + box.h - point.y) / direction.y
        : direction.y < 0
          ? (box.y - point.y) / direction.y
          : Infinity;
    const t = Math.min(tx, ty);
    if (!Number.isFinite(t) || t < 0) return point;
    return { x: point.x + direction.x * t, y: point.y + direction.y * t };
  }

  function relationOffsets() {
    const groups = new Map();
    doc.relations.forEach((relation) => {
      if (relation.source === relation.target) return;
      const key = [relation.source, relation.target].sort().join("|");
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(relation.id);
    });
    const offsets = new Map();
    groups.forEach((ids) => {
      ids.forEach((id, index) => offsets.set(id, (index - (ids.length - 1) / 2) * PARALLEL_GAP));
    });
    return offsets;
  }

  function relationGeometry(relation, offset) {
    const sourceBox = layouts.get(relation.source);
    const targetBox = layouts.get(relation.target);
    if (!sourceBox || !targetBox) return null;

    if (relation.source === relation.target) {
      const start = { x: sourceBox.x + sourceBox.w * 0.7, y: sourceBox.y };
      const end = { x: sourceBox.x + sourceBox.w, y: sourceBox.y + Math.min(30, sourceBox.h / 2) };
      return {
        d: `M${start.x},${start.y} C${start.x},${start.y - 44} ${end.x + 44},${end.y} ${end.x},${end.y}`,
        start,
        end,
        startDir: { x: 0, y: -1 },
        endDir: { x: -1, y: 0 },
        normal: { x: 0, y: -1 },
        mid: { x: end.x + 22, y: start.y - 26 },
        selfLoop: true,
      };
    }

    const sourceCenter = boxCenter(sourceBox);
    const targetCenter = boxCenter(targetBox);
    const dx = targetCenter.x - sourceCenter.x;
    const dy = targetCenter.y - sourceCenter.y;
    const length = Math.hypot(dx, dy) || 1;
    const direction = { x: dx / length, y: dy / length };
    // The normal follows the id-sorted pair, so parallel relations spread out consistently both ways.
    const flip = relation.source > relation.target ? -1 : 1;
    const normal = { x: -direction.y * flip, y: direction.x * flip };
    const shiftedSource = { x: sourceCenter.x + normal.x * offset, y: sourceCenter.y + normal.y * offset };
    const shiftedTarget = { x: targetCenter.x + normal.x * offset, y: targetCenter.y + normal.y * offset };
    const start = exitPoint(sourceBox, shiftedSource, direction);
    const end = exitPoint(targetBox, shiftedTarget, { x: -direction.x, y: -direction.y });
    const textNormal = normal.y > 0 || (normal.y === 0 && normal.x > 0) ? { x: -normal.x, y: -normal.y } : normal;
    return {
      d: `M${start.x},${start.y} L${end.x},${end.y}`,
      start,
      end,
      startDir: direction,
      endDir: { x: -direction.x, y: -direction.y },
      normal: textNormal,
      mid: { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 },
      selfLoop: false,
    };
  }

  // ---- Rendering ------------------------------------------------------------------------------

  function markerDefs(color, suffix) {
    const common = { markerUnits: "userSpaceOnUse", orient: "auto", overflow: "visible" };
    return [
      svgEl(
        "marker",
        { ...common, id: `uml-triangle${suffix}`, markerWidth: 16, markerHeight: 16, refX: 15, refY: 8 },
        [svgEl("path", { d: "M1,1 L15,8 L1,15 Z", fill: "#ffffff", stroke: color, "stroke-width": 1.4 })],
      ),
      svgEl("marker", { ...common, id: `uml-arrow${suffix}`, markerWidth: 14, markerHeight: 14, refX: 13, refY: 7 }, [
        svgEl("path", {
          d: "M1,1 L13,7 L1,13",
          fill: "none",
          stroke: color,
          "stroke-width": 1.4,
          "stroke-linejoin": "round",
        }),
      ]),
      svgEl("marker", { ...common, id: `uml-diamond${suffix}`, markerWidth: 20, markerHeight: 14, refX: 19, refY: 7 }, [
        svgEl("path", { d: "M1,7 L10,1 L19,7 L10,13 Z", fill: "#ffffff", stroke: color, "stroke-width": 1.4 }),
      ]),
      svgEl(
        "marker",
        { ...common, id: `uml-diamond-filled${suffix}`, markerWidth: 20, markerHeight: 14, refX: 19, refY: 7 },
        [svgEl("path", { d: "M1,7 L10,1 L19,7 L10,13 Z", fill: color, stroke: color, "stroke-width": 1.4 })],
      ),
    ];
  }

  function renderRelation(relation, offset, interactive) {
    const geometry = relationGeometry(relation, offset);
    if (!geometry) return null;
    const selected = interactive && selection.has(relation.id);
    const color = selected ? SELECT_COLOR : LINE_COLOR;
    const marker = MARKERS[relation.kind];
    const group = svgEl("g", interactive ? { "data-id": relation.id, "data-type": "relation", class: "uml-hit" } : {});
    if (interactive) {
      group.append(svgEl("path", { d: geometry.d, fill: "none", stroke: "transparent", "stroke-width": 14 }));
    }
    group.append(
      svgEl("path", {
        d: geometry.d,
        fill: "none",
        stroke: color,
        "stroke-width": selected ? 2 : 1.4,
        "stroke-dasharray": DASHED_KINDS.has(relation.kind) ? "7 5" : undefined,
        "marker-end": marker ? `url(#uml-${marker}${selected ? "-selected" : ""})` : undefined,
      }),
    );

    const { normal } = geometry;
    const labelAt = (point, direction, distance) => ({
      x: point.x + direction.x * distance + normal.x * 12,
      y: point.y + direction.y * distance + normal.y * 12 + 4,
    });
    if (relation.label) {
      const position = { x: geometry.mid.x + normal.x * 10, y: geometry.mid.y + normal.y * 10 + 4 };
      group.append(
        svgText(relation.label, position.x, position.y, { anchor: "middle", halo: true, italic: true, fill: color }),
      );
    }
    if (relation.source_multiplicity) {
      const position = labelAt(geometry.start, geometry.startDir, 20);
      group.append(
        svgText(relation.source_multiplicity, position.x, position.y, {
          anchor: "middle",
          halo: true,
          size: 12,
          fill: color,
        }),
      );
    }
    if (relation.target_multiplicity) {
      const position = labelAt(geometry.end, geometry.endDir, 24);
      group.append(
        svgText(relation.target_multiplicity, position.x, position.y, {
          anchor: "middle",
          halo: true,
          size: 12,
          fill: color,
        }),
      );
    }
    return group;
  }

  function renderClass(item, interactive) {
    const box = layouts.get(item.id);
    const palette = PALETTE[item.color] || PALETTE.default;
    const selected = interactive && selection.has(item.id);
    const group = svgEl(
      "g",
      interactive ? { "data-id": item.id, "data-type": "class", class: "uml-hit uml-node" } : {},
    );
    group.append(
      svgEl("rect", {
        x: box.x,
        y: box.y,
        width: box.w,
        height: box.h,
        fill: palette.body,
        stroke: palette.stroke,
        "stroke-width": 1.4,
      }),
      svgEl("rect", {
        x: box.x + 0.7,
        y: box.y + 0.7,
        width: box.w - 1.4,
        height: box.headerHeight - 0.7,
        fill: palette.header,
      }),
    );

    let cursor = box.y + PAD_Y;
    box.header.forEach((line) => {
      const lineHeight = line.size ? 15 : LINE_HEIGHT;
      group.append(
        svgText(line.text, box.x + box.w / 2, cursor + lineHeight * 0.74, {
          anchor: "middle",
          size: line.size,
          weight: line.weight,
          italic: line.italic,
        }),
      );
      cursor += lineHeight;
    });
    cursor = box.y + box.headerHeight;

    box.compartments.forEach((lines) => {
      group.append(
        svgEl("line", {
          x1: box.x,
          y1: cursor,
          x2: box.x + box.w,
          y2: cursor,
          stroke: palette.stroke,
          "stroke-width": 1.2,
        }),
      );
      if (!lines.length) {
        cursor += 14;
        return;
      }
      cursor += PAD_Y;
      lines.forEach((line) => {
        group.append(
          svgText(line.text, box.x + PAD_X, cursor + LINE_HEIGHT * 0.74, {
            underline: line.underline,
            italic: line.italic,
          }),
        );
        cursor += LINE_HEIGHT;
      });
      cursor += PAD_Y;
    });

    if (selected) {
      group.append(
        svgEl("rect", {
          x: box.x - 4,
          y: box.y - 4,
          width: box.w + 8,
          height: box.h + 8,
          fill: "none",
          stroke: SELECT_COLOR,
          "stroke-width": 1.6,
          "stroke-dasharray": "5 3",
          rx: 3,
          "pointer-events": "none",
        }),
      );
    }
    return group;
  }

  function renderNote(item, interactive) {
    const box = layouts.get(item.id);
    const selected = interactive && selection.has(item.id);
    const fold = 12;
    const right = box.x + box.w;
    const group = svgEl("g", interactive ? { "data-id": item.id, "data-type": "note", class: "uml-hit uml-node" } : {});
    group.append(
      svgEl("path", {
        d: `M${box.x},${box.y} H${right - fold} L${right},${box.y + fold} V${box.y + box.h} H${box.x} Z`,
        fill: "#fff6c9",
        stroke: selected ? SELECT_COLOR : "#a08a45",
        "stroke-width": selected ? 2 : 1.3,
      }),
      svgEl("path", {
        d: `M${right - fold},${box.y} V${box.y + fold} H${right}`,
        fill: "none",
        stroke: "#a08a45",
        "stroke-width": 1.1,
      }),
    );
    box.lines.forEach((line, index) => {
      group.append(
        svgText(line, box.x + 10, box.y + 10 + index * LINE_HEIGHT + LINE_HEIGHT * 0.74, { fill: "#4d4222" }),
      );
    });
    return group;
  }

  function renderContent(interactive) {
    const group = svgEl("g");
    const offsets = relationOffsets();
    doc.relations.forEach((relation) => {
      const node = renderRelation(relation, offsets.get(relation.id) || 0, interactive);
      if (node) group.append(node);
    });
    doc.notes.forEach((item) => group.append(renderNote(item, interactive)));
    doc.classes.forEach((item) => group.append(renderClass(item, interactive)));
    return group;
  }

  let viewportGroup = null;
  let gridPattern = null;
  let overlayGroup = null;

  function render() {
    computeLayouts();
    gridPattern = svgEl("pattern", { id: "uml-grid", width: GRID, height: GRID, patternUnits: "userSpaceOnUse" }, [
      svgEl("circle", { cx: 1, cy: 1, r: 1, fill: "rgba(90, 72, 50, 0.22)" }),
    ]);
    const defs = svgEl("defs", {}, [
      gridPattern,
      ...markerDefs(LINE_COLOR, ""),
      ...markerDefs(SELECT_COLOR, "-selected"),
    ]);
    const background = svgEl("rect", {
      width: "100%",
      height: "100%",
      fill: snapEnabled ? "url(#uml-grid)" : "transparent",
      "data-background": "true",
    });
    viewportGroup = svgEl("g", {}, [renderContent(true)]);
    overlayGroup = svgEl("g", { "pointer-events": "none" });
    viewportGroup.append(overlayGroup);

    if (selection.size === 1) {
      const [id] = selection;
      const box = layouts.get(id);
      if (box) {
        const cx = box.x + box.w + 16;
        const cy = box.y + box.h / 2;
        const tooltip = svgEl("title");
        tooltip.textContent = "Ziehen, um eine Beziehung zu erstellen";
        viewportGroup.append(
          svgEl("g", { "data-handle": id, class: "uml-link-handle" }, [
            tooltip,
            svgEl("circle", { cx, cy, r: 9, fill: SELECT_COLOR, stroke: "#ffffff", "stroke-width": 2 }),
            svgEl("path", {
              d: `M${cx - 4},${cy} H${cx + 4} M${cx},${cy - 4} V${cy + 4}`,
              stroke: "#ffffff",
              "stroke-width": 2,
              "stroke-linecap": "round",
            }),
          ]),
        );
      }
    }

    if (connectMode && connectSource && layouts.get(connectSource)) {
      const box = layouts.get(connectSource);
      overlayGroup.append(
        svgEl("rect", {
          x: box.x - 6,
          y: box.y - 6,
          width: box.w + 12,
          height: box.h + 12,
          fill: "none",
          stroke: SELECT_COLOR,
          "stroke-width": 2,
          rx: 4,
        }),
      );
    }

    svg.replaceChildren(defs, background, viewportGroup);
    applyView();
  }

  function applyView() {
    const transform = `translate(${view.x} ${view.y}) scale(${view.k})`;
    viewportGroup?.setAttribute("transform", transform);
    gridPattern?.setAttribute("patternTransform", transform);
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

  function zoomAt(factor, clientX, clientY) {
    const size = canvasSize();
    const pointX = clientX ?? size.left + size.width / 2;
    const pointY = clientY ?? size.top + size.height / 2;
    const world = toWorld(pointX, pointY);
    view.k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.k * factor));
    view.x = pointX - size.left - world.x * view.k;
    view.y = pointY - size.top - world.y * view.k;
    applyView();
  }

  function fitView() {
    computeLayouts();
    const bounds = contentBounds();
    const size = canvasSize();
    if (!bounds) {
      view.k = 1;
      view.x = size.width / 2 - 100;
      view.y = size.height / 2 - 60;
      applyView();
      return;
    }
    const width = bounds.x2 - bounds.x1 + 120;
    const height = bounds.y2 - bounds.y1 + 120;
    view.k = Math.min(1.25, Math.max(MIN_ZOOM, Math.min(size.width / width, size.height / height)));
    view.x = (size.width - (bounds.x2 - bounds.x1) * view.k) / 2 - bounds.x1 * view.k;
    view.y = (size.height - (bounds.y2 - bounds.y1) * view.k) / 2 - bounds.y1 * view.k;
    applyView();
  }

  function centerOn(id) {
    const box = layouts.get(id);
    if (!box) return;
    const size = canvasSize();
    view.x = size.width / 2 - (box.x + box.w / 2) * view.k;
    view.y = size.height / 2 - (box.y + box.h / 2) * view.k;
    applyView();
  }

  function visibleCenter() {
    const size = canvasSize();
    return toWorld(size.left + size.width / 2, size.top + size.height / 2);
  }

  // ---- History & mutations --------------------------------------------------------------------

  function pushHistory(key) {
    const now = Date.now();
    if (key && key === history.lastKey && now - history.lastTime < 1500) {
      history.lastTime = now;
      return;
    }
    history.undo.push(JSON.stringify(doc));
    if (history.undo.length > HISTORY_LIMIT) history.undo.shift();
    history.redo = [];
    history.lastKey = key;
    history.lastTime = now;
  }

  function mutate(key, change) {
    pushHistory(key);
    change();
    afterChange();
  }

  function afterChange() {
    markDirty();
    render();
    updateInspector();
  }

  function restore(stackFrom, stackTo) {
    if (!stackFrom.length) return;
    stackTo.push(JSON.stringify(doc));
    doc = JSON.parse(stackFrom.pop());
    history.lastKey = null;
    selection = new Set([...selection].filter((id) => nodeType(id)));
    afterChange();
  }

  function undo() {
    restore(history.undo, history.redo);
  }

  function redo() {
    restore(history.redo, history.undo);
  }

  function uniqueName(base) {
    const names = new Set(doc.classes.map((item) => item.name));
    if (!names.has(base)) return base;
    let index = 2;
    while (names.has(`${base}${index}`)) index += 1;
    return `${base}${index}`;
  }

  // Walks right, then down, from the preferred spot until a new ~180×120 element would not overlap anything.
  function freeSpot(startX, startY) {
    computeLayouts();
    const margin = 30;
    const overlaps = (x, y) =>
      [...layouts.values()].some(
        (box) =>
          x < box.x + box.w + margin &&
          x + 180 > box.x - margin &&
          y < box.y + box.h + margin &&
          y + 120 > box.y - margin,
      );
    for (let row = 0; row < 8; row += 1) {
      for (let column = 0; column < 8; column += 1) {
        const x = startX + column * 220;
        const y = startY + row * 160;
        if (!overlaps(x, y)) return { x, y };
      }
    }
    return { x: startX, y: startY };
  }

  function addElement(kind, position) {
    if (kind !== "note" && doc.classes.length >= 300) return showHint("Es sind höchstens 300 Klassen möglich.");
    if (kind === "note" && doc.notes.length >= 200) return showHint("Es sind höchstens 200 Notizen möglich.");
    const center = position || visibleCenter();
    const { x, y } = position
      ? { x: snap(center.x - 80, true), y: snap(center.y - 30, true) }
      : freeSpot(snap(center.x - 80, true), snap(center.y - 50, true));
    let id;
    mutate(null, () => {
      if (kind === "note") {
        id = format.newId("n");
        doc.notes.push({ id, text: "Notiz", x, y });
      } else {
        id = format.newId("c");
        doc.classes.push({
          id,
          kind,
          name: uniqueName(DEFAULT_NAMES[kind]),
          stereotype: "",
          x,
          y,
          color: "default",
          attributes: [],
          methods: [],
          literals: kind === "enum" ? ["WERT_A", "WERT_B"] : [],
        });
      }
      selection = new Set([id]);
    });
    const nameField = kind === "note" ? fields.text : fields.name;
    nameField.focus();
    nameField.select();
  }

  function createRelation(sourceId, targetId) {
    const sourceType = nodeType(sourceId);
    const targetType = nodeType(targetId);
    if (!sourceType || !targetType || sourceType === "relation" || targetType === "relation") return;
    let kind = relationKindSelect.value;
    if (sourceType === "note" || targetType === "note") kind = "note_link";
    else if (kind === "note_link") kind = "association";
    if (
      kind === "inheritance" &&
      findClass(targetId)?.kind === "interface" &&
      findClass(sourceId)?.kind !== "interface"
    ) {
      kind = "realization";
    }
    if (!format.relationAllowed(kind, sourceType, targetType, sourceId === targetId)) {
      showHint(
        sourceType === "note" && targetType === "note"
          ? "Zwei Notizen lassen sich nicht verbinden."
          : "Eine Klasse kann nicht von sich selbst erben.",
      );
      return;
    }
    if (doc.relations.length >= 600) return showHint("Es sind höchstens 600 Beziehungen möglich.");
    const id = format.newId("r");
    mutate(null, () => {
      doc.relations.push({
        id,
        kind,
        source: sourceId,
        target: targetId,
        label: "",
        source_multiplicity: "",
        target_multiplicity: "",
      });
      selection = new Set([id]);
    });
  }

  function deleteSelection() {
    if (!selection.size) return;
    mutate(null, () => {
      doc.classes = doc.classes.filter((item) => !selection.has(item.id));
      doc.notes = doc.notes.filter((item) => !selection.has(item.id));
      const nodeIds = new Set([...doc.classes, ...doc.notes].map((item) => item.id));
      doc.relations = doc.relations.filter(
        (item) => !selection.has(item.id) && nodeIds.has(item.source) && nodeIds.has(item.target),
      );
      selection = new Set();
    });
  }

  function duplicateSelection() {
    const nodes = [...doc.classes, ...doc.notes].filter((item) => selection.has(item.id));
    if (!nodes.length) return;
    const mapping = new Map();
    mutate(null, () => {
      nodes.forEach((item) => {
        const copy = JSON.parse(JSON.stringify(item));
        copy.id = format.newId(findClass(item.id) ? "c" : "n");
        copy.x += 40;
        copy.y += 40;
        if (findClass(item.id)) {
          copy.name = uniqueName(item.name);
          doc.classes.push(copy);
        } else {
          doc.notes.push(copy);
        }
        mapping.set(item.id, copy.id);
      });
      doc.relations
        .filter((item) => mapping.has(item.source) && mapping.has(item.target))
        .forEach((item) => {
          doc.relations.push({
            ...item,
            id: format.newId("r"),
            source: mapping.get(item.source),
            target: mapping.get(item.target),
          });
        });
      selection = new Set(mapping.values());
    });
  }

  function nudgeSelection(dx, dy) {
    const nodes = [...doc.classes, ...doc.notes].filter((item) => selection.has(item.id));
    if (!nodes.length) return;
    mutate("nudge", () => {
      nodes.forEach((item) => {
        item.x += dx;
        item.y += dy;
      });
    });
  }

  function setConnectMode(active) {
    connectMode = active;
    connectSource = null;
    connectButton.setAttribute("aria-pressed", String(active));
    connectButton.classList.toggle("is-active", active);
    svg.classList.toggle("is-connecting", active);
    updateConnectHint();
    render();
  }

  // ---- Pointer interaction --------------------------------------------------------------------

  function elementAt(clientX, clientY) {
    const target = document.elementFromPoint(clientX, clientY);
    return target?.closest?.("[data-id]") || null;
  }

  function onPointerDown(event) {
    if (event.button === 1) {
      event.preventDefault();
      interaction = { type: "pan", startX: event.clientX, startY: event.clientY, originX: view.x, originY: view.y };
      svg.setPointerCapture(event.pointerId);
      return;
    }
    if (event.button !== 0) return;
    svg.focus({ preventScroll: true });
    exportMenu.open = false;
    const world = toWorld(event.clientX, event.clientY);

    const handle = event.target.closest("[data-handle]");
    if (handle) {
      interaction = {
        type: "link",
        sourceId: handle.dataset.handle,
        current: world,
        startX: event.clientX,
        startY: event.clientY,
      };
      svg.setPointerCapture(event.pointerId);
      return;
    }

    const element = event.target.closest("[data-id]");
    if (connectMode) {
      if (!element || element.dataset.type === "relation") {
        connectSource = null;
      } else if (!connectSource) {
        connectSource = element.dataset.id;
      } else {
        const sourceId = connectSource;
        connectSource = null;
        createRelation(sourceId, element.dataset.id);
      }
      updateConnectHint();
      render();
      return;
    }

    if (element) {
      const id = element.dataset.id;
      if (event.shiftKey) {
        if (selection.has(id)) selection.delete(id);
        else selection.add(id);
      } else if (!selection.has(id)) {
        selection = new Set([id]);
      }
      if (element.dataset.type !== "relation" && selection.has(id)) {
        const origins = new Map();
        [...doc.classes, ...doc.notes]
          .filter((item) => selection.has(item.id))
          .forEach((item) => origins.set(item.id, { x: item.x, y: item.y }));
        interaction = { type: "drag", start: world, origins, moved: false };
      }
      render();
      updateInspector();
    } else if (event.shiftKey) {
      interaction = { type: "marquee", start: world, current: world };
    } else {
      if (selection.size) {
        selection = new Set();
        render();
        updateInspector();
      }
      interaction = { type: "pan", startX: event.clientX, startY: event.clientY, originX: view.x, originY: view.y };
      svg.classList.add("is-panning");
    }
    svg.setPointerCapture(event.pointerId);
  }

  function onPointerMove(event) {
    if (!interaction) return;
    if (interaction.type === "pan") {
      view.x = interaction.originX + event.clientX - interaction.startX;
      view.y = interaction.originY + event.clientY - interaction.startY;
      applyView();
      return;
    }
    const world = toWorld(event.clientX, event.clientY);
    if (interaction.type === "drag") {
      const dx = world.x - interaction.start.x;
      const dy = world.y - interaction.start.y;
      if (!interaction.moved && Math.hypot(dx, dy) * view.k < 3) return;
      if (!interaction.moved) {
        pushHistory(null);
        interaction.moved = true;
      }
      interaction.origins.forEach((origin, id) => {
        const item = findNode(id);
        if (!item) return;
        item.x = snap(origin.x + dx, snapEnabled && !event.altKey);
        item.y = snap(origin.y + dy, snapEnabled && !event.altKey);
      });
      render();
      return;
    }
    interaction.current = world;
    overlayGroup.replaceChildren();
    if (interaction.type === "marquee") {
      const { start, current } = interaction;
      overlayGroup.append(
        svgEl("rect", {
          x: Math.min(start.x, current.x),
          y: Math.min(start.y, current.y),
          width: Math.abs(current.x - start.x),
          height: Math.abs(current.y - start.y),
          fill: "rgba(179, 119, 47, 0.08)",
          stroke: SELECT_COLOR,
          "stroke-dasharray": "4 3",
        }),
      );
    } else if (interaction.type === "link") {
      const box = layouts.get(interaction.sourceId);
      if (!box) return;
      const center = boxCenter(box);
      overlayGroup.append(
        svgEl("line", {
          x1: center.x,
          y1: center.y,
          x2: world.x,
          y2: world.y,
          stroke: SELECT_COLOR,
          "stroke-width": 1.6,
          "stroke-dasharray": "6 4",
        }),
      );
    }
  }

  function onPointerUp(event) {
    if (!interaction) return;
    const current = interaction;
    interaction = null;
    svg.classList.remove("is-panning");
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);

    if (current.type === "drag" && current.moved) {
      history.lastKey = null;
      afterChange();
    } else if (current.type === "marquee") {
      const x1 = Math.min(current.start.x, current.current.x);
      const y1 = Math.min(current.start.y, current.current.y);
      const x2 = Math.max(current.start.x, current.current.x);
      const y2 = Math.max(current.start.y, current.current.y);
      layouts.forEach((box, id) => {
        if (box.x < x2 && box.x + box.w > x1 && box.y < y2 && box.y + box.h > y1) selection.add(id);
      });
      render();
      updateInspector();
    } else if (current.type === "link") {
      const target = elementAt(event.clientX, event.clientY);
      overlayGroup.replaceChildren();
      const travelled = Math.hypot(event.clientX - current.startX, event.clientY - current.startY);
      // A plain click on the handle lands on its own class; only a real drag back onto it makes a self-relation.
      const isSelfClick = target?.dataset.id === current.sourceId && travelled < 40;
      if (target && target.dataset.type !== "relation" && !isSelfClick)
        createRelation(current.sourceId, target.dataset.id);
      else render();
    }
  }

  svg.addEventListener("pointerdown", onPointerDown);
  svg.addEventListener("pointermove", onPointerMove);
  svg.addEventListener("pointerup", onPointerUp);
  svg.addEventListener("pointercancel", onPointerUp);
  svg.addEventListener("dblclick", (event) => {
    if (event.target.closest("[data-handle]")) return;
    const element = event.target.closest("[data-id]");
    if (!element) {
      addElement("class", toWorld(event.clientX, event.clientY));
      return;
    }
    const type = element.dataset.type;
    const field = type === "class" ? fields.name : type === "note" ? fields.text : fields.label;
    field.focus();
    field.select?.();
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

  // ---- Inspector ------------------------------------------------------------------------------

  Object.entries(PALETTE).forEach(([key, palette]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "uml-color-swatch";
    button.dataset.color = key;
    button.style.setProperty("--swatch", palette.header);
    button.style.setProperty("--swatch-border", palette.stroke);
    button.setAttribute("aria-label", palette.label);
    button.title = palette.label;
    colorOptions.append(button);
  });

  function setFieldValue(field, value) {
    if (document.activeElement !== field && field.value !== value) field.value = value;
  }

  function showPanel(name) {
    Object.entries(panels).forEach(([key, panel]) => (panel.hidden = key !== name));
  }

  function renderOutline() {
    const items = [
      ...doc.classes.map((item) => ({ id: item.id, label: item.name || "Unbenannt", meta: KIND_LABELS[item.kind] })),
      ...doc.notes.map((item) => ({ id: item.id, label: item.text.split("\n")[0] || "Notiz", meta: "Notiz" })),
    ];
    outline.replaceChildren(
      ...items.map((item) => {
        const entry = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.outlineId = item.id;
        const label = document.createElement("span");
        label.textContent = item.label;
        const meta = document.createElement("small");
        meta.textContent = item.meta;
        button.append(label, meta);
        entry.append(button);
        return entry;
      }),
    );
    if (!items.length) {
      const empty = document.createElement("li");
      empty.className = "uml-muted";
      empty.textContent = "Noch keine Elemente.";
      outline.append(empty);
    }
  }

  function updateInspector() {
    if (selection.size > 1) {
      showPanel("multi");
      root.querySelector("[data-uml-multi-heading]").textContent = `${selection.size} Elemente ausgewählt`;
      return;
    }
    const [id] = selection;
    const type = id ? nodeType(id) : null;
    if (type === "class") {
      const item = findClass(id);
      showPanel("class");
      root.querySelector("[data-uml-class-heading]").textContent = KIND_LABELS[item.kind];
      setFieldValue(fields.name, item.name);
      setFieldValue(fields.kind, item.kind);
      setFieldValue(fields.stereotype, item.stereotype);
      setFieldValue(fields.literals, item.literals.join("\n"));
      setFieldValue(fields.attributes, item.attributes.map(format.formatAttribute).join("\n"));
      setFieldValue(fields.methods, item.methods.map(format.formatMethod).join("\n"));
      root.querySelector("[data-uml-literals-field]").hidden = item.kind !== "enum";
      colorOptions.querySelectorAll("[data-color]").forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.color === item.color));
      });
    } else if (type === "relation") {
      const item = findRelation(id);
      showPanel("relation");
      const sourceName = findClass(item.source)?.name || "Notiz";
      const targetName = findClass(item.target)?.name || "Notiz";
      root.querySelector("[data-uml-relation-summary]").textContent = `${sourceName} → ${targetName}`;
      root.querySelector("[data-uml-relation-help]").textContent = RELATION_HELP[item.kind];
      setFieldValue(fields["relation-kind"], item.kind);
      setFieldValue(fields.label, item.label);
      setFieldValue(fields.source_multiplicity, item.source_multiplicity);
      setFieldValue(fields.target_multiplicity, item.target_multiplicity);
    } else if (type === "note") {
      showPanel("note");
      setFieldValue(fields.text, findNote(id).text);
    } else {
      showPanel("empty");
      renderOutline();
    }
  }

  function selectedOf(finder) {
    if (selection.size !== 1) return null;
    return finder([...selection][0]);
  }

  function bindText(field, finder, apply) {
    field.addEventListener("input", () => {
      const item = selectedOf(finder);
      if (!item) return;
      mutate(`${field.dataset.umlField}:${item.id}`, () => apply(item, field.value));
    });
  }

  bindText(fields.name, findClass, (item, value) => (item.name = value.slice(0, format.LIMITS.name)));
  bindText(fields.stereotype, findClass, (item, value) => {
    item.stereotype = value.replace(/[«»<>]/g, "").slice(0, format.LIMITS.stereotype);
  });
  bindText(fields.literals, findClass, (item, value) => {
    item.literals = value
      .split("\n")
      .map((line) => line.trim().slice(0, format.LIMITS.name))
      .filter(Boolean)
      .slice(0, format.LIMITS.members);
  });
  [
    [fields.attributes, format.parseAttribute, "attributes"],
    [fields.methods, format.parseMethod, "methods"],
  ].forEach(([field, parser, key]) => {
    bindText(field, findClass, (item, value) => {
      item[key] = format.parseLines(value, parser);
      const lineCount = value.split("\n").filter((line) => line.trim()).length;
      memberError.hidden = lineCount <= format.LIMITS.members;
      memberError.textContent = `Es werden nur die ersten ${format.LIMITS.members} Zeilen übernommen.`;
    });
  });
  bindText(fields.text, findNote, (item, value) => (item.text = value.slice(0, format.LIMITS.note)));
  bindText(fields.label, findRelation, (item, value) => (item.label = value.slice(0, format.LIMITS.label)));
  bindText(fields.source_multiplicity, findRelation, (item, value) => {
    item.source_multiplicity = value.slice(0, format.LIMITS.multiplicity);
  });
  bindText(fields.target_multiplicity, findRelation, (item, value) => {
    item.target_multiplicity = value.slice(0, format.LIMITS.multiplicity);
  });

  fields.kind.addEventListener("change", () => {
    const item = selectedOf(findClass);
    if (!item) return;
    mutate(null, () => {
      item.kind = fields.kind.value;
      if (item.kind === "enum" && !item.literals.length) item.literals = ["WERT_A", "WERT_B"];
    });
  });

  fields["relation-kind"].addEventListener("change", () => {
    const item = selectedOf(findRelation);
    if (!item) return;
    const kind = fields["relation-kind"].value;
    const allowed = format.relationAllowed(
      kind,
      nodeType(item.source),
      nodeType(item.target),
      item.source === item.target,
    );
    if (!allowed) {
      fields["relation-kind"].value = item.kind;
      showHint(
        kind === "note_link"
          ? "Notizverbindungen verbinden nur Notizen mit Elementen."
          : "Diese Beziehungsart passt nicht zu den verbundenen Elementen.",
      );
      return;
    }
    mutate(null, () => (item.kind = kind));
  });

  colorOptions.addEventListener("click", (event) => {
    const button = event.target.closest("[data-color]");
    const item = selectedOf(findClass);
    if (!button || !item) return;
    mutate(null, () => (item.color = button.dataset.color));
  });

  outline.addEventListener("click", (event) => {
    const button = event.target.closest("[data-outline-id]");
    if (!button) return;
    selection = new Set([button.dataset.outlineId]);
    render();
    centerOn(button.dataset.outlineId);
    updateInspector();
  });

  // Commit a finished text edit as its own undo step.
  root.querySelector(".uml-inspector").addEventListener("focusout", () => {
    history.lastKey = null;
    updateInspector();
  });

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

  function documentForSave() {
    const copy = format.normalizeDocument(doc);
    copy.classes.forEach((item) => {
      if (!item.name.trim()) item.name = "Unbenannt";
    });
    return copy;
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
        body: JSON.stringify({ title, document: documentForSave() }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || `Fehler ${response.status}`);
      if (!saveState.dirty) setStatus("Gespeichert", "saved");
      document.title = `${title} | UML | Lunora`;
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

  function buildExportSvg() {
    computeLayouts();
    const content = renderContent(false);
    const probe = svgEl("svg", { width: 10, height: 10, style: "position:absolute;left:-9999px;top:0" }, [
      svgEl("defs", {}, markerDefs(LINE_COLOR, "")),
      content,
    ]);
    document.body.append(probe);
    const box = content.getBBox();
    probe.remove();
    const margin = 24;
    const width = Math.ceil(box.width + margin * 2) || 200;
    const height = Math.ceil(box.height + margin * 2) || 120;
    const exportSvg = svgEl(
      "svg",
      {
        xmlns: SVG_NS,
        width,
        height,
        viewBox: `${Math.floor(box.x - margin)} ${Math.floor(box.y - margin)} ${width} ${height}`,
      },
      [
        svgEl("defs", {}, markerDefs(LINE_COLOR, "")),
        svgEl("rect", {
          x: Math.floor(box.x - margin),
          y: Math.floor(box.y - margin),
          width,
          height,
          fill: "#ffffff",
        }),
        content,
      ],
    );
    return { markup: new XMLSerializer().serializeToString(exportSvg), width, height };
  }

  function exportPng() {
    const { markup, width, height } = buildExportSvg();
    const image = new Image();
    const url = URL.createObjectURL(new Blob([markup], { type: "image/svg+xml" }));
    image.onload = () => {
      const scale = 2;
      const canvas = document.createElement("canvas");
      canvas.width = width * scale;
      canvas.height = height * scale;
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

  let dialogFile = null;
  function showTextDialog(heading, content, filename, mime) {
    root.querySelector("[data-uml-dialog-title]").textContent = heading;
    root.querySelector("[data-uml-dialog-text]").value = content;
    dialogFile = { filename, mime, content };
    dialog.showModal();
  }

  root.querySelector("[data-uml-dialog-copy]").addEventListener("click", async () => {
    const area = root.querySelector("[data-uml-dialog-text]");
    try {
      await navigator.clipboard.writeText(area.value);
      showHint("In die Zwischenablage kopiert.");
    } catch (_error) {
      area.select();
      showHint("Text markiert – mit Strg+C kopieren.");
    }
  });
  root.querySelector("[data-uml-dialog-download]").addEventListener("click", () => {
    if (dialogFile) download(dialogFile.filename, new Blob([dialogFile.content], { type: dialogFile.mime }));
  });

  function runExport(kind) {
    exportMenu.open = false;
    const base = slug(titleInput.value);
    const clean = documentForSave();
    if (kind === "png") exportPng();
    else if (kind === "svg") download(`${base}.svg`, new Blob([buildExportSvg().markup], { type: "image/svg+xml" }));
    else if (kind === "plantuml") {
      showTextDialog("PlantUML", format.toPlantUml(clean, titleInput.value.trim()), `${base}.puml`, "text/plain");
    } else if (kind === "java") {
      if (!clean.classes.length) return showHint("Füge zuerst mindestens eine Klasse hinzu.");
      showTextDialog("Java-Code", format.toJava(clean), `${base}-java.txt`, "text/plain");
    } else if (kind === "json") {
      const data = { format: "lunora-uml", title: titleInput.value.trim(), document: clean };
      download(`${base}.json`, new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    } else if (kind === "import") {
      importInput.value = "";
      importInput.click();
    }
  }

  root.querySelectorAll("[data-uml-export]").forEach((button) => {
    button.addEventListener("click", () => runExport(button.dataset.umlExport));
  });

  importInput.addEventListener("change", async () => {
    const [file] = importInput.files;
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      const imported = format.normalizeDocument(data?.document ?? data);
      if (!window.confirm("Das aktuelle Diagramm durch den Import ersetzen? (Rückgängig ist möglich.)")) return;
      mutate(null, () => {
        doc = imported;
        selection = new Set();
      });
      fitView();
      showHint(`${imported.classes.length} Klasse(n) importiert.`);
    } catch (_error) {
      showHint("Die Datei ist kein gültiges Diagramm (JSON).");
    }
  });

  // ---- Toolbar & keyboard ---------------------------------------------------------------------

  root.querySelectorAll("[data-uml-add]").forEach((button) => {
    button.addEventListener("click", () => addElement(button.dataset.umlAdd));
  });

  function toggleGrid() {
    snapEnabled = !snapEnabled;
    gridButton.setAttribute("aria-pressed", String(snapEnabled));
    gridButton.classList.toggle("is-active", snapEnabled);
    render();
  }

  const ACTIONS = {
    save,
    undo,
    redo,
    duplicate: duplicateSelection,
    delete: deleteSelection,
    connect: () => setConnectMode(!connectMode),
    "zoom-in": () => zoomAt(1.2),
    "zoom-out": () => zoomAt(1 / 1.2),
    "zoom-reset": () => zoomAt(1 / view.k),
    fit: fitView,
    grid: toggleGrid,
    swap: () => {
      const item = selectedOf(findRelation);
      if (!item) return;
      mutate(null, () => {
        [item.source, item.target] = [item.target, item.source];
        [item.source_multiplicity, item.target_multiplicity] = [item.target_multiplicity, item.source_multiplicity];
      });
    },
  };

  root.querySelectorAll("[data-uml-action]").forEach((button) => {
    button.addEventListener("click", () => ACTIONS[button.dataset.umlAction]?.());
  });
  relationKindSelect.addEventListener("change", updateConnectHint);
  gridButton.classList.add("is-active");

  document.addEventListener("keydown", (event) => {
    const modifier = event.ctrlKey || event.metaKey;
    const key = event.key.toLowerCase();
    if (modifier && key === "s") {
      event.preventDefault();
      saveState.dirty = true;
      save();
      return;
    }
    if (dialog.open || isTyping(event.target)) return;

    if (modifier && key === "z") {
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
    } else if (modifier && key === "y") {
      event.preventDefault();
      redo();
    } else if (modifier && key === "d") {
      event.preventDefault();
      duplicateSelection();
    } else if (modifier && key === "a") {
      event.preventDefault();
      selection = new Set([...doc.classes, ...doc.notes, ...doc.relations].map((item) => item.id));
      render();
      updateInspector();
    } else if (modifier) {
      return;
    } else if (key === "delete" || key === "backspace") {
      event.preventDefault();
      deleteSelection();
    } else if (key === "escape") {
      if (connectMode) setConnectMode(false);
      else if (selection.size) {
        selection = new Set();
        render();
        updateInspector();
      }
    } else if (key === "c") {
      setConnectMode(!connectMode);
    } else if (key === "f") {
      fitView();
    } else if (key === "g") {
      toggleGrid();
    } else if (key === "+" || key === "=") {
      zoomAt(1.2);
    } else if (key === "-") {
      zoomAt(1 / 1.2);
    } else if (key === "0") {
      zoomAt(1 / view.k);
    } else if (key.startsWith("arrow") && selection.size) {
      event.preventDefault();
      const step = event.shiftKey ? 50 : snapEnabled ? SNAP : 1;
      const dx = key === "arrowleft" ? -step : key === "arrowright" ? step : 0;
      const dy = key === "arrowup" ? -step : key === "arrowdown" ? step : 0;
      nudgeSelection(dx, dy);
    }
  });

  document.addEventListener("click", (event) => {
    if (exportMenu.open && !exportMenu.contains(event.target)) exportMenu.open = false;
  });

  render();
  updateInspector();
  fitView();
})();
