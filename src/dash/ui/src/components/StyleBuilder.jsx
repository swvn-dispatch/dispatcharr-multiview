import { useState, useEffect, useRef } from 'react';
import { Stack, Group, Text, Button, TextInput, NumberInput, Select, ActionIcon, SegmentedControl, Switch, SimpleGrid, Table, Collapse } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconTrash, IconPlus, IconArrowUp, IconArrowDown, IconCopy, IconPhoto, IconX, IconDownload, IconUpload, IconChevronDown, IconChevronUp, IconDeviceFloppy } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { Rnd } from 'react-rnd';
import { patchConfig, previewStyle, uploadStyleBackground, fetchStyleBackgroundBlob } from '../api.js';
import { genId } from '../utils/id.js';
import { resolveElements, splitRow, distributeDynamicCounts } from '../utils/styleResolve.js';
import { snapRect, buildSnapTargets } from '../utils/snap.js';
import { downloadJson } from '../utils/download.js';
import { blobToBase64 } from '../utils/file.js';
import { ALIGN_VALUES, validateStyleEntry } from '../utils/validate.js';

const ELEMENT_LABELS = { static: 'Static', row: 'Auto Row', grid: 'Auto Grid' };
const ELEMENT_STYLES = {
  static: { border: '1px solid var(--mantine-color-blue-5)', background: 'rgba(51, 154, 240, 0.15)' },
  row: { border: '2px dashed var(--mantine-color-teal-5)', background: 'rgba(18, 184, 134, 0.12)' },
  grid: { border: '2px dotted var(--mantine-color-orange-5)', background: 'rgba(253, 126, 20, 0.12)' },
};

// react-rnd/re-resizable renders each resize handle as an already-sized,
// already-positioned hit-area div (generous default touch targets); this
// style only adds a visible fill/border on top -- deliberately not setting
// width/height/position, so the existing hit area is untouched and only
// its visibility changes. Corners only (edges keep their normal invisible
// hit area -- a corner dot is enough of an affordance without covering the
// whole box outline), and only on mobile: shown only on the selected
// element, and only where there's no hover to reveal an invisible handle
// in the first place -- on desktop the existing yellow selection outline
// is affordance enough, and a mouse can find the edge/corner hit areas via
// hover/cursor changes anyway.
const RESIZE_HANDLE_CORNER = { background: 'var(--mantine-color-yellow-5)', border: '1px solid rgba(0, 0, 0, 0.4)', borderRadius: '50%' };
const RESIZE_HANDLE_STYLES_MOBILE = {
  topLeft: RESIZE_HANDLE_CORNER,
  topRight: RESIZE_HANDLE_CORNER,
  bottomLeft: RESIZE_HANDLE_CORNER,
  bottomRight: RESIZE_HANDLE_CORNER,
};

const BUILTINS = [
  { value: 'auto', label: 'Auto Grid' },
  { value: 'featured', label: 'Featured' },
  { value: 'top_featured', label: 'Top Featured' },
];

// Element-based reconstructions of each built-in, for the read-only
// preview -- real element combinations that behave like the algorithm
// they're standing in for (not a channel-count-frozen snapshot), so the
// preview canvas actually adapts to the preview channel count via the
// same resolveElements pipeline a real custom style uses:
// - "auto": a single whole-canvas grid element. _split_grid uses the exact
//   same cols/rows/centering formula as the built-in's own _auto_grid_rects
//   (see layouts.py), so this is a mathematically exact reproduction, not
//   an approximation.
// - "featured"/"top_featured": a fixed static main tile plus a row element
//   absorbing the rest, matching the built-ins' documented "~60/40 split,
//   remainder stacked in a row" structure. The real algorithms compute the
//   side/bottom size dynamically per channel count (capped at 60/40); a
//   static split can't reproduce that exactly, but it's the same shape a
//   user would build by hand to approximate it.
const BUILTIN_ELEMENT_TEMPLATES = {
  auto: [
    { id: 'b-grid', type: 'grid', x: 0, y: 0, w: 1, h: 1, valign: 'center', halign: 'center' },
  ],
  featured: [
    { id: 'b-main', type: 'static', x: 0, y: 0, w: 0.6, h: 1, valign: 'center', halign: 'center' },
    { id: 'b-side', type: 'row', direction: 'vertical', x: 0.6, y: 0, w: 0.4, h: 1, valign: 'center', halign: 'center' },
  ],
  top_featured: [
    { id: 'b-main', type: 'static', x: 0, y: 0, w: 1, h: 0.6, valign: 'center', halign: 'center' },
    { id: 'b-bottom', type: 'row', direction: 'horizontal', x: 0, y: 0.6, w: 1, h: 0.4, valign: 'center', halign: 'center' },
  ],
};

// Only the fractions matter for a preview -- a single representative channel
// count is enough to show the general shape of a built-in style.
const PREVIEW_CHANNEL_COUNT = 4;
const ALIGN_OPTIONS = ALIGN_VALUES.map((v) => ({ value: v, label: v }));

// Internal division lines for every row/grid element at the given preview
// channel count -- reuses splitRow/splitGrid directly (not a separate
// reimplementation) so these guides always exactly match how the element
// actually resolves. Returned as 0..1 *fractions* (same space every element
// already lives in) -- pixel conversion happens wherever the canvas's
// current measured size is known: ElementCanvas's rendering and snap.js's
// buildSnapTargets. {axis, pos, from, to}, `from`/`to` bounding the line's
// visual extent to the owning element's own rect.
function computeSubdivisionGuides(elements, n) {
  const statics = elements.filter((e) => e.type === 'static');
  const dynamics = elements.filter((e) => e.type === 'row' || e.type === 'grid');
  const remaining = Math.max(0, n - statics.length);
  const counts = distributeDynamicCounts(remaining, dynamics);

  const guides = [];
  dynamics.forEach((el, i) => {
    const count = counts[i];
    if (count <= 1) return;
    const x0 = el.x, y0 = el.y;
    const x1 = el.x + el.w, y1 = el.y + el.h;

    if (el.type === 'grid') {
      // Clean, uniform column/row dividers computed directly (same
      // cols/rows formula splitGrid uses internally) -- NOT derived from
      // splitGrid's flattened piece list, since the last (partial) row's
      // cells are horizontally centered/offset and would otherwise produce
      // extra divider lines that don't align with the full rows above.
      const cols = Math.ceil(Math.sqrt(count));
      const rows = Math.ceil(count / cols);
      for (let c = 1; c < cols; c++) guides.push({ axis: 'x', pos: x0 + (el.w * c) / cols, from: y0, to: y1 });
      for (let r = 1; r < rows; r++) guides.push({ axis: 'y', pos: y0 + (el.h * r) / rows, from: x0, to: x1 });
      return;
    }

    // Pieces are now naturally-sized and centered as a block (see
    // splitRow), so they no longer necessarily start at the element's own
    // x0/y0 -- a centered or right/bottom-aligned block's leading piece
    // starts partway into the element. Excluding boundaries by comparing
    // against x0/y0 therefore let the block's own leading edge through as
    // a spurious extra guide line; exclude the *pieces'* own leading edge
    // (their minimum start) instead, so only real inter-piece boundaries
    // are drawn.
    const pieces = splitRow(el, count);
    const xs = new Set();
    const ys = new Set();
    for (const [px, py] of pieces) {
      xs.add(px);
      ys.add(py);
    }
    const eps = 1e-6;
    const xsSorted = [...xs].sort((a, b) => a - b);
    const ysSorted = [...ys].sort((a, b) => a - b);
    const xStart = xsSorted[0] ?? x0;
    const yStart = ysSorted[0] ?? y0;
    for (const x of xsSorted) if (x > xStart + eps) guides.push({ axis: 'x', pos: x, from: y0, to: y1 });
    for (const y of ysSorted) if (y > yStart + eps) guides.push({ axis: 'y', pos: y, from: x0, to: x1 });
  });
  return guides;
}

// Measures a live-mounted element's content-box size via ResizeObserver.
// Uses a callback ref (state-backed), not a plain useRef -- a useRef
// object's *identity* never changes across renders, so an effect gated on
// `[ref]` only ever runs once and can end up watching a stale/detached
// node if the underlying DOM element is later unmounted and a new one
// mounted in its place (e.g. a conditionally-rendered container). A
// callback ref re-fires this hook's effect every time React actually
// attaches or detaches a node, so the observer always tracks the current
// live element. Returns [size, setNode]; pass setNode as the target's
// `ref` prop. `size` is null until the first real measurement lands --
// callers must not substitute a guessed size in the meantime, since any
// pixel<->fraction math done against a guess (rather than the real,
// possibly much larger, rendered size) would compute badly wrong fractions.
function useContainerSize() {
  const [node, setNode] = useState(null);
  const [size, setSize] = useState(null);
  useEffect(() => {
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      if (width > 0 && height > 0) setSize({ width: Math.round(width), height: Math.round(height) });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);
  return [size, setNode];
}

// Clamps a fraction to [0, 1] and snaps values within epsilon of 0 or 1 to
// exactly 0/1 -- defends against any residual snapping/rounding drift ever
// persisting a visibly-wrong value (e.g. 1.004 or -0.002).
function clampFraction(v) {
  const eps = 1e-3;
  if (v <= eps) return 0;
  if (v >= 1 - eps) return 1;
  return v;
}

function clampRect({ x, y, w, h }) {
  return { x: clampFraction(x), y: clampFraction(y), w: clampFraction(w), h: clampFraction(h) };
}

// Fits the largest 16:9 box into a measured containerW x containerH space,
// letterboxing whichever dimension isn't the limiting factor. Used instead
// of a CSS aspect-ratio + hardcoded vh maxHeight clamp so the canvas always
// exactly fills whatever room the surrounding flex layout leaves it,
// shrinking as needed with no guessed magic numbers.
function fitCanvasSize(containerW, containerH) {
  const ratio = 16 / 9;
  let width = containerW;
  let height = width / ratio;
  if (height > containerH) {
    height = containerH;
    width = height * ratio;
  }
  return { width: Math.round(width), height: Math.round(height) };
}

function TilePreview({ tiles }) {
  return (
    <div style={{ position: 'relative', width: 160, height: 90, background: 'var(--mantine-color-dark-6)', border: '1px solid var(--mantine-color-dark-4)', flexShrink: 0 }}>
      {(tiles ?? []).map((t, i) => (
        <div
          key={i}
          style={{
            position: 'absolute',
            left: `${t[0] * 100}%`,
            top: `${t[1] * 100}%`,
            width: `${t[2] * 100}%`,
            height: `${t[3] * 100}%`,
            border: '1px solid var(--mantine-color-blue-5)',
            background: 'rgba(51, 154, 240, 0.15)',
            boxSizing: 'border-box',
          }}
        />
      ))}
    </div>
  );
}

function ElementCanvas({ elements, selectedIdx, onSelect, onElementChange, backgroundUrl, previewCountNum, snapToCanvas, snapToElements, canvasW, canvasH, isMobile, readOnly }) {
  const [guides, setGuides] = useState([]); // active drag/resize guides only -- subdivisionGuides are always shown separately
  const [liveOverride, setLiveOverride] = useState(null); // { idx, x, y, w, h } in fractions, live during drag/resize

  // Recomputed from a live-patched elements array (not just the committed
  // `elements` prop) so an Auto Row/Grid's internal division lines follow
  // the box in real time while it's being dragged/resized, not just after
  // it's dropped. This never touches Rnd's own position/size props (that's
  // what caused the earlier jitter bug) -- it only feeds the guide-line
  // computation below.
  const effectiveElements = liveOverride
    ? elements.map((e, i) => (i === liveOverride.idx ? { ...e, x: liveOverride.x, y: liveOverride.y, w: liveOverride.w, h: liveOverride.h } : e))
    : elements;
  const subdivisionGuides = computeSubdivisionGuides(effectiveElements, previewCountNum);

  function px(el) {
    return { x: el.x * canvasW, y: el.y * canvasH, w: el.w * canvasW, h: el.h * canvasH };
  }

  function othersPx(idx) {
    return elements.filter((_, i) => i !== idx).map(px);
  }

  function computeSnap(idx, rect) {
    // Subdivisions recomputed with the dragged element itself excluded --
    // a row/grid's own internal divider lines move together with it (fixed
    // offset relative to its own live position), so they're meaningless
    // (and can be spuriously self-attracting) as snap targets for that
    // same element's own drag/resize. They stay valid targets for every
    // other element, and are still rendered regardless (see
    // `subdivisionGuides` above, unfiltered).
    const othersOnly = computeSubdivisionGuides(effectiveElements.filter((_, i) => i !== idx), previewCountNum);
    const { targetsX, targetsY } = buildSnapTargets({
      canvasW,
      canvasH,
      others: othersPx(idx),
      subdivisions: othersOnly,
      snapToCanvas,
      snapToElements,
    });
    return snapRect(rect, targetsX, targetsY);
  }

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', background: 'var(--mantine-color-dark-6)', border: '1px solid var(--mantine-color-dark-4)' }}>
      {backgroundUrl && (
        <img
          src={backgroundUrl}
          alt=""
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', pointerEvents: 'none' }}
        />
      )}
      {subdivisionGuides.map((g, gi) =>
        g.axis === 'x' ? (
          <div key={`sub-${gi}`} style={{ position: 'absolute', left: g.pos * canvasW, top: g.from * canvasH, width: 1, height: (g.to - g.from) * canvasH, background: 'rgba(255, 255, 255, 0.3)', pointerEvents: 'none' }} />
        ) : (
          <div key={`sub-${gi}`} style={{ position: 'absolute', top: g.pos * canvasH, left: g.from * canvasW, height: 1, width: (g.to - g.from) * canvasW, background: 'rgba(255, 255, 255, 0.3)', pointerEvents: 'none' }} />
        )
      )}
      {elements.map((el, i) => {
        const base = px(el);
        const isSelected = selectedIdx === i;
        return (
          <Rnd
            key={el.id}
            bounds="parent"
            size={{ width: base.w, height: base.h }}
            position={{ x: base.x, y: base.y }}
            minWidth={20}
            minHeight={20}
            lockAspectRatio={el.lockAspect ? (el.w * canvasW) / (el.h * canvasH) : false}
            resizeHandleStyles={isSelected && isMobile && !readOnly ? RESIZE_HANDLE_STYLES_MOBILE : undefined}
            disableDragging={readOnly}
            enableResizing={!readOnly}
            onMouseDown={() => onSelect(i)}
            onDrag={(e, d) => {
              const snapped = computeSnap(i, { x: d.x, y: d.y, w: base.w, h: base.h });
              setGuides(snapped.guides);
              setLiveOverride({ idx: i, x: snapped.x / canvasW, y: snapped.y / canvasH, w: el.w, h: el.h });
            }}
            onDragStop={(e, d) => {
              const snapped = computeSnap(i, { x: d.x, y: d.y, w: base.w, h: base.h });
              setGuides([]);
              setLiveOverride(null);
              onElementChange(i, clampRect({ x: snapped.x / canvasW, y: snapped.y / canvasH, w: el.w, h: el.h }));
            }}
            onResize={(e, dir, ref, delta, pos) => {
              const snapped = computeSnap(i, { x: pos.x, y: pos.y, w: ref.offsetWidth, h: ref.offsetHeight });
              setGuides(snapped.guides);
              setLiveOverride({
                idx: i,
                x: snapped.x / canvasW,
                y: snapped.y / canvasH,
                w: snapped.w / canvasW,
                h: snapped.h / canvasH,
              });
            }}
            onResizeStop={(e, dir, ref, delta, pos) => {
              const snapped = computeSnap(i, { x: pos.x, y: pos.y, w: ref.offsetWidth, h: ref.offsetHeight });
              setGuides([]);
              setLiveOverride(null);
              onElementChange(i, clampRect({
                x: snapped.x / canvasW,
                y: snapped.y / canvasH,
                w: snapped.w / canvasW,
                h: snapped.h / canvasH,
              }));
            }}
            style={{
              ...ELEMENT_STYLES[el.type],
              outline: isSelected ? '2px solid var(--mantine-color-yellow-5)' : 'none',
              outlineOffset: -2,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Text size="xs" c="dimmed" ta="center" style={{ userSelect: 'none', pointerEvents: 'none' }}>
              {el.name || i + 1}
            </Text>
          </Rnd>
        );
      })}
      {guides.map((g, gi) =>
        g.axis === 'x' ? (
          <div key={`live-${gi}`} style={{ position: 'absolute', left: g.pos, top: 0, width: 1, height: canvasH, background: 'var(--mantine-color-red-5)', pointerEvents: 'none' }} />
        ) : (
          <div key={`live-${gi}`} style={{ position: 'absolute', top: g.pos, left: 0, height: 1, width: canvasW, background: 'var(--mantine-color-red-5)', pointerEvents: 'none' }} />
        )
      )}
    </div>
  );
}

function StyleEditor({ style, styleId, onUpdate, readOnly = false }) {
  // Local draft, decoupled from the persisted `style` prop -- every
  // mutation (name, elements, background) edits `draft` only; nothing
  // reaches the server until "Save" is clicked. Resyncs from `style`
  // whenever a *different* style is opened (styleId changes), so switching
  // away without saving cleanly discards any in-progress edits ("open and
  // just not change anything"). For a read-only built-in, `style.elements`
  // is a fixed element-based reconstruction (BUILTIN_ELEMENT_TEMPLATES) --
  // it flows through the exact same draft/resolveElements pipeline as a
  // real style, so it already adapts correctly to preview-count changes
  // with no special-casing needed here.
  const [draft, setDraft] = useState(style);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    setDraft(style);
    setDirty(false);
  }, [styleId]); // eslint-disable-line react-hooks/exhaustive-deps

  const [previewCount, setPreviewCount] = useState(4);
  const [selectedIdx, setSelectedIdx] = useState(null);
  const [backgroundUrl, setBackgroundUrl] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [snapToElements, setSnapToElements] = useState(true);
  const [snapToCanvas, setSnapToCanvas] = useState(true);
  const fileInputRef = useRef(null);
  const [canvasSize, setCanvasNode] = useContainerSize();
  const isMobile = useMediaQuery('(max-width: 48em)');
  const previewCountNum = previewCount || 4;
  const elements = draft.elements ?? [];
  const selected = selectedIdx != null ? elements[selectedIdx] : null;

  useEffect(() => {
    let cancelled = false;
    let objectUrl = null;
    if (draft.background_image) {
      fetchStyleBackgroundBlob(styleId).then((blob) => {
        if (cancelled || !blob) return;
        objectUrl = URL.createObjectURL(blob);
        setBackgroundUrl(objectUrl);
      });
    } else {
      setBackgroundUrl(null);
    }
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [styleId, draft.background_image]);

  function updateElements(next) {
    setDraft((d) => ({ ...d, elements: next }));
    setDirty(true);
  }

  async function handleSave() {
    setSaving(true);
    try {
      await onUpdate(draft);
      setDirty(false);
    } finally {
      setSaving(false);
    }
  }

  async function handleBackgroundFile(e) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setUploading(true);
    try {
      const dataBase64 = await blobToBase64(file);
      const result = await uploadStyleBackground(styleId, file.name, dataBase64);
      setDraft((d) => ({ ...d, background_image: result.filename }));
      setDirty(true);
    } catch (err) {
      notifications.show({ title: 'Upload failed', message: err.message, color: 'red', autoClose: 4000 });
    } finally {
      setUploading(false);
    }
  }

  function handleRemoveBackground() {
    setDraft((d) => ({ ...d, background_image: null }));
    setDirty(true);
  }

  function addElement(type) {
    const base = { id: genId(), type, x: 0.1, y: 0.1, w: 0.3, h: 0.3, valign: 'center', halign: 'center' };
    if (type === 'row') base.direction = 'horizontal';
    const next = [...elements, base];
    updateElements(next);
    setSelectedIdx(next.length - 1);
  }

  function removeElement(idx) {
    updateElements(elements.filter((_, i) => i !== idx));
    if (selectedIdx === idx) setSelectedIdx(null);
    else if (selectedIdx != null && selectedIdx > idx) setSelectedIdx(selectedIdx - 1);
  }

  function duplicateElement(idx) {
    const source = elements[idx];
    const copy = { ...source, id: genId(), x: clampFraction(source.x + 0.02), y: clampFraction(source.y + 0.02) };
    const next = [...elements.slice(0, idx + 1), copy, ...elements.slice(idx + 1)];
    updateElements(next);
    setSelectedIdx(idx + 1);
  }

  function moveElement(idx, delta) {
    const newIdx = idx + delta;
    if (newIdx < 0 || newIdx >= elements.length) return;
    const next = [...elements];
    [next[idx], next[newIdx]] = [next[newIdx], next[idx]];
    updateElements(next);
    if (selectedIdx === idx) setSelectedIdx(newIdx);
    else if (selectedIdx === newIdx) setSelectedIdx(idx);
  }

  function updateElementField(idx, patch) {
    updateElements(elements.map((e, i) => (i === idx ? { ...e, ...patch } : e)));
  }

  return (
    <Stack gap="sm" style={{ flex: 1, minHeight: 0 }}>
      <Group gap="xs" align="flex-end" wrap="wrap">
        <TextInput
          size="xs"
          label="Style name"
          value={draft.name ?? ''}
          onChange={(e) => { setDraft((d) => ({ ...d, name: e.currentTarget.value })); setDirty(true); }}
          placeholder="Style name"
          disabled={readOnly}
          style={{ flex: isMobile ? '1 1 100%' : '1 1 200px' }}
        />
        <NumberInput
          size="xs"
          label="Preview channel count"
          min={2}
          max={9}
          value={previewCount}
          onChange={(v) => setPreviewCount(v || 4)}
          w={isMobile ? '100%' : 150}
        />
        <Switch size="xs" label="Snap to elements" disabled={readOnly} checked={snapToElements} onChange={(e) => setSnapToElements(e.currentTarget.checked)} />
        <Switch size="xs" label="Snap to canvas" disabled={readOnly} checked={snapToCanvas} onChange={(e) => setSnapToCanvas(e.currentTarget.checked)} />
        <Button
          size="xs"
          leftSection={<IconDeviceFloppy size={14} />}
          disabled={readOnly || !dirty}
          loading={saving}
          color={dirty ? 'blue' : 'gray'}
          variant={dirty ? 'filled' : 'default'}
          onClick={handleSave}
          style={isMobile ? { flex: '1 1 100%' } : undefined}
        >
          {dirty ? 'Save' : 'Saved'}
        </Button>
      </Group>

      <SimpleGrid cols={isMobile ? 2 : 4} spacing="xs">
        <Button size="xs" variant="default" leftSection={<IconPlus size={12} />} disabled={readOnly} onClick={() => addElement('static')}>
          Static Position
        </Button>
        <Button size="xs" variant="default" leftSection={<IconPlus size={12} />} disabled={readOnly} onClick={() => addElement('row')}>
          Auto Row
        </Button>
        <Button size="xs" variant="default" leftSection={<IconPlus size={12} />} disabled={readOnly} onClick={() => addElement('grid')}>
          Auto Grid
        </Button>
        <Group gap={4} wrap="nowrap">
          <Button
            size="xs"
            variant="default"
            leftSection={<IconPhoto size={12} />}
            loading={uploading}
            disabled={readOnly}
            onClick={() => fileInputRef.current?.click()}
            style={{ flex: 1 }}
          >
            {draft.background_image ? 'Change Background' : 'Background Image'}
          </Button>
          <ActionIcon size="sm" variant="subtle" color="red" disabled={readOnly || !draft.background_image} onClick={handleRemoveBackground} aria-label="Remove background">
            <IconX size={14} />
          </ActionIcon>
        </Group>
        <input ref={fileInputRef} type="file" accept="image/png,image/jpeg" style={{ display: 'none' }} onChange={handleBackgroundFile} />
      </SimpleGrid>

      <Text size="xs" c="dimmed">
        {readOnly
          ? (elements.length === 0 ? 'This style has no representable elements at this channel count.' : 'Click a box or list row to inspect its properties.')
          : (elements.length === 0 ? 'Add a Static Position, Auto Row, or Auto Grid to get started.' : 'Drag a box to move it, drag its edge/corner to resize it. Click a box or list row to select it.')}
      </Text>
      <Group align="stretch" gap="sm" wrap={isMobile ? 'wrap' : 'nowrap'} style={{ flex: 1, minHeight: 0 }}>
            {/* Properties column -- always rendered with a fixed row set so
                selecting/deselecting/switching element type never changes
                this panel's height (no layout jump elsewhere on the page). */}
            <Stack gap={6} p="sm" w={isMobile ? '100%' : 260} style={{ flexShrink: 0, minHeight: 0, overflowY: 'auto', border: '1px solid var(--mantine-color-dark-4)', borderRadius: 6 }}>
              <Text size="xs" fw={600}>{selected ? (selected.name || ELEMENT_LABELS[selected.type]) : 'No element selected'}</Text>
              <Table withRowBorders={false} verticalSpacing={4} horizontalSpacing="xs" style={{ tableLayout: 'fixed', width: '100%' }}>
                <Table.Tbody>
                  <Table.Tr>
                    <Table.Td w={80}><Text size="xs" c="dimmed">Direction</Text></Table.Td>
                    <Table.Td>
                      <SegmentedControl
                        size="xs"
                        fullWidth
                        disabled={!selected || readOnly || selected.type !== 'row'}
                        data={[{ label: 'Horiz', value: 'horizontal' }, { label: 'Vert', value: 'vertical' }]}
                        value={selected?.direction ?? 'horizontal'}
                        onChange={(v) => selectedIdx != null && updateElementField(selectedIdx, { direction: v })}
                      />
                    </Table.Td>
                  </Table.Tr>
                  <Table.Tr>
                    <Table.Td w={80}><Text size="xs" c="dimmed">Max channels</Text></Table.Td>
                    <Table.Td>
                      <NumberInput
                        size="xs"
                        placeholder="Unlimited"
                        disabled={!selected || readOnly || !['row', 'grid'].includes(selected.type)}
                        min={1}
                        value={selected?.max ?? ''}
                        onChange={(v) => selectedIdx != null && updateElementField(selectedIdx, { max: v === '' ? null : v })}
                      />
                    </Table.Td>
                  </Table.Tr>
                  <Table.Tr>
                    <Table.Td w={80}><Text size="xs" c="dimmed">Anchor</Text></Table.Td>
                    <Table.Td>
                      <Group grow gap={4} wrap="nowrap">
                        <Select
                          size="xs"
                          placeholder="V anchor"
                          disabled={!selected || readOnly}
                          data={ALIGN_OPTIONS}
                          value={selected?.valign ?? null}
                          onChange={(v) => v && selectedIdx != null && updateElementField(selectedIdx, { valign: v })}
                        />
                        <Select
                          size="xs"
                          placeholder="H anchor"
                          disabled={!selected || readOnly}
                          data={ALIGN_OPTIONS}
                          value={selected?.halign ?? null}
                          onChange={(v) => v && selectedIdx != null && updateElementField(selectedIdx, { halign: v })}
                        />
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                  <Table.Tr>
                    <Table.Td w={80}><Text size="xs" c="dimmed">Size</Text></Table.Td>
                    <Table.Td>
                      <Group grow gap={4} wrap="nowrap">
                        <NumberInput
                          size="xs"
                          label="Width (px)"
                          description="of 1920"
                          disabled={!selected || readOnly}
                          min={1}
                          value={selected ? Math.round(selected.w * 1920) : ''}
                          onChange={(v) => {
                            if (!v || selectedIdx == null) return;
                            const w = v / 1920;
                            if (selected.lockAspect) {
                              const ratio = (selected.w * 1920) / (selected.h * 1080);
                              updateElementField(selectedIdx, { w, h: (v / ratio) / 1080 });
                            } else {
                              updateElementField(selectedIdx, { w });
                            }
                          }}
                        />
                        <NumberInput
                          size="xs"
                          label="Height (px)"
                          description="of 1080"
                          disabled={!selected || readOnly}
                          min={1}
                          value={selected ? Math.round(selected.h * 1080) : ''}
                          onChange={(v) => {
                            if (!v || selectedIdx == null) return;
                            const h = v / 1080;
                            if (selected.lockAspect) {
                              const ratio = (selected.w * 1920) / (selected.h * 1080);
                              updateElementField(selectedIdx, { w: (v * ratio) / 1920, h });
                            } else {
                              updateElementField(selectedIdx, { h });
                            }
                          }}
                        />
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                  <Table.Tr>
                    <Table.Td w={80}><Text size="xs" c="dimmed">Lock aspect</Text></Table.Td>
                    <Table.Td>
                      <Switch
                        size="xs"
                        disabled={!selected || readOnly}
                        checked={!!selected?.lockAspect}
                        onChange={(e) => selectedIdx != null && updateElementField(selectedIdx, { lockAspect: e.currentTarget.checked })}
                      />
                    </Table.Td>
                  </Table.Tr>
                </Table.Tbody>
              </Table>
            </Stack>

            {/* Canvas column -- fills whatever space is left, fitted to
                16:9 in JS (fitCanvasSize) rather than a CSS aspect-ratio +
                hardcoded vh clamp, so it shrinks correctly no matter how
                much room the properties/layers columns and surrounding
                chrome leave it. */}
            <div
              ref={setCanvasNode}
              style={
                isMobile
                  ? { width: '100%', aspectRatio: '16 / 9' }
                  : { flex: 1, minWidth: 0, minHeight: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }
              }
            >
              {canvasSize && (() => {
                const fitted = fitCanvasSize(canvasSize.width, canvasSize.height);
                return (
                  <div style={{ width: fitted.width, height: fitted.height }}>
                    <ElementCanvas
                      elements={elements}
                      selectedIdx={selectedIdx}
                      onSelect={setSelectedIdx}
                      onElementChange={updateElementField}
                      backgroundUrl={backgroundUrl}
                      previewCountNum={previewCountNum}
                      snapToCanvas={snapToCanvas}
                      snapToElements={snapToElements}
                      canvasW={fitted.width}
                      canvasH={fitted.height}
                      isMobile={isMobile}
                      readOnly={readOnly}
                    />
                  </div>
                );
              })()}
            </div>

            {/* Layers column -- scrolls internally if it has more rows
                than fit, rather than growing the whole page. */}
            <Stack gap={6} p="sm" w={isMobile ? '100%' : 220} style={{ flexShrink: 0, minHeight: 0, overflowY: 'auto', border: '1px solid var(--mantine-color-dark-4)', borderRadius: 6 }}>
              <Text size="xs" fw={600} c="dimmed">Elements</Text>
              {elements.map((el, i) => (
                <Group
                  key={el.id}
                  gap={4}
                  wrap="nowrap"
                  onClick={() => setSelectedIdx(i)}
                  style={{
                    cursor: 'pointer',
                    padding: 4,
                    borderRadius: 4,
                    background: selectedIdx === i ? 'rgba(51, 154, 240, 0.18)' : 'transparent',
                    borderLeft: selectedIdx === i ? '2px solid var(--mantine-color-blue-5)' : '2px solid transparent',
                  }}
                >
                  <Text size="xs" c="dimmed">{i + 1}.</Text>
                  <TextInput
                    size="xs"
                    variant="unstyled"
                    value={el.name ?? ''}
                    placeholder={ELEMENT_LABELS[el.type]}
                    disabled={readOnly}
                    onChange={(e) => updateElementField(i, { name: e.currentTarget.value })}
                    onClick={(e) => e.stopPropagation()}
                    style={{ flex: 1, minWidth: 0 }}
                    styles={{ input: { minHeight: 'unset', height: 'auto', padding: 0 } }}
                  />
                  <ActionIcon size="xs" variant="subtle" disabled={readOnly} onClick={(e) => { e.stopPropagation(); duplicateElement(i); }} aria-label="Duplicate element">
                    <IconCopy size={12} />
                  </ActionIcon>
                  <ActionIcon size="xs" variant="subtle" disabled={readOnly || i === 0} onClick={(e) => { e.stopPropagation(); moveElement(i, -1); }}>
                    <IconArrowUp size={12} />
                  </ActionIcon>
                  <ActionIcon size="xs" variant="subtle" disabled={readOnly || i === elements.length - 1} onClick={(e) => { e.stopPropagation(); moveElement(i, 1); }}>
                    <IconArrowDown size={12} />
                  </ActionIcon>
                  <ActionIcon size="xs" variant="subtle" color="red" disabled={readOnly} onClick={(e) => { e.stopPropagation(); removeElement(i); }}>
                    <IconTrash size={12} />
                  </ActionIcon>
                </Group>
              ))}
            </Stack>
          </Group>
    </Stack>
  );
}

// Builds a self-contained, shareable export for one custom style -- embeds
// the background image as base64 (if any) so the file works standalone
// rather than referencing a filename that only exists on the source instance.
async function styleToExportObject(id, style) {
  const payload = { name: style.name, elements: style.elements ?? [] };
  if (style.background_image) {
    const blob = await fetchStyleBackgroundBlob(id);
    if (blob) {
      payload.background_image_filename = style.background_image;
      payload.background_image_data = await blobToBase64(blob);
    }
  }
  return payload;
}

// Reverses styleToExportObject: fresh id (never reuse the file's -- avoids
// colliding with an existing style), re-uploads any embedded background to
// get a real server-side file. Returns [id, style] ready to merge into the
// registry.
async function importStyleEntry(entry) {
  const newId = genId();
  const style = { name: entry.name || 'Imported Style', elements: entry.elements ?? [] };
  if (entry.background_image_data) {
    const result = await uploadStyleBackground(newId, entry.background_image_filename || 'background.png', entry.background_image_data);
    style.background_image = result.filename;
  }
  return [newId, style];
}

// Picks an achievable preview channel count for a style: PREVIEW_CHANNEL_COUNT
// if it has a row/grid element (can absorb any remainder), otherwise the
// style can only ever show exactly as many channels as it has static
// elements -- falling back to that (instead of always requesting
// PREVIEW_CHANNEL_COUNT) avoids resolveElements legitimately returning
// nothing for an all-static style with fewer statics than that count.
function previewCountFor(elements) {
  const hasDynamic = elements.some((e) => e.type === 'row' || e.type === 'grid');
  return hasDynamic ? PREVIEW_CHANNEL_COUNT : Math.max(elements.length, 1);
}

// Clickable gallery tile for a custom style -- same visual shape as a
// built-in's TilePreview + label, plus small export/delete actions that
// stopPropagation so they don't also open the editor.
function CustomStyleTile({ id, style, onOpen, onExport, onDelete }) {
  const elements = style.elements ?? [];
  const tiles = resolveElements(elements, previewCountFor(elements)) ?? [];
  return (
    <Stack gap={4} align="center" style={{ cursor: 'pointer', flexShrink: 0 }} onClick={() => onOpen(id)}>
      <TilePreview tiles={tiles} />
      <Text size="xs">{style.name || 'New Style'}</Text>
      <Group gap={4}>
        <ActionIcon size="xs" variant="subtle" onClick={(e) => { e.stopPropagation(); onExport(id, style); }} aria-label="Export style">
          <IconDownload size={12} />
        </ActionIcon>
        <ActionIcon size="xs" variant="subtle" color="red" onClick={(e) => { e.stopPropagation(); onDelete(id); }} aria-label="Delete style">
          <IconTrash size={12} />
        </ActionIcon>
      </Group>
    </Stack>
  );
}

// Trailing tile in the custom styles row -- same footprint as a real tile so
// it scrolls into view like any other, always present as the affordance for
// adding a style (replaces the old standalone "New Style" button).
function NewStyleTile({ onClick }) {
  return (
    <Stack gap={4} align="center" style={{ cursor: 'pointer', flexShrink: 0 }} onClick={onClick}>
      <div
        style={{
          width: 160,
          height: 90,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          border: '1px dashed var(--mantine-color-dark-4)',
          color: 'var(--mantine-color-dimmed)',
        }}
      >
        <IconPlus size={24} />
      </div>
      <Text size="xs" c="dimmed">New Style</Text>
    </Stack>
  );
}

export function StyleBuilder({ settings, onFieldsReload }) {
  const customLayouts = settings.multiview_custom_layouts ?? {};
  // Explicit ordered id list, mirroring multiview_order for layouts -- the
  // dict's own key order isn't a reliable place to record display order
  // (not guaranteed to round-trip through JSON storage), so this is the
  // only source of truth for carousel position. Falls back to the dict's
  // current keys if the backend hasn't migrated it in yet (defensive only;
  // ensure_custom_layout_order always provides it in practice).
  const customOrder = settings.multiview_custom_layouts_order ?? Object.keys(customLayouts);
  const [builtinPreviews, setBuiltinPreviews] = useState({});
  const [openStyleId, setOpenStyleId] = useState(null);
  const [openBuiltin, setOpenBuiltin] = useState(null); // {value, label} from BUILTINS, mutually exclusive with openStyleId
  const [carouselOpen, setCarouselOpen] = useState(true);
  const importStylesRef = useRef(null);
  const isMobile = useMediaQuery('(max-width: 48em)');

  // Collapse the carousel by default the moment a style is opened on
  // mobile, to reclaim vertical space for the editor -- only fires on
  // open/close (not every render), so a manual re-expand during editing
  // isn't immediately fought.
  useEffect(() => {
    if ((openStyleId || openBuiltin) && isMobile) setCarouselOpen(false);
  }, [openStyleId, openBuiltin]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let cancelled = false;
    Promise.all(BUILTINS.map((b) => previewStyle(b.value, PREVIEW_CHANNEL_COUNT).catch(() => null))).then((results) => {
      if (cancelled) return;
      const map = {};
      BUILTINS.forEach((b, i) => {
        if (results[i]) map[b.value] = results[i].tiles;
      });
      setBuiltinPreviews(map);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function saveRegistry(nextLayouts, nextOrder) {
    try {
      await patchConfig({ multiview_custom_layouts: nextLayouts, multiview_custom_layouts_order: nextOrder });
      await onFieldsReload();
    } catch (err) {
      notifications.show({ title: 'Save failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  async function handleNewStyle() {
    try {
      const id = genId();
      await saveRegistry({ ...customLayouts, [id]: { name: 'New Style', elements: [] } }, [...customOrder, id]);
      setOpenStyleId(id);
    } catch (err) {
      notifications.show({ title: 'Failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  function handleDeleteStyle(id) {
    const next = { ...customLayouts };
    delete next[id];
    saveRegistry(next, customOrder.filter((oid) => oid !== id));
    if (openStyleId === id) setOpenStyleId(null);
  }

  function handleStyleUpdate(id, next) {
    return saveRegistry({ ...customLayouts, [id]: next }, customOrder);
  }

  async function handleExportStyle(id, style) {
    try {
      const payload = await styleToExportObject(id, style);
      downloadJson(payload, `multiview-style-${(style.name || 'style').trim().replace(/\s+/g, '-') || 'style'}.json`);
    } catch (err) {
      notifications.show({ title: 'Export failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  async function handleImportStylesFile(e) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Not a valid style file');
      }
      if (Array.isArray(parsed.elements)) {
        // Single-style shape: {name, elements, ...}.
        validateStyleEntry(parsed);
        const [newId, style] = await importStyleEntry(parsed);
        await saveRegistry({ ...customLayouts, [newId]: style }, [...customOrder, newId]);
        notifications.show({ message: `Style "${style.name}" imported`, color: 'green', autoClose: 2000 });
      } else {
        // Whole-registry shape: {styleId: {name, elements, ...}, ...}.
        // Validate every entry up front -- no partial/half-applied registry
        // if a later entry turns out to be malformed.
        const entries = Object.entries(parsed);
        entries.forEach(([id, entry]) => validateStyleEntry(entry, `style "${id}"`));
        const additions = {};
        const newIds = [];
        for (const [, entry] of entries) {
          const [newId, style] = await importStyleEntry(entry);
          additions[newId] = style;
          newIds.push(newId);
        }
        await saveRegistry({ ...customLayouts, ...additions }, [...customOrder, ...newIds]);
        notifications.show({ message: `Imported ${newIds.length} style(s)`, color: 'green', autoClose: 2000 });
      }
    } catch (err) {
      notifications.show({ title: 'Import failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  const customEntries = customOrder.filter((id) => customLayouts[id]).map((id) => [id, customLayouts[id]]);
  const openStyle = openStyleId ? customLayouts[openStyleId] : null;
  // Built-ins are algorithmic, not element-based -- BUILTIN_ELEMENT_TEMPLATES
  // is a real element reconstruction that behaves like the algorithm it
  // stands in for, flowing through the same draft/resolveElements pipeline
  // as any real style (adapts correctly to preview-count changes for free).
  const openBuiltinStyle = openBuiltin ? { name: openBuiltin.label, elements: BUILTIN_ELEMENT_TEMPLATES[openBuiltin.value] } : null;

  function openCustomStyle(id) {
    setOpenBuiltin(null);
    setOpenStyleId(id);
  }

  function openBuiltinPreview(b) {
    setOpenStyleId(null);
    setOpenBuiltin(b);
  }

  // No "nothing open" state -- there's always an editor showing something,
  // defaulting to the first custom style (most useful default -- you
  // opened this to work on your own styles) or, failing that, the first
  // built-in. Also re-defaults if the currently-open style was deleted out
  // from under it (customLayouts no longer has that id).
  useEffect(() => {
    if (openStyle || openBuiltinStyle) return;
    if (customEntries.length > 0) {
      setOpenBuiltin(null);
      setOpenStyleId(customEntries[0][0]);
    } else {
      setOpenStyleId(null);
      setOpenBuiltin(BUILTINS[0]);
    }
  }, [openStyle, openBuiltinStyle, customEntries]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Stack gap="xs" style={{ flex: 1, minHeight: 0 }}>
      <Group justify="space-between" gap="xs">
        <Group gap={4}>
          <Text size="xs" c="dimmed">Click a built-in to preview it, or a custom style to edit it.</Text>
          {isMobile && (
            <ActionIcon size="xs" variant="subtle" onClick={() => setCarouselOpen((v) => !v)} aria-label={carouselOpen ? 'Collapse styles' : 'Expand styles'}>
              {carouselOpen ? <IconChevronUp size={12} /> : <IconChevronDown size={12} />}
            </ActionIcon>
          )}
        </Group>
        <Button size="xs" variant="default" leftSection={<IconUpload size={12} />} onClick={() => importStylesRef.current?.click()}>
          Import
        </Button>
        <input ref={importStylesRef} type="file" accept="application/json" style={{ display: 'none' }} onChange={handleImportStylesFile} />
      </Group>

      <Collapse in={!isMobile || carouselOpen}>
        <Group align="flex-start" gap="md" wrap="nowrap" style={{ overflowX: 'auto', paddingBottom: 4 }}>
          {BUILTINS.map((b) => (
            <Stack key={b.value} gap={4} align="center" style={{ flexShrink: 0, cursor: 'pointer' }} onClick={() => openBuiltinPreview(b)}>
              <TilePreview tiles={builtinPreviews[b.value]} />
              <Text size="xs">{b.label}</Text>
            </Stack>
          ))}
          {customEntries.map(([id, style]) => (
            <CustomStyleTile key={id} id={id} style={style} onOpen={openCustomStyle} onExport={handleExportStyle} onDelete={handleDeleteStyle} />
          ))}
          <NewStyleTile onClick={handleNewStyle} />
        </Group>
      </Collapse>

      {openStyle ? (
        <StyleEditor style={openStyle} styleId={openStyleId} onUpdate={(next) => handleStyleUpdate(openStyleId, next)} />
      ) : openBuiltinStyle ? (
        <StyleEditor style={openBuiltinStyle} styleId={`builtin:${openBuiltin.value}`} onUpdate={() => {}} readOnly />
      ) : null}
    </Stack>
  );
}
