// crypto.randomUUID() requires a secure context (https:, localhost, 127.0.0.1)
// and throws elsewhere -- this dashboard is commonly reached over plain HTTP
// on a LAN IP, so use crypto.getRandomValues() instead, which has no such
// restriction.
export function genId(bytes = 4) {
  const arr = new Uint8Array(bytes);
  crypto.getRandomValues(arr);
  return Array.from(arr, (b) => b.toString(16).padStart(2, '0')).join('');
}
