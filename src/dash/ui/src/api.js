import { createApiClient } from '@swvn-dispatch/dispatch-ui-kit';

let handleUnauthorized = () => {};
export function setUnauthorizedHandler(fn) {
  handleUnauthorized = fn;
}

const client = createApiClient({
  tokenKey: 'mv_token',
  onUnauthorized: (err) => handleUnauthorized(err),
});

export const login = client.login;
export const logout = client.logout;
export const isAuthenticated = client.isAuthenticated;
export const getUsername = client.getUsername;

const { request } = client;

export const loadFields = () => request('/fields');
export const loadConfig = () => request('/config');
export const loadChannels = () => request('/channels');
export const previewStyle = (layout, channelCount) =>
  request(`/styles/preview?layout=${encodeURIComponent(layout)}&channel_count=${channelCount}`);
export const patchConfig = (updates) => request('/config', { method: 'PATCH', body: JSON.stringify(updates) });
export const triggerRefresh = () => request('/refresh', { method: 'POST' });
export const listStreams = () => request('/streams');
export const restartStreams = (n) =>
  request('/streams/restart', { method: 'POST', body: JSON.stringify(n != null ? { n } : {}) });
export const reconnectChannel = (n, channel_idx) =>
  request('/streams/restart', { method: 'POST', body: JSON.stringify({ n, channel_idx }) });
