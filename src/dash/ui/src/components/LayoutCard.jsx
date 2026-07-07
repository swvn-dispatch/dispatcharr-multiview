import { useState } from 'react';
import { Group, Text, Button, Modal, Divider, ActionIcon, Stack } from '@mantine/core';
import { IconTrash, IconPlus, IconMinus } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { CollapsiblePanel, FieldRenderer, useDebouncedFieldSave } from '@swvn-dispatch/dispatch-ui-kit';
import { patchConfig } from '../api.js';
import { groupFields, stripPrefix, isTrigger } from '../utils/fields.js';

export function LayoutCard({ n, fields, settings, isLast, onSettingsChange, onFieldsReload, onRemove, onChannelCountChange }) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const handleChange = useDebouncedFieldSave(patchConfig, {
    onOptimisticChange: onSettingsChange,
    shouldReload: isTrigger,
    onReload: onFieldsReload,
    onSaved: () => notifications.show({ message: 'Saved', color: 'green', autoClose: 1500 }),
    onError: (err) => notifications.show({ title: 'Save failed', message: err.message, color: 'red', autoClose: 4000 }),
  });
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

  const removeButton = (
    <Button
      size="xs"
      color="red"
      variant="subtle"
      leftSection={<IconTrash size={14} />}
      style={{ visibility: isLast ? 'visible' : 'hidden' }}
      onClick={(e) => {
        e.stopPropagation();
        setConfirmOpen(true);
      }}
    >
      Remove
    </Button>
  );

  return (
    <>
      <CollapsiblePanel as="Card" title={`Layout ${n}: ${name}`} trailingAction={removeButton}>
        <Stack gap="md" p="md" pt={0}>
          {renderGrid(base)}
          {(channels.length > 0 || channelCountField) && (
            <>
              <Divider
                label={
                  <Group gap={4} align="center">
                    <Text size="xs" c="dimmed">Channels</Text>
                    <ActionIcon size="xs" variant="subtle" disabled={channelCount <= chMin} onClick={() => onChannelCountChange(-1)}>
                      <IconMinus size={10} />
                    </ActionIcon>
                    <Text size="xs" fw={600} w={14} ta="center">{channelCount}</Text>
                    <ActionIcon size="xs" variant="subtle" disabled={channelCount >= chMax} onClick={() => onChannelCountChange(1)}>
                      <IconPlus size={10} />
                    </ActionIcon>
                  </Group>
                }
                labelPosition="left"
              />
              {renderGrid(channels)}
            </>
          )}
          {audioSource && (
            <FieldRenderer field={audioSource} value={settings[audioSource.id]} onChange={(v) => handleChange(audioSource.id, v, true)} />
          )}
          {epg.length > 0 && (
            <>
              <Divider label="EPG" labelPosition="left" />
              {renderGrid(epg)}
            </>
          )}
        </Stack>
      </CollapsiblePanel>
      <Modal opened={confirmOpen} onClose={() => setConfirmOpen(false)} title={`Remove Layout ${n}`} size="xs" centered>
        <Text size="sm">Remove Layout {n}: {name}? This cannot be undone.</Text>
        <Group mt="md" justify="flex-end">
          <Button variant="default" onClick={() => setConfirmOpen(false)}>Cancel</Button>
          <Button color="red" onClick={() => { setConfirmOpen(false); onRemove(); }}>Remove</Button>
        </Group>
      </Modal>
    </>
  );
}
