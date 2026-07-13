export const TRIGGER_RE = /^(video_encoder|multiview_[0-9a-f]{8}_(selector_type|channel_count|epg_source_mode))$/;
export const isTrigger = (id) => TRIGGER_RE.test(id);

const LAYOUT_PREFIX_RE = /^Layout\s+\d+[:\s]*/i;
export const stripPrefix = (f) => ({ ...f, label: f.label.replace(LAYOUT_PREFIX_RE, '') });

export function groupFields(fields, layoutId) {
  const base = [], channels = [], epg = [];
  let audioSource = null, channelCountField = null;
  for (const f of fields) {
    const key = f.id.replace(`multiview_${layoutId}_`, '');
    if (['name', 'layout', 'selector_type'].includes(key)) base.push(f);
    else if (key === 'channel_count') channelCountField = f;
    else if (key === 'audio_source') audioSource = f;
    else if (key.startsWith('epg_')) epg.push(f);
    else channels.push(f);
  }
  return { base, channels, epg, audioSource, channelCountField };
}
