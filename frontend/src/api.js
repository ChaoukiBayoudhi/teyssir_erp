// Thin API client for the Teyssir DRF backend (spec §5). Token auth; same-origin via the
// Vite dev proxy (and Caddy in production), so no CORS handling is needed here.
const BASE = "/api/v1";

export function getToken() {
  return localStorage.getItem("teyssir_token") || "";
}
export function setToken(t) {
  localStorage.setItem("teyssir_token", t);
}
export function clearToken() {
  localStorage.removeItem("teyssir_token");
}

async function request(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && getToken()) headers["Authorization"] = `Token ${getToken()}`;
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    // fetch only throws on a network-level failure (node unreachable) — treat as offline
    const err = new Error("offline");
    err.offline = true;
    throw err;
  }
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.status === 204 ? null : res.json();
}

export async function login(username, password) {
  const data = await request("/auth/token", {
    method: "POST",
    auth: false,
    body: { username, password },
  });
  setToken(data.token);
  return data;
}

export const searchProducts = (q) =>
  request(`/catalog/products/?search=${encodeURIComponent(q)}`);

export const lookupBarcode = (code) =>
  request(`/catalog/products/?barcode=${encodeURIComponent(code)}`);

export const checkout = (payload) =>
  request("/pos/checkout", { method: "POST", body: payload });

export const salesReport = (from, to) =>
  request(`/reports/sales?from=${from}&to=${to}`);

export const stocktake = (payload) =>
  request("/inventory/stocktake", { method: "POST", body: payload });

export const cashOpen = (terminal, opening_float) =>
  request("/cash/open", { method: "POST", body: { terminal, opening_float } });
export const cashX = (terminal) => request(`/cash/x?terminal=${terminal}`);
export const cashZ = (terminal, counted_cash) =>
  request("/cash/z", { method: "POST", body: { terminal, counted_cash } });

export const listSuppliers = () => request("/suppliers/");
export const createSupplier = (name) =>
  request("/suppliers/", { method: "POST", body: { name } });
export const receiveGoods = (payload) =>
  request("/purchasing/receive", { method: "POST", body: payload });

export const listCustomers = () => request("/customers/");
export const createCustomer = (body) =>
  request("/customers/", { method: "POST", body });
export const customerStatement = (id) => request(`/customers/${id}/statement/`);
export const customerPayment = (id, amount) =>
  request(`/customers/${id}/payment/`, { method: "POST", body: { amount } });
