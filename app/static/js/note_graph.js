// Note-link graph (note_graph.html): a small hand-written force layout drawn as SVG. Nodes are real
// links, so Tab walks through the notes and Enter opens one; dragging a node only rearranges it.
(() => {
  const dataElement = document.getElementById("note-graph-data");
  const container = document.querySelector("[data-note-graph]");
  if (!dataElement || !container) return;

  const SVG_NS = "http://www.w3.org/2000/svg";
  const ISOLATED_STORAGE_KEY = "lunora-note-graph-isolated";
  const LINK_DISTANCE = 80;
  const REPULSION = 400;
  const SPRING = 0.08;
  const GRAVITY = 0.02;
  const DAMPING = 0.82;

  const data = JSON.parse(dataElement.textContent);
  const isolatedToggle = document.querySelector("[data-graph-isolated]");
  const emptyHint = container.querySelector("[data-graph-empty]");

  const svgElement = (name, attributes = {}) => {
    const element = document.createElementNS(SVG_NS, name);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
    return element;
  };
  const truncate = (text, length) => (text.length > length ? `${text.slice(0, length - 1)}…` : text);

  const svg = svgElement("svg", {
    class: "note-graph-svg",
    role: "group",
    "aria-label": "Notizen und ihre Verknüpfungen",
  });
  const edgeLayer = svgElement("g", { class: "note-graph-edges" });
  const nodeLayer = svgElement("g", { class: "note-graph-nodes" });
  svg.append(edgeLayer, nodeLayer);
  container.prepend(svg);

  const nodesById = new Map();
  data.nodes.forEach((node, index) => {
    // Golden-angle spiral: a deterministic start without overlapping nodes.
    const radius = 14 * Math.sqrt(index + 0.5);
    const angle = index * 2.399963;
    Object.assign(node, {
      x: radius * Math.cos(angle),
      y: radius * Math.sin(angle),
      vx: 0,
      vy: 0,
      fixed: false,
      size: 6 + Math.min(14, Math.sqrt(node.degree) * 3),
      neighbors: new Set(),
    });
    nodesById.set(node.id, node);
  });
  const edges = data.edges.map(([sourceId, targetId]) => {
    const source = nodesById.get(sourceId);
    const target = nodesById.get(targetId);
    source.neighbors.add(target);
    target.neighbors.add(source);
    return { source, target };
  });

  let activeNodes = [];
  let activeEdges = [];
  let alpha = 1;
  let frame = 0;
  let autoFit = true;
  let view = { x: -200, y: -150, w: 400, h: 300 };
  let suppressClick = false;

  const applyView = () => svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);

  const fitView = () => {
    if (!activeNodes.length) return;
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const node of activeNodes) {
      minX = Math.min(minX, node.x - node.size);
      maxX = Math.max(maxX, node.x + node.size);
      minY = Math.min(minY, node.y - node.size);
      maxY = Math.max(maxY, node.y + node.size + 18);
    }
    const padding = 40;
    view = {
      x: minX - padding,
      y: minY - padding,
      w: Math.max(240, maxX - minX + padding * 2),
      h: Math.max(180, maxY - minY + padding * 2),
    };
    applyView();
  };

  const zoomAt = (factor, centerX = view.x + view.w / 2, centerY = view.y + view.h / 2) => {
    autoFit = false;
    const width = Math.min(20000, Math.max(80, view.w * factor));
    const scale = width / view.w;
    view = {
      x: centerX - (centerX - view.x) * scale,
      y: centerY - (centerY - view.y) * scale,
      w: width,
      h: view.h * scale,
    };
    applyView();
  };

  const toSvgPoint = (event) => {
    const matrix = svg.getScreenCTM();
    if (!matrix) return { x: 0, y: 0 };
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    return { x: point.x, y: point.y };
  };

  const draw = () => {
    for (const edge of activeEdges) {
      edge.element.setAttribute("x1", edge.source.x.toFixed(1));
      edge.element.setAttribute("y1", edge.source.y.toFixed(1));
      edge.element.setAttribute("x2", edge.target.x.toFixed(1));
      edge.element.setAttribute("y2", edge.target.y.toFixed(1));
    }
    for (const node of activeNodes) {
      node.element.setAttribute("transform", `translate(${node.x.toFixed(1)} ${node.y.toFixed(1)})`);
    }
  };

  // ponytail: O(n²) repulsion per frame; the server caps the graph at MAX_GRAPH_NOTES. Switch to a
  // quadtree (Barnes–Hut) if that cap ever has to rise.
  const tick = () => {
    const count = activeNodes.length;
    for (let i = 0; i < count; i += 1) {
      const a = activeNodes[i];
      for (let j = i + 1; j < count; j += 1) {
        const b = activeNodes[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let distanceSquared = dx * dx + dy * dy;
        if (distanceSquared < 0.01) {
          dx = Math.random() - 0.5;
          dy = Math.random() - 0.5;
          distanceSquared = 0.5;
        }
        const force = (REPULSION * alpha) / distanceSquared;
        a.vx += dx * force;
        a.vy += dy * force;
        b.vx -= dx * force;
        b.vy -= dy * force;
      }
    }
    for (const { source, target } of activeEdges) {
      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const distance = Math.hypot(dx, dy) || 1;
      const force = ((distance - LINK_DISTANCE) * SPRING * alpha) / distance;
      source.vx += dx * force;
      source.vy += dy * force;
      target.vx -= dx * force;
      target.vy -= dy * force;
    }
    for (const node of activeNodes) {
      if (node.fixed) {
        node.vx = 0;
        node.vy = 0;
        continue;
      }
      node.vx = (node.vx - node.x * GRAVITY * alpha) * DAMPING;
      node.vy = (node.vy - node.y * GRAVITY * alpha) * DAMPING;
      node.x += node.vx;
      node.y += node.vy;
    }
    alpha *= 0.985;
  };

  const step = () => {
    tick();
    draw();
    if (autoFit) fitView();
    frame = alpha > 0.01 ? requestAnimationFrame(step) : 0;
  };

  const reheat = (value) => {
    alpha = Math.max(alpha, value);
    if (!frame) frame = requestAnimationFrame(step);
  };

  const setHighlight = (node) => {
    svg.classList.toggle("is-highlighting", Boolean(node));
    for (const other of activeNodes) {
      other.element.classList.toggle("is-active", Boolean(node) && (other === node || node.neighbors.has(other)));
    }
    for (const edge of activeEdges) {
      edge.element.classList.toggle("is-active", Boolean(node) && (edge.source === node || edge.target === node));
    }
  };

  const buildNode = (node) => {
    const classes = ["note-graph-node"];
    if (node.color) classes.push(`tone-${node.color}`);
    if (node.archived) classes.push("is-archived");
    const link = svgElement("a", { href: node.url, class: classes.join(" ") });
    const title = svgElement("title");
    const connections = node.degree === 1 ? "1 Verknüpfung" : `${node.degree} Verknüpfungen`;
    title.textContent = `${node.title} – ${connections}${node.archived ? " (archiviert)" : ""}`;
    const label = svgElement("text", { y: node.size + 14 });
    label.textContent = truncate(node.title, 32);
    link.append(title, svgElement("circle", { r: node.size }), label);

    link.addEventListener("pointerenter", () => setHighlight(node));
    link.addEventListener("pointerleave", () => setHighlight(null));
    link.addEventListener("focus", () => setHighlight(node));
    link.addEventListener("blur", () => setHighlight(null));
    link.addEventListener("click", (event) => {
      if (suppressClick) event.preventDefault();
      suppressClick = false;
    });
    link.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.stopPropagation();
      link.setPointerCapture(event.pointerId);
      const start = { x: event.clientX, y: event.clientY };
      let moved = false;
      node.fixed = true;
      const move = (moveEvent) => {
        if (!moved && Math.hypot(moveEvent.clientX - start.x, moveEvent.clientY - start.y) < 4) return;
        moved = true;
        autoFit = false;
        const point = toSvgPoint(moveEvent);
        node.x = point.x;
        node.y = point.y;
        reheat(0.3);
        draw();
      };
      const end = () => {
        node.fixed = false;
        suppressClick = moved;
        link.removeEventListener("pointermove", move);
        link.removeEventListener("pointerup", end);
        link.removeEventListener("pointercancel", end);
      };
      link.addEventListener("pointermove", move);
      link.addEventListener("pointerup", end);
      link.addEventListener("pointercancel", end);
    });
    node.element = link;
  };

  const render = () => {
    const showIsolated = Boolean(isolatedToggle?.checked);
    activeNodes = data.nodes.filter((node) => showIsolated || node.degree > 0);
    activeEdges = edges;
    edgeLayer.replaceChildren();
    nodeLayer.replaceChildren();
    for (const edge of activeEdges) {
      edge.element ??= svgElement("line");
      edgeLayer.append(edge.element);
    }
    for (const node of activeNodes) {
      if (!node.element) buildNode(node);
      nodeLayer.append(node.element);
    }
    const empty = activeNodes.length === 0;
    svg.toggleAttribute("hidden", empty);
    if (emptyHint) emptyHint.hidden = !empty;
    if (empty) return;
    autoFit = true;
    draw();
    fitView();
    alpha = 0;
    reheat(1);
  };

  // Background drag pans the view; the wheel zooms around the pointer.
  svg.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    svg.setPointerCapture(event.pointerId);
    svg.classList.add("is-panning");
    let last = toSvgPoint(event);
    const move = (moveEvent) => {
      autoFit = false;
      const point = toSvgPoint(moveEvent);
      view.x -= point.x - last.x;
      view.y -= point.y - last.y;
      applyView();
      last = toSvgPoint(moveEvent);
    };
    const end = () => {
      svg.classList.remove("is-panning");
      svg.removeEventListener("pointermove", move);
      svg.removeEventListener("pointerup", end);
      svg.removeEventListener("pointercancel", end);
    };
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerup", end);
    svg.addEventListener("pointercancel", end);
  });

  svg.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const point = toSvgPoint(event);
      zoomAt(event.deltaY > 0 ? 1.15 : 1 / 1.15, point.x, point.y);
    },
    { passive: false },
  );

  document.querySelectorAll("[data-graph-zoom]").forEach((button) => {
    button.addEventListener("click", () => zoomAt(button.dataset.graphZoom === "in" ? 1 / 1.3 : 1.3));
  });
  document.querySelector("[data-graph-fit]")?.addEventListener("click", () => {
    autoFit = true;
    fitView();
  });

  if (isolatedToggle) {
    try {
      isolatedToggle.checked = localStorage.getItem(ISOLATED_STORAGE_KEY) === "1";
    } catch {
      // Storage can be unavailable; the toggle then starts off.
    }
    isolatedToggle.addEventListener("change", () => {
      try {
        localStorage.setItem(ISOLATED_STORAGE_KEY, isolatedToggle.checked ? "1" : "0");
      } catch {
        // Not remembered then.
      }
      render();
    });
  }

  render();
})();
