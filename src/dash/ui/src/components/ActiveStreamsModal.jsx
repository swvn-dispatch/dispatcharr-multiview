import { useState, useEffect, useCallback } from 'react';
import { Modal, Text, Stack, Group, Button } from '@mantine/core';
import { IconPlayerPlay, IconRefresh } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { ConfirmModal } from '@swvn-dispatch/dispatch-ui-kit';
import { listStreams, restartStreams, reconnectChannel } from '../api.js';

export function ActiveStreamsModal({ opened, onClose, settings }) {
  const [active, setActive] = useState([]);
  const [busy, setBusy] = useState({});
  const [confirm, setConfirm] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const d = await listStreams();
      setActive(d.active ?? []);
    } catch {
      // ignore
    }
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
                  <Button
                    size="xs"
                    color="orange"
                    variant="subtle"
                    leftSection={<IconPlayerPlay size={12} />}
                    loading={busy[`${n}`]}
                    onClick={() =>
                      ask(
                        'Reload',
                        `Reload ${name}? The compositor will restart and active players will reconnect. Some players may buffer, stutter, or require a manual refresh depending on how they handle stream interruptions.`,
                        'Reload',
                        'orange',
                        () => withBusy(`${n}`, () => restartStreams(n), `${name} reloaded`),
                      )
                    }
                  >
                    Reload
                  </Button>
                </Group>
                <Stack gap={4} pl="sm">
                  {(channels ?? []).map(({ idx, name: chName }) => (
                    <Group key={idx} justify="space-between">
                      <Text size="xs" c="dimmed">{chName}</Text>
                      <Button
                        size="xs"
                        variant="subtle"
                        leftSection={<IconRefresh size={11} />}
                        loading={busy[`${n}-${idx}`]}
                        onClick={() =>
                          ask('Reconnect', `Reconnect ${chName}?`, 'Reconnect', 'blue', () =>
                            withBusy(`${n}-${idx}`, () => reconnectChannel(n, idx), `${chName} reconnecting`),
                          )
                        }
                      >
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
        <Button
          fullWidth
          mt="md"
          color="orange"
          variant="light"
          leftSection={<IconPlayerPlay size={14} />}
          loading={busy['all']}
          onClick={() =>
            ask(
              'Reload All',
              `Reload all ${active.length} multiviews? All compositors will restart and active players will reconnect. Some players may buffer, stutter, or require a manual refresh depending on how they handle stream interruptions.`,
              'Reload All',
              'orange',
              () => withBusy('all', () => restartStreams(null), `${active.length} multiviews reloaded`),
            )
          }
        >
          Reload All
        </Button>
      )}
    </Modal>
  );
}
