import { useState, useEffect, useCallback, useRef } from 'react';
import { AppShell, Stack, Text, Loader, Center, Button, Menu, ActionIcon } from '@mantine/core';
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
import { LayoutCard } from './LayoutCard.jsx';
import { ActiveStreamsModal } from './ActiveStreamsModal.jsx';
import { StyleBuilderPage } from './StyleBuilder.jsx';

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
  const [view, setView] = useState('dashboard'); // 'dashboard' | 'styles'
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
      const blob = new Blob([JSON.stringify(config.settings ?? {}, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `multiview-backup-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
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
      const oldOrder = Array.isArray(parsed.multiview_order) ? parsed.multiview_order : [];
      const idMap = Object.fromEntries(oldOrder.map((oldId) => [oldId, genId()]));
      const newOrder = oldOrder.map((oldId) => idMap[oldId]);

      const layoutKeyRe = /^multiview_([0-9a-f]{8})_(.+)$/;
      const remapped = {};
      for (const [key, value] of Object.entries(parsed)) {
        if (key === 'multiview_order') continue;
        const m = key.match(layoutKeyRe);
        if (m && idMap[m[1]]) {
          remapped[`multiview_${idMap[m[1]]}_${m[2]}`] = value;
        } else if (!m) {
          remapped[key] = value; // global setting, or a shared registry like multiview_custom_layouts
        }
        // else: a per-layout key whose id isn't in multiview_order -- orphaned, dropped
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
          },
          {
            key: 'refresh',
            label: 'Refresh M3U & EPG',
            icon: IconRefresh,
            loading: refreshing,
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
            onClick: () => setView('styles'),
            active: view === 'styles',
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

      <AppShell.Main>
        {view === 'styles' ? (
          <StyleBuilderPage settings={settings} onFieldsReload={fetchFields} onBack={() => setView('dashboard')} />
        ) : (
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
        )}
      </AppShell.Main>

      <ActiveStreamsModal opened={streamsOpen} onClose={() => setStreamsOpen(false)} settings={settings} />
      <ConfirmModal action={confirm} onClose={() => setConfirm(null)} />
    </AppShell>
  );
}
