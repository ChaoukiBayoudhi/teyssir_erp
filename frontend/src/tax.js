/** Prefer exempt 0% when TVA UI is hidden; else is_default / first rate. */
export function preferExemptTaxRate(rates) {
  const list = rates || [];
  return list.find((x) => Number(x.rate_percent) === 0)
    || list.find((x) => x.is_default)
    || list[0]
    || null;
}
