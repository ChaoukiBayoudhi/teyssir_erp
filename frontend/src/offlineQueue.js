// Offline checkout queue (spec §4.3): if the local node is momentarily unreachable, hold the
// sale locally and replay it on reconnect. The node itself is the primary offline mechanism;
// this guards the thin window where even localhost is down (e.g. a service restart).
const KEY = "teyssir_pending_sales";

export const pending = () => JSON.parse(localStorage.getItem(KEY) || "[]");
const save = (arr) => localStorage.setItem(KEY, JSON.stringify(arr));

export function enqueue(payload) {
  const arr = pending();
  arr.push({ ...payload, queued_at: new Date().toISOString() });
  save(arr);
  return arr.length;
}

// Replay queued sales through `checkoutFn`. Drops a sale on success; keeps it if still offline.
export async function flush(checkoutFn) {
  const remaining = [];
  const succeeded = [];
  for (const payload of pending()) {
    try {
      succeeded.push(await checkoutFn(payload));
    } catch (err) {
      remaining.push(payload); // offline or transient — try again later
    }
  }
  save(remaining);
  return { succeeded, remaining: remaining.length };
}
