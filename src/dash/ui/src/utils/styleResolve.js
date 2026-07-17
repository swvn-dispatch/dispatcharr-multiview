// Mirrors _resolve_elements in src/layouts.py -- keep both in sync. This
// copy exists only so the Style Builder's live preview doesn't need a
// network round-trip per drag; it's pure geometry math with no
// channel-selection logic, so drift risk is low, but any change to the
// split/assignment rule must be made in both places.

// Preview canvas is always treated as 1920x1080 for pixel/aspect-ratio math
// (matches the "of 1920"/"of 1080" element-size inputs elsewhere in the
// Style Builder) -- only the aspect ratio matters here, not real output
// resolution, so a fixed 16:9 pair is fine for the live preview.
const PREVIEW_OUT_W = 1920;
const PREVIEW_OUT_H = 1080;

export function splitRow(el, count, outW = PREVIEW_OUT_W, outH = PREVIEW_OUT_H) {
  const { x, y, w, h, valign = 'center', halign = 'center' } = el;
  const vertical = el.direction === 'vertical';
  const rowWPx = w * outW;
  const rowHPx = h * outH;
  const pieces = [];
  if (vertical) {
    const naturalPx = Math.min(rowHPx / count, (rowWPx * 9) / 16);
    const totalPx = naturalPx * count;
    const availPx = rowHPx;
    const offsetPx = valign === 'top' ? 0 : valign === 'bottom' ? availPx - totalPx : (availPx - totalPx) / 2;
    const pieceH = naturalPx / outH;
    for (let i = 0; i < count; i++) {
      const pieceValign = count === 1 ? 'center' : i === 0 ? 'bottom' : i === count - 1 ? 'top' : 'center';
      const pieceY = y + (offsetPx + i * naturalPx) / outH;
      pieces.push([x, pieceY, w, pieceH, pieceValign, halign]);
    }
  } else {
    const naturalPx = Math.min(rowWPx / count, (rowHPx * 16) / 9);
    const totalPx = naturalPx * count;
    const availPx = rowWPx;
    const offsetPx = halign === 'left' ? 0 : halign === 'right' ? availPx - totalPx : (availPx - totalPx) / 2;
    const pieceW = naturalPx / outW;
    for (let i = 0; i < count; i++) {
      const pieceHalign = count === 1 ? 'center' : i === 0 ? 'right' : i === count - 1 ? 'left' : 'center';
      const pieceX = x + (offsetPx + i * naturalPx) / outW;
      pieces.push([pieceX, y, pieceW, h, valign, pieceHalign]);
    }
  }
  return pieces;
}

// Same cols/rows formula as _split_grid in src/layouts.py, scoped to this
// element's own rect. All pieces use the grid element's own single
// valign/halign uniformly (deliberate simplification, matches row's behavior).
export function splitGrid(el, count) {
  const { x, y, w, h, valign = 'center', halign = 'center' } = el;
  const cols = Math.ceil(Math.sqrt(count));
  const rows = Math.ceil(count / cols);
  const tileW = w / cols;
  const tileH = h / rows;

  const lastRowCount = count % cols || cols;
  const emptyCells = cols - lastRowCount;
  const offsetX = emptyCells > 0 ? (emptyCells * tileW) / 2 : 0;

  const pieces = [];
  for (let i = 0; i < count; i++) {
    const c = i % cols;
    const r = Math.floor(i / cols);
    const isLast = r === rows - 1 && emptyCells > 0;
    const px = x + c * tileW + (isLast ? offsetX : 0);
    const py = y + r * tileH;
    pieces.push([px, py, tileW, tileH, valign, halign]);
  }
  return pieces;
}

// Mirrors _distribute_dynamic_counts in src/layouts.py -- max-min fair
// distribution of `remaining` across dynamic elements, respecting each
// element's optional `max` cap (null/undefined = unlimited). Degrades to a
// plain even split (remainder to the earliest elements) when no caps are set.
export function distributeDynamicCounts(remaining, dynamics) {
  const counts = new Array(dynamics.length).fill(0);
  const caps = dynamics.map((d) => (d.max != null ? d.max : null));
  const active = new Set(dynamics.map((_, i) => i));
  let left = remaining;
  while (left > 0 && active.size > 0) {
    const share = Math.floor(left / active.size);
    if (share === 0) {
      for (const idx of [...active].sort((a, b) => a - b).slice(0, left)) counts[idx] += 1;
      break;
    }
    let progressed = false;
    for (const idx of [...active]) {
      const cap = caps[idx];
      const room = cap != null ? cap - counts[idx] : null;
      const give = room != null ? Math.min(share, room) : share;
      if (give > 0) {
        counts[idx] += give;
        left -= give;
        progressed = true;
      }
      if (cap != null && counts[idx] >= cap) active.delete(idx);
    }
    if (!progressed) break;
  }
  return counts;
}

// Returns an array of [x, y, w, h, valign, halign] fractions, or null if the
// elements can't cover n channels (no dynamic elements and n exceeds the
// static count, or the dynamic elements' caps can't absorb the remainder).
export function resolveElements(elements, n, outW = PREVIEW_OUT_W, outH = PREVIEW_OUT_H) {
  const statics = elements.filter((e) => e.type === 'static');
  const dynamics = elements.filter((e) => e.type === 'row' || e.type === 'grid');
  const remaining = Math.max(0, n - statics.length);
  if (remaining > 0 && dynamics.length === 0) return null;

  const counts = distributeDynamicCounts(remaining, dynamics);

  const result = [];
  let channelIdx = 0;
  let dynI = 0;
  for (const el of elements) {
    if (channelIdx >= n) break;
    if (el.type === 'static') {
      result.push([el.x, el.y, el.w, el.h, el.valign ?? 'center', el.halign ?? 'center']);
      channelIdx += 1;
    } else if (el.type === 'row' || el.type === 'grid') {
      const count = counts[dynI];
      dynI += 1;
      if (count <= 0) continue;
      result.push(...(el.type === 'row' ? splitRow(el, count, outW, outH) : splitGrid(el, count)));
      channelIdx += count;
    }
  }

  if (channelIdx < n) return null;
  return result.slice(0, n);
}
