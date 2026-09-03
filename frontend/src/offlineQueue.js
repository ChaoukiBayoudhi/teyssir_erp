// Offline checkout queue (spec §4.3): if the local node is momentarily unreachable, hold the
// sale locally and replay it on reconnect. The node itself is the primary offline mechanism;
// this guards the thin window where even localhost is down (e.g. a service restart).
const KEY = "teyssir_pending_sales";
const DEAD_KEY = "teyssir_dead_sales"; // non-offline failures quarantined (poison messages)

export const pending = () => JSON.parse(localStorage.getItem(KEY) || "[]");
export const deadLetter = () => JSON.parse(localStorage.getItem(DEAD_KEY) || "[]");
const save = (arr) => localStorage.setItem(KEY, JSON.stringify(arr));
const saveDead = (arr) => localStorage.setItem(DEAD_KEY, JSON.stringify(arr));

export function enqueue(payload) {
  const arr = pending();
  arr.push({ ...payload, queued_at: new Date().toISOString() });
  save(arr);
  return arr.length;
}

// Replay queued sales through `checkoutFn`.
// - success → drop
// - offline / network → keep for later retry
// - validation / 4xx / 5xx → quarantine (do NOT poison-loop forever)
export async function flush(checkoutFn) {
  const remaining = [];
  const succeeded = [];
  const dead = deadLetter();
  for (const payload of pending()) {
    try {
      succeeded.push(await checkoutFn(payload));
    } catch (err) {
      if (err && err.offline) {
        remaining.push(payload);
      } else {
        dead.push({
          ...payload,
          failed_at: new Date().toISOString(),
          error: String(err && err.message ? err.message : err),
        });
      }
    }
  }
  save(remaining);
  saveDead(dead);
  return { succeeded, remaining: remaining.length, dead: dead.length };
}
