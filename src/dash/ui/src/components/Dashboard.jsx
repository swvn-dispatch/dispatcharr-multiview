import { useState, useEffect, useCallback } from 'react';
import { AppShell, Stack, Text, Loader, Center, Button } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconRefresh, IconActivity } from '@tabler/icons-react';
import { AppHeader, SettingsPanel, ConfirmModal } from '@swvn-dispatch/dispatch-ui-kit';
import logoUrl from '/logo.png';
import { loadFields, loadConfig, patchConfig, triggerRefresh, listStreams, setUnauthorizedHandler } from '../api.js';
import { isTrigger } from '../utils/fields.js';
import { LayoutCard } from './LayoutCard.jsx';
import { ActiveStreamsModal } from './ActiveStreamsModal.jsx';

export function Dashboard({ onLoggedOut }) {
  const [fieldData, setFieldData] = useState(null);
  const [settings, setSettings] = useState({});
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [streamsOpen, setStreamsOpen] = useState(false);
  const [activeCount, setActiveCount] = useState(0);
  const [confirm, setConfirm] = useState(null);

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
        setActiveCount((d.active ?? []).length);
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

  async function handleChangeChannelCount(n, delta) {
    const key = `multiview_${n}_channel_count`;
    const next = (settings[key] ?? 4) + delta;
    try {
      await patchConfig({ [key]: next });
      await fetchFields();
    } catch (err) {
      notifications.show({ title: 'Failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  async function handleAddLayout() {
    const n = (fieldData?.layout_count ?? 1) + 1;
    const updates = {
      multiview_count: n,
      [`multiview_${n}_name`]: `Multiview ${n}`,
      [`multiview_${n}_layout`]: 'auto',
      [`multiview_${n}_selector_type`]: 'classic',
      [`multiview_${n}_channel_count`]: 4,
      [`multiview_${n}_epg_source_mode`]: 'dummy',
    };
    try {
      await patchConfig(updates);
      await fetchFields();
      notifications.show({ message: `Layout ${n} added`, color: 'green', autoClose: 2000 });
    } catch (err) {
      notifications.show({ title: 'Failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  async function handleRemoveLayout(n) {
    const count = fieldData?.layout_count ?? 1;
    if (count <= 1) return;
    const nullKeys = Object.fromEntries(
      Object.keys(settings).filter((k) => k.startsWith(`multiview_${n}_`)).map((k) => [k, null]),
    );
    nullKeys.multiview_count = count - 1;
    try {
      await patchConfig(nullKeys);
      await fetchFields();
      notifications.show({ message: `Layout ${n} removed`, color: 'green', autoClose: 2000 });
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
        ]}
      />

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
          {layouts.map(({ n, fields }) => (
            <LayoutCard
              key={n}
              n={n}
              fields={fields}
              settings={settings}
              isLast={n === layout_count && layout_count > 1}
              onSettingsChange={handleSettingsChange}
              onFieldsReload={fetchFields}
              onRemove={() => handleRemoveLayout(n)}
              onChannelCountChange={(delta) => handleChangeChannelCount(n, delta)}
            />
          ))}
          <Button variant="default" onClick={handleAddLayout}>+ Add Layout</Button>
        </Stack>
      </AppShell.Main>

      <ActiveStreamsModal opened={streamsOpen} onClose={() => setStreamsOpen(false)} settings={settings} />
      <ConfirmModal action={confirm} onClose={() => setConfirm(null)} />
    </AppShell>
  );
}
