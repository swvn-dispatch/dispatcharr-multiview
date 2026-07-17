import { useState, useEffect, useCallback, useRef } from 'react';
import { AppShell, Stack, Text, Loader, Center, Button, Menu, ActionIcon, Modal } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconRefresh, IconActivity, IconSettings, IconDownload, IconUpload, IconPalette } from '@tabler/icons-react';
import { AppHeader, SettingsPanel, ConfirmModal } from '@swvn-dispatch/dispatch-ui-kit';
import { DndContext, PointerSensor, closestCenter, useSensor, useSensors } from '@dnd-kit/core';
import { SortableContext, verticalListSortingStrategy, useSortable, arrayMove } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import logoUrl from '/logo.png';
import { loadFields, loadConfig, patchConfig, triggerRefresh, listStreams, restartStreams, loadChannels, setUnauthorizedHandler, getUsername } from '../api.js';
import { isTrigger } from '../utils/fields.js';
import { genId } from '../utils/id.js';
import { downloadJson } from '../utils/download.js';
import { validateBackupObject, validateLegacyBackupObject } from '../utils/validate.js';
import { LayoutCard } from './LayoutCard.jsx';
import { ActiveStreamsModal } from './ActiveStreamsModal.jsx';
import { StyleBuilder } from './StyleBuilder.jsx';

const LAYOUT_KEY_RE = /^multiview_([0-9a-f]{8})_(.+)$/;

// Transforms the flat multiview_{id}_field settings keys into a clean
// {global, layouts: [...], custom_styles, plugin_version, dispatcharr_version}
// export shape. Array position in `layouts` *is* the display order -- the
// separate multiview_order id-list doesn't appear in the exported shape at all.
function toExportObject(settings, order, pluginVersion, dispatcharrVersion) {
  const layouts = order.map((id) => {
    const obj = { id };
    const channels = [];
    for (const [key, value] of Object.entries(settings)) {
      const m = key.match(LAYOUT_KEY_RE);
      if (!m || m[1] !== id) continue;
      const chMatch = m[2].match(/^channel_(\d+)$/);
      if (chMatch) channels[parseInt(chMatch[1], 10) - 1] = value;
      else obj[m[2]] = value;
    }
    if (channels.length) obj.channels = channels;
    return obj;
  });

  const global = {};
  for (const [key, value] of Object.entries(settings)) {
    if (key === 'multiview_order' || key === 'multiview_count' || key === 'multiview_custom_layouts' || key === 'multiview_custom_layouts_order') continue;
    if (key === 'multiview_pre_migration_backup' || key === 'multiview_pre_reconcile_backup') continue;
    if (LAYOUT_KEY_RE.test(key)) continue;
    global[key] = value; // dash_enabled, output_resolution, video_encoder, stray keys, etc.
  }

  // Built in the explicit multiview_custom_layouts_order sequence, not the
  // dict's own key order -- that order isn't reliably preserved through
  // backend storage (see ensure_custom_layout_order in config.py), so
  // spreading the raw dict here would risk baking a scrambled order into
  // the export. JS object insertion order *is* preserved through
  // JSON.stringify/parse, so building it in the right order here is enough
  // for a re-import to come back in the right order too.
  const customLayouts = settings.multiview_custom_layouts ?? {};
  const customOrder = settings.multiview_custom_layouts_order ?? Object.keys(customLayouts);
  const custom_styles = {};
  for (const id of customOrder) {
    if (customLayouts[id]) custom_styles[id] = customLayouts[id];
  }

  return {
    global,
    layouts,
    custom_styles,
    plugin_version: pluginVersion,
    dispatcharr_version: dispatcharrVersion,
  };
}

// Reverses toExportObject back into flat multiview_{id}_field keys, with
// fresh ids (never reuse ids from the file -- avoids colliding with layouts
// that already exist in this instance).
function fromExportObject(parsed) {
  const flat = {};
  const newOrder = [];
  for (const layout of parsed.layouts) {
    const newId = genId();
    newOrder.push(newId);
    for (const [key, value] of Object.entries(layout)) {
      if (key === 'id') continue;
      if (key === 'channels') {
        value.forEach((v, i) => { flat[`multiview_${newId}_channel_${i + 1}`] = v; });
      } else {
        flat[`multiview_${newId}_${key}`] = value;
      }
    }
  }
  Object.assign(flat, parsed.global ?? {});
  flat.multiview_order = newOrder;
  flat.multiview_custom_layouts = parsed.custom_styles ?? {};
  flat.multiview_custom_layouts_order = Object.keys(parsed.custom_styles ?? {});
  return flat;
}

function SortableLayoutCard({ id, ...props }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };
  return (
    <div ref={setNodeRef} style={style}>
      <LayoutCard id={id} dragHandleProps={{ ...attributes, ...listeners }} {...props} />
    </div>
  );
}

export function Dashboard({ onLoggedOut }) {
  const [fieldData, setFieldData] = useState(null);
  const [settings, setSettings] = useState({});
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [streamsOpen, setStreamsOpen] = useState(false);
  const [styleBuilderOpen, setStyleBuilderOpen] = useState(false);
  const [activeCount, setActiveCount] = useState(0);
  const [activeStreamIds, setActiveStreamIds] = useState(() => new Set());
  const [confirm, setConfirm] = useState(null);
  const fileInputRef = useRef(null);

  const fetchFields = useCallback(async () => {
    try {
      const [data, config] = await Promise.all([loadFields(), loadConfig()]);
      setFieldData(data);
      const dbSettings = config.settings ?? {};
      const allFields = [...(data.global ?? []), ...(data.layouts ?? []).flatMap((l) => l.fields)];
      const merged = { ...dbSettings };
      for (const f of allFields) {
        if (!(f.id in merged) && f.default !== undefined) merged[f.id] = f.default;
      }
      setSettings(merged);
    } catch (err) {
      if (err.status !== 401) setError(err.message);
    }
  }, []);

  useEffect(() => {
    fetchFields();
  }, [fetchFields]);

  useEffect(() => {
    const poll = async () => {
      try {
        const d = await listStreams();
        const active = d.active ?? [];
        setActiveCount(active.length);
        setActiveStreamIds(new Set(active.map((s) => s.n)));
      } catch {
        // noop
      }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, []);

  // Wires 401s from any api.js call (not just this component's own requests)
  // back to the same logout path as a manual Logout click.
  useEffect(() => {
    setUnauthorizedHandler(onLoggedOut);
    return () => setUnauthorizedHandler(() => {});
  }, [onLoggedOut]);

  const handleSettingsChange = useCallback((updates) => setSettings((p) => ({ ...p, ...updates })), []);

  function currentOrder() {
    return (fieldData?.layouts ?? []).map((l) => l.n);
  }

  async function handleChangeChannelCount(id, delta) {
    const key = `multiview_${id}_channel_count`;
    const next = (settings[key] ?? 4) + delta;
    try {
      await patchConfig({ [key]: next });
      await fetchFields();
    } catch (err) {
      notifications.show({ title: 'Failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  async function handleAddLayout() {
    try {
      const order = currentOrder();
      const id = genId();
      const position = order.length + 1;
      const updates = {
        multiview_order: [...order, id],
        [`multiview_${id}_name`]: `Multiview ${position}`,
        [`multiview_${id}_layout`]: 'auto',
        [`multiview_${id}_selector_type`]: 'classic',
        [`multiview_${id}_channel_count`]: 4,
        [`multiview_${id}_epg_source_mode`]: 'dummy',
      };
      await patchConfig(updates);
      await fetchFields();
      notifications.show({ message: `Layout ${position} added`, color: 'green', autoClose: 2000 });
    } catch (err) {
      notifications.show({ title: 'Failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  async function handleRemoveLayout(id) {
    const order = currentOrder();
    if (order.length <= 1) return;
    const updates = { multiview_order: order.filter((x) => x !== id) };
    for (const key of Object.keys(settings)) {
      if (key.startsWith(`multiview_${id}_`)) updates[key] = null;
    }
    try {
      await patchConfig(updates);
      // Best-effort: the layout's stream URL is now permanently gone, so kill
      // any in-progress viewer immediately instead of letting them silently
      // hang until their player eventually errors out. No-op if not active.
      try {
        await restartStreams(id);
      } catch {
        // ignore -- config removal already succeeded, this is just cleanup
      }
      await fetchFields();
      notifications.show({ message: 'Layout removed', color: 'green', autoClose: 2000 });
    } catch (err) {
      notifications.show({ title: 'Failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));

  async function handleDragEnd(event) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const order = currentOrder();
    const oldIndex = order.indexOf(active.id);
    const newIndex = order.indexOf(over.id);
    if (oldIndex === -1 || newIndex === -1) return;
    const newOrder = arrayMove(order, oldIndex, newIndex);
    try {
      await patchConfig({ multiview_order: newOrder });
      await fetchFields();
    } catch (err) {
      notifications.show({ title: 'Failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  async function doRefresh() {
    setRefreshing(true);
    try {
      const result = await triggerRefresh();
      notifications.show({
        message: result.message || 'M3U & EPG refreshed',
        color: result.status === 'success' ? 'green' : 'red',
        autoClose: 3000,
      });
    } catch (err) {
      notifications.show({ title: 'Refresh failed', message: err.message, color: 'red', autoClose: 4000 });
    } finally {
      setRefreshing(false);
    }
  }

  async function handleExportBackup() {
    try {
      const config = await loadConfig();
      const order = currentOrder();
      const exportObj = toExportObject(config.settings ?? {}, order, config.plugin_version, config.dispatcharr_version);
      downloadJson(exportObj, `multiview-backup-${new Date().toISOString().slice(0, 10)}.json`);
    } catch (err) {
      notifications.show({ title: 'Export failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  async function handleImportFile(e) {
    const file = e.target.files?.[0];
    e.target.value = ''; // allow re-selecting the same file again later
    if (!file) return;

    try {
      const parsed = JSON.parse(await file.text());
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Backup file is not a valid settings object');
      }

      // Never reuse ids from the file -- avoids colliding with layouts that
      // already exist in this instance and sidesteps merging two order lists.
      let remapped;
      let newOrder;
      if (Array.isArray(parsed.layouts)) {
        // Current export shape: {global, layouts: [...], custom_styles, plugin_version, dispatcharr_version}.
        validateBackupObject(parsed);
        remapped = fromExportObject(parsed);
        newOrder = remapped.multiview_order;
      } else {
        // Pre-restructure flat export: multiview_order + multiview_{id}_* keys
        // scattered at the top level alongside global settings.
        validateLegacyBackupObject(parsed);
        const oldOrder = Array.isArray(parsed.multiview_order) ? parsed.multiview_order : [];
        const idMap = Object.fromEntries(oldOrder.map((oldId) => [oldId, genId()]));
        newOrder = oldOrder.map((oldId) => idMap[oldId]);
        remapped = {};
        for (const [key, value] of Object.entries(parsed)) {
          if (key === 'multiview_order') continue;
          const m = key.match(LAYOUT_KEY_RE);
          if (m && idMap[m[1]]) {
            remapped[`multiview_${idMap[m[1]]}_${m[2]}`] = value;
          } else if (!m) {
            remapped[key] = value; // global setting, or a shared registry like multiview_custom_layouts
          }
          // else: a per-layout key whose id isn't in multiview_order -- orphaned, dropped
        }
        remapped.multiview_order = newOrder;
      }

      // channel_{m} and epg_forward_channel store a Dispatcharr Channel.id DB
      // primary key, which is instance-specific and won't resolve on a
      // different install (or after channels were re-added on this one).
      let channelIds = new Set();
      try {
        const channels = await loadChannels();
        channelIds = new Set(channels.map((c) => String(c.id)));
      } catch {
        channelIds = new Set(); // skip validation rather than blocking the whole restore
      }

      const clearedSlots = [];
      newOrder.forEach((newId, idx) => {
        const name = remapped[`multiview_${newId}_name`] || `Multiview ${idx + 1}`;
        const prefix = `multiview_${newId}_`;
        for (const key of Object.keys(remapped)) {
          if (!key.startsWith(prefix)) continue;
          const suffix = key.slice(prefix.length);
          if (suffix !== 'epg_forward_channel' && !/^channel_\d+$/.test(suffix)) continue;
          const val = remapped[key];
          if (val && val !== '_none' && channelIds.size > 0 && !channelIds.has(String(val))) {
            remapped[key] = '_none';
            clearedSlots.push(`${name} (${suffix})`);
          }
        }
      });

      const updates = {};
      for (const key of Object.keys(settings)) {
        if (key.startsWith('multiview_')) updates[key] = null;
      }
      Object.assign(updates, remapped);
      updates.multiview_order = newOrder;

      const warningText = clearedSlots.length
        ? ` ${clearedSlots.length} channel selection(s) could not be matched and were cleared (reselect manually): ${clearedSlots.join(', ')}.`
        : '';

      setConfirm({
        title: 'Restore Backup',
        confirmLabel: 'Restore',
        color: 'red',
        message: `Restore will replace all current layouts and settings with this backup (${newOrder.length} layout(s)).${warningText} Continue?`,
        onConfirm: async () => {
          try {
            await patchConfig(updates);
            await fetchFields();
            notifications.show({ message: 'Backup restored', color: 'green', autoClose: 2000 });
          } catch (err) {
            notifications.show({ title: 'Restore failed', message: err.message, color: 'red', autoClose: 4000 });
          }
        },
      });
    } catch (err) {
      notifications.show({ title: 'Import failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  if (error) return <Center mih="100dvh"><Text c="red">{error}</Text></Center>;
  if (!fieldData) return <Center mih="100dvh"><Loader /></Center>;

  const { warnings = [], global: globalFields = [], layouts = [], layout_count } = fieldData;

  return (
    <AppShell header={{ height: 56 }}>
      <AppHeader
        logoUrl={logoUrl}
        appName="Multiview"
        version={__APP_VERSION__}
        githubUrl="https://github.com/swvn-dispatch/dispatcharr-multiview"
        username={getUsername()}
        onLogout={onLoggedOut}
        actions={[
          {
            key: 'streams',
            label: 'Streams',
            icon: IconActivity,
            onClick: () => setStreamsOpen(true),
            active: activeCount > 0,
            count: activeCount,
            variant: 'default',
          },
          {
            key: 'refresh',
            label: 'Refresh M3U & EPG',
            icon: IconRefresh,
            loading: refreshing,
            variant: 'default',
            onClick: () =>
              setConfirm({
                title: 'Refresh M3U & EPG',
                confirmLabel: 'Refresh',
                color: 'blue',
                message: 'Regenerate the M3U playlist and sync EPG data now?',
                onConfirm: doRefresh,
              }),
          },
          {
            key: 'style-builder',
            label: 'Style Builder',
            icon: IconPalette,
            variant: 'default',
            onClick: () => setStyleBuilderOpen(true),
          },
        ]}
        extra={
          <Menu shadow="md" width={190} position="bottom-end">
            <Menu.Target>
              <ActionIcon size="lg" variant="default" aria-label="More actions">
                <IconSettings size={18} />
              </ActionIcon>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Item leftSection={<IconDownload size={14} />} onClick={handleExportBackup}>
                Download Backup
              </Menu.Item>
              <Menu.Item leftSection={<IconUpload size={14} />} onClick={() => fileInputRef.current?.click()}>
                Restore Backup
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
        }
      />
      <input
        ref={fileInputRef}
        type="file"
        accept="application/json"
        style={{ display: 'none' }}
        onChange={handleImportFile}
      />

      <Modal
        opened={styleBuilderOpen}
        onClose={() => setStyleBuilderOpen(false)}
        title="Style Builder"
        fullScreen
        styles={{
          content: { display: 'flex', flexDirection: 'column' },
          body: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflowY: 'auto' },
        }}
      >
        <StyleBuilder settings={settings} onFieldsReload={fetchFields} />
      </Modal>

      <AppShell.Main>
        <Stack p="md" maw={860} mx="auto">
          <SettingsPanel
            fields={globalFields}
            warnings={warnings}
            values={settings}
            onSave={patchConfig}
            onOptimisticChange={handleSettingsChange}
            shouldReload={isTrigger}
            onReload={fetchFields}
            onSaved={() => notifications.show({ message: 'Saved', color: 'green', autoClose: 1500 })}
            onError={(err) => notifications.show({ title: 'Save failed', message: err.message, color: 'red', autoClose: 4000 })}
          />
          <Text size="xs" tt="uppercase" fw={700} c="dimmed" mt="sm">Layouts</Text>
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
            <SortableContext items={currentOrder()} strategy={verticalListSortingStrategy}>
              {layouts.map(({ n: id, position, fields }) => (
                <SortableLayoutCard
                  key={id}
                  id={id}
                  position={position}
                  fields={fields}
                  settings={settings}
                  canRemove={layout_count > 1}
                  hasActiveStream={activeStreamIds.has(id)}
                  onSettingsChange={handleSettingsChange}
                  onFieldsReload={fetchFields}
                  onRemove={() => handleRemoveLayout(id)}
                  onChannelCountChange={(delta) => handleChangeChannelCount(id, delta)}
                />
              ))}
            </SortableContext>
          </DndContext>
          <Button variant="default" onClick={handleAddLayout}>+ Add Layout</Button>
        </Stack>
      </AppShell.Main>

      <ActiveStreamsModal opened={streamsOpen} onClose={() => setStreamsOpen(false)} settings={settings} />
      <ConfirmModal action={confirm} onClose={() => setConfirm(null)} />
    </AppShell>
  );
}
