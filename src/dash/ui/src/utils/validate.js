// Strict schema validation for both JSON import paths (per-style import in
// StyleBuilder, whole-settings backup import in Dashboard) -- shared so a
// malformed file is rejected with one specific, actionable error message
// instead of being silently accepted or partially imported by either path.

export const ALIGN_VALUES = ['center', 'top', 'bottom', 'left', 'right'];

function isPlainObject(v) {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

function validateElement(el, path) {
  if (!isPlainObject(el)) throw new Error(`${path}: not an object`);
  if (!['static', 'row', 'grid'].includes(el.type)) throw new Error(`${path}.type: invalid "${el.type}"`);
  for (const k of ['x', 'y', 'w', 'h']) {
    if (typeof el[k] !== 'number' || !Number.isFinite(el[k])) throw new Error(`${path}.${k}: must be a finite number`);
  }
  if (el.name != null && typeof el.name !== 'string') throw new Error(`${path}.name: must be a string`);
  if (el.valign != null && !ALIGN_VALUES.includes(el.valign)) throw new Error(`${path}.valign: invalid "${el.valign}"`);
  if (el.halign != null && !ALIGN_VALUES.includes(el.halign)) throw new Error(`${path}.halign: invalid "${el.halign}"`);
  if (el.type === 'row' && el.direction != null && !['horizontal', 'vertical'].includes(el.direction)) {
    throw new Error(`${path}.direction: invalid "${el.direction}"`);
  }
  if (el.max != null && (typeof el.max !== 'number' || !Number.isFinite(el.max))) {
    throw new Error(`${path}.max: must be a number`);
  }
  if (el.lockAspect != null && typeof el.lockAspect !== 'boolean') {
    throw new Error(`${path}.lockAspect: must be a boolean`);
  }
}

// Validates one style object: {name?, elements: [...], background_image_data?, background_image_filename?}.
// Throws with a specific, path-prefixed message on the first violation found.
export function validateStyleEntry(entry, path = 'style') {
  if (!isPlainObject(entry)) throw new Error(`${path}: not an object`);
  if (entry.name != null && typeof entry.name !== 'string') throw new Error(`${path}.name: must be a string`);
  if (!Array.isArray(entry.elements)) throw new Error(`${path}.elements: must be an array`);
  entry.elements.forEach((el, i) => validateElement(el, `${path}.elements[${i}]`));
  if (entry.background_image_data != null && typeof entry.background_image_data !== 'string') {
    throw new Error(`${path}.background_image_data: must be a string`);
  }
  if (entry.background_image_filename != null && typeof entry.background_image_filename !== 'string') {
    throw new Error(`${path}.background_image_filename: must be a string`);
  }
}

const LAYOUT_FIELD_TYPES = {
  name: 'string',
  layout: 'string',
  channel_count: 'number',
  selector_type: 'string',
  epg_source_mode: 'string',
};

function validateLayoutEntry(layout, path) {
  if (!isPlainObject(layout)) throw new Error(`${path}: not an object`);
  for (const [field, type] of Object.entries(LAYOUT_FIELD_TYPES)) {
    if (layout[field] != null && typeof layout[field] !== type) {
      throw new Error(`${path}.${field}: must be a ${type}`);
    }
  }
  if (layout.channels != null) {
    if (!Array.isArray(layout.channels)) throw new Error(`${path}.channels: must be an array`);
    layout.channels.forEach((ch, i) => {
      if (ch != null && typeof ch !== 'string') throw new Error(`${path}.channels[${i}]: must be a string`);
    });
  }
}

// Validates the current (nested) settings-backup export shape:
// {global?, layouts: [...], custom_styles?, plugin_version?, dispatcharr_version?}.
export function validateBackupObject(parsed) {
  if (!isPlainObject(parsed)) throw new Error('backup: not an object');
  if (!Array.isArray(parsed.layouts)) throw new Error('backup.layouts: must be an array');
  parsed.layouts.forEach((layout, i) => validateLayoutEntry(layout, `backup.layouts[${i}]`));
  if (parsed.global != null && !isPlainObject(parsed.global)) throw new Error('backup.global: must be an object');
  if (parsed.custom_styles != null) {
    if (!isPlainObject(parsed.custom_styles)) throw new Error('backup.custom_styles: must be an object');
    for (const [id, style] of Object.entries(parsed.custom_styles)) {
      validateStyleEntry(style, `backup.custom_styles["${id}"]`);
    }
  }
}

// Validates the legacy flat-export shape: multiview_order + multiview_{id}_*
// keys scattered at the top level alongside global settings. Rejects stray
// multiview_{id}_* keys whose id isn't listed in multiview_order.
export function validateLegacyBackupObject(parsed) {
  if (!isPlainObject(parsed)) throw new Error('backup: not an object');
  const order = parsed.multiview_order;
  if (!Array.isArray(order) || order.some((id) => typeof id !== 'string')) {
    throw new Error('backup.multiview_order: must be an array of strings');
  }
  const orderSet = new Set(order);
  const layoutKeyRe = /^multiview_([0-9a-f]{8})_(.+)$/;
  for (const key of Object.keys(parsed)) {
    const m = key.match(layoutKeyRe);
    if (m && !orderSet.has(m[1])) {
      throw new Error(`backup["${key}"]: id "${m[1]}" not present in multiview_order`);
    }
  }
}
