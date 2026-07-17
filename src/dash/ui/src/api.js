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
export const uploadStyleBackground = (styleId, filename, dataBase64) =>
  request('/styles/background', { method: 'POST', body: JSON.stringify({ style_id: styleId, filename, data_base64: dataBase64 }) });

// The background image endpoint returns raw image bytes, not JSON, so it
// can't go through the shared `request()` wrapper -- fetch it directly with
// the same bearer token, as a blob for use in an <img> object URL.
export async function fetchStyleBackgroundBlob(styleId) {
  const token = localStorage.getItem('mv_token');
  const res = await fetch(`/api/styles/background?style_id=${encodeURIComponent(styleId)}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) return null;
  return res.blob();
}
