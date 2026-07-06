import { useState, useEffect, useCallback, useRef } from 'react';
import {
  AppShell, Group, Button, Stack, Text, Loader, Center, ActionIcon, Image,
  Paper, Alert, Collapse, UnstyledButton, Tooltip, Badge,
  Card, Divider, Modal, TextInput, PasswordInput, Title, Select, NumberInput,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconRefresh, IconAlertCircle, IconChevronDown, IconChevronRight, IconTrash,
  IconPlus, IconMinus, IconInfoCircle, IconPlayerPlay, IconActivity,
} from '@tabler/icons-react';
import logoUrl from '/logo.png';

// ── API ──────────────────────────────────────────────────────────────────────

const getToken = () => localStorage.getItem('mv_token');
const setToken = (t) => localStorage.setItem('mv_token', t);
const clearToken = () => localStorage.removeItem('mv_token');

let _onUnauthorized = null;

async function apiFetch(path, { method = 'GET', body = null } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const resp = await fetch(path, { method, headers, body: body !== null ? JSON.stringify(body) : undefined });
  if (resp.status === 401) {
    clearToken();
    if (_onUnauthorized) _onUnauthorized();
    const e = new Error('Session expired'); e.status = 401; throw e;
  }
  return resp;
}

async function login(username, password) {
  const resp = await fetch('/dash/api/auth/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!resp.ok) { const d = await resp.json().catch(() => ({})); throw new Error(d.error || 'Invalid credentials'); }
  setToken((await resp.json()).access);
}

const loadFields      = async () => { const r = await apiFetch('/dash/api/fields');                                       if (!r.ok) throw new Error('Failed to load fields'); return r.json(); };
const loadConfig      = async () => { const r = await apiFetch('/dash/api/config');                                       if (!r.ok) throw new Error('Failed to load config'); return r.json(); };
const patchConfig     = async (u)  => { const r = await apiFetch('/dash/api/config', { method: 'PATCH', body: u });      if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.error || 'Save failed'); } };
const triggerRefresh  = async () => (await apiFetch('/dash/api/refresh',         { method: 'POST' })).json();
const listStreams      = async () => (await apiFetch('/dash/api/streams')).json();
const restartStreams      = async (n) => (await apiFetch('/dash/api/streams/restart', { method: 'POST', body: n != null ? { n } : {} })).json();
const reconnectChannel    = async (n, channel_idx) => (await apiFetch('/dash/api/streams/restart', { method: 'POST', body: { n, channel_idx } })).json();

// ── Field helpers ─────────────────────────────────────────────────────────────

const TRIGGER_RE = /^(video_encoder|multiview_\d+_(selector_type|channel_count|epg_source_mode))$/;
const isTrigger = (id) => TRIGGER_RE.test(id);

function FieldLabel({ label, description }) {
  if (!description) return label;
  return (
    <Group gap={4} align="center" wrap="nowrap">
      <span>{label}</span>
      <Tooltip label={description} multiline maw={260} withArrow position="top-start" events={{ hover: true, focus: true, touch: true }}>
        <ActionIcon size="xs" variant="transparent" c="dimmed" tabIndex={-1} style={{ cursor: 'default', flexShrink: 0 }}>
          <IconInfoCircle size={13} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );
}

function FieldRenderer({ field, value, onChange }) {
  const common = { label: <FieldLabel label={field.label} description={field.description} /> };
  switch (field.type) {
    case 'select': {
      const seen = new Set();
      const data = (field.options ?? []).reduce((acc, o) => {
        const v = String(o.value);
        if (!seen.has(v)) { seen.add(v); acc.push({ value: v, label: o.label }); }
        return acc;
      }, []);
      return <Select {...common} data={data} value={String(value ?? field.default ?? '')} onChange={onChange} allowDeselect={false} />;
    }
    case 'number':
      return <NumberInput {...common} value={value ?? field.default ?? 0} min={field.min} max={field.max} placeholder={field.placeholder} onChange={onChange} />;
    case 'string':
      return <TextInput {...common} value={value ?? field.default ?? ''} placeholder={field.placeholder} onChange={(e) => onChange(e.currentTarget.value)} />;
    default:
      return null;
  }
}

function useSaveField(onSettingsChange, onFieldsReload) {
  const timers = useRef({});
  return useCallback(async (fieldId, value, immediate) => {
    onSettingsChange({ [fieldId]: value });
    const doSave = async () => {
      try {
        await patchConfig({ [fieldId]: value });
        notifications.show({ message: 'Saved', color: 'green', autoClose: 1500 });
        if (isTrigger(fieldId)) await onFieldsReload();
      } catch (err) {
        notifications.show({ title: 'Save failed', message: err.message, color: 'red', autoClose: 4000 });
      }
    };
    clearTimeout(timers.current[fieldId]);
    if (immediate) await doSave();
    else timers.current[fieldId] = setTimeout(doSave, 700);
  }, [onSettingsChange, onFieldsReload]);
}

// ── Login ─────────────────────────────────────────────────────────────────────

function LoginForm({ onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError]       = useState('');
  const [loading, setLoading]   = useState(false);

  async function handleSubmit(e) {
    e.preventDefault(); setLoading(true); setError('');
    try { await login(username, password); onLogin(); }
    catch (err) { setError(err.message || 'Login failed'); }
    finally { setLoading(false); }
  }

  return (
    <Center mih="100dvh" bg="dark.8">
      <Stack align="center" gap="lg" w={340} px="md">
        <img src={logoUrl} style={{ height: 40, width: 'auto' }} alt="Multiview" />
        <Text size="sm" c="dimmed" ta="center">
          Sign in with your Dispatcharr credentials. The account must have permission to modify plugin settings.
        </Text>
        <Paper withBorder p="xl" radius="md" w="100%">
          <form onSubmit={handleSubmit}>
            <Stack gap="sm">
              {error && (
                <Alert icon={<IconAlertCircle size={16} />} color="red" variant="light" py="xs">
                  {error}
                </Alert>
              )}
              <TextInput label="Username" autoComplete="username" value={username} onChange={(e) => setUsername(e.currentTarget.value)} required />
              <PasswordInput label="Password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.currentTarget.value)} required />
              <Button type="submit" loading={loading} fullWidth mt="xs">Sign in</Button>
            </Stack>
          </form>
        </Paper>
      </Stack>
    </Center>
  );
}

// ── Global settings ───────────────────────────────────────────────────────────

const LAYOUT_PREFIX_RE = /^Layout\s+\d+[:\s]*/i;
const stripPrefix = (f) => ({ ...f, label: f.label.replace(LAYOUT_PREFIX_RE, '') });

function GlobalSettings({ fields, warnings, settings, onSettingsChange, onFieldsReload }) {
  const [opened, setOpened] = useState(false);
  const handleChange = useSaveField(onSettingsChange, onFieldsReload);

  return (
    <Stack gap="xs">
      {warnings.map((w) => (
        <Alert key={w.id} icon={<IconAlertCircle size={16} />} color="yellow" variant="light">
          <Text size="sm" fw={500}>{w.label}</Text>
          {w.description && <Text size="xs" c="dimmed" mt={2}>{w.description}</Text>}
        </Alert>
      ))}
      <Paper withBorder radius="md">
        <UnstyledButton w="100%" p="md" onClick={() => setOpened((o) => !o)}>
          <Group justify="space-between">
            <Text size="sm" fw={600}>Global Settings</Text>
            {opened ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
          </Group>
        </UnstyledButton>
        <Collapse in={opened}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 'var(--mantine-spacing-sm)', padding: 'var(--mantine-spacing-md)', paddingTop: 0 }}>
            {fields.map((f) => (
              <FieldRenderer key={f.id} field={f} value={settings[f.id]} onChange={(v) => handleChange(f.id, v, f.type !== 'string')} />
            ))}
          </div>
        </Collapse>
      </Paper>
    </Stack>
  );
}

// ── Layout card ───────────────────────────────────────────────────────────────

function groupFields(fields, n) {
  const base = [], channels = [], epg = [];
  let audioSource = null, channelCountField = null;
  for (const f of fields) {
    const key = f.id.replace(`multiview_${n}_`, '');
    if (['name', 'layout', 'selector_type'].includes(key)) base.push(f);
    else if (key === 'channel_count') channelCountField = f;
    else if (key === 'audio_source') audioSource = f;
    else if (key.startsWith('epg_')) epg.push(f);
    else channels.push(f);
  }
  return { base, channels, epg, audioSource, channelCountField };
}

function LayoutCard({ n, fields, settings, isLast, onSettingsChange, onFieldsReload, onRemove, onChannelCountChange }) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const handleChange = useSaveField(onSettingsChange, onFieldsReload);
  const { base, channels, epg, audioSource, channelCountField } = groupFields(fields.map(stripPrefix), n);
  const name = settings[`multiview_${n}_name`] || `Multiview ${n}`;
  const channelCount = settings[`multiview_${n}_channel_count`] ?? channelCountField?.default ?? 4;
  const chMin = channelCountField?.min ?? 2;
  const chMax = channelCountField?.max ?? 9;

  function renderGrid(fs) {
    if (!fs.length) return null;
    return (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 'var(--mantine-spacing-sm)' }}>
        {fs.map((f) => (
          <FieldRenderer key={f.id} field={f} value={settings[f.id]} onChange={(v) => handleChange(f.id, v, f.type !== 'string')} />
        ))}
      </div>
    );
  }

  return (
    <Card withBorder radius="md" p={0}>
      <UnstyledButton w="100%" p="md" onClick={() => setExpanded((o) => !o)}>
        <Group justify="space-between">
          <Group gap="xs">
            {expanded ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
            <Text fw={600}>Layout {n}: {name}</Text>
          </Group>
          <Button size="xs" color="red" variant="subtle" leftSection={<IconTrash size={14} />}
            style={{ visibility: isLast ? 'visible' : 'hidden' }}
            onClick={(e) => { e.stopPropagation(); setConfirmOpen(true); }}>
            Remove
          </Button>
        </Group>
      </UnstyledButton>
      <Modal opened={confirmOpen} onClose={() => setConfirmOpen(false)} title={`Remove Layout ${n}`} size="xs" centered>
        <Text size="sm">Remove Layout {n}: {name}? This cannot be undone.</Text>
        <Group mt="md" justify="flex-end">
          <Button variant="default" onClick={() => setConfirmOpen(false)}>Cancel</Button>
          <Button color="red" onClick={() => { setConfirmOpen(false); onRemove(); }}>Remove</Button>
        </Group>
      </Modal>
      <Collapse in={expanded}>
        <Stack gap="md" p="md" pt={0}>
          {renderGrid(base)}
          {(channels.length > 0 || channelCountField) && (
            <>
              <Divider label={
                <Group gap={4} align="center">
                  <Text size="xs" c="dimmed">Channels</Text>
                  <ActionIcon size="xs" variant="subtle" disabled={channelCount <= chMin} onClick={() => onChannelCountChange(-1)}><IconMinus size={10} /></ActionIcon>
                  <Text size="xs" fw={600} w={14} ta="center">{channelCount}</Text>
                  <ActionIcon size="xs" variant="subtle" disabled={channelCount >= chMax} onClick={() => onChannelCountChange(1)}><IconPlus size={10} /></ActionIcon>
                </Group>
              } labelPosition="left" />
              {renderGrid(channels)}
            </>
          )}
          {audioSource && (
            <FieldRenderer field={audioSource} value={settings[audioSource.id]} onChange={(v) => handleChange(audioSource.id, v, true)} />
          )}
          {epg.length > 0 && <><Divider label="EPG" labelPosition="left" />{renderGrid(epg)}</>}
        </Stack>
      </Collapse>
    </Card>
  );
}

// ── Active streams modal ──────────────────────────────────────────────────────

function ActiveStreamsModal({ opened, onClose, settings }) {
  const [active, setActive] = useState([]);
  const [busy, setBusy] = useState({});
  const [confirm, setConfirm] = useState(null);

  const refresh = useCallback(async () => {
    try { const d = await listStreams(); setActive(d.active ?? []); } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (!opened) return;
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, [opened, refresh]);

  const withBusy = async (key, fn, msg) => {
    setBusy((b) => ({ ...b, [key]: true }));
    try {
      await fn();
      notifications.show({ message: msg, color: 'blue', autoClose: 2000 });
      await refresh();
    } catch (err) {
      notifications.show({ title: 'Failed', message: err.message, color: 'red', autoClose: 4000 });
    } finally {
      setBusy((b) => ({ ...b, [key]: false }));
    }
  };

  const ask = (title, message, confirmLabel, color, onConfirm) =>
    setConfirm({ title, message, confirmLabel, color, onConfirm });

  return (
    <Modal opened={opened} onClose={onClose} title="Active Multiviews" size="sm" centered>
      <ConfirmModal action={confirm} onClose={() => setConfirm(null)} />
      {active.length === 0 ? (
        <Text size="sm" c="dimmed">No multiviews currently active.</Text>
      ) : (
        <Stack gap="md">
          {active.map(({ n, channels }) => {
            const name = settings[`multiview_${n}_name`] || `Multiview ${n}`;
            return (
              <div key={n}>
                <Group justify="space-between" mb={6}>
                  <Text size="sm" fw={600}>{name}</Text>
                  <Button size="xs" color="orange" variant="subtle" leftSection={<IconPlayerPlay size={12} />}
                    loading={busy[`${n}`]}
                    onClick={() => ask('Reload', `Reload ${name}? The compositor will restart and active players will reconnect. Some players may buffer, stutter, or require a manual refresh depending on how they handle stream interruptions.`, 'Reload', 'orange',
                      () => withBusy(`${n}`, () => restartStreams(n), `${name} reloaded`))}>
                    Reload
                  </Button>
                </Group>
                <Stack gap={4} pl="sm">
                  {(channels ?? []).map(({ idx, name: chName }) => (
                    <Group key={idx} justify="space-between">
                      <Text size="xs" c="dimmed">{chName}</Text>
                      <Button size="xs" variant="subtle" leftSection={<IconRefresh size={11} />}
                        loading={busy[`${n}-${idx}`]}
                        onClick={() => ask('Reconnect', `Reconnect ${chName}?`, 'Reconnect', 'blue',
                          () => withBusy(`${n}-${idx}`, () => reconnectChannel(n, idx), `${chName} reconnecting`))}>
                        Reconnect
                      </Button>
                    </Group>
                  ))}
                </Stack>
              </div>
            );
          })}
        </Stack>
      )}
      {active.length > 1 && (
        <Button fullWidth mt="md" color="orange" variant="light" leftSection={<IconPlayerPlay size={14} />}
          loading={busy['all']}
          onClick={() => ask('Reload All', `Reload all ${active.length} multiviews? All compositors will restart and active players will reconnect. Some players may buffer, stutter, or require a manual refresh depending on how they handle stream interruptions.`, 'Reload All', 'orange',
            () => withBusy('all', () => restartStreams(null), `${active.length} multiviews reloaded`))}>
          Reload All
        </Button>
      )}
    </Modal>
  );
}

// ── Confirm modal ─────────────────────────────────────────────────────────────

function ConfirmModal({ action, onClose }) {
  if (!action) return null;
  return (
    <Modal opened={!!action} onClose={onClose} title={action.title} size="sm" centered>
      <Text size="sm">{action.message}</Text>
      <Group mt="md" justify="flex-end">
        <Button variant="default" onClick={onClose}>Cancel</Button>
        <Button color={action.color ?? 'blue'} loading={action.loading} onClick={() => { onClose(); action.onConfirm(); }}>
          {action.confirmLabel ?? 'Confirm'}
        </Button>
      </Group>
    </Modal>
  );
}

// ── App shell ─────────────────────────────────────────────────────────────────

export function App() {
  const [authed, setAuthed]       = useState(!!localStorage.getItem('mv_token'));
  const [fieldData, setFieldData] = useState(null);
  const [settings, setSettings]   = useState({});
  const [error, setError]         = useState('');
  const [refreshing, setRefreshing]   = useState(false);
  const [streamsOpen, setStreamsOpen] = useState(false);
  const [activeCount, setActiveCount] = useState(0);
  const [confirm, setConfirm]         = useState(null);

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

  useEffect(() => { if (authed) fetchFields(); }, [authed, fetchFields]);

  useEffect(() => {
    if (!authed) return;
    const poll = async () => { try { const d = await listStreams(); setActiveCount((d.active ?? []).length); } catch { /* noop */ } };
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, [authed]);

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
      await patchConfig(updates); await fetchFields();
      notifications.show({ message: `Layout ${n} added`, color: 'green', autoClose: 2000 });
    } catch (err) {
      notifications.show({ title: 'Failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  async function handleRemoveLayout(n) {
    const count = fieldData?.layout_count ?? 1;
    if (count <= 1) return;
    const nullKeys = Object.fromEntries(Object.keys(settings).filter((k) => k.startsWith(`multiview_${n}_`)).map((k) => [k, null]));
    nullKeys.multiview_count = count - 1;
    try {
      await patchConfig(nullKeys); await fetchFields();
      notifications.show({ message: `Layout ${n} removed`, color: 'green', autoClose: 2000 });
    } catch (err) {
      notifications.show({ title: 'Failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  async function doRefresh() {
    setRefreshing(true);
    try {
      const result = await triggerRefresh();
      notifications.show({ message: result.message || 'M3U & EPG refreshed', color: result.status === 'success' ? 'green' : 'red', autoClose: 3000 });
    } catch (err) {
      notifications.show({ title: 'Refresh failed', message: err.message, color: 'red', autoClose: 4000 });
    } finally {
      setRefreshing(false);
    }
  }

  function handleLogout() { clearToken(); setAuthed(false); setFieldData(null); setSettings({}); }

  useEffect(() => {
    _onUnauthorized = () => { setAuthed(false); setFieldData(null); setSettings({}); };
    return () => { _onUnauthorized = null; };
  }, []);

  if (!authed) return <LoginForm onLogin={() => setAuthed(true)} />;
  if (error)   return <Center mih="100dvh"><Text c="red">{error}</Text></Center>;
  if (!fieldData) return <Center mih="100dvh"><Loader /></Center>;

  const { warnings = [], global: globalFields = [], layouts = [], layout_count } = fieldData;

  return (
    <AppShell header={{ height: 56 }}>
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Image src={logoUrl} h={32} w="auto" style={{ flexShrink: 0 }} />
          <Group gap="xs" wrap="nowrap">
            <Button size="sm" leftSection={<IconActivity size={16} />}
              color={activeCount > 0 ? 'teal' : undefined}
              variant={activeCount > 0 ? 'filled' : 'default'}
              styles={activeCount > 0 ? { root: { '--button-bg': 'var(--mantine-color-teal-9)', '--button-hover': 'var(--mantine-color-teal-8)' } } : undefined}
              onClick={() => setStreamsOpen(true)} visibleFrom="xs">
              Streams{activeCount > 0 ? ` (${activeCount})` : ''}
            </Button>
            <ActionIcon size="lg"
              color={activeCount > 0 ? 'teal' : undefined}
              variant={activeCount > 0 ? 'filled' : 'default'}
              styles={activeCount > 0 ? { root: { '--button-bg': 'var(--mantine-color-teal-9)', '--button-hover': 'var(--mantine-color-teal-8)' } } : undefined}
              onClick={() => setStreamsOpen(true)} hiddenFrom="xs" aria-label="Active Streams">
              <IconActivity size={18} />
            </ActionIcon>
            <Button size="sm" leftSection={<IconRefresh size={16} />} loading={refreshing}
              onClick={() => setConfirm({ title: 'Refresh M3U & EPG', confirmLabel: 'Refresh', color: 'blue',
                message: 'Regenerate the M3U playlist and sync EPG data now?',
                onConfirm: doRefresh })}
              visibleFrom="xs">
              Refresh M3U &amp; EPG
            </Button>
            <ActionIcon size="lg" variant="default" loading={refreshing} hiddenFrom="xs" aria-label="Refresh M3U & EPG"
              onClick={() => setConfirm({ title: 'Refresh M3U & EPG', confirmLabel: 'Refresh', color: 'blue',
                message: 'Regenerate the M3U playlist and sync EPG data now?',
                onConfirm: doRefresh })}>
              <IconRefresh size={18} />
            </ActionIcon>
            <Button size="sm" variant="subtle" onClick={handleLogout}>Logout</Button>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Main>
        <Stack p="md" maw={860} mx="auto">
          <GlobalSettings fields={globalFields} warnings={warnings} settings={settings} onSettingsChange={handleSettingsChange} onFieldsReload={fetchFields} />
          <Text size="xs" tt="uppercase" fw={700} c="dimmed" mt="sm">Layouts</Text>
          {layouts.map(({ n, fields }) => (
            <LayoutCard key={n} n={n} fields={fields} settings={settings} isLast={n === layout_count && layout_count > 1}
              onSettingsChange={handleSettingsChange} onFieldsReload={fetchFields}
              onRemove={() => handleRemoveLayout(n)} onChannelCountChange={(delta) => handleChangeChannelCount(n, delta)} />
          ))}
          <Button variant="default" onClick={handleAddLayout}>+ Add Layout</Button>
        </Stack>
      </AppShell.Main>

      <ActiveStreamsModal opened={streamsOpen} onClose={() => setStreamsOpen(false)} settings={settings} />
      <ConfirmModal action={confirm} onClose={() => setConfirm(null)} />
    </AppShell>
  );
}
