import { useState } from 'react';
import { Group, Text, Button, Modal, Divider, ActionIcon, Stack, Combobox, InputBase, useCombobox } from '@mantine/core';
import { IconTrash, IconPlus, IconMinus, IconGripVertical } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { CollapsiblePanel, FieldRenderer, useDebouncedFieldSave } from '@swvn-dispatch/dispatch-ui-kit';
import { patchConfig } from '../api.js';
import { groupFields, stripPrefix, isTrigger } from '../utils/fields.js';

function ChannelSelect({ label, description, data, value, onChange }) {
  const combobox = useCombobox({
    onDropdownClose: () => combobox.resetSelectedOption(),
  });
  const [search, setSearch] = useState('');
  const selected = data.find((o) => o.value === value);
  const filtered = data.filter((o) => o.label.toLowerCase().includes(search.trim().toLowerCase()));

  return (
    <Combobox
      store={combobox}
      onOptionSubmit={(v) => {
        onChange(v);
        setSearch('');
        combobox.closeDropdown();
      }}
    >
      <Combobox.Target>
        <InputBase
          label={label}
          description={description}
          component="button"
          type="button"
          pointer
          rightSection={<Combobox.Chevron />}
          onClick={() => combobox.toggleDropdown()}
        >
          {selected?.label ?? <Text component="span" c="dimmed">Select channel</Text>}
        </InputBase>
      </Combobox.Target>
      <Combobox.Dropdown>
        <Combobox.Search
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          placeholder="Search channels..."
        />
        <Combobox.Options mah={260} style={{ overflowY: 'auto' }}>
          {filtered.length === 0 ? (
            <Combobox.Empty>No channels found</Combobox.Empty>
          ) : (
            filtered.map((o) => (
              <Combobox.Option value={o.value} key={o.value} active={o.value === value}>
                {o.label}
              </Combobox.Option>
            ))
          )}
        </Combobox.Options>
      </Combobox.Dropdown>
    </Combobox>
  );
}

export function LayoutCard({ id, position, fields, settings, canRemove, hasActiveStream, dragHandleProps, onSettingsChange, onFieldsReload, onRemove, onChannelCountChange }) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const handleChange = useDebouncedFieldSave(patchConfig, {
    onOptimisticChange: onSettingsChange,
    shouldReload: isTrigger,
    onReload: onFieldsReload,
    onSaved: () => notifications.show({ message: 'Saved', color: 'green', autoClose: 1500 }),
    onError: (err) => notifications.show({ title: 'Save failed', message: err.message, color: 'red', autoClose: 4000 }),
  });
  const { base, channels, epg, audioSource, channelCountField } = groupFields(fields.map(stripPrefix), id);
  const name = settings[`multiview_${id}_name`] || `Multiview ${position}`;
  const channelCount = settings[`multiview_${id}_channel_count`] ?? channelCountField?.default ?? 4;
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

  function renderChannelGrid(fs) {
    if (!fs.length) return null;
    return (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 'var(--mantine-spacing-sm)' }}>
        {fs.map((f) => {
          const seen = new Set();
          const data = (f.options ?? []).reduce((acc, o) => {
            const v = String(o.value);
            if (!seen.has(v)) { seen.add(v); acc.push({ value: v, label: o.label }); }
            return acc;
          }, []);
          return (
            <ChannelSelect
              key={f.id}
              label={f.label}
              description={f.description}
              data={data}
              value={String(settings[f.id] ?? f.default ?? '')}
              onChange={(v) => handleChange(f.id, v, true)}
            />
          );
        })}
      </div>
    );
  }

  const trailingAction = (
    <Group gap={4} wrap="nowrap">
      <ActionIcon
        size="sm"
        variant="subtle"
        color="gray"
        style={{ cursor: 'grab', touchAction: 'none' }}
        onClick={(e) => e.stopPropagation()}
        {...dragHandleProps}
      >
        <IconGripVertical size={16} />
      </ActionIcon>
      <Button
        size="xs"
        color="red"
        variant="subtle"
        leftSection={<IconTrash size={14} />}
        style={{ visibility: canRemove ? 'visible' : 'hidden' }}
        onClick={(e) => {
          e.stopPropagation();
          setConfirmOpen(true);
        }}
      >
        Remove
      </Button>
    </Group>
  );

  return (
    <>
      <CollapsiblePanel as="Card" title={`Layout ${position}: ${name}`} trailingAction={trailingAction}>
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
              {renderChannelGrid(channels)}
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
      <Modal opened={confirmOpen} onClose={() => setConfirmOpen(false)} title={`Remove Layout ${position}`} size="xs" centered>
        <Text size="sm">Remove Layout {position}: {name}? This cannot be undone.</Text>
        {hasActiveStream && (
          <Text size="sm" c="orange" mt="xs">
            This layout has an active stream — removing it will disconnect that viewer.
          </Text>
        )}
        <Group mt="md" justify="flex-end">
          <Button variant="default" onClick={() => setConfirmOpen(false)}>Cancel</Button>
          <Button color="red" onClick={() => { setConfirmOpen(false); onRemove(); }}>Remove</Button>
        </Group>
      </Modal>
    </>
  );
}
