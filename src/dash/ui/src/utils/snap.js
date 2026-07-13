// Snap-to-edge helper for the Style Builder's drag/resize canvas. All units
// are pixels (canvas-space), not fractions -- callers convert back to
// fractions after snapping.

function bestMatch(value, targets, threshold) {
  let best = null;
  for (const t of targets) {
    const dist = Math.abs(value - t);
    if (dist <= threshold && (!best || dist < best.dist)) best = { target: t, dist };
  }
  return best;
}

// Given the original position (left or top) and its three derived candidate
// coordinates for one axis (start/center/end), each already matched against
// the target list: pick the closest match to actually move the box by,
// expressed as a single delta (shifting start/center/end together, since
// they're all just `position + constant`) -- then re-check every candidate
// (including the ones that lost) at that same delta. One that still lands on
// its own matched target within threshold means that alignment coincidentally
// also holds true (e.g. this box is exactly the same width as the thing it
// just left-aligned to, so it's now also right-aligned), and gets its own
// guide line too instead of being dropped.
function resolveAxis(originalPos, candidateValues, targets, threshold) {
  const active = candidateValues
    .map((value) => ({ value, match: bestMatch(value, targets, threshold) }))
    .filter((c) => c.match);
  if (!active.length) return { pos: originalPos, guides: [] };

  const winner = active.reduce((a, b) => (a.match.dist <= b.match.dist ? a : b));
  const delta = winner.match.target - winner.value;
  const finalPos = originalPos + delta;

  const guides = [];
  for (const c of active) {
    if (Math.abs(c.value + delta - c.match.target) <= threshold) {
      guides.push(c.match.target);
    }
  }
  return { pos: finalPos, guides };
}

// rect: {x, y, w, h}. targetsX/targetsY: arrays of candidate pixel values to
// snap to (already filtered by whatever modes are enabled -- this function
// doesn't know about canvas/elements/subdivisions, just matches values).
// Returns a possibly-adjusted rect plus the list of active guide lines
// ({axis: 'x'|'y', pos}) to render -- more than one per axis when multiple
// alignments coincide (see resolveAxis).
//
// Checks left/center/right (x-axis) and top/center/bottom (y-axis)
// independently against the given targets, snapping to the closest match
// within threshold.
export function snapRect(rect, targetsX, targetsY, threshold = 8) {
  const { x, y, w, h } = rect;
  const guides = [];

  const xResult = resolveAxis(x, [x, x + w / 2, x + w], targetsX, threshold);
  for (const pos of xResult.guides) guides.push({ axis: 'x', pos });

  const yResult = resolveAxis(y, [y, y + h / 2, y + h], targetsY, threshold);
  for (const pos of yResult.guides) guides.push({ axis: 'y', pos });

  return { x: xResult.pos, y: yResult.pos, w, h, guides };
}

// Assembles the candidate snap-target lists from whichever modes are
// enabled. `others`: array of {x,y,w,h} (every other element, already in
// px). `subdivisions`: array of {axis, pos} (row/grid internal division
// lines, as 0..1 *fractions* -- converted to px here using canvasW/canvasH,
// same as every other fractional value in this app) -- gated behind
// `snapToElements` since they're a property of elements, same as their own
// edges.
export function buildSnapTargets({ canvasW, canvasH, others, subdivisions, snapToCanvas, snapToElements }) {
  const targetsX = [];
  const targetsY = [];
  if (snapToCanvas) {
    targetsX.push(0, canvasW / 2, canvasW);
    targetsY.push(0, canvasH / 2, canvasH);
  }
  if (snapToElements) {
    for (const o of others) {
      targetsX.push(o.x, o.x + o.w / 2, o.x + o.w);
      targetsY.push(o.y, o.y + o.h / 2, o.y + o.h);
    }
    for (const s of subdivisions ?? []) {
      if (s.axis === 'x') targetsX.push(s.pos * canvasW);
      else targetsY.push(s.pos * canvasH);
    }
  }
  return { targetsX, targetsY };
}
