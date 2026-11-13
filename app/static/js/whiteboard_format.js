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

  // Bounding box of all strokes, or null for an empty board.
  function documentBounds(doc) {
    let bounds = null;
    doc.elements.forEach((item) => {
      if (item.type !== "stroke") return;
      for (let i = 0; i < item.points.length; i += 2) {
        const x = item.points[i];
        const y = item.points[i + 1];
        if (!bounds) bounds = { x1: x, y1: y, x2: x, y2: y };
        bounds.x1 = Math.min(bounds.x1, x);
        bounds.y1 = Math.min(bounds.y1, y);
        bounds.x2 = Math.max(bounds.x2, x);
        bounds.y2 = Math.max(bounds.y2, y);
      }
    });
    return bounds;
  }

  globalThis.LunoraWhiteboardFormat = {
    COLORS,
    WIDTHS,
    LIMITS,
    emptyDocument,
    normalizeDocument,
    pointCount,
    simplifyStroke,
    strokePath,
    hitsStroke,
    documentBounds,
  };
})();
