/** Display helpers — quantities as integers, money as millime-aware strings. */

/** Whole pieces only: 1 not "1.000". */
export function fmtQty(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0";
  return String(Math.trunc(n));
}

/** Parse qty input; rejects fractional values. Returns null if invalid. */
export function parseQty(value, { min = 0 } = {}) {
  if (value === "" || value == null) return null;
  const n = Number(value);
  if (!Number.isFinite(n) || !Number.isInteger(n) || n < min) return null;
  return n;
}
