import { useState, useEffect } from 'react';
import { Stack, Group, Text, Button, TextInput, NumberInput, Select, ActionIcon, Tabs } from '@mantine/core';
import { IconCopy, IconTrash, IconPlus, IconArrowLeft } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { patchConfig, previewStyle } from '../api.js';
import { genId } from '../utils/id.js';

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

export function StyleBuilderPage({ settings, onFieldsReload, onBack }) {
  const customLayouts = settings.multiview_custom_layouts ?? {};
  const [builtinPreviews, setBuiltinPreviews] = useState({});
  const [activeCounts, setActiveCounts] = useState({}); // styleId -> channel count string being edited

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

  async function handleDuplicate(builtin) {
    try {
      const id = genId();
      const results = await Promise.all(CHANNEL_COUNTS.map((n) => previewStyle(builtin.value, n).catch(() => null)));
      const tiles = {};
      CHANNEL_COUNTS.forEach((n, i) => {
        if (results[i]) tiles[n] = results[i].tiles;
      });
      await saveRegistry({ ...customLayouts, [id]: { name: `Copy of ${builtin.label}`, tiles } });
      notifications.show({ message: `Style "${builtin.label}" duplicated`, color: 'green', autoClose: 2000 });
    } catch (err) {
      notifications.show({ title: 'Failed', message: err.message, color: 'red', autoClose: 4000 });
    }
  }

  function handleRename(id, name) {
    saveRegistry({ ...customLayouts, [id]: { ...customLayouts[id], name } });
  }

  function handleDeleteStyle(id) {
    const next = { ...customLayouts };
    delete next[id];
    saveRegistry(next);
  }

  function handleTileChange(id, count, tileIdx, fieldIdx, value) {
    const style = customLayouts[id];
    const tiles = { ...(style.tiles ?? {}) };
    const list = (tiles[count] ?? []).map((row) => [...row]);
    list[tileIdx][fieldIdx] = value;
    tiles[count] = list;
    saveRegistry({ ...customLayouts, [id]: { ...style, tiles } });
  }

  function handleSeedCount(id, count) {
    const style = customLayouts[id];
    const n = parseInt(count, 10);
    const tiles = { ...(style.tiles ?? {}) };
    if (tiles[count]) return;
    const cols = Math.ceil(Math.sqrt(n));
    const rows = Math.ceil(n / cols);
    const list = [];
    for (let i = 0; i < n; i++) {
      const c = i % cols;
      const r = Math.floor(i / cols);
      list.push([c / cols, r / rows, 1 / cols, 1 / rows, 'center', 'center']);
    }
    tiles[count] = list;
    saveRegistry({ ...customLayouts, [id]: { ...style, tiles } });
  }

  function handleRemoveCount(id, count) {
    const style = customLayouts[id];
    const tiles = { ...(style.tiles ?? {}) };
    delete tiles[count];
    saveRegistry({ ...customLayouts, [id]: { ...style, tiles } });
  }

  const customEntries = Object.entries(customLayouts);

  return (
    <Stack p="md" maw={860} mx="auto" gap="md">
      <Button variant="subtle" leftSection={<IconArrowLeft size={14} />} onClick={onBack} style={{ alignSelf: 'flex-start' }}>
        Back to Dashboard
      </Button>
      <Text size="lg" fw={700}>Style Builder</Text>

      <Tabs defaultValue="builtins">
        <Tabs.List>
          <Tabs.Tab value="builtins">Built-in Styles</Tabs.Tab>
          <Tabs.Tab value="custom">Custom Styles{customEntries.length > 0 ? ` (${customEntries.length})` : ''}</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="builtins" pt="md">
          <Stack gap="md">
            <Text size="xs" c="dimmed">
              Built-in styles are read-only. Duplicate one to create an editable custom style, then assign it in a
              layout&apos;s &quot;Layout Style&quot; dropdown.
            </Text>
            <Group gap="md" wrap="wrap">
              {BUILTINS.map((b) => (
                <Stack key={b.value} gap={4} align="center">
                  <TilePreview tiles={builtinPreviews[b.value]} />
                  <Text size="xs">{b.label}</Text>
                  <Button size="xs" variant="subtle" leftSection={<IconCopy size={12} />} onClick={() => handleDuplicate(b)}>
                    Duplicate
                  </Button>
                </Stack>
              ))}
            </Group>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="custom" pt="md">
          <Stack gap="md">
            {customEntries.length === 0 && (
              <Text size="sm" c="dimmed">No custom styles yet — duplicate a built-in from the other tab to create one.</Text>
            )}
            {customEntries.map(([id, style]) => {
              const definedCounts = Object.keys(style.tiles ?? {}).sort((a, b) => Number(a) - Number(b));
              const activeCount = activeCounts[id] ?? definedCounts[0] ?? '4';
              const tiles = (style.tiles ?? {})[activeCount] ?? [];
              const isDefined = definedCounts.includes(activeCount);
              return (
                <Stack key={id} gap="sm" p="sm" style={{ border: '1px solid var(--mantine-color-dark-4)', borderRadius: 6 }}>
                  <Group justify="space-between">
                    <TextInput
                      size="xs"
                      value={style.name ?? ''}
                      onChange={(e) => handleRename(id, e.currentTarget.value)}
                      placeholder="Style name"
                      style={{ maxWidth: 240 }}
                    />
                    <ActionIcon color="red" variant="subtle" onClick={() => handleDeleteStyle(id)}>
                      <IconTrash size={14} />
                    </ActionIcon>
                  </Group>
                  <Group gap="xs" align="flex-end">
                    <Select
                      size="xs"
                      label="Channel count"
                      data={CHANNEL_COUNTS}
                      value={activeCount}
                      onChange={(v) => v && setActiveCounts((p) => ({ ...p, [id]: v }))}
                      w={120}
                    />
                    {isDefined ? (
                      <Button size="xs" variant="subtle" color="red" onClick={() => handleRemoveCount(id, activeCount)}>
                        Remove this count
                      </Button>
                    ) : (
                      <Button size="xs" variant="default" leftSection={<IconPlus size={12} />} onClick={() => handleSeedCount(id, activeCount)}>
                        Define {activeCount}-channel layout
                      </Button>
                    )}
                  </Group>
                  {isDefined && (
                    <Group align="flex-start" gap="lg" wrap="wrap">
                      <TilePreview tiles={tiles} />
                      <Stack gap={6}>
                        {tiles.map((t, i) => (
                          <Group key={i} gap={4} wrap="nowrap">
                            <Text size="xs" w={16}>{i + 1}</Text>
                            {['x', 'y', 'w', 'h'].map((label, fi) => (
                              <NumberInput
                                key={label}
                                size="xs"
                                w={70}
                                min={0}
                                max={1}
                                step={0.01}
                                decimalScale={2}
                                value={t[fi]}
                                onChange={(v) => handleTileChange(id, activeCount, i, fi, v ?? 0)}
                              />
                            ))}
                            <Select
                              size="xs"
                              w={90}
                              data={ALIGN_OPTIONS}
                              value={t[4]}
                              onChange={(v) => v && handleTileChange(id, activeCount, i, 4, v)}
                            />
                            <Select
                              size="xs"
                              w={90}
                              data={ALIGN_OPTIONS}
                              value={t[5]}
                              onChange={(v) => v && handleTileChange(id, activeCount, i, 5, v)}
                            />
                          </Group>
                        ))}
                      </Stack>
                    </Group>
                  )}
                </Stack>
              );
            })}
          </Stack>
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
