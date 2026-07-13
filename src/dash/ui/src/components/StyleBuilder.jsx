import { useState, useEffect, useRef } from 'react';
import { Stack, Group, Text, Button, TextInput, NumberInput, Select, ActionIcon, SegmentedControl, Switch } from '@mantine/core';
import { IconTrash, IconPlus, IconArrowUp, IconArrowDown, IconArrowLeft, IconPhoto, IconX, IconDownload, IconUpload } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { Rnd } from 'react-rnd';
import { patchConfig, previewStyle, uploadStyleBackground, fetchStyleBackgroundBlob } from '../api.js';
import { genId } from '../utils/id.js';
import { resolveElements, splitRow, distributeDynamicCounts } from '../utils/styleResolve.js';
import { snapRect, buildSnapTargets } from '../utils/snap.js';
import { downloadJson } from '../utils/download.js';
import { blobToBase64 } from '../utils/file.js';

const ELEMENT_LABELS = { static: 'Static', row: 'Auto Row', grid: 'Auto Grid' };
const ELEMENT_STYLES = {
  static: { border: '1px solid var(--mantine-color-blue-5)', background: 'rgba(51, 154, 240, 0.15)' },
  row: { border: '2px dashed var(--mantine-color-teal-5)', background: 'rgba(18, 184, 134, 0.12)' },
  grid: { border: '2px dotted var(--mantine-color-orange-5)', background: 'rgba(253, 126, 20, 0.12)' },
};

const BUILTINS = [
  { value: 'auto', label: 'Auto Grid' },
  { value: 'featured', label: 'Featured' },
  { value: 'top_featured', label: 'Top Featured' },
];

// Only the fractions matter for a preview -- a single representative channel
// count is enough to show the general shape of a built-in style.
const PREVIEW_CHANNEL_COUNT = 4;
const CHANNEL_COUNTS = Array.from({ length: 8 }, (_, i) => String(i + 2)); // "2".."9"
const ALIGN_OPTIONS = ['center', 'top', 'bottom', 'left', 'right'].map((v) => ({ value: v, label: v }));

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

    const pieces = splitRow(el, count);
    const xs = new Set();
    const ys = new Set();
    for (const [px, py] of pieces) {
      xs.add(px);
      ys.add(py);
    }
    const eps = 1e-6;
    for (const x of xs) if (x > x0 + eps) guides.push({ axis: 'x', pos: x, from: y0, to: y1 });
    for (const y of ys) if (y > y0 + eps) guides.push({ axis: 'y', pos: y, from: x0, to: x1 });
  });
  return guides;
}

// Measures a ref'd element's live content-box size via ResizeObserver.
// Returns null until the first real measurement lands -- callers must not
// substitute a guessed size in the meantime, since any pixel<->fraction
// math done against a guess (rather than the real, possibly much larger,
// rendered size) would compute badly wrong fractions.
function useContainerSize(ref) {
  const [size, setSize] = useState(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      if (width > 0 && height > 0) setSize({ width: Math.round(width), height: Math.round(height) });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return size;
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

function ElementCanvas({ elements, selectedIdx, onSelect, onElementChange, backgroundUrl, previewCountNum, snapToCanvas, snapToElements, canvasW, canvasH }) {
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
    const { targetsX, targetsY } = buildSnapTargets({
      canvasW,
      canvasH,
      others: othersPx(idx),
      subdivisions: subdivisionGuides,
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
            lockAspectRatio={el.lockAspect ? el.w / el.h : false}
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

function StyleEditor({ style, styleId, onUpdate }) {
  const [previewCount, setPreviewCount] = useState('4');
  const [selectedIdx, setSelectedIdx] = useState(null);
  const [backgroundUrl, setBackgroundUrl] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [snapToElements, setSnapToElements] = useState(true);
  const [snapToCanvas, setSnapToCanvas] = useState(true);
  const fileInputRef = useRef(null);
  const canvasContainerRef = useRef(null);
  const canvasSize = useContainerSize(canvasContainerRef);
  const elements = style.elements ?? [];
  const previewCountNum = parseInt(previewCount, 10);
  const previewTiles = resolveElements(elements, previewCountNum) ?? [];
  const selected = selectedIdx != null ? elements[selectedIdx] : null;

  useEffect(() => {
    let cancelled = false;
    let objectUrl = null;
    if (style.background_image) {
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
  }, [styleId, style.background_image]);

  function updateElements(next) {
    onUpdate({ ...style, elements: next });
  }

  async function handleBackgroundFile(e) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setUploading(true);
    try {
      const dataBase64 = await blobToBase64(file);
      const result = await uploadStyleBackground(styleId, file.name, dataBase64);
      onUpdate({ ...style, background_image: result.filename });
    } catch (err) {
      notifications.show({ title: 'Upload failed', message: err.message, color: 'red', autoClose: 4000 });
    } finally {
      setUploading(false);
    }
  }

  function handleRemoveBackground() {
    onUpdate({ ...style, background_image: null });
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
    <Stack gap="sm">
      <Group gap="xs" align="flex-end">
        <TextInput
          size="xs"
          label="Style name"
          value={style.name ?? ''}
          onChange={(e) => onUpdate({ ...style, name: e.currentTarget.value })}
          placeholder="Style name"
          style={{ maxWidth: 240 }}
        />
        <Select
          size="xs"
          label="Preview channel count"
          data={CHANNEL_COUNTS}
          value={previewCount}
          onChange={(v) => v && setPreviewCount(v)}
          w={160}
        />
        <Switch size="xs" label="Snap to elements" checked={snapToElements} onChange={(e) => setSnapToElements(e.currentTarget.checked)} />
        <Switch size="xs" label="Snap to canvas" checked={snapToCanvas} onChange={(e) => setSnapToCanvas(e.currentTarget.checked)} />
      </Group>

      <Group gap="xs" align="flex-end">
        <Button size="xs" variant="default" leftSection={<IconPlus size={12} />} onClick={() => addElement('static')}>
          Static Position
        </Button>
        <Button size="xs" variant="default" leftSection={<IconPlus size={12} />} onClick={() => addElement('row')}>
          Auto Row
        </Button>
        <Button size="xs" variant="default" leftSection={<IconPlus size={12} />} onClick={() => addElement('grid')}>
          Auto Grid
        </Button>
        <Button
          size="xs"
          variant="default"
          leftSection={<IconPhoto size={12} />}
          loading={uploading}
          onClick={() => fileInputRef.current?.click()}
        >
          {style.background_image ? 'Change Background' : 'Background Image'}
        </Button>
        {style.background_image && (
          <ActionIcon size="sm" variant="subtle" color="red" onClick={handleRemoveBackground} aria-label="Remove background">
            <IconX size={14} />
          </ActionIcon>
        )}
        <input ref={fileInputRef} type="file" accept="image/png,image/jpeg" style={{ display: 'none' }} onChange={handleBackgroundFile} />
      </Group>

      {elements.length === 0 ? (
        <Text size="xs" c="dimmed">Add a Static Position, Auto Row, or Auto Grid to get started.</Text>
      ) : (
        <>
          <Text size="xs" c="dimmed">Drag a box to move it, drag its edge/corner to resize it. Click a box or list row to select it.</Text>
          {previewTiles.length === 0 && (
            <Text size="xs" c="orange">This style can't cover {previewCount} channels yet -- add an Auto Row/Grid, more Static Positions, or raise a Max channels cap.</Text>
          )}
          <Group align="flex-start" gap="md" wrap="wrap" justify="center">
            <div ref={canvasContainerRef} style={{ width: '100%', maxWidth: 1400, aspectRatio: '16 / 9', maxHeight: 'min(60vh, calc(100vh - 320px))' }}>
              {canvasSize && (
                <ElementCanvas
                  elements={elements}
                  selectedIdx={selectedIdx}
                  onSelect={setSelectedIdx}
                  onElementChange={updateElementField}
                  backgroundUrl={backgroundUrl}
                  previewCountNum={previewCountNum}
                  snapToCanvas={snapToCanvas}
                  snapToElements={snapToElements}
                  canvasW={canvasSize.width}
                  canvasH={canvasSize.height}
                />
              )}
            </div>

            <Stack gap={4} w={190}>
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
                    background: selectedIdx === i ? 'var(--mantine-color-blue-9)' : 'transparent',
                  }}
                >
                  <Text size="xs" c="dimmed">{i + 1}.</Text>
                  <TextInput
                    size="xs"
                    variant="unstyled"
                    value={el.name ?? ''}
                    placeholder={ELEMENT_LABELS[el.type]}
                    onChange={(e) => updateElementField(i, { name: e.currentTarget.value })}
                    onClick={(e) => e.stopPropagation()}
                    style={{ flex: 1, minWidth: 0 }}
                    styles={{ input: { minHeight: 'unset', height: 'auto', padding: 0 } }}
                  />
                  <ActionIcon size="xs" variant="subtle" disabled={i === 0} onClick={(e) => { e.stopPropagation(); moveElement(i, -1); }}>
                    <IconArrowUp size={12} />
                  </ActionIcon>
                  <ActionIcon size="xs" variant="subtle" disabled={i === elements.length - 1} onClick={(e) => { e.stopPropagation(); moveElement(i, 1); }}>
                    <IconArrowDown size={12} />
                  </ActionIcon>
                  <ActionIcon size="xs" variant="subtle" color="red" onClick={(e) => { e.stopPropagation(); removeElement(i); }}>
                    <IconTrash size={12} />
                  </ActionIcon>
                </Group>
              ))}
            </Stack>
          </Group>

          {selected && (
            <Group gap="xs" p="xs" align="flex-end" style={{ border: '1px solid var(--mantine-color-dark-4)', borderRadius: 6 }}>
              <Text size="xs" fw={600}>{selected.name || ELEMENT_LABELS[selected.type]}:</Text>
              {selected.type === 'row' && (
                <SegmentedControl
                  size="xs"
                  data={[{ label: 'Horiz', value: 'horizontal' }, { label: 'Vert', value: 'vertical' }]}
                  value={selected.direction}
                  onChange={(v) => updateElementField(selectedIdx, { direction: v })}
                />
              )}
              {(selected.type === 'row' || selected.type === 'grid') && (
                <NumberInput
                  size="xs"
                  label="Max channels"
                  placeholder="Unlimited"
                  w={110}
                  min={1}
                  value={selected.max ?? ''}
                  onChange={(v) => updateElementField(selectedIdx, { max: v === '' ? null : v })}
                />
              )}
              <Select
                size="xs"
                w={90}
                label="V anchor"
                data={ALIGN_OPTIONS}
                value={selected.valign}
                onChange={(v) => v && updateElementField(selectedIdx, { valign: v })}
              />
              <Select
                size="xs"
                w={90}
                label="H anchor"
                data={ALIGN_OPTIONS}
                value={selected.halign}
                onChange={(v) => v && updateElementField(selectedIdx, { halign: v })}
              />
              <NumberInput
                size="xs"
                w={110}
                label="Width (1080p px)"
                min={1}
                value={Math.round(selected.w * 1920)}
                onChange={(v) => v && updateElementField(selectedIdx, { w: v / 1920 })}
              />
              <NumberInput
                size="xs"
                w={110}
                label="Height (1080p px)"
                min={1}
                value={Math.round(selected.h * 1080)}
                onChange={(v) => v && updateElementField(selectedIdx, { h: v / 1080 })}
              />
              <Switch
                size="xs"
                label="Lock aspect ratio"
                checked={!!selected.lockAspect}
                onChange={(e) => updateElementField(selectedIdx, { lockAspect: e.currentTarget.checked })}
              />
            </Group>
          )}
        </>
      )}
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

// Clickable gallery tile for a custom style -- same visual shape as a
// built-in's TilePreview + label, plus small export/delete actions that
// stopPropagation so they don't also open the editor.
function CustomStyleTile({ id, style, onOpen, onExport, onDelete }) {
  const tiles = resolveElements(style.elements ?? [], PREVIEW_CHANNEL_COUNT) ?? [];
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
  const [builtinPreviews, setBuiltinPreviews] = useState({});
  const [openStyleId, setOpenStyleId] = useState(null);
  const importStylesRef = useRef(null);

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

  async function saveRegistry(next) {
    try {
      await patchConfig({ multiview_custom_layouts: next });
      await onFieldsReload();
    } catch (err) {
      notifications.show({ title: 'Save failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  async function handleNewStyle() {
    try {
      const id = genId();
      await saveRegistry({ ...customLayouts, [id]: { name: 'New Style', elements: [] } });
      setOpenStyleId(id);
    } catch (err) {
      notifications.show({ title: 'Failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  function handleDeleteStyle(id) {
    const next = { ...customLayouts };
    delete next[id];
    saveRegistry(next);
    if (openStyleId === id) setOpenStyleId(null);
  }

  function handleStyleUpdate(id, next) {
    saveRegistry({ ...customLayouts, [id]: next });
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
        const [newId, style] = await importStyleEntry(parsed);
        await saveRegistry({ ...customLayouts, [newId]: style });
        notifications.show({ message: `Style "${style.name}" imported`, color: 'green', autoClose: 2000 });
      } else {
        // Whole-registry shape: {styleId: {name, elements, ...}, ...}.
        const additions = {};
        for (const entry of Object.values(parsed)) {
          const [newId, style] = await importStyleEntry(entry);
          additions[newId] = style;
        }
        await saveRegistry({ ...customLayouts, ...additions });
        notifications.show({ message: `Imported ${Object.keys(additions).length} style(s)`, color: 'green', autoClose: 2000 });
      }
    } catch (err) {
      notifications.show({ title: 'Import failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  const customEntries = Object.entries(customLayouts);
  const openStyle = openStyleId ? customLayouts[openStyleId] : null;

  return (
    <Stack gap="md" style={{ flex: 1, minHeight: 0 }}>
      <Group justify="space-between" gap="xs">
        <Text size="xs" c="dimmed">Built-in styles are read-only. Click a custom style to edit it.</Text>
        <Button size="xs" variant="default" leftSection={<IconUpload size={12} />} onClick={() => importStylesRef.current?.click()}>
          Import
        </Button>
        <input ref={importStylesRef} type="file" accept="application/json" style={{ display: 'none' }} onChange={handleImportStylesFile} />
      </Group>

      <Group gap="md" wrap="nowrap" style={{ overflowX: 'auto', paddingBottom: 4 }}>
        {BUILTINS.map((b) => (
          <Stack key={b.value} gap={4} align="center" style={{ flexShrink: 0 }}>
            <TilePreview tiles={builtinPreviews[b.value]} />
            <Text size="xs">{b.label}</Text>
          </Stack>
        ))}
        {customEntries.map(([id, style]) => (
          <CustomStyleTile key={id} id={id} style={style} onOpen={setOpenStyleId} onExport={handleExportStyle} onDelete={handleDeleteStyle} />
        ))}
        <NewStyleTile onClick={handleNewStyle} />
      </Group>

      {openStyle ? (
        <Stack gap="sm" style={{ flex: 1, minHeight: 0 }}>
          <Button
            size="xs"
            variant="subtle"
            leftSection={<IconArrowLeft size={14} />}
            onClick={() => setOpenStyleId(null)}
            style={{ alignSelf: 'flex-start' }}
          >
            Close editor
          </Button>
          <StyleEditor style={openStyle} styleId={openStyleId} onUpdate={(next) => handleStyleUpdate(openStyleId, next)} />
        </Stack>
      ) : (
        <Text size="xs" c="dimmed">Select a custom style above to edit it, or create a new one.</Text>
      )}
    </Stack>
  );
}
