// Pure helpers of the whiteboard editor (no DOM), exposed as a global so vitest can load them.
// LIMITS, COLORS and WIDTHS mirror app/services/whiteboard_content.py; change both sides together.
(function () {
  const COLORS = ["default", "blue", "green", "yellow", "red", "violet", "gray"];
  const WIDTHS = [2, 4, 8];
  const LIMITS = { elements: 3000, strokePoints: 2000, totalPoints: 150000, text: 500, coordinate: 100000 };

  function coordinate(value) {
    const clamped = Math.min(LIMITS.coordinate, Math.max(-LIMITS.coordinate, Number(value) || 0));
    return Math.round(clamped * 10) / 10;
  }

  function emptyDocument() {
    return { version: 1, elements: [] };
  }

  function normalizeDocument(value) {
    return { version: 1, elements: Array.isArray(value?.elements) ? value.elements : [] };
  }

  function pointCount(doc) {
    return doc.elements.reduce((sum, item) => sum + (item.type === "stroke" ? item.points.length / 2 : 0), 0);
  }

  // Squared distance from (px, py) to the segment (ax, ay)-(bx, by).
  function segmentDistanceSquared(px, py, ax, ay, bx, by) {
    const dx = bx - ax;
    const dy = by - ay;
    const length = dx * dx + dy * dy;
    const t = length ? Math.min(1, Math.max(0, ((px - ax) * dx + (py - ay) * dy) / length)) : 0;
    const x = ax + t * dx - px;
    const y = ay + t * dy - py;
    return x * x + y * y;
  }

  // Ramer-Douglas-Peucker on a flat [x1, y1, x2, y2, ...] list, iterative so long strokes can't overflow the stack.
  function reduce(points, tolerance) {
    const count = points.length / 2;
    if (count <= 2) return points.slice();
    const keep = new Uint8Array(count);
    keep[0] = keep[count - 1] = 1;
    const limit = tolerance * tolerance;
    const stack = [[0, count - 1]];
    while (stack.length) {
      const [first, last] = stack.pop();
      let worst = 0;
      let index = -1;
      for (let i = first + 1; i < last; i += 1) {
        const distance = segmentDistanceSquared(
          points[i * 2],
          points[i * 2 + 1],
          points[first * 2],
          points[first * 2 + 1],
          points[last * 2],
          points[last * 2 + 1],
        );
        if (distance > worst) {
          worst = distance;
          index = i;
        }
      }
      if (index !== -1 && worst > limit) {
        keep[index] = 1;
        stack.push([first, index], [index, last]);
      }
    }
    const result = [];
    for (let i = 0; i < count; i += 1) if (keep[i]) result.push(points[i * 2], points[i * 2 + 1]);
    return result;
  }

  // Drops points closer than `distance` to the last kept one; the ends always stay.
  function thin(points, distance) {
    const count = points.length / 2;
    if (count <= 2) return points.slice();
    const limit = distance * distance;
    const result = [points[0], points[1]];
    for (let i = 1; i < count - 1; i += 1) {
      const dx = points[i * 2] - result[result.length - 2];
      const dy = points[i * 2 + 1] - result[result.length - 1];
      if (dx * dx + dy * dy >= limit) result.push(points[i * 2], points[i * 2 + 1]);
    }
    result.push(points[count * 2 - 2], points[count * 2 - 1]);
    return result;
  }

  // Evenly picks at most `limit` points, ends included.
  function cap(points, limit) {
    const count = points.length / 2;
    if (count <= limit) return points;
    const result = [];
    for (let i = 0; i < limit; i += 1) {
      const index = Math.round((i * (count - 1)) / (limit - 1));
      result.push(points[index * 2], points[index * 2 + 1]);
    }
    return result;
  }

  // Thins a raw pointer trail and rounds it for saving. The caps bound the work: reduce() is quadratic
  // in the worst case, so it never sees more than twice the stroke limit.
  function simplifyStroke(points, tolerance) {
    const input = cap(thin(points, tolerance), LIMITS.strokePoints * 2);
    return cap(reduce(input, tolerance), LIMITS.strokePoints).map(coordinate);
  }

  // Smooth path through the points: quadratic curves between segment midpoints, a single point is a dot.
  function strokePath(points) {
    const count = points.length / 2;
    if (!count) return "";
    let d = `M${points[0]} ${points[1]}`;
    if (count === 1) return `${d}L${points[0]} ${points[1]}`;
    for (let i = 1; i < count - 1; i += 1) {
      const x = points[i * 2];
      const y = points[i * 2 + 1];
      d += `Q${x} ${y} ${(x + points[i * 2 + 2]) / 2} ${(y + points[i * 2 + 3]) / 2}`;
    }
    return `${d}L${points[count * 2 - 2]} ${points[count * 2 - 1]}`;
  }

  function cross(ax, ay, bx, by, cx, cy) {
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
  }

  // Squared distance between the segments a-b and c-d (0 when they cross).
  function segmentsDistanceSquared(ax, ay, bx, by, cx, cy, dx, dy) {
    const crosses =
      cross(ax, ay, bx, by, cx, cy) * cross(ax, ay, bx, by, dx, dy) < 0 &&
      cross(cx, cy, dx, dy, ax, ay) * cross(cx, cy, dx, dy, bx, by) < 0;
    if (crosses) return 0;
    return Math.min(
      segmentDistanceSquared(ax, ay, cx, cy, dx, dy),
      segmentDistanceSquared(bx, by, cx, cy, dx, dy),
      segmentDistanceSquared(cx, cy, ax, ay, bx, by),
      segmentDistanceSquared(dx, dy, ax, ay, bx, by),
    );
  }

  // Whether the stroke comes within `tolerance` of the eraser's path from (ax, ay) to (bx, by). Testing
  // the swept segment instead of single positions keeps a fast swipe from jumping over thin strokes.
  function hitsStroke(points, ax, ay, bx, by, tolerance) {
    const limit = tolerance * tolerance;
    const last = Math.max(points.length / 2 - 1, 1);
    for (let i = 0; i < last; i += 1) {
      const next = Math.min(i * 2 + 2, points.length - 2);
      const distance = segmentsDistanceSquared(
        ax,
        ay,
        bx,
        by,
        points[i * 2],
        points[i * 2 + 1],
        points[next],
        points[next + 1],
      );
      if (distance <= limit) return true;
    }
    return false;
  }

  // Cuts the part of a stroke within `radius` of the eraser's path (ax, ay)-(bx, by) out of it and
  // returns what is left as point lists, one per remaining piece (none when all of it is gone).
  function eraseFromStroke(points, ax, ay, bx, by, radius) {
    const count = points.length / 2;
    let length = 0;
    for (let i = 1; i < count; i += 1) {
      length += Math.hypot(points[i * 2] - points[i * 2 - 2], points[i * 2 + 1] - points[i * 2 - 1]);
    }
    // Saved strokes have long straight segments, so sample them finely enough to cut inside one;
    // the second term bounds the work on very long strokes.
    const step = Math.max(radius / 2, length / 20000, 0.01);
    const limit = radius * radius;
    const pieces = [];
    let run = [];
    const visit = (x, y) => {
      if (segmentDistanceSquared(x, y, ax, ay, bx, by) > limit) {
        run.push(x, y);
        return;
      }
      if (run.length >= 4) pieces.push(run);
      run = [];
    };
    visit(points[0], points[1]);
    for (let i = 1; i < count; i += 1) {
      const x0 = points[i * 2 - 2];
      const y0 = points[i * 2 - 1];
      const dx = points[i * 2] - x0;
      const dy = points[i * 2 + 1] - y0;
      const parts = Math.max(1, Math.ceil(Math.hypot(dx, dy) / step));
      for (let j = 1; j <= parts; j += 1) visit(x0 + (dx * j) / parts, y0 + (dy * j) / parts);
    }
    if (run.length >= 4) pieces.push(run);
    // reduce() drops the sampling points again, leaving the original corners plus the cut ends.
    return pieces.map((piece) => cap(reduce(piece, 0.05), LIMITS.strokePoints).map(coordinate));
  }

  // Text sizes in board units per allowed size key, and the line metrics the SVG <text> and the edit
  // field share so the text doesn't jump when it is opened for editing.
  const TEXT_SIZES = { s: 16, m: 24, l: 40 };
  const TEXT_LINE_HEIGHT = 1.25;
  const TEXT_ASCENT = 1.05;
  const ELLIPSE_SEGMENTS = 36;

  // Fallback width when no canvas is around to measure (tests, fit before fonts load).
  function estimateTextWidth(text, size) {
    return text.length * size * 0.55;
  }

  function pointsBounds(points) {
    const bounds = { x1: Infinity, y1: Infinity, x2: -Infinity, y2: -Infinity };
    for (let i = 0; i < points.length; i += 2) {
      bounds.x1 = Math.min(bounds.x1, points[i]);
      bounds.y1 = Math.min(bounds.y1, points[i + 1]);
      bounds.x2 = Math.max(bounds.x2, points[i]);
      bounds.y2 = Math.max(bounds.y2, points[i + 1]);
    }
    return bounds;
  }

  function elementBounds(item, measure = estimateTextWidth) {
    if (item.type === "stroke") return pointsBounds(item.points);
    if (item.type === "line" || item.type === "arrow") return pointsBounds([item.x1, item.y1, item.x2, item.y2]);
    if (item.type === "text") {
      const size = TEXT_SIZES[item.size];
      const lines = item.text.split("\n");
      const width = Math.max(...lines.map((line) => measure(line, size)));
      return { x1: item.x, y1: item.y, x2: item.x + width, y2: item.y + lines.length * size * TEXT_LINE_HEIGHT };
    }
    return { x1: item.x, y1: item.y, x2: item.x + item.w, y2: item.y + item.h };
  }

  // Bounding box of everything on the board, or null for an empty board.
  function documentBounds(doc, measure) {
    if (!doc.elements.length) return null;
    return doc.elements.reduce(
      (bounds, item) => {
        const box = elementBounds(item, measure);
        return {
          x1: Math.min(bounds.x1, box.x1),
          y1: Math.min(bounds.y1, box.y1),
          x2: Math.max(bounds.x2, box.x2),
          y2: Math.max(bounds.y2, box.y2),
        };
      },
      { x1: Infinity, y1: Infinity, x2: -Infinity, y2: -Infinity },
    );
  }

  // The drawn line of an element as a flat polyline; closed for rectangles, ellipses and text boxes.
  function outline(item, measure) {
    if (item.type === "stroke") return item.points;
    if (item.type === "line" || item.type === "arrow") return [item.x1, item.y1, item.x2, item.y2];
    if (item.type === "ellipse") {
      const rx = item.w / 2;
      const ry = item.h / 2;
      const points = [];
      for (let i = 0; i <= ELLIPSE_SEGMENTS; i += 1) {
        const angle = (i / ELLIPSE_SEGMENTS) * 2 * Math.PI;
        points.push(item.x + rx + rx * Math.cos(angle), item.y + ry + ry * Math.sin(angle));
      }
      return points;
    }
    const box = elementBounds(item, measure);
    return [box.x1, box.y1, box.x2, box.y1, box.x2, box.y2, box.x1, box.y2, box.x1, box.y1];
  }

  function contains(item, x, y, tolerance, measure) {
    if (item.type === "ellipse") {
      const dx = (x - item.x - item.w / 2) / (item.w / 2 + tolerance);
      const dy = (y - item.y - item.h / 2) / (item.h / 2 + tolerance);
      return dx * dx + dy * dy <= 1;
    }
    const box = elementBounds(item, measure);
    return x >= box.x1 - tolerance && x <= box.x2 + tolerance && y >= box.y1 - tolerance && y <= box.y2 + tolerance;
  }

  // Whether a pointer moving from (ax, ay) to (bx, by) touches the element: its drawn line for outlines,
  // anywhere inside for text and filled shapes. A click passes the same point twice.
  function hitsElement(item, ax, ay, bx, by, tolerance, measure) {
    const solid = item.type === "text" || ((item.type === "rect" || item.type === "ellipse") && item.fill);
    if (solid && contains(item, bx, by, tolerance, measure)) return true;
    const reach = item.type === "text" ? tolerance : tolerance + item.width / 2;
    return hitsStroke(outline(item, measure), ax, ay, bx, by, reach);
  }

  // A copy shifted by (dx, dy) and rounded the way the server stores it.
  function moveElement(item, dx, dy) {
    const moved = { ...item };
    if (item.type === "stroke") {
      moved.points = item.points.map((value, index) => coordinate(value + (index % 2 ? dy : dx)));
    } else if (item.type === "line" || item.type === "arrow") {
      moved.x1 = coordinate(item.x1 + dx);
      moved.y1 = coordinate(item.y1 + dy);
      moved.x2 = coordinate(item.x2 + dx);
      moved.y2 = coordinate(item.y2 + dy);
    } else {
      moved.x = coordinate(item.x + dx);
      moved.y = coordinate(item.y + dy);
      if ("w" in item) {
        moved.w = coordinate(item.w);
        moved.h = coordinate(item.h);
      }
    }
    return moved;
  }

  // The box spanned by a drag from (x1, y1) to (x2, y2) in any direction; `square` keeps the longer side.
  function normalizeBox(x1, y1, x2, y2, square) {
    let w = x2 - x1;
    let h = y2 - y1;
    if (square) {
      const side = Math.max(Math.abs(w), Math.abs(h));
      w = Math.sign(w || 1) * side;
      h = Math.sign(h || 1) * side;
    }
    return { x: Math.min(x1, x1 + w), y: Math.min(y1, y1 + h), w: Math.abs(w), h: Math.abs(h) };
  }

  // The two barbs of an arrow tip at (x2, y2), sized by the stroke width but never longer than the arrow.
  function arrowHead(x1, y1, x2, y2, width) {
    const angle = Math.atan2(y2 - y1, x2 - x1);
    const length = Math.min(Math.max(10, width * 3.5), Math.hypot(x2 - x1, y2 - y1) * 0.6 || 10);
    const spread = Math.PI / 7;
    const barb = (side) => {
      const x = x2 - length * Math.cos(angle + side * spread);
      const y = y2 - length * Math.sin(angle + side * spread);
      return `${Math.round(x * 10) / 10} ${Math.round(y * 10) / 10}`;
    };
    return `M${barb(-1)}L${x2} ${y2}L${barb(1)}`;
  }

  globalThis.LunoraWhiteboardFormat = {
    COLORS,
    WIDTHS,
    LIMITS,
    TEXT_SIZES,
    TEXT_LINE_HEIGHT,
    TEXT_ASCENT,
    coordinate,
    emptyDocument,
    normalizeDocument,
    pointCount,
    simplifyStroke,
    strokePath,
    hitsStroke,
    estimateTextWidth,
    elementBounds,
    documentBounds,
    hitsElement,
    containsPoint: contains,
    eraseFromStroke,
    moveElement,
    normalizeBox,
    arrowHead,
  };
})();
